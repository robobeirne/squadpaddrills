#include "SquadPadPlaybackReader.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

// Native tests. These require an actual Unreal Editor build and the real
// project fixture. No stub execution or packaged-iPhone result is implied.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSquadPadPlaybackReaderContractTest,
    "SquadPad.Runtime.PlaybackReader.Contract",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FSquadPadPlaybackReaderContractTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    using namespace SquadPadRuntime;
    using namespace squadpad::playback;

    const FString FixturePath = FPaths::Combine(FPaths::ProjectDir(),
        TEXT("Fixtures/Playback/first_pass.playback.json"));
    FString Fixture;
    if (!TestTrue(TEXT("Actual compiled first-pass fixture is available"),
        FFileHelper::LoadFileToString(Fixture, *FixturePath)))
    {
        return false;
    }

    PlanInput Input;
    FString Error = TEXT("previous error");
    if (!TestTrue(TEXT("Actual fixture passes Unreal JSON ingress"),
        ParsePlaybackJson(Fixture, Input, Error)))
    {
        AddError(Error);
        return false;
    }
    TestTrue(TEXT("Success clears error"), Error.IsEmpty());
    TestTrue(TEXT("Source fixture identity survives"),
        Input.id == "rondo-4v1.level-1.segment-0.pass-receive");
    TestEqual(TEXT("Actual fixture actor count"), static_cast<int32>(Input.actors.size()), 2);
    TestEqual(TEXT("Actual fixture ball count"), static_cast<int32>(Input.balls.size()), 1);
    TestEqual(TEXT("Actual fixture contact count"), static_cast<int32>(Input.contacts.size()), 2);
    if (Input.contacts.size() != 2 || Input.possession.size() != 3)
    {
        AddError(TEXT("Actual fixture does not have its expected contact/ownership records"));
        return false;
    }
    TestTrue(TEXT("Fractional kick time survives JSON conversion"),
        FMath::Abs(Input.contacts[0].time_seconds - 1.39262125) <= 1e-10);
    TestTrue(TEXT("Fractional receiving time survives JSON conversion"),
        FMath::Abs(Input.contacts[1].time_seconds - 2.43762125) <= 1e-10);
    TestTrue(TEXT("Explicit null transit owner becomes the optional empty sentinel"),
        Input.possession[1].value.owner_id.empty());
    TestTrue(TEXT("Case-sensitive receiver ID survives"),
        Input.possession[2].value.owner_id == "B");

    PlaybackController Controller(PlaybackPlan::Create(Input));
    const StepResult Receive = Controller.Seek(2.43762125);
    TestTrue(TEXT("Seeking parsed input has no contact effects"), Receive.crossed_contact_ids.empty());
    TestTrue(TEXT("Parsed exact reception selects receiver possession"),
        Receive.frame.balls.size() == 1 && Receive.frame.balls[0].possession.owner_id == "B");

    auto Rejected = [this](const TCHAR* Label, const FString& Candidate)
    {
        PlanInput Prior;
        Prior.id = "previous.loaded.plan";
        Prior.duration_seconds = 17.0;
        Prior.motions.push_back(Motion{"preserved.motion", 2.0});
        FString Failure;
        TestFalse(Label, ParsePlaybackJson(Candidate, Prior, Failure));
        TestTrue(TEXT("Rejected load supplies an error"), !Failure.IsEmpty());
        TestTrue(TEXT("Rejected load leaves existing input untouched"),
            Prior.id == "previous.loaded.plan" && Prior.duration_seconds == 17.0 &&
            Prior.motions.size() == 1 && Prior.motions[0].id == "preserved.motion" &&
            Prior.actors.empty() && Prior.balls.empty());
    };

    auto AddRootMember = [&Fixture](const FString& Member)
    {
        FString Result = Fixture;
        const int32 RootStart = Result.Find(TEXT("{"), ESearchCase::CaseSensitive);
        Result.InsertAt(RootStart + 1, Member);
        return Result;
    };

    auto Mutated = [this, &Fixture](TFunctionRef<void(const TSharedPtr<FJsonObject>&)> Change)
    {
        TSharedPtr<FJsonObject> Root;
        const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Fixture);
        if (!FJsonSerializer::Deserialize(Reader, Root, FJsonSerializer::EFlags::None) || !Root.IsValid())
        {
            AddError(TEXT("Test setup failed to decode the real fixture"));
            return FString();
        }
        Change(Root);
        FString Result;
        const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Result);
        if (!FJsonSerializer::Serialize(Root, Writer, true))
        {
            AddError(TEXT("Test setup failed to encode its deliberate mutation"));
        }
        return Result;
    };

    Rejected(TEXT("Duplicate root members fail before DOM overwrite"),
        AddRootMember(TEXT("\"id\":\"shadow\",")));
    Rejected(TEXT("Escaped duplicate names fail before DOM overwrite"),
        AddRootMember(TEXT("\"\\u0069d\":\"shadow\",")));
    Rejected(TEXT("Unknown root fields fail"),
        AddRootMember(TEXT("\"unsupported\":true,")));
    Rejected(TEXT("Required root field spelling is case-sensitive"),
        Fixture.Replace(TEXT("\"schema\""), TEXT("\"Schema\""), ESearchCase::CaseSensitive));
    Rejected(TEXT("Nested field spelling is case-sensitive"),
        Fixture.Replace(TEXT("\"play_rate\""), TEXT("\"Play_Rate\""), ESearchCase::CaseSensitive));
    Rejected(TEXT("Case-variant unknown root member cannot collapse in the DOM"),
        AddRootMember(TEXT("\"Schema\":\"squadpad.playback.v1\",")));
    FString TrailingCaseVariant = Fixture;
    TrailingCaseVariant.InsertAt(TrailingCaseVariant.Find(TEXT("}"), ESearchCase::CaseSensitive,
        ESearchDir::FromEnd), TEXT(",\"Schema\":\"squadpad.playback.v1\""));
    Rejected(TEXT("Case-variant unknown member fails after the canonical member too"), TrailingCaseVariant);
    Rejected(TEXT("Malformed JSON fails"), TEXT("{\"id\":"));
    Rejected(TEXT("A second root value fails"), Fixture + TEXT(" {}"));
    Rejected(TEXT("Trailing non-JSON data fails"), Fixture + TEXT(" trailing"));
    Rejected(TEXT("Root array fails"), TEXT("[]"));
    Rejected(TEXT("Null root fails"), TEXT("null"));

    Rejected(TEXT("Required field cannot disappear"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->RemoveField(TEXT("root_ownership"));
    }));
    Rejected(TEXT("Boolean cannot replace a number"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->SetBoolField(TEXT("duration_seconds"), true);
    }));
    Rejected(TEXT("Number cannot replace a loop boolean"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->GetArrayField(TEXT("actors"))[0]->AsObject()->GetArrayField(TEXT("sections"))[0]
            ->AsObject()->SetNumberField(TEXT("loop"), 1.0);
    }));
    Rejected(TEXT("Semantic unknown motion reference fails"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->GetArrayField(TEXT("actors"))[0]->AsObject()->GetArrayField(TEXT("sections"))[0]
            ->AsObject()->SetStringField(TEXT("source_motion_id"), TEXT("missing.motion"));
    }));
    Rejected(TEXT("Unknown nested action field fails"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->GetArrayField(TEXT("actors"))[0]->AsObject()->GetArrayField(TEXT("sections"))[0]
            ->AsObject()->SetStringField(TEXT("unsupported"), TEXT("ignored"));
    }));
    Rejected(TEXT("Empty string cannot impersonate JSON null"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->GetArrayField(TEXT("possession"))[0]->AsObject()->SetStringField(TEXT("from_actor_id"), TEXT(""));
    }));
    Rejected(TEXT("Unknown possession enum fails"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->GetArrayField(TEXT("possession"))[0]->AsObject()->SetStringField(TEXT("state"), TEXT("OWNED"));
    }));
    Rejected(TEXT("Metadata must be object-shaped"), Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->SetBoolField(TEXT("evidence"), true);
    }));

    // New root metadata replaces existing fields through the parsed DOM, then
    // raw insertion deliberately tests lexical evidence a DOM would discard.
    const FString WithoutEvidence = Mutated([](const TSharedPtr<FJsonObject>& Root)
    {
        Root->RemoveField(TEXT("evidence"));
    });
    auto WithEvidence = [&WithoutEvidence](const FString& Value)
    {
        FString Result = WithoutEvidence;
        Result.InsertAt(Result.Find(TEXT("{"), ESearchCase::CaseSensitive) + 1,
            FString(TEXT("\"evidence\":")) + Value + TEXT(","));
        return Result;
    };
    Rejected(TEXT("Duplicate nested metadata fields fail"), WithEvidence(TEXT("{\"same\":1,\"same\":2}")));
    Rejected(TEXT("Nonfinite-range metadata number fails"), WithEvidence(TEXT("{\"number\":1e9999}")));
    Rejected(TEXT("Explicit NaN fails"), WithEvidence(TEXT("{\"number\":NaN}")));
    TestTrue(TEXT("Metadata names remain case-sensitive and content stays open"),
        ParsePlaybackJson(WithEvidence(TEXT("{\"A\":1,\"a\":2,\"nested\":[null,true,\"text\"]}")), Input, Error));

    Rejected(TEXT("Payload character limit is enforced"),
        FString::ChrN(MaximumPlaybackJsonCharacters + 1, TEXT(' ')));
    FString TooDeep = TEXT("{\"evidence\":");
    for (int32 Index = 0; Index < MaximumPlaybackJsonDepth; ++Index)
    {
        TooDeep += TEXT("[");
    }
    TooDeep += TEXT("0");
    for (int32 Index = 0; Index < MaximumPlaybackJsonDepth; ++Index)
    {
        TooDeep += TEXT("]");
    }
    TooDeep += TEXT("}");
    Rejected(TEXT("Nesting limit is enforced before DOM construction"), TooDeep);

    FString TooManyTokens = TEXT("{\"evidence\":[");
    TooManyTokens.Reserve(2 * MaximumPlaybackJsonTokens + 32);
    for (int32 Index = 0; Index < MaximumPlaybackJsonTokens; ++Index)
    {
        TooManyTokens += TEXT("0,");
    }
    TooManyTokens += TEXT("0]}");
    Rejected(TEXT("Notation-token limit is enforced before DOM construction"), TooManyTokens);
    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
