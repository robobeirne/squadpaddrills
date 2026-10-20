using UnrealBuildTool;

public class SquadPad3DTarget : TargetRules
{
    public SquadPad3DTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("SquadPadRuntime");
    }
}
