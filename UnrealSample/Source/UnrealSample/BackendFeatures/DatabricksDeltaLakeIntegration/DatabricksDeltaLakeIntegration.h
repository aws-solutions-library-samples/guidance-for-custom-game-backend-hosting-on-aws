// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "DatabricksDeltaLakeIntegration.generated.h"

// A simple helper class to send events to the Data Pipeline.
class EventDataSender
{
private:
	FString m_dataPipelineEndpoint;
	UAWSGameSDK *m_awsGameSDK;
	FString DoubleDigit(int number);
public:

	EventDataSender(const FString& dataPipelineEndpoint, UAWSGameSDK *awsGameSDK);
    void SendEvent(const FString& event_id, const FString& event_type, const FString& event_data, UAWSGameSDK::FRequestComplete callback);
};


// The sample level script class for logging in and sending events 

UCLASS( ClassGroup=(Custom), meta=(BlueprintSpawnableComponent) )
class UNREALSAMPLE_API UDatabricksDeltaLakeIntegration : public UActorComponent
{
	GENERATED_BODY()

public:	
	// Sets default values for this component's properties
	UDatabricksDeltaLakeIntegration();

    void OnLoginResultCallback(const UserInfo& userInfo);
	void OnLoginOrRefreshErrorCallback(const FString& error);
	void OnSendTestEventResponse(const FString& response);

    UPROPERTY(EditAnywhere)
    FString m_loginEndpoint;
	UPROPERTY(EditAnywhere)
    FString m_dataPipelineEndpoint;

protected:
	// Called when the game starts
	virtual void BeginPlay() override;

public:	
	// Called every frame
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

		
};
