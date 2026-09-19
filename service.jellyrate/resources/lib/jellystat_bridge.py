# -*- coding: utf-8 -*-
"""
<summary>
Optional hand-off of a score to JellyStat.
</summary>
<remarks>
JellyStat (script.jellystat) keeps its own library mirror with a per-user
rating column, which its dashboard and rating queue are built on. When it is
installed, a score given here is worth recording there too, otherwise the
title turns up in JellyStat's "not rated by you" queue even though the user
rated it the moment the credits rolled.

The save goes through JellyStat's own ratings module rather than touching
its database file, so its schema and migrations stay its own business. The
call uses push=False: JellyRate has already written the rating to Jellyfin
itself, and JellyStat's separate thumbs and favourite pushes are its own
feature, not something a background save should trigger.

Everything here is best effort. JellyStat missing is the normal case, not an
error, and no failure of this bridge may ever cost the user the rating that
already went to Jellyfin.
</remarks>
"""
import sys

import xbmc
import xbmcaddon
import xbmcvfs

JELLYSTAT_ID = "script.jellystat"


def _log(message, level=xbmc.LOGINFO):
    """
    <summary>
    Write one line to Kodi's log under the JellyRate tag.
    </summary>
    <param name="message">Text to log.</param>
    <param name="level">Kodi log level, info by default.</param>
    """
    xbmc.log("[service.jellyrate] %s" % message, level)


def jellystat_path():
    """
    <summary>
    The JellyStat add-on's directory, or None when it is not installed.
    </summary>
    <remarks>
    xbmcaddon.Addon raises when the add-on id is unknown, and that is the
    supported way to probe for another add-on's presence.
    </remarks>
    """
    try:
        addon = xbmcaddon.Addon(JELLYSTAT_ID)
    except Exception:
        return None
    path = xbmcvfs.translatePath(addon.getAddonInfo("path"))
    return path or None


def save(jellyfin_id, score):
    """
    <summary>
    Record the score in JellyStat's mirror.
    </summary>
    <remarks>
    Returns None when JellyStat is not installed, True on success and False
    when JellyStat refused or errored (already logged). The caller treats
    None and True the same way: nothing to tell the user.
    </remarks>
    """
    path = jellystat_path()
    if not path:
        return None

    inserted = path not in sys.path
    if inserted:
        # In front, so JellyStat's own module names (ratings, library, main)
        # resolve from its directory and not from anywhere else.
        sys.path.insert(0, path)
    try:
        import ratings
        result = ratings.rate(jellyfin_id, float(score), push=False)
        _log("Score %s for %s saved in JellyStat"
             % (score, result.get("name", jellyfin_id)))
        return True
    except Exception as exc:
        # Typically: the item is not in JellyStat's mirror yet because it
        # was added to the library after JellyStat's last sync. The score is
        # safe in Jellyfin either way, and JellyStat's own rating queue will
        # offer the title once its next sync sees it.
        _log("JellyStat did not take the score for %s: %s"
             % (jellyfin_id, exc), xbmc.LOGWARNING)
        return False
    finally:
        if inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass
