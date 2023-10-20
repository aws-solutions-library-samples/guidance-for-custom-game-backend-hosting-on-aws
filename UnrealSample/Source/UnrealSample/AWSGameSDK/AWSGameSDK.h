// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#pragma once

#include "CoreMinimal.h"
#include "HttpRetrySystem.h"
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
        FString facebook_id;
        FString refresh_token;
        int auth_token_expires_in;
        int refresh_token_expires_in;
        // Define ToString() method
		FString ToString() const
		{
			return FString::Printf(TEXT("user_id=%s\n guest_secret=%s\n auth_token=%s\n apple_id=%s\n steam_id=%s\n google_play_id=%s \n facebook_id=%s \n refresh_token=%s \n auth_token_expires_in=%d \n refresh_token_expires_in=%d"),
				*user_id, *guest_secret, *auth_token, *apple_id, *steam_id, *google_play_id, *facebook_id, *refresh_token, auth_token_expires_in, refresh_token_expires_in);
		}
};

/**
 * 
 */
UCLASS()
class UNREALSAMPLE_API UAWSGameSDK : public UGameInstanceSubsystem
{
	GENERATED_BODY()
    
public:

    UserInfo m_userInfo;

    // Begin USubsystem
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
    // End USubsystem
    
    void Init(const FString& loginEndpoint);

	DECLARE_DELEGATE_OneParam(FLoginComplete, const UserInfo& userInfo);
    
    void LoginAsNewGuestUser(FLoginComplete callback);
    void LoginAsGuestUser(const FString& user_id, const FString& guest_secret, FLoginComplete callback);
    void LoginWithAppleIdToken(const FString& appleAuthToken, FLoginComplete callback);
    void LinkAppleIdToCurrentUser(const FString& appleAuthToken, FLoginComplete callback);
    void LoginWithSteamToken(const FString& steamAuthToken, FLoginComplete callback);
    void LinkSteamIdToCurrentUser(const FString& steamAuthToken, FLoginComplete callback);
    void LoginWithGooglePlayToken(const FString& googlePlayAuthToken, FLoginComplete callback);
    void LinkGooglePlayIdToCurrentUser(const FString& googlePlayAuthToken, FLoginComplete callback);
    void LoginWithFacebookAccessToken(const FString& facebookAccessToken, const FString& facebookUserId, FLoginComplete callback);
    void LinkFacebookIdToCurrentUser(const FString& facebookAccessToken, const FString& facebookUserId, FLoginComplete callback);
    void LoginWithRefreshToken(const FString& refreshToken, FLoginComplete callback);
    void RefreshAccessToken(FLoginComplete callback);

	// Simplified GET interface with parameters on the query string
	DECLARE_DELEGATE_OneParam(FRequestComplete, const FString& response);
	void BackendGetRequest(const FString& url, const FString& resource, const TMap<FString, FString>& queryParameters, FRequestComplete callback);

	// Alternate interface allowing usage of IHttpRequest for different verbs, headers, etc.
	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> NewBackendRequest();

	DECLARE_MULTICAST_DELEGATE_OneParam(FLoginFailure, const FString&);
	FLoginFailure OnLoginFailure;

private:
    
    FString m_loginEndpoint;
	FTimerHandle m_updateTimerHandle;
	FTimerHandle m_refreshTokenTimer;
	TSharedPtr<class FHttpRetrySystem::FManager> m_httpRetryManager;
    
	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> NewBackendRequest_NoAuth();
    void CallRestApiGetUserLogin(const FString& url, const FString& resource, const TMap<FString, FString>& queryParameters, FLoginComplete callback);
    void LoginWithAppleId(const FString& appleAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback);
    void LoginWithSteam(const FString& steamAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback);
    void LoginWithGooglePlay(const FString& googlePlayAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback);
    void LoginWithFacebook(const FString& facebookAccessToken, const FString& facebookUserId, const FString& authToken, bool linkToExistingUser, FLoginComplete callback);
    void CallRestApiGetWithAuth(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, FRequestComplete callback);
	void ScheduleTokenRefresh(float expiresIn);
};
