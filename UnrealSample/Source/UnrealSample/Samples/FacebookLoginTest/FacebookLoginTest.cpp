// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "FacebookLoginTest.h"
#include "../../AWSGameSDK/AWSGameSDK.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"

// Sets default values for this component's properties
UFacebookLoginTest::UFacebookLoginTest()
{
	// Set this component to be initialized when the game starts, and to be ticked every frame.  You can turn these features
	// off to improve performance if you don't need them.
	PrimaryComponentTick.bCanEverTick = true;

	// ...
}


// Called when the game starts
void UFacebookLoginTest::BeginPlay()
{
	Super::BeginPlay();

	// Get the subsystems
    UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();

    // Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
	AWSGameSDK->Init(this->m_loginEndpoint);
	AWSGameSDK->OnLoginFailure.AddUObject(this, &UFacebookLoginTest::OnLoginOrRefreshErrorCallback);

	// Define the OnLoginResult callback
	UAWSGameSDK::FLoginComplete loginCallback;
    loginCallback.BindUObject(this, &UFacebookLoginTest::OnGuestLoginResultCallback);

	// Login as a new guest user first
    AWSGameSDK->LoginAsNewGuestUser(loginCallback);
	
}

// Called when there is an error with login or token refresh. You will need to handle logging in again here
void UFacebookLoginTest::OnLoginOrRefreshErrorCallback(const FString& errorMessage){
    UE_LOG(LogTemp, Display, TEXT("Received login error: %s \n"), *errorMessage);
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Red, FString::Printf(TEXT("Received login error: \n %s \n"), *errorMessage), false, FVector2D(1.5f,1.5f));

    // NOTE:  You will need to handle logging in again here
}

// Called when guest login is done
void UFacebookLoginTest::OnGuestLoginResultCallback(const UserInfo& userInfo){
    UE_LOG(LogTemp, Display, TEXT("Received guest login response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test linking facebook id to the existing guest account
	UAWSGameSDK::FLoginComplete onLinkFacebookIdCallback;
    onLinkFacebookIdCallback.BindUObject(this, &UFacebookLoginTest::OnLinkFacebookIdResultCallback);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: You need a valid Facebook access token and user ID that can be obtained by logging in with a Facebook account
    //       We don't currently have any recommendations on how to integrate the Facebook SDK with Unreal Engine 5, but there are some community options available
	AWSGameSDK->LinkFacebookIdToCurrentUser("tokenHere", "userIDHere", onLinkFacebookIdCallback);
}

void UFacebookLoginTest::OnLinkFacebookIdResultCallback(const UserInfo& userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received Facebook ID linking response: %s \n"), *userInfo.ToString());
    if(GEngine)
            GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received Facebook ID linking response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));

    // Test logging in with existing facebook_id
	UAWSGameSDK::FLoginComplete onFacebookLoginCallback;
    onFacebookLoginCallback.BindUObject(this, &UFacebookLoginTest::OnLoginWithFacebook);
	UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
    UAWSGameSDK* AWSGameSDK = GameInstance->GetSubsystem<UAWSGameSDK>();

    // NOTE: As above, you're expected to have a valid Facebook access token and user ID
    AWSGameSDK->LoginWithFacebookAccessToken("tokenHere", "userIdHere", onFacebookLoginCallback);
}

void UFacebookLoginTest::OnLoginWithFacebook(const UserInfo& userInfo){

    UE_LOG(LogTemp, Display, TEXT("Received Facebook login response: %s \n"), *userInfo.ToString());
    if(GEngine)
        GEngine->AddOnScreenDebugMessage(-1, 30.0f, FColor::Black, FString::Printf(TEXT("Received Facebook login response: \n %s \n"), *userInfo.ToString()), false, FVector2D(1.5f,1.5f));
}

// Called every frame
void UFacebookLoginTest::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);

	// ...
}

