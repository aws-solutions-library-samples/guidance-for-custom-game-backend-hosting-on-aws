// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "AppleIdLoginTest.generated.h"


UCLASS( ClassGroup=(Custom), meta=(BlueprintSpawnableComponent) )
class UNREALSAMPLE_API UAppleIdLoginTest : public UActorComponent
{
	GENERATED_BODY()

public:	
	// Sets default values for this component's properties
	UAppleIdLoginTest();

	void OnGuestLoginResultCallback(const UserInfo& userInfo);
	void OnLinkAppleIdResultCallback(const UserInfo& userInfo);
	void OnLoginWithAppleId(const UserInfo& userInfo);
	void OnLoginOrRefreshErrorCallback(const FString& error);

    UPROPERTY(EditAnywhere)
    FString m_loginEndpoint;

protected:
	// Called when the game starts
	virtual void BeginPlay() override;

public:	
	// Called every frame
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

};
