#!/usr/bin/env python3
"""
<summary>
Test for the house rules gate itself (tests/check_addon.py): a clean tree passes, and
a tree seeded with one of each breach is refused with a message naming it: a literal
dash, each escaped dash form in Python, HTML and JavaScript, an attribution trailer,
a private address, a credential shaped token, a secret looking file name, Python
grammar newer than the floor, and an addon.xml that disagrees with VERSION.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero when a check fails.
The gate is run as a subprocess against temporary trees, so this file never has to
carry a breach itself: every offending string is assembled at run time.

Usage:
    python3 tests/test_check_addon.py
</remarks>
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKER = os.path.join(ROOT, "tests", "check_addon.py")
ADDON_ID = "service.jellyrate"

ADDON_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="service.jellyrate" name="JellyRate" version="%s" provider-name="c0def0rc0ffee">
    <requires><import addon="xbmc.python" version="3.0.0"/></requires>
    <extension point="xbmc.service" library="service.py"/>
    <extension point="xbmc.addon.metadata">
        <license>MIT</license>
        <assets><icon>resources/icon.png</icon></assets>
    </extension>
</addon>
"""

FAILURES = []


def check(condition, message):
    """<summary>Record a failure without stopping.</summary>"""
    if not condition:
        FAILURES.append(message)
        print("FAIL: %s" % message)


def write(root, relative, text):
    """<summary>Write one file under the tree, creating folders as needed.</summary>"""
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def clean_tree(version="1.0.0"):
    """<summary>A minimal tree the gate accepts: addon.xml, VERSION, the service file and the icon.</summary>"""
    root = tempfile.mkdtemp(prefix="jellyrate-gate-")
    write(root, "VERSION", version + "\n")
    write(root, os.path.join(ADDON_ID, "addon.xml"), ADDON_XML % "1.0.0")
    write(root, os.path.join(ADDON_ID, "service.py"), '"""<summary>Service.</summary>"""\n')
    write(root, os.path.join(ADDON_ID, "resources", "icon.png"), "")
    return root


def run(root):
    """<summary>Run the gate on a tree; returns (exit code, output).</summary>"""
    done = subprocess.run([sys.executable, CHECKER, root], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True)
    return done.returncode, done.stdout


def expect(root, relative, text, fragment, what):
    """<summary>Add one offending file to a clean tree and assert the gate refuses it with a message holding the fragment.</summary>"""
    write(root, relative, text)
    code, output = run(root)
    check(code != 0, "%s: the gate passed a tree with %s" % (what, relative))
    check(relative.replace(os.sep, "/") in output.replace(os.sep, "/") and fragment in output,
          "%s: the gate did not name %s with %r:\n%s" % (what, relative, fragment, output))
    os.remove(os.path.join(root, relative))


def test_clean_tree_passes():
    """<summary>The minimal tree passes.</summary>"""
    print("running test_clean_tree_passes")
    code, output = run(clean_tree())
    check(code == 0 and "OK" in output, "clean tree refused:\n%s" % output)


def test_breaches_are_named():
    """<summary>One of each breach, each refused and named.</summary>"""
    print("running test_breaches_are_named")
    root = clean_tree()
    backslash = chr(92)
    expect(root, "notes.md", "a " + chr(0x2014) + " b\n", "dash character", "em dash")
    expect(root, "notes.md", "a " + chr(0x2013) + " b\n", "dash character", "en dash")
    expect(root, "escaped.py", 'x = "' + backslash + 'u2014"\n', "as an escape", "python u escape")
    expect(root, "wide.py", 'x = "' + backslash + 'U00002013"\n', "as an escape", "python U escape")
    expect(root, "page.html", "<p>a &" + "mdash; b</p>\n", "as an escape", "html entity")
    expect(root, "page.html", "<p>a &" + "#8211; b</p>\n", "as an escape", "decimal entity")
    expect(root, "script.js", 'var s = "&' + '#X2014;";\n', "as an escape", "upper case hex entity")
    expect(root, "style.css", 'content: "' + backslash + 'u2014";\n', "as an escape", "css escape")
    expect(root, "trailer.py", "# Co-Authored" + "-By: someone\n", "attribution trailer", "trailer")
    expect(root, "address.md", "host " + ".".join(["10", "0", "0", "5"]) + "\n", "private network address", "address")
    expect(root, "cgnat.md", "host " + ".".join(["100", "64", "1", "1"]) + "\n", "private network address", "cgnat")
    expect(root, "shape.py", "x = 'ghp" + "_" + "a" * 30 + "'\n", "holds a credential", "token shape")
    expect(root, "newer.py", "match x:\n    case 1:\n        pass\n", "needs Python newer than 3.8", "python floor")
    expect(root, "broken.py", "def f(:\n", "does not compile", "syntax error")
    code, output = run(root)
    check(code == 0, "the tree is clean again after each removal:\n%s" % output)

    write(root, "api_token.txt", "nothing\n")
    code, output = run(root)
    check(code != 0 and "secret looking file name" in output, "a secret looking file name is refused")
    os.remove(os.path.join(root, "api_token.txt"))

    write(root, "VERSION", "1.0.1\n")
    code, output = run(root)
    check(code != 0 and "does not match VERSION" in output, "addon.xml disagreeing with VERSION is refused")


def test_examples_allowed():
    """<summary>The documented example address range and plain hyphens pass.</summary>"""
    print("running test_examples_allowed")
    root = clean_tree()
    write(root, "docs.md", "Example host 123.123.123.5, range 1-10, flag --no-bump, a hyphen-joined word.\n")
    code, output = run(root)
    check(code == 0, "examples refused:\n%s" % output)


def main():
    """
    <summary>
    Run every test and return the exit code: 0 when all passed, 1 otherwise.
    </summary>
    """
    for test in (test_clean_tree_passes, test_breaches_are_named, test_examples_allowed):
        test()
    if FAILURES:
        print("test_check_addon: %d failure(s)" % len(FAILURES))
        return 1
    print("test_check_addon: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
