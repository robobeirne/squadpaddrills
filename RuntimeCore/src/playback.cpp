#include "squadpad/playback.hpp"

#include <algorithm>
#include <cmath>
#include <set>
#include <utility>

namespace squadpad::playback {
namespace {

void Require(bool condition, const std::string& message) {
  if (!condition) throw ValidationError(message);
}
void Finite(double value, const std::string& label) {
  Require(std::isfinite(value), label + " must be finite");
}
bool Close(double a, double b, double tolerance = kTimeTolerance) {
  return std::abs(a - b) <= tolerance;
}
void Identifier(const std::string& value, const std::string& label) {
  Require(!value.empty(), label + " must not be empty");
  for (unsigned char c : value) {
    Require((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.' || c == ':',
            label + " must be an ASCII identifier");
  }
}
void Unique(const std::string& id, std::set<std::string>& seen, const std::string& label) {
  Identifier(id, label);
  Require(seen.insert(id).second, "duplicate " + label + ": " + id);
}
void Vector(const Vec3& value, const std::string& label) {
  for (double x : value) Finite(x, label);
}
const Motion& FindMotion(const PlanInput& input, const std::string& id) {
  const auto found = std::find_if(input.motions.begin(), input.motions.end(),
                                [&](const Motion& value) { return value.id == id; });
  Require(found != input.motions.end(), "unknown source motion: " + id);
  return *found;
}
const Actor& FindActor(const PlanInput& input, const std::string& id) {
  const auto found = std::find_if(input.actors.begin(), input.actors.end(),
                                [&](const Actor& value) { return value.id == id; });
  Require(found != input.actors.end(), "unknown actor: " + id);
  return *found;
}
template <typename Key>
void ValidateKeys(const std::vector<Key>& keys, double duration, const std::string& label) {
  Require(keys.size() >= 2, label + " needs full-duration keys");
  Require(Close(keys.front().time_seconds, 0.0) &&
          Close(keys.back().time_seconds, duration), label + " must cover [0,duration]");
  double previous = -1.0;
  for (const auto& key : keys) {
    Finite(key.time_seconds, label + " time");
    Require(key.time_seconds >= 0.0 && key.time_seconds <= duration &&
            key.time_seconds > previous, label + " times must strictly increase inside the duration");
    Vector(key.position_cm, label + " position");
    previous = key.time_seconds;
  }
}
double NormalizeYaw(double yaw) {
  double result = std::fmod(yaw, 360.0);
  if (result < 0.0) result += 360.0;
  return result == 0.0 ? 0.0 : result;
}
double YawBetween(double left, double right, double factor) {
  // Normalize separately to avoid overflow for large finite input angles.
  const double a = NormalizeYaw(left);
  double delta = NormalizeYaw(right) - a;
  if (delta <= -180.0) delta += 360.0;
  if (delta > 180.0) delta -= 360.0;
  return NormalizeYaw(a + delta * factor);
}
double Interpolate(double left, double right, double factor) {
  // Weighted form avoids overflowing right-left when endpoints have opposite signs.
  return left * (1.0 - factor) + right * factor;
}
template <typename Key>
std::pair<std::size_t, double> Interval(const std::vector<Key>& keys, double seconds) {
  const auto upper = std::upper_bound(keys.begin(), keys.end(), seconds,
      [](double value, const Key& key) { return value < key.time_seconds; });
  if (upper == keys.begin()) return {0, 0.0};
  if (upper == keys.end()) return {keys.size() - 2, 1.0};
  const auto index = static_cast<std::size_t>(upper - keys.begin() - 1);
  return {index, (seconds - keys[index].time_seconds) /
                 (keys[index + 1].time_seconds - keys[index].time_seconds)};
}
template <typename Key>
Vec3 Position(const std::vector<Key>& keys, double seconds) {
  const auto [index, factor] = Interval(keys, seconds);
  Vec3 out{};
  for (std::size_t i = 0; i < out.size(); ++i)
    out[i] = Interpolate(keys[index].position_cm[i], keys[index + 1].position_cm[i], factor);
  return out;
}
std::vector<const PossessionKey*> PossessionFor(const PlanInput& input, const std::string& ball) {
  std::vector<const PossessionKey*> out;
  for (const auto& key : input.possession) if (key.ball_id == ball) out.push_back(&key);
  return out;
}
void Validate(PlanInput& input) {
  Require(input.schema == "squadpad.playback.v1", "unsupported playback schema");
  Require(input.distance_units == "centimetres" && input.time_units == "seconds" &&
          input.angle_units == "degrees", "playback units disagree");
  Require(input.axes == "unreal-z-up-x-forward", "unsupported world axes");
  Require(input.root_ownership == "actor_tracks_only", "root travel must have one explicit owner");
  Identifier(input.id, "plan ID");
  Finite(input.duration_seconds, "duration");
  Require(input.duration_seconds > 0.0, "duration must be positive");
  Require(!input.motions.empty() && !input.actors.empty() && !input.balls.empty(),
          "motions, actors and balls must be nonempty");
  std::set<std::string> motion_ids, actor_ids, ball_ids, contact_ids, section_ids;
  for (const auto& motion : input.motions) {
    Unique(motion.id, motion_ids, "motion ID");
    Finite(motion.duration_seconds, "motion duration");
    Require(motion.duration_seconds > 0.0, "motion duration must be positive");
  }
  for (const auto& actor : input.actors) {
    Unique(actor.id, actor_ids, "actor ID");
    Identifier(actor.appearance_slot, "appearance slot");
    ValidateKeys(actor.transform_keys, input.duration_seconds, "actor transform");
    for (const auto& key : actor.transform_keys) Finite(key.yaw_degrees, "actor yaw");
    Require(!actor.sections.empty(), "actor requires full-duration sections");
    double cursor = 0.0;
    for (const auto& section : actor.sections) {
      Unique(section.id, section_ids, "action ID");
      const auto& motion = FindMotion(input, section.source_motion_id);
      for (double value : {section.start_seconds, section.end_seconds,
                           section.source_start_seconds, section.play_rate}) Finite(value, "section value");
      Require(Close(section.start_seconds, cursor) && section.start_seconds >= 0.0 &&
              section.end_seconds > section.start_seconds && section.end_seconds <= input.duration_seconds,
              "sections must be contiguous and inside the timeline");
      Require(section.source_start_seconds >= 0.0 &&
              section.source_start_seconds < motion.duration_seconds && section.play_rate > 0.0,
              "invalid source offset or play rate");
      const double span = section.end_seconds - section.start_seconds;
      const double source_end = section.source_start_seconds + span * section.play_rate;
      Finite(source_end, "section source end");
      Require(section.loop || source_end <= motion.duration_seconds + kTimeTolerance,
              "nonlooping section overruns source motion");
      cursor = section.end_seconds;
    }
    Require(Close(cursor, input.duration_seconds), "sections do not cover the final endpoint");
  }
  for (const auto& ball : input.balls) {
    Unique(ball.id, ball_ids, "ball ID");
    Require(actor_ids.count(ball.id) == 0, "actor and ball IDs must be disjoint");
    Finite(ball.radius_cm, "ball radius");
    Require(ball.radius_cm > 0.0, "ball radius must be positive");
    ValidateKeys(ball.position_keys, input.duration_seconds, "ball position");
  }
  double previous_time = -1.0;
  std::string previous_ball;
  for (const auto& key : input.possession) {
    Finite(key.time_seconds, "possession time");
    Require(key.time_seconds >= 0.0 && key.time_seconds <= input.duration_seconds,
            "possession outside timeline");
    Require(key.time_seconds > previous_time ||
            (key.time_seconds == previous_time && key.ball_id > previous_ball),
            "possession must be ordered by time and ball ID");
    Require(ball_ids.count(key.ball_id) == 1, "possession references unknown ball");
    const auto& p = key.value;
    if (p.state == PossessionKind::Owned) {
      Require(actor_ids.count(p.owner_id) == 1 && p.from_actor_id.empty() && p.to_actor_id.empty(),
              "owned possession needs only a known owner");
    } else {
      Require(p.state == PossessionKind::InTransit && p.owner_id.empty() &&
              actor_ids.count(p.from_actor_id) == 1 && actor_ids.count(p.to_actor_id) == 1 &&
              p.from_actor_id != p.to_actor_id, "transit needs distinct known sender and receiver");
    }
    previous_time = key.time_seconds;
    previous_ball = key.ball_id;
  }
  double previous_contact_time = -1.0;
  std::string previous_contact_id;
  for (const auto& contact : input.contacts) {
    Unique(contact.id, contact_ids, "contact ID");
    Identifier(contact.limb, "contact limb");
    Require(contact.kind == "kick" || contact.kind == "receive", "unsupported contact kind");
    Require(contact.calibration_status == "unmeasured", "v1 baseline contact cannot claim calibration");
    Finite(contact.time_seconds, "contact time");
    Finite(contact.source_contact_seconds, "source contact time");
    Require(contact.time_seconds > previous_contact_time ||
            (contact.time_seconds == previous_contact_time && contact.id > previous_contact_id),
            "contacts must be ordered by time and ID");
    previous_contact_time = contact.time_seconds;
    previous_contact_id = contact.id;
    Require(contact.time_seconds >= 0.0 && contact.time_seconds <= input.duration_seconds &&
            ball_ids.count(contact.ball_id) == 1, "invalid contact time or ball");
    const auto& actor = FindActor(input, contact.actor_id);
    const auto found = std::find_if(actor.sections.begin(), actor.sections.end(),
        [&](const Section& s) { return s.id == contact.action_id; });
    Require(found != actor.sections.end(), "contact references unknown actor action");
    const auto& section = *found;
    const auto& motion = FindMotion(input, contact.source_motion_id);
    Require(section.source_motion_id == contact.source_motion_id && !section.loop,
            "contact needs its nonlooping action motion");
    Require(contact.time_seconds >= section.start_seconds && contact.time_seconds <= section.end_seconds &&
            contact.source_contact_seconds >= 0.0 && contact.source_contact_seconds <= motion.duration_seconds,
            "contact outside action or source motion");
    const Section* active = &actor.sections.front();
    for (const auto& candidate : actor.sections) {
      if (candidate.start_seconds > contact.time_seconds) break;
      active = &candidate;
    }
    Require(active->id == section.id, "contact action is not active at its timeline instant");
    const double expected = section.source_start_seconds +
                            (contact.time_seconds - section.start_seconds) * section.play_rate;
    Require(Close(contact.source_contact_seconds, expected, kContactTimeTolerance),
            "contact source and timeline clocks disagree");
  }
  std::set<std::string> matched_contacts;
  for (const auto& ball : input.balls) {
    const auto keys = PossessionFor(input, ball.id);
    Require(!keys.empty() && keys.front()->time_seconds == 0.0,
            "each ball needs possession at time zero");
    for (std::size_t i = 1; i < keys.size(); ++i) {
      const auto& before = keys[i - 1]->value;
      const auto& after = keys[i]->value;
      Require(keys[i]->time_seconds > keys[i - 1]->time_seconds,
              "per-ball possession times must increase");
      std::string kind, actor;
      if (before.state == PossessionKind::Owned && after.state == PossessionKind::InTransit) {
        Require(before.owner_id == after.from_actor_id, "kick sender differs from previous owner");
        kind = "kick"; actor = after.from_actor_id;
      } else {
        Require(before.state == PossessionKind::InTransit && after.state == PossessionKind::Owned &&
                before.to_actor_id == after.owner_id, "unsupported possession transition");
        kind = "receive"; actor = after.owner_id;
      }
      std::vector<const Contact*> matches;
      for (const auto& contact : input.contacts)
        if (contact.ball_id == ball.id && contact.kind == kind && contact.actor_id == actor &&
            Close(contact.time_seconds, keys[i]->time_seconds, kContactTimeTolerance)) matches.push_back(&contact);
      Require(matches.size() == 1, "possession transition needs exactly one matching contact");
      Require(matched_contacts.insert(matches.front()->id).second, "contact reused for multiple transitions");
    }
  }
  Require(matched_contacts.size() == input.contacts.size(), "contact lacks a matching possession transition");
}

}  // namespace

PlaybackPlan::PlaybackPlan(PlanInput input) : input_(std::move(input)) {}
PlaybackPlan PlaybackPlan::Create(PlanInput input) {
  Validate(input);
  return PlaybackPlan(std::move(input));
}
FrameState PlaybackPlan::Evaluate(double seconds) const {
  Finite(seconds, "sample time");
  const double time = std::clamp(seconds, 0.0, input_.duration_seconds);
  FrameState frame{time, {}, {}};
  frame.actors.reserve(input_.actors.size());
  frame.balls.reserve(input_.balls.size());
  for (const auto& actor : input_.actors) {
    const auto [index, factor] = Interval(actor.transform_keys, time);
    const Section* active = &actor.sections.front();
    for (const auto& section : actor.sections) {
      if (section.start_seconds > time) break;
      active = &section;
    }
    const auto& motion = FindMotion(input_, active->source_motion_id);
    double source_time = active->source_start_seconds +
                         std::clamp(time - active->start_seconds, 0.0,
                                    active->end_seconds - active->start_seconds) * active->play_rate;
    source_time = active->loop ? std::fmod(source_time, motion.duration_seconds)
                               : std::min(source_time, motion.duration_seconds);
    frame.actors.push_back({actor.id, actor.appearance_slot, Position(actor.transform_keys, time),
        YawBetween(actor.transform_keys[index].yaw_degrees, actor.transform_keys[index + 1].yaw_degrees, factor),
        active->id, active->source_motion_id, source_time});
  }
  for (const auto& ball : input_.balls) {
    const auto keys = PossessionFor(input_, ball.id);
    const PossessionKey* active = keys.front();
    for (const auto* key : keys) {
      if (key->time_seconds > time) break;
      active = key;
    }
    frame.balls.push_back({ball.id, ball.radius_cm, Position(ball.position_keys, time), active->value});
  }
  return frame;
}
std::vector<std::string> PlaybackPlan::ContactsCrossed(double previous, double current) const {
  Finite(previous, "previous time"); Finite(current, "current time");
  Require(previous >= 0.0 && current >= previous && current <= input_.duration_seconds,
          "contact interval must advance inside the timeline");
  std::vector<std::string> out;
  for (const auto& contact : input_.contacts)
    if (contact.time_seconds > previous && contact.time_seconds <= current) out.push_back(contact.id);
  return out;
}

PlaybackController::PlaybackController(PlaybackPlan plan) : plan_(std::move(plan)) {}
void PlaybackController::Load(PlaybackPlan plan) {
  plan_ = std::move(plan);
  time_seconds_ = 0.0; rate_ = 1.0; playing_ = false;
}
void PlaybackController::Play() noexcept { playing_ = time_seconds_ < plan_.DurationSeconds(); }
StepResult PlaybackController::Seek(double seconds) {
  Finite(seconds, "seek time");
  time_seconds_ = std::clamp(seconds, 0.0, plan_.DurationSeconds());
  if (time_seconds_ >= plan_.DurationSeconds()) playing_ = false;
  return {CurrentFrame(), {}};
}
StepResult PlaybackController::Restart() {
  time_seconds_ = 0.0; playing_ = false;
  return {CurrentFrame(), {}};
}
void PlaybackController::SetRate(double rate) {
  Finite(rate, "playback rate");
  Require(rate > 0.0, "playback rate must be positive");
  rate_ = rate;
}
StepResult PlaybackController::Advance(double delta_seconds) {
  Finite(delta_seconds, "advance delta");
  Require(delta_seconds >= 0.0, "advance delta must be nonnegative");
  if (!playing_ || delta_seconds == 0.0) return {CurrentFrame(), {}};
  const double previous = time_seconds_;
  const double remaining = plan_.DurationSeconds() - previous;
  if (delta_seconds >= remaining / rate_) time_seconds_ = plan_.DurationSeconds();
  else time_seconds_ += delta_seconds * rate_;
  if (time_seconds_ >= plan_.DurationSeconds()) playing_ = false;
  return {CurrentFrame(), plan_.ContactsCrossed(previous, time_seconds_)};
}

}  // namespace squadpad::playback
