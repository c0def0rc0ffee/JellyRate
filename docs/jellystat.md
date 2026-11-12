# Managing JellyRate from JellyStat

With [JellyStat](https://github.com/c0def0rc0ffee/JellyStat) installed and its web dashboard switched on, its **Add-ons** page shows JellyRate with two entries:

- **Settings** opens the [settings](settings.md) as one form, checked against the same limits as the Configure screen. It covers five of them: the switch that lets JellyStat manage JellyRate is left out, so the dashboard cannot turn off the connection it is using.
- **Recent ratings** lists the 100 most recently played films and episodes with their scores, straight from Jellyfin, like the My Ratings browser. Opening one lets you give it a new score, which goes to Jellyfin and, when **Also save the score in JellyStat** is on, to JellyStat as well. A score changed on another device meanwhile is not saved over; reload and try again.

Backup and restore on that page cover the settings. The scores themselves live in Jellyfin.

JellyRate listens on that Kodi only, never on the network, and JellyStat's own password guards the dashboard. Switch **Allow JellyStat to manage JellyRate** off to stop it altogether.

Back to the [README](../README.md).
