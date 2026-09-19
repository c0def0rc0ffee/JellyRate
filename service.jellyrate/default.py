# -*- coding: utf-8 -*-
"""
<summary>
Launcher for the JellyRate "My Ratings" browser (Program add-on entry).
</summary>
"""
import os
import sys

import xbmcaddon
import xbmcvfs

ADDON_PATH = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path"))
sys.path.insert(0, os.path.join(ADDON_PATH, "resources", "lib"))

from ratings_list import show_ratings_list  # noqa: E402

if __name__ == "__main__":
    show_ratings_list()
