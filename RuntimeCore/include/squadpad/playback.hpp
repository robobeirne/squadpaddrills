#pragma once

// Portable SquadPad playback, C++17. No Unreal, JSON, rendering or physics dependency.
// A validated PlaybackPlan owns a private copy of its inputs. Evaluation has no effects.

#include <array>
#include <stdexcept>
#include <string>
#include <vector>

namespace squadpad::playback {

constexpr double kTimeTolerance = 1e-8;
constexpr double kContactTimeTolerance = 1e-7;
using Vec3 = std::array<double, 3>;

class ValidationError : public std::invalid_argument {
 public:
  using std::invalid_argument::invalid_argument;
};

struct Motion { std::string id; double duration_seconds = 0.0; };
struct PositionKey { double time_seconds = 0.0; Vec3 position_cm{}; };
struct TransformKey { double time_seconds = 0.0; Vec3 position_cm{}; double yaw_degrees = 0.0; };
struct Section {
  std::string id;
  std::string source_motion_id;
  double start_seconds = 0.0;
  double end_seconds = 0.0;
  double source_start_seconds = 0.0;
  double play_rate = 0.0;
  bool loop = false;
};
struct Actor {
  std::string id;
  std::string appearance_slot;
  std::vector<TransformKey> transform_keys;
  std::vector<Section> sections;
};
struct Ball {
  std::string id;
  double radius_cm = 0.0;
  std::vector<PositionKey> position_keys;
};
enum class PossessionKind { Owned, InTransit };
struct Possession {
  PossessionKind state = PossessionKind::Owned;
  // Empty identifiers encode JSON null for the optional owner/from/to fields.
  std::string owner_id;
  std::string from_actor_id;
  std::string to_actor_id;
};
struct PossessionKey {
  double time_seconds = 0.0;
  std::string ball_id;
  Possession value;
};
struct Contact {
  std::string id;
  std::string kind;
  double time_seconds = 0.0;
  std::string actor_id;
  std::string ball_id;
  std::string action_id;
  std::string source_motion_id;
  double source_contact_seconds = 0.0;
  std::string limb;
  std::string calibration_status;
};
struct PlanInput {
  std::string schema;
  std::string distance_units;
  std::string time_units;
  std::string angle_units;
  std::string axes;
  std::string root_ownership;
  std::string id;
  double duration_seconds = 0.0;
  std::vector<Motion> motions;
  std::vector<Actor> actors;
  std::vector<Ball> balls;
  std::vector<PossessionKey> possession;
  std::vector<Contact> contacts;
};
struct ActorState {
  std::string id;
  std::string appearance_slot;
  Vec3 position_cm;
  double yaw_degrees;
  std::string action_id;
  std::string source_motion_id;
  double source_time_seconds;
};
struct BallState {
  std::string id;
  double radius_cm;
  Vec3 position_cm;
  Possession possession;
};
struct FrameState {
  double time_seconds;
  std::vector<ActorState> actors;
  std::vector<BallState> balls;
};

class PlaybackPlan {
 public:
  // Throws ValidationError before exposing a plan with inconsistent typed data.
  static PlaybackPlan Create(PlanInput input);
  FrameState Evaluate(double seconds) const;
  double DurationSeconds() const noexcept { return input_.duration_seconds; }
  const std::string& Id() const noexcept { return input_.id; }
  const PlanInput& Data() const noexcept { return input_; }
  std::vector<std::string> ContactsCrossed(double previous, double current) const;

 private:
  explicit PlaybackPlan(PlanInput input);
  PlanInput input_;
};

struct StepResult {
  FrameState frame;
  std::vector<std::string> crossed_contact_ids;
};

class PlaybackController {
 public:
  explicit PlaybackController(PlaybackPlan plan);
  void Load(PlaybackPlan plan);
  void Play() noexcept;
  void Pause() noexcept { playing_ = false; }
  StepResult Seek(double seconds);
  StepResult Restart();
  void SetRate(double rate);
  StepResult Advance(double delta_seconds);
  FrameState CurrentFrame() const { return plan_.Evaluate(time_seconds_); }
  bool IsPlaying() const noexcept { return playing_; }
  double TimeSeconds() const noexcept { return time_seconds_; }
  double Rate() const noexcept { return rate_; }
  const PlaybackPlan& Plan() const noexcept { return plan_; }

 private:
  PlaybackPlan plan_;
  double time_seconds_ = 0.0;
  double rate_ = 1.0;
  bool playing_ = false;
};

}  // namespace squadpad::playback
