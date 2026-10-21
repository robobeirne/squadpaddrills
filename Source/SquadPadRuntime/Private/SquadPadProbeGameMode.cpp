#include "SquadPadProbeGameMode.h"

#include "SquadPadProbePlayerController.h"

ASquadPadProbeGameMode::ASquadPadProbeGameMode()
{
    PlayerControllerClass = ASquadPadProbePlayerController::StaticClass();
    DefaultPawnClass = nullptr;
    HUDClass = nullptr;
}
