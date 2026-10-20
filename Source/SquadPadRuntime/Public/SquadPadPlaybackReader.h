#pragma once

#include "CoreMinimal.h"
#include "squadpad/playback.hpp"

namespace SquadPadRuntime
{

// Runtime ingress limits, not measured phone resource budgets. The portable v1
// data contract remains unchanged. Larger payloads require an explicit profile.
inline constexpr int32 MaximumPlaybackJsonCharacters = 4 * 1024 * 1024;
inline constexpr int32 MaximumPlaybackJsonDepth = 32;
inline constexpr int32 MaximumPlaybackJsonTokens = 500000;
inline constexpr int32 MaximumPlaybackActors = 64;
inline constexpr int32 MaximumPlaybackBalls = 32;
inline constexpr int32 MaximumPlaybackMotions = 1024;
inline constexpr int32 MaximumPlaybackTotalKeys = 65536;
inline constexpr int32 MaximumPlaybackTotalSections = 4096;
inline constexpr int32 MaximumPlaybackContacts = 2048;
inline constexpr int32 MaximumPlaybackPossessionRecords = 4096;

// Uses Unreal's JSON reader/serializer, then the portable semantic validator.
// Duplicate object members are rejected before DOM construction, including
// escaped aliases and nested metadata. JSON null alone maps to an empty optional
// possession ID. Contract field names and identifiers remain case-sensitive.
// Only root provenance/evidence objects allow arbitrary metadata member names.
//
// On failure OutInput is untouched and OutError describes the failure. Success
// replaces OutInput and clears OutError. This does not load or certify assets.
SQUADPADRUNTIME_API bool ParsePlaybackJson(
    const FString& Json,
    squadpad::playback::PlanInput& OutInput,
    FString& OutError);

} // namespace SquadPadRuntime
