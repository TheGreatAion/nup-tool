# nup tool

A Windows desktop app for [NUP](https://github.com/a55uka/nup), the container format for
unencrypted Nintendo Switch / Switch 2 game dumps. It does everything the `nup` command line
tool does, without the command line.

## Install it

Download `nup-tool-setup-<version>.exe` from the
[latest release](https://github.com/TheGreatAion/nup-tool/releases/latest) and run it. Python
isn't needed. It installs for your user account with no admin prompt, adds a Start menu shortcut
and optionally a desktop icon, and can be removed from Windows' Installed apps list.

To use it without installing, download `nup-tool-<version>-portable.zip` from the same release,
unzip it, and open `nup tool.exe`. Keep the `_internal` folder next to the exe.

Neither is signed, so Windows SmartScreen may warn about them. Choose **More info**, then
**Run anyway**.

Once it's open, you can also drop a dump folder or a `.nup` file onto the window.

## What it does

**Pack tab: dump folder to `.nup`**

- Shows which packages it found before you commit to anything
- Sets compression level, title, publisher, display version, icon, title ID override, and
  localized titles and icons
- Fills in the title, publisher, display version and icons from the game's control data
  (`control.nacp` and its `icon_<Language>.dat` files) when the dump folder has it, in the newest
  update, `base/`, or the folder itself, directly or in `control/`. **Load from control.nacp**
  reads one from anywhere. Values you've typed are kept
- Split mode: one file each for the base, every update, and the DLC
- Shows progress with speed and time left. Cancel or failure leaves no partial files
- Never overwrites an existing file

**Browse tab: look inside a `.nup`**

- Tree of packages, folders and files, with sizes, codec and SHA-256
- **Verify:** full check (hashes every file) or quick check (structure only)
- **Edit metadata:** changes titles, publisher, version and icons in place. File contents are
  untouched, and a failed save leaves the original as it was
- **Extract:**
  - *Selected items*: whatever you've selected in the tree (also on right-click)
  - *Everything, as a dump folder*: gives you a `base/`, `updates/`, `dlc/` folder you can pack again
  - *Rescue*: pulls every file out of a container whose folder tree is broken

Extracted files are checked against their SHA-256 as they're written. Containers are treated as
untrusted: one that tries to write outside the destination folder, or whose folders loop or
expand endlessly, is refused.

Metadata follows the numbering in the spec. Containers packed by `nup` between its PR #20 and a
fix for that numbering store metadata under shifted IDs, so their titles and icons can show up
wrong here; repack them.

## Dump folder layout

| Folder | Becomes |
|---|---|
| `base/` with `exefs/` and/or `romfs/` | the base package |
| `updates/<decimal version>/` with `exefs/` and/or `romfs/` | one update package per version |
| `dlc/<16-hex title id>/<index>/` with `exefs/` and/or `romfs/` | one addon package per index |
| `exefs/` and/or `romfs/` directly in the folder | a single base package |

See the [nup README](https://github.com/a55uka/nup#packing-a-dump) for details. This app doesn't
dump or decrypt games: it only works with already-unencrypted content.

## Developing

Needs Python 3.10 to 3.14. Python 3.15 won't work yet, because `zstandard` has no prebuilt wheel
for it.

```bash
py -3.13 -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e .[dev]
```

Run from source:

```bash
.venv\Scripts\python -m nup_gui
```

Run the tests (the GUI tests use Qt's offscreen mode, so no windows appear):

```bash
.venv\Scripts\python -m pytest
```

Building needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) as well:

```bash
winget install --id JRSoftware.InnoSetup --scope user
```

Build the app into `dist\nup tool` and the installer into `dist`:

```bash
pwsh -File build.ps1
```

The version comes from `nup_gui/__init__.py`. `nup_tool.spec` is the PyInstaller recipe and
`installer.iss` the Inno Setup one.

### Code layout

| Path | Contents |
|---|---|
| `nup/` | The upstream library, vendored. See `nup/UPSTREAM.md` for the commit and the local fixes |
| `nup_gui/core.py` | All operations, with no Qt: pack, extract, verify, edit, safety checks |
| `nup_gui/jobs.py` | Runs operations on a worker thread, and the progress bar |
| `nup_gui/pack_page.py`, `browse_page.py`, `edit_dialog.py`, `widgets.py` | The UI |
| `nup_gui/main_window.py` | Connects the UI to the jobs |
| `tests/` | Tests against fake dumps built on the fly. No real game data |

## License

nup tool is MIT licensed (`LICENSE`). The vendored `nup` library is MIT licensed by its authors
(`nup/LICENSE`). The other bundled components and their licenses are listed in
`licenses/THIRD-PARTY-NOTICES.txt`, which ships with the app.
