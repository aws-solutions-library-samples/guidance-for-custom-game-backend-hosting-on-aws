// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "AWSGameSDK.h"
#include "CoreMinimal.h"
#include "Engine/World.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "GenericPlatform/GenericPlatformHttp.h"

void UAWSGameSDK::Initialize(FSubsystemCollectionBase& Collection){
    UE_LOG(LogTemp, Display, TEXT("Init AWSGameSDK Subsystem") );

	m_httpRetryManager = MakeShared<FHttpRetrySystem::FManager>(
		FHttpRetrySystem::FRetryLimitCountSetting(2),
		FHttpRetrySystem::FRetryTimeoutRelativeSecondsSetting()
		);

	FTimerDelegate updateDelegate;
	updateDelegate.BindWeakLambda(this, [this]()
		{
			m_httpRetryManager->Update();
		});

	const float tickFrequency = 0.25f;
	const bool repeat = true;
	GetWorld()->GetTimerManager().SetTimer(this->m_updateTimerHandle, updateDelegate, tickFrequency, repeat);
}

void UAWSGameSDK::Deinitialize(){

}

/// PUBLIC API ///

void UAWSGameSDK::Init(const FString& loginEndpoint){
    this->m_loginEndpoint = loginEndpoint;
}

void UAWSGameSDK::LoginAsNewGuestUser(FLoginComplete callback){
    
    // Login as new guest (NULL, NULL for user_id and guest_secret)
    this->LoginAsGuestUser("", "", callback);
}

void UAWSGameSDK::LoginAsGuestUser(const FString& user_id, const FString& guest_secret, FLoginComplete callback){

    // Define an FString, FString map for the query parameters
    TMap<FString, FString> queryParameters;

    if(user_id != "" && guest_secret != "") {
        queryParameters.Add(TEXT("user_id"), user_id);
        queryParameters.Add(TEXT("guest_secret"), guest_secret);
    }

    // CallRestApiGet with login endpoint and resource login-as-guest
    this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-as-guest",  queryParameters, callback);
}

void UAWSGameSDK::LoginWithRefreshToken(const FString& refreshToken, FLoginComplete callback){

    // Define an FString, FString map for the query parameters
    TMap<FString, FString> queryParameters;

    if(refreshToken != "") {
        queryParameters.Add(TEXT("refresh_token"), refreshToken);
    }

    // CallRestApiGet with login endpoint and resource refresh-access-token
    this->CallRestApiGetUserLogin(this->m_loginEndpoint, "refresh-access-token",  queryParameters, callback);
}

void UAWSGameSDK::RefreshAccessToken(FLoginComplete callback){
    
    // Check that we have login endpoint
    if(this->m_loginEndpoint == "") {
        UE_LOG(LogTemp, Error, TEXT("Login endpoint is not set"));
        return;
    }
    // Check that we have refresh token
    if(this->m_userInfo.refresh_token == "") {
        UE_LOG(LogTemp, Error, TEXT("Refresh token is not set"));
        return;
    }

    // Call the refresh-access-token endpoint
    this->LoginWithRefreshToken(this->m_userInfo.refresh_token, callback);
}

void UAWSGameSDK::LoginWithAppleIdToken(const FString& appleAuthToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Apple ID auth token"));
    this->LoginWithAppleId(appleAuthToken, "", false, callback);
}

void UAWSGameSDK::LinkAppleIdToCurrentUser(const FString& appleAuthToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Apple ID to existing user"));
    this->LoginWithAppleId(appleAuthToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::LoginWithSteamToken(const FString& steamAuthToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Steam auth token"));
    this->LoginWithSteam(steamAuthToken, "", false, callback);
}

void UAWSGameSDK::LinkSteamIdToCurrentUser(const FString& steamAuthToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Steam ID to existing user"));
    this->LoginWithSteam(steamAuthToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::LoginWithGooglePlayToken(const FString& googlePlayToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Google Play auth token"));
    this->LoginWithGooglePlay(googlePlayToken, "", false, callback);
}

void UAWSGameSDK::LinkGooglePlayIdToCurrentUser(const FString& googlePlayToken, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Google Play ID to existing user"));
    this->LoginWithGooglePlay(googlePlayToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::LoginWithFacebookAccessToken(const FString& facebookAccessToken, const FString& facebookUserId, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Facebook auth token"));
    this->LoginWithFacebook(facebookAccessToken, facebookUserId, "", false, callback);
}
void UAWSGameSDK::LinkFacebookIdToCurrentUser(const FString& facebookAccessToken, const FString& facebookUserId, FLoginComplete callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Facebook ID to existing user"));
    this->LoginWithFacebook(facebookAccessToken, facebookUserId, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::BackendGetRequest(const FString& url, const FString& resource, const TMap<FString, FString>& queryParameters, FRequestComplete callback){
    // If Url doesn't end with '/', add it
    FString urlWithTrailingSlash = url;
    if(url.EndsWith(TEXT("/")) == false) {
        urlWithTrailingSlash += TEXT("/");
    }

    this->CallRestApiGetWithAuth(urlWithTrailingSlash, resource, queryParameters, callback);
}


/// PRIVATE ///

void UAWSGameSDK::CallRestApiGetUserLogin(const FString& url, const FString& resource, const TMap<FString, FString>& queryParameters, FLoginComplete callback) {

    FString resourceForUrl = resource;
    if(queryParameters.Num() > 0) {
        resourceForUrl += "?";
        //Iterate the query parameters and add url encoded values
        for(auto& queryParameter : queryParameters) {
            resourceForUrl += "&" + queryParameter.Key + "=" + FGenericPlatformHttp::UrlEncode(queryParameter.Value);
        }
    }

    FString fullUrl = url + resourceForUrl;
    
    UE_LOG(LogTemp, Display, TEXT("Making API request: %s"), *fullUrl );

    TSharedRef<IHttpRequest, ESPMode::ThreadSafe> pRequest = NewBackendRequest_NoAuth();
    pRequest->SetVerb(TEXT("GET"));
    pRequest->SetURL(fullUrl);

    // Callback executed after request is complete
    pRequest->OnProcessRequestComplete().BindWeakLambda(this, [this, callback](FHttpRequestPtr pRequest, FHttpResponsePtr pResponse, bool connectedSuccessfully)
    {
        if (connectedSuccessfully) {

            // Get the response content
            FString responseString = pResponse->GetContentAsString();
            UE_LOG(LogTemp, Display, TEXT("Received response: %s"), *responseString );

            // Parse the JSON response
            TSharedPtr<FJsonObject> JsonObject;
            TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(responseString);

            // Deserialize the json data given Reader and the actual object to deserialize
            if (FJsonSerializer::Deserialize(Reader, JsonObject)) {
                
                FString UserId = "", GuestSecret = "", AccessToken = "", AppleId = "", SteamId = "", GooglePlayId = "", FacebookId = "", RefreshToken  = "";
                int32 AccessTokenExpiration, RefreshTokenExpiration;

                // First get all the fields from the existing user info
                UserId = this->m_userInfo.user_id;
                GuestSecret = this->m_userInfo.guest_secret;
                AccessToken = this->m_userInfo.auth_token;
                AppleId = this->m_userInfo.apple_id;
                SteamId = this->m_userInfo.steam_id;
                GooglePlayId = this->m_userInfo.google_play_id;
                FacebookId = this->m_userInfo.facebook_id;
                RefreshToken = this->m_userInfo.refresh_token;
                AccessTokenExpiration = this->m_userInfo.auth_token_expires_in;
                RefreshTokenExpiration = this->m_userInfo.refresh_token_expires_in;

                // We don't always get all fields (guest secret is only for guest login, apple_id on for Apple etc.)
                // But we always need to have the userID in a successful response
                JsonObject->TryGetStringField("user_id", UserId);
                if(UserId == "") {
                    UE_LOG(LogTemp, Error, TEXT("No user_id in response"));
                    return;
                }

                // Then try to get any fields we received in the response
                JsonObject->TryGetStringField("guest_secret", GuestSecret);
                JsonObject->TryGetStringField("auth_token", AccessToken);
                JsonObject->TryGetStringField("apple_id", AppleId);
                JsonObject->TryGetStringField("steam_id", SteamId);
                JsonObject->TryGetStringField("google_play_id", GooglePlayId);
                JsonObject->TryGetStringField("facebook_id", FacebookId);
                JsonObject->TryGetStringField("refresh_token", RefreshToken);
                JsonObject->TryGetNumberField("auth_token_expires_in", AccessTokenExpiration);
                JsonObject->TryGetNumberField("refresh_token_expires_in", RefreshTokenExpiration);

                UE_LOG(LogTemp, Log, TEXT("user_id: %s"), *UserId);
                UE_LOG(LogTemp, Log, TEXT("guest_secret: %s"), *GuestSecret);
                UE_LOG(LogTemp, Log, TEXT("auth_token: %s"), *AccessToken);
                UE_LOG(LogTemp, Log, TEXT("apple_id: %s"), *AppleId);
                UE_LOG(LogTemp, Log, TEXT("steam_id: %s"), *SteamId);
                UE_LOG(LogTemp, Log, TEXT("google_play_id: %s"), *GooglePlayId);
                UE_LOG(LogTemp, Log, TEXT("facebook_id: %s"), *FacebookId);
                UE_LOG(LogTemp, Log, TEXT("refresh_token: %s"), *RefreshToken);
                UE_LOG(LogTemp, Log, TEXT("auth_token_expires_in: %d"), AccessTokenExpiration);
                UE_LOG(LogTemp, Log, TEXT("refresh_token_expires_in: %d"), RefreshTokenExpiration);
                // Define a UserInfo struct to store the data
                this->m_userInfo.user_id = UserId;
                this->m_userInfo.guest_secret = GuestSecret;
                this->m_userInfo.auth_token = AccessToken;
                this->m_userInfo.apple_id = AppleId;
                this->m_userInfo.steam_id = SteamId;
                this->m_userInfo.google_play_id = GooglePlayId;
                this->m_userInfo.facebook_id = FacebookId;
                this->m_userInfo.refresh_token = RefreshToken;
                this->m_userInfo.auth_token_expires_in = AccessTokenExpiration;
                this->m_userInfo.refresh_token_expires_in = RefreshTokenExpiration;

				ScheduleTokenRefresh(FGenericPlatformMath::Min(AccessTokenExpiration, RefreshTokenExpiration));

                // Send the info back to the original requester through the callback (if set)
				callback.ExecuteIfBound(this->m_userInfo);
            }
            else {
                // Failed to deserialize the JSON response, report a login error back to the client
                UE_LOG(LogTemp, Error, TEXT("Failed to deserialize JSON response."));
				OnLoginFailure.Broadcast(TEXT("Failed to login or refresh token"));
            }
        }
        else {
            switch (pRequest->GetStatus()) {
            case EHttpRequestStatus::Failed_ConnectionError:
                UE_LOG(LogTemp, Error, TEXT("Connection failed."));
				OnLoginFailure.Broadcast(TEXT("Connection failed."));
            default:
                UE_LOG(LogTemp, Error, TEXT("Request failed."));
				OnLoginFailure.Broadcast(TEXT("Connection failed."));
            }
        }
    });
     // Submit the request for processing
     pRequest->ProcessRequest();
}

void UAWSGameSDK::LoginWithAppleId(const FString& appleAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback){
    
    // Set up query params for the request (either new/existing AppleID user or linking)
    TMap<FString, FString> queryParameters;

    // Linking apple ID to existing user
    if(appleAuthToken != "" && authToken != "" && linkToExistingUser) {
        queryParameters.Add(TEXT("apple_auth_token"), appleAuthToken);
        queryParameters.Add(TEXT("auth_token"), authToken);
        if(linkToExistingUser)
            queryParameters.Add(TEXT("link_to_existing_user"), TEXT("Yes"));

        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-apple-id",  queryParameters, callback);
    }

    // Getting an existing or new user
    else if(appleAuthToken != "") {
        queryParameters.Add(TEXT("apple_auth_token"), appleAuthToken);
        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-apple-id",  queryParameters, callback);
    }
}

void UAWSGameSDK::LoginWithSteam(const FString& steamAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback){
    
    // Set up query params for the request (either new/existing SteamID user or linking)
    TMap<FString, FString> queryParameters;

    // Linking steam ID to existing user
    if(steamAuthToken != "" && authToken != "" && linkToExistingUser) {
        queryParameters.Add(TEXT("steam_auth_token"), steamAuthToken);
        queryParameters.Add(TEXT("auth_token"), authToken);
        if(linkToExistingUser)
            queryParameters.Add(TEXT("link_to_existing_user"), TEXT("Yes"));

        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-steam",  queryParameters, callback);
    }

    // Getting an existing or new user
    else if(steamAuthToken != "") {
        queryParameters.Add(TEXT("steam_auth_token"), steamAuthToken);
        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-steam",  queryParameters, callback);
    }
}

void UAWSGameSDK::LoginWithGooglePlay(const FString& googlePlayAuthToken, const FString& authToken, bool linkToExistingUser, FLoginComplete callback){

    // Set up query params for the request (either new/existing GooglePlay user or linking)
    TMap<FString, FString> queryParameters;

    // Linking Google Play ID to existing user
    if(googlePlayAuthToken != "" && authToken != "" && linkToExistingUser) {
        queryParameters.Add(TEXT("google_play_auth_token"), googlePlayAuthToken);
        queryParameters.Add(TEXT("auth_token"), authToken);
        if(linkToExistingUser)
            queryParameters.Add(TEXT("link_to_existing_user"), TEXT("Yes"));

        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-google-play",  queryParameters, callback);
    }

    // Getting an existing or new user
    else if(googlePlayAuthToken != "") {
        queryParameters.Add(TEXT("google_play_auth_token"), googlePlayAuthToken);
        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-google-play",  queryParameters, callback);
    }
}

void UAWSGameSDK::LoginWithFacebook(const FString& facebookAccessToken, const FString& facebookUserId, const FString& authToken, bool linkToExistingUser, FLoginComplete callback){
    
    // Set up query params for the request (either new/existing Facebook user or linking)
    TMap<FString, FString> queryParameters;

    // Linking Facebook ID to existing user
    if(facebookAccessToken != "" && facebookUserId != "" && authToken != "" && linkToExistingUser) {
        queryParameters.Add(TEXT("facebook_access_token"), facebookAccessToken);
        queryParameters.Add(TEXT("facebook_user_id"), facebookUserId);
        queryParameters.Add(TEXT("auth_token"), authToken);
        if(linkToExistingUser)
            queryParameters.Add(TEXT("link_to_existing_user"), TEXT("Yes"));

        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-facebook",  queryParameters, callback);
    }

    // Getting an existing or new user
    else if(facebookAccessToken != "" && facebookUserId != "") {
        queryParameters.Add(TEXT("facebook_access_token"), facebookAccessToken);
        queryParameters.Add(TEXT("facebook_user_id"), facebookUserId);
        this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-with-facebook",  queryParameters, callback);
    }
}

TSharedRef<IHttpRequest, ESPMode::ThreadSafe> UAWSGameSDK::NewBackendRequest()
{
	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> request = NewBackendRequest_NoAuth();
	request->SetHeader(TEXT("Authorization"), this->m_userInfo.auth_token);

	return request;
}

TSharedRef<IHttpRequest, ESPMode::ThreadSafe> UAWSGameSDK::NewBackendRequest_NoAuth()
{
	return FHttpModule::Get().CreateRequest();

	static const TSet<int32> retryCodes(TArray<int32>({ 400, 500, 501, 502, 503, 504 }));
	static const TSet<FName> retryVerbs(TArray<FName>({ FName(TEXT("GET")), FName(TEXT("HEAD")), FName(TEXT("POST")) }));

	if (m_httpRetryManager == nullptr) {
		// Fallback to the Http Manager without retries
		return FHttpModule::Get().CreateRequest();
	}

	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> request = m_httpRetryManager->CreateRequest(
		2,
		FHttpRetrySystem::FRetryTimeoutRelativeSecondsSetting(),
		retryCodes,
		retryVerbs);
	request->SetTimeout(30.0f);

	return request;
}

void UAWSGameSDK::CallRestApiGetWithAuth(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, FRequestComplete callback){
    
    FString resourceForUrl = resource;
    if(queryParameters.Num() > 0) {
        resourceForUrl += "?";
        //Iterate the query parameters
        for(auto& queryParameter : queryParameters) {
            resourceForUrl += "&" + queryParameter.Key + "=" + FGenericPlatformHttp::UrlEncode(queryParameter.Value);
        }
    }

    FString fullUrl = url + resourceForUrl;
    
    UE_LOG(LogTemp, Display, TEXT("Making authenticated API request: %s"), *fullUrl );

    TSharedRef<IHttpRequest, ESPMode::ThreadSafe> pRequest = NewBackendRequest();
    pRequest->SetVerb(TEXT("GET"));
    pRequest->SetURL(fullUrl);

    // Callback executed after request is complete
    pRequest->OnProcessRequestComplete().BindWeakLambda(this, [this, callback](FHttpRequestPtr pRequest, FHttpResponsePtr pResponse, bool connectedSuccessfully)
    {
        if (connectedSuccessfully) {

            // Get the response content
            FString responseString = pResponse->GetContentAsString();
            UE_LOG(LogTemp, Display, TEXT("Received response: %s"), *responseString );

            // Send the info back to the original requester through the callback
            callback.ExecuteIfBound(responseString);
        }
        else {
            switch (pRequest->GetStatus()) {
            case EHttpRequestStatus::Failed_ConnectionError:
                UE_LOG(LogTemp, Error, TEXT("Connection failed."));
            default:
                UE_LOG(LogTemp, Error, TEXT("Request failed."));
            }
        }
    });
     // Submit the request for processing
     pRequest->ProcessRequest();
}

void UAWSGameSDK::ScheduleTokenRefresh(float expiresIn)
{
	// Don't refresh if we have less than 30 seconds to avoid DDOSing our own service
	if (expiresIn < 30.0f)
	{
		OnLoginFailure.Broadcast(TEXT("Access token lasts less than 30 seconds, will not refresh"));
	}

	FTimerDelegate refreshDelegate;
	refreshDelegate.BindWeakLambda(this, [this]()
		{
			FLoginComplete noCallback;
			this->RefreshAccessToken(noCallback);
		});

	// Refresh the token 10 seconds before it expires
	const float refreshTimeBeforeExpiration = 10.0f;
	const bool repeat = false;
	GetWorld()->GetTimerManager().SetTimer(this->m_refreshTokenTimer, refreshDelegate, expiresIn - refreshTimeBeforeExpiration, repeat);
}