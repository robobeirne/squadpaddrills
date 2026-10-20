"""Build the real Editor target and require one fresh native reader contract result.

Default mode is preflight: it observes an explicit installed engine and prints a
plan, without invoking UBT or Unreal. Linux/macOS execution uses POSIX process
groups; Windows preflight is supported, but Windows execution is not implemented.
No engine installation, source asset import, signing or device build occurs here.

The command and report contract follows Epic's current documentation:
https://dev.epicgames.com/documentation/en-us/unreal-engine/run-automation-tests-in-unreal-engine
https://dev.epicgames.com/documentation/en-us/unreal-engine/review-test-results-in-unreal-engine
https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-command-line-arguments-reference
Native command/API compatibility still needs an actual installed-engine run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
TARGET = "SquadPad3DEditor"
TEST = "SquadPad.Runtime.PlaybackReader.Contract"
SCHEMA = "squadpad.native-gate.v1"
REQUIRED_INPUTS = (
    "Source/SquadPad3DEditor.Target.cs",
    "Source/SquadPadRuntime/SquadPadRuntime.Build.cs",
    "Source/SquadPadRuntime/Private/Tests/SquadPadPlaybackReaderTests.cpp",
    "Source/SquadPadRuntime/Private/SquadPadPlaybackReader.cpp",
    "Source/SquadPadRuntime/Public/SquadPadPlaybackReader.h",
    "RuntimeCore/include/squadpad/playback.hpp",
    "RuntimeCore/src/playback.cpp",
    "Fixtures/Playback/first_pass.playback.json",
)


class GateError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, maximum_bytes=10 * 1024 * 1024):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"Missing or symlinked JSON file: {path}")
    require(path.stat().st_size <= maximum_bytes, f"JSON exceeds size limit: {path}")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON member {key!r}: {path}")
            result[key] = value
        return result

    def invalid_number(value):
        raise GateError(f"Nonfinite JSON value {value}: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"),
                          object_pairs_hook=object_pairs, parse_constant=invalid_number)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GateError(f"Invalid JSON in {path}: {error}") from error


def file_record(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size > 0, f"Required file is missing or empty: {path}")
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": digest(path)}


def source_snapshot(project):
    root = project.parent
    for name in REQUIRED_INPUTS:
        require((root / name).is_file(), f"Required project input is missing: {name}")
    paths = {project, *[root / name for name in REQUIRED_INPUTS]}
    for directory in ("Source", "RuntimeCore/include", "RuntimeCore/src", "Config", "Content/Python"):
        paths.update(p for p in (root / directory).rglob("*")
                     if p.is_file() and (directory != "Content/Python" or p.suffix == ".py"))
    # Hash the executing gate as well as every native source/configuration input.
    paths.add(Path(__file__).resolve())
    result = {}
    for path in sorted(paths):
        require(path.resolve().is_relative_to(root), f"Input escapes project root: {path}")
        result[str(path.relative_to(root))] = digest(path)
    return result


def git_identity(root):
    git = shutil.which("git")
    require(git is not None, "git is required to record the exact source commit")

    def git_read(*args):
        try:
            result = subprocess.run([git, "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                    capture_output=True, text=True, timeout=15, check=False)
        except subprocess.TimeoutExpired as error:
            raise GateError("Timed out reading source git identity") from error
        require(result.returncode == 0, "Cannot record source git identity: " + result.stderr.strip())
        return result.stdout.strip()

    commit = git_read("rev-parse", "HEAD")
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) is not None,
            "Source checkout has no full git commit identity")
    status = git_read("status", "--porcelain=v1", "--untracked-files=all")
    return {"commit": commit, "dirty": bool(status), "status_porcelain": status}


def observed_engine(engine_root, project, host):
    root = engine_root.resolve()
    version_path = root / "Engine/Build/Build.version"
    version = read_json(version_path, 256 * 1024)
    require(isinstance(version, dict), "Engine Build.version must be an object")
    for key in ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist"):
        require(type(version.get(key)) is int and version[key] >= 0,
                f"Engine Build.version has no valid {key}")
    project_data = read_json(project, 256 * 1024)
    require(isinstance(project_data, dict), "Project descriptor must be an object")
    association = project_data.get("EngineAssociation")
    require(association == f"{version['MajorVersion']}.{version['MinorVersion']}",
            f"Project EngineAssociation {association!r} does not match observed engine major/minor")
    layouts = {
        "Linux": ("Linux", "Engine/Build/BatchFiles/Linux/Build.sh",
                  ("Engine/Binaries/Linux/UnrealEditor", "Engine/Binaries/Linux/UnrealEditor-Cmd")),
        "Darwin": ("Mac", "Engine/Build/BatchFiles/Mac/Build.sh",
                   ("Engine/Binaries/Mac/UnrealEditor-Cmd",
                    "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor")),
        "Windows": ("Win64", "Engine/Build/BatchFiles/Build.bat",
                    ("Engine/Binaries/Win64/UnrealEditor-Cmd.exe",)),
    }
    require(host in layouts, f"Unsupported host platform: {host}")
    target_platform, wrapper_name, editor_names = layouts[host]
    wrapper = root / wrapper_name
    candidates = [root / name for name in editor_names if (root / name).is_file()]
    require(candidates, "No installed Editor executable exists at the supported engine paths")
    editor = candidates[0]
    if host != "Windows":
        require(os.access(editor, os.X_OK), f"Editor is not executable: {editor}")
        require(Path("/bin/bash").is_file(), "The installed build wrapper requires /bin/bash")
    with editor.open("rb") as binary:
        magic = binary.read(4)
    known_magic = {"Linux": (b"\x7fELF",),
                   "Darwin": (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf"),
                   "Windows": (b"MZ",)}
    require(any(magic.startswith(value) for value in known_magic[host]),
            "Editor path is not a native binary for this host; script stand-ins are not accepted")
    files = {"build_version": file_record(version_path), "build_wrapper": file_record(wrapper),
             "editor": file_record(editor),
             "unreal_build_tool": file_record(root / "Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll")}
    return {"root": str(root), "host": host, "target_platform": target_platform,
            "build_version": version, "files": files,
            "toolchain_status": "unverified_until_real_UBT_build_succeeds"}


def commands(engine, project, output):
    wrapper = engine["files"]["build_wrapper"]["path"]
    build = [wrapper, TARGET, engine["target_platform"], "Development", f"-Project={project}", "-WaitMutex"]
    if engine["host"] != "Windows":
        build.insert(0, "/bin/bash")
    editor = [engine["files"]["editor"]["path"], str(project),
              "-unattended", "-NullRHI", "-nosound", "-nosplash", "-nop4",
              "-stdout", "-UTF8Output",
              f"-AbsLog={output / 'editor.log'}",
              f"-ReportExportPath={output / 'automation'}",
              f"-ExecCmds=Automation RunTest {TEST};Quit"]
    return {"build": build, "editor": editor}


def kill_process_group(process):
    # Even if the wrapper exits on SIGTERM, kill remaining compiler/editor children.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            continue
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as error:
        raise GateError("Failed to reap the timed-out native process") from error


def run_process(argv, cwd, log_path, timeout_seconds):
    require(os.name == "posix", "Native process execution currently supports Linux/macOS only")
    result = {"argv": argv, "started_at_utc": timestamp(), "started_epoch_ns": time.time_ns(),
              "timeout_seconds": timeout_seconds, "log_path": str(log_path),
              "launched": False, "timed_out": False, "returncode": None}
    start = time.monotonic()
    environment = os.environ.copy()
    environment["TZ"] = "UTC"  # Makes Unreal's unzoned JSON report timestamps unambiguous.
    process = None
    with Path(log_path).open("xb") as log:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            result["launched"] = True
            try:
                result["returncode"] = process.wait(timeout=timeout_seconds)
                # A wrapper can finish before a child that still owns our log FD.
                # Preserve its actual result while stopping remaining group members.
                completed_returncode = result["returncode"]
                kill_process_group(process)
                result["returncode"] = completed_returncode
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                kill_process_group(process)
                result["returncode"] = process.returncode
        except OSError as error:
            result["launch_error"] = str(error)
        except KeyboardInterrupt:
            result["interrupted"] = True
            if process is not None:
                kill_process_group(process)
                result["returncode"] = process.returncode
        except BaseException:
            if process is not None:
                kill_process_group(process)
            raise
    result.update(finished_at_utc=timestamp(), finished_epoch_ns=time.time_ns(),
                  duration_seconds=round(time.monotonic() - start, 3), log_sha256=digest(log_path))
    return result


def require_process_success(result, name):
    require(result["launched"], f"{name} could not launch: {result.get('launch_error', 'unknown error')}")
    require(not result.get("interrupted"), f"{name} was interrupted")
    require(not result["timed_out"], f"{name} exceeded its time limit")
    require(result["returncode"] == 0, f"{name} failed with exit code {result['returncode']}")


def built_module(project, target_platform):
    directory = project.parent / "Binaries" / target_platform
    manifest_path = directory / "UnrealEditor.modules"
    manifest = read_json(manifest_path, 256 * 1024)
    require(isinstance(manifest, dict) and isinstance(manifest.get("Modules"), dict),
            "UBT did not produce a valid Editor module manifest")
    filename = manifest["Modules"].get("SquadPadRuntime")
    require(isinstance(filename, str) and filename and Path(filename).name == filename
            and "/" not in filename and "\\" not in filename,
            "The Editor module manifest does not identify a local SquadPadRuntime binary")
    require(isinstance(manifest.get("BuildId"), str) and manifest["BuildId"], "Editor module BuildId is missing")
    return {"manifest": file_record(manifest_path), "binary": file_record(directory / filename),
            "build_id": manifest["BuildId"]}


def validate_report(path, started_ns, finished_ns, target_platform):
    path = Path(path)
    report = read_json(path)
    require(isinstance(report, dict), "Automation report must be an object")
    tolerance_ns = 2_000_000_000  # Allow coarse filesystem/report timestamp resolution.
    require(started_ns - tolerance_ns <= path.stat().st_mtime_ns <= finished_ns + tolerance_ns,
            "Automation report file is stale or outside this editor run")
    try:
        created = datetime.strptime(report["reportCreatedOn"], "%Y.%m.%d-%H.%M.%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as error:
        raise GateError("Automation report has no valid reportCreatedOn timestamp") from error
    require(started_ns - tolerance_ns <= int(created.timestamp() * 1e9) <= finished_ns + tolerance_ns,
            "Automation report contains a stale or future creation timestamp")
    for name in ("succeeded", "succeededWithWarnings"):
        require(type(report.get(name)) is int and report[name] in (0, 1),
                f"Automation report requires an integral {name} count of zero or one")
    require(report["succeeded"] + report["succeededWithWarnings"] == 1,
            "Automation report must contain exactly one successful test")
    for name, expected in (("failed", 0), ("notRun", 0), ("inProcess", 0)):
        require(type(report.get(name)) is int and report[name] == expected,
                f"Automation report requires {name}={expected}")
    if "skipped" in report:
        require(type(report["skipped"]) is int and report["skipped"] == 0, "Automation report contains skipped tests")
    tests = report.get("tests")
    require(isinstance(tests, list) and len(tests) == 1, "Automation must report exactly one test, never zero or a broader suite")
    test = tests[0]
    require(isinstance(test, dict) and test.get("fullTestPath") == TEST, "Automation ran the wrong test")
    require(test.get("state") == "Success", "Native reader contract did not finish with Success")
    require(type(test.get("errors")) is int and test["errors"] == 0,
            "Native reader contract has missing/nonzero errors")
    require(type(test.get("warnings")) is int and test["warnings"] >= 0,
            "Native reader contract has no valid warning count")
    require(report["succeededWithWarnings"] == int(test["warnings"] > 0),
            "Native reader warning count disagrees with the report success totals")
    for value in (report.get("totalDuration"), test.get("duration")):
        require(type(value) in (float, int) and math.isfinite(value) and value >= 0,
                "Automation report has an invalid duration")
    entries = test.get("entries")
    require(isinstance(entries, list), "Native reader contract event entries are missing")
    warning_messages = []
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("event"), dict)
                and entry["event"].get("type") in ("Info", "Warning"),
                "Native reader contract contains an error or malformed event")
        if entry["event"]["type"] == "Warning":
            require(isinstance(entry["event"].get("message"), str), "Native warning event has no message")
            warning_messages.append(entry["event"]["message"])
    require(len(warning_messages) == test["warnings"], "Native warning events disagree with the warning count")
    devices = report.get("devices")
    expected_platform = {"Linux": "LinuxEditor", "Mac": "MacEditor", "Win64": "WindowsEditor"}[target_platform]
    require(isinstance(devices, list) and len(devices) == 1 and isinstance(devices[0], dict),
            "Automation must identify the single local Editor device")
    device = devices[0]
    require(device.get("platform") == expected_platform and isinstance(device.get("instance"), str)
            and device["instance"], "Automation device does not match the requested Editor platform")
    require(test.get("deviceInstance") == [device["instance"]], "Native test device does not match the report device")
    return {"status": "passed", "test_path": TEST, "state": "Success", "test_count": 1,
            "report_path": str(path), "report_sha256": digest(path),
            "report_created_on": report["reportCreatedOn"], "duration_seconds": test["duration"],
            "device_platform": expected_platform, "warning_count": test["warnings"],
            "warning_messages": warning_messages}


def runtime_engine_identity(output, engine):
    found = set()
    pattern = re.compile(r"LogInit:\s*Engine Version:\s*(\d+)\.(\d+)\.(\d+)-(\d+)([^\r\n]*)")
    for path in (output / "editor.log", output / "editor-stdout.log"):
        if path.is_file():
            with path.open(encoding="utf-8-sig", errors="replace") as log:
                for line in log:
                    match = pattern.search(line)
                    if match:
                        found.add(tuple(int(match.group(index)) for index in range(1, 5)))
    version = engine["build_version"]
    expected = tuple(version[key] for key in ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist"))
    require(found == {expected}, "Running Editor engine version/changelist is missing or differs from Build.version")
    return {"version": ".".join(str(x) for x in expected[:3]), "changelist": expected[3],
            "matches_observed_build_version": True}


def write_receipt(path, report):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def execute_gate(args):
    project = args.project.expanduser().resolve()
    require(project.is_file() and project.name == "SquadPad3D.uproject",
            "An existing SquadPad3D.uproject is required")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id) is not None, "Invalid run ID")
    output_parent = project.parent / "Saved/SquadPad/NativeGate"
    require(output_parent.resolve().is_relative_to(project.parent), "Native output path escapes the project")
    output_parent.mkdir(parents=True, exist_ok=True)
    output = output_parent / run_id
    require(not output.exists() and not output.is_symlink(), "Run directory already exists; reports must never be reused")
    output.mkdir()
    receipt_path = output / "receipt.json"
    report = {"schema": SCHEMA, "mode": "run" if args.run else "preflight", "run_id": run_id,
              "status": "running", "recorded_at_utc": timestamp(), "project": str(project),
              "output_directory": str(output), "failure": None, "phases": {}, "automation": None,
              "evidence": {"toolchain_verified": False, "editor_target_built": False,
                           "unreal_editor_executed": False, "native_reader_contract_passed": False,
                           "skeletal_pose_evaluated": False, "contact_geometry_measured": False,
                           "visual_quality_accepted": False, "ios_executed": False},
              "scope": "Installed Editor target build and native playback JSON-reader contract only"}
    report["build_parallelism"] = "Not overridden by gate; UBT applies installed configuration and core/memory scheduling. No fixed action cap is claimed."
    write_receipt(receipt_path, report)
    try:
        require(sys.version_info >= (3, 10), "Python 3.10 or newer is required")
        report["git_before"] = git_identity(project.parent)
        report["source_sha256_before"] = source_snapshot(project)
        engine = observed_engine(args.engine_root.expanduser(), project, platform.system())
        report["engine"] = engine
        report["commands"] = commands(engine, project, output)
        report["timeouts_seconds"] = {"build": args.build_timeout_seconds, "editor": args.test_timeout_seconds}
        if not args.run:
            report["status"] = "preflight_passed"
        else:
            require(engine["host"] in ("Linux", "Darwin"), "Windows native execution is not implemented; preflight only")
            write_receipt(receipt_path, report)
            build = run_process(report["commands"]["build"], project.parent,
                                output / "build.log", args.build_timeout_seconds)
            report["phases"]["build"] = build
            require_process_success(build, "Unreal Build Tool")
            report["evidence"]["toolchain_verified"] = True
            report["built_module"] = built_module(project, engine["target_platform"])
            report["evidence"]["editor_target_built"] = True
            automation = output / "automation"
            require(not automation.exists(), "Automation output already exists before Editor launch")
            automation.mkdir()
            write_receipt(receipt_path, report)
            editor = run_process(report["commands"]["editor"], project.parent,
                                 output / "editor-stdout.log", args.test_timeout_seconds)
            report["phases"]["editor"] = editor
            report["evidence"]["unreal_editor_executed"] = editor["launched"]
            require_process_success(editor, "Unreal Editor")
            report["automation"] = validate_report(automation / "index.json", editor["started_epoch_ns"],
                                                    editor["finished_epoch_ns"], engine["target_platform"])
            report["runtime_engine_identity"] = runtime_engine_identity(output, engine)
            report["status"] = "passed"
    except (GateError, OSError, ValueError, KeyboardInterrupt) as error:
        report["status"] = "failed" if args.run else "preflight_failed"
        report["failure"] = str(error) or "Interrupted"
    finally:
        if "source_sha256_before" in report:
            try:
                report["source_sha256_after"] = source_snapshot(project)
                report["git_after"] = git_identity(project.parent)
                require(report["source_sha256_before"] == report["source_sha256_after"], "Source inputs changed during the gate")
                require(report["git_before"]["commit"] == report["git_after"]["commit"], "Source commit changed during the gate")
                if "engine" in report:
                    report["engine_sha256_after"] = {key: digest(record["path"]) for key, record in report["engine"]["files"].items()}
                    require(report["engine_sha256_after"] == {key: record["sha256"] for key, record in report["engine"]["files"].items()},
                            "Observed engine inputs changed during the gate")
                if "built_module" in report:
                    report["built_module_sha256_after"] = {key: digest(report["built_module"][key]["path"])
                                                            for key in ("manifest", "binary")}
                    require(report["built_module_sha256_after"] == {key: report["built_module"][key]["sha256"]
                                                                   for key in ("manifest", "binary")},
                            "Built runtime module changed during Editor execution")
            except (GateError, OSError) as error:
                report["status"] = "failed" if args.run else "preflight_failed"
                report["failure"] = "; ".join(x for x in (report["failure"], str(error)) if x)
        report["evidence"]["native_reader_contract_passed"] = report["status"] == "passed"
        report["output_sha256"] = {name: digest(output / name)
                                   for name in ("build.log", "editor-stdout.log", "editor.log", "automation/index.json")
                                   if (output / name).is_file() and not (output / name).is_symlink()}
        report["finished_at_utc"] = timestamp()
        write_receipt(receipt_path, report)
    return report, receipt_path


def bounded_timeout(value):
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be whole seconds") from error
    if not 1 <= seconds <= 7200:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 7200 seconds")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True, help="Observed install directory containing Engine/")
    parser.add_argument("--project", type=Path, default=ROOT / "SquadPad3D.uproject")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="Observe prerequisites and commands only (default)")
    mode.add_argument("--run", action="store_true", help="Build the Editor target and execute the exact native contract")
    parser.add_argument("--run-id", help="Unique output slug; an existing directory is always rejected")
    parser.add_argument("--build-timeout-seconds", type=bounded_timeout, default=1800)
    parser.add_argument("--test-timeout-seconds", type=bounded_timeout, default=600)
    args = parser.parse_args(argv)

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    handled_signals = [signal.SIGTERM]
    if os.name == "posix":
        handled_signals.append(signal.SIGHUP)
    previous_handlers = {signum: signal.signal(signum, interrupted) for signum in handled_signals}
    try:
        report, receipt = execute_gate(args)
        print(json.dumps({"status": report["status"], "receipt": str(receipt),
                          "failure": report["failure"], "evidence": report["evidence"]}, indent=2))
        return 0 if report["status"] in ("passed", "preflight_passed") else 1
    except (GateError, OSError, ValueError, KeyboardInterrupt) as error:
        print(json.dumps({"status": "failed", "failure": str(error) or "Interrupted",
                          "native_reader_contract_passed": False}), file=sys.stderr)
        return 1
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


if __name__ == "__main__":
    raise SystemExit(main())
