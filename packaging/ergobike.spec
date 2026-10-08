# Build: pyinstaller packaging/ergobike.spec  ->  dist/ErgoBike/ErgoBike.exe
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[(str(root / "ergobike" / "web"), "ergobike/web")],
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("websockets"),
    excludes=["tkinter", "matplotlib", "PySide6", "PyQt5", "PyQt6", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ErgoBike",
          icon=str(root / "packaging" / "ergobike.ico"), console=False, upx=False,
          version=str(root / "packaging" / "version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, name="ErgoBike", upx=False)
