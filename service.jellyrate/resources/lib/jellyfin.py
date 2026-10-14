# -*- coding: utf-8 -*-
"""
<summary>
Bridge to the Jellyfin for Kodi add-on and the Jellyfin HTTP API.
</summary>
<remarks>
We deliberately do NOT ask the user for a server address / login. Instead we
reuse whatever plugin.video.jellyfin has already stored, and we map the played
Kodi item to its Jellyfin id through plugin.video.jellyfin's sync database.
</remarks>
"""
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import xbmc
import xbmcvfs

JELLYFIN_ADDON_DATA = "special://profile/addon_data/plugin.video.jellyfin/"
LOG_PREFIX = "[service.jellyrate.jellyfin] "

# How long one Jellyfin call may take on the service and My Ratings paths. The
# add-on API passes its own, shorter figure (api_server.API_TIMEOUT).
DEFAULT_TIMEOUT = 15


class JellyfinError(Exception):
    """
    <summary>
    Jellyfin answered, but not usefully: a status other than 200 or a body that
    is not JSON. Network failures stay as urllib.error.URLError or OSError.
    </summary>
    """


def _log(message, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to Kodi's log under this module's tag.
    </summary>
    <param name="message">Text to log.</param>
    <param name="level">Kodi log level, info by default.</param>
    """
    xbmc.log(LOG_PREFIX + message, level)


def _translate(path):
    """
    <summary>
    Resolve a special:// path to a real filesystem path.
    </summary>
    <param name="path">A Kodi special path.</param>
    <returns>The translated path.</returns>
    """
    return xbmcvfs.translatePath(path)


# --------------------------------------------------------------------------- #
# Credentials (read from plugin.video.jellyfin/data.json)
# --------------------------------------------------------------------------- #
def get_credentials():
    """
    <summary>
    Return {'address', 'user_id', 'token'} or None if not signed in.
    </summary>
    <remarks>
    With more than one server in data.json the one Jellyfin for Kodi used most
    recently is taken, by its DateLastAccessed stamp, which is how that add-on
    itself picks the server to connect to. A server without the stamp sorts last.
    </remarks>
    """
    path = _translate(JELLYFIN_ADDON_DATA + "data.json")
    if not xbmcvfs.exists(path):
        _log("data.json not found at %s" % path, xbmc.LOGWARNING)
        return None

    handle = xbmcvfs.File(path)
    try:
        raw = handle.read()
    finally:
        handle.close()

    try:
        data = json.loads(raw)
    except ValueError as exc:
        _log("Could not parse data.json: %s" % exc, xbmc.LOGERROR)
        return None

    servers = [s for s in (data.get("Servers") or []) if isinstance(s, dict)]
    if not servers:
        return None

    # ISO 8601 stamps in one format sort correctly as text; newest first.
    server = sorted(servers, key=lambda s: str(s.get("DateLastAccessed") or ""),
                    reverse=True)[0]
    address = (
        server.get("address")
        or server.get("ManualAddress")
        or server.get("LocalAddress")
        or server.get("RemoteAddress")
    )
    user_id = server.get("UserId")
    token = server.get("AccessToken")

    if not (address and user_id and token):
        _log("data.json is missing address/UserId/AccessToken", xbmc.LOGWARNING)
        return None

    return {
        "address": address.rstrip("/"),
        "user_id": user_id,
        "token": token,
    }


# --------------------------------------------------------------------------- #
# Kodi id -> Jellyfin id (read from plugin.video.jellyfin's sync db)
# --------------------------------------------------------------------------- #
def _candidate_db_paths():
    """
    <summary>
    Every jellyfin*.db file in the Jellyfin for Kodi add-on data folder and in Kodi's database folder.
    </summary>
    <returns>A list of translated paths; a folder that cannot be listed contributes nothing.</returns>
    """
    paths = []
    for special in (JELLYFIN_ADDON_DATA, "special://database/"):
        directory = _translate(special)
        try:
            _, files = xbmcvfs.listdir(special)
        except Exception:
            files = []
        for name in files:
            if name.lower().startswith("jellyfin") and name.lower().endswith(".db"):
                paths.append(os.path.join(directory, name))
    return paths


def _find_mapping_db():
    """
    <summary>
    Locate the plugin.video.jellyfin database that holds the id mapping.
    </summary>
    """
    for path in _candidate_db_paths():
        try:
            con = sqlite3.connect(path)
            try:
                row = con.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='jellyfin'"
                ).fetchone()
            finally:
                con.close()
            if row:
                return path
        except sqlite3.Error:
            continue
    return None


def get_jellyfin_id(kodi_id, media_type):
    """
    <summary>
    Return the Jellyfin item id for a Kodi library item, or None.
    </summary>
    """
    db_path = _find_mapping_db()
    if not db_path:
        _log("No plugin.video.jellyfin mapping database found", xbmc.LOGWARNING)
        return None

    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT jellyfin_id FROM jellyfin "
            "WHERE kodi_id = ? AND media_type = ?",
            (kodi_id, media_type),
        ).fetchone()
    except sqlite3.Error as exc:
        _log("Mapping query failed: %s" % exc, xbmc.LOGERROR)
        return None
    finally:
        con.close()

    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Write the rating to Jellyfin
# --------------------------------------------------------------------------- #
def _request(url, token, method="GET", body=None, timeout=DEFAULT_TIMEOUT):
    """
    <summary>
    One authenticated HTTP call to Jellyfin.
    </summary>
    <param name="url">Full URL.</param>
    <param name="token">Access token, sent as X-Emby-Token.</param>
    <param name="method">HTTP method, GET by default.</param>
    <param name="body">A JSON serialisable body, or None for no body.</param>
    <param name="timeout">Seconds to wait for the connection and the response.</param>
    <returns>A tuple of the HTTP status and the raw response bytes.</returns>
    <exception cref="urllib.error.URLError">An error status or an unreachable server; nothing is caught here, callers decide.</exception>
    """
    headers = {
        "X-Emby-Token": token,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.status if hasattr(resp, "status") else resp.getcode()
        payload = resp.read()
        return status, payload


def _quote(value):
    """
    <summary>
    A user or item id made safe for a URL path segment.
    </summary>
    <param name="value">The id; anything that is not a string is stringified first.</param>
    <returns>The percent encoded text, with no character left as a separator.</returns>
    """
    return urllib.parse.quote(str(value), safe="")


def _user_data_url(creds, item_id):
    """
    <summary>
    The per user UserData endpoint for an item.
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="item_id">The Jellyfin item id.</param>
    <returns>The URL, with both ids percent encoded.</returns>
    """
    return "%s/Users/%s/Items/%s/UserData" % (
        creds["address"],
        _quote(creds["user_id"]),
        _quote(item_id),
    )


def get_user_data(creds, item_id, timeout=DEFAULT_TIMEOUT):
    """
    <summary>
    Return the current Jellyfin UserData dto for an item (or {} on failure).
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="item_id">The Jellyfin item id.</param>
    <param name="timeout">Seconds allowed for the call.</param>
    <remarks>
    Read only: this feeds the "previously rated" hint. Nothing in it is ever
    posted back, see set_rating.
    </remarks>
    """
    try:
        status, payload = _request(
            _user_data_url(creds, item_id), creds["token"], method="GET",
            timeout=timeout,
        )
        if status == 200 and payload:
            return json.loads(payload.decode("utf-8")) or {}
    except urllib.error.HTTPError as exc:
        # Older servers may not expose GET on this route.
        _log("GET UserData returned HTTP %s" % exc.code, xbmc.LOGWARNING)
    except Exception as exc:
        _log("GET UserData failed: %s" % exc, xbmc.LOGWARNING)
    return {}


def get_previous_rating(user_data):
    """
    <summary>
    Pull the existing 1-10 rating out of a UserData dto, or None.
    </summary>
    """
    if not user_data:
        return None
    rating = user_data.get("Rating")
    if rating is None:
        return None
    try:
        return float(rating)
    except (TypeError, ValueError):
        return None


def set_rating(creds, item_id, rating, timeout=DEFAULT_TIMEOUT):
    """
    <summary>
    Write a per user numeric rating (1 to 10) to Jellyfin UserData.
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="item_id">The Jellyfin item id.</param>
    <param name="rating">The score; sent as a float.</param>
    <param name="timeout">Seconds allowed for the call.</param>
    <returns>True when Jellyfin answered 200 or 204.</returns>
    <exception cref="urllib.error.URLError">An error status or an unreachable server.</exception>
    <remarks>
    The body is {"Rating": value} and nothing else. Jellyfin applies every field
    present in an UpdateUserItemDataDto and leaves the rest alone, so posting a
    dto fetched earlier would rewind Played, PlayCount, PlaybackPositionTicks and
    LastPlayedDate to whatever they were when it was fetched, which an earlier
    version of this function did.
    </remarks>
    """
    status, _ = _request(
        _user_data_url(creds, item_id), creds["token"], method="POST",
        body={"Rating": float(rating)}, timeout=timeout,
    )
    success = status in (200, 204)
    if not success:
        _log("POST UserData returned HTTP %s" % status, xbmc.LOGERROR)
    return success


# --------------------------------------------------------------------------- #
# Recently played items (for the "My Ratings" browser)
# --------------------------------------------------------------------------- #
def get_recent_played(creds, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    <summary>
    Return recently played movies/episodes, newest first.
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="limit">How many items to ask for.</param>
    <param name="timeout">Seconds allowed for the call.</param>
    <returns>A list of item dicts, possibly empty when nothing has been played.</returns>
    <exception cref="urllib.error.URLError">An unreachable server or an HTTP error status.</exception>
    <exception cref="JellyfinError">A status other than 200, or a body that is not JSON.</exception>
    <remarks>
    Each entry: {id, name, type, series, season, episode, rating,
    last_played, user_data}. Pulled straight from Jellyfin so it always
    reflects the server (including items rated on other devices). Failures
    raise rather than returning an empty list, so a caller can tell "nothing
    played" from "Jellyfin is not answering"; the add-on API turns them into 503.
    </remarks>
    """
    query = urllib.parse.urlencode(
        {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Episode",
            "Filters": "IsPlayed",
            "SortBy": "DatePlayed",
            "SortOrder": "Descending",
            "Fields": "SeriesName,ParentIndexNumber,IndexNumber",
            "EnableUserData": "true",
            "Limit": int(limit),
        }
    )
    url = "%s/Users/%s/Items?%s" % (creds["address"], _quote(creds["user_id"]), query)

    try:
        status, payload = _request(url, creds["token"], method="GET", timeout=timeout)
    except Exception as exc:
        _log("get_recent_played request failed: %s" % exc, xbmc.LOGERROR)
        raise

    if status != 200 or not payload:
        _log("get_recent_played returned HTTP %s" % status, xbmc.LOGWARNING)
        raise JellyfinError("Jellyfin answered HTTP %s" % status)

    try:
        data = json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        _log("get_recent_played parse error: %s" % exc, xbmc.LOGERROR)
        raise JellyfinError("Jellyfin's answer was not JSON")

    items = []
    for entry in data.get("Items", []):
        user_data = entry.get("UserData") or {}
        items.append(
            {
                "id": entry.get("Id"),
                "name": entry.get("Name") or "",
                "type": entry.get("Type") or "",
                "series": entry.get("SeriesName") or "",
                "season": entry.get("ParentIndexNumber"),
                "episode": entry.get("IndexNumber"),
                "rating": get_previous_rating(user_data),
                "last_played": user_data.get("LastPlayedDate") or "",
                "user_data": user_data,
            }
        )
    return items


def get_item(creds, item_id, timeout=DEFAULT_TIMEOUT):
    """
    <summary>
    One item with its user data, in the same shape get_recent_played gives.
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="item_id">The Jellyfin item id.</param>
    <param name="timeout">Seconds allowed for the call.</param>
    <returns>The item dict, or None when Jellyfin has no such item for this user.</returns>
    <exception cref="urllib.error.URLError">An unreachable server; a 404 is
    answered with None instead, since that is the item not existing.</exception>
    <remarks>
    Used by the add-on API to show and re-rate one title, so it asks the server
    afresh rather than trusting a list fetched earlier: the rating may have been
    changed on another device since.
    </remarks>
    """
    url = "%s/Users/%s/Items/%s" % (creds["address"], _quote(creds["user_id"]),
                                    _quote(item_id))
    try:
        status, payload = _request(url, creds["token"], method="GET", timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 404):
            return None
        raise
    if status != 200 or not payload:
        return None
    entry = json.loads(payload.decode("utf-8"))
    user_data = entry.get("UserData") or {}
    return {
        "id": entry.get("Id"),
        "name": entry.get("Name") or "",
        "type": entry.get("Type") or "",
        "series": entry.get("SeriesName") or "",
        "season": entry.get("ParentIndexNumber"),
        "episode": entry.get("IndexNumber"),
        "rating": get_previous_rating(user_data),
        "last_played": user_data.get("LastPlayedDate") or "",
        "user_data": user_data,
    }


# --------------------------------------------------------------------------- #
# Shared formatting
# --------------------------------------------------------------------------- #
def played_text(raw):
    """
    <summary>
    Jellyfin's LastPlayedDate, in UTC, as this box's local date and time in en-GB form.
    </summary>
    <param name="raw">An ISO 8601 timestamp such as '2026-10-13T18:30:00.0000000Z', or ''.</param>
    <returns>'13/10/2026 19:30', or '' when it is missing or unreadable.</returns>
    <remarks>
    Used by the My Ratings screen and the add-on API alike, so the two show the
    same text for the same play.
    </remarks>
    """
    try:
        when = datetime.strptime((raw or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return ""
    local = when.replace(tzinfo=timezone.utc).astimezone()
    return local.strftime("%d/%m/%Y %H:%M")
