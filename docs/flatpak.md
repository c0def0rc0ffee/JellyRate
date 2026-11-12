# Installing on a Flatpak Kodi

A sandboxed Flatpak Kodi cannot read a zip outside its sandbox, so **Install from zip file** will not find it. Drop the add-on folder straight into the add-ons directory instead:

```
ADDONS=~/.var/app/tv.kodi.Kodi/data/addons
rm -rf "$ADDONS/service.jellyrate"
unzip -o service.jellyrate-x.y.z.zip -d "$ADDONS"
```

Then restart Kodi.

Back to the [README](../README.md).
