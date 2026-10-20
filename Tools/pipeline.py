#!/usr/bin/env python3
"""Cloud-safe preflight for the SquadPad Unreal preparation package.

This CLI reads local files only. It does not run Unreal or Blender, install
software, access a device or create a native build.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "Content/Python"))
sys.path.insert(0, str(PROJECT / "Tools/Drill"))

from squadpad_plan import InputError, plan_import  # noqa: E402
from squadpad_quality import check_contact_geometry, validate_contacts  # noqa: E402
from export_first_pass import build_fixture, load_inputs, validate as validate_fixture  # noqa: E402


def read_json(path):
    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid_constant)


def emit(value, output=None):
    rendered = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if output:
        path = Path(output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {path}")
    else:
        print(rendered, end="")


def offline_check(manifest_path):
    plan = plan_import(manifest_path, require_files=False)
    root = Path(plan["project_root"])
    fixture = read_json(plan["fixture_path"])
    validate_fixture(fixture)
    rebuilt = build_fixture(*load_inputs(root / "Fixtures/rondo-4v1.level-1.observed.json", None))
    if rebuilt != fixture:
        raise InputError("Packaged fixture differs from deterministic regeneration of its observation")
    contact_contract = validate_contacts(plan)
    syntax_paths = sorted(root.rglob("*.py"))
    syntax_paths = [path for path in syntax_paths if not any(
        part in {"Intermediate", "Saved", "Binaries", ".venv"} for part in path.parts)]
    for path in syntax_paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    project = read_json(root / "SquadPad3D.uproject")
    enabled_plugins = {record["Name"] for record in project["Plugins"] if record.get("Enabled")}
    required_plugins = {"PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting"}
    if not required_plugins.issubset(enabled_plugins):
        raise InputError("Editor import plugins are missing from the project descriptor")
    missing = [{"id": source["id"], "kind": source["kind"],
                "path": str(Path(source["path"]).relative_to(root))}
               for source in plan["sources"] if source["sha256"] is None]
    return {"schema": "squadpad.offline.validation.v1", "status": "offline_preparation_valid",
            "scope": "local input, Python syntax and deterministic fixture checks only",
            "manifest_sha256": plan["manifest_sha256"], "fixture_sha256": plan["fixture_sha256"],
            "checks": {"input_contract": "valid", "fixture_regeneration": "identical",
                       "python_syntax": "valid", "python_files_checked": len(syntax_paths),
                       "required_editor_plugins": "declared", "contacts": contact_contract},
            "source_files": {"status": "missing" if missing else "present", "missing": missing,
                             "scope": "presence, signatures and optional declared hashes only"},
            "contact_geometry": check_contact_geometry(plan),
            "execution_gates": {"asset_export": "not_executed_by_this_check",
                                "fbx_skeleton_and_basis": "not_verified_by_this_check",
                                "unreal_import": "not_executed_by_this_check",
                                "sequence_playback": "not_executed_by_this_check",
                                "visual_quality": "not_reviewed_by_this_check",
                                "ios_integration": "not_built_by_this_check",
                                "target_device_performance": "not_measured_by_this_check"},
            "remote_access_used": False, "native_quality_accepted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT / "import_manifest.json")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("offline-check", "plan", "preflight"):
        sub = commands.add_parser(name)
        sub.add_argument("--output", type=Path)
    commands.choices["plan"].add_argument("--require-assets", action="store_true")
    contacts = commands.add_parser("contacts")
    contacts.add_argument("--measurements", type=Path)
    contacts.add_argument("--output", type=Path)
    contacts.add_argument("--distance-tolerance-cm", type=float, default=1.5)
    contacts.add_argument("--time-tolerance-seconds", type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == "offline-check":
            emit(offline_check(args.manifest), args.output)
            return 0
        plan = plan_import(args.manifest, require_files=args.command == "preflight" or
                           getattr(args, "require_assets", False))
        findings = validate_contacts(plan)
        if args.command == "contacts":
            measurements = read_json(args.measurements) if args.measurements else None
            report = check_contact_geometry(plan, measurements, args.distance_tolerance_cm,
                                            args.time_tolerance_seconds)
            emit(report, args.output)
            return {"pass": 0, "fail": 1, "blocked": 3}[report["status"]]
        if args.command == "preflight":
            emit({"status": "inputs_ready_for_engine_import", "scope": "input files only",
                  "contact_metadata": findings, "all_source_files_present": plan["all_source_files_present"],
                  "unreal_import_verified": False, "visual_quality_accepted": False}, args.output)
        else:
            plan["contact_metadata"] = findings
            emit(plan, args.output)
        return 0
    except (ValueError, OSError, TypeError, KeyError, SyntaxError) as error:
        print(f"Preflight failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
