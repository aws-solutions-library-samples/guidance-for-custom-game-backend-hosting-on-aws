// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/Subsystem.h"
#include "AWSGameSDK.generated.h"


// Define a struct for user  data
struct UserInfo
{
        FString user_id;
        FString guest_secret;
        FString auth_token;
        FString apple_id;
        FString steam_id;
        FString google_play_id;
        FString refresh_token;
        int auth_token_expires_in;
        int refresh_token_expires_in;
        // Define ToString() method
        FString ToString()
        {
            return FString::Printf(TEXT("user_id=%s\n guest_secret=%s\n auth_token=%s\n apple_id=%s\n steam_id=%s\n google_play_id=%s \n refresh_token=%s \n auth_token_expires_in=%d \n refresh_token_expires_in=%d"), 
                                        *user_id, *guest_secret, *auth_token, *apple_id, *steam_id, *google_play_id, *refresh_token, auth_token_expires_in, refresh_token_expires_in);
        }
};

/**
 * 
 */
UCLASS()
class UNREALSAMPLE_API UAWSGameSDK : public UGameInstanceSubsystem, public FTickableGameObject
{
	GENERATED_BODY()
    
public:

    UserInfo m_userInfo;

    // Begin USubsystem
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
    // End USubsystem
    
    void Init(const FString& loginEndpoint, std::function<void(const FString&)> loginOrRefreshErrorCallback);
    
    void LoginAsNewGuestUser(std::function<void(UserInfo userInfo)> callback);
    void LoginAsGuestUser(const FString& user_id, const FString& guest_secret, std::function<void(UserInfo userInfo)> callback);
    void BackendGetRequest(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, std::function<void(FString response)> callback);
    void LoginWithAppleIdToken(const FString& appleAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LinkAppleIdToCurrentUser(const FString& appleAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LoginWithSteamToken(const FString& steamAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LinkSteamIdToCurrentUser(const FString& steamAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LoginWithGooglePlayToken(const FString& googlePlayAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LinkGooglePlayIdToCurrentUser(const FString& googlePlayAuthToken, std::function<void(UserInfo userInfo)> callback);
    void LoginWithRefreshToken(const FString& refreshToken, std::function<void(UserInfo userInfo)> callback);
    void RefreshAccessToken(std::function<void(UserInfo userInfo)> callback);

    //Tickable object methods
    virtual UWorld* GetTickableGameObjectWorld() const override { return GetWorld(); }
	virtual ETickableTickType GetTickableTickType() const override { return ETickableTickType::Always; }
	virtual bool IsAllowedToTick() const override { return true; }
	virtual void Tick(float DeltaTime) override;
	virtual TStatId GetStatId() const override { return TStatId(); }

private:
    
    FString m_loginEndpoint;
    std::function<void(const FString&)> m_loginOrRefreshErrorCallback;

    FDateTime AuthTokenExpirationUTC = FDateTime::MinValue();
    FDateTime RefreshTokenExpirationUTC = FDateTime::MinValue();
    
    void CallRestApiGetUserLogin(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, std::function<void(UserInfo userInfo)> callback);
    void LoginWithAppleId(const FString& appleAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback);
    void LoginWithSteam(const FString& steamAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback);
    void LoginWithGooglePlay(const FString& googlePlayAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback);
    void CallRestApiGetWithAuth(const FString& url, const FString& resource, const FString& authToken, TMap<FString, FString> queryParameters, std::function<void(FString response)> callback);
    
};
