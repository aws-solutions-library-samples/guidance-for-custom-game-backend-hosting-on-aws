// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

#include "AWSGameSDK.h"
#include "CoreMinimal.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "GenericPlatform/GenericPlatformHttp.h"

void UAWSGameSDK::Initialize(FSubsystemCollectionBase& Collection){
    UE_LOG(LogTemp, Display, TEXT("Init AWSGameSDK Subsystem") );
}

void UAWSGameSDK::Deinitialize(){

}

// Called every frame
void UAWSGameSDK::Tick(float DeltaTime)
{
    // If AuthTokenExpirationUTC is not min value and difference to current UTCNow is less than 5 seconds, refresh the token
    if(this->AuthTokenExpirationUTC != FDateTime::MinValue() && this->AuthTokenExpirationUTC - FDateTime::UtcNow() < FTimespan(0, 0, 5)) {
        UE_LOG(LogTemp, Display, TEXT("AWSGameSDK: Access token expiring, refresh."));
        // Reset the expiration time
        this->AuthTokenExpirationUTC = FDateTime::MinValue();
        // Call the refresh token method
        this->RefreshAccessToken(nullptr);
    }
}

/// PUBLIC API ///

void UAWSGameSDK::Init(const FString& loginEndpoint, std::function<void(const FString&)> loginErrorCallback){
    this->m_loginEndpoint = loginEndpoint;
    this->m_loginOrRefreshErrorCallback = loginErrorCallback;
}

void UAWSGameSDK::LoginAsNewGuestUser(std::function<void(UserInfo userInfo)> callback){
    
    // Login as new guest (NULL, NULL for user_id and guest_secret)
    this->LoginAsGuestUser("", "", callback);
}

void UAWSGameSDK::LoginAsGuestUser(const FString& user_id, const FString& guest_secret, std::function<void(UserInfo userInfo)> callback){

    // Define an FString, FString map for the query parameters
    TMap<FString, FString> queryParameters;

    if(user_id != "" && guest_secret != "") {
        queryParameters.Add(TEXT("user_id"), user_id);
        queryParameters.Add(TEXT("guest_secret"), guest_secret);
    }

    // CallRestApiGet with login endpoint and resource login-as-guest
    this->CallRestApiGetUserLogin(this->m_loginEndpoint, "login-as-guest",  queryParameters, callback);
}

void UAWSGameSDK::LoginWithRefreshToken(const FString& refreshToken, std::function<void(UserInfo userInfo)> callback){

    // Define an FString, FString map for the query parameters
    TMap<FString, FString> queryParameters;

    if(refreshToken != "") {
        queryParameters.Add(TEXT("refresh_token"), refreshToken);
    }

    // CallRestApiGet with login endpoint and resource refresh-access-token
    this->CallRestApiGetUserLogin(this->m_loginEndpoint, "refresh-access-token",  queryParameters, callback);
}

void UAWSGameSDK::RefreshAccessToken(std::function<void(UserInfo userInfo)> callback){
    
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

void UAWSGameSDK::LoginWithAppleIdToken(const FString& appleAuthToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Apple ID auth token"));
    this->LoginWithAppleId(appleAuthToken, "", false, callback);
}

void UAWSGameSDK::LinkAppleIdToCurrentUser(const FString& appleAuthToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Apple ID to existing user"));
    this->LoginWithAppleId(appleAuthToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::LoginWithSteamToken(const FString& steamAuthToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Steam auth token"));
    this->LoginWithSteam(steamAuthToken, "", false, callback);
}

void UAWSGameSDK::LinkSteamIdToCurrentUser(const FString& steamAuthToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Steam ID to existing user"));
    this->LoginWithSteam(steamAuthToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::LoginWithGooglePlayToken(const FString& googlePlayToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Logging in with Google Play auth token"));
    this->LoginWithGooglePlay(googlePlayToken, "", false, callback);
}

void UAWSGameSDK::LinkGooglePlayIdToCurrentUser(const FString& googlePlayToken, std::function<void(UserInfo userInfo)> callback){
    UE_LOG(LogTemp, Display, TEXT("Linking Google Play ID to existing user"));
    this->LoginWithGooglePlay(googlePlayToken, this->m_userInfo.auth_token, true, callback);
}

void UAWSGameSDK::BackendGetRequest(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, std::function<void(FString response)> callback){
    // If Url doesn't end with '/', add it
    FString urlWithTrailingSlash = url;
    if(url.EndsWith(TEXT("/")) == false) {
        urlWithTrailingSlash += TEXT("/");
    }

    this->CallRestApiGetWithAuth(urlWithTrailingSlash, resource, this->m_userInfo.auth_token, queryParameters, callback);
}


/// PRIVATE ///

void UAWSGameSDK::CallRestApiGetUserLogin(const FString& url, const FString& resource, TMap<FString, FString> queryParameters, std::function<void(UserInfo userInfo)> callback) {

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

    FHttpModule& httpModule = FHttpModule::Get();
    TSharedRef<IHttpRequest, ESPMode::ThreadSafe> pRequest = httpModule.CreateRequest();
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
                
                FString UserId = "", GuestSecret = "", AccessToken = "", AppleId = "", SteamId = "", GooglePlayId = "", RefreshToken  = "";
                int32 AccessTokenExpiration, RefreshTokenExpiration;

                // First get all the fields from the existing user info
                UserId = this->m_userInfo.user_id;
                GuestSecret = this->m_userInfo.guest_secret;
                AccessToken = this->m_userInfo.auth_token;
                AppleId = this->m_userInfo.apple_id;
                SteamId = this->m_userInfo.steam_id;
                GooglePlayId = this->m_userInfo.google_play_id;
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
                JsonObject->TryGetStringField("refresh_token", RefreshToken);
                JsonObject->TryGetNumberField("auth_token_expires_in", AccessTokenExpiration);
                JsonObject->TryGetNumberField("refresh_token_expires_in", RefreshTokenExpiration);

                UE_LOG(LogTemp, Log, TEXT("user_id: %s"), *UserId);
                UE_LOG(LogTemp, Log, TEXT("guest_secret: %s"), *GuestSecret);
                UE_LOG(LogTemp, Log, TEXT("auth_token: %s"), *AccessToken);
                UE_LOG(LogTemp, Log, TEXT("apple_id: %s"), *AppleId);
                UE_LOG(LogTemp, Log, TEXT("steam_id: %s"), *SteamId);
                UE_LOG(LogTemp, Log, TEXT("google_play_id: %s"), *GooglePlayId);
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
                this->m_userInfo.refresh_token = RefreshToken;
                this->m_userInfo.auth_token_expires_in = AccessTokenExpiration;
                this->m_userInfo.refresh_token_expires_in = RefreshTokenExpiration;

                // Generate the FDateTimes from access and refresh token expiration times
                FTimespan authTokenExpirationSeconds;
                authTokenExpirationSeconds = authTokenExpirationSeconds.FromSeconds(AccessTokenExpiration);
                FTimespan refreshTokenExpirationSeconds;
                refreshTokenExpirationSeconds = refreshTokenExpirationSeconds.FromSeconds(RefreshTokenExpiration);
                this->AuthTokenExpirationUTC = FDateTime::UtcNow() + authTokenExpirationSeconds;
                this->RefreshTokenExpirationUTC = FDateTime::UtcNow() + refreshTokenExpirationSeconds;

                // Send the info back to the original requester through the callback (if set)
                if(callback != nullptr)
                    callback(this->m_userInfo);
            }
            else {
                // Failed to deserialize the JSON response, report a login error back to the client
                UE_LOG(LogTemp, Error, TEXT("Failed to deserialize JSON response."));
                if(this->m_loginOrRefreshErrorCallback != nullptr)
                    this->m_loginOrRefreshErrorCallback("Failed to login or refresh token");
            }
        }
        else {
            switch (pRequest->GetStatus()) {
            case EHttpRequestStatus::Failed_ConnectionError:
                UE_LOG(LogTemp, Error, TEXT("Connection failed."));
                if(this->m_loginOrRefreshErrorCallback != nullptr)
                    this->m_loginOrRefreshErrorCallback("Connection failed.");
            default:
                UE_LOG(LogTemp, Error, TEXT("Request failed."));
                if(this->m_loginOrRefreshErrorCallback != nullptr)
                    this->m_loginOrRefreshErrorCallback("Request failed.");
            }
        }
    });
     // Submit the request for processing
     pRequest->ProcessRequest();
}

void UAWSGameSDK::LoginWithAppleId(const FString& appleAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback){
    
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

void UAWSGameSDK::LoginWithSteam(const FString& steamAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback){
    
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

void UAWSGameSDK::LoginWithGooglePlay(const FString& googlePlayAuthToken, const FString& authToken, bool linkToExistingUser, std::function<void(UserInfo userInfo)> callback){

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

void UAWSGameSDK::CallRestApiGetWithAuth(const FString& url, const FString& resource, const FString& authToken, TMap<FString, FString> queryParameters, std::function<void(FString response)> callback){
    
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

    FHttpModule& httpModule = FHttpModule::Get();
    TSharedRef<IHttpRequest, ESPMode::ThreadSafe> pRequest = httpModule.CreateRequest();
    pRequest->SetVerb(TEXT("GET"));
    pRequest->SetURL(fullUrl);
    pRequest->SetHeader(TEXT("Authorization"), authToken);

    // Callback executed after request is complete
    pRequest->OnProcessRequestComplete().BindWeakLambda(this, [this, callback](FHttpRequestPtr pRequest, FHttpResponsePtr pResponse, bool connectedSuccessfully)
    {
        if (connectedSuccessfully) {

            // Get the response content
            FString responseString = pResponse->GetContentAsString();
            UE_LOG(LogTemp, Display, TEXT("Received response: %s"), *responseString );

            // Send the info back to the original requester through the callback
            callback(responseString);
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