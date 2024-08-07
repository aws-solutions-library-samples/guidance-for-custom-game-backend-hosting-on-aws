// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "../../AWSGameSDK/WebSocketClient.h"
#include "SimpleWebsocketChat.generated.h"


UCLASS( ClassGroup=(Custom), meta=(BlueprintSpawnableComponent) )
class UNREALSAMPLE_API USimpleWebsocketChat : public UActorComponent
{
	GENERATED_BODY()

public:	
	// Sets default values for this component's properties
	USimpleWebsocketChat();

	void OnLoginResultCallback(const UserInfo& userInfo);
	void OnLoginOrRefreshErrorCallback(const FString& error);

	void OnMessageReceived(const FString& message);

	UPROPERTY(EditAnywhere)
    FString m_loginEndpoint;
	UPROPERTY(EditAnywhere)
    FString m_websocketEndpointUrl;

protected:
	// Called when the game starts
	virtual void BeginPlay() override;

public:	
	// Called every frame
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;
	
	void SetUserName(const FString& userName);
	void JoinChannel(const FString& channelName);
	void LeaveChannel(const FString& channelName);
	void SendMessage(const FString& channelName, const FString& message);

	WebSocketClient *m_webSocketClient;

private:

};
