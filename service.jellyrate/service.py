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
        </remarks>
        """
        if not self.isPlayingVideo():
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
        # Played to the natural end -> treat as 100% watched.
        """
        <summary>
        Kodi hook: playback reached its natural end, so queue the item as fully watched.
        </summary>
        """
        self._enqueue(100.0)

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
                }
            )
        self._reset_current()


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

    # Fetch existing UserData so we can show the previous rating and reuse the
    # dto when writing back (avoids a second round-trip).
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
        ok = jellyfin.set_rating(creds, jellyfin_id, rating, user_data=user_data)
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


def main():
    """
    <summary>
    Service loop: record the playback position once a second, rate every finished item in the queue, and stop when Kodi asks to abort.
    </summary>
    """
    log("JellyRate service started")
    monitor = xbmc.Monitor()
    player = JellyRatePlayer()

    while not monitor.abortRequested():
        player.track_position()
        while player.pending:
            process_item(player.pending.pop(0))
        if monitor.waitForAbort(1):
            break

    log("JellyRate service stopped")


if __name__ == "__main__":
    main()
