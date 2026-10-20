#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "SquadPadProbeGameMode.generated.h"

/** Minimal game mode for the native delivery probe. No product navigation. */
UCLASS()
class SQUADPADRUNTIME_API ASquadPadProbeGameMode : public AGameModeBase
{
    GENERATED_BODY()

public:
    ASquadPadProbeGameMode();
};
