# -*- coding: utf-8 -*-
"""
<summary>
JellyRate's add-on API: a small HTTP server on 127.0.0.1 through which the JellyStat
dashboard reads and changes JellyRate's settings and re-rates recently played titles.
</summary>
<remarks>
This implements version 1 of the shared add-on API spec (ADDON-API-SPEC.md, kept
beside the Kodi projects), the same contract Engage's server follows:

- It binds to 127.0.0.1 on a port the OS picks, never to the LAN. JellyStat, in the
  same Kodi, proxies to it behind its own password.
- It announces itself in the Home window property AddonApi.service.jellyrate as JSON
  holding the port and a token made fresh each start, never written to disk or
  logged. Every request must carry the token as X-Addon-Api-Token.
- Writes carry If-Match. Both resources use per record ETags ("etag": "record" in
  /info): the settings change only as a whole, and the recently played list moves
  every time anything finishes playing, so a list wide ETag would refuse a re-rate
  for no reason.

Two resources. "settings" is a single record (id 1) holding the five settings the
Configure screen shows. "ratings" is the recently played list straight from
Jellyfin, newest first; a record can be re-scored but not added or deleted, because
the titles are Jellyfin's. A re-rate goes exactly the way the My Ratings screen
sends one: to Jellyfin, then to JellyStat as well when that setting is on.

The service calls sync() once the GUI has settled, again whenever the settings
change, and stop() on the way out. handle() routes without a socket, which is what
the tests drive.
</remarks>
"""
import hashlib
import hmac
import json
import re
import secrets
import socket
import threading
import traceback
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import xbmc
import xbmcaddon
import xbmcgui

import jellyfin
import jellystat_bridge

ADDON_ID = "service.jellyrate"
API_VERSION = 1
HOME_WINDOW_ID = 10000
DISCOVERY_PROPERTY = "AddonApi." + ADDON_ID
SETTING_ID = "api_enabled"
TOKEN_HEADER = "X-Addon-Api-Token"

# Larger bodies are refused before they are read.
MAX_BODY = 2 ** 20

# How many recently played titles the ratings resource lists, the same as the My
# Ratings screen.
RECENT_LIMIT = 100

# Seconds allowed for each Jellyfin call made on behalf of the dashboard. JellyStat's
# proxy gives up after 10 seconds, and a re-rate makes two calls, so both have to
# finish inside that or the dashboard reports a failure for a write that then lands.
API_TIMEOUT = 4

# Seconds a request may stall on the socket (a body that never arrives in full) before
# the handler gives up on it; the handler thread used to block for ever.
SOCKET_TIMEOUT = 10

# Jellyfin failures the ratings routes turn into 503: the server unreachable or slow
# (URLError, OSError), an error status or a body that is not JSON (JellyfinError,
# ValueError).
_JELLYFIN_FAILURES = (urllib.error.URLError, OSError, ValueError, jellyfin.JellyfinError)

# The settings the API reads and writes, in the Configure screen's order, with the
# Configure screen's own labels and ranges. api_enabled is deliberately absent: the
# dashboard switching off the API it is talking through would strand it.
SETTINGS = [
    {"key": "enabled", "type": "boolean", "label": "Enable rating prompt",
     "default": True},
    {"key": "prompt_on_stop", "type": "boolean", "label": "Also prompt when you stop early",
     "default": True},
    {"key": "min_percent", "type": "integer",
     "label": "Only prompt after watching at least (%)",
     "min": 0, "max": 100, "step": 5, "default": 70},
    {"key": "default_rating", "type": "integer", "label": "Default rating shown on the stars",
     "min": 1, "max": 10, "step": 1, "default": 5},
    {"key": "save_to_jellystat", "type": "boolean", "label": "Also save the score in JellyStat",
     "default": True,
     "help": "When the JellyStat add-on is installed, each score is also recorded in its "
             "local statistics library. Has no effect when JellyStat is not installed."},
]
_SETTING_KEYS = [s["key"] for s in SETTINGS]

_SETTINGS_PATH = re.compile(r"^/settings/(\d+)$")
# Jellyfin item ids are 32 lowercase hex digits.
_RATING_PATH = re.compile(r"^/ratings/([0-9a-f]{32})$")

_server = None
_state_lock = threading.Lock()
_write_lock = threading.Lock()


class ApiError(Exception):
    """
    <summary>
    An error response in the spec's shape: a status, a code and a message for the user.
    </summary>
    """

    def __init__(self, status, code, message, field=None, headers=None):
        """
        <summary>
        Keep everything the error response needs.
        </summary>
        <param name="status">The HTTP status.</param>
        <param name="code">The spec's error code, such as 'stale'.</param>
        <param name="message">Plain UK English, shown to the user unchanged.</param>
        <param name="field">The field at fault, for a validation error.</param>
        <param name="headers">Extra response headers, such as Allow on a 405.</param>
        """
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.headers = headers or {}


def _log(message, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to Kodi's log under the JellyRate API tag.
    </summary>
    <param name="message">Text to log. Never the token.</param>
    <param name="level">Kodi log level, info by default.</param>
    """
    xbmc.log("[%s] API: %s" % (ADDON_ID, message), level)


def _addon():
    """
    <summary>
    A fresh Addon object, so every read sees the settings as they are now.
    </summary>
    <returns>An xbmcaddon.Addon.</returns>
    """
    return xbmcaddon.Addon(ADDON_ID)


# ---- starting and stopping ------------------------------------------------

def sync():
    """
    <summary>
    Start or stop the server to match the 'Allow JellyStat to manage JellyRate' setting.
    </summary>
    <remarks>
    Safe to call at any time and as often as wanted. A setting that cannot be read
    leaves the server stopped, the cautious answer.
    </remarks>
    """
    try:
        enabled = _addon().getSettingBool(SETTING_ID)
    except Exception as exc:
        _log("could not read the %s setting, leaving the API off: %s" % (SETTING_ID, exc),
             xbmc.LOGWARNING)
        enabled = False
    if enabled:
        start()
    else:
        stop()


def start():
    """
    <summary>
    Bind the server on a free loopback port, serve on a daemon thread and announce it.
    </summary>
    <remarks>
    The announcement goes up only after the bind succeeds, so the property never
    names a port nobody is listening on.
    </remarks>
    """
    global _server
    with _state_lock:
        if _server is not None:
            return
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.api_token = secrets.token_urlsafe(32)
        threading.Thread(target=server.serve_forever, name="JellyRateApi", daemon=True).start()
        _server = server
        port = server.server_address[1]
        xbmcgui.Window(HOME_WINDOW_ID).setProperty(DISCOVERY_PROPERTY, json.dumps({
            "api": API_VERSION,
            "port": port,
            "token": server.api_token,
            "version": _addon_version(),
        }))
        _log("listening on 127.0.0.1:%d" % port)


def stop():
    """
    <summary>
    Withdraw the announcement and shut the server down.
    </summary>
    <remarks>
    The property is cleared first, so JellyStat stops sending before the socket
    closes. shutdown() waits for the serve loop, at most half a second.
    </remarks>
    """
    global _server
    with _state_lock:
        if _server is None:
            return
        server, _server = _server, None
        try:
            xbmcgui.Window(HOME_WINDOW_ID).clearProperty(DISCOVERY_PROPERTY)
        except Exception as exc:
            _log("could not clear %s: %s" % (DISCOVERY_PROPERTY, exc), xbmc.LOGWARNING)
        server.shutdown()
        server.server_close()
        _log("stopped")


def _addon_version():
    """
    <summary>
    The running add-on version, or '' when Kodi will not say.
    </summary>
    """
    try:
        return _addon().getAddonInfo("version") or ""
    except Exception:
        return ""


# ---- shared helpers -------------------------------------------------------

def etag_of(value):
    """
    <summary>
    An ETag for any JSON value: a short hash of it, in quotes.
    </summary>
    <param name="value">The state a write must have been made against.</param>
    <returns>The ETag header value.</returns>
    """
    blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return '"%s"' % hashlib.sha256(blob).hexdigest()[:20]


def _check_match(current, if_match):
    """
    <summary>
    Refuse a write that does not carry the ETag of the state it would change.
    </summary>
    <param name="current">The ETag as things stand now.</param>
    <param name="if_match">The request's If-Match header, or None.</param>
    <exception cref="ApiError">428 when it is missing, 412 when it is stale.</exception>
    """
    if not if_match:
        raise ApiError(428, "precondition_required",
                       "This change has to say which version it was made against.")
    if if_match.strip() != current:
        raise ApiError(412, "stale", "This was changed somewhere else. Reload and try again.")


def _allow(method, allowed):
    """
    <summary>
    Refuse a method the path does not offer.
    </summary>
    <param name="method">The request method.</param>
    <param name="allowed">The methods the path offers.</param>
    <exception cref="ApiError">405, with an Allow header.</exception>
    """
    if method not in allowed:
        raise ApiError(405, "method_not_allowed", "%s is not offered here." % method,
                       headers={"Allow": ", ".join(allowed)})


def _is_int(value):
    """
    <summary>
    True for a JSON integer; JSON true and false arrive as bool, a subclass of int.
    </summary>
    """
    return isinstance(value, int) and not isinstance(value, bool)


# ---- settings -------------------------------------------------------------

def read_settings():
    """
    <summary>
    The five settings as they stand, typed.
    </summary>
    <returns>A dict keyed by setting id.</returns>
    """
    addon = _addon()
    values = {}
    for spec in SETTINGS:
        if spec["type"] == "boolean":
            values[spec["key"]] = addon.getSettingBool(spec["key"])
        else:
            values[spec["key"]] = addon.getSettingInt(spec["key"])
    return values


def _settings_record(values):
    """
    <summary>
    The settings in their API shape: id 1, a title, then the values.
    </summary>
    <param name="values">From read_settings().</param>
    <returns>A new dict.</returns>
    """
    record = {"id": 1, "title": "JellyRate settings"}
    record.update(values)
    return record


def check_settings(body):
    """
    <summary>
    Check sent settings against the Configure screen's own limits.
    </summary>
    <param name="body">The parsed JSON body.</param>
    <returns>The sent settings that JellyRate knows, typed. Unknown keys, and the
    read only id and title, are ignored.</returns>
    <exception cref="ApiError">422 naming the first setting that breaks a rule.</exception>
    <remarks>
    The integers must sit on the slider's own steps, so a minimum of 72% is refused
    just as the slider never offers it.
    </remarks>
    """
    if not isinstance(body, dict):
        raise ApiError(422, "validation", "The settings must be a JSON object.")
    checked = {}
    for spec in SETTINGS:
        key = spec["key"]
        if key not in body:
            continue
        value = body[key]
        if spec["type"] == "boolean":
            if not isinstance(value, bool):
                raise ApiError(422, "validation", "%s must be on or off." % spec["label"], key)
        elif (not _is_int(value) or not spec["min"] <= value <= spec["max"]
              or (value - spec["min"]) % spec["step"]):
            raise ApiError(422, "validation", "%s must be a whole number from %d to %d%s." % (
                spec["label"], spec["min"], spec["max"],
                ", in steps of %d" % spec["step"] if spec["step"] > 1 else ""), key)
        checked[key] = value
    return checked


def write_settings(values):
    """
    <summary>
    Store checked settings through Kodi, so the service and the Configure screen see
    them at once.
    </summary>
    <param name="values">From check_settings().</param>
    """
    addon = _addon()
    for key, value in values.items():
        if isinstance(value, bool):
            addon.setSettingBool(key, value)
        else:
            addon.setSettingInt(key, value)


# ---- ratings --------------------------------------------------------------

def _creds():
    """
    <summary>
    The Jellyfin login the Jellyfin for Kodi add-on holds.
    </summary>
    <returns>The credentials dict.</returns>
    <exception cref="ApiError">503 when there is none, which is the usual reason
    the ratings cannot be read.</exception>
    """
    creds = jellyfin.get_credentials()
    if not creds:
        raise ApiError(503, "unavailable",
                       "No Jellyfin login found. Sign in with the Jellyfin for Kodi add-on.")
    return creds


def _rating_record(item):
    """
    <summary>
    A recently played item in its API shape.
    </summary>
    <param name="item">An item dict from jellyfin.get_recent_played or get_item.</param>
    <returns>id, title, kind, played and score (read only), and rating, the score as a
    whole number or null when unrated.</returns>
    <remarks>
    The title is built by the My Ratings screen's own label function and the played
    date by the shared jellyfin.played_text, so an episode reads the same on the
    dashboard as in Kodi.
    </remarks>
    """
    from ratings_list import _format_label
    rating = item.get("rating")
    score = None if rating is None else int(round(rating))
    return {
        "id": item.get("id"),
        "title": _format_label(item),
        "kind": "Episode" if item.get("type") == "Episode" else "Film",
        "played": jellyfin.played_text(item.get("last_played")),
        "score": "Unrated" if score is None else "%d / 10" % score,
        "rating": score,
    }


def _rating_etag(item):
    """
    <summary>
    The ETag of one title's rating: what a re-rate must have been made against.
    </summary>
    <param name="item">The item as Jellyfin has it now.</param>
    """
    return etag_of({"id": item.get("id"), "rating": item.get("rating")})


def _fetch_item(creds, item_id):
    """
    <summary>
    One title from Jellyfin, asked afresh.
    </summary>
    <param name="creds">The Jellyfin login.</param>
    <param name="item_id">The item id from the path.</param>
    <returns>The item dict.</returns>
    <exception cref="ApiError">404 when Jellyfin has no such item, 503 when it does
    not answer.</exception>
    """
    try:
        item = jellyfin.get_item(creds, item_id, timeout=API_TIMEOUT)
    except _JELLYFIN_FAILURES as exc:
        _log("Jellyfin did not answer for %s: %s" % (item_id, exc), xbmc.LOGWARNING)
        raise ApiError(503, "unavailable", "Jellyfin is not answering. Try again shortly.")
    if not item:
        raise ApiError(404, "not_found", "Jellyfin has no such title.")
    return item


def _recent_items(creds):
    """
    <summary>
    The recently played titles from Jellyfin, for GET /ratings.
    </summary>
    <param name="creds">The Jellyfin login.</param>
    <returns>The item dicts jellyfin.get_recent_played gives; empty when nothing has
    been played.</returns>
    <exception cref="ApiError">503 when Jellyfin is unreachable, refuses or answers
    with something other than JSON, which the spec's Appendix B requires rather than
    an empty list the dashboard would show as "nothing played".</exception>
    """
    try:
        return jellyfin.get_recent_played(creds, limit=RECENT_LIMIT, timeout=API_TIMEOUT)
    except _JELLYFIN_FAILURES as exc:
        _log("Jellyfin did not answer for the recent list: %s" % exc, xbmc.LOGWARNING)
        raise ApiError(503, "unavailable", "Jellyfin is not answering. Try again shortly.")


def _rerate(item_id, body, if_match):
    """
    <summary>
    PUT /ratings/{id}: give a title a new score, as the My Ratings screen does.
    </summary>
    <param name="item_id">The Jellyfin item id from the path.</param>
    <param name="body">The parsed JSON body; only 'rating' is read.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>200 with the title as it now stands, and its new ETag.</returns>
    <remarks>
    The score goes to Jellyfin first; JellyStat gets it too when that setting is on,
    even if the Jellyfin write failed, the same local first rule the service follows
    after playback. Only a failed Jellyfin write is reported as a failure.

    No lock is held here: the ETag is checked against what Jellyfin holds at that
    moment, which no lock in this process can pin, and holding _write_lock across two
    network calls used to stall settings writes behind a slow server. The remaining
    race between the check and the write is the one the spec accepts for v1.
    </remarks>
    """
    if not isinstance(body, dict):
        raise ApiError(422, "validation", "The rating must be a JSON object.")
    rating = body.get("rating")
    if not _is_int(rating) or not 1 <= rating <= 10:
        raise ApiError(422, "validation", "The score must be a whole number from 1 to 10.",
                       "rating")
    creds = _creds()
    item = _fetch_item(creds, item_id)
    _check_match(_rating_etag(item), if_match)
    try:
        ok = jellyfin.set_rating(creds, item_id, rating, timeout=API_TIMEOUT)
    except Exception as exc:
        _log("Error sending rating: %s" % exc, xbmc.LOGERROR)
        ok = False
    if _addon().getSettingBool("save_to_jellystat"):
        jellystat_bridge.save(item_id, rating)
    if not ok:
        raise ApiError(503, "unavailable", "Jellyfin did not take the score. Try again shortly.")
    item["rating"] = float(rating)
    _log("re-rated %s to %d over the API" % (item_id, rating))
    return 200, _rating_record(item), {"ETag": _rating_etag(item)}


# ---- routing --------------------------------------------------------------

def info():
    """
    <summary>
    The body of GET /info.
    </summary>
    <remarks>
    Both resources declare per record ETags and no add or delete. The settings are a
    single record, which JellyStat opens directly.
    </remarks>
    """
    return {
        "api": API_VERSION,
        "id": ADDON_ID,
        "name": "JellyRate",
        "version": _addon_version(),
        "resources": [
            {"name": "settings", "label": "Settings", "label_one": "Settings",
             "title_field": "title", "single": True, "create": False, "delete": False,
             "etag": "record"},
            {"name": "ratings", "label": "Recent ratings", "label_one": "Rating",
             "title_field": "title", "create": False, "delete": False, "etag": "record"},
        ],
        "backup": True,
        "restore": True,
    }


def schema():
    """
    <summary>
    The body of GET /schema.
    </summary>
    <remarks>
    The settings record's title heads its page, so it is not listed as a field as
    well; the ratings list shows its readonly fields under each title.
    </remarks>
    """
    settings_fields = [dict(spec) for spec in SETTINGS]
    return {"resources": {
        "settings": {"id_type": "integer", "fields": settings_fields},
        "ratings": {"id_type": "string", "fields": [
            {"key": "title", "label": "Title", "type": "readonly"},
            {"key": "kind", "label": "Type", "type": "readonly"},
            {"key": "played", "label": "Last played", "type": "readonly"},
            {"key": "score", "label": "Score", "type": "readonly"},
            {"key": "rating", "label": "Score out of 10", "type": "integer", "required": True,
             "min": 1, "max": 10,
             "help": "Saved to Jellyfin, and to JellyStat as well when that setting is on."},
        ]},
    }}


def handle(method, path, query, body, if_match):
    """
    <summary>
    Route one request that has already passed the Host and token checks.
    </summary>
    <param name="method">'GET', 'POST', 'PUT' or 'DELETE'.</param>
    <param name="path">The URL path, without the query.</param>
    <param name="query">The parsed query, as parse_qs returns it (unused so far).</param>
    <param name="body">The parsed JSON body, or None for GET and DELETE.</param>
    <param name="if_match">The If-Match header, or None.</param>
    <returns>(status, payload or None, extra headers).</returns>
    <exception cref="ApiError">For every error response.</exception>
    """
    if path == "/info":
        _allow(method, ["GET"])
        return 200, info(), {}
    if path == "/schema":
        _allow(method, ["GET"])
        return 200, schema(), {}
    if path == "/settings":
        _allow(method, ["GET"])
        values = read_settings()
        return 200, {"items": [_settings_record(values)]}, {"ETag": etag_of(values)}
    match = _SETTINGS_PATH.match(path)
    if match:
        _allow(method, ["GET", "PUT"])
        if match.group(1) != "1":
            raise ApiError(404, "not_found", "There is only one set of settings.")
        if method == "GET":
            values = read_settings()
            return 200, _settings_record(values), {"ETag": etag_of(values)}
        changes = check_settings(body)
        with _write_lock:
            _check_match(etag_of(read_settings()), if_match)
            write_settings(changes)
            values = read_settings()
        _log("settings changed over the API: %s" % (", ".join(sorted(changes)) or "none"))
        return 200, _settings_record(values), {"ETag": etag_of(values)}
    if path == "/ratings":
        _allow(method, ["GET"])
        items = _recent_items(_creds())
        return 200, {"items": [_rating_record(i) for i in items if i.get("id")]}, {}
    match = _RATING_PATH.match(path)
    if match:
        _allow(method, ["GET", "PUT"])
        if method == "GET":
            item = _fetch_item(_creds(), match.group(1))
            return 200, _rating_record(item), {"ETag": _rating_etag(item)}
        return _rerate(match.group(1), body, if_match)
    if path == "/backup":
        _allow(method, ["GET"])
        values = read_settings()
        name = "jellyrate-backup-%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S")
        return 200, {"type": "jellyrate-backup", "format": 1, "settings": values}, {
            "ETag": etag_of(values),
            "Content-Disposition": 'attachment; filename="%s"' % name}
    if path == "/restore":
        _allow(method, ["POST"])
        if not isinstance(body, dict) or body.get("type") != "jellyrate-backup":
            raise ApiError(422, "validation", "That is not a JellyRate backup.")
        changes = check_settings(body.get("settings"))
        with _write_lock:
            _check_match(etag_of(read_settings()), if_match)
            write_settings(changes)
            values = read_settings()
        _log("restored %d settings over the API" % len(changes))
        return 200, {"restored": len(changes)}, {"ETag": etag_of(values)}
    raise ApiError(404, "not_found", "Nothing is served at %s." % path)


# ---- HTTP -----------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """
    <summary>
    One request: the Host and token checks, the body, then handle().
    </summary>
    <remarks>
    Responses are HTTP/1.0, one request per connection, so a refused request never
    leaves an unread body in a kept alive connection. The socket timeout bounds a
    body that stops arriving; without it a short body held the thread for ever.
    </remarks>
    """

    server_version = "JellyRateApi"
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        """
        <summary>
        Send the standard access line to kodi.log at debug level instead of stderr.
        </summary>
        <param name="fmt">The format string BaseHTTPRequestHandler passes.</param>
        <param name="args">Its arguments. The request line carries no token.</param>
        """
        _log(fmt % args, xbmc.LOGDEBUG)

    def do_GET(self):
        """<summary>Serve a GET.</summary>"""
        self._serve("GET")

    def do_POST(self):
        """<summary>Serve a POST.</summary>"""
        self._serve("POST")

    def do_PUT(self):
        """<summary>Serve a PUT.</summary>"""
        self._serve("PUT")

    def do_DELETE(self):
        """<summary>Serve a DELETE.</summary>"""
        self._serve("DELETE")

    def _serve(self, method):
        """
        <summary>
        Check the request, route it and send the response or the error.
        </summary>
        <param name="method">The request method.</param>
        <remarks>
        The Host check comes first, then the token, both before any body is read. An
        unexpected exception is logged in full to kodi.log and answered with a bare
        500, so no internals reach the dashboard.
        </remarks>
        """
        try:
            expected_host = "127.0.0.1:%d" % self.server.server_address[1]
            if (self.headers.get("Host") or "").strip() != expected_host:
                raise ApiError(400, "bad_request",
                               "This server only answers to %s." % expected_host)
            sent = (self.headers.get(TOKEN_HEADER) or "").encode("utf-8")
            if not hmac.compare_digest(sent, self.server.api_token.encode("utf-8")):
                raise ApiError(401, "unauthorised", "Missing or wrong token.")
            route = urlparse(self.path)
            body = self._read_body() if method in ("POST", "PUT") else None
            status, payload, headers = handle(method, route.path, parse_qs(route.query),
                                              body, self.headers.get("If-Match"))
            self._send(status, payload, headers)
        except ApiError as err:
            error = {"code": err.code, "message": err.message}
            if err.field:
                error["field"] = err.field
            self._send(err.status, {"error": error}, err.headers)
        except Exception:
            _log("request failed:\n%s" % traceback.format_exc(), xbmc.LOGERROR)
            self._send(500, {"error": {"code": "internal",
                                       "message": "JellyRate hit an error; kodi.log has the detail."}},
                       {})

    def _read_body(self):
        """
        <summary>
        Read and parse the JSON body.
        </summary>
        <returns>The parsed value.</returns>
        <exception cref="ApiError">413 when Content-Length is over MAX_BODY, 400 when it
        is missing, not a number, negative, or the body is not JSON.</exception>
        <remarks>
        A negative length is refused outright: rfile.read of a negative count reads
        until the client closes, which held the thread for ever.
        </remarks>
        """
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            raise ApiError(400, "bad_request", "The request needs a Content-Length.")
        if length < 0:
            raise ApiError(400, "bad_request", "The Content-Length cannot be negative.")
        if length > MAX_BODY:
            raise ApiError(413, "too_large", "The request is larger than %d bytes." % MAX_BODY)
        try:
            raw = self.rfile.read(length)
        except socket.timeout:
            raise ApiError(400, "bad_request", "The request body did not arrive in full.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_request", "The request body is not JSON.")

    def _send(self, status, payload, headers):
        """
        <summary>
        Write the status, headers and JSON body.
        </summary>
        <param name="status">The HTTP status.</param>
        <param name="payload">The value to send as JSON, or None for no body.</param>
        <param name="headers">Extra headers, such as ETag.</param>
        """
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if data:
            self.wfile.write(data)
