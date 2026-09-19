# -*- coding: utf-8 -*-
"""
<summary>
The "My Ratings" browser: recently played items, newest first, with their
Jellyfin ratings. Selecting a row (re)rates that item.
</summary>
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

import jellyfin
from rating_dialog import ask_rating

ADDON = xbmcaddon.Addon()
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ADDON_ICON = ADDON.getAddonInfo("icon")

SKIN_XML = "script-jellyrate-list.xml"

# Control ids (must match the skin xml).
LIST_ID = 500
HEADER_ID = 600
HINT_ID = 601
CLOSE_ID = 700

_CANCEL_ACTIONS = (
    xbmcgui.ACTION_PREVIOUS_MENU,
    xbmcgui.ACTION_NAV_BACK,
)


def _format_label(item):
    """
    <summary>
    Human-readable title for an item dict from jellyfin.get_recent_played.
    </summary>
    """
    if item.get("type") == "Episode":
        series = item.get("series") or ""
        season = item.get("season")
        episode = item.get("episode")
        code = ""
        if season is not None and episode is not None:
            code = " S%02dE%02d" % (int(season), int(episode))
        name = item.get("name") or ""
        label = series + code
        if name:
            label = (label + " · " + name) if label else name
        return label or name
    return item.get("name") or "(untitled)"


def _format_rating(rating):
    """
    <summary>
    A rating as text for the list's second label.
    </summary>
    <param name="rating">A float, or None.</param>
    <returns>Unrated when None, otherwise the rounded score out of 10.</returns>
    """
    if rating is None:
        return "Unrated"
    return "%d / 10" % int(round(rating))


def _format_date(raw):
    """
    <summary>
    Jellyfin LastPlayedDate (ISO 8601, UTC) -> 'YYYY-MM-DD HH:MM'.
    </summary>
    """
    if not raw:
        return ""
    cleaned = raw.replace("T", " ")
    return cleaned[:16]


class _RatingsListWindow(xbmcgui.WindowXMLDialog):
    # Set by the caller before doModal().
    """
    <summary>
    The list window behind show_ratings_list().
    </summary>
    <remarks>
    items and creds are set by the caller before doModal(). Rating a row updates the item dict in place, so the list stays current without a refetch.
    </remarks>
    """
    items = []
    creds = None

    def onInit(self):
        """
        <summary>
        Kodi hook: set the header and hint labels, then fill the list.
        </summary>
        """
        try:
            self.getControl(HEADER_ID).setLabel(
                "%s · Recently played" % ADDON_NAME
            )
        except Exception:
            pass
        try:
            self.getControl(HINT_ID).setLabel(
                "Select an item to rate · Back to close"
            )
        except Exception:
            pass
        self._populate()

    def _populate(self):
        """
        <summary>
        Rebuild the list control from items, one row per item with the rating as the second label and the played date as a property, then focus the list.
        </summary>
        """
        control = self.getControl(LIST_ID)
        control.reset()
        list_items = []
        for item in self.items:
            li = xbmcgui.ListItem(label=_format_label(item))
            li.setLabel2(_format_rating(item["rating"]))
            li.setProperty("date", _format_date(item["last_played"]))
            list_items.append(li)
        control.addItems(list_items)
        self.setFocusId(LIST_ID)

    def _rate_selected(self):
        """
        <summary>
        Ask for a new rating for the highlighted row, send it to Jellyfin and to JellyStat, update the row and notify the outcome.
        </summary>
        <remarks>
        The JellyStat save happens whether or not the Jellyfin write succeeded, matching process_item() in service.py. A cancelled dialog changes nothing.
        </remarks>
        """
        control = self.getControl(LIST_ID)
        pos = control.getSelectedPosition()
        if pos < 0 or pos >= len(self.items):
            return
        item = self.items[pos]

        previous = item["rating"]
        if previous is not None:
            default = int(round(previous))
        else:
            default = ADDON.getSettingInt("default_rating")

        new_rating = ask_rating(_format_label(item), default, previous)
        if new_rating is None:
            return

        try:
            ok = jellyfin.set_rating(
                self.creds, item["id"], new_rating, user_data=item.get("user_data")
            )
        except Exception as exc:
            xbmc.log("[service.jellyrate] re-rate failed: %s" % exc, xbmc.LOGERROR)
            ok = False

        # Keep JellyStat's mirror in step with a re-rate too.
        if ADDON.getSettingBool("save_to_jellystat"):
            import jellystat_bridge
            jellystat_bridge.save(item["id"], new_rating)

        if ok:
            item["rating"] = float(new_rating)
            try:
                control.getListItem(pos).setLabel2(_format_rating(item["rating"]))
            except Exception:
                pass
            xbmcgui.Dialog().notification(
                ADDON_NAME,
                "Rated %s: %d/10" % (_format_label(item), new_rating),
                ADDON_ICON,
                3000,
            )
        else:
            xbmcgui.Dialog().notification(
                ADDON_NAME,
                "Failed to send rating to Jellyfin",
                xbmcgui.NOTIFICATION_ERROR,
                3000,
            )

    def onClick(self, control_id):
        """
        <summary>
        Kodi hook: selecting a row rates it; the Close button closes the window.
        </summary>
        <param name="control_id">Id of the clicked control.</param>
        """
        if control_id == LIST_ID:
            self._rate_selected()
        elif control_id == CLOSE_ID:
            self.close()

    def onAction(self, action):
        """
        <summary>
        Kodi hook: Back or previous menu closes the window.
        </summary>
        <param name="action">The Kodi action.</param>
        """
        if action.getId() in _CANCEL_ACTIONS:
            self.close()


def show_ratings_list():
    """
    <summary>
    Entry point for the My Ratings browser: fetch the stored credentials and the 100 most recently played items behind a busy dialog, then show the list window.
    </summary>
    <remarks>
    With no credentials or no played items an OK dialog says why and nothing else opens.
    </remarks>
    """
    creds = jellyfin.get_credentials()
    if not creds:
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            "Jellyfin login not found.\n"
            "Sign in with Jellyfin for Kodi (plugin.video.jellyfin) first.",
        )
        return

    xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
    try:
        items = jellyfin.get_recent_played(creds, 100)
    finally:
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    if not items:
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            "No recently played movies or episodes found on Jellyfin.",
        )
        return

    window = _RatingsListWindow(SKIN_XML, ADDON_PATH, "Default", "1080i")
    window.items = items
    window.creds = creds
    window.doModal()
    del window
