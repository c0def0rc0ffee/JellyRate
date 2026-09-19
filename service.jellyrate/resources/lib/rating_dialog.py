# -*- coding: utf-8 -*-
"""
<summary>
The 1-10 star rating dialog shown after playback (Trakt-style).
</summary>
<remarks>
Ten stars in a row: move left/right to choose, press OK on a star to confirm,
press down for Cancel, Back to dismiss. Navigation is driven explicitly in
onAction so it works reliably with a remote (the old slider control only
responded to the mouse).
</remarks>
"""
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))

SKIN_XML = "script-jellyrate-rating.xml"

MIN_RATING = 1
MAX_RATING = 10

# Control ids (must match the skin xml).
TITLE_LABEL_ID = 10
PREVIOUS_LABEL_ID = 11
VALUE_LABEL_ID = 12
STAR_BUTTON_BASE = 100      # star buttons are 101..110  (value = id - 100)
STAR_FILLED_BASE = 300      # filled-star images are 301..310
CANCEL_ID = 200

_CANCEL_ACTIONS = (
    xbmcgui.ACTION_PREVIOUS_MENU,
    xbmcgui.ACTION_NAV_BACK,
)


class _RatingDialog(xbmcgui.WindowXMLDialog):
    # Defaults; the caller overrides these before doModal().
    """
    <summary>
    The ten star window behind ask_rating().
    </summary>
    <remarks>
    title, value and previous are set by the caller before doModal(); afterwards confirmed says whether OK was pressed on a star and value holds the chosen rating.
    </remarks>
    """
    title = ""
    value = 5
    previous = None
    confirmed = False

    # ------------------------------------------------------------------ #
    def onInit(self):
        """
        <summary>
        Kodi hook: clamp the starting value, show the title and the previous rating hint, paint the stars and focus the chosen one.
        </summary>
        <remarks>
        Each control lookup is wrapped on its own, so a skin missing one control does not break the dialog.
        </remarks>
        """
        self.value = max(MIN_RATING, min(MAX_RATING, int(self.value)))
        try:
            self.getControl(TITLE_LABEL_ID).setLabel(self.title)
        except Exception:
            pass
        try:
            if self.previous is not None:
                hint = "Previously rated: %d / %d" % (
                    int(round(self.previous)),
                    MAX_RATING,
                )
            else:
                hint = "Not yet rated"
            self.getControl(PREVIOUS_LABEL_ID).setLabel(hint)
        except Exception:
            pass
        self._paint()
        try:
            self.setFocusId(STAR_BUTTON_BASE + self.value)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def _paint(self):
        """
        <summary>
        Fill stars up to the current value; update the numeric label.
        </summary>
        """
        for star in range(MIN_RATING, MAX_RATING + 1):
            try:
                self.getControl(STAR_FILLED_BASE + star).setVisible(
                    star <= self.value
                )
            except Exception:
                pass
        try:
            self.getControl(VALUE_LABEL_ID).setLabel(
                "%d / %d" % (self.value, MAX_RATING)
            )
        except Exception:
            pass

    def _set_value(self, value):
        """
        <summary>
        Change the chosen rating, repaint when it moved, and focus that star's button.
        </summary>
        <param name="value">The wanted rating; anything outside 1 to 10 is clamped.</param>
        """
        value = max(MIN_RATING, min(MAX_RATING, value))
        if value != self.value:
            self.value = value
            self._paint()
        try:
            self.setFocusId(STAR_BUTTON_BASE + self.value)
        except Exception:
            pass

    def _focused_star(self):
        """
        <summary>
        The rating whose star button currently has focus.
        </summary>
        <returns>1 to 10, or None when focus is elsewhere, such as on Cancel.</returns>
        """
        focus = self.getFocusId()
        if STAR_BUTTON_BASE + MIN_RATING <= focus <= STAR_BUTTON_BASE + MAX_RATING:
            return focus - STAR_BUTTON_BASE
        return None

    # ------------------------------------------------------------------ #
    def onAction(self, action):
        """
        <summary>
        Kodi hook: Back or previous menu cancels; left and right on a star move the chosen rating; down on a star moves to Cancel; up from Cancel returns focus to the chosen star.
        </summary>
        <param name="action">The Kodi action.</param>
        """
        action_id = action.getId()
        if action_id in _CANCEL_ACTIONS:
            self.confirmed = False
            self.close()
            return

        on_star = self._focused_star() is not None
        if action_id == xbmcgui.ACTION_MOVE_LEFT and on_star:
            self._set_value(self.value - 1)
        elif action_id == xbmcgui.ACTION_MOVE_RIGHT and on_star:
            self._set_value(self.value + 1)
        elif action_id == xbmcgui.ACTION_MOVE_DOWN and on_star:
            try:
                self.setFocusId(CANCEL_ID)
            except Exception:
                pass
        elif action_id == xbmcgui.ACTION_MOVE_UP and not on_star:
            self._set_value(self.value)  # return focus to the chosen star

    def onClick(self, control_id):
        """
        <summary>
        Kodi hook: a star button confirms that rating and closes; Cancel closes without confirming.
        </summary>
        <param name="control_id">Id of the clicked control.</param>
        """
        star = control_id - STAR_BUTTON_BASE
        if MIN_RATING <= star <= MAX_RATING:
            self.value = star
            self.confirmed = True
            self.close()
        elif control_id == CANCEL_ID:
            self.confirmed = False
            self.close()


def _ask_with_stars(title, default_value, previous):
    """
    <summary>
    Run the star window modally and hand back its result.
    </summary>
    <param name="title">Text for the title label.</param>
    <param name="default_value">Rating the stars start on, clamped to 1 to 10.</param>
    <param name="previous">Existing rating shown as a hint, or None.</param>
    <returns>The confirmed rating, or None when cancelled.</returns>
    <remarks>
    Anything the window raises propagates; ask_rating() catches it and falls back to a select list.
    </remarks>
    """
    dialog = _RatingDialog(SKIN_XML, ADDON_PATH, "Default", "1080i")
    dialog.title = title
    dialog.value = max(MIN_RATING, min(MAX_RATING, int(default_value)))
    dialog.previous = previous
    dialog.doModal()
    confirmed = dialog.confirmed
    value = dialog.value
    del dialog
    return value if confirmed else None


def _ask_with_select(title, default_value, previous):
    """
    <summary>
    Plain fallback if the custom window cannot be loaded for any reason.
    </summary>
    """
    options = ["%d" % i for i in range(MIN_RATING, MAX_RATING + 1)]
    if previous is not None:
        prev_int = int(round(previous))
        for idx, opt in enumerate(options):
            if int(opt) == prev_int:
                options[idx] = "%s  (previous)" % opt
    preselect = max(0, min(len(options) - 1, int(default_value) - MIN_RATING))
    choice = xbmcgui.Dialog().select(
        "Rate: %s" % title if title else "Rate", options, preselect=preselect
    )
    if choice < 0:
        return None
    return choice + MIN_RATING


def ask_rating(title="", default_value=5, previous=None):
    """
    <summary>
    Prompt for a 1-10 rating. Returns an int, or None if cancelled.
    </summary>
    <remarks>
    ``previous`` is the existing Jellyfin rating (float) or None; it is shown
    as a hint and the stars start on it.
    </remarks>
    """
    try:
        return _ask_with_stars(title, default_value, previous)
    except Exception as exc:
        xbmc.log(
            "[service.jellyrate] star dialog failed (%s); using select list"
            % exc,
            xbmc.LOGWARNING,
        )
        return _ask_with_select(title, default_value, previous)
