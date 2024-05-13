// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: MIT-0


#include "SimpleWebsocketChat.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "../../PlayerDataSave.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "../../PlayerDataManager.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "GenericPlatform/GenericPlatformHttp.h"

#include "WebSocketsModule.h"
#include "IWebSocket.h"

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

    const FString ServerURL = this->m_websocketEndpointUrl+"/?auth_token="+*userInfo.auth_token; // Your server URL. You can use ws, wss or wss+insecure.
    const FString ServerProtocol = TEXT("wss");              // The WebServer protocol you want to use.
        
    TSharedPtr<IWebSocket> Socket = FWebSocketsModule::Get().CreateWebSocket(ServerURL, ServerProtocol);

    // We bind all available events
    Socket->OnConnected().AddLambda([]() -> void {
        // This code will run once connected.
        // Log resposne
        UE_LOG(LogTemp, Display, TEXT("Connected"));
    });
        
    Socket->OnConnectionError().AddLambda([](const FString & Error) -> void {
        // This code will run if the connection failed. Check Error to see what happened.
    });
        
    Socket->OnClosed().AddLambda([](int32 StatusCode, const FString& Reason, bool bWasClean) -> void {
        // This code will run when the connection to the server has been terminated.
        // Because of an error or a call to Socket->Close().
    });
        
    Socket->OnMessage().AddLambda([](const FString & Message) -> void {
        // This code will run when we receive a string message from the server.
        UE_LOG(LogTemp, Display, TEXT("Received message: %s"), *Message);
        if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received message: %s \n"), *Message), false, FVector2D(1.5f, 1.5f));
    });
        
    Socket->OnRawMessage().AddLambda([](const void* Data, SIZE_T Size, SIZE_T BytesRemaining) -> void {
        // This code will run when we receive a raw (binary) message from the server.
    });
        
    Socket->OnMessageSent().AddLambda([](const FString& MessageString) -> void {
        // This code is called after we sent a message to the server.
    });
        
    // And we finally connect to the server. 
    Socket->Connect();
}