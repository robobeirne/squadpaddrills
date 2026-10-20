#include "SquadPadPlaybackReader.h"

#include "Containers/StringConv.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonTypes.h"

#include <cmath>
#include <exception>
#include <initializer_list>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace SquadPadRuntime
{
namespace
{

using FJsonObjectPtr = TSharedPtr<FJsonObject>;
using FJsonValuePtr = TSharedPtr<FJsonValue>;
using FJsonArray = TArray<FJsonValuePtr>;
using namespace squadpad::playback;

struct FPlaybackReadFailure
{
    FString Message;
};

void RequireRead(bool Condition, const FString& Message)
{
    if (!Condition)
    {
        throw FPlaybackReadFailure{Message};
    }
}

std::string Utf8(const FString& Text)
{
    const FTCHARToUTF8 Converted(*Text, Text.Len());
    return std::string(Converted.Get(), static_cast<std::size_t>(Converted.Length()));
}

FString MemberPath(const FString& Parent, const TCHAR* Name)
{
    return Parent + TEXT(".") + Name;
}

FString ElementPath(const FString& Parent, int32 Index)
{
    return FString::Printf(TEXT("%s[%d]"), *Parent, Index);
}

// This is a token preflight over Epic's JSON parser, not a second JSON grammar.
// A DOM normally loses duplicate member evidence. Track decoded names before
// deserialization, using explicit string lengths to preserve embedded zeros.
void PreflightJson(const FString& Json)
{
    RequireRead(Json.Len() > 0 && Json.Len() <= MaximumPlaybackJsonCharacters,
        TEXT("Playback JSON is empty or exceeds 4 Mi TCHAR code units"));

    struct FScope
    {
        bool bObject;
        bool bOpenMetadata;
        std::set<std::basic_string<TCHAR>> MemberNames;
    };
    std::vector<FScope> Stack;
    Stack.reserve(MaximumPlaybackJsonDepth);
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    EJsonNotation Notation;
    int32 TokenCount = 0;
    bool bRootStarted = false;
    bool bRootFinished = false;

    while (Reader->ReadNext(Notation))
    {
        RequireRead(++TokenCount <= MaximumPlaybackJsonTokens,
            TEXT("Playback JSON exceeds 500000 notation tokens"));
        RequireRead(Notation != EJsonNotation::Error,
            TEXT("Invalid playback JSON: ") + Reader->GetErrorMessage());

        const bool bEnd = Notation == EJsonNotation::ObjectEnd ||
            Notation == EJsonNotation::ArrayEnd;
        bool bOpenMetadata = !Stack.empty() && Stack.back().bOpenMetadata;
        if (!bEnd)
        {
            if (Stack.empty())
            {
                RequireRead(!bRootStarted && !bRootFinished,
                    TEXT("Playback JSON must contain exactly one root value"));
                RequireRead(Notation == EJsonNotation::ObjectStart,
                    TEXT("Playback JSON root must be an object"));
                bRootStarted = true;
            }
            else if (Stack.back().bObject)
            {
                const FString& Name = Reader->GetIdentifier();
                const std::basic_string<TCHAR> ExactName(*Name,
                    static_cast<std::size_t>(Name.Len()));
                RequireRead(Stack.back().MemberNames.insert(ExactName).second,
                    FString::Printf(TEXT("Duplicate JSON object member at line %u, character %u"),
                        Reader->GetLineNumber(), Reader->GetCharacterNumber()));

                if (!bOpenMetadata)
                {
                    // Every v1 contract field uses lowercase ASCII letters or
                    // underscores. Check this before DOM construction because
                    // some engine string-key maps fold case. The subsequent
                    // Fields checks enforce the exact allowed names per object.
                    RequireRead(!Name.IsEmpty(), TEXT("Playback contract field name is empty"));
                    for (int32 Index = 0; Index < Name.Len(); ++Index)
                    {
                        const TCHAR Character = Name[Index];
                        if (!((Character >= TEXT('a') && Character <= TEXT('z')) ||
                            Character == TEXT('_')))
                        {
                            throw FPlaybackReadFailure{FString::Printf(
                                TEXT("Playback contract field spelling is invalid at line %u, character %u"),
                                Reader->GetLineNumber(), Reader->GetCharacterNumber())};
                        }
                    }
                    // Only these exact root fields open the metadata namespace.
                    // Descendants keep it through both objects and arrays.
                    bOpenMetadata = Stack.size() == 1 &&
                        (Name.Equals(TEXT("provenance"), ESearchCase::CaseSensitive) ||
                         Name.Equals(TEXT("evidence"), ESearchCase::CaseSensitive));
                }
            }
        }

        switch (Notation)
        {
        case EJsonNotation::ObjectStart:
        case EJsonNotation::ArrayStart:
            RequireRead(Stack.size() < static_cast<std::size_t>(MaximumPlaybackJsonDepth),
                TEXT("Playback JSON nesting exceeds depth 32"));
            Stack.push_back(FScope{Notation == EJsonNotation::ObjectStart, bOpenMetadata, {}});
            break;
        case EJsonNotation::ObjectEnd:
        case EJsonNotation::ArrayEnd:
            RequireRead(!Stack.empty() &&
                Stack.back().bObject == (Notation == EJsonNotation::ObjectEnd),
                TEXT("Playback JSON has mismatched containers"));
            Stack.pop_back();
            if (Stack.empty())
            {
                bRootFinished = true;
            }
            break;
        case EJsonNotation::Number:
            RequireRead(std::isfinite(Reader->GetValueAsNumber()),
                TEXT("Playback JSON contains a number outside finite double range"));
            break;
        case EJsonNotation::String:
        case EJsonNotation::Boolean:
        case EJsonNotation::Null:
            break;
        default:
            throw FPlaybackReadFailure{TEXT("Unsupported playback JSON notation")};
        }
    }

    RequireRead(Reader->GetErrorMessage().IsEmpty(),
        TEXT("Invalid playback JSON: ") + Reader->GetErrorMessage());
    RequireRead(bRootStarted && bRootFinished && Stack.empty(),
        TEXT("Playback JSON root is incomplete"));
}

FJsonObjectPtr Object(const FJsonValuePtr& Value, const FString& Path)
{
    RequireRead(Value.IsValid() && Value->Type == EJson::Object,
        Path + TEXT(" must be an object"));
    const FJsonObjectPtr Result = Value->AsObject();
    RequireRead(Result.IsValid(), Path + TEXT(" contains an invalid object"));
    return Result;
}

FJsonValuePtr Field(const FJsonObjectPtr& Value, const TCHAR* Name, const FString& Path)
{
    const FJsonValuePtr Result = Value->TryGetField(Name);
    RequireRead(Result.IsValid(), MemberPath(Path, Name) + TEXT(" is required"));
    return Result;
}

void Fields(const FJsonObjectPtr& Value,
    std::initializer_list<const TCHAR*> Required,
    std::initializer_list<const TCHAR*> Optional,
    const FString& Path)
{
    int32 ExpectedCount = 0;
    for (const TCHAR* Name : Required)
    {
        RequireRead(Value->HasField(Name), MemberPath(Path, Name) + TEXT(" is required"));
        ++ExpectedCount;
    }
    for (const TCHAR* Name : Optional)
    {
        if (Value->HasField(Name))
        {
            ++ExpectedCount;
        }
    }
    // Preflight excludes case variants outside metadata before any map can fold
    // them. Count after checking the exact required and optional field names.
    // This works with UE 5.8 shared-string keys without assuming FString keys.
    RequireRead(Value->Values.Num() == ExpectedCount,
        Path + TEXT(" contains unsupported fields"));
}

FString String(const FJsonValuePtr& Value, const FString& Path)
{
    RequireRead(Value.IsValid() && Value->Type == EJson::String,
        Path + TEXT(" must be a string"));
    return Value->AsString();
}

std::string Identifier(const FJsonValuePtr& Value, const FString& Path)
{
    const FString Text = String(Value, Path);
    RequireRead(!Text.IsEmpty(), Path + TEXT(" must be a portable identifier"));
    std::string Result;
    Result.reserve(static_cast<std::size_t>(Text.Len()));
    for (int32 Index = 0; Index < Text.Len(); ++Index)
    {
        const TCHAR Character = Text[Index];
        const bool bAllowed = (Character >= TEXT('A') && Character <= TEXT('Z')) ||
            (Character >= TEXT('a') && Character <= TEXT('z')) ||
            (Character >= TEXT('0') && Character <= TEXT('9')) ||
            Character == TEXT('_') || Character == TEXT('-') ||
            Character == TEXT('.') || Character == TEXT(':');
        RequireRead(bAllowed, Path + TEXT(" must be a portable ASCII identifier"));
        Result.push_back(static_cast<char>(Character));
    }
    return Result;
}

std::string NullableIdentifier(const FJsonValuePtr& Value, const FString& Path)
{
    RequireRead(Value.IsValid(), Path + TEXT(" is required"));
    if (Value->Type == EJson::Null)
    {
        return {};
    }
    // An empty string must fail, rather than becoming the null sentinel used
    // by PlanInput. Only an explicit JSON null maps to an empty optional ID.
    return Identifier(Value, Path);
}

double Number(const FJsonValuePtr& Value, const FString& Path)
{
    RequireRead(Value.IsValid() && Value->Type == EJson::Number,
        Path + TEXT(" must be a number"));
    const double Result = Value->AsNumber();
    RequireRead(std::isfinite(Result), Path + TEXT(" must fit a finite double"));
    return Result;
}

bool Boolean(const FJsonValuePtr& Value, const FString& Path)
{
    RequireRead(Value.IsValid() && Value->Type == EJson::Boolean,
        Path + TEXT(" must be a boolean"));
    return Value->AsBool();
}

const FJsonArray& Array(const FJsonValuePtr& Value, const FString& Path,
    int32 Minimum, int32 Maximum)
{
    RequireRead(Value.IsValid() && Value->Type == EJson::Array,
        Path + TEXT(" must be an array"));
    const FJsonArray& Result = Value->AsArray();
    RequireRead(Result.Num() >= Minimum && Result.Num() <= Maximum,
        FString::Printf(TEXT("%s requires %d to %d entries in this runtime profile"),
            *Path, Minimum, Maximum));
    return Result;
}

Vec3 Vector(const FJsonValuePtr& Value, const FString& Path)
{
    const FJsonArray& Values = Array(Value, Path, 3, 3);
    return {Number(Values[0], ElementPath(Path, 0)),
        Number(Values[1], ElementPath(Path, 1)),
        Number(Values[2], ElementPath(Path, 2))};
}

FString ReadString(const FJsonObjectPtr& Value, const TCHAR* Name, const FString& Path)
{
    return String(Field(Value, Name, Path), MemberPath(Path, Name));
}

std::string ReadId(const FJsonObjectPtr& Value, const TCHAR* Name, const FString& Path)
{
    return Identifier(Field(Value, Name, Path), MemberPath(Path, Name));
}

double ReadNumber(const FJsonObjectPtr& Value, const TCHAR* Name, const FString& Path)
{
    return Number(Field(Value, Name, Path), MemberPath(Path, Name));
}

const FJsonArray& ReadArray(const FJsonObjectPtr& Value, const TCHAR* Name,
    const FString& Path, int32 Minimum, int32 Maximum)
{
    return Array(Field(Value, Name, Path), MemberPath(Path, Name), Minimum, Maximum);
}

void AddToLimit(int32 Additional, int32 Maximum, int32& Total, const FString& Label)
{
    RequireRead(Additional <= Maximum - Total, Label + TEXT(" exceeds the runtime total limit"));
    Total += Additional;
}

PlanInput ReadPlan(const FJsonObjectPtr& Root)
{
    const FString Path = TEXT("playback");
    Fields(Root, {TEXT("schema"), TEXT("id"), TEXT("units"), TEXT("axes"),
        TEXT("root_ownership"), TEXT("duration_seconds"), TEXT("motions"),
        TEXT("actors"), TEXT("balls"), TEXT("possession"), TEXT("contacts")},
        {TEXT("provenance"), TEXT("evidence")}, Path);

    // Metadata is intentionally open-object shaped in the shared schema. The
    // token preflight has already checked every nested value and member name.
    for (const TCHAR* Name : {TEXT("provenance"), TEXT("evidence")})
    {
        if (Root->HasField(Name))
        {
            Object(Field(Root, Name, Path), MemberPath(Path, Name));
        }
    }

    PlanInput Result;
    Result.schema = Utf8(ReadString(Root, TEXT("schema"), Path));
    Result.id = ReadId(Root, TEXT("id"), Path);
    Result.axes = Utf8(ReadString(Root, TEXT("axes"), Path));
    Result.root_ownership = Utf8(ReadString(Root, TEXT("root_ownership"), Path));
    Result.duration_seconds = ReadNumber(Root, TEXT("duration_seconds"), Path);
    const FJsonObjectPtr Units = Object(Field(Root, TEXT("units"), Path), TEXT("playback.units"));
    Fields(Units, {TEXT("distance"), TEXT("time"), TEXT("angle")}, {}, TEXT("playback.units"));
    Result.distance_units = Utf8(ReadString(Units, TEXT("distance"), TEXT("playback.units")));
    Result.time_units = Utf8(ReadString(Units, TEXT("time"), TEXT("playback.units")));
    Result.angle_units = Utf8(ReadString(Units, TEXT("angle"), TEXT("playback.units")));

    const FJsonArray& Motions = ReadArray(Root, TEXT("motions"), Path, 1, MaximumPlaybackMotions);
    for (int32 Index = 0; Index < Motions.Num(); ++Index)
    {
        const FString P = ElementPath(TEXT("playback.motions"), Index);
        const FJsonObjectPtr Item = Object(Motions[Index], P);
        Fields(Item, {TEXT("id"), TEXT("duration_seconds")}, {}, P);
        Result.motions.push_back(Motion{ReadId(Item, TEXT("id"), P),
            ReadNumber(Item, TEXT("duration_seconds"), P)});
    }

    int32 TotalKeys = 0;
    int32 TotalSections = 0;
    const FJsonArray& Actors = ReadArray(Root, TEXT("actors"), Path, 1, MaximumPlaybackActors);
    for (int32 Index = 0; Index < Actors.Num(); ++Index)
    {
        const FString P = ElementPath(TEXT("playback.actors"), Index);
        const FJsonObjectPtr Item = Object(Actors[Index], P);
        Fields(Item, {TEXT("id"), TEXT("appearance_slot"), TEXT("transform_keys"), TEXT("sections")}, {}, P);
        Actor Value;
        Value.id = ReadId(Item, TEXT("id"), P);
        Value.appearance_slot = ReadId(Item, TEXT("appearance_slot"), P);
        const FJsonArray& Keys = ReadArray(Item, TEXT("transform_keys"), P, 2, MaximumPlaybackTotalKeys);
        AddToLimit(Keys.Num(), MaximumPlaybackTotalKeys, TotalKeys, TEXT("Actor and ball keys"));
        for (int32 KeyIndex = 0; KeyIndex < Keys.Num(); ++KeyIndex)
        {
            const FString K = ElementPath(MemberPath(P, TEXT("transform_keys")), KeyIndex);
            const FJsonObjectPtr Key = Object(Keys[KeyIndex], K);
            Fields(Key, {TEXT("time_seconds"), TEXT("position_cm"), TEXT("yaw_degrees")}, {}, K);
            Value.transform_keys.push_back(TransformKey{
                ReadNumber(Key, TEXT("time_seconds"), K),
                Vector(Field(Key, TEXT("position_cm"), K), MemberPath(K, TEXT("position_cm"))),
                ReadNumber(Key, TEXT("yaw_degrees"), K)});
        }
        const FJsonArray& Sections = ReadArray(Item, TEXT("sections"), P, 1, MaximumPlaybackTotalSections);
        AddToLimit(Sections.Num(), MaximumPlaybackTotalSections, TotalSections, TEXT("Action sections"));
        for (int32 SectionIndex = 0; SectionIndex < Sections.Num(); ++SectionIndex)
        {
            const FString S = ElementPath(MemberPath(P, TEXT("sections")), SectionIndex);
            const FJsonObjectPtr SectionObject = Object(Sections[SectionIndex], S);
            Fields(SectionObject, {TEXT("id"), TEXT("source_motion_id"), TEXT("start_seconds"),
                TEXT("end_seconds"), TEXT("source_start_seconds"), TEXT("play_rate"), TEXT("loop")}, {}, S);
            Value.sections.push_back(Section{
                ReadId(SectionObject, TEXT("id"), S),
                ReadId(SectionObject, TEXT("source_motion_id"), S),
                ReadNumber(SectionObject, TEXT("start_seconds"), S),
                ReadNumber(SectionObject, TEXT("end_seconds"), S),
                ReadNumber(SectionObject, TEXT("source_start_seconds"), S),
                ReadNumber(SectionObject, TEXT("play_rate"), S),
                Boolean(Field(SectionObject, TEXT("loop"), S), MemberPath(S, TEXT("loop")))});
        }
        Result.actors.push_back(std::move(Value));
    }

    const FJsonArray& Balls = ReadArray(Root, TEXT("balls"), Path, 1, MaximumPlaybackBalls);
    for (int32 Index = 0; Index < Balls.Num(); ++Index)
    {
        const FString P = ElementPath(TEXT("playback.balls"), Index);
        const FJsonObjectPtr Item = Object(Balls[Index], P);
        Fields(Item, {TEXT("id"), TEXT("radius_cm"), TEXT("position_keys")}, {}, P);
        Ball Value;
        Value.id = ReadId(Item, TEXT("id"), P);
        Value.radius_cm = ReadNumber(Item, TEXT("radius_cm"), P);
        const FJsonArray& Keys = ReadArray(Item, TEXT("position_keys"), P, 2, MaximumPlaybackTotalKeys);
        AddToLimit(Keys.Num(), MaximumPlaybackTotalKeys, TotalKeys, TEXT("Actor and ball keys"));
        for (int32 KeyIndex = 0; KeyIndex < Keys.Num(); ++KeyIndex)
        {
            const FString K = ElementPath(MemberPath(P, TEXT("position_keys")), KeyIndex);
            const FJsonObjectPtr Key = Object(Keys[KeyIndex], K);
            Fields(Key, {TEXT("time_seconds"), TEXT("position_cm")}, {}, K);
            Value.position_keys.push_back(PositionKey{ReadNumber(Key, TEXT("time_seconds"), K),
                Vector(Field(Key, TEXT("position_cm"), K), MemberPath(K, TEXT("position_cm")))});
        }
        Result.balls.push_back(std::move(Value));
    }

    const FJsonArray& PossessionRecords = ReadArray(Root, TEXT("possession"), Path,
        1, MaximumPlaybackPossessionRecords);
    for (int32 Index = 0; Index < PossessionRecords.Num(); ++Index)
    {
        const FString P = ElementPath(TEXT("playback.possession"), Index);
        const FJsonObjectPtr Item = Object(PossessionRecords[Index], P);
        Fields(Item, {TEXT("time_seconds"), TEXT("ball_id"), TEXT("state"), TEXT("owner_id"),
            TEXT("from_actor_id"), TEXT("to_actor_id")}, {}, P);
        PossessionKey Value;
        Value.time_seconds = ReadNumber(Item, TEXT("time_seconds"), P);
        Value.ball_id = ReadId(Item, TEXT("ball_id"), P);
        const FString State = ReadString(Item, TEXT("state"), P);
        RequireRead(State.Equals(TEXT("owned"), ESearchCase::CaseSensitive) ||
            State.Equals(TEXT("in_transit"), ESearchCase::CaseSensitive),
            MemberPath(P, TEXT("state")) + TEXT(" is unsupported"));
        Value.value.state = State.Equals(TEXT("owned"), ESearchCase::CaseSensitive) ?
            PossessionKind::Owned : PossessionKind::InTransit;
        Value.value.owner_id = NullableIdentifier(Field(Item, TEXT("owner_id"), P), MemberPath(P, TEXT("owner_id")));
        Value.value.from_actor_id = NullableIdentifier(Field(Item, TEXT("from_actor_id"), P), MemberPath(P, TEXT("from_actor_id")));
        Value.value.to_actor_id = NullableIdentifier(Field(Item, TEXT("to_actor_id"), P), MemberPath(P, TEXT("to_actor_id")));
        Result.possession.push_back(std::move(Value));
    }

    const FJsonArray& Contacts = ReadArray(Root, TEXT("contacts"), Path, 0, MaximumPlaybackContacts);
    for (int32 Index = 0; Index < Contacts.Num(); ++Index)
    {
        const FString P = ElementPath(TEXT("playback.contacts"), Index);
        const FJsonObjectPtr Item = Object(Contacts[Index], P);
        Fields(Item, {TEXT("id"), TEXT("kind"), TEXT("time_seconds"), TEXT("actor_id"),
            TEXT("ball_id"), TEXT("action_id"), TEXT("source_motion_id"), TEXT("source_contact_seconds"),
            TEXT("limb"), TEXT("calibration_status")}, {}, P);
        Result.contacts.push_back(Contact{
            ReadId(Item, TEXT("id"), P),
            Utf8(ReadString(Item, TEXT("kind"), P)),
            ReadNumber(Item, TEXT("time_seconds"), P),
            ReadId(Item, TEXT("actor_id"), P),
            ReadId(Item, TEXT("ball_id"), P),
            ReadId(Item, TEXT("action_id"), P),
            ReadId(Item, TEXT("source_motion_id"), P),
            ReadNumber(Item, TEXT("source_contact_seconds"), P),
            ReadId(Item, TEXT("limb"), P),
            Utf8(ReadString(Item, TEXT("calibration_status"), P))});
    }
    return Result;
}

} // namespace

bool ParsePlaybackJson(const FString& Json, PlanInput& OutInput, FString& OutError)
{
    try
    {
        PreflightJson(Json);
        FJsonObjectPtr Root;
        const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
        RequireRead(FJsonSerializer::Deserialize(Reader, Root, FJsonSerializer::EFlags::None) && Root.IsValid(),
            TEXT("Unable to deserialize playback JSON: ") + Reader->GetErrorMessage());
        PlanInput Candidate = ReadPlan(Root);
        (void)PlaybackPlan::Create(Candidate);
        OutInput = std::move(Candidate);
        OutError.Reset();
        return true;
    }
    catch (const FPlaybackReadFailure& Failure)
    {
        OutError = Failure.Message;
    }
    catch (const ValidationError& Failure)
    {
        OutError = TEXT("Playback semantic validation failed: ") + FString(UTF8_TO_TCHAR(Failure.what()));
    }
    catch (const std::exception& Failure)
    {
        OutError = TEXT("Playback loading failed: ") + FString(UTF8_TO_TCHAR(Failure.what()));
    }
    return false;
}

} // namespace SquadPadRuntime
