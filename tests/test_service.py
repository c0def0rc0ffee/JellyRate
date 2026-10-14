#!/usr/bin/env python3
"""
<summary>
Unit test for the service (service.py): what the player queues and when a queued
item is due, the playlist case where the next episode must not be lost, playback
errors, the watched percentage threshold, the rating write and the API start gate.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
No Kodi is needed: the xbmc modules are stubbed in sys.modules before service.py is
imported, and the Jellyfin client, the JellyStat hand-off and the rating dialog are
replaced with fakes that record what they were asked.

Usage:
    python3 tests/test_service.py
</remarks>
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "service.jellyrate")

# ---- Kodi stubs -----------------------------------------------------------

SETTINGS = {"enabled": True, "prompt_on_stop": True, "min_percent": 70,
            "default_rating": 5, "save_to_jellystat": True, "api_enabled": True}
NOTIFICATIONS = []


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon over the SETTINGS dict.</summary>"""

    def __init__(self, addon_id=None):
        """<summary>Any id is accepted; there is one settings dict.</summary>"""

    def getSettingBool(self, key):
        """<summary>The setting as stored.</summary>"""
        return SETTINGS[key]

    def getSettingInt(self, key):
        """<summary>The setting as stored.</summary>"""
        return SETTINGS[key]

    def getAddonInfo(self, key):
        """<summary>Id, name and path of the add-on under test; '' for anything else.</summary>"""
        return {"id": "service.jellyrate", "name": "JellyRate", "path": ADDON_DIR}.get(key, "")


class _Dialog:
    """<summary>Stand-in for xbmcgui.Dialog that records notifications.</summary>"""

    def notification(self, heading, message, icon=None, time=0):
        """<summary>Record the message only.</summary>"""
        NOTIFICATIONS.append(message)


class _Player:
    """<summary>Stand-in for xbmc.Player: playing state and position set by the test.</summary>"""

    playing_video = False
    position = 0.0
    duration = 0.0
    tag = None

    def __init__(self):
        """<summary>Nothing to set up; the test sets the attributes directly.</summary>"""

    def isPlayingVideo(self):
        """<summary>Whatever the test set playing_video to.</summary>"""
        return self.playing_video

    def getTime(self):
        """<summary>The position the test set.</summary>"""
        return self.position

    def getTotalTime(self):
        """<summary>The duration the test set.</summary>"""
        return self.duration

    def getVideoInfoTag(self):
        """<summary>The tag the test set.</summary>"""
        return self.tag


class _Tag:
    """<summary>Stand-in for a video info tag.</summary>"""

    def __init__(self, db_id, media_type, title):
        """<summary>Keep the three values the service reads.</summary>"""
        self.db_id, self.media_type, self.title = db_id, media_type, title

    def getDbId(self):
        """<summary>The library id, or a non positive number for something not in the library.</summary>"""
        return self.db_id

    def getMediaType(self):
        """<summary>Kodi's media type name.</summary>"""
        return self.media_type

    def getTitle(self):
        """<summary>The title.</summary>"""
        return self.title


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmc.Player = _Player
xbmc.Monitor = object
xbmc.getCondVisibility = lambda cond: False
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.Window = object
xbmcgui.WindowXMLDialog = object
xbmcgui.Dialog = _Dialog
xbmcgui.NOTIFICATION_ERROR = "error"
xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK = 10, 92
xbmcgui.ACTION_MOVE_LEFT, xbmcgui.ACTION_MOVE_RIGHT = 1, 2
xbmcgui.ACTION_MOVE_UP, xbmcgui.ACTION_MOVE_DOWN = 3, 4
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda path: path
for name, module in (("xbmc", xbmc), ("xbmcaddon", xbmcaddon), ("xbmcgui", xbmcgui),
                     ("xbmcvfs", xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, ADDON_DIR)

import service  # noqa: E402

# ---- fakes for what the service calls -------------------------------------

SET_CALLS = []
BRIDGE_CALLS = []
ASKED = []
ANSWER = {"rating": 8}

service.jellyfin.get_credentials = lambda: {"address": "http://jellyfin.example", "user_id": "u", "token": "t"}
service.jellyfin.get_jellyfin_id = lambda kodi_id, media_type: "j%d" % kodi_id if kodi_id != 99 else None
service.jellyfin.get_user_data = lambda creds, item_id: {"Rating": 6, "Played": False, "PlaybackPositionTicks": 5}
service.jellyfin.set_rating = lambda creds, item_id, rating, timeout=15: SET_CALLS.append((item_id, rating)) or True
service.jellystat_bridge.save = lambda item_id, score: BRIDGE_CALLS.append((item_id, score)) or True


def fake_ask(title, default_value, previous):
    """<summary>Record the prompt and answer with ANSWER['rating'].</summary>"""
    ASKED.append((title, default_value, previous))
    return ANSWER["rating"]


service.ask_rating = fake_ask

# ---- helpers --------------------------------------------------------------

FAILURES = []


def check(condition, message):
    """<summary>Record a failure without stopping.</summary>"""
    if not condition:
        FAILURES.append(message)
        print("FAIL: %s" % message)


def equal(actual, expected, what):
    """<summary>check() for equality, printing both sides on failure.</summary>"""
    check(actual == expected, "%s: expected %r, got %r" % (what, expected, actual))


def summary(player):
    """<summary>The queue as (title, percent) pairs.</summary>"""
    return [(item["title"], round(item["percent"])) for item in player.pending]


# ---- tests ----------------------------------------------------------------

def test_queueing():
    """<summary>What ends up queued: natural end, early stop with the percentage, and what is dropped.</summary>"""
    print("running test_queueing")
    player = service.JellyRatePlayer()
    player.playing_video = True
    player.tag = _Tag(1, "movie", "Alien")
    player.onAVStarted()
    player.position, player.duration = 1800.0, 2000.0
    player.track_position()
    player.onPlayBackStopped()
    equal(summary(player), [("Alien", 90)], "an early stop queues the percentage reached")

    player.tag = _Tag(2, "episode", "Pilot")
    player.onAVStarted()
    player.onPlayBackEnded()
    equal(summary(player)[-1], ("Pilot", 100), "a natural end is fully watched")

    player.tag = _Tag(-1, "movie", "Not in the library")
    player.onAVStarted()
    player.onPlayBackEnded()
    player.tag = _Tag(3, "tvshow", "Wrong type")
    player.onAVStarted()
    player.onPlayBackEnded()
    equal(len(player.pending), 2, "no library id or an unrateable type is dropped")

    SETTINGS["prompt_on_stop"] = False
    player.tag = _Tag(4, "movie", "Stopped")
    player.onAVStarted()
    player.track_position()
    player.onPlayBackStopped()
    equal(len(player.pending), 2, "an early stop is dropped when prompting on stop is off")
    SETTINGS["prompt_on_stop"] = True


def test_playlist_waits_for_playback_to_stop():
    """<summary>The regression: episode 1 ends, episode 2 autoplays, is watched and stopped early; both must be queued with real percentages and nothing is due while episode 2 plays.</summary>"""
    print("running test_playlist_waits_for_playback_to_stop")
    player = service.JellyRatePlayer()
    player.playing_video = True
    player.tag = _Tag(1, "episode", "S01E01")
    player.position, player.duration = 2000.0, 2000.0
    player.onAVStarted()
    player.track_position()
    player.onPlayBackEnded()
    now = player.pending[0]["queued_at"]
    equal(player.take_due(now + 60), [], "nothing is due while a video is playing")

    player.tag = _Tag(2, "episode", "S01E02")
    player.onAVStarted()
    player.position, player.duration = 1900.0, 2000.0
    player.track_position()
    player.playing_video = False
    player.onPlayBackStopped()
    equal(summary(player), [("S01E01", 100), ("S01E02", 95)],
          "the second episode keeps its real percentage instead of 0")
    queued = player.pending[-1]["queued_at"]
    equal(player.take_due(queued + 0.5), [], "nothing is due inside the grace period after a stop")
    due = player.take_due(queued + service.PLAYBACK_GRACE_SECONDS)
    equal([item["title"] for item in due], ["S01E01", "S01E02"], "both are due, in order, once playback has stopped")
    equal(player.pending, [], "and the queue is emptied")
    equal(player.take_due(queued + 100), [], "an empty queue is never due")


def test_errors_and_audio_reset():
    """<summary>A playback error, or audio starting, forgets the tracked video so it is never queued later.</summary>"""
    print("running test_errors_and_audio_reset")
    player = service.JellyRatePlayer()
    player.playing_video = True
    player.tag = _Tag(1, "movie", "Broken")
    player.onAVStarted()
    player.onPlayBackError()
    equal(player.kodi_id, None, "an error clears the tracked item")
    player.onPlayBackEnded()
    equal(player.pending, [], "and nothing is queued when the next thing ends")

    player.tag = _Tag(2, "movie", "Film")
    player.onAVStarted()
    player.playing_video = False
    player.onAVStarted()
    equal(player.kodi_id, None, "audio starting clears the tracked video")
    player.onPlayBackEnded()
    equal(player.pending, [], "the song ending queues nothing")


def test_process_item():
    """<summary>The threshold, the prompt's starting values and the write with the score alone.</summary>"""
    print("running test_process_item")
    SET_CALLS.clear()
    BRIDGE_CALLS.clear()
    ASKED.clear()
    NOTIFICATIONS.clear()
    service.process_item({"kodi_id": 1, "media_type": "movie", "title": "Short", "percent": 40.0})
    equal(ASKED, [], "below the threshold nothing is asked")
    service.process_item({"kodi_id": 99, "media_type": "movie", "title": "Local", "percent": 100.0})
    equal(ASKED, [], "no Jellyfin id, nothing asked")
    service.process_item({"kodi_id": 1, "media_type": "movie", "title": "Alien", "percent": 100.0})
    equal(ASKED, [("Alien", 6, 6.0)], "the stars start on the previous rating")
    equal(SET_CALLS, [("j1", 8)], "the score is written with no user data to post back")
    equal(BRIDGE_CALLS, [("j1", 8)], "and handed to JellyStat")
    check(any(n.startswith("Rated Alien") for n in NOTIFICATIONS), "the toast names the title")

    ANSWER["rating"] = None
    SET_CALLS.clear()
    service.process_item({"kodi_id": 1, "media_type": "movie", "title": "Alien", "percent": 100.0})
    equal(SET_CALLS, [], "cancelling writes nothing")
    ANSWER["rating"] = 8

    SETTINGS["enabled"] = False
    ASKED.clear()
    service.process_item({"kodi_id": 1, "media_type": "movie", "title": "Alien", "percent": 100.0})
    equal(ASKED, [], "the master switch off asks nothing")
    SETTINGS["enabled"] = True


def test_api_start_gate():
    """<summary>The API starts once Home has been seen and the settle period has passed, with a backstop when Home never shows.</summary>"""
    print("running test_api_start_gate")
    settle, backstop = service.API_SETTLE_SECONDS, service.HOME_BACKSTOP_SECONDS

    gate = service.ApiStartGate(1000.0)
    equal(gate.due(1001.0, True), False, "not on first sight of Home")
    equal(gate.due(1001.0 + settle - 1, False), False, "not before the settle period ends")
    equal(gate.due(1001.0 + settle, False), True, "due when the settle period ends, whatever window is showing")
    equal(gate.due(1001.0 + settle + 5, True), False, "only once")

    gate = service.ApiStartGate(1000.0)
    for second in range(backstop):
        check(gate.due(1000.0 + second, False) is False, "nothing before the backstop")
    equal(gate.due(1000.0 + backstop, False), False, "the backstop starts the settle period")
    equal(gate.due(1000.0 + backstop + settle, False), True, "and the API starts after it with Home never seen")

    gate = service.ApiStartGate(1000.0)
    gate.due(1005.0, True)
    equal(gate.due(1005.0 + settle, False), True, "Home seen late then left: due from when it was seen")


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_queueing, test_playlist_waits_for_playback_to_stop, test_errors_and_audio_reset,
                 test_process_item, test_api_start_gate):
        test()
    if FAILURES:
        print("test_service: %d failure(s)" % len(FAILURES))
        return 1
    print("test_service: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
