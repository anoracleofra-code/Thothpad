from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files(
    "thinc", includes=["**/*.json", "**/*.cfg", "**/*.txt"]
)
hiddenimports = collect_submodules(
    "thinc", filter=lambda name: not name.startswith("thinc.tests")
)
