#!/usr/bin/env python3
"""
<summary>
Unit test for the Jellyfin client (resources/lib/jellyfin.py): the exact body of a
rating write, URL quoting, the recent list raising rather than returning an empty
list, timeouts, credential selection from data.json, the Kodi id mapping and the
shared played date text.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
No Kodi and no Jellyfin are needed: the xbmc modules are stubbed in sys.modules, a
loopback HTTP server stands in for Jellyfin and records every request it gets, and
data.json and jellyfin.db are temporary files.

Usage:
    python3 tests/test_jellyfin.py
</remarks>
"""

import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import types
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "service.jellyrate", "resources", "lib")

# ---- Kodi stubs -----------------------------------------------------------

FILES = {}
LISTINGS = {}


class _File:
    """<summary>Stand-in for xbmcvfs.File over the FILES dict.</summary>"""

    def __init__(self, path):
        """<summary>Remember which FILES entry to serve.</summary>"""
        self.path = path

    def read(self):
        """<summary>The whole text, as Kodi's File.read gives it.</summary>"""
        return FILES[self.path]

    def close(self):
        """<summary>Nothing to release.</summary>"""


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda path: path
xbmcvfs.exists = lambda path: path in FILES
xbmcvfs.File = _File
xbmcvfs.listdir = lambda path: ([], list(LISTINGS.get(path, [])))
for name, module in (("xbmc", xbmc), ("xbmcvfs", xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, LIB_DIR)

import jellyfin  # noqa: E402

# ---- fake Jellyfin server -------------------------------------------------

REQUESTS = []
RESPONSES = {}


class _Handler(BaseHTTPRequestHandler):
    """<summary>Record each request and answer with whatever RESPONSES holds for its path.</summary>"""

    def _serve(self):
        """<summary>Read any body, record the request, then answer with the canned status, payload and delay for the path.</summary>"""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        REQUESTS.append({"method": self.command, "path": self.path,
                         "headers": {k.lower(): v for k, v in self.headers.items()},
                         "body": json.loads(body.decode("utf-8")) if body else None})
        status, payload, delay = RESPONSES.get(self.path.split("?")[0], (404, b"", 0))
        if delay:
            time.sleep(delay)
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _serve
    do_POST = _serve

    def log_message(self, fmt, *args):
        """<summary>Keep the access log out of the test output.</summary>"""


class _Server(ThreadingHTTPServer):
    """<summary>The fake Jellyfin: one thread per request so a slow answer never holds up the next test, and a client that gave up (the timeout test) is not reported as an error.</summary>"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        """<summary>Swallow the broken pipe a client that timed out leaves behind.</summary>"""


SERVER = _Server(("127.0.0.1", 0), _Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
PORT = SERVER.server_address[1]
CREDS = {"address": "http://127.0.0.1:%d" % PORT, "user_id": "user one", "token": "tok"}

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


def raises(kinds, func, what):
    """<summary>Run func expecting one of the exception kinds; returns the exception or None.</summary>"""
    try:
        func()
    except kinds as exc:
        return exc
    except Exception as exc:  # noqa: BLE001, the test wants to name the wrong kind
        FAILURES.append("%s: raised %r instead" % (what, exc))
        print("FAIL: %s: raised %r instead" % (what, exc))
        return None
    FAILURES.append("%s: nothing raised" % what)
    print("FAIL: %s: nothing raised" % what)
    return None


# ---- tests ----------------------------------------------------------------

def test_set_rating_body():
    """<summary>The write carries the score and nothing else, whatever UserData was fetched before.</summary>"""
    print("running test_set_rating_body")
    REQUESTS.clear()
    RESPONSES["/Users/user%20one/Items/abc/UserData"] = (204, b"", 0)
    equal(jellyfin.set_rating(CREDS, "abc", 8), True, "204 is success")
    sent = REQUESTS[-1]
    equal(sent["method"], "POST", "method")
    equal(sent["path"], "/Users/user%20one/Items/abc/UserData", "user id quoted in the path")
    equal(sent["body"], {"Rating": 8.0}, "the body is the score alone, so Played and the resume point are untouched")
    equal(sent["headers"].get("x-emby-token"), "tok", "token header")
    check(sent["headers"].get("content-type", "").startswith("application/json"), "JSON content type")

    RESPONSES["/Users/user%20one/Items/a%20b%2Fc/UserData"] = (200, b"{}", 0)
    jellyfin.set_rating(CREDS, "a b/c", 3)
    equal(REQUESTS[-1]["path"], "/Users/user%20one/Items/a%20b%2Fc/UserData", "item id quoted in the path")

    RESPONSES["/Users/user%20one/Items/bad/UserData"] = (500, b"", 0)
    raises(urllib.error.HTTPError, lambda: jellyfin.set_rating(CREDS, "bad", 5), "an error status raises")


def test_user_data_and_item():
    """<summary>Reading UserData and one item, including the not found answers.</summary>"""
    print("running test_user_data_and_item")
    RESPONSES["/Users/user%20one/Items/abc/UserData"] = (200, json.dumps({"Rating": 7, "Played": True}).encode(), 0)
    equal(jellyfin.get_user_data(CREDS, "abc"), {"Rating": 7, "Played": True}, "user data read")
    equal(jellyfin.get_previous_rating({"Rating": 7}), 7.0, "previous rating as a float")
    equal(jellyfin.get_user_data(CREDS, "missing"), {}, "a 404 reads as no user data")
    RESPONSES["/Users/user%20one/Items/abc"] = (200, json.dumps({
        "Id": "abc", "Name": "Pilot", "Type": "Episode", "SeriesName": "Flash",
        "ParentIndexNumber": 1, "IndexNumber": 1,
        "UserData": {"Rating": 7, "LastPlayedDate": "2026-10-13T18:30:00.0000000Z"}}).encode(), 0)
    item = jellyfin.get_item(CREDS, "abc")
    equal((item["id"], item["series"], item["season"], item["episode"], item["rating"]),
          ("abc", "Flash", 1, 1, 7.0), "item shape")
    equal(jellyfin.get_item(CREDS, "missing"), None, "a 404 item is None")


def test_recent_played():
    """<summary>The recent list parses a good answer and raises on every failure rather than returning [].</summary>"""
    print("running test_recent_played")
    path = "/Users/user%20one/Items"
    RESPONSES[path] = (200, json.dumps({"Items": [
        {"Id": "a", "Name": "Alien", "Type": "Movie", "UserData": {"Rating": 7, "LastPlayedDate": "x"}},
        {"Id": "b", "Name": "Pilot", "Type": "Episode", "SeriesName": "Flash", "UserData": {}},
    ]}).encode(), 0)
    REQUESTS.clear()
    items = jellyfin.get_recent_played(CREDS, 50)
    equal([(i["id"], i["rating"]) for i in items], [("a", 7.0), ("b", None)], "items parsed")
    query = REQUESTS[-1]["path"].split("?", 1)[1]
    check("Limit=50" in query and "SortBy=DatePlayed" in query, "query carries the limit and sort: %s" % query)
    RESPONSES[path] = (200, json.dumps({"Items": []}).encode(), 0)
    equal(jellyfin.get_recent_played(CREDS), [], "nothing played is an empty list")
    RESPONSES[path] = (500, b"", 0)
    raises(urllib.error.HTTPError, lambda: jellyfin.get_recent_played(CREDS), "HTTP 500 raises")
    RESPONSES[path] = (200, b"<html>", 0)
    raises(jellyfin.JellyfinError, lambda: jellyfin.get_recent_played(CREDS), "a body that is not JSON raises")
    dead = dict(CREDS, address="http://127.0.0.1:1")
    raises((urllib.error.URLError, OSError), lambda: jellyfin.get_recent_played(dead), "a refused connection raises")


def test_timeout():
    """<summary>The timeout argument reaches the socket.</summary>"""
    print("running test_timeout")
    RESPONSES["/Users/user%20one/Items/slow"] = (200, b"{}", 1.5)
    started = time.monotonic()
    exc = raises((urllib.error.URLError, socket.timeout, OSError),
                 lambda: jellyfin.get_item(CREDS, "slow", timeout=0.3), "a slow answer times out")
    check(exc is not None and time.monotonic() - started < 1.4, "and within the timeout given")
    RESPONSES.pop("/Users/user%20one/Items/slow", None)


def test_credentials():
    """<summary>data.json parsing and the choice of the most recently used server.</summary>"""
    print("running test_credentials")
    path = jellyfin.JELLYFIN_ADDON_DATA + "data.json"
    FILES.pop(path, None)
    equal(jellyfin.get_credentials(), None, "no data.json")
    FILES[path] = "{not json"
    equal(jellyfin.get_credentials(), None, "unreadable data.json")
    FILES[path] = json.dumps({"Servers": []})
    equal(jellyfin.get_credentials(), None, "no servers")
    FILES[path] = json.dumps({"Servers": [{"address": "http://one", "UserId": "u1"}]})
    equal(jellyfin.get_credentials(), None, "a server without a token")
    FILES[path] = json.dumps({"Servers": [
        {"address": "http://old/", "UserId": "u1", "AccessToken": "t1",
         "DateLastAccessed": "2026-01-01T00:00:00Z"},
        {"address": "http://new", "UserId": "u2", "AccessToken": "t2",
         "DateLastAccessed": "2026-10-13T18:00:00Z"},
        {"address": "http://unstamped", "UserId": "u3", "AccessToken": "t3"},
    ]})
    equal(jellyfin.get_credentials(), {"address": "http://new", "user_id": "u2", "token": "t2"},
          "the most recently used server wins, not the first listed")
    FILES[path] = json.dumps({"Servers": [
        {"address": "http://only/", "UserId": "u1", "AccessToken": "t1"}]})
    equal(jellyfin.get_credentials()["address"], "http://only", "trailing slash dropped")


def test_mapping_db():
    """<summary>The Kodi id to Jellyfin id lookup through a jellyfin.db laid out like the Jellyfin for Kodi one.</summary>"""
    print("running test_mapping_db")
    folder = tempfile.mkdtemp(prefix="jellyrate-test-")
    db = os.path.join(folder, "jellyfin.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE jellyfin (jellyfin_id TEXT, kodi_id INTEGER, media_type TEXT)")
    con.executemany("INSERT INTO jellyfin VALUES (?, ?, ?)",
                    [("m" * 32, 5, "movie"), ("e" * 32, 5, "episode")])
    con.commit()
    con.close()
    LISTINGS[jellyfin.JELLYFIN_ADDON_DATA] = ["jellyfin.db"]
    original = jellyfin._translate
    jellyfin._translate = lambda special: folder if special == jellyfin.JELLYFIN_ADDON_DATA else special
    try:
        equal(jellyfin.get_jellyfin_id(5, "movie"), "m" * 32, "movie id 5")
        equal(jellyfin.get_jellyfin_id(5, "episode"), "e" * 32, "episode id 5 is a different item")
        equal(jellyfin.get_jellyfin_id(6, "movie"), None, "unknown id")
    finally:
        jellyfin._translate = original
        LISTINGS.clear()


def test_played_text():
    """<summary>The shared played date text is local time in en-GB form.</summary>"""
    print("running test_played_text")
    text = jellyfin.played_text("2026-10-13T18:30:00.0000000Z")
    check(len(text) == 16 and text[2] == "/" and text[5] == "/" and text[10] == " ", "DD/MM/YYYY HH:MM: %r" % text)
    check(text.startswith("13/10/2026") or text.startswith("14/10/2026"), "the date is the local one: %r" % text)
    equal(jellyfin.played_text(""), "", "empty")
    equal(jellyfin.played_text(None), "", "missing")
    equal(jellyfin.played_text("2026-10-13"), "", "a bare date is unreadable")


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    try:
        for test in (test_set_rating_body, test_user_data_and_item, test_recent_played,
                     test_timeout, test_credentials, test_mapping_db, test_played_text):
            test()
    finally:
        SERVER.shutdown()
        SERVER.server_close()
    if FAILURES:
        print("test_jellyfin: %d failure(s)" % len(FAILURES))
        return 1
    print("test_jellyfin: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
