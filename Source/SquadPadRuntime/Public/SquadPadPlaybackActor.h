#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SquadPadPlaybackDataAsset.h"
#include "squadpad/playback.hpp"
#include "SquadPadPlaybackActor.generated.h"

class USceneComponent;
class USkeletalMeshComponent;
class UStaticMeshComponent;

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FSquadPadContactEvent, const FString&, ContactId);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FSquadPadPlaybackErrorEvent, const FString&, Message);
DECLARE_DYNAMIC_MULTICAST_DELEGATE(FSquadPadPlaybackReadyEvent);
DECLARE_DYNAMIC_MULTICAST_DELEGATE(FSquadPadPlaybackCompletedEvent);

/** GC-visible transient state, never serialized into the probe map. */
USTRUCT()
struct FSquadPadActorVisual
{
    GENERATED_BODY()

    UPROPERTY(Transient)
    FString ActorId;

    UPROPERTY(Transient)
    TObjectPtr<USkeletalMeshComponent> Component = nullptr;

    UPROPERTY(Transient)
    TArray<FSquadPadMotionAssetBinding> Motions;
};

USTRUCT()
struct FSquadPadBallVisual
{
    GENERATED_BODY()

    UPROPERTY(Transient)
    FString BallId;

    UPROPERTY(Transient)
    TObjectPtr<UStaticMeshComponent> Component = nullptr;

    UPROPERTY(Transient)
    float MeshRadiusCm = 50.0f;
};

/**
 * Minimal native presentation adapter for the unblended baseline.
 * Place at the identity transform. Portable actor tracks already own world travel.
 * Commands and queries are game-thread only. Reentrant commands from callbacks
 * fail, so an event batch cannot replace its scene partway through dispatch.
 * No autonomous animation clock, root extraction, physics, cloth or post-process
 * animation Blueprint runs. Corrective rigs need a later presentation policy.
 */
UCLASS(BlueprintType, Blueprintable)
class SQUADPADRUNTIME_API ASquadPadPlaybackActor : public AActor
{
    GENERATED_BODY()

public:
    ASquadPadPlaybackActor();
    virtual ~ASquadPadPlaybackActor() override;

    /** Configured asset survives unload. LoadedData separately owns the active asset. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TObjectPtr<USquadPadPlaybackDataAsset> PlaybackData = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    bool bAutoLoadOnBeginPlay = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    bool bAutoPlay = false;

    UPROPERTY(BlueprintAssignable, Category="SquadPad")
    FSquadPadContactEvent OnContact;

    UPROPERTY(BlueprintAssignable, Category="SquadPad")
    FSquadPadPlaybackReadyEvent OnLoaded;

    UPROPERTY(BlueprintAssignable, Category="SquadPad")
    FSquadPadPlaybackCompletedEvent OnPlaybackCompleted;

    UPROPERTY(BlueprintAssignable, Category="SquadPad")
    FSquadPadPlaybackErrorEvent OnError;

    /** Failed replacement leaves the active plan and existing components intact. */
    UFUNCTION(BlueprintCallable, Category="SquadPad", meta=(UnsafeDuringActorConstruction="true"))
    bool LoadPlayback(USquadPadPlaybackDataAsset* InData);

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool Play();

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool Pause();

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool Seek(double Seconds);

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool Restart();

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool SetPlaybackRate(double Rate);

    /** Manual probe entry or actor Tick. Paused calls do not move the playhead. */
    UFUNCTION(BlueprintCallable, Category="SquadPad")
    bool AdvancePlayback(double DeltaSeconds);

    UFUNCTION(BlueprintCallable, Category="SquadPad")
    void UnloadPlayback();

    UFUNCTION(BlueprintPure, Category="SquadPad")
    bool IsLoaded() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    bool IsPlaying() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    double GetPlaybackTime() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    double GetDuration() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    double GetPlaybackRate() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    FString GetPlaybackId() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    FString GetLastError() const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    USkeletalMeshComponent* GetActorMesh(const FString& ActorId) const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    UStaticMeshComponent* GetBallMesh(const FString& BallId) const;

    /** Canonical source clock. Returns -1 when the actor is absent. */
    UFUNCTION(BlueprintPure, Category="SquadPad")
    double GetActorSourceTime(const FString& ActorId) const;

    /** Actual single-node instance time, independent of the requested source clock. */
    UFUNCTION(BlueprintPure, Category="SquadPad")
    double GetActorSampledTime(const FString& ActorId) const;

    /** Actual native instance asset; null if absent or no longer an AnimSequence. */
    UFUNCTION(BlueprintPure, Category="SquadPad")
    UAnimSequence* GetActorSampledAnimation(const FString& ActorId) const;

    UFUNCTION(BlueprintPure, Category="SquadPad")
    FString GetActorAction(const FString& ActorId) const;

    /** Empty during transit or when the ball is absent. */
    UFUNCTION(BlueprintPure, Category="SquadPad")
    FString GetBallOwner(const FString& BallId) const;

    virtual void Tick(float DeltaSeconds) override;
    virtual void Destroyed() override;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    UPROPERTY(VisibleAnywhere, Category="SquadPad")
    TObjectPtr<USceneComponent> SceneRoot = nullptr;

    UPROPERTY(Transient)
    TObjectPtr<USquadPadPlaybackDataAsset> LoadedData = nullptr;

    UPROPERTY(Transient)
    TObjectPtr<USquadPadPlaybackDataAsset> PendingData = nullptr;

    UPROPERTY(Transient)
    TArray<FSquadPadActorVisual> ActorVisuals;

    UPROPERTY(Transient)
    TArray<FSquadPadBallVisual> BallVisuals;

    UPROPERTY(Transient)
    TArray<FSquadPadActorVisual> PendingActorVisuals;

    UPROPERTY(Transient)
    TArray<FSquadPadBallVisual> PendingBallVisuals;

    UPROPERTY(Transient)
    FString LastError;

    TUniquePtr<squadpad::playback::PlaybackController> Controller;
    bool bCommandExecuting = false;

    bool CheckCommandEntry();
    bool RequireLoaded();
    bool Fail(const FString& Message);
    void ClearPlaybackInternal();
    void ClearPending();
    bool ApplyFrame(const squadpad::playback::FrameState& Frame,
                    TArray<FSquadPadActorVisual>& Actors,
                    TArray<FSquadPadBallVisual>& Balls,
                    FString& Error);
};
