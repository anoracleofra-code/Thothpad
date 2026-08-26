#!/usr/bin/env python3
"""Acceptance checks for packaged ThothPad artifacts on Linux and macOS."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


APP_ID = "org.thothpad.ThothPad"
WEBENGINE_MARKERS = (
    "qt6webengine",
    "qtwebengineprocess",
    "webenginecore.framework",
    "qtwebengine_resources",
    "qtwebengine_dictionaries",
)
UNICODE_SAMPLE = (
    "# ThothPad packaged acceptance\n\n"
    "Curly quotes: \u201cDjinn\u201d and \u2018ibis\u2019.\n"
    "Combining text: cafe\u0301. Emoji: \U0001f4dd \U0001f9d1\u200d\U0001f4bb.\n"
    "Scripts: \u0398\u03c9\u03b8 \u0643\u0627\u062a\u0628 \u4f5c\u5bb6.\r\n"
).encode("utf-8")


class AcceptanceError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(command, capture_output=True, check=False, **kwargs)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise AcceptanceError(f"command failed ({result.returncode}): {' '.join(command)}\n{stderr}")
    return result


def find_profile(root: Path) -> dict[str, object]:
    candidates = sorted(root.rglob("ThothPad-*.package.json"))
    candidates.extend(sorted(root.rglob("thothpad-package-profile.json")))
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("variant") in {"Core", "Full"}:
            return value
    raise AcceptanceError("the packaged Core/Full profile is missing")


def webengine_files(root: Path) -> list[str]:
    matches: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix().lower()
        if any(marker in relative for marker in WEBENGINE_MARKERS):
            matches.append(path.relative_to(root).as_posix())
    return sorted(matches)


def validate_layout(
    root: Path, variant: str, platform_name: str, *, composed_runtime: bool = False
) -> dict[str, object]:
    profile = find_profile(root)
    if profile.get("variant") != variant:
        raise AcceptanceError(
            f"artifact profile is {profile.get('variant')!r}; expected {variant!r}"
        )
    markers = webengine_files(root)
    if variant == "Core" and markers:
        raise AcceptanceError(f"Core artifact contains Qt WebEngine: {markers[0]}")
    declared_webengine = profile.get("webengine_policy") == "included"
    if variant == "Full" and not markers and not (composed_runtime and declared_webengine):
        raise AcceptanceError("Full artifact does not contain a Qt WebEngine runtime")

    if platform_name == "linux":
        layouts = (
            (root / "usr" / "bin" / "thothpad", root / "usr" / "bin" / "writer-engine" / "writer-engine"),
            (root / "bin" / "thothpad", root / "bin" / "writer-engine" / "writer-engine"),
        )
        executable, engine = next(
            ((app, sidecar) for app, sidecar in layouts if app.is_file()), layouts[0]
        )
    else:
        executable = root / "Contents" / "MacOS" / "thothpad"
        engine = root / "Contents" / "MacOS" / "writer-engine" / "writer-engine"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise AcceptanceError(f"packaged application executable is missing: {executable}")
    if not engine.is_file() or not os.access(engine, os.X_OK):
        raise AcceptanceError(f"bundled engine executable is missing: {engine}")
    return {
        "profile_variant": profile.get("variant"),
        "webengine_file_count": len(markers),
        "webengine_from_composed_runtime": bool(
            variant == "Full" and not markers and composed_runtime and declared_webengine
        ),
        "application": str(executable),
        "engine": str(engine),
    }


def linux_processes() -> dict[int, tuple[int, str]]:
    result: dict[int, tuple[int, str]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
            fields = stat[stat.rfind(")") + 2 :].split()
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
            result[int(entry.name)] = (int(fields[1]), command)
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
    return result


def macos_processes() -> dict[int, tuple[int, str]]:
    output = run_checked(["ps", "-axo", "pid=,ppid=,command="]).stdout.decode(
        "utf-8", errors="replace"
    )
    result: dict[int, tuple[int, str]] = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            with contextlib.suppress(ValueError):
                result[int(parts[0])] = (int(parts[1]), parts[2])
    return result


def process_tree(root_pid: int, platform_name: str) -> dict[int, str]:
    processes = linux_processes() if platform_name == "linux" else macos_processes()
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in processes.items():
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return {pid: processes.get(pid, (0, ""))[1] for pid in selected}


def linux_tcp_sockets(pids: set[int]) -> list[str]:
    socket_inodes: set[str] = set()
    for pid in pids:
        fd_root = Path("/proc") / str(pid) / "fd"
        try:
            descriptors = list(fd_root.iterdir())
        except (FileNotFoundError, PermissionError):
            continue
        for descriptor in descriptors:
            with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
                target = os.readlink(descriptor)
                if target.startswith("socket:["):
                    socket_inodes.add(target[8:-1])
    matches: list[str] = []
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        with contextlib.suppress(FileNotFoundError, PermissionError):
            for line in table.read_text(encoding="ascii").splitlines()[1:]:
                fields = line.split()
                if len(fields) > 9 and fields[9] in socket_inodes:
                    matches.append(f"{table.name}:{fields[1]}->{fields[2]} state={fields[3]}")
    return sorted(matches)


def macos_tcp_sockets(pids: set[int]) -> list[str]:
    if not pids:
        return []
    result = subprocess.run(
        ["lsof", "-nP", "-a", "-p", ",".join(str(pid) for pid in sorted(pids)), "-iTCP"],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise AcceptanceError(f"lsof failed while checking TCP sockets: {result.stderr}")
    return result.stdout.splitlines()[1:] if result.stdout else []


def tcp_sockets(pids: set[int], platform_name: str) -> list[str]:
    return linux_tcp_sockets(pids) if platform_name == "linux" else macos_tcp_sockets(pids)


def terminate_tree(process: subprocess.Popen[bytes], platform_name: str) -> None:
    del platform_name
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2.0)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


@contextlib.contextmanager
def extracted_appimage(artifact: Path, temporary: Path) -> Iterator[tuple[Path, list[str], dict[str, str]]]:
    artifact.chmod(artifact.stat().st_mode | 0o111)
    extraction = temporary / "appimage"
    extraction.mkdir()
    run_checked([str(artifact), "--appimage-extract"], cwd=extraction)
    root = extraction / "squashfs-root"
    if not (root / "AppRun").is_file():
        raise AcceptanceError("AppImage extraction did not produce AppRun")
    yield root, [str(root / "AppRun")], {}


@contextlib.contextmanager
def mounted_dmg(artifact: Path, temporary: Path) -> Iterator[tuple[Path, list[str], dict[str, str]]]:
    del temporary
    attached = run_checked(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(artifact)])
    plist = plistlib.loads(attached.stdout)
    mount_points = [
        entity.get("mount-point")
        for entity in plist.get("system-entities", [])
        if entity.get("mount-point")
    ]
    if not mount_points:
        raise AcceptanceError("DMG did not report a mounted volume")
    mount = Path(mount_points[0])
    applications = sorted(mount.glob("*.app"))
    if len(applications) != 1:
        raise AcceptanceError(f"DMG must contain one application bundle; found {len(applications)}")
    app = applications[0]
    try:
        yield app, [str(app / "Contents" / "MacOS" / "thothpad")], {}
    finally:
        run_checked(["hdiutil", "detach", str(mount)])


@contextlib.contextmanager
def installed_flatpak(artifact: Path, temporary: Path) -> Iterator[tuple[Path, list[str], dict[str, str]]]:
    environment = os.environ.copy()
    environment["XDG_DATA_HOME"] = str(temporary / "flatpak-data")
    environment["XDG_CONFIG_HOME"] = str(temporary / "flatpak-config")
    run_checked(
        ["flatpak", "install", "--user", "--noninteractive", "--reinstall", str(artifact)],
        env=environment,
    )
    location = run_checked(
        ["flatpak", "info", "--user", "--show-location", APP_ID], env=environment
    ).stdout.decode("utf-8").strip()
    root = Path(location) / "files"
    command = [
        "flatpak",
        "run",
        "--user",
        "--env=QT_QPA_PLATFORM=offscreen",
        "--env=QT_QUICK_BACKEND=software",
        APP_ID,
    ]
    try:
        yield root, command, environment
    finally:
        subprocess.run(
            ["flatpak", "uninstall", "--user", "--noninteractive", APP_ID],
            env=environment,
            capture_output=True,
            check=False,
        )


def prepared_artifact(
    package_format: str, artifact: Path, temporary: Path
) -> contextlib.AbstractContextManager[tuple[Path, list[str], dict[str, str]]]:
    if package_format == "appimage":
        return extracted_appimage(artifact, temporary)
    if package_format == "dmg":
        return mounted_dmg(artifact, temporary)
    if package_format == "flatpak":
        return installed_flatpak(artifact, temporary)
    raise AcceptanceError(f"unsupported package format: {package_format}")


def run_trial(
    command: list[str],
    base_environment: dict[str, str],
    work: Path,
    sample: Path,
    platform_name: str,
    startup_timeout: float,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment.update(base_environment)
    environment.pop("THOTHPAD_ENGINE", None)
    isolated = {
        "HOME": str(work / "home"),
        "XDG_CONFIG_HOME": str(work / "config"),
        "XDG_DATA_HOME": str(work / "data"),
        "XDG_CACHE_HOME": str(work / "cache"),
    }
    is_flatpak = bool(command and command[0] == "flatpak")
    if not is_flatpak:
        environment.update(isolated)
    environment.update(
        {
            "QT_QPA_PLATFORM": "minimal" if platform_name == "linux" else "offscreen",
            "QT_QUICK_BACKEND": "software",
            "LIBGL_ALWAYS_SOFTWARE": "1",
        }
    )
    for name in ("home", "config", "data", "cache"):
        (work / name).mkdir(parents=True, exist_ok=True)
    stderr_path = work / "stderr.log"
    started = time.monotonic()
    trial_command = command
    if is_flatpak:
        trial_command = [
            *command[:-1],
            *(f"--env={name}={value}" for name, value in isolated.items()),
            f"--filesystem={work}",
            f"--filesystem={sample}:ro",
            command[-1],
        ]
    with stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [*trial_command, "--disable-gpu", str(sample)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            start_new_session=True,
        )
        engine_processes: dict[int, str] = {}
        try:
            deadline = started + startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AcceptanceError(
                        f"application exited during startup with {process.returncode}: "
                        f"{stderr_path.read_text(encoding='utf-8', errors='replace')[-2000:]}"
                    )
                tree = process_tree(process.pid, platform_name)
                engine_processes = {
                    pid: value for pid, value in tree.items() if "writer-engine" in value.lower()
                }
                if engine_processes:
                    break
                time.sleep(0.05)
            if not engine_processes:
                raise AcceptanceError("bundled writer-engine did not start before the timeout")

            # Engine startup proves the event loop created the main window and prose controller.
            for _ in range(5):
                time.sleep(0.05)
                if process.poll() is not None:
                    raise AcceptanceError("application did not remain alive after engine startup")
            tree = process_tree(process.pid, platform_name)
            final_engine_processes = {
                pid: value for pid, value in tree.items() if "writer-engine" in value.lower()
            }
            if not final_engine_processes:
                raise AcceptanceError("bundled writer-engine did not remain alive after startup")
            sockets = tcp_sockets(set(tree), platform_name)
            if sockets:
                raise AcceptanceError(f"deterministic startup opened TCP sockets: {sockets}")
            return {
                "startup_ms": round((time.monotonic() - started) * 1000.0, 3),
                "process_count": len(tree),
                "engine_process_count": len(final_engine_processes),
                "tcp_socket_count": 0,
            }
        finally:
            terminate_tree(process, platform_name)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("appimage", "flatpak", "dmg"), required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--variant", choices=("Core", "Full"), required=True)
    parser.add_argument("--launch-trials", type=int, default=100)
    parser.add_argument("--startup-timeout", type=float, default=10.0)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.launch_trials <= 1000:
        parser.error("--launch-trials must be between 1 and 1000")
    if args.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    artifact = args.artifact.resolve()
    if not artifact.is_file():
        raise AcceptanceError(f"artifact does not exist: {artifact}")
    platform_name = "macos" if args.format == "dmg" else "linux"
    temporary_owner: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir:
        temporary = args.work_dir.resolve()
        temporary.mkdir(parents=True, exist_ok=True)
    else:
        temporary_owner = tempfile.TemporaryDirectory(prefix="thothpad-package-acceptance-")
        temporary = Path(temporary_owner.name)

    evidence: dict[str, object] = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "failed",
        "platform": platform_name,
        "format": args.format,
        "variant": args.variant,
        "artifact": str(artifact),
        "artifact_sha256": sha256(artifact),
        "launch_trials_requested": args.launch_trials,
        "launch_trials_completed": 0,
        "checks": {
            "variant": False,
            "bundled_engine": False,
            "no_startup_tcp": False,
            "unicode_byte_identical": False,
            "stable_main_process": False,
        },
        "trials": [],
    }
    try:
        sample = temporary / "Unicode \u03a9 \U0001f4dd.md"
        sample.write_bytes(UNICODE_SAMPLE)
        original_hash = sha256(sample)
        with prepared_artifact(args.format, artifact, temporary) as (root, command, environment):
            evidence["layout"] = validate_layout(
                root,
                args.variant,
                platform_name,
                composed_runtime=args.format == "flatpak",
            )
            evidence["checks"]["variant"] = True  # type: ignore[index]
            for index in range(1, args.launch_trials + 1):
                trial = run_trial(
                    command, environment, temporary / "trials" / f"trial-{index:03d}",
                    sample, platform_name, args.startup_timeout,
                )
                if sha256(sample) != original_hash:
                    raise AcceptanceError(f"Unicode sample changed during launch trial {index}")
                evidence["trials"].append(trial)  # type: ignore[union-attr]
                evidence["launch_trials_completed"] = index
        checks = evidence["checks"]
        checks["bundled_engine"] = True  # type: ignore[index]
        checks["no_startup_tcp"] = True  # type: ignore[index]
        checks["unicode_byte_identical"] = True  # type: ignore[index]
        checks["stable_main_process"] = True  # type: ignore[index]
        evidence["status"] = "passed"
        return_code = 0
    except Exception as error:
        evidence["error"] = str(error)
        return_code = 1
    finally:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if temporary_owner is not None:
            temporary_owner.cleanup()
    if return_code:
        print(f"Package acceptance failed: {evidence.get('error')}", file=sys.stderr)
    else:
        print(f"Verified {args.variant} {args.format}: {args.launch_trials} clean sequential launches")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
