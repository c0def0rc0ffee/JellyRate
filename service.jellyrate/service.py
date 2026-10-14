# -*- coding: utf-8 -*-
"""
<summary>
JellyRate service entry point.
</summary>
<remarks>
Runs in the background, watches for finished video playback, asks the user for
a 1-10 rating, and writes that rating back to Jellyfin using the credentials
already stored by the Jellyfin for Kodi add-on.
</remarks>
"""
import os
import sys
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ADDON_ICON = os.path.join(ADDON_PATH, "resources", "icon.png")

# Make the bundled modules importable.
sys.path.insert(0, os.path.join(ADDON_PATH, "resources", "lib"))

import jellyfin  # noqa: E402
import jellystat_bridge  # noqa: E402
from rating_dialog import ask_rating  # noqa: E402

# Media types we are willing to rate (matches Kodi getMediaType / jellyfin.db).
RATEABLE_TYPES = ("movie", "episode", "musicvideo")

# How long the home screen must have been up before the add-on API announces itself.
# Home window writes during Kodi's start up are implicated in a crash (the reason
# Engage waits too), and the announcement is one.
API_SETTLE_SECONDS = 20

# How long to wait for the home screen to show at all before the settle period starts
# anyway: a start up window other than Home, or a video started at login, must not
# keep the API off for good.
HOME_BACKSTOP_SECONDS = 60

# How long after an item finished (and nothing else is playing) before its prompt is
# shown. Kodi starts the next playlist item a moment after the previous one ends, and
# the dialog must not open in that gap only to have the next episode start under it.
PLAYBACK_GRACE_SECONDS = 2


def log(message, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to Kodi's log, prefixed with the add-on id.
    </summary>
    <param name="message">Text to log.</param>
    <param name="level">Kodi log level, info by default.</param>
    """
    xbmc.log("[%s] %s" % (ADDON_ID, message), level)


def notify(message, error=False):
    """
    <summary>
    Show a four second Kodi toast under the add-on's name.
    </summary>
    <param name="message">Text to show.</param>
    <param name="error">True to use Kodi's error icon instead of the add-on icon.</param>
    """
    xbmcgui.Dialog().notification(
        ADDON_NAME,
        message,
        xbmcgui.NOTIFICATION_ERROR if error else ADDON_ICON,
        4000,
    )


class JellyRatePlayer(xbmc.Player):
    """
    <summary>
    Captures what is playing and queues finished items for rating.
    </summary>
    """

    def __init__(self):
        """
        <summary>
        Start with an empty rating queue and no item being tracked.
        </summary>
        """
        super().__init__()
        self.pending = []
        self.last_pos = 0.0
        self.total = 0.0
        self._reset_current()

    def _reset_current(self):
        """
        <summary>
        Forget the item being tracked: its Kodi id, media type, title and playback positions.
        </summary>
        """
        self.kodi_id = None
        self.media_type = None
        self.title = ""
        self.last_pos = 0.0
        self.total = 0.0

    def onAVStarted(self):
        """
        <summary>
        Kodi hook: remember the Kodi id, media type and title of the video that just started; audio playback is ignored.
        </summary>
        <remarks>
        A failed info tag read is logged as a warning and the tracked item cleared, so nothing is queued for it later.
        Audio starting clears it too: otherwise a video that errored stayed tracked and was queued when the song ended.
        </remarks>
        """
        if not self.isPlayingVideo():
            self._reset_current()
            return
        try:
            tag = self.getVideoInfoTag()
            self.kodi_id = tag.getDbId()
            self.media_type = tag.getMediaType()
            self.title = tag.getTitle()
        except Exception as exc:  # pragma: no cover, defensive
            log("Could not read info tag: %s" % exc, xbmc.LOGWARNING)
            self._reset_current()

    def track_position(self):
        """
        <summary>
        Called from the service loop so we know how far playback got.
        </summary>
        """
        if self.isPlayingVideo():
            try:
                self.last_pos = self.getTime()
                self.total = self.getTotalTime()
            except Exception:
                pass

    def onPlayBackEnded(self):
        """
        <summary>
        Kodi hook: playback reached its natural end, so queue the item as fully watched.
        </summary>
        """
        self._enqueue(100.0)

    def onPlayBackError(self):
        """
        <summary>
        Kodi hook: playback failed, so forget the item; nothing that did not play is rated.
        </summary>
        """
        self._reset_current()

    def onPlayBackStopped(self):
        """
        <summary>
        Kodi hook: playback was stopped early; queue the item with the percentage reached, or drop it when prompting on stop is switched off.
        </summary>
        <remarks>
        The percentage comes from the last position track_position() recorded, since the player has already stopped by the time this runs.
        </remarks>
        """
        if not ADDON.getSettingBool("prompt_on_stop"):
            self._reset_current()
            return
        percent = (self.last_pos / self.total * 100.0) if self.total else 0.0
        self._enqueue(percent)

    def _enqueue(self, percent):
        """
        <summary>
        Queue the tracked item for rating when it is a library item of a rateable type, then clear the tracked item.
        </summary>
        <param name="percent">How much of it was watched, 0 to 100.</param>
        <remarks>
        Only items with a positive Kodi id and a media type in RATEABLE_TYPES are queued; anything else is dropped without comment.
        The queue entry records when it was queued (time.monotonic) so take_due can apply the grace period.
        </remarks>
        """
        if (
            self.kodi_id
            and self.kodi_id > 0
            and self.media_type in RATEABLE_TYPES
        ):
            self.pending.append(
                {
                    "kodi_id": self.kodi_id,
                    "media_type": self.media_type,
                    "title": self.title,
                    "percent": percent,
                    "queued_at": time.monotonic(),
                }
            )
        self._reset_current()

    def take_due(self, now=None):
        """
        <summary>
        Remove and return the queued items whose prompt may be shown now.
        </summary>
        <param name="now">The clock reading to judge by, time.monotonic() by default; tests pass their own.</param>
        <returns>The due items in the order they finished, or an empty list.</returns>
        <remarks>
        Nothing is due while a video is playing: the prompt would open over the next
        episode, and the service loop blocked inside it would stop tracking that
        episode's position, so stopping it early used to be read as 0 percent. Nothing
        is due within PLAYBACK_GRACE_SECONDS of being queued either, so the gap before
        Kodi starts the next playlist item does not count as "nothing playing". Items
        are taken all together, so a batch is shown one after the other.
        </remarks>
        """
        if not self.pending or self.isPlayingVideo():
            return []
        if now is None:
            now = time.monotonic()
        if any(now - item["queued_at"] < PLAYBACK_GRACE_SECONDS for item in self.pending):
            return []
        due, self.pending = self.pending, []
        return due


def process_item(item):
    """
    <summary>
    Show the rating dialog for a finished item and push it to Jellyfin.
    </summary>
    """
    if not ADDON.getSettingBool("enabled"):
        return

    threshold = ADDON.getSettingInt("min_percent")
    if item["percent"] < threshold:
        log(
            "Skipping '%s' (%.0f%% < %d%% threshold)"
            % (item["title"], item["percent"], threshold)
        )
        return

    creds = jellyfin.get_credentials()
    if not creds:
        log("No Jellyfin credentials found in plugin.video.jellyfin", xbmc.LOGWARNING)
        notify("Jellyfin login not found", error=True)
        return

    jellyfin_id = jellyfin.get_jellyfin_id(item["kodi_id"], item["media_type"])
    if not jellyfin_id:
        # Item did not come from Jellyfin (e.g. local-only library entry).
        log(
            "No Jellyfin id for kodi_id=%s type=%s; skipping"
            % (item["kodi_id"], item["media_type"])
        )
        return

    # Fetch the existing UserData only for the "previously rated" hint. It is never
    # posted back: the write sends the score alone (see jellyfin.set_rating).
    user_data = jellyfin.get_user_data(creds, jellyfin_id)
    previous = jellyfin.get_previous_rating(user_data)

    if previous is not None:
        default_rating = int(round(previous))
    else:
        default_rating = ADDON.getSettingInt("default_rating")

    rating = ask_rating(item["title"], default_rating, previous)
    if rating is None:
        return  # user cancelled

    try:
        ok = jellyfin.set_rating(creds, jellyfin_id, rating)
    except Exception as exc:
        log("Error sending rating: %s" % exc, xbmc.LOGERROR)
        ok = False

    # The score also goes into JellyStat's mirror when that add-on is
    # installed, even if the Jellyfin write just failed: local-first, the
    # same principle JellyStat itself applies.
    if ADDON.getSettingBool("save_to_jellystat"):
        jellystat_bridge.save(jellyfin_id, rating)

    if ok:
        notify("Rated %s: %d/10" % (item["title"], rating))
    else:
        notify("Failed to send rating to Jellyfin", error=True)


def _sync_api():
    """
    <summary>
    Bring the add-on API in line with its setting (api_server.sync).
    </summary>
    <remarks>
    Imported here so a broken API module can never stop ratings working: any failure
    is logged and the service carries on.
    </remarks>
    """
    try:
        import api_server
        api_server.sync()
    except Exception as exc:
        log("Add-on API could not start: %s" % exc, xbmc.LOGERROR)


def _stop_api():
    """
    <summary>
    Stop the add-on API if it is running, logging rather than raising on failure.
    </summary>
    """
    try:
        import api_server
        api_server.stop()
    except Exception as exc:
        log("Add-on API did not stop cleanly: %s" % exc, xbmc.LOGWARNING)


class ApiStartGate:
    """
    <summary>
    Decides when the add-on API may first announce itself, without blocking the
    service loop that also tracks playback.
    </summary>
    <remarks>
    The shape is Engage's _wait_for_gui: wait for the home screen to show, with
    HOME_BACKSTOP_SECONDS as a backstop, then rest API_SETTLE_SECONDS, then start
    whatever window is visible at that moment. The earlier gate needed Home to be
    visible on the very tick the rest ended, so a start up window other than Home, or
    a video started straight after login, kept the API off until the user happened to
    sit on Home.
    </remarks>
    """

    def __init__(self, started_at):
        """
        <summary>
        Start the backstop clock.
        </summary>
        <param name="started_at">The time.monotonic() reading when the service started.</param>
        """
        self.started_at = started_at
        self.home_seen_at = None
        self.opened = False

    def due(self, now, home_visible):
        """
        <summary>
        Report, once, that the settle period has ended.
        </summary>
        <param name="now">The current time.monotonic() reading.</param>
        <param name="home_visible">Whether Kodi says the home window is visible right now.</param>
        <returns>True on the single call where the API may start; False before and after.</returns>
        """
        if self.opened:
            return False
        if self.home_seen_at is None:
            if home_visible or now - self.started_at >= HOME_BACKSTOP_SECONDS:
                self.home_seen_at = now
            else:
                return False
        if now - self.home_seen_at >= API_SETTLE_SECONDS:
            self.opened = True
            return True
        return False


class JellyRateMonitor(xbmc.Monitor):
    """
    <summary>
    The service's abort wait, plus the settings callback that switches the add-on API
    on or off without a restart.
    </summary>
    """

    def __init__(self):
        """
        <summary>
        Start with the GUI not yet settled, so a settings change during start up does
        not bring the API up early.
        </summary>
        """
        super().__init__()
        self.gui_settled = False

    def onSettingsChanged(self):
        """
        <summary>
        Kodi hook after this add-on's settings are saved, from Configure or the API.
        </summary>
        """
        if self.gui_settled:
            _sync_api()


def main():
    """
    <summary>
    Service loop: record the playback position once a second, rate every finished item once nothing is playing, and stop when Kodi asks to abort.
    </summary>
    <remarks>
    Prompts wait until playback has stopped (JellyRatePlayer.take_due), so the dialog
    never opens over the next episode. The add-on API starts when ApiStartGate says
    so, and stops however the loop ends.
    </remarks>
    """
    log("JellyRate service started")
    monitor = JellyRateMonitor()
    player = JellyRatePlayer()
    gate = ApiStartGate(time.monotonic())

    try:
        while not monitor.abortRequested():
            player.track_position()
            for item in player.take_due():
                process_item(item)
            if not monitor.gui_settled and gate.due(
                    time.monotonic(), xbmc.getCondVisibility("Window.IsVisible(home)")):
                monitor.gui_settled = True
                _sync_api()
            if monitor.waitForAbort(1):
                break
    finally:
        _stop_api()

    log("JellyRate service stopped")


if __name__ == "__main__":
    main()
