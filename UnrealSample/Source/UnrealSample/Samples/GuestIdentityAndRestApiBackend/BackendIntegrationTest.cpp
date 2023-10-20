// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "BackendIntegrationTest.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "../../PlayerDataSave.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "../../PlayerDataManager.h"

// Sets default values for this component's properties
UBackendIntegrationTest::UBackendIntegrationTest()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}

// Called when the game starts
void UBackendIntegrationTest::BeginPlay()
{
	Super::BeginPlay();

    // Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    
    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
	AWSGameSDK->Init(this->m_loginEndpoint);
	AWSGameSDK->OnLoginFailure.AddUObject(this, &UBackendIntegrationTest::OnLoginOrRefreshErrorCallback);

    // Define the OnLoginResult callback
	UAWSGameSDK::FLoginComplete loginCallback;
	loginCallback.BindUObject(this, &UBackendIntegrationTest::OnLoginResultCallback);
    
    // Get player data if we have any 
    auto playerData = PlayerDataManager->LoadGameData();

    // If not saved player data, login as a new player
    if(playerData == nullptr){
        UE_LOG(LogTemp, Display, TEXT("No player data yet, request a new identity"));
        if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 15.0f, FColor::Black, TEXT("No player data yet, request a new identity"));	

        // Login as a guest user
        AWSGameSDK->LoginAsNewGuestUser(loginCallback);
    }
    else {
        UE_LOG(LogTemp, Display, TEXT("Existing player data\n user_id: %s \n guest_secret: %s"), *playerData->UserId, *playerData->GuestSecret);
        if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Existing player data\n user_id: %s \n guest_secret: %s"), *playerData->UserId, *playerData->GuestSecret),false, FVector2D(1.5f,1.5f));

        AWSGameSDK->LoginAsGuestUser(playerData->UserId, playerData->GuestSecret, loginCallback);
    }
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void UBackendIntegrationTest::OnLoginOrRefreshErrorCallback(const FString& errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when login is done
void UBackendIntegrationTest::OnLoginResultCallback(const UserInfo& userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Save the player data
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    PlayerDataManager->SaveGameData(userInfo.user_id, userInfo.guest_secret);

    // NOTE: You could get the expiration in seconds for refresh token (and modify to FDateTime) as well as the refresh token itself from the userInfo,
    // and login next time with the refresh token itself. This can be done by calling AWSGameSDK->LoginWithRefreshToken(refreshToken, loginCallback);

    // Test calling our custom backend system to set player data
	UAWSGameSDK::FRequestComplete setPlayerDataCallback;
	setPlayerDataCallback.BindUObject(this, &UBackendIntegrationTest::OnSetPlayerDataResponse);
    TMap<FString,FString> params;
    params.Add("player_name", "John Doe");
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    AWSGameSDK->BackendGetRequest(this->m_sampleHttpApiEndpointUrl, "set-player-data", params, setPlayerDataCallback);
}

// Called when set-player-data gets a response
void UBackendIntegrationTest::OnSetPlayerDataResponse(const FString& response){
    UE_LOG(LogTemp, Display, TEXT("Received set-player-data response: %s \n"), *response);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received set-player-data response: \n %s \n"), *response), false, FVector2D(1.5f,1.5f));

    // Test getting the same player data
	UAWSGameSDK::FRequestComplete getPlayerDataCallback;
	getPlayerDataCallback.BindUObject(this, &UBackendIntegrationTest::OnGetPlayerDataResponse);
    TMap<FString,FString> params; // We don't have any params for this call
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    AWSGameSDK->BackendGetRequest(this->m_sampleHttpApiEndpointUrl, "get-player-data", params, getPlayerDataCallback);
}

// Called when get-player-data gets a response
void UBackendIntegrationTest::OnGetPlayerDataResponse(const FString& response){
    UE_LOG(LogTemp, Display, TEXT("Received get-player-data response: %s \n"), *response);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received get-player-data response: \n %s \n"), *response), false, FVector2D(1.5f,1.5f));
}

// Called every frame
void UBackendIntegrationTest::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

