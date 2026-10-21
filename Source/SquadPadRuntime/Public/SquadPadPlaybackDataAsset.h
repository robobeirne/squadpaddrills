#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "SquadPadPlaybackDataAsset.generated.h"

class UAnimSequence;
class UMaterialInterface;
class USkeletalMesh;
class UStaticMesh;

/** A captured performance already retargeted onto this profile's exact Skeleton. */
USTRUCT(BlueprintType)
struct SQUADPADRUNTIME_API FSquadPadMotionAssetBinding
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    FString SourceMotionId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TObjectPtr<UAnimSequence> Animation = nullptr;
};

/** Hard references keep the explicitly bound baseline assets reachable by the cooker. */
USTRUCT(BlueprintType)
struct SQUADPADRUNTIME_API FSquadPadAthleteAssetProfile
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    FString ProfileId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TObjectPtr<USkeletalMesh> Mesh = nullptr;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TArray<FSquadPadMotionAssetBinding> Motions;
};

USTRUCT(BlueprintType)
struct SQUADPADRUNTIME_API FSquadPadAppearanceAssetBinding
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    FString AppearanceSlot;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    FString ProfileId;
};

/**
 * Cooked first-pass presentation inputs. JSON has no source-machine paths.
 * The editor probe validates source provenance before creating this native asset.
 * Runtime validates the JSON, exact binding coverage and native asset compatibility.
 * This direct-clip baseline does not implement retargeting, IK or pose blending.
 */
UCLASS(BlueprintType)
class SQUADPADRUNTIME_API USquadPadPlaybackDataAsset : public UDataAsset
{
    GENERATED_BODY()

public:
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad", meta=(MultiLine="true"))
    FString PlaybackJson;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TArray<FSquadPadAthleteAssetProfile> Profiles;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TArray<FSquadPadAppearanceAssetBinding> AppearanceBindings;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TObjectPtr<UStaticMesh> BallMesh = nullptr;

    /** Radius in the unscaled mesh's centimetre coordinate system. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad", meta=(ClampMin="0.001"))
    float BallMeshRadiusCm = 50.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="SquadPad")
    TObjectPtr<UMaterialInterface> BallMaterial = nullptr;
};
