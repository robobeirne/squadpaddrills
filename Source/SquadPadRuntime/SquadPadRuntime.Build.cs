using System.IO;
using UnrealBuildTool;

public class SquadPadRuntime : ModuleRules
{
    public SquadPadRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        // The tested portable core throws validation errors. The adapter catches
        // these inside this module before returning to Unreal or Blueprint.
        bEnableExceptions = true;
        // Keep the included portable implementation in its own translation unit.
        bUseUnity = false;
        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core", "CoreUObject", "Engine"
        });
        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "Json"
        });

        string PortableRoot = Path.GetFullPath(Path.Combine(ModuleDirectory, "../../RuntimeCore"));
        PublicIncludePaths.Add(Path.Combine(PortableRoot, "include"));
        ExternalDependencies.Add(Path.Combine(PortableRoot, "include/squadpad/playback.hpp"));
        ExternalDependencies.Add(Path.Combine(PortableRoot, "src/playback.cpp"));
        // Inherit the selected engine's C++ standard. The core is C++17-compatible.
        // No editor module or Python dependency enters the shipped runtime.
    }
}
