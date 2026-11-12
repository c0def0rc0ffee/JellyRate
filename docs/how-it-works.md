# How JellyRate works

JellyRate piggybacks on the official [Jellyfin for Kodi](https://github.com/jellyfin/jellyfin-kodi) add-on (`plugin.video.jellyfin`). It reuses the login that add-on has already stored and uses its sync database to find the matching Jellyfin item, so there is nothing to configure as long as Jellyfin for Kodi is signed in.

## The rating prompt

1. A background service watches for playback to end (`onPlayBackEnded`) or, if enabled, for you to stop early (`onPlayBackStopped`).
2. If you watched at least the set percentage (default 70%), a star dialog appears. There are ten stars: move left or right, press OK on a star, or click it.
3. If you have rated the item before, the stars start on your previous score and the dialog shows *Previously rated: X / 10*. That is handy, because the Jellyfin web UI does not display this value.
4. The prompt waits until nothing is playing. With a playlist or autoplay, the stars for each episode appear once you stop or the run ends, in order.

## Finding the Jellyfin item

The Kodi item is mapped to its Jellyfin id through the `jellyfin.db` database of `plugin.video.jellyfin`: `kodi_id` plus `media_type` gives `jellyfin_id`.

Jellyfin for Kodi must be in *add-on (native)* sync mode, so items live in Kodi's library with a Jellyfin id mapping. Library items that did not come from Jellyfin have no Jellyfin id and are silently skipped.

## Saving the score

The rating is sent to Jellyfin as:

```
POST /Users/{userId}/Items/{itemId}/UserData
{"Rating": <1 to 10>}
```

It is authenticated with the access token from Jellyfin for Kodi's `data.json`, sent in the `Authorization` header.

The score lands in Jellyfin's per user `UserData.Rating` field. It is real, persisted, and available through the API and some clients, but the Jellyfin web UI does not render it as a star widget: the web UI only shows the community rating and the favourite toggle.

Films, episodes and music videos are supported.

With more than one server signed in through Jellyfin for Kodi, the one that add-on used most recently is the one rated against, the same choice it makes itself when it connects.

## The My Ratings browser

JellyRate also installs a launchable list of your recently played items, newest first, with the score next to each one. Use it to see what you have rated and to fix anything you skipped or exited by accident.

- Open it from **Add-ons > Program add-ons > JellyRate**, with the **Open** button on the add-on's info page, or from Favourites once you add it there.
- The list is pulled live from Jellyfin (`SortBy=DatePlayed`), so it reflects ratings made on any device. Unrated items are shown as not rated.
- Select any row to open the same star dialog, filled in with the current score, and rate or re-rate it. **Back** closes the list.

Back to the [README](../README.md).
