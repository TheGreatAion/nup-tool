"""Entry script for the packaged app (PyInstaller needs a plain script, not a -m module)."""

from nup_gui.__main__ import main

raise SystemExit(main())
