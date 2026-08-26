#!/usr/bin/env python3
"""Fail-closed verification for the reviewed ThothPad release toolchain."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path


SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MSVC_TOOLSET = re.compile(r"^v14\d$")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(mapping: dict, key: str, context: str):
    if key not in mapping:
        raise SystemExit(f"toolchain lock is missing {context}.{key}")
    return mapping[key]


def validate_hash(value: object, context: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise SystemExit(f"toolchain lock has invalid SHA-256 at {context}")


def validate_commit(value: object, context: str) -> None:
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        raise SystemExit(f"toolchain lock has invalid commit at {context}")


def validate_document(document: dict) -> None:
    if document.get("schema_version") != 2:
        raise SystemExit("unsupported toolchain lock schema")
    python = require(document, "python", "root")
    require(python, "version", "python")
    runtimes = require(python, "runtime_sha256", "python")
    validate_hash(
        require(runtimes, "windows", "python.runtime_sha256"),
        "python.runtime_sha256.windows",
    )
    craft = require(document, "craft", "root")
    validate_commit(require(craft, "core_commit", "craft"), "craft.core_commit")
    validate_commit(
        require(craft, "blueprints_commit", "craft"), "craft.blueprints_commit"
    )
    for name in ("windows", "linux", "macos"):
        target = require(document, name, "root")
        for tool in ("cmake", "ninja", "compiler"):
            entry = require(target, tool, name)
            require(entry, "version", f"{name}.{tool}")
        require(target["compiler"], "id", f"{name}.compiler")
        if name == "windows":
            toolset = require(
                target["compiler"], "toolset", f"{name}.compiler"
            )
            if not isinstance(toolset, str) or not MSVC_TOOLSET.fullmatch(toolset):
                raise SystemExit(
                    f"toolchain lock has invalid MSVC toolset at {name}.compiler.toolset"
                )
            directory_version = require(
                target["compiler"], "directory_version", f"{name}.compiler"
            )
            if not isinstance(directory_version, str) or not re.fullmatch(
                r"14\.\d+\.\d+", directory_version
            ):
                raise SystemExit(
                    f"toolchain lock has invalid MSVC directory version at "
                    f"{name}.compiler.directory_version"
                )
            nsis = require(target, "nsis", name)
            require(nsis, "version", f"{name}.nsis")
            nsis_files = require(nsis, "files", f"{name}.nsis")
            if not isinstance(nsis_files, dict) or not nsis_files:
                raise SystemExit(
                    f"toolchain lock requires hashed artifacts at {name}.nsis.files"
                )
            for relative, value in nsis_files.items():
                validate_hash(value, f"{name}.nsis.files.{relative}")
        if name == "linux":
            for asset in ("linuxdeploy", "linuxdeploy_qt"):
                entry = require(target, asset, name)
                require(entry, "tag", f"{name}.{asset}")
                require(entry, "url", f"{name}.{asset}")
                validate_commit(require(entry, "commit", f"{name}.{asset}"), f"{name}.{asset}.commit")
                validate_hash(require(entry, "sha256", f"{name}.{asset}"), f"{name}.{asset}.sha256")
            flatpak = require(target, "flatpak", name)
            require(flatpak, "runtime", f"{name}.flatpak")
            require(flatpak, "runtime_version", f"{name}.flatpak")
            validate_hash(
                require(flatpak, "runtime_commit_x86_64", f"{name}.flatpak"),
                f"{name}.flatpak.runtime_commit_x86_64",
            )
        if name == "macos":
            require(target, "xcode_version", name)
            require(target, "xcode_build", name)
        for family in ("qt", "kf"):
            entry = require(target, family, name)
            require(entry, "version", f"{name}.{family}")
            validate_commit(
                require(entry, "source_commit", f"{name}.{family}"),
                f"{name}.{family}.source_commit",
            )
            files = require(entry, "files", f"{name}.{family}")
            if not isinstance(files, dict) or not files:
                raise SystemExit(
                    f"toolchain lock requires hashed artifacts at {name}.{family}.files"
                )
            for relative, value in files.items():
                validate_hash(value, f"{name}.{family}.files.{relative}")
        for tool in ("cmake", "ninja", "compiler"):
            if "sha256" in target[tool]:
                validate_hash(target[tool]["sha256"], f"{name}.{tool}.sha256")


def read_lock(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read toolchain lock: {error}") from error
    if not isinstance(document, dict):
        raise SystemExit("toolchain lock root must be an object")
    validate_document(document)
    return document


def key_value(document: dict, key: str):
    value = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise SystemExit(f"toolchain lock key does not exist: {key}")
        value = value[part]
    return value


def verify_file(path: Path, expected: str) -> None:
    if not path.is_file():
        raise SystemExit(f"locked tool is missing: {path}")
    actual = digest(path)
    if actual != expected.lower():
        raise SystemExit(
            f"tool hash mismatch for {path}: expected {expected}, got {actual}"
        )


def command_output(command: list[str], *, allow_failure: bool = False) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode and not allow_failure:
        raise SystemExit(
            f"tool command failed ({result.returncode}): {' '.join(command)}"
        )
    return f"{result.stdout}\n{result.stderr}".strip()


def executable_path(value: Path) -> Path:
    if value.parent == Path("."):
        import shutil

        resolved = shutil.which(str(value))
        if not resolved:
            raise SystemExit(f"tool executable was not found on PATH: {value}")
        path = Path(resolved)
    else:
        path = value.resolve()
    if not path.is_file():
        raise SystemExit(f"tool executable does not exist: {path}")
    return path


def msvc_toolset(version: str) -> str:
    match = re.fullmatch(r"19\.(\d+)\.\d+(?:\.\d+)?", version)
    minor = int(match.group(1)) if match else -1
    if not 30 <= minor <= 49:
        raise SystemExit(
            f"unknown MSVC toolset mapping for compiler version {version}"
        )
    return "v143"


def check_tool_version(kind: str, output: str, expected: str) -> None:
    if kind == "cmake":
        match = re.search(r"cmake version ([^\s]+)", output)
        actual = match.group(1) if match else ""
    else:
        actual = output.splitlines()[0].strip() if output else ""
    if actual != expected:
        raise SystemExit(
            f"{kind} version mismatch: expected {expected}, "
            f"got {actual or 'unknown'}"
        )


def check_nsis_version(output: str, expected: str) -> None:
    actual = output.strip().lstrip("v")
    if actual != expected:
        raise SystemExit("NSIS version does not match the toolchain lock")


def verify_build_tool(path: Path, entry: dict, kind: str) -> None:
    executable = executable_path(path)
    output = command_output([str(executable), "--version"])
    check_tool_version(kind, output, entry["version"])
    if "sha256" in entry:
        verify_file(executable, entry["sha256"])


def compiler_record(build_dir: Path) -> tuple[str, str, Path]:
    cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8")
    parts = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        match = re.search(
            rf"^CMAKE_CACHE_{name}_VERSION:INTERNAL=(\d+)$", cache, re.MULTILINE
        )
        if not match:
            raise SystemExit(f"CMake cache is missing its {name.lower()} version")
        parts.append(match.group(1))
    record = build_dir / "CMakeFiles" / ".".join(parts) / "CMakeCXXCompiler.cmake"
    if not record.is_file():
        raise SystemExit(f"active CMake C++ compiler record is missing: {record}")
    text = record.read_text(encoding="utf-8")
    values = {}
    for key in (
        "CMAKE_CXX_COMPILER",
        "CMAKE_CXX_COMPILER_ID",
        "CMAKE_CXX_COMPILER_VERSION",
    ):
        match = re.search(rf'^set\({key} "([^"]+)"\)', text, re.MULTILINE)
        if not match:
            raise SystemExit(f"CMake compiler record is missing {key}")
        values[key] = match.group(1)
    return (
        values["CMAKE_CXX_COMPILER_ID"],
        values["CMAKE_CXX_COMPILER_VERSION"],
        Path(values["CMAKE_CXX_COMPILER"]),
    )


def verify_compiler(build_dir: Path, entry: dict) -> None:
    actual_id, actual_version, executable = compiler_record(build_dir.resolve())
    if actual_id != entry["id"] or actual_version != entry["version"]:
        raise SystemExit(
            "compiler mismatch: expected "
            f"{entry['id']} {entry['version']}, got {actual_id} {actual_version}"
        )
    if entry.get("id") == "MSVC" and "toolset" in entry:
        derived = msvc_toolset(actual_version)
        if entry["toolset"] != derived:
            raise SystemExit(
                f"MSVC toolset mismatch: locked {entry['toolset']}, "
                f"compiler version {actual_version} implies {derived}"
            )
        directory_version = entry.get("directory_version")
        if directory_version is not None:
            parts = executable.parts
            if "MSVC" not in parts:
                raise SystemExit(
                    "MSVC compiler path does not contain a Tools/MSVC "
                    f"toolset directory: {executable}"
                )
            index = parts.index("MSVC")
            installed = parts[index + 1] if index + 1 < len(parts) else ""
            if installed != directory_version:
                raise SystemExit(
                    "MSVC toolset directory mismatch: runner has "
                    f"{installed!r}, lock pins {directory_version!r}"
                )
    if "sha256" in entry:
        verify_file(executable, entry["sha256"])


def verify_file_map(prefix: Path, files: dict) -> None:
    for relative, expected in files.items():
        verify_file(prefix / Path(relative), expected)


def verify_qt_kf(target: dict, qtpaths: Path, prefix: Path | None = None) -> None:
    executable = executable_path(qtpaths)
    actual_qt = command_output([str(executable), "--qt-version"]).strip()
    if actual_qt != target["qt"]["version"]:
        raise SystemExit(
            f"Qt version mismatch: expected {target['qt']['version']}, got {actual_qt}"
        )
    if prefix is None:
        prefix_text = command_output(
            [str(executable), "--query", "QT_INSTALL_PREFIX"]
        ).strip()
        prefix = Path(prefix_text)
    verify_file_map(prefix, target["qt"]["files"])
    verify_file_map(prefix, target["kf"]["files"])


def flatpak_runtime_commit(output: str) -> str:
    match = re.search(r"^([0-9a-f]{64})$", output.strip(), re.MULTILINE)
    if not match:
        raise SystemExit(
            "flatpak info --show-commit did not return a commit hash"
        )
    return match.group(1)


def verify_flatpak_runtime(document: dict, flatpak: Path) -> None:
    target = document["linux"]["flatpak"]
    executable = executable_path(flatpak)
    reference = f"{target['runtime']}/x86_64/{target['runtime_version']}"
    output = command_output(
        [str(executable), "info", "--user", "--show-commit", reference]
    )
    actual = flatpak_runtime_commit(output)
    if actual != target["runtime_commit_x86_64"]:
        raise SystemExit(
            "Flatpak runtime commit mismatch: expected "
            f"{target['runtime_commit_x86_64']}, got {actual}"
        )


def venv_base_executable(resolved: Path, config: Path) -> Path:
    """Resolve the base interpreter recorded by a virtualenv layout.

    A venv's python executable is a derived launcher (trampoline/symlink) that
    differs per virtualenv; the lock pins the base CPython runtime instead.
    """
    values = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    candidate = values.get("executable", "")
    if candidate and Path(candidate).is_file():
        return Path(candidate)
    home = values.get("home", "")
    if home:
        fallback = Path(home) / ("python.exe" if sys.platform == "win32" else "bin/python")
        if fallback.is_file():
            return fallback
    raise SystemExit(f"pyvenv.cfg does not record a base interpreter: {config}")


def verify_python(document: dict, target_name: str, python_path: Path) -> None:
    expected = document["python"]["version"]
    if platform.python_version() != expected:
        raise SystemExit(
            f"Python version mismatch: expected {expected}, got {platform.python_version()}"
        )
    runtime_hash = document["python"]["runtime_sha256"].get(target_name)
    if runtime_hash:
        resolved = executable_path(python_path)
        config = next(
            (parent / "pyvenv.cfg" for parent in (resolved.parent, resolved.parent.parent) if (parent / "pyvenv.cfg").is_file()),
            None,
        )
        if config is not None:
            resolved = venv_base_executable(resolved, config)
        verify_file(resolved, runtime_hash)


def verify_common(
    args: argparse.Namespace, document: dict, target_name: str
) -> None:
    target = document[target_name]
    verify_python(document, target_name, args.python)
    verify_build_tool(args.cmake, target["cmake"], "cmake")
    verify_build_tool(args.ninja, target["ninja"], "ninja")
    verify_compiler(args.build_dir, target["compiler"])


def command_value(args: argparse.Namespace, document: dict) -> int:
    value = key_value(document, args.key)
    print(
        json.dumps(value, sort_keys=True)
        if isinstance(value, (dict, list))
        else value
    )
    return 0


def command_asset(args: argparse.Namespace, document: dict) -> int:
    expected = key_value(document, args.key)
    if not isinstance(expected, dict) or "sha256" not in expected:
        raise SystemExit(f"{args.key} is not a locked asset entry")
    verify_file(args.path, str(expected["sha256"]))
    return 0


def command_windows(args: argparse.Namespace, document: dict) -> int:
    verify_common(args, document, "windows")
    craft_root = args.craft_root.resolve()
    verify_file_map(craft_root, document["windows"]["qt"]["files"])
    verify_file_map(craft_root, document["windows"]["kf"]["files"])
    verify_file_map(craft_root, document["windows"]["nsis"]["files"])
    repositories = {
        "core_commit": craft_root / "craft",
        "blueprints_commit": craft_root
        / "etc"
        / "blueprints"
        / "locations"
        / "craft-blueprints-kde",
    }
    for key, repository in repositories.items():
        actual = command_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"]
        ).strip()
        expected = document["craft"][key]
        if actual != expected:
            raise SystemExit(
                f"Craft {key} mismatch: expected {expected}, got {actual}"
            )
    verify_qt_kf(
        document["windows"], craft_root / "bin" / "qtpaths6.exe", craft_root
    )
    nsis_output = command_output(
        [str(craft_root / "dev-utils" / "bin" / "makensis.exe"), "/VERSION"]
    )
    check_nsis_version(nsis_output, document["windows"]["nsis"]["version"])
    return 0


def command_linux(args: argparse.Namespace, document: dict) -> int:
    verify_common(args, document, "linux")
    verify_qt_kf(document["linux"], args.qtpaths)
    if getattr(args, "flatpak", None):
        verify_flatpak_runtime(document, args.flatpak)
    return 0


def command_macos(args: argparse.Namespace, document: dict) -> int:
    verify_common(args, document, "macos")
    verify_qt_kf(document["macos"], args.qtpaths)
    deploy_output = command_output([str(args.macdeployqt), "-version"])
    if document["macos"]["qt"]["version"] not in deploy_output:
        raise SystemExit("macdeployqt does not match the locked Qt version")
    output = command_output([str(args.xcodebuild), "-version"])
    if f"Xcode {document['macos']['xcode_version']}" not in output:
        raise SystemExit("Xcode version does not match packaging/toolchain-lock.json")
    if f"Build version {document['macos']['xcode_build']}" not in output:
        raise SystemExit("Xcode build does not match packaging/toolchain-lock.json")
    return 0


def add_common_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--build-dir", type=Path, required=True)
    command.add_argument("--python", type=Path, default=Path(sys.executable))
    command.add_argument("--cmake", type=Path, default=Path("cmake"))
    command.add_argument("--ninja", type=Path, default=Path("ninja"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--lock", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    value = commands.add_parser("value")
    value.add_argument("key")
    value.set_defaults(func=command_value)
    asset = commands.add_parser("asset")
    asset.add_argument("--key", required=True)
    asset.add_argument("--path", type=Path, required=True)
    asset.set_defaults(func=command_asset)
    windows = commands.add_parser("windows")
    add_common_arguments(windows)
    windows.add_argument("--craft-root", type=Path, required=True)
    windows.set_defaults(func=command_windows)
    linux = commands.add_parser("linux")
    add_common_arguments(linux)
    linux.add_argument("--qtpaths", type=Path, default=Path("qtpaths6"))
    linux.add_argument("--flatpak", type=Path, default=None)
    linux.set_defaults(func=command_linux)
    macos = commands.add_parser("macos")
    add_common_arguments(macos)
    macos.add_argument("--qtpaths", type=Path, default=Path("qtpaths6"))
    macos.add_argument("--macdeployqt", type=Path, default=Path("macdeployqt"))
    macos.add_argument("--xcodebuild", type=Path, default=Path("xcodebuild"))
    macos.set_defaults(func=command_macos)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args, read_lock(args.lock.resolve())))
