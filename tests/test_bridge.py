#!/usr/bin/env python3
"""
<summary>
Unit test for the JellyStat hand-off (resources/lib/jellystat_bridge.py): nothing
happens without JellyStat, a score reaches its ratings module with push off, a
refusal is reported as False, and sys.path is left as it was found.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
No Kodi and no JellyStat are needed: xbmcaddon.Addon is stubbed to know, or not
know, the JellyStat id, and a temporary folder holds a fake ratings module that
records what it was asked.

Usage:
    python3 tests/test_bridge.py
</remarks>
"""

import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "service.jellyrate", "resources", "lib")

# ---- Kodi stubs -----------------------------------------------------------

STATE = {"jellystat_path": None}


class _Addon:
    """<summary>Stand-in for xbmcaddon.Addon that knows JellyStat only when STATE says so.</summary>"""

    def __init__(self, addon_id=None):
        """<summary>Raise for the JellyStat id when STATE holds no path, as Kodi does for an add-on that is not installed.</summary>"""
        if addon_id == "script.jellystat" and not STATE["jellystat_path"]:
            raise RuntimeError("Unknown addon id")

    def getAddonInfo(self, key):
        """<summary>The JellyStat path from STATE; '' for anything else.</summary>"""
        return STATE["jellystat_path"] if key == "path" else ""


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
xbmc.log = lambda msg, level=1: None
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = _Addon
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda path: path
for name, module in (("xbmc", xbmc), ("xbmcaddon", xbmcaddon), ("xbmcvfs", xbmcvfs)):
    sys.modules[name] = module
sys.path.insert(0, LIB_DIR)

import jellystat_bridge  # noqa: E402

FAKE_RATINGS = '''
"""<summary>A fake of JellyStat's ratings module that records calls.</summary>"""
CALLS = []


def rate(item_id, score, favourite=None, push=True, keep_local=False):
    """<summary>Record the call; a score of 99 is refused.</summary>"""
    CALLS.append((item_id, score, push))
    if score == 99:
        raise RuntimeError("That item is not in the mirror.")
    return {"name": "Alien"}
'''

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


# ---- tests ----------------------------------------------------------------

def test_without_jellystat():
    """<summary>No JellyStat installed: None, no import attempted, sys.path untouched.</summary>"""
    print("running test_without_jellystat")
    STATE["jellystat_path"] = None
    before = list(sys.path)
    equal(jellystat_bridge.jellystat_path(), None, "not installed")
    equal(jellystat_bridge.save("a" * 32, 7), None, "nothing to do")
    equal(sys.path, before, "sys.path untouched")
    check("ratings" not in sys.modules, "no ratings module imported")


def test_with_jellystat():
    """<summary>JellyStat present: the score reaches ratings.rate with push off, as a float; a refusal is False.</summary>"""
    print("running test_with_jellystat")
    folder = tempfile.mkdtemp(prefix="jellyrate-bridge-")
    with open(os.path.join(folder, "ratings.py"), "w", encoding="utf-8") as handle:
        handle.write(FAKE_RATINGS)
    STATE["jellystat_path"] = folder
    before = list(sys.path)
    equal(jellystat_bridge.jellystat_path(), folder, "installed")
    equal(jellystat_bridge.save("a" * 32, 7), True, "saved")
    ratings = sys.modules["ratings"]
    equal(ratings.CALLS, [("a" * 32, 7.0, False)], "rate called with push off and a float score")
    equal(sys.path, before, "sys.path restored after the import")
    equal(jellystat_bridge.save("b" * 32, 99), False, "a refusal is reported as False, never raised")
    equal(sys.path, before, "sys.path restored after a refusal too")
    equal(jellystat_bridge.save("c" * 32, 3), True, "the cached module keeps working")
    equal(len(ratings.CALLS), 3, "every call reached JellyStat")


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_without_jellystat, test_with_jellystat):
        test()
    if FAILURES:
        print("test_bridge: %d failure(s)" % len(FAILURES))
        return 1
    print("test_bridge: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
