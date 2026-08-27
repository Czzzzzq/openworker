# OpenWorker WinUI 3 floating UI host

This Windows-only helper owns every visible surface used by `floating_icon.py`:

- draggable 44 px floating icon;
- right-click action menu;
- frozen-desktop full-screen selection layer;
- live screenshot coordinate/size HUD;
- selection action toolbar;
- screenshot-processing progress, notices, and cancel surfaces.

The Python process is windowless and only coordinates backend requests, browser presence,
state persistence, and final region capture. A single persistent WinUI 3 process receives UTF-8
JSON Lines commands on stdin and writes user-action events on stdout. The selection layer uses
one frozen desktop bitmap plus four dim rectangles, so pointer movement only updates XAML
geometry instead of copying the full screen on every frame. There is intentionally no Tk path
or fallback.

Build or publish the helper with:

```powershell
.\build.ps1
```

The project is an unpackaged, self-contained Windows App SDK 2.3.1 application. The published
executable can run from a normal folder; `floating_icon.py` discovers the local Release output
first during development and the `publish` output for staged builds.
