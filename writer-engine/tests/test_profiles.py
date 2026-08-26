import json
import os

import pytest

from backend import config
from backend.profiles import export_profile, import_profile, save_profile


def test_profile_names_reject_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    with pytest.raises(ValueError):
        save_profile("../outside", {"name": "outside"})


def test_profile_import_and_export_require_safe_json_paths(tmp_path, monkeypatch):
    user_profiles = tmp_path / "profiles"
    monkeypatch.setattr(config, "PROFILES_DIR", user_profiles)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"name": "sample", "prefer": ["plain verbs"]}), encoding="utf-8")
    imported = import_profile(path=str(source), name="sample")
    assert imported["name"] == "sample"

    destination = tmp_path / "exports" / "sample.json"
    exported = export_profile("sample", str(destination))
    assert exported["path"] == str(destination)
    assert json.loads(destination.read_text(encoding="utf-8"))["name"] == "sample"

    with pytest.raises(ValueError):
        import_profile(path="../source.json")


def test_profile_writes_use_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    replaced = []
    original = os.replace

    def capture(source, destination):
        replaced.append((source, destination))
        return original(source, destination)

    monkeypatch.setattr("backend.atomic_io.os.replace", capture)
    result = save_profile("atomic", {"prefer": ["plain verbs"]})
    assert replaced
    source, destination = replaced[0]
    assert str(source).endswith(".tmp")
    assert destination == tmp_path / "profiles" / "atomic.json"
    assert result["profile"]["name"] == "atomic"
