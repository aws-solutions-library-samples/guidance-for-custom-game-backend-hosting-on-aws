// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "DatabricksDeltaLakeIntegration.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "../../PlayerDataSave.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "../../PlayerDataManager.h"


/// EVENT SENDER /////

EventDataSender::EventDataSender(const FString& dataPipelineEndpoint, UAWSGameSDK* awsGameSDK){
    this->m_dataPipelineEndpoint = dataPipelineEndpoint;
    this->m_awsGameSDK = awsGameSDK;
}

// Method that returns a double digit version of a number
FString EventDataSender::DoubleDigit(int number){
    return FString::Printf(TEXT("%02d"), number);
}

// Method for sending an event to the data pipeline
void EventDataSender::SendEvent(const FString& event_id, const FString& event_type, const FString& event_data, UAWSGameSDK::FRequestComplete callback){

    // Get the FDateTime now
    auto now = FDateTime::Now();
    
    FString now_string = FString::Printf(TEXT("%i-%s-%s %s:%s:%s"),now.GetYear(), *this->DoubleDigit(now.GetMonth()), *this->DoubleDigit(now.GetDay()), *this->DoubleDigit(now.GetHour()), *this->DoubleDigit(now.GetMinute()), *this->DoubleDigit(now.GetSecond())); // We're targeting this format 2024-02-22 03:03:02

    auto event_json = FString::Printf(TEXT("{\"event_id\": \"%s\", \"event_type\": \"%s\", \"updated_at\": \"%s\", \"event_data\": \"%s\"}"), *event_id, *event_type, *now_string, *event_data);

    UE_LOG(LogTemp, Display, TEXT("Sending event: %s \n"), *event_json);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Sending event: %s \n"), *event_json), false, FVector2D(1.5f, 1.5f));

    // Send the event to the pipeline
    this->m_awsGameSDK->BackendPostRequest(this->m_dataPipelineEndpoint, "put-record",event_json, callback);
}


//// MAIN CLASS //////

// Sets default values for this component's properties
UDatabricksDeltaLakeIntegration::UDatabricksDeltaLakeIntegration()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}

// Called when the game starts
void UDatabricksDeltaLakeIntegration::BeginPlay()
{
	Super::BeginPlay();

    // Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    
    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
	AWSGameSDK->Init(this->m_loginEndpoint);
	AWSGameSDK->OnLoginFailure.AddUObject(this, &UDatabricksDeltaLakeIntegration::OnLoginOrRefreshErrorCallback);

    // Define the OnLoginResult callback
	UAWSGameSDK::FLoginComplete loginCallback;
	loginCallback.BindUObject(this, &UDatabricksDeltaLakeIntegration::OnLoginResultCallback);
    
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
void UDatabricksDeltaLakeIntegration::OnLoginOrRefreshErrorCallback(const FString& errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when login is done
void UDatabricksDeltaLakeIntegration::OnLoginResultCallback(const UserInfo& userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Save the player data
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    PlayerDataManager->SaveGameData(userInfo.user_id, userInfo.guest_secret);

    // NOTE: You could get the expiration in seconds for refresh token (and modify to FDateTime) as well as the refresh token itself from the userInfo,
    // and login next time with the refresh token itself. This can be done by calling AWSGameSDK->LoginWithRefreshToken(refreshToken, loginCallback);

    // Test sending some events to the data pipeline
    UAWSGameSDK::FRequestComplete sendTestEventCallback;
	sendTestEventCallback.BindUObject(this, &UDatabricksDeltaLakeIntegration::OnSendTestEventResponse);
    auto eventSender = EventDataSender(this->m_dataPipelineEndpoint, AWSGameSDK);
	eventSender.SendEvent("00006", "Login", "Player logged in", sendTestEventCallback);
    eventSender.SendEvent("00006", "CollectedItem", "Magic Sword", sendTestEventCallback);
    eventSender.SendEvent("00006", "Killed Enemy", "Spider", sendTestEventCallback);
    eventSender.SendEvent("00006", "Logout", "Player logged out", sendTestEventCallback);
}

// Called when event send gets a response
void UDatabricksDeltaLakeIntegration::OnSendTestEventResponse(const FString& response){
    UE_LOG(LogTemp, Display, TEXT("put-record response: %s \n"), *response);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("put-record response: %s \n"), *response), false, FVector2D(1.5f,1.5f));

}
// Called every frame
void UDatabricksDeltaLakeIntegration::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

