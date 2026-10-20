#include "squadpad/playback.hpp"

#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <string>

using namespace squadpad::playback;
namespace {
int checks = 0;
void Check(bool value, const std::string& name) {
  ++checks;
  if (!value) throw std::runtime_error("check failed: " + name);
}
void Near(double actual, double expected, const std::string& name, double tolerance = 1e-9) {
  Check(std::isfinite(actual) && std::abs(actual - expected) <= tolerance, name);
}
void Reject(const std::function<void()>& function, const std::string& name) {
  bool rejected = false;
  try { function(); } catch (const ValidationError&) { rejected = true; }
  Check(rejected, name);
}
PlanInput Fixture() {
  PlanInput in{};
  in.schema = "squadpad.playback.v1";
  in.distance_units = "centimetres";
  in.time_units = "seconds";
  in.angle_units = "degrees";
  in.axes = "unreal-z-up-x-forward";
  in.root_ownership = "actor_tracks_only";
  in.id = "independent.two-actor.fixture";
  in.duration_seconds = 4.0;
  in.motions = {{"pass", 3.0}, {"receive", 2.0}, {"idle", 1.0}};
  in.actors = {
    {"A", "athlete-slot", {{0, {0, 0, 0}, 350}, {4, {400, 80, 0}, 10}},
      {{"A.pass", "pass", 0, 3, 0, 1, false}, {"A.idle", "idle", 3, 4, 0, 1, true}}},
    {"B", "athlete-slot", {{0, {100, 100, 0}, 180}, {4, {100, 100, 0}, 180}},
      {{"B.idle.pre", "idle", 0, 1, 0, 1, true},
       {"B.receive", "receive", 1, 3, 0, 1, false},
       {"B.idle.post", "idle", 3, 4, 0, 1, true}}}
  };
  in.balls = {{"ball", 11, {{0, {0, 0, 11}}, {1, {0, 0, 11}},
                           {2, {100, 100, 11}}, {4, {100, 100, 11}}}}};
  in.possession = {
    {0, "ball", {PossessionKind::Owned, "A", "", ""}},
    {1, "ball", {PossessionKind::InTransit, "", "A", "B"}},
    {2, "ball", {PossessionKind::Owned, "B", "", ""}}
  };
  in.contacts = {
    {"kick", "kick", 1, "A", "ball", "A.pass", "pass", 1, "right_foot", "unmeasured"},
    {"receive", "receive", 2, "B", "ball", "B.receive", "receive", 1, "right_foot", "unmeasured"}
  };
  return in;
}
void FrameChecks() {
  auto input = Fixture();
  const auto plan = PlaybackPlan::Create(input);
  input.actors[0].transform_keys[0].position_cm[0] = 9000;
  Near(plan.Evaluate(0).actors[0].position_cm[0], 0, "input copied into immutable plan");
  Near(plan.Evaluate(-10).time_seconds, 0, "negative query clamps");
  Near(plan.Evaluate(40).time_seconds, 4, "high query clamps");
  const auto mid = plan.Evaluate(2);
  Near(mid.actors[0].position_cm[0], 200, "world position uses one track");
  Near(mid.actors[0].position_cm[1], 40, "linear position");
  Near(mid.actors[0].yaw_degrees, 0, "shortest yaw crosses zero");
  Near(plan.Evaluate(1.5).balls[0].position_cm[0], 50, "ball interpolation");
  Near(plan.Evaluate(1.5).actors[1].source_time_seconds, 0.5, "source offset from action start");
  Check(plan.Evaluate(1 - 1e-10).balls[0].possession.owner_id == "A", "before release owner");
  Check(plan.Evaluate(1).balls[0].possession.state == PossessionKind::InTransit, "exact release transit");
  Check(plan.Evaluate(2).balls[0].possession.owner_id == "B", "exact receive owner");
  Check(plan.Evaluate(3).actors[0].action_id == "A.idle", "half-open action boundary");
  Near(plan.Evaluate(4).actors[0].source_time_seconds, 0, "loop endpoint uses modulo");
  for (double time : {3.75, 0.5, 2.0, 1.0, 4.0, 0.0})
    Near(plan.Evaluate(time).actors[0].position_cm[0], time * 100, "history-free reverse/random query");
  auto half = Fixture();
  half.actors[0].transform_keys[0].yaw_degrees = 10;
  half.actors[0].transform_keys[1].yaw_degrees = 190;
  Near(PlaybackPlan::Create(half).Evaluate(2).actors[0].yaw_degrees, 100, "positive 180 tie");
  half.actors[0].transform_keys[0].yaw_degrees = 190;
  half.actors[0].transform_keys[1].yaw_degrees = 10;
  Near(PlaybackPlan::Create(half).Evaluate(2).actors[0].yaw_degrees, 280, "negative 180 tie resolves positive");
  half.actors[0].transform_keys[0].yaw_degrees = 1e308;
  half.actors[0].transform_keys[1].yaw_degrees = -1e308;
  Check(std::isfinite(PlaybackPlan::Create(half).Evaluate(2).actors[0].yaw_degrees), "large finite yaw safe");
  half.actors[0].transform_keys[0].position_cm[0] = -1e308;
  half.actors[0].transform_keys[1].position_cm[0] = 1e308;
  Near(PlaybackPlan::Create(half).Evaluate(2).actors[0].position_cm[0], 0, "opposite finite positions avoid delta overflow");
  auto nonloop = Fixture();
  nonloop.actors[0].sections.back().loop = false;
  Near(PlaybackPlan::Create(nonloop).Evaluate(4).actors[0].source_time_seconds, 1, "nonloop endpoint holds final pose");
}
void ControlChecks() {
  const auto plan = PlaybackPlan::Create(Fixture());
  PlaybackController control(plan);
  Near(control.Advance(1).frame.time_seconds, 0, "starts paused");
  control.Play();
  control.SetRate(2);
  auto first = control.Advance(0.5);
  Check(first.crossed_contact_ids == std::vector<std::string>{"kick"}, "advance includes new endpoint");
  Check(control.Advance(0).crossed_contact_ids.empty(), "zero advance no duplicate event");
  control.Pause();
  Near(control.Advance(100).frame.time_seconds, 1, "pause holds");
  Check(control.Seek(2).crossed_contact_ids.empty(), "seek skips effects");
  Check(!control.IsPlaying(), "seek preserves paused state");
  control.Seek(0);
  control.Play();
  auto replay = control.Advance(1);
  Check(replay.crossed_contact_ids == std::vector<std::string>({"kick", "receive"}), "new traversal re-emits contacts");
  auto end = control.Advance(1000);
  Near(end.frame.time_seconds, 4, "advance clamps at end");
  Check(!control.IsPlaying(), "end pauses");
  control.Play();
  Check(!control.IsPlaying(), "play at end stays paused");
  Check(control.Restart().crossed_contact_ids.empty(), "restart no effects");
  Near(control.TimeSeconds(), 0, "restart time zero");
  Check(!control.IsPlaying(), "restart pauses");
  Near(control.Rate(), 2, "restart preserves selected rate");
  control.Play();
  control.Seek(1.5);
  Check(control.IsPlaying(), "seek preserves playing state below end");
  control.Seek(4);
  Check(!control.IsPlaying(), "seek to end pauses");
  control.Restart(); control.Play();
  control.SetRate(std::numeric_limits<double>::max());
  Near(control.Advance(std::numeric_limits<double>::max()).frame.time_seconds, 4, "advance caps without multiply overflow");
  control.Load(plan);
  Check(!control.IsPlaying() && control.TimeSeconds() == 0 && control.Rate() == 1, "load resets controls");
  for (double dt : {1.0 / 30.0, 1.0 / 60.0}) {
    PlaybackController stepped(plan); stepped.Play();
    while (stepped.TimeSeconds() + dt < 2.5) stepped.Advance(dt);
    stepped.Advance(2.5 - stepped.TimeSeconds());
    Near(stepped.CurrentFrame().actors[0].position_cm[0], 250, "30/60 Hz state agrees with direct sample");
  }
}
void InvalidChecks() {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double inf = std::numeric_limits<double>::infinity();
  const auto bad = [&](const std::function<void(PlanInput&)>& change, const std::string& name) {
    auto in = Fixture(); change(in); Reject([&] { PlaybackPlan::Create(in); }, name);
  };
  bad([&](auto& in) { in.duration_seconds = nan; }, "NaN duration");
  bad([](auto& in) { in.schema = "v2"; }, "unknown schema");
  bad([](auto& in) { in.distance_units = "metres"; }, "unknown units");
  bad([](auto& in) { in.axes = "y-up"; }, "unknown axes");
  bad([](auto& in) { in.root_ownership = "both"; }, "ambiguous root ownership");
  bad([](auto& in) { in.id = "bad/id"; }, "path-like ID");
  bad([](auto& in) { in.motions.push_back(in.motions[0]); }, "duplicate motion");
  bad([](auto& in) { in.actors.push_back(in.actors[0]); }, "duplicate actor");
  bad([](auto& in) { in.balls[0].id = "A"; }, "object ID collision");
  bad([](auto& in) { in.balls[0].radius_cm = 0; }, "zero ball radius");
  bad([&](auto& in) { in.actors[0].transform_keys[0].position_cm[0] = inf; }, "infinite position");
  bad([](auto& in) { in.actors[0].transform_keys[1].time_seconds = 0; }, "duplicate transform time");
  bad([](auto& in) { in.actors[0].transform_keys.pop_back(); }, "incomplete transform coverage");
  bad([](auto& in) { in.actors[0].sections[1].start_seconds = 3.1; }, "section gap");
  bad([](auto& in) { in.actors[0].sections[1].start_seconds = 2.9; }, "section overlap");
  bad([](auto& in) { in.actors[0].sections[1].id = "A.pass"; }, "duplicate action");
  bad([](auto& in) { in.actors[0].sections[0].source_motion_id = "missing"; }, "unknown motion");
  bad([](auto& in) { in.actors[0].sections[0].play_rate = 2; }, "source overrun");
  bad([](auto& in) { in.actors[0].sections[0].source_start_seconds = -1; }, "negative source offset");
  bad([](auto& in) { in.possession.erase(in.possession.begin()); }, "missing initial possession");
  bad([](auto& in) { in.possession[1].value.owner_id = "A"; }, "transit has owner");
  bad([](auto& in) { in.possession[1].value.to_actor_id = "A"; }, "self pass");
  bad([](auto& in) { in.possession[2].value.owner_id = "A"; }, "receiver mismatch");
  bad([](auto& in) { in.possession[1].value.state = static_cast<PossessionKind>(99); }, "invalid typed enum");
  bad([](auto& in) { in.contacts[0].source_contact_seconds = 1.1; }, "contact clock mismatch");
  bad([](auto& in) { in.contacts[0].action_id = "B.receive"; }, "contact wrong actor action");
  bad([](auto& in) { in.contacts[0].calibration_status = "measured"; }, "false calibration claim");
  bad([](auto& in) { in.contacts.pop_back(); }, "missing transition contact");
  bad([](auto& in) { std::swap(in.contacts[0], in.contacts[1]); }, "contact ordering");
  bad([](auto& in) { in.actors[0].sections[0].loop = true; }, "loop contact ambiguity");
  bad([](auto& in) {
    in.actors[0].sections[0].end_seconds = 1;
    in.actors[0].sections[1].start_seconds = 1;
  }, "contact on outgoing nonfinal endpoint");
  auto plan = PlaybackPlan::Create(Fixture());
  Reject([&] { plan.Evaluate(nan); }, "NaN sample time");
  Reject([&] { plan.Evaluate(inf); }, "infinite sample time");
  Reject([&] { plan.ContactsCrossed(2, 1); }, "reversed event interval");
  PlaybackController control(plan);
  Reject([&] { control.SetRate(0); }, "zero controller rate");
  Reject([&] { control.SetRate(-1); }, "negative controller rate");
  Reject([&] { control.SetRate(inf); }, "infinite controller rate");
  Reject([&] { control.Advance(-1); }, "negative delta");
  Reject([&] { control.Advance(nan); }, "NaN delta");
  Reject([&] { control.Seek(inf); }, "infinite seek");
}
}  // namespace
int main() {
  try {
    FrameChecks(); ControlChecks(); InvalidChecks();
    std::cout << "{\"status\":\"passed\",\"checks\":" << checks
              << ",\"scope\":\"portable_cpp17_only\",\"unreal_executed\":false}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
