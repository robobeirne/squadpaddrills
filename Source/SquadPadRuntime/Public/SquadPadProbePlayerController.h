#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"
#include "SquadPadProbePlayerController.generated.h"

class ASquadPadPlaybackActor;

/** Developer controls for a single isolated probe scene. Not the Swift bridge. */
UCLASS()
class SQUADPADRUNTIME_API ASquadPadProbePlayerController : public APlayerController
{
    GENERATED_BODY()

public:
    ASquadPadProbePlayerController();

    UFUNCTION(Exec)
    void SPPlay();

    UFUNCTION(Exec)
    void SPPause();

    UFUNCTION(Exec)
    void SPSeek(double Seconds);

    UFUNCTION(Exec)
    void SPRate(double Rate);

    UFUNCTION(Exec)
    void SPRestart();

    UFUNCTION(Exec)
    void SPUnload();

    UFUNCTION(Exec)
    void SPLoad();

    UFUNCTION(Exec)
    void SPStatus();

protected:
    virtual void BeginPlay() override;

private:
    ASquadPadPlaybackActor* FindPlaybackActor();
    void Report(const FString& Message);
    void ReportCommand(ASquadPadPlaybackActor* Actor, bool bSucceeded);
};
