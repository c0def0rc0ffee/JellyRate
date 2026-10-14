#!/usr/bin/env python3
"""
<summary>
Unit test for JellyRate's add-on API (resources/lib/api_server.py): the settings
record and its limits, re-rating a recently played title, the per record ETags,
backup and restore, every error code, and a real loopback server for the Host,
token, size and discovery checks.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
No Kodi and no Jellyfin are needed: the xbmc modules are stubbed in sys.modules
before the add-on is imported, settings live in a dict, and the Jellyfin calls and
the JellyStat hand-off are replaced with fakes that record what they were asked.

Usage:
    python3 tests/test_api.py
</remarks>
"""

import http.client
import json
import os
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "service.jellyrate", "resources", "lib")

# ---- Kodi stubs -----------------------------------------------------------

PROPS = {}
SETTINGS = {"enabled": True, "prompt_on_stop": True, "min_percent": 70,
            "default_rating": 5, "save_to_jellystat": True, "api_enabled": True}


class _Window:
    """<summary>Stand-in for xbmcgui.Window over the PROPS dict.</summary>"""

    def __init__(self, window_id):
        assert window_id == 10000, "the Home window"

    def getProperty(self, key):
        return PROPS.get(key, "")

    def setProperty(self, key, value):
        PROPS[key] = value

    def clearProperty(self, key):
        PROPS.pop(key, None)


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon over the SETTINGS dict, typed like Kodi's.</summary>"""

    def __init__(self, addon_id=None):
        pass

    def getSettingBool(self, key):
        value = SETTINGS[key]
        assert isinstance(value, bool), "%s read as a bool" % key
        return value

    def getSettingInt(self, key):
        value = SETTINGS[key]
        assert isinstance(value, int) and not isinstance(value, bool), "%s read as an int" % key
        return value

    def setSettingBool(self, key, value):
        assert isinstance(value, bool)
        SETTINGS[key] = value

    def setSettingInt(self, key, value):
        assert isinstance(value, int) and not isinstance(value, bool)
        SETTINGS[key] = value

    def getAddonInfo(self, key):
        return {"version": "9.9.9", "name": "JellyRate", "path": ROOT, "icon": ""}.get(key, "")


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmc.Monitor = object
xbmc.Player = object
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.Window = _Window
xbmcgui.WindowXMLDialog = object
xbmcgui.Dialog = object
xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK = 10, 92
xbmcgui.NOTIFICATION_INFO = xbmcgui.NOTIFICATION_WARNING = xbmcgui.NOTIFICATION_ERROR = "info"
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda path: path
for name, module in (("xbmc", xbmc), ("xbmcaddon", xbmcaddon), ("xbmcgui", xbmcgui),
                     ("xbmcvfs", xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, LIB_DIR)

import api_server  # noqa: E402
import jellyfin  # noqa: E402
import jellystat_bridge  # noqa: E402

# ---- fake Jellyfin --------------------------------------------------------

FILM = "a" * 32
EPISODE = "b" * 32
CREDS = {"address": "http://jellyfin.example", "user_id": "u", "token": "t"}
LIBRARY = {
    FILM: {"id": FILM, "name": "Alien", "type": "Movie", "series": "", "season": None,
           "episode": None, "rating": 7.0, "last_played": "2026-10-13T18:30:00.0000000Z",
           "user_data": {"Rating": 7.0, "Played": True}},
    EPISODE: {"id": EPISODE, "name": "Pilot", "type": "Episode", "series": "Flash",
              "season": 1, "episode": 1, "rating": None, "last_played": "",
              "user_data": {"Played": True}},
}
STATE = {"creds": CREDS, "down": False, "refuse": False, "list_error": None}
SET_CALLS = []
BRIDGE_CALLS = []


def fake_get_item(creds, item_id, timeout=None):
    """<summary>One item from LIBRARY, a copy so the API cannot change the fake by accident.</summary>"""
    if STATE["down"]:
        raise OSError("connection refused")
    item = LIBRARY.get(item_id)
    return json.loads(json.dumps(item)) if item else None


def fake_set_rating(creds, item_id, rating, timeout=None):
    """<summary>Record the write, with the timeout it was given, and store it unless the fake server is refusing.</summary>"""
    SET_CALLS.append((item_id, rating, timeout))
    if STATE["refuse"]:
        return False
    LIBRARY[item_id]["rating"] = float(rating)
    return True


def fake_get_recent_played(creds, limit=100, timeout=None):
    """<summary>Every LIBRARY item as a copy, or whatever failure STATE['list_error'] holds, raised.</summary>"""
    if STATE["list_error"] is not None:
        raise STATE["list_error"]
    return [json.loads(json.dumps(i)) for i in LIBRARY.values()]


jellyfin.get_credentials = lambda: STATE["creds"]
jellyfin.get_recent_played = fake_get_recent_played
jellyfin.get_item = fake_get_item
jellyfin.set_rating = fake_set_rating
jellystat_bridge.save = lambda item_id, score: BRIDGE_CALLS.append((item_id, score)) or True

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


def api_error(method, path, body=None, if_match=None):
    """<summary>Run handle() expecting an error; returns (status, code, field).</summary>"""
    try:
        api_server.handle(method, path, {}, body, if_match)
    except api_server.ApiError as err:
        return err.status, err.code, err.field
    return None, None, None


def call(method, path, body=None, if_match=None):
    """<summary>Run handle() expecting success.</summary>"""
    return api_server.handle(method, path, {}, body, if_match)


# ---- tests ----------------------------------------------------------------

def test_info_schema():
    """<summary>Both resources described, and the schema covers every setting.</summary>"""
    print("running test_info_schema")
    _, info, _ = call("GET", "/info")
    resources = {r["name"]: r for r in info["resources"]}
    equal(sorted(resources), ["ratings", "settings"], "resources")
    equal((resources["settings"]["single"], resources["settings"]["create"],
           resources["settings"]["delete"], resources["settings"]["etag"]),
          (True, False, False, "record"), "settings is a single record with record ETags")
    equal((resources["ratings"]["create"], resources["ratings"]["delete"]), (False, False),
          "ratings cannot be added or deleted")
    _, schema, _ = call("GET", "/schema")
    keys = [f["key"] for f in schema["resources"]["settings"]["fields"] if f["type"] != "readonly"]
    equal(keys, api_server._SETTING_KEYS, "every setting in the schema, in order")
    check("api_enabled" not in keys, "the API switch is not offered to the dashboard")


def test_settings():
    """<summary>The settings record, its limits and its ETag.</summary>"""
    print("running test_settings")
    status, listing, headers = call("GET", "/settings")
    equal(status, 200, "list")
    equal(len(listing["items"]), 1, "one record")
    record = listing["items"][0]
    equal((record["id"], record["min_percent"], record["enabled"]), (1, 70, True), "values read")
    etag = headers["ETag"]
    equal(call("GET", "/settings/1")[2]["ETag"], etag, "record and list share an ETag")
    equal(api_error("GET", "/settings/2")[:2], (404, "not_found"), "only id 1")
    equal(api_error("POST", "/settings")[:2], (405, "method_not_allowed"), "no add")
    equal(api_error("DELETE", "/settings/1")[:2], (405, "method_not_allowed"), "no delete")

    equal(api_error("PUT", "/settings/1", {"min_percent": 75}), (428, "precondition_required", None),
          "write without If-Match")
    equal(api_error("PUT", "/settings/1", {"min_percent": 75}, '"old"'), (412, "stale", None),
          "stale write")
    for body, field in (({"min_percent": 72}, "min_percent"), ({"min_percent": 105}, "min_percent"),
                        ({"default_rating": 0}, "default_rating"), ({"enabled": 1}, "enabled"),
                        ({"default_rating": True}, "default_rating"),
                        ({"save_to_jellystat": "yes"}, "save_to_jellystat")):
        equal(api_error("PUT", "/settings/1", body, etag), (422, "validation", field),
              "refused %r" % body)
    equal(SETTINGS["min_percent"], 70, "nothing written by a refused change")

    status, record, headers = call("PUT", "/settings/1",
                                   {"min_percent": 75, "enabled": False, "title": "ignored",
                                    "api_enabled": False, "id": 9}, etag)
    equal((status, record["min_percent"], record["enabled"]), (200, 75, False), "changed")
    equal(SETTINGS["api_enabled"], True, "the API switch cannot be changed through the API")
    check(headers["ETag"] != etag, "ETag moved")
    equal(api_error("PUT", "/settings/1", {"min_percent": 80}, etag)[:2], (412, "stale"),
          "the old ETag is refused after a change")
    SETTINGS.update({"min_percent": 70, "enabled": True})


def test_ratings():
    """<summary>Listing, reading and re-rating, the JellyStat hand-off and the failures.</summary>"""
    print("running test_ratings")
    status, listing, headers = call("GET", "/ratings")
    equal(status, 200, "list")
    check("ETag" not in headers, "no collection ETag for record scoped resources")
    films = {r["id"]: r for r in listing["items"]}
    equal(films[FILM]["score"], "7 / 10", "score text")
    equal(films[FILM]["rating"], 7, "rating as a whole number")
    equal(films[EPISODE]["title"], "Flash S01E01 · Pilot", "episode label as the My Ratings screen")
    equal((films[EPISODE]["score"], films[EPISODE]["rating"]), ("Unrated", None), "unrated")
    check(films[FILM]["played"].startswith("13/10/2026"), "played in en-GB: %r" % films[FILM]["played"])

    _, record, headers = call("GET", "/ratings/" + FILM)
    etag = headers["ETag"]
    equal(record["rating"], 7, "read one")
    equal(api_error("GET", "/ratings/" + "c" * 32)[:2], (404, "not_found"), "unknown title")
    equal(api_error("GET", "/ratings/not-an-id")[:2], (404, "not_found"), "malformed id")
    equal(api_error("POST", "/ratings")[:2], (405, "method_not_allowed"), "no add")
    equal(api_error("DELETE", "/ratings/" + FILM)[:2], (405, "method_not_allowed"), "no delete")

    for bad in (0, 11, 7.5, True, "8", None):
        equal(api_error("PUT", "/ratings/" + FILM, {"rating": bad}, etag), (422, "validation", "rating"),
              "score %r refused" % (bad,))
    equal(api_error("PUT", "/ratings/" + FILM, {"rating": 8}), (428, "precondition_required", None),
          "re-rate without If-Match")

    SET_CALLS.clear()
    BRIDGE_CALLS.clear()
    status, record, headers = call("PUT", "/ratings/" + FILM, {"rating": 9, "title": "x"}, etag)
    equal((status, record["rating"], record["score"]), (200, 9, "9 / 10"), "re-rated")
    equal(SET_CALLS[-1][:2], (FILM, 9), "written to Jellyfin")
    equal(SET_CALLS[-1][2], api_server.API_TIMEOUT, "the dashboard's shorter timeout is used")
    check(2 * api_server.API_TIMEOUT < 10, "two Jellyfin calls fit inside JellyStat's 10 second proxy limit")
    equal(BRIDGE_CALLS, [(FILM, 9)], "handed to JellyStat")
    equal(api_error("PUT", "/ratings/" + FILM, {"rating": 3}, etag)[:2], (412, "stale"),
          "a re-rate against the old score is refused")
    new_etag = headers["ETag"]

    SETTINGS["save_to_jellystat"] = False
    BRIDGE_CALLS.clear()
    _, _, headers = call("PUT", "/ratings/" + FILM, {"rating": 8}, new_etag)
    equal(BRIDGE_CALLS, [], "not handed to JellyStat when that setting is off")
    SETTINGS["save_to_jellystat"] = True

    STATE["refuse"] = True
    BRIDGE_CALLS.clear()
    equal(api_error("PUT", "/ratings/" + FILM, {"rating": 6}, headers["ETag"])[:2], (503, "unavailable"),
          "Jellyfin refusing is reported")
    equal(BRIDGE_CALLS, [(FILM, 6)], "JellyStat still gets it, local first, as after playback")
    STATE["refuse"] = False

    STATE["down"] = True
    equal(api_error("GET", "/ratings/" + FILM)[:2], (503, "unavailable"), "Jellyfin down")
    STATE["down"] = False
    STATE["creds"] = None
    equal(api_error("GET", "/ratings")[:2], (503, "unavailable"), "no Jellyfin login")
    STATE["creds"] = CREDS
    for failure in (OSError("connection refused"), jellyfin.JellyfinError("HTTP 500"),
                    ValueError("not JSON")):
        STATE["list_error"] = failure
        equal(api_error("GET", "/ratings")[:2], (503, "unavailable"),
              "the list answers 503, not an empty list, on %r" % failure)
    STATE["list_error"] = None


def test_backup_restore():
    """<summary>Backup carries the settings; restore checks them and ignores unknowns.</summary>"""
    print("running test_backup_restore")
    status, backup, headers = call("GET", "/backup")
    equal((status, backup["type"], backup["settings"]["min_percent"]), (200, "jellyrate-backup", 70),
          "backup")
    check(headers["Content-Disposition"].startswith('attachment; filename="jellyrate-backup-'),
          "download name")
    etag = headers["ETag"]
    equal(api_error("POST", "/restore", {"type": "engage-backup"}, etag)[:2], (422, "validation"),
          "someone else's backup")
    bad = {"type": "jellyrate-backup", "settings": {"min_percent": 3}}
    equal(api_error("POST", "/restore", bad, etag), (422, "validation", "min_percent"), "bad value")
    doc = {"type": "jellyrate-backup", "format": 1,
           "settings": {"min_percent": 90, "default_rating": 8, "api_enabled": False, "x": 1}}
    status, payload, _ = call("POST", "/restore", doc, etag)
    equal((status, payload), (200, {"restored": 2}), "restore")
    equal((SETTINGS["min_percent"], SETTINGS["default_rating"], SETTINGS["api_enabled"]),
          (90, 8, True), "values restored, the API switch untouched")
    SETTINGS.update({"min_percent": 70, "default_rating": 5})


def request(port, method, path, token=None, host=None, body=None, headers=None):
    """<summary>One request to the loopback server; returns (status, parsed body or None).</summary>"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = {"Host": host or "127.0.0.1:%d" % port}
    if token is not None:
        sent[api_server.TOKEN_HEADER] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    sent.update(headers or {})
    try:
        conn.putrequest(method, path, skip_host=True)
        for name, value in sent.items():
            conn.putheader(name, value)
        if data is not None and "Content-Length" not in sent:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


def test_server():
    """<summary>Bind, discovery, Host and token checks, size limit, setting and stop.</summary>"""
    print("running test_server")
    PROPS.clear()
    SETTINGS["api_enabled"] = False
    api_server.sync()
    check(api_server.DISCOVERY_PROPERTY not in PROPS, "setting off: nothing announced")
    SETTINGS["api_enabled"] = True
    api_server.sync()
    try:
        announced = json.loads(PROPS.get(api_server.DISCOVERY_PROPERTY, "{}"))
        equal(sorted(announced), ["api", "port", "token", "version"], "discovery keys")
        port, token = announced["port"], announced["token"]
        equal(request(port, "GET", "/info", token)[0], 200, "info over HTTP")
        equal(request(port, "GET", "/info")[0], 401, "no token")
        equal(request(port, "GET", "/info", token, host="evil.example:%d" % port)[0], 400, "wrong Host")
        status, body = request(port, "PUT", "/settings/1", token, body={},
                               headers={"Content-Length": str(api_server.MAX_BODY + 1)})
        equal((status, body["error"]["code"]), (413, "too_large"), "oversized body")
        status, body = request(port, "PUT", "/settings/1", token, body={},
                               headers={"Content-Length": "-1"})
        equal((status, body["error"]["code"]), (400, "bad_request"),
              "a negative Content-Length is refused instead of blocking the thread")
        api_server._Handler.timeout = 1
        try:
            started = time.monotonic()
            status, body = request(port, "PUT", "/settings/1", token, body={},
                                   headers={"Content-Length": "40"})
            equal((status, body["error"]["code"]), (400, "bad_request"),
                  "a body shorter than its Content-Length is given up on")
            check(time.monotonic() - started < 5, "and given up on within the socket timeout")
        finally:
            api_server._Handler.timeout = api_server.SOCKET_TIMEOUT
    finally:
        SETTINGS["api_enabled"] = False
        api_server.sync()
        SETTINGS["api_enabled"] = True
    check(api_server.DISCOVERY_PROPERTY not in PROPS, "stop withdraws the announcement")


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_info_schema, test_settings, test_ratings, test_backup_restore, test_server):
        test()
    if FAILURES:
        print("test_api: %d failure(s)" % len(FAILURES))
        return 1
    print("test_api: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
