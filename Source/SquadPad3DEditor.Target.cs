using UnrealBuildTool;

public class SquadPad3DEditorTarget : TargetRules
{
    public SquadPad3DEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("SquadPadRuntime");
    }
}
