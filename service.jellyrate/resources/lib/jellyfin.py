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

import xbmc
import xbmcvfs

JELLYFIN_ADDON_DATA = "special://profile/addon_data/plugin.video.jellyfin/"
LOG_PREFIX = "[service.jellyrate.jellyfin] "


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

    servers = data.get("Servers") or []
    if not servers:
        return None

    server = servers[0]
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
def _request(url, token, method="GET", body=None):
    """
    <summary>
    One authenticated HTTP call to Jellyfin with a 15 second timeout.
    </summary>
    <param name="url">Full URL.</param>
    <param name="token">Access token, sent as X-Emby-Token.</param>
    <param name="method">HTTP method, GET by default.</param>
    <param name="body">A JSON serialisable body, or None for no body.</param>
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        status = getattr(resp, "status", resp.getcode())
        payload = resp.read()
        return status, payload


def _user_data_url(creds, item_id):
    """
    <summary>
    The per user UserData endpoint for an item.
    </summary>
    <param name="creds">A credentials dict from get_credentials().</param>
    <param name="item_id">The Jellyfin item id.</param>
    <returns>The URL.</returns>
    """
    return "%s/Users/%s/Items/%s/UserData" % (
        creds["address"],
        creds["user_id"],
        item_id,
    )


def get_user_data(creds, item_id):
    """
    <summary>
    Return the current Jellyfin UserData dto for an item (or {} on failure).
    </summary>
    """
    try:
        status, payload = _request(
            _user_data_url(creds, item_id), creds["token"], method="GET"
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


def set_rating(creds, item_id, rating, user_data=None):
    """
    <summary>
    Write a per-user numeric rating (1-10) to Jellyfin UserData.
    </summary>
    <remarks>
    Posts the existing UserData back with Rating updated so that other fields
    (played state, playback position, etc.) are preserved. Pass ``user_data``
    if you already fetched it to avoid a second request.
    </remarks>
    """
    dto = dict(user_data) if user_data else get_user_data(creds, item_id)
    dto["Rating"] = float(rating)

    status, _ = _request(
        _user_data_url(creds, item_id), creds["token"], method="POST", body=dto
    )
    success = status in (200, 204)
    if not success:
        _log("POST UserData returned HTTP %s" % status, xbmc.LOGERROR)
    return success


# --------------------------------------------------------------------------- #
# Recently played items (for the "My Ratings" browser)
# --------------------------------------------------------------------------- #
def get_recent_played(creds, limit=100):
    """
    <summary>
    Return recently played movies/episodes, newest first.
    </summary>
    <remarks>
    Each entry: {id, name, type, series, season, episode, rating,
    last_played, user_data}. Pulled straight from Jellyfin so it always
    reflects the server (including items rated on other devices).
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
    url = "%s/Users/%s/Items?%s" % (creds["address"], creds["user_id"], query)

    try:
        status, payload = _request(url, creds["token"], method="GET")
    except Exception as exc:
        _log("get_recent_played request failed: %s" % exc, xbmc.LOGERROR)
        return []

    if status != 200 or not payload:
        _log("get_recent_played returned HTTP %s" % status, xbmc.LOGWARNING)
        return []

    try:
        data = json.loads(payload.decode("utf-8"))
    except ValueError as exc:
        _log("get_recent_played parse error: %s" % exc, xbmc.LOGERROR)
        return []

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
