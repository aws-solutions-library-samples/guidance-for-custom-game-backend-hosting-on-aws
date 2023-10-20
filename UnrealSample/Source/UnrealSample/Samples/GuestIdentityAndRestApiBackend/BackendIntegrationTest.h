// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "BackendIntegrationTest.generated.h"

UCLASS( ClassGroup=(Custom), meta=(BlueprintSpawnableComponent) )
class UNREALSAMPLE_API UBackendIntegrationTest : public UActorComponent
{
	GENERATED_BODY()

public:	
	// Sets default values for this component's properties
	UBackendIntegrationTest();

    void OnLoginResultCallback(const UserInfo& userInfo);
	void OnLoginOrRefreshErrorCallback(const FString& error);
	void OnSetPlayerDataResponse(const FString& response);
    void OnGetPlayerDataResponse(const FString& response);

    UPROPERTY(EditAnywhere)
    FString m_loginEndpoint;
	UPROPERTY(EditAnywhere)
    FString m_sampleHttpApiEndpointUrl;

protected:
	// Called when the game starts
	virtual void BeginPlay() override;

public:	
	// Called every frame
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

		
};
