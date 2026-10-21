#include "SquadPadPlaybackActor.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/SceneComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "SquadPadPlaybackReader.h"
#include "Templates/UnrealTemplate.h"

#include <cmath>
#include <map>
#include <set>
#include <utility>

namespace
{
// Matches the existing native FBX import gate. Source clocks are never rescaled.
constexpr double ImportedDurationToleranceSeconds = 1.0 / 60.0 + 1e-6;
constexpr double MaximumPositionCm = 1000000.0;
constexpr double MaximumBallRadiusCm = 10000.0;
constexpr int32 MaximumActors = 64;
constexpr int32 MaximumBalls = 16;

FString Text(const std::string& Value)
{
    return UTF8_TO_TCHAR(Value.c_str());
}

std::string Identifier(const FString& Value)
{
    return std::string(TCHAR_TO_UTF8(*Value));
}

bool IsIdentifier(const FString& Value)
{
    if (Value.IsEmpty()) return false;
    for (TCHAR Character : Value)
    {
        if (!((Character >= TEXT('A') && Character <= TEXT('Z')) ||
              (Character >= TEXT('a') && Character <= TEXT('z')) ||
              (Character >= TEXT('0') && Character <= TEXT('9')) ||
              Character == TEXT('_') || Character == TEXT('-') ||
              Character == TEXT('.') || Character == TEXT(':')))
        {
            return false;
        }
    }
    return true;
}

bool Reject(FString& Error, const FString& Message)
{
    Error = Message;
    return false;
}

void DestroyVisuals(TArray<FSquadPadActorVisual>& Actors, TArray<FSquadPadBallVisual>& Balls)
{
    for (FSquadPadActorVisual& Visual : Actors)
    {
        if (IsValid(Visual.Component)) Visual.Component->DestroyComponent();
    }
    for (FSquadPadBallVisual& Visual : Balls)
    {
        if (IsValid(Visual.Component)) Visual.Component->DestroyComponent();
    }
    Actors.Reset();
    Balls.Reset();
}

const FSquadPadAthleteAssetProfile* ProfileForActor(
    const USquadPadPlaybackDataAsset& Data, const squadpad::playback::Actor& Actor)
{
    const FString Slot = Text(Actor.appearance_slot);
    const FSquadPadAppearanceAssetBinding* Binding = Data.AppearanceBindings.FindByPredicate(
        [&](const FSquadPadAppearanceAssetBinding& Item)
        { return Item.AppearanceSlot.Equals(Slot, ESearchCase::CaseSensitive); });
    if (!Binding) return nullptr;
    return Data.Profiles.FindByPredicate([&](const FSquadPadAthleteAssetProfile& Item)
        { return Item.ProfileId.Equals(Binding->ProfileId, ESearchCase::CaseSensitive); });
}

bool ValidateNativeBindings(const USquadPadPlaybackDataAsset& Data,
                            const squadpad::playback::PlanInput& Input, FString& Error)
{
    if (Input.actors.size() > static_cast<std::size_t>(MaximumActors) ||
        Input.balls.size() > static_cast<std::size_t>(MaximumBalls))
        return Reject(Error, TEXT("Native probe supports at most 64 actors and 16 balls."));

    std::map<std::string, double> Durations;
    for (const auto& Motion : Input.motions) Durations.emplace(Motion.id, Motion.duration_seconds);
    std::set<std::string> RequiredSlots;
    for (const auto& Actor : Input.actors)
    {
        RequiredSlots.insert(Actor.appearance_slot);
        for (const auto& Key : Actor.transform_keys)
            for (const double Coordinate : Key.position_cm)
                if (std::abs(Coordinate) > MaximumPositionCm)
                    return Reject(Error, TEXT("Actor position exceeds the native probe's 10 km coordinate bound."));
    }
    for (const auto& Ball : Input.balls)
    {
        if (Ball.radius_cm > MaximumBallRadiusCm)
            return Reject(Error, TEXT("Ball radius exceeds the native probe's 100 m bound."));
        for (const auto& Key : Ball.position_keys)
            for (const double Coordinate : Key.position_cm)
                if (std::abs(Coordinate) > MaximumPositionCm)
                    return Reject(Error, TEXT("Ball position exceeds the native probe's 10 km coordinate bound."));
    }

    std::map<std::string, const FSquadPadAthleteAssetProfile*> Profiles;
    for (const auto& Profile : Data.Profiles)
    {
        if (!IsIdentifier(Profile.ProfileId) ||
            !Profiles.emplace(Identifier(Profile.ProfileId), &Profile).second)
            return Reject(Error, TEXT("Native profile IDs must be valid and unique, including letter case."));
        if (!IsValid(Profile.Mesh) || !IsValid(Profile.Mesh->GetSkeleton()))
            return Reject(Error, TEXT("Native athlete profile requires a mesh with a Skeleton."));
        if (Profile.Mesh->PostProcessAnimBlueprint)
            return Reject(Error, TEXT("The direct-clip baseline does not support a mesh requiring a post-process animation Blueprint."));
    }

    std::map<std::string, std::string> SlotProfiles;
    for (const auto& Binding : Data.AppearanceBindings)
    {
        if (!IsIdentifier(Binding.AppearanceSlot) || !IsIdentifier(Binding.ProfileId))
            return Reject(Error, TEXT("Native appearance bindings contain an invalid identifier."));
        const auto Slot = Identifier(Binding.AppearanceSlot);
        const auto Profile = Identifier(Binding.ProfileId);
        if (!RequiredSlots.count(Slot) || !Profiles.count(Profile) ||
            !SlotProfiles.emplace(Slot, Profile).second)
            return Reject(Error, TEXT("Native appearance bindings require exact, unique slot coverage and known profiles."));
    }
    if (SlotProfiles.size() != RequiredSlots.size())
        return Reject(Error, TEXT("A required appearance slot has no native asset binding."));

    std::map<std::string, std::set<std::string>> RequiredMotions;
    for (const auto& Actor : Input.actors)
    {
        auto& Required = RequiredMotions[SlotProfiles.at(Actor.appearance_slot)];
        for (const auto& Section : Actor.sections) Required.insert(Section.source_motion_id);
    }
    if (RequiredMotions.size() != Profiles.size())
        return Reject(Error, TEXT("Native data contains an unused athlete profile."));

    for (const auto& Pair : Profiles)
    {
        const auto& Profile = *Pair.second;
        const auto& Required = RequiredMotions.at(Pair.first);
        std::set<std::string> Seen;
        for (const auto& Binding : Profile.Motions)
        {
            if (!IsIdentifier(Binding.SourceMotionId))
                return Reject(Error, TEXT("A native motion binding has an invalid source ID."));
            const auto MotionId = Identifier(Binding.SourceMotionId);
            if (!Required.count(MotionId) || !Seen.insert(MotionId).second)
                return Reject(Error, TEXT("Native motion bindings require exact unique coverage for their profile."));
            UAnimSequence* Animation = Binding.Animation.Get();
            if (!IsValid(Animation) || Animation->GetSkeleton() != Profile.Mesh->GetSkeleton())
                return Reject(Error, TEXT("A native animation is absent or uses a different Skeleton from its mesh."));
            const double NativeDuration = Animation->GetPlayLength();
            if (!std::isfinite(NativeDuration) || NativeDuration <= 0.0 ||
                std::abs(NativeDuration - Durations.at(MotionId)) > ImportedDurationToleranceSeconds)
                return Reject(Error, TEXT("Native animation duration differs from its canonical source clock beyond the import tolerance."));
            if (Animation->bEnableRootMotion || Animation->bForceRootLock ||
                !FMath::IsNearlyEqual(Animation->RateScale, 1.0f, 1e-6f))
                return Reject(Error, TEXT("Native baseline animation requires root extraction off, force root lock off and rate scale 1."));
            if (Animation->GetAdditiveAnimType() != AAT_None)
                return Reject(Error, TEXT("The direct-clip baseline requires non-additive animations."));
        }
        if (Seen != Required)
            return Reject(Error, TEXT("A required source performance has no native animation binding."));
    }
    if (!IsValid(Data.BallMesh) || !FMath::IsFinite(Data.BallMeshRadiusCm) ||
        Data.BallMeshRadiusCm <= 0.0f || Data.BallMeshRadiusCm > MaximumPositionCm)
        return Reject(Error, TEXT("Native ball presentation requires a mesh and a finite positive mesh radius."));
    const FBoxSphereBounds Bounds = Data.BallMesh->GetBounds();
    if (!Bounds.Origin.IsNearlyZero(0.05) ||
        !FMath::IsNearlyEqual(Bounds.BoxExtent.X, static_cast<double>(Data.BallMeshRadiusCm), 0.05) ||
        !FMath::IsNearlyEqual(Bounds.BoxExtent.Y, static_cast<double>(Data.BallMeshRadiusCm), 0.05) ||
        !FMath::IsNearlyEqual(Bounds.BoxExtent.Z, static_cast<double>(Data.BallMeshRadiusCm), 0.05))
        return Reject(Error, TEXT("Ball mesh must be centred at its origin with bounds matching the declared radius within 0.05 cm."));
    return true;
}
} // namespace

ASquadPadPlaybackActor::ASquadPadPlaybackActor()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.bStartWithTickEnabled = false;
    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SquadPadRoot"));
    SceneRoot->SetMobility(EComponentMobility::Movable);
    SetRootComponent(SceneRoot);
}

ASquadPadPlaybackActor::~ASquadPadPlaybackActor() = default;

void ASquadPadPlaybackActor::BeginPlay()
{
    Super::BeginPlay();
    if (bAutoLoadOnBeginPlay && LoadPlayback(PlaybackData) && bAutoPlay) Play();
}

void ASquadPadPlaybackActor::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    ClearPlaybackInternal();
    Super::EndPlay(EndPlayReason);
}

void ASquadPadPlaybackActor::Destroyed()
{
    ClearPlaybackInternal();
    Super::Destroyed();
}

bool ASquadPadPlaybackActor::CheckCommandEntry()
{
    if (!ensureMsgf(IsInGameThread(), TEXT("SquadPad playback requires the game thread."))) return false;
    if (bCommandExecuting || IsActorBeingDestroyed()) return false;
    return true;
}

bool ASquadPadPlaybackActor::RequireLoaded()
{
    return Controller ? true : Fail(TEXT("No playback is loaded."));
}

bool ASquadPadPlaybackActor::Fail(const FString& Message)
{
    LastError = Message;
    OnError.Broadcast(Message);
    return false;
}

void ASquadPadPlaybackActor::ClearPending()
{
    DestroyVisuals(PendingActorVisuals, PendingBallVisuals);
    PendingData = nullptr;
}

void ASquadPadPlaybackActor::ClearPlaybackInternal()
{
    SetActorTickEnabled(false);
    Controller.Reset();
    DestroyVisuals(ActorVisuals, BallVisuals);
    LoadedData = nullptr;
    ClearPending();
}

void ASquadPadPlaybackActor::UnloadPlayback()
{
    if (!CheckCommandEntry()) return;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    ClearPlaybackInternal();
    LastError.Reset();
}

bool ASquadPadPlaybackActor::LoadPlayback(USquadPadPlaybackDataAsset* InData)
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!IsValid(InData) || !GetWorld()) return Fail(TEXT("Loading requires a native data asset and a world."));
    if (!GetActorTransform().Equals(FTransform::Identity, 1e-8))
        return Fail(TEXT("Place the playback actor at the identity transform to preserve the world-space contract."));

    squadpad::playback::PlanInput Input;
    FString Error;
    if (!SquadPadRuntime::ParsePlaybackJson(InData->PlaybackJson, Input, Error)) return Fail(Error);
    if (!ValidateNativeBindings(*InData, Input, Error)) return Fail(Error);

    TUniquePtr<squadpad::playback::PlaybackController> NewController;
    try
    {
        NewController = MakeUnique<squadpad::playback::PlaybackController>(
            squadpad::playback::PlaybackPlan::Create(Input));
    }
    catch (const std::exception& Exception)
    {
        return Fail(UTF8_TO_TCHAR(Exception.what()));
    }

    ClearPending();
    PendingData = InData;
    for (const auto& Actor : Input.actors)
    {
        const auto* Profile = ProfileForActor(*InData, Actor);
        if (!Profile)
        {
            ClearPending();
            return Fail(TEXT("Validated native appearance profile is no longer available."));
        }
        FSquadPadActorVisual& Visual = PendingActorVisuals.AddDefaulted_GetRef();
        Visual.ActorId = Text(Actor.id);
        Visual.Motions = Profile->Motions;
        Visual.Component = NewObject<USkeletalMeshComponent>(this, NAME_None, RF_Transient);
        auto* Component = Visual.Component.Get();
        if (!Component)
        {
            ClearPending();
            return Fail(TEXT("Failed to allocate a native athlete component."));
        }
        Component->SetupAttachment(SceneRoot);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetVisibility(false);
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetGenerateOverlapEvents(false);
        Component->SetSkeletalMeshAsset(Profile->Mesh);
        Component->bEnableUpdateRateOptimizations = false;
        Component->bOnlyAllowAutonomousTickPose = true;
        Component->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        Component->PrimaryComponentTick.bStartWithTickEnabled = false;
        Component->RegisterComponent();
        Component->SetComponentTickEnabled(false);
        Component->SetAnimationMode(EAnimationMode::AnimationSingleNode, true);
        Component->SetDisablePostProcessBlueprint(true);
        Component->SetSimulatePhysics(false);
        Component->SuspendClothingSimulation();
        if (!Component->IsRegistered() || !Component->GetSingleNodeInstance())
        {
            ClearPending();
            return Fail(TEXT("Native athlete component registration or single-node initialization failed."));
        }
    }
    for (const auto& Ball : Input.balls)
    {
        FSquadPadBallVisual& Visual = PendingBallVisuals.AddDefaulted_GetRef();
        Visual.BallId = Text(Ball.id);
        Visual.MeshRadiusCm = InData->BallMeshRadiusCm;
        Visual.Component = NewObject<UStaticMeshComponent>(this, NAME_None, RF_Transient);
        auto* Component = Visual.Component.Get();
        if (!Component)
        {
            ClearPending();
            return Fail(TEXT("Failed to allocate a native ball component."));
        }
        Component->SetupAttachment(SceneRoot);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetVisibility(false);
        Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Component->SetGenerateOverlapEvents(false);
        Component->SetStaticMesh(InData->BallMesh);
        if (InData->BallMaterial) Component->SetMaterial(0, InData->BallMaterial);
        Component->RegisterComponent();
        Component->SetComponentTickEnabled(false);
        Component->SetSimulatePhysics(false);
        if (!Component->IsRegistered())
        {
            ClearPending();
            return Fail(TEXT("Native ball component registration failed."));
        }
    }
    if (!ApplyFrame(NewController->CurrentFrame(), PendingActorVisuals, PendingBallVisuals, Error))
    {
        ClearPending();
        return Fail(Error);
    }

    // Commit only after every new component exists and the first pose is evaluated.
    SetActorTickEnabled(false);
    DestroyVisuals(ActorVisuals, BallVisuals);
    Controller = MoveTemp(NewController);
    ActorVisuals = MoveTemp(PendingActorVisuals);
    BallVisuals = MoveTemp(PendingBallVisuals);
    LoadedData = PendingData;
    PendingData = nullptr;
    PlaybackData = InData;
    for (auto& Visual : ActorVisuals) Visual.Component->SetVisibility(true);
    for (auto& Visual : BallVisuals) Visual.Component->SetVisibility(true);
    LastError.Reset();
    OnLoaded.Broadcast();
    return true;
}

bool ASquadPadPlaybackActor::ApplyFrame(const squadpad::playback::FrameState& Frame,
                                      TArray<FSquadPadActorVisual>& Actors,
                                      TArray<FSquadPadBallVisual>& Balls,
                                      FString& Error)
{
    if (!GetActorTransform().Equals(FTransform::Identity, 1e-8))
        return Reject(Error, TEXT("Playback actor transform changed from the identity world frame."));
    if (Actors.Num() != static_cast<int32>(Frame.actors.size()) ||
        Balls.Num() != static_cast<int32>(Frame.balls.size()))
        return Reject(Error, TEXT("Native component counts no longer match the playback plan."));

    // Preflight every component before changing any pose or transform.
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        const auto& State = Frame.actors[static_cast<std::size_t>(Index)];
        const auto& Visual = Actors[Index];
        if (!Visual.ActorId.Equals(Text(State.id), ESearchCase::CaseSensitive) ||
            !IsValid(Visual.Component) || !Visual.Component->IsRegistered() ||
            !Visual.Component->GetSingleNodeInstance())
            return Reject(Error, TEXT("Native athlete components changed or were destroyed. Reload the scene."));
        const auto* Binding = Visual.Motions.FindByPredicate([&](const FSquadPadMotionAssetBinding& Item)
            { return Item.SourceMotionId.Equals(Text(State.source_motion_id), ESearchCase::CaseSensitive); });
        if (!Binding || !IsValid(Binding->Animation))
            return Reject(Error, TEXT("An active native animation binding is unavailable."));
    }
    for (int32 Index = 0; Index < Balls.Num(); ++Index)
    {
        const auto& State = Frame.balls[static_cast<std::size_t>(Index)];
        const auto& Visual = Balls[Index];
        if (!Visual.BallId.Equals(Text(State.id), ESearchCase::CaseSensitive) ||
            !IsValid(Visual.Component) || !Visual.Component->IsRegistered())
            return Reject(Error, TEXT("Native ball components changed or were destroyed. Reload the scene."));
    }
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        const auto& State = Frame.actors[static_cast<std::size_t>(Index)];
        auto& Visual = Actors[Index];
        auto* Component = Visual.Component.Get();
        const auto* Binding = Visual.Motions.FindByPredicate([&](const FSquadPadMotionAssetBinding& Item)
            { return Item.SourceMotionId.Equals(Text(State.source_motion_id), ESearchCase::CaseSensitive); });
        UAnimSingleNodeInstance* Instance = Component->GetSingleNodeInstance();
        if (Instance->GetCurrentAsset() != Binding->Animation.Get())
        {
            Instance->SetAnimationAsset(Binding->Animation, false, 0.0f);
        }
        Instance->SetPlaying(false);
        Instance->SetLooping(false);
        Instance->SetPlayRate(0.0f);
        Instance->SetRootMotionMode(ERootMotionMode::NoRootMotionExtraction);
        const double NativeLength = Binding->Animation->GetPlayLength();
        const float Sample = static_cast<float>(FMath::Clamp(State.source_time_seconds, 0.0, NativeLength));
        Component->SetRelativeLocationAndRotation(
            FVector(State.position_cm[0], State.position_cm[1], State.position_cm[2]),
            FRotator(0.0, State.yaw_degrees, 0.0), false, nullptr, ETeleportType::TeleportPhysics);
        // Native transform/bone callbacks may destroy this actor independently of
        // the guarded Blueprint commands. Do not use cleared transient state.
        if (IsActorBeingDestroyed() || !IsValid(Component) || !Component->IsRegistered() ||
            Actors.Num() != static_cast<int32>(Frame.actors.size()) ||
            Balls.Num() != static_cast<int32>(Frame.balls.size()))
            return Reject(Error, TEXT("Playback scene was destroyed during native transform evaluation."));
        // Current and previous times are equal: no notify interval, including on seeks.
        Instance->SetPositionWithPreviousTime(Sample, Sample, false);
        Component->TickAnimation(0.0f, false);
        // A null tick function forces the direct/manual update path, not a queued tick.
        Component->RefreshBoneTransforms(nullptr);
        if (IsActorBeingDestroyed() || !IsValid(Component) || !Component->IsRegistered() ||
            Actors.Num() != static_cast<int32>(Frame.actors.size()) ||
            Balls.Num() != static_cast<int32>(Frame.balls.size()))
            return Reject(Error, TEXT("Playback scene was destroyed during native bone evaluation."));
        Component->UpdateComponentToWorld();
        Component->UpdateBounds();
        Component->MarkRenderDynamicDataDirty();
        if (Component->GetNumComponentSpaceTransforms() <= 0)
            return Reject(Error, TEXT("Native skeletal evaluation returned no component-space bones."));
    }
    for (int32 Index = 0; Index < Balls.Num(); ++Index)
    {
        const auto& State = Frame.balls[static_cast<std::size_t>(Index)];
        auto& Visual = Balls[Index];
        auto* Component = Visual.Component.Get();
        Component->SetRelativeTransform(FTransform(FQuat::Identity,
            FVector(State.position_cm[0], State.position_cm[1], State.position_cm[2]),
            FVector(State.radius_cm / Visual.MeshRadiusCm)),
            false, nullptr, ETeleportType::TeleportPhysics);
        if (IsActorBeingDestroyed() || !IsValid(Component) || !Component->IsRegistered() ||
            Actors.Num() != static_cast<int32>(Frame.actors.size()) ||
            Balls.Num() != static_cast<int32>(Frame.balls.size()))
            return Reject(Error, TEXT("Playback scene was destroyed during native ball evaluation."));
    }
    return true;
}

bool ASquadPadPlaybackActor::Play()
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    Controller->Play();
    SetActorTickEnabled(Controller->IsPlaying());
    LastError.Reset();
    return true;
}

bool ASquadPadPlaybackActor::Pause()
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    Controller->Pause();
    SetActorTickEnabled(false);
    LastError.Reset();
    return true;
}

bool ASquadPadPlaybackActor::Seek(double Seconds)
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    try
    {
        const auto Result = Controller->Seek(Seconds);
        FString Error;
        if (!ApplyFrame(Result.frame, ActorVisuals, BallVisuals, Error))
        {
            if (Controller) Controller->Pause();
            SetActorTickEnabled(false);
            return Fail(Error);
        }
        SetActorTickEnabled(Controller->IsPlaying());
        LastError.Reset();
        return true;
    }
    catch (const std::exception& Exception) { return Fail(UTF8_TO_TCHAR(Exception.what())); }
}

bool ASquadPadPlaybackActor::Restart()
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    const auto Result = Controller->Restart();
    SetActorTickEnabled(false);
    FString Error;
    if (!ApplyFrame(Result.frame, ActorVisuals, BallVisuals, Error)) return Fail(Error);
    LastError.Reset();
    return true;
}

bool ASquadPadPlaybackActor::SetPlaybackRate(double Rate)
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    try
    {
        Controller->SetRate(Rate);
        LastError.Reset();
        return true;
    }
    catch (const std::exception& Exception) { return Fail(UTF8_TO_TCHAR(Exception.what())); }
}

bool ASquadPadPlaybackActor::AdvancePlayback(double DeltaSeconds)
{
    if (!CheckCommandEntry()) return false;
    TGuardValue<bool> Guard(bCommandExecuting, true);
    if (!RequireLoaded()) return false;
    try
    {
        const bool WasPlaying = Controller->IsPlaying();
        const auto Result = Controller->Advance(DeltaSeconds);
        FString Error;
        if (!ApplyFrame(Result.frame, ActorVisuals, BallVisuals, Error))
        {
            if (Controller) Controller->Pause();
            SetActorTickEnabled(false);
            return Fail(Error);
        }
        const bool Completed = WasPlaying && !Controller->IsPlaying() &&
                               Controller->TimeSeconds() == Controller->Plan().DurationSeconds();
        SetActorTickEnabled(Controller->IsPlaying());
        LastError.Reset();
        // The complete native frame is applied before publishing timeline events.
        for (const auto& Contact : Result.crossed_contact_ids)
        {
            OnContact.Broadcast(Text(Contact));
            if (IsActorBeingDestroyed()) return true;
        }
        if (Completed) OnPlaybackCompleted.Broadcast();
        return true;
    }
    catch (const std::exception& Exception) { return Fail(UTF8_TO_TCHAR(Exception.what())); }
}

void ASquadPadPlaybackActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (Controller && Controller->IsPlaying()) AdvancePlayback(DeltaSeconds);
}

bool ASquadPadPlaybackActor::IsLoaded() const { return Controller.IsValid(); }
bool ASquadPadPlaybackActor::IsPlaying() const { return Controller && Controller->IsPlaying(); }
double ASquadPadPlaybackActor::GetPlaybackTime() const { return Controller ? Controller->TimeSeconds() : 0.0; }
double ASquadPadPlaybackActor::GetDuration() const { return Controller ? Controller->Plan().DurationSeconds() : 0.0; }
double ASquadPadPlaybackActor::GetPlaybackRate() const { return Controller ? Controller->Rate() : 1.0; }
FString ASquadPadPlaybackActor::GetPlaybackId() const { return Controller ? Text(Controller->Plan().Id()) : FString(); }
FString ASquadPadPlaybackActor::GetLastError() const { return LastError; }

USkeletalMeshComponent* ASquadPadPlaybackActor::GetActorMesh(const FString& ActorId) const
{
    const auto* Visual = ActorVisuals.FindByPredicate([&](const FSquadPadActorVisual& Item)
        { return Item.ActorId.Equals(ActorId, ESearchCase::CaseSensitive); });
    return Visual && IsValid(Visual->Component) ? Visual->Component.Get() : nullptr;
}

UStaticMeshComponent* ASquadPadPlaybackActor::GetBallMesh(const FString& BallId) const
{
    const auto* Visual = BallVisuals.FindByPredicate([&](const FSquadPadBallVisual& Item)
        { return Item.BallId.Equals(BallId, ESearchCase::CaseSensitive); });
    return Visual && IsValid(Visual->Component) ? Visual->Component.Get() : nullptr;
}

double ASquadPadPlaybackActor::GetActorSourceTime(const FString& ActorId) const
{
    if (!Controller) return -1.0;
    for (const auto& Actor : Controller->CurrentFrame().actors)
        if (Text(Actor.id).Equals(ActorId, ESearchCase::CaseSensitive)) return Actor.source_time_seconds;
    return -1.0;
}

double ASquadPadPlaybackActor::GetActorSampledTime(const FString& ActorId) const
{
    auto* Component = GetActorMesh(ActorId);
    auto* Instance = Component ? Component->GetSingleNodeInstance() : nullptr;
    return Instance ? static_cast<double>(Instance->GetCurrentTime()) : -1.0;
}

UAnimSequence* ASquadPadPlaybackActor::GetActorSampledAnimation(const FString& ActorId) const
{
    auto* Component = GetActorMesh(ActorId);
    auto* Instance = Component ? Component->GetSingleNodeInstance() : nullptr;
    return Instance ? Cast<UAnimSequence>(Instance->GetCurrentAsset()) : nullptr;
}

FString ASquadPadPlaybackActor::GetActorAction(const FString& ActorId) const
{
    if (Controller)
        for (const auto& Actor : Controller->CurrentFrame().actors)
            if (Text(Actor.id).Equals(ActorId, ESearchCase::CaseSensitive)) return Text(Actor.action_id);
    return FString();
}

FString ASquadPadPlaybackActor::GetBallOwner(const FString& BallId) const
{
    if (Controller)
        for (const auto& Ball : Controller->CurrentFrame().balls)
            if (Text(Ball.id).Equals(BallId, ESearchCase::CaseSensitive)) return Text(Ball.possession.owner_id);
    return FString();
}
