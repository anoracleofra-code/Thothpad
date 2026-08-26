from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import sys


model_data, model_binaries, model_hiddenimports = collect_all("en_core_web_sm")
harper_binary = Path("harper-bridge/target/release") / (
    "thothpad-harper.exe" if sys.platform == "win32" else "thothpad-harper"
)

a = Analysis(
    ["backend/sidecar.py"],
    pathex=["."],
    binaries=model_binaries + [(str(harper_binary), "grammar")],
    datas=[
        ("profiles", "profiles"),
        ("backend/data/slopless", "backend/data/slopless"),
        ("backend/data/wordnet", "backend/data/wordnet"),
        ("THIRD_PARTY.md", "notices"),
        ("../THIRD_PARTY_NOTICES.md", "notices"),
        ("../COPYING", "licenses"),
    ] + model_data,
    hiddenimports=model_hiddenimports,
    hookspath=["pyinstaller-hooks"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "spacy.tests", "thinc.tests", "numpy.tests", "pytest",
        "pandas", "scipy", "matplotlib", "PIL", "nltk", "textblob",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="writer-engine",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="writer-engine",
)
