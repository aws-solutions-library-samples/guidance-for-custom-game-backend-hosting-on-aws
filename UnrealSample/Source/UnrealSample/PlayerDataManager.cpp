// Fill out your copyright notice in the Description page of Project Settings.


#include "PlayerDataManager.h"
#include "Engine.h"
#include "PlayerDataSave.h"

void UPlayerDataManager::Initialize(FSubsystemCollectionBase& Collection){
    UE_LOG(LogTemp, Display, TEXT("Init PlayerDataManager Subsystem") );

    // Load the Player Data
	PlayerData = Cast<UPlayerDataSave>(UGameplayStatics::LoadGameFromSlot(SaveSlot, 0));
}

void UPlayerDataManager::SaveGameData(FString UserId, FString GuestSecret)
{
    if(!PlayerData)
    {
        PlayerData = Cast<UPlayerDataSave>(UGameplayStatics::CreateSaveGameObject(UPlayerDataSave::StaticClass()));
    }

    PlayerData->UserId = UserId;
    PlayerData->GuestSecret = GuestSecret;

    UGameplayStatics::SaveGameToSlot(PlayerData, SaveSlot, 0);

}

UPlayerDataSave* UPlayerDataManager::LoadGameData()
{
    // Return the player data loaded in Initialize or null
	if (PlayerData)
		return PlayerData;
	else
		return nullptr;
}
