// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "AppleIdLoginTest.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"

// Sets default values for this component's properties
UAppleIdLoginTest::UAppleIdLoginTest()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}


// Called when the game starts
void UAppleIdLoginTest::BeginPlay()
{
	Super::BeginPlay();

	// Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();

    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
	AWSGameSDK->Init(this->m_loginEndpoint);
	AWSGameSDK->OnLoginFailure.AddUObject(this, &UAppleIdLoginTest::OnLoginOrRefreshErrorCallback);

	// Define the OnLoginResult callback
	UAWSGameSDK::FLoginComplete loginCallback;
	loginCallback.BindUObject(this, &UAppleIdLoginTest::OnGuestLoginResultCallback);

	// Login as a new guest user first
    AWSGameSDK->LoginAsNewGuestUser(loginCallback);
	
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void UAppleIdLoginTest::OnLoginOrRefreshErrorCallback(const FString& errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when guest login is done
void UAppleIdLoginTest::OnGuestLoginResultCallback(const UserInfo& userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received guest login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test linking apple id to the existing guest account
	UAWSGameSDK::FLoginComplete onLinkAppleIdCallback;
	onLinkAppleIdCallback.BindUObject(this, &UAppleIdLoginTest::OnLinkAppleIdResultCallback);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: You need to use an appropriate Apple SDK plugin to sign in with Apple and get the Apple ID token for the user and pass it here
    AWSGameSDK->LinkAppleIdToCurrentUser("eyYourToken", onLinkAppleIdCallback);
}

void UAppleIdLoginTest::OnLinkAppleIdResultCallback(const UserInfo& userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received apple ID linking response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received apple ID linking response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test logging in with existing apple_id
	UAWSGameSDK::FLoginComplete onAppleIdLoginCallback;
	onAppleIdLoginCallback.BindUObject(this, &UAppleIdLoginTest::OnLoginWithAppleId);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: You need to use an appropriate Apple SDK plugin to sign in with Apple and get the Apple ID token for the user and pass it here
    AWSGameSDK->LoginWithAppleIdToken("eyYourToken", onAppleIdLoginCallback);
}

void UAppleIdLoginTest::OnLoginWithAppleId(const UserInfo& userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received apple ID login response: %s \n"), *userInfo.ToString());
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received apple ID login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));
}

// Called every frame
void UAppleIdLoginTest::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

