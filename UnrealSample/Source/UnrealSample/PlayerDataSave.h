// Fill out your copyright notice in the Description page of Project Settings.

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
