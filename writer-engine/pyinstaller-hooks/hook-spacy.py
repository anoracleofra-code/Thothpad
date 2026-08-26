from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _desktop_module(name: str) -> bool:
    if name.startswith(("spacy.tests", "spacy.cli", "spacy.displacy")):
        return False
    if name.startswith("spacy.lang."):
        return name.startswith("spacy.lang.en") or name in {
            "spacy.lang.char_classes",
            "spacy.lang.lex_attrs",
            "spacy.lang.norm_exceptions",
            "spacy.lang.punctuation",
            "spacy.lang.tokenizer_exceptions",
        }
    return True


datas = collect_data_files(
    "spacy", includes=["**/*.json", "**/*.cfg", "**/*.txt"]
)
hiddenimports = collect_submodules("spacy", filter=_desktop_module)
