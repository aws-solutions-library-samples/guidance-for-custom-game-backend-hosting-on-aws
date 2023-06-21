// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/SaveGame.h"
#include "PlayerDataSave.generated.h"

/**
 * 
 */
UCLASS()
class UNREALSAMPLE_API UPlayerDataSave : public USaveGame
{
	GENERATED_BODY()

public:
	UPROPERTY()
	FString UserId;

	UPROPERTY()
	FString GuestSecret;
};
