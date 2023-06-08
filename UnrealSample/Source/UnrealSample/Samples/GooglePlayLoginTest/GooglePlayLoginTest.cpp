// Fill out your copyright notice in the Description page of Project Settings.


#include "GooglePlayLoginTest.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"

// Sets default values for this component's properties
UGooglePlayLoginTest::UGooglePlayLoginTest()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}


// Called when the game starts
void UGooglePlayLoginTest::BeginPlay()
{
	Super::BeginPlay();

	// Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();

    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
    auto loginOrRefreshErrorCallback = std::bind(&UGooglePlayLoginTest::OnLoginOrRefreshErrorCallback, this, std::placeholders::_1);
    AWSGameSDK->Init(this->m_loginEndpoint, loginOrRefreshErrorCallback);

	// Define the OnLoginResult callback
    auto loginCallback = std::bind(&UGooglePlayLoginTest::OnGuestLoginResultCallback, this, std::placeholders::_1);

	// Login as a new guest user first
    AWSGameSDK->LoginAsNewGuestUser(loginCallback);
	
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void UGooglePlayLoginTest::OnLoginOrRefreshErrorCallback(FString errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when guest login is done
void UGooglePlayLoginTest::OnGuestLoginResultCallback(UserInfo userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received guest login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test linking google play ID to the existing guest account
    auto onLinkGooglePlayIdCallback = std::bind(&UGooglePlayLoginTest::OnLinkGooglePlayIdResultCallback, this, std::placeholders::_1);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: You need a Google Play token here retrieved with RequestServerSideAccess call within Google Play v2 SDK
    //       At the time of writing, Google Play v2 SDK was not available for C++
    //       The documentation states you can reach out to Google if interested on v2: https://developers.google.com/games/services/cpp/GettingStartedNativeClient
	//       Another option is to work around to integrate with the native Java SDK: https://developers.google.com/games/services/android/signin 
    //       There are some forum discussions on running Android Java code with Unreal: https://forums.unrealengine.com/t/how-to-execute-java-android-code-from-c/312543/10
    //       The correct single use code to be sent to backend is something like "4/0AbCD..."
    AWSGameSDK->LinkGooglePlayIdToCurrentUser("TOKENHERE", onLinkGooglePlayIdCallback);

    // You would use AWSGameSDK->LoginWithGooglePlayToken(token, callback) to login with a new or existing Google Play linked user
}

void UGooglePlayLoginTest::OnLinkGooglePlayIdResultCallback(UserInfo userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received Google Play ID linking response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received Google Play ID linking response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));
}

// Called every frame
void UGooglePlayLoginTest::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

