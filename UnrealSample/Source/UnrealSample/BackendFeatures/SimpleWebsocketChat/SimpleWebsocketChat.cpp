// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: MIT-0


#include "SimpleWebsocketChat.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "../../AWSGameSDK/WebSocketClient.h"
#include "../../PlayerDataSave.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "../../PlayerDataManager.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "GenericPlatform/GenericPlatformHttp.h"

//#include "WebSocketsModule.h"
//#include "IWebSocket.h"

#include <stdio.h>
#include <locale.h>
#include <stdlib.h>
#include <uchar.h>

// MAIN CLASS

// Sets default values for this component's properties
USimpleWebsocketChat::USimpleWebsocketChat()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}

// Called when the game starts
void USimpleWebsocketChat::BeginPlay()
{
	Super::BeginPlay();

    // Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    
    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
	AWSGameSDK->Init(this->m_loginEndpoint);
	AWSGameSDK->OnLoginFailure.AddUObject(this, &USimpleWebsocketChat::OnLoginOrRefreshErrorCallback);

    // Define the OnLoginResult callback
	UAWSGameSDK::FLoginComplete loginCallback;
	loginCallback.BindUObject(this, &USimpleWebsocketChat::OnLoginResultCallback);
    
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

// Called every frame
void USimpleWebsocketChat::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void USimpleWebsocketChat::OnLoginOrRefreshErrorCallback(const FString& errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when login is done
void USimpleWebsocketChat::OnLoginResultCallback(const UserInfo& userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received login response: %s \n"), *userInfo.ToString());
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.user_id), false, FVector2D(1.5f,1.5f));

    // Save the player data
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UPlayerDataManager* PlayerDataManager = GameInstance->GetSubsystem<UPlayerDataManager>(); 
    PlayerDataManager->SaveGameData(userInfo.user_id, userInfo.guest_secret);

    // Create a new WebSocket and bind callback
    WebSocketClient::FOnMessageReceived messageCallback;
	messageCallback.BindUObject(this, &USimpleWebsocketChat::OnMessageReceived);
    this->m_webSocketClient = new WebSocketClient(userInfo.auth_token, this->m_websocketEndpointUrl, messageCallback);

    // Test the Websocket client
    this->SetUserName("John Doe");
    this->JoinChannel("global");
    this->SendMessage("global", "Hello, World!");
    this->LeaveChannel("global");
}

void USimpleWebsocketChat::OnMessageReceived(const FString& message){

    // Show the message
    UE_LOG(LogTemp, Display, TEXT("Received message: %s \n"), *message);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received message: \n %s \n"), *message), false, FVector2D(1.5f, 1.5f));
}

// Websocket messages

void USimpleWebsocketChat::SetUserName(const FString& username){
    
    // Create a JSON object for the set-name message
    TSharedPtr<FJsonObject> JsonMessage = MakeShareable(new FJsonObject());
    JsonMessage->SetStringField("type", "set-name");
    TSharedPtr<FJsonObject> JsonPayload = MakeShareable(new FJsonObject());
    JsonPayload->SetStringField("username", username);
    JsonMessage->SetObjectField("payload", JsonPayload);
    FString JsonString;
    TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonString);
    FJsonSerializer::Serialize(JsonMessage.ToSharedRef(), JsonWriter);

    this->m_webSocketClient->SendMessage(JsonString);
}

void USimpleWebsocketChat::JoinChannel(const FString& channelName){

    // Create a JSON object for the join-channel message
    TSharedPtr<FJsonObject> JsonMessage = MakeShareable(new FJsonObject());
    JsonMessage->SetStringField("type", "join");
    TSharedPtr<FJsonObject> JsonPayload = MakeShareable(new FJsonObject());
    JsonPayload->SetStringField("channel", channelName);
    JsonMessage->SetObjectField("payload", JsonPayload);
    FString JsonString;
    TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonString);
    FJsonSerializer::Serialize(JsonMessage.ToSharedRef(), JsonWriter);

    this->m_webSocketClient->SendMessage(JsonString);
}

void USimpleWebsocketChat::LeaveChannel(const FString& channelName){

    // Create a JSON object for the leave-channel message
    TSharedPtr<FJsonObject> JsonMessage = MakeShareable(new FJsonObject());
    JsonMessage->SetStringField("type", "leave");
    TSharedPtr<FJsonObject> JsonPayload = MakeShareable(new FJsonObject());
    JsonPayload->SetStringField("channel", channelName);
    JsonMessage->SetObjectField("payload", JsonPayload);
    FString JsonString;
    TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonString);
    FJsonSerializer::Serialize(JsonMessage.ToSharedRef(), JsonWriter);

    this->m_webSocketClient->SendMessage(JsonString);
}

void USimpleWebsocketChat::SendMessage(const FString& channelName, const FString& message){

    // Create a JSON object for the send-message message
    TSharedPtr<FJsonObject> JsonMessage = MakeShareable(new FJsonObject());
    JsonMessage->SetStringField("type", "message");
    TSharedPtr<FJsonObject> JsonPayload = MakeShareable(new FJsonObject());
    JsonPayload->SetStringField("channel", channelName);
    JsonPayload->SetStringField("message", message);
    JsonMessage->SetObjectField("payload", JsonPayload);
    FString JsonString;
    TSharedRef<TJsonWriter<>> JsonWriter = TJsonWriterFactory<>::Create(&JsonString);
    FJsonSerializer::Serialize(JsonMessage.ToSharedRef(), JsonWriter);

    this->m_webSocketClient->SendMessage(JsonString);
}