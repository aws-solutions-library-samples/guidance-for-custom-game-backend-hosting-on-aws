# AWS Game Backend Framework Unreal SDK

The AWS Game Backend Framework Unreal SDK provides integrations to the custom identity component, managed refreshing of access tokens, helper methods for calling custom backend features, and samples for integrating with different game platforms.

**NOTE:** The Unreal sample is built with Unreal 5.2, but as it leverages very basic Unreal features, you should be able to build it on any more recent version of Unreal Engine 5. The solution will require Visual Studio 2022 (Windows) or XCode (MacOS) installed as well as the .NET 6 capability. You can upgrade the project to UE 5.3, which has been tested to work well.

# SDK Overview

## Initializing the SDK

Initializing and accessing the AWS Game SDK within Unreal (see the Unreal Integration Samples for sample code):

```cpp
// Get the subsystems
UGameInstance* GameInstance = Cast<UGameInstance>(UGameplayStatics::GetGameInstance(GetWorld()));
UAWSGameSDK* AWSGameSDK =  GameInstance->GetSubsystem<UAWSGameSDK>();

// Init with the login endpoint defined in the Editor and a callback to handle errors for logging in and refresh
AWSGameSDK->Init(this->m_loginEndpoint);
AWSGameSDK->OnLoginFailure.AddUObject(this, &UBackendIntegrationTest::OnLoginOrRefreshErrorCallback);
```

## SDK Public API

The public API for the SDK includes the following methods. Most of them will require you to provide a callback for results (see the Unreal Integration Samples for sample code):

```cpp
void LoginAsNewGuestUser(FLoginComplete callback);
void LoginAsGuestUser(const FString& user_id, const FString& guest_secret, FLoginComplete callback);
void BackendGetRequest(const FString& url, const FString& resource, const TMap<FString, FString>& queryParameters, FRequestComplete callback);
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
```
## Adding the SDK to an existing project

To add the SDK to an existing project:

1. Copy the folder `AWSGameSDK` to your Unreal C++ Project in `Source\<YOUR_PROJECT>\`
2. Modify the file `AWSGameSDK.h` by replacing the class definition with reference you your project name: `class UNREALSAMPLE_API UAWSGameSDK` replaced with `class <YOUR_PROJECT>_API UAWSGameSDK`
3. Add the code line `PrivateDependencyModuleNames.AddRange(new string[] {  "Http", "Json", "JsonUtilities" });` to `<YOUR_PROJECT>.Build.cs` file found under `Source\<YOUR_PROJECT>\`.
3. Integrate with the SDK from your custom code (see Unreal Integration Samples for example integrations)

# Unreal Integration Samples

To test the integrations with Unreal, open the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

## Guest Identity and Rest API test level

* Open the level `Samples/GuestIdentityAndRestApiTest/GuestIdentityAndRestApiTest`

This is a simple test level that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the logged in user to set player name and get player name in sequence to test the HTTP API integration

Configure the `BackendIntegrationTest` component of the `BackendIntegrationTest` Actor to set up API endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Sample Http Api Endpoint Url` to the endpoint `BackendEndpointUrl` value found in the PythonServerlessHttpApiStack Outputs or the `FargateSampleNodeJsServiceServiceURL` found in the NodeJsFargateApiStack Outputs. Both backend sample integrations support the same functionality to validate tokens and set and get player data.

Press play to test the integration. You'll see the login and backend call activity in the Output Log as well as key items on on screen log as well.

## Sign in with Apple test level

* Open the level `Samples/AppleIdLoginTest/TestLevel`

This is a simple test level that will login a guest user, upgrade the guest user to authenticated by linking an Apple ID to it, and then finally test logging in directly with Apple ID. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Apple ID sign in JWT token. This requires you to sign up as an Apple Developer, create an App Identifier and using a plugin or native SDK in XCode to set up the signing in process. This framework does not include any platform specific identity SDKs.

Configure the `AppleIdLoginTest` component of the `BackendIntegrationTest` Actor to set up the login API endpoint. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Apple ID JWT token to the `AWSGameSDK->LinkAppleIdToCurrentUser("eyYourToken", onLinkAppleIdCallback);` and `AWSGameSDK->LoginWithAppleIdToken("eyYourToken", onAppleIdLoginCallback);` found in `UnrealSample/Source/UnrealSample/Samples/AppleIdLoginTest/AppleIdLoginTest.cpp`.

## Steam test level

* Open the level `Samples/SteamLoginTest/SteamLoginTest`

This is a simple test level that will login a guest user, upgrade the guest user to authenticated by linking a Steam ID to it, and then finally test logging in directly with Steam. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Steam ID token. This requires you to sign up as a Steam Developer, create an App and the [Steam Online Subsystem](https://docs.unrealengine.com/5.2/en-US/online-subsystem-steam-interface-in-unreal-engine/) to integrate with Steam. You'll then use either _GetAuthTicketForWebApi_ (new) or _GetAuthSessionTicket_ (old) to retrieve a ticket to validate with the identity component API.

Configure the `SteamLoginTest` component of the `SteamLoginTest` Actor to set up the login API endpoint. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Steam token to the `AWSGameSDK->LinkSteamIdToCurrentUser("eyYourToken", onLinkSteamIdCallback);` and `AWSGameSDK->LoginWithSteamToken("eyYourToken", onSteamLoginCallback);` found in `UnrealSample/Source/UnrealSample/Samples/SteamLoginTest/SteamLoginTest.cpp`.

## Google Play level

* Open the level `Samples/GooglePlayLoginTest/GooglePlayLoginTest`

This is a simple test level that will login a guest user, and upgrade the guest user to authenticated by linking a Google Play ID to it.

To use this sample, you will need a valid Google Play token retrieved with the _RequestServerSideAccess_ Google Play SDK v2 API. This requires you to sign up as a Google Play Developer, [creating and configuring](https://developers.google.com/games/services/console/enabling) an app. At the time of writing, Google Play v2 SDK was not available for C++. The [documentation](https://developers.google.com/games/services/cpp/GettingStartedNativeClient) states you can reach out to Google if interested in v2. Another option is to work around to integrate with the [native Java SDK](https://developers.google.com/games/services/android/signin). There are some [forum discussions](https://forums.unrealengine.com/t/how-to-execute-java-android-code-from-c/312543/10) on running Android Java code with Unreal.

Configure the `GooglePlayLoginTest` component of the `GooglePlayLoginTest` Actor to set up the login API endpoint. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Google Play token to the `AWSGameSDK->LinkGooglePlayIdToCurrentUser("YOURTOKEN", onLinkGooglePlayIdCallback)` found in `UnrealSample/Source/UnrealSample/Samples/GooglePlayLoginTest/GooglePlayLoginTest.cpp`.

## Facebook Test Level

* Open the level `Samples/FacebookLoginTest/FacebookLoginTest`

This is a simple test level that will login a guest user, upgrade the guest user to authenticated by linking a Facebook ID to it, and then finally test logging in directly with Facebook. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Facebook access token and user ID. This requires you to sign up as a Facebook Developer, create an App and integrate with the Facebook API:s from Unreal. Currently, we don't have any specific recommendations on how to integrate with Facebook, but there are some community options.

Configure the `FacebookLoginTest` component of the `FacebookLoginTest` Actor to set up the login API endpoint. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Facebook token to the `AWSGameSDK->LinkFacebookIdToCurrentUser("tokenHere", "userIDHere", onLinkFacebookIdCallback);` and `AWSGameSDK->LoginWithFacebookAccessToken("tokenHere", "userIdHere", onFacebookLoginCallback);` found in `UnrealSample/Source/UnrealSample/Samples/FacebookLoginTest/FacebookLoginTest.cpp`.

