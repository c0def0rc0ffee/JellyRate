# JellyRate

A Kodi **service add-on** that pops up a **1-10 star rating when a movie or
episode finishes playing**, then writes that score back to your **Jellyfin**
server as a per-user rating.

It piggybacks on the official [Jellyfin for Kodi](https://github.com/jellyfin/jellyfin-kodi)
add-on (`plugin.video.jellyfin`): it reuses the login that add-on has already
stored and uses its sync database to find the matching Jellyfin item, so there
is **nothing to configure** as long as Jellyfin for Kodi is signed in.

## How it works

1. A background service watches for playback to end (`onPlayBackEnded`) or, if
   enabled, for you to stop early (`onPlayBackStopped`).
2. If you watched at least *N%* (configurable, default 70%), a star dialog
   appears (ten stars: move left/right, press OK on a star, or click it). If
   you've rated the item before, the stars start on your previous score and it
   shows *"Previously rated: X/10"*, handy since the web UI does not display
   this value.
3. The Kodi item is mapped to its Jellyfin id via
   `plugin.video.jellyfin`'s `jellyfin.db` (`kodi_id` + `media_type` to
   `jellyfin_id`).
4. The rating is sent to Jellyfin:
   `POST /Users/{userId}/Items/{itemId}/UserData` with `{"Rating": <1-10>}`,
   authenticated with the token from `data.json`.

## My Ratings browser

JellyRate also installs a **launchable list** of your recently played items
(newest first) with the score next to each one, so you can see what you've
rated and fix anything you skipped or exited by accident.

- Open it from **Add-ons > Program add-ons > JellyRate**, or use the **Open**
  button on the add-on's info page (or add it to Favourites).
- The list is pulled live from Jellyfin (`SortBy=DatePlayed`), so it reflects
  ratings made on any device. Unrated items are shown as not rated.
- **Select any row** to open the same 1-10 star dialog (pre-filled with the
  current score) and rate or re-rate it. **Back** closes the list.

## Requirements

- Kodi 19 (Matrix) or newer (Python 3).
- [Jellyfin for Kodi](https://github.com/jellyfin/jellyfin-kodi)
  (`plugin.video.jellyfin`) installed **and signed in**, in *add-on (native)*
  sync mode so items live in Kodi's library with a Jellyfin id mapping.

## Install

1. In Kodi: **Settings > Add-ons > Install from zip file** and pick the
   `service.jellyrate-x.y.z.zip` from the Releases page.
2. The service starts automatically. Configure it under
   **Add-ons > My add-ons > Program add-ons > JellyRate > Configure**.

For a sandboxed Flatpak Kodi (which cannot read a zip outside its sandbox),
drop the folder straight into the add-ons directory instead:

```
ADDONS=~/.var/app/tv.kodi.Kodi/data/addons
rm -rf "$ADDONS/service.jellyrate"
unzip -o service.jellyrate-x.y.z.zip -d "$ADDONS"
```

Then restart Kodi.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Enable rating prompt | On | Master switch |
| Also prompt when you stop early | On | Prompt on Stop, not just natural end |
| Only prompt after watching at least (%) | 70 | Skip the prompt for brief plays |
| Default rating shown on the stars | 5 | Where the stars start |

## Notes and limitations

- **Numeric vs. the web UI:** the score is stored in Jellyfin's per-user
  `UserData.Rating` field. It is real, persisted, and available through the
  API and some clients, but the **Jellyfin web UI does not render it as a
  star widget** (web only shows the community rating and the favourite toggle).
  If you'd prefer the rating to drive the visible favourite (e.g. 8+ = favourite),
  that is a small change in `resources/lib/jellyfin.py`.
- Only library items that came from Jellyfin can be rated. Local-only items
  have no Jellyfin id and are silently skipped.
- Movies, episodes and music videos are supported.

## Licence

MIT, see [LICENSE](LICENSE).
