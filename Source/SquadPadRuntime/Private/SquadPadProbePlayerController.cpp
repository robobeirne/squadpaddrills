#include "SquadPadProbePlayerController.h"

#include "Camera/CameraActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "SquadPadPlaybackActor.h"

DEFINE_LOG_CATEGORY_STATIC(LogSquadPadProbe, Log, All);

ASquadPadProbePlayerController::ASquadPadProbePlayerController()
{
    bAutoManageActiveCameraTarget = false;
}

void ASquadPadProbePlayerController::BeginPlay()
{
    Super::BeginPlay();
    ACameraActor* ProbeCamera = nullptr;
    for (TActorIterator<ACameraActor> It(GetWorld()); It; ++It)
    {
        if (!It->ActorHasTag(TEXT("SquadPadProbeCamera")))
        {
            continue;
        }
        if (ProbeCamera)
        {
            Report(TEXT("Multiple tagged probe cameras. Camera selection refused."));
            return;
        }
        ProbeCamera = *It;
    }
    if (ProbeCamera)
    {
        SetViewTarget(ProbeCamera);
    }
    else
    {
        Report(TEXT("Probe camera missing. Build the dedicated runtime probe map."));
    }
}

ASquadPadPlaybackActor* ASquadPadProbePlayerController::FindPlaybackActor()
{
    if (!GetWorld())
    {
        Report(TEXT("No active world."));
        return nullptr;
    }
    ASquadPadPlaybackActor* Result = nullptr;
    for (TActorIterator<ASquadPadPlaybackActor> It(GetWorld()); It; ++It)
    {
        if (Result)
        {
            Report(TEXT("Multiple playback actors. Probe command refused."));
            return nullptr;
        }
        Result = *It;
    }
    if (!Result)
    {
        Report(TEXT("No SquadPad playback actor in the active map."));
    }
    return Result;
}

void ASquadPadProbePlayerController::Report(const FString& Message)
{
    UE_LOG(LogSquadPadProbe, Display, TEXT("%s"), *Message);
    ClientMessage(Message);
}

void ASquadPadProbePlayerController::ReportCommand(ASquadPadPlaybackActor* Actor, bool bSucceeded)
{
    if (!bSucceeded)
    {
        Report(Actor->GetLastError());
        return;
    }
    Report(FString::Printf(TEXT("time=%.9f duration=%.9f rate=%.3f playing=%s loaded=%s"),
        Actor->GetPlaybackTime(), Actor->GetDuration(), Actor->GetPlaybackRate(),
        Actor->IsPlaying() ? TEXT("true") : TEXT("false"),
        Actor->IsLoaded() ? TEXT("true") : TEXT("false")));
}

void ASquadPadProbePlayerController::SPPlay()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->Play());
}

void ASquadPadProbePlayerController::SPPause()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->Pause());
}

void ASquadPadProbePlayerController::SPSeek(double Seconds)
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->Seek(Seconds));
}

void ASquadPadProbePlayerController::SPRate(double Rate)
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->SetPlaybackRate(Rate));
}

void ASquadPadProbePlayerController::SPRestart()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->Restart());
}

void ASquadPadProbePlayerController::SPUnload()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
    {
        Actor->UnloadPlayback();
        ReportCommand(Actor, !Actor->IsLoaded());
    }
}

void ASquadPadProbePlayerController::SPLoad()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, Actor->LoadPlayback(Actor->PlaybackData));
}

void ASquadPadProbePlayerController::SPStatus()
{
    if (ASquadPadPlaybackActor* Actor = FindPlaybackActor())
        ReportCommand(Actor, true);
}
