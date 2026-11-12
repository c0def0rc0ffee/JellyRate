#!/usr/bin/env python3
"""
<summary>
Unit test for the two windows: every control id the Python uses exists in its skin
XML, every navigation target in the XML is a real control, and the star dialog's
remote navigation (left, right, down to Cancel, up back to the chosen star, OK,
Back) does what rating_dialog.py says.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
No Kodi is needed: xbmcgui.WindowXMLDialog is replaced with a fake that keeps a
focus id and hands out fake controls recording their labels and visibility.

Usage:
    python3 tests/test_dialog.py
</remarks>
"""

import os
import sys
import types
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "service.jellyrate")
LIB_DIR = os.path.join(ADDON_DIR, "resources", "lib")
SKIN_DIR = os.path.join(ADDON_DIR, "resources", "skins", "Default", "1080i")

# ---- Kodi stubs -----------------------------------------------------------

ACTIONS = {"left": 1, "right": 2, "up": 3, "down": 4, "previous": 10, "back": 92}


class _Control:
    """<summary>A fake control recording its label and visibility.</summary>"""

    def __init__(self):
        """<summary>Start with no label and visible, as a fresh skin control is.</summary>"""
        self.label = None
        self.visible = True

    def setLabel(self, text):
        """<summary>Record the label.</summary>"""
        self.label = text

    def setVisible(self, visible):
        """<summary>Record the visibility.</summary>"""
        self.visible = visible


class _Action:
    """<summary>A fake action carrying one id.</summary>"""

    def __init__(self, action_id):
        """<summary>Keep the id.</summary>"""
        self.action_id = action_id

    def getId(self):
        """<summary>The id, as xbmcgui.Action.getId gives it.</summary>"""
        return self.action_id


class _WindowXMLDialog:
    """<summary>Stand-in for xbmcgui.WindowXMLDialog: controls by id, a focus id and a closed flag.</summary>"""

    def __init__(self, *args, **kwargs):
        """<summary>Accept the XML name, path, skin and resolution Kodi takes, and start empty.</summary>"""
        self.controls = {}
        self.focus = 0
        self.closed = False

    def getControl(self, control_id):
        """<summary>The fake control for an id, made on first use so every id the Python asks for exists.</summary>"""
        return self.controls.setdefault(control_id, _Control())

    def setFocusId(self, control_id):
        """<summary>Record the focus.</summary>"""
        self.focus = control_id

    def getFocusId(self):
        """<summary>The recorded focus, 0 before any was set.</summary>"""
        return self.focus

    def close(self):
        """<summary>Mark the window closed.</summary>"""
        self.closed = True

    def doModal(self):
        """<summary>Run onInit only; the test drives the rest, since there is no Kodi event loop.</summary>"""
        self.onInit()


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon.</summary>"""

    def __init__(self, addon_id=None):
        """<summary>Any id is accepted.</summary>"""

    def getAddonInfo(self, key):
        """<summary>The add-on path and name; '' for anything else.</summary>"""
        return {"path": ADDON_DIR, "name": "JellyRate"}.get(key, "")


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.WindowXMLDialog = _WindowXMLDialog
xbmcgui.Dialog = object
xbmcgui.ACTION_MOVE_LEFT, xbmcgui.ACTION_MOVE_RIGHT = ACTIONS["left"], ACTIONS["right"]
xbmcgui.ACTION_MOVE_UP, xbmcgui.ACTION_MOVE_DOWN = ACTIONS["up"], ACTIONS["down"]
xbmcgui.ACTION_PREVIOUS_MENU, xbmcgui.ACTION_NAV_BACK = ACTIONS["previous"], ACTIONS["back"]
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda path: path
for name, module in (("xbmc", xbmc), ("xbmcaddon", xbmcaddon), ("xbmcgui", xbmcgui),
                     ("xbmcvfs", xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, LIB_DIR)

import rating_dialog  # noqa: E402
import ratings_list  # noqa: E402

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


def control_ids(xml_name):
    """<summary>Every control id in a skin XML, and every navigation target with the id of the control it sits on.</summary>"""
    tree = ET.parse(os.path.join(SKIN_DIR, xml_name))
    ids = set()
    targets = []
    for control in tree.iter("control"):
        if control.get("id"):
            ids.add(int(control.get("id")))
        for tag in ("onup", "ondown", "onleft", "onright"):
            node = control.find(tag)
            if node is not None and node.text and node.text.strip().isdigit():
                targets.append((control.get("id"), tag, int(node.text.strip())))
    return ids, targets


def press(dialog, name):
    """<summary>Send one navigation action to the dialog.</summary>"""
    dialog.onAction(_Action(ACTIONS[name]))


# ---- tests ----------------------------------------------------------------

def test_rating_xml_ids():
    """<summary>The star dialog's ids in the Python all exist in its XML, and the XML's navigation points at real controls.</summary>"""
    print("running test_rating_xml_ids")
    ids, targets = control_ids(rating_dialog.SKIN_XML)
    wanted = {rating_dialog.TITLE_LABEL_ID, rating_dialog.PREVIOUS_LABEL_ID,
              rating_dialog.VALUE_LABEL_ID, rating_dialog.CANCEL_ID}
    for star in range(rating_dialog.MIN_RATING, rating_dialog.MAX_RATING + 1):
        wanted.add(rating_dialog.STAR_BUTTON_BASE + star)
        wanted.add(rating_dialog.STAR_FILLED_BASE + star)
    equal(sorted(wanted - ids), [], "control ids missing from %s" % rating_dialog.SKIN_XML)
    for owner, tag, target in targets:
        check(target in ids, "%s on control %s points at missing id %d" % (tag, owner, target))
    check(len(targets) > 0, "the XML carries navigation")


def test_star_overlays_unconditioned():
    """
    <summary>The filled star overlays carry no visible tag, so setVisible from rating_dialog.py can show them.</summary>
    <remarks>
    Kodi lets a visible condition in the XML win over setVisible. A fixed false here kept
    every star grey in the real dialog while the fake controls above still passed.
    </remarks>
    """
    print("running test_star_overlays_unconditioned")
    tree = ET.parse(os.path.join(SKIN_DIR, rating_dialog.SKIN_XML))
    filled = {str(rating_dialog.STAR_FILLED_BASE + s)
              for s in range(rating_dialog.MIN_RATING, rating_dialog.MAX_RATING + 1)}
    for control in tree.iter("control"):
        if control.get("id") in filled:
            check(control.find("visible") is None,
                  "filled star %s has a visible tag that overrides setVisible" % control.get("id"))


def test_list_xml_ids():
    """<summary>The My Ratings window's ids in the Python all exist in its XML, and its navigation points at real controls.</summary>"""
    print("running test_list_xml_ids")
    ids, targets = control_ids(ratings_list.SKIN_XML)
    wanted = {ratings_list.LIST_ID, ratings_list.HEADER_ID, ratings_list.HINT_ID, ratings_list.CLOSE_ID}
    equal(sorted(wanted - ids), [], "control ids missing from %s" % ratings_list.SKIN_XML)
    for owner, tag, target in targets:
        check(target in ids, "%s on control %s points at missing id %d" % (tag, owner, target))
    check((str(ratings_list.LIST_ID), "onleft", ratings_list.CLOSE_ID) in targets,
          "left from the list reaches Close")
    check((str(ratings_list.CLOSE_ID), "onup", ratings_list.LIST_ID) in targets,
          "up from Close returns to the list")


def test_star_navigation():
    """<summary>Left and right move and clamp, down reaches Cancel, up returns to the chosen star, OK confirms, Back cancels.</summary>"""
    print("running test_star_navigation")
    base = rating_dialog.STAR_BUTTON_BASE
    filled = rating_dialog.STAR_FILLED_BASE
    dialog = rating_dialog._RatingDialog("x", ADDON_DIR, "Default", "1080i")
    dialog.title, dialog.value, dialog.previous = "Alien", 7, 7.0
    dialog.onInit()
    equal(dialog.getFocusId(), base + 7, "focus starts on the chosen star")
    equal(dialog.controls[rating_dialog.TITLE_LABEL_ID].label, "Alien", "title shown")
    equal(dialog.controls[rating_dialog.PREVIOUS_LABEL_ID].label, "Previously rated: 7 / 10", "previous hint")
    equal(dialog.controls[rating_dialog.VALUE_LABEL_ID].label, "7 / 10", "value label")
    equal([dialog.controls[filled + s].visible for s in range(1, 11)],
          [True] * 7 + [False] * 3, "seven stars filled")

    press(dialog, "right")
    equal((dialog.value, dialog.getFocusId()), (8, base + 8), "right moves up one")
    for _ in range(5):
        press(dialog, "right")
    equal((dialog.value, dialog.getFocusId()), (10, base + 10), "right clamps at ten")
    for _ in range(12):
        press(dialog, "left")
    equal((dialog.value, dialog.getFocusId()), (1, base + 1), "left clamps at one")
    equal(dialog.controls[filled + 1].visible and not dialog.controls[filled + 2].visible, True, "one star filled")

    press(dialog, "down")
    equal(dialog.getFocusId(), rating_dialog.CANCEL_ID, "down from a star reaches Cancel")
    press(dialog, "left")
    equal((dialog.value, dialog.getFocusId()), (1, rating_dialog.CANCEL_ID), "left on Cancel changes nothing")
    press(dialog, "up")
    equal(dialog.getFocusId(), base + 1, "up from Cancel returns to the chosen star")
    equal(dialog.closed, False, "still open")

    dialog.onClick(base + 4)
    equal((dialog.confirmed, dialog.value, dialog.closed), (True, 4, True), "OK on a star confirms that star")

    dialog = rating_dialog._RatingDialog("x", ADDON_DIR, "Default", "1080i")
    dialog.title, dialog.value, dialog.previous = "Alien", 50, None
    dialog.onInit()
    equal(dialog.value, 10, "a starting value is clamped")
    equal(dialog.controls[rating_dialog.PREVIOUS_LABEL_ID].label, "Not yet rated", "no previous hint")
    press(dialog, "back")
    equal((dialog.confirmed, dialog.closed), (False, True), "Back cancels")

    dialog = rating_dialog._RatingDialog("x", ADDON_DIR, "Default", "1080i")
    dialog.onInit()
    dialog.onClick(rating_dialog.CANCEL_ID)
    equal((dialog.confirmed, dialog.closed), (False, True), "Cancel cancels")
    dialog = rating_dialog._RatingDialog("x", ADDON_DIR, "Default", "1080i")
    dialog.onInit()
    press(dialog, "previous")
    equal((dialog.confirmed, dialog.closed), (False, True), "previous menu cancels")


def test_ask_rating():
    """<summary>ask_rating hands back the confirmed star, or None.</summary>"""
    print("running test_ask_rating")
    original = rating_dialog._RatingDialog.onInit

    def confirm(self):
        """<summary>onInit that presses OK on star nine straight away.</summary>"""
        original(self)
        self.onClick(rating_dialog.STAR_BUTTON_BASE + 9)

    rating_dialog._RatingDialog.onInit = confirm
    try:
        equal(rating_dialog.ask_rating("Alien", 5, None), 9, "confirmed star returned")
    finally:
        rating_dialog._RatingDialog.onInit = original

    def cancel(self):
        """<summary>onInit that presses Back straight away.</summary>"""
        original(self)
        self.onAction(_Action(ACTIONS["back"]))

    rating_dialog._RatingDialog.onInit = cancel
    try:
        equal(rating_dialog.ask_rating("Alien", 5, None), None, "cancelled is None")
    finally:
        rating_dialog._RatingDialog.onInit = original


def test_list_labels():
    """<summary>The My Ratings row text: episode code, rating text and the played date in en-GB form.</summary>"""
    print("running test_list_labels")
    episode = {"type": "Episode", "series": "Flash", "season": 1, "episode": 2, "name": "Pilot"}
    equal(ratings_list._format_label(episode), "Flash S01E02 " + chr(0xB7) + " Pilot", "episode label")
    equal(ratings_list._format_label({"type": "Movie", "name": "Alien"}), "Alien", "film label")
    equal(ratings_list._format_rating(None), "Unrated", "unrated")
    equal(ratings_list._format_rating(7.6), "8 / 10", "rounded")
    text = ratings_list._format_date("2026-10-13T18:30:00.0000000Z")
    check(len(text) == 16 and text[2] == "/" and text[5] == "/", "played date is DD/MM/YYYY HH:MM, not ISO: %r" % text)
    equal(ratings_list._format_date(""), "", "no date")


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_rating_xml_ids, test_star_overlays_unconditioned, test_list_xml_ids, test_star_navigation, test_ask_rating,
                 test_list_labels):
        test()
    if FAILURES:
        print("test_dialog: %d failure(s)" % len(FAILURES))
        return 1
    print("test_dialog: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
