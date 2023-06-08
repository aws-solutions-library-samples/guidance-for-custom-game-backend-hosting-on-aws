// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "PlayerDataManager.generated.h"

/**
 * 
 */
UCLASS()
class UNREALSAMPLE_API UPlayerDataManager : public UGameInstanceSubsystem
{
	GENERATED_BODY()

	FString SaveSlot = "PlayerData";

	class UPlayerDataSave* PlayerData;
	
public:

	virtual void Initialize(FSubsystemCollectionBase& Collection) override;

	void SaveGameData(FString userId, FString guest_secret);
	class UPlayerDataSave* LoadGameData();
};
