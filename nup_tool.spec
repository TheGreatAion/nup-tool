# PyInstaller build recipe: pyinstaller --noconfirm nup_tool.spec
# Output: dist/nup tool/nup tool.exe, plus the _internal folder it needs beside it.

a = Analysis(
    ["launcher.py"],
    datas=[("nup_gui/assets", "assets"), ("licenses", "licenses"), ("nup/LICENSE", "licenses/nup")],
    hiddenimports=["zstandard.backend_c"],
    excludes=["tkinter", "unittest", "pydoc", "pytest"],
)

# Parts of Qt the app doesn't use. Only image formats it can actually show stay in.
_UNUSED = (
    "opengl32sw", "Qt6Pdf", "Qt6Qml", "Qt6Quick", "Qt6Network", "Qt6OpenGL",
    "qpdf", "qtuiotouchplugin", "qdirect2d", "qminimal", "qoffscreen",
    "qgif", "qtga", "qtiff", "qwbmp", "qwebp", "qicns", "qsvg", "Qt6Svg",
    "qnetworklistmanager", "qtvirtualkeyboard", "Qt6VirtualKeyboard",
)
a.binaries = [b for b in a.binaries
              if not any(part in b[0].replace("\\", "/").split("/")[-1] for part in _UNUSED)]
a.datas = [d for d in a.datas if "/translations/" not in d[0].replace("\\", "/")]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nup tool",
    console=False,
    icon="nup_gui/assets/icon.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="nup tool")
