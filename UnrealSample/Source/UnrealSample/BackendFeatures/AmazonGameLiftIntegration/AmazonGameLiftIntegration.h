// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "AmazonGameLiftIntegration.generated.h"


class LatencyMeasurer : public FRunnable
{
public:
    LatencyMeasurer();

	FString latencyInMs = "";

    //override Init,Run and Stop.
    virtual bool Init() override;
	virtual uint32 Run() override;
	virtual void Exit() override;
	virtual void Stop() override;

	bool SynchronousRequest(TSharedRef<IHttpRequest> HttpRequest);
	float GetLatency(FString Location);
};


UCLASS( ClassGroup=(Custom), meta=(BlueprintSpawnableComponent) )
class UNREALSAMPLE_API UAmazonGameLiftIntegration : public UActorComponent
{
	GENERATED_BODY()

public:	
	// Sets default values for this component's properties
	UAmazonGameLiftIntegration();

	void OnLoginResultCallback(const UserInfo& userInfo);
	void OnLoginOrRefreshErrorCallback(const FString& error);

	void OnRequestMatchmakingResponse(const FString& response);
	void OnGetMatchStatusResponse(const FString& response);

	UPROPERTY(EditAnywhere)
    FString m_loginEndpoint;
	UPROPERTY(EditAnywhere)
    FString m_gameliftIntegrationBackendEndpointUrl;

protected:
	// Called when the game starts
	virtual void BeginPlay() override;

public:	
	// Called every frame
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;
	

private:

	LatencyMeasurer *m_latencyMeasurer = NULL;
	bool m_matchmakingStarted = false;
	bool m_loginSucceeded = false;
	FString m_ticketId;
	FTimerHandle m_getMatchStatusTimerHandle;

	void ScheduleGetMatchStatus(float waitTime);

};
