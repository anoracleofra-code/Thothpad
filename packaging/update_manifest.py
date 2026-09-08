from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
CHANNELS = frozenset({"stable", "beta", "development"})


def _valid_commit(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_update_manifest(
    *,
    version: str,
    channel: str,
    artifact_url: str,
    artifact: Path,
    source_commit: str,
) -> dict[str, Any]:
    if not _VERSION.fullmatch(version):
        raise ValueError("update version must be semantic version text")
    if channel not in CHANNELS:
        raise ValueError("unsupported update channel")
    parsed = urlparse(artifact_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("update artifacts must use an HTTPS URL")
    if not _valid_commit(source_commit):
        raise ValueError("source commit must be a full hexadecimal Git hash")
    if not artifact.is_file():
        raise ValueError("update artifact does not exist")
    return {
        "schema_version": 1,
        "version": version,
        "channel": channel,
        "artifact_url": artifact_url,
        "artifact_size": artifact.stat().st_size,
        "artifact_sha256": sha256_file(artifact),
        "source_commit": source_commit.casefold(),
        "automatic_install": False,
        "checksum_required_before_install": True,
        "downgrade_requires_explicit_override": True,
    }


def verify_update_artifact(manifest: dict[str, Any], artifact: Path) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("invalid update manifest")
    expected_size = manifest.get("artifact_size")
    expected_hash = manifest.get("artifact_sha256")
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size < 0
        or not _valid_sha256(expected_hash)
    ):
        raise TypeError("update manifest is missing artifact integrity fields")
    parsed = urlparse(str(manifest.get("artifact_url", "")))
    gates = {
        "schema": manifest.get("schema_version") == 1,
        "version_valid": isinstance(manifest.get("version"), str) and bool(_VERSION.fullmatch(manifest["version"])),
        "channel_valid": manifest.get("channel") in CHANNELS,
        "https_artifact_url": parsed.scheme == "https" and bool(parsed.netloc),
        "source_commit_valid": _valid_commit(manifest.get("source_commit")),
        "size_matches": artifact.is_file() and artifact.stat().st_size == expected_size,
        "sha256_matches": artifact.is_file() and sha256_file(artifact) == expected_hash,
        "manual_install_policy": manifest.get("automatic_install") is False,
        "checksum_policy": manifest.get("checksum_required_before_install") is True,
        "downgrade_policy": manifest.get("downgrade_requires_explicit_override") is True,
    }
    return {"gates": gates, "all_green": all(gates.values())}
