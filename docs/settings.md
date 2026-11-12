# JellyRate settings

Matches JellyRate 1.5.2.

Open them under **Add-ons > My add-ons > Program add-ons > JellyRate > Configure**.

| Setting | Default | Meaning |
|---|---|---|
| Enable rating prompt | On | Master switch. |
| Also prompt when you stop early | On | Prompt on Stop, not just at the natural end. |
| Only prompt after watching at least (%) | 70 | Skip the prompt for brief plays. 0 to 100 in steps of 5. |
| Also save the score in JellyStat | On | When [JellyStat](https://github.com/c0def0rc0ffee/JellyStat) is installed, the score is recorded in its library mirror too, so the title leaves its rating queue. Best effort: JellyStat missing is normal, and the rating has already reached Jellyfin either way. |
| Default rating shown on the stars | 5 | Where the stars start. 1 to 10. |
| Allow JellyStat to manage JellyRate | On | Lets [JellyStat](https://github.com/c0def0rc0ffee/JellyStat)'s web dashboard on the same Kodi change these settings and re-rate recently played titles. JellyRate listens on that Kodi only, never on the network; JellyStat's own password guards the dashboard. See [JellyStat](jellystat.md). |

Back to the [README](../README.md).
