"""Build and compare the portable C++17 playback core against its Python reference.

Generated typed inputs avoid adding a JSON parser to the core. All binaries and
generated C++ remain temporary. Results are explicitly not Unreal/iOS evidence.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

from evaluate_playback import PlaybackController, PlaybackPlan, read_json

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "RuntimeCore"
FLAGS = ["-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror"]
TOLERANCES = {"position_cm": 1e-7, "yaw_degrees": 1e-8,
              "time_seconds": 1e-10, "source_time_seconds": 1e-9,
              "radius_cm": 1e-10, "playback_rate": 1e-10}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quoted(value):
    # Validated contract identifiers use only ASCII letters/digits/_.:-.
    # json.dumps also supplies safe C++ literal escaping for metadata strings.
    return json.dumps(value if value is not None else "", ensure_ascii=True)


def literal(value):
    value = float(value)
    if math.isnan(value):
        return "std::numeric_limits<double>::quiet_NaN()"
    if math.isinf(value):
        return ("-" if value < 0 else "") + "std::numeric_limits<double>::infinity()"
    text = format(value, ".17g")
    return text if any(c in text for c in ".eE") else text + ".0"


def vec(value):
    return "{" + ",".join(literal(x) for x in value) + "}"


def make_input(document, function_name):
    lines = [f"PlanInput {function_name}() {{", "PlanInput p{};"]
    for field in ("schema", "id", "axes", "root_ownership"):
        lines.append(f"p.{field} = {quoted(document[field])};")
    for source, target in (("distance", "distance_units"), ("time", "time_units"), ("angle", "angle_units")):
        lines.append(f"p.{target} = {quoted(document['units'][source])};")
    lines.append(f"p.duration_seconds = {literal(document['duration_seconds'])};")
    for motion in document["motions"]:
        lines.append(f"p.motions.push_back({{{quoted(motion['id'])},{literal(motion['duration_seconds'])}}});")
    for actor in document["actors"]:
        lines += ["{ Actor a{};", f"a.id={quoted(actor['id'])}; a.appearance_slot={quoted(actor['appearance_slot'])};"]
        for key in actor["transform_keys"]:
            lines.append("a.transform_keys.push_back({" + literal(key["time_seconds"]) + "," + vec(key["position_cm"]) + "," + literal(key["yaw_degrees"]) + "});")
        for section in actor["sections"]:
            fields = [quoted(section["id"]), quoted(section["source_motion_id"])]
            fields += [literal(section[k]) for k in ("start_seconds", "end_seconds", "source_start_seconds", "play_rate")]
            fields.append("true" if section["loop"] else "false")
            lines.append("a.sections.push_back({" + ",".join(fields) + "});")
        lines.append("p.actors.push_back(std::move(a)); }")
    for ball in document["balls"]:
        lines += ["{ Ball b{};", f"b.id={quoted(ball['id'])}; b.radius_cm={literal(ball['radius_cm'])};"]
        for key in ball["position_keys"]:
            lines.append("b.position_keys.push_back({" + literal(key["time_seconds"]) + "," + vec(key["position_cm"]) + "});")
        lines.append("p.balls.push_back(std::move(b)); }")
    for key in document["possession"]:
        state = "PossessionKind::Owned" if key["state"] == "owned" else "PossessionKind::InTransit"
        values = ",".join(quoted(key[k]) for k in ("owner_id", "from_actor_id", "to_actor_id"))
        lines.append(f"p.possession.push_back({{{literal(key['time_seconds'])},{quoted(key['ball_id'])},{{{state},{values}}}}});")
    for contact in document["contacts"]:
        values = [quoted(contact["id"]), quoted(contact["kind"]), literal(contact["time_seconds"])]
        values += [quoted(contact[k]) for k in ("actor_id", "ball_id", "action_id", "source_motion_id")]
        values += [literal(contact["source_contact_seconds"]), quoted(contact["limb"]), quoted(contact["calibration_status"])]
        lines.append("p.contacts.push_back({" + ",".join(values) + "});")
    lines += ["return p;", "}"]
    return "\n".join(lines)


SERIALIZE = r'''
void String(const std::string& s) { std::cout << std::quoted(s); }
void Nullable(const std::string& s) { if(s.empty()) std::cout << "null"; else String(s); }
void Vector(const Vec3& v) { std::cout << '[' << v[0] << ',' << v[1] << ',' << v[2] << ']'; }
void Frame(const FrameState& f) {
  std::cout << "{\"time_seconds\":" << f.time_seconds << ",\"actors\":[";
  for(std::size_t i=0;i<f.actors.size();++i) {
    if(i) { std::cout << ','; }
    const auto& a=f.actors[i];
    std::cout << "{\"id\":"; String(a.id);
    std::cout << ",\"appearance_slot\":"; String(a.appearance_slot);
    std::cout << ",\"position_cm\":"; Vector(a.position_cm);
    std::cout << ",\"yaw_degrees\":" << a.yaw_degrees << ",\"action_id\":"; String(a.action_id);
    std::cout << ",\"source_motion_id\":"; String(a.source_motion_id);
    std::cout << ",\"source_time_seconds\":" << a.source_time_seconds << '}';
  }
  std::cout << "],\"balls\":[";
  for(std::size_t i=0;i<f.balls.size();++i) {
    if(i) { std::cout << ','; }
    const auto& b=f.balls[i];
    std::cout << "{\"id\":"; String(b.id);
    std::cout << ",\"radius_cm\":" << b.radius_cm << ",\"position_cm\":"; Vector(b.position_cm);
    std::cout << ",\"possession\":{\"state\":";
    String(b.possession.state==PossessionKind::Owned ? "owned" : "in_transit");
    std::cout << ",\"owner_id\":"; Nullable(b.possession.owner_id);
    std::cout << ",\"from_actor_id\":"; Nullable(b.possession.from_actor_id);
    std::cout << ",\"to_actor_id\":"; Nullable(b.possession.to_actor_id); std::cout << "}}";
  }
  std::cout << "]}";
}
void StateLine(const std::string& label, const PlaybackController& c, const StepResult& result) {
  std::cout << "{\"kind\":\"control\",\"label\":"; String(label);
  std::cout << ",\"frame\":"; Frame(result.frame);
  std::cout << ",\"crossed_contact_ids\":[";
  for(std::size_t i=0;i<result.crossed_contact_ids.size();++i) { if(i) std::cout << ','; String(result.crossed_contact_ids[i]); }
  std::cout << "],\"is_playing\":" << (c.IsPlaying()?"true":"false")
            << ",\"playback_rate\":" << c.Rate() << "}\n";
}
'''


def sampling_times(document):
    duration = document["duration_seconds"]
    times = {0.0, duration, -1.0, duration + 1.0}
    for rate in (30, 60):
        times.update(i / rate for i in range(math.floor(duration * rate) + 1))
    for actor in document["actors"]:
        for section in actor["sections"]:
            for time in (section["start_seconds"], section["end_seconds"]):
                times.update((time - 1e-9, time, time + 1e-9))
    for contact in document["contacts"]:
        time = contact["time_seconds"]
        times.update((time - 1e-9, time, time + 1e-9))
    rng = random.Random(20261020)
    times.update(rng.uniform(0, duration) for _ in range(32))
    result = sorted(times)
    # Include a reverse traversal and a repeat to verify no evaluation history.
    return result + list(reversed(result[::11])) + result[:8]


def control_commands(document):
    release, receive = [c["time_seconds"] for c in document["contacts"][:2]]
    duration = document["duration_seconds"]
    return [("initial", "observe", None), ("paused_advance", "advance", 0.3),
            ("play", "play", None), ("exact_release", "advance", release),
            ("zero_delta", "advance", 0.0), ("pause", "pause", None),
            ("paused_hold", "advance", 0.5), ("seek_before_release", "seek", release - 1e-6),
            ("quarter_rate", "set_rate", 0.25), ("resume", "play", None),
            ("cross_release", "advance", 8e-6), ("seek_receive", "seek", receive),
            ("restart", "restart", None), ("double_rate", "set_rate", 2.0),
            ("replay", "play", None), ("exact_receive", "advance", receive / 2.0),
            ("end_clamp", "advance", duration), ("play_at_end", "play", None),
            ("restart_again", "restart", None), ("negative_seek", "seek", -4.0),
            ("high_seek", "seek", 2 * duration), ("huge_rate", "set_rate", 1e308),
            ("seek_zero", "seek", 0.0), ("huge_play", "play", None),
            ("huge_zero_delta", "advance", 0.0), ("huge_advance", "advance", 1e308)]


def independent_documents(document):
    out = []
    for name, start_yaw, end_yaw in (("yaw_wrap", 350.0, 10.0),
                                     ("positive_half_turn", 10.0, 190.0),
                                     ("negative_half_turn", 190.0, 10.0)):
        value = copy.deepcopy(document)
        value["id"] = "independent." + name
        value["actors"][0]["transform_keys"] = [
            {"time_seconds": 0.0, "position_cm": [0.0, 20.0, 0.0], "yaw_degrees": start_yaw},
            {"time_seconds": value["duration_seconds"], "position_cm": [400.0, -20.0, 0.0], "yaw_degrees": end_yaw}]
        out.append((name, value, [0.0, value["duration_seconds"] * 0.25,
                                  value["duration_seconds"] * 0.5, value["duration_seconds"]]))
    return out


def compare(expected, actual, path, maxima):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(expected) != set(actual):
            raise AssertionError(f"Object fields disagree at {path}")
        for key, value in expected.items(): compare(value, actual[key], path + "." + key, maxima)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise AssertionError(f"List length disagrees at {path}")
        for index, value in enumerate(expected): compare(value, actual[index], path + f"[{index}]", maxima)
    elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
        if not isinstance(actual, (float, int)) or isinstance(actual, bool) or not math.isfinite(actual):
            raise AssertionError(f"Invalid native number at {path}")
        name = next((key for key in TOLERANCES if "." + key in path), "time_seconds")
        error = abs(expected - actual)
        maxima[name] = max(maxima.get(name, 0.0), error)
        if error > TOLERANCES[name]:
            raise AssertionError(f"Numeric mismatch {path}: expected {expected}, got {actual}, tolerance {TOLERANCES[name]}")
    elif type(expected) is not type(actual) or expected != actual:
        raise AssertionError(f"State mismatch at {path}: {expected!r} != {actual!r}")


def harness(document):
    actual_plan = PlaybackPlan(document)
    times = sampling_times(document)
    definitions = [make_input(document, "ActualInput")]
    body = ["const auto actual = PlaybackPlan::Create(ActualInput());"]
    expected = []
    cases = [("actual_first_pass", document, times)] + independent_documents(document)
    for index, (name, value, samples) in enumerate(cases):
        if index:
            definitions.append(make_input(value, f"CaseInput{index}"))
        expression = "actual" if index == 0 else f"PlaybackPlan::Create(CaseInput{index}())"
        body.append("{ const auto plan = " + expression + ";")
        body.append("for(double time : std::vector<double>{" + ",".join(literal(t) for t in samples) + "}) {")
        body.append("std::cout << \"{\\\"kind\\\":\\\"sample\\\",\\\"case\\\":\"; String(" + quoted(name) + ");")
        body.append("std::cout << \",\\\"frame\\\":\"; Frame(plan.Evaluate(time)); std::cout << \"}\\n\"; } }")
        reference = actual_plan if index == 0 else PlaybackPlan(value)
        expected.extend({"kind": "sample", "case": name, "frame": reference.evaluate(t)} for t in samples)
    body.append("PlaybackController control(actual);")
    reference_control = PlaybackController(actual_plan)
    methods = {"play": "Play", "pause": "Pause", "seek": "Seek", "restart": "Restart", "set_rate": "SetRate", "advance": "Advance"}
    for label, method, argument in control_commands(document):
        body.append("{")
        if method == "observe":
            result = {"frame": reference_control.plan.evaluate(reference_control.time_seconds), "crossed_contact_ids": []}
            body.append("StepResult result{control.CurrentFrame(), {}};")
        else:
            result = getattr(reference_control, method)(argument) if argument is not None else getattr(reference_control, method)()
            call = "control." + methods[method] + "(" + (literal(argument) if argument is not None else "") + ")"
            if method in ("seek", "restart", "advance"):
                body.append("const auto result = " + call + ";")
            else:
                body += [call + ";", "StepResult result{control.CurrentFrame(), {}};"]
                result = {"frame": reference_control.plan.evaluate(reference_control.time_seconds), "crossed_contact_ids": []}
        expected.append(dict(kind="control", label=label, **result,
                             is_playing=reference_control.is_playing, playback_rate=reference_control.playback_rate))
        body += ["StateLine(" + quoted(label) + ", control, result);", "}"]
    source = '\n'.join(['#include "squadpad/playback.hpp"', '#include <iostream>', '#include <iomanip>',
                         '#include <limits>', '#include <utility>', 'using namespace squadpad::playback;',
                         SERIALIZE] + definitions + ['int main() { try { std::cout << std::setprecision(17);'] + body +
                        ['return 0; } catch(const std::exception& e) { std::cerr << e.what(); return 1; } }'])
    return source, expected, len(times), len(cases) - 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "Fixtures/Playback/first_pass.playback.json")
    parser.add_argument("--report", type=Path, default=ROOT / "Reports/runtime-core-validation.json")
    parser.add_argument("--compiler", default="g++")
    args = parser.parse_args(argv)
    compiler = shutil.which(args.compiler)
    if not compiler:
        raise SystemExit("C++ compiler unavailable")
    fixture = args.fixture.resolve()
    document = read_json(fixture)
    source, expected, actual_samples, independent_cases = harness(document)
    input_files = [CORE / "include/squadpad/playback.hpp", CORE / "src/playback.cpp",
                   CORE / "tests/playback_tests.cpp", Path(__file__),
                   Path(__file__).with_name("evaluate_playback.py"), fixture]
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in input_files}
    compiler_version = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix="squadpad-playback-native-") as work:
        work = Path(work)
        unit = work / "playback-unit"
        probe = work / "playback-fixture"
        generated = work / "fixture_harness.cpp"
        generated.write_text(source, encoding="utf-8")
        common = [compiler, *FLAGS, "-I", str(CORE / "include"), str(CORE / "src/playback.cpp")]
        subprocess.run([*common, str(CORE / "tests/playback_tests.cpp"), "-o", str(unit)], check=True, capture_output=True, text=True)
        unit_result = json.loads(subprocess.run([str(unit)], check=True, capture_output=True, text=True).stdout)
        subprocess.run([*common, str(generated), "-o", str(probe)], check=True, capture_output=True, text=True)
        native = [json.loads(line) for line in subprocess.run([str(probe)], check=True, capture_output=True, text=True).stdout.splitlines()]
        if len(native) != len(expected): raise AssertionError("Native result count disagrees")
        maxima = {}
        for index, (reference, result) in enumerate(zip(expected, native)):
            compare(reference, result, f"result[{index}]", maxima)
    if hashes != {str(path.relative_to(ROOT)): digest(path) for path in input_files}:
        raise AssertionError("Inputs changed during verification")
    report = {
        "schema": "squadpad.portable-native-validation.v1", "status": "passed",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "portable C++17 typed validation, state and controller parity only",
        "compiler": {"path": compiler, "version": compiler_version, "flags": FLAGS},
        "source_sha256": hashes, "generated_harness_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "numeric_tolerances": TOLERANCES, "maximum_observed_absolute_errors": maxima,
        "unit_checks": unit_result["checks"], "actual_fixture_samples": actual_samples,
        "independent_arithmetic_cases": independent_cases, "controller_states": len(control_commands(document)),
        "native_reference_comparisons": len(native), "inputs_unchanged_during_run": True,
        "temporary_binaries_removed": True, "unreal_executed": False, "ios_executed": False,
        "skeletal_pose_evaluated": False, "contact_geometry_measured": False,
        "visual_quality_accepted": False,
        "preserved_contact_times_seconds": {c["id"]: c["time_seconds"] for c in document["contacts"]},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.report.resolve()),
                      "unit_checks": report["unit_checks"], "native_reference_comparisons": len(native),
                      "unreal_executed": False}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(error.stderr or error.stdout or str(error), file=sys.stderr)
        raise SystemExit(1)
