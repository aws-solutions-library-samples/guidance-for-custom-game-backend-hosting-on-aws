// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "SteamLoginTest.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"

// Sets default values for this component's properties
USteamLoginTest::USteamLoginTest()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}


// Called when the game starts
void USteamLoginTest::BeginPlay()
{
	Super::BeginPlay();

	// Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();

    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
    auto loginOrRefreshErrorCallback = std::bind(&USteamLoginTest::OnLoginOrRefreshErrorCallback, this, std::placeholders::_1);
    AWSGameSDK->Init(this->m_loginEndpoint, loginOrRefreshErrorCallback);

	// Define the OnLoginResult callback
    auto loginCallback = std::bind(&USteamLoginTest::OnGuestLoginResultCallback, this, std::placeholders::_1);

	// Login as a new guest user first
    AWSGameSDK->LoginAsNewGuestUser(loginCallback);
	
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void USteamLoginTest::OnLoginOrRefreshErrorCallback(FString errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when guest login is done
void USteamLoginTest::OnGuestLoginResultCallback(UserInfo userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received guest login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test linking steam id to the existing guest account
    auto onLinkSteamIdCallback = std::bind(&USteamLoginTest::OnLinkSteamIdResultCallback, this, std::placeholders::_1);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: You need a Steam token here retrieved with either GetAuthTicketForWebApi (new) or GetAuthSessionTicket (old): https://partner.steamgames.com/doc/features/auth
    //       See Unreal Steam Online subsystem documentation for more details on integration: https://docs.unrealengine.com/5.2/en-US/online-subsystem-steam-interface-in-unreal-engine/
	AWSGameSDK->LinkSteamIdToCurrentUser("tokenHere", onLinkSteamIdCallback);
}

void USteamLoginTest::OnLinkSteamIdResultCallback(UserInfo userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received Steam ID linking response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received Steam ID linking response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test logging in with existing steam_id
    auto onSteamIdLoginCallback = std::bind(&USteamLoginTest::OnLoginWithSteam, this, std::placeholders::_1);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: As above, you're expected to have a valid Steam token here
    AWSGameSDK->LoginWithSteamToken("tokenHere", onSteamIdLoginCallback);
}

void USteamLoginTest::OnLoginWithSteam(UserInfo userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received Steam login response: %s \n"), *userInfo.ToString());
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received Steam login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));
}

// Called every frame
void USteamLoginTest::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

