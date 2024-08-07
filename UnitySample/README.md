# AWS Game Backend Framework Unity SDK

The AWS Game Backend Framework Unity SDK provides integrations to the custom identity component, managed refreshing of access tokens, helper methods for calling custom backend features, and samples for integrating with different game platforms.

# SDK Overview

## Initializing the SDK

Initializing and accessing the AWS Game SDK within Unity (see the Unity Integration Samples for sample code):

```csharp
// Set the login endpoint and callback
AWSGameSDKClient.Instance.Init(loginEndpointUrl, this.OnLoginOrRefreshError);
```

## SDK Public API

The public API for the SDK includes the following methods. Most of them will require you to provide a callback for results (see the Unity Integration Samples for sample code):

```csharp
public void LoginWithRefreshToken(string refreshToken, Action<LoginRequestData> callback);
public void RefreshAccessToken(Action<LoginRequestData> callback);
public void LoginAsNewGuestUser(Action<LoginRequestData> callback);
public void LoginAsGuest(string user_id, string guest_secret, Action<LoginRequestData> callback);
public void LoginWithAppleIdToken(string appleAuthToken, Action<LoginRequestData> callback);
public void LinkAppleIdToCurrentUser(string appleAuthToken, Action<LoginRequestData> callback);
public void LoginWithSteamToken(string steamAuthToken, Action<LoginRequestData> callback);
public void LinkSteamIdToCurrentUser(string steamAuthToken, Action<LoginRequestData> callback);
public void LoginWithGooglePlayToken(string googlePlayAuthToken, Action<LoginRequestData> callback);
public void LinkGooglePlayIdToCurrentUser(string googlePlayAuthToken, Action<LoginRequestData> callback);
public void LinkFacebookIdToCurrentUser(string facebookAccessToken, string facebookUserId, Action<LoginRequestData> callback)
public void LoginWithFacebookAccessToken(string facebookAccessToken, string facebookUserId, Action<LoginRequestData> callback)
public void BackendGetRequest(string url, string resource, Action<UnityWebRequest> callback, Dictionary<string, string> getParameters = null)
public void BackendPostRequest(string url, string resource, Action<UnityWebRequest> callback, string body)
```

In addition, there is a `WebSocketClient.cs` class that supports setting up authenticated two-way WebSocket connections between your game client and backend. See the [Simple WebSocket Chat](../BackendFeatures/SimpleWebsocketChat/Readme.md) for an example of utilizing WebSocket connections.

## Adding the SDK to an existing project

To add the SDK to an existing project:

1. Drag and drop the folder `AWSGameSDK` found under _Assets_ to your Unity project
2. Add the script `AWSGameSDKClient.cs` to a GameObject in the first scene you need to access it. It will be persisted across scenes (and only one copy always existing even if you have it in multiple scenes)
3. Integrate with the SDK from your custom code (see Unity Integration Samples for example integrations)

# Unity Integration Samples

To test the integrations with Unity, open the Unity sample project (`UnitySample`) with Unity 2021 (or above).

## Guest Identity and Rest API test scene

* Open the scene `Samples/GuestIdentityAndRestApiBackend/GuestIdentityAndRestApiBackend.unity`

This is a simple test scene that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the PlayerPrefs if available to login as an existing user. It will then use the logged in user to set player name and get player name in sequence to test the HTTP API integration

Configure the `GuestIdentityAndRestApiBackend` component of the `BackendIntegrationTest` GameObject to set up API endpoints. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs, and the `Backend Endpoint Url` to the `BackendEndpointUrl` value found in the PythonServerlessHttpApiStack Outputs or the `FargateSampleNodeJsServiceServiceURL` found in the NodeJsFargateApiStack Outputs. Both backend sample integrations support the same functionality to validate tokens and set and get player data.

Press play to test the integration. You'll see the login and backend call activity in the Console as well as key items on on screen log as well.

## Sign in with Apple test scene

* Open the scene `Samples/AppleIdLogin/AppleIdLogin.unity`

This is a simple test scene that will login a guest user, upgrade the guest user to authenticated by linking an Apple ID to it, and then finally test logging in directly with Apple ID. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Apple ID sign in JWT token. This requires you to sign up as an Apple Developer, create an App Identifier, and using a plugin or native SDK in XCode to set up the signing in process. This framework does not include any platform specific identity SDKs. See [Set up an Apple sign-in](https://docs.unity.com/authentication/en/manual/set-up-apple-signin) in the Unity documentation for details.

Configure the `AppleIdLoginTest` component of the `BackendIntegrationTest` GameObject to set up the login API endpoint. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Apple ID JWT token to the `AWSGameSDKClient.Instance.LinkAppleIdToCurrentUser("eyYourTokenHere", this.OnLinkAppleIdResponse);` and `AWSGameSDKClient.Instance.LoginWithAppleIdToken("eyYourTokenHere", this.OnLoginWithAppleIdResponse);` found in `UnitySample/Assets/Samples/AppleIdLogin/AppleIdLoginTest.cs`.

## Steam test scene

* Open the scene `Samples/SteamLogin/SteamLogin.unity`

This is a simple test scene that will login a guest user, upgrade the guest user to authenticated by linking a Steam ID to it, and then finally test logging in directly with Steam. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Steam ID token. This requires you to sign up as a Steam Developer, create an App and the [SteamWorks .NET SDK](https://steamworks.github.io/) to integrate with Steam. You'll then use either _GetAuthTicketForWebApi_ (new) or _GetAuthSessionTicket_ (old) to retrieve a ticket to validate with the identity component API.

Configure the `SteamLoginTest` component of the `BackendIntegrationTest` GameObject to set up the login API endpoint. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Steam token to the `AWSGameSDKClient.Instance.LinkSteamIdToCurrentUser("eyYourTokenHere", this.OnLinkSteamIdResponse);` and `AWSGameSDKClient.Instance.LoginWithSteamToken("eyYourTokenHere", this.OnLoginWithSteamResponse);` found in `UnitySample/Assets/Samples/SteamLogin/SteamLoginTest.cs`.

## Google Play test scene

* Open the scene `Samples/GooglePlayLogin/GooglePlayLogin.unity`

This is a simple test scene that will login a guest user, and upgrade the guest user to authenticated by linking a Google Play ID to it. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Google Play token retrieved with the _RequestServerSideAccess_ Google Play SDK v2 API. This requires you to sign up as a Google Play Developer, and [creating and configuring](https://developers.google.com/games/services/console/enabling) an app. You'll then use the [Unity Google Play integration instructions](https://docs.unity.com/authentication/en-us/manual/platform-signin-google-play-games) to add the SDK and integrate with Google Play.

Configure the `GooglePlayLoginTest` component of the `BackendIntegrationTest` GameObject to set up the login API endpoint. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Google Play token to the `AWSGameSDKClient.Instance.LinkGooglePlayIdToCurrentUser("YOUTOKEN", this.OnLinkGooglePlayIdResponse);` found in `UnitySample/Assets/Samples/GooglePlayLogin/GooglePlayLoginTest.cs`.

## Facebook test scene

* Open the scene `Samples/FacebookLogin/FacebookLogin.unity`

This is a simple test scene that will login a guest user, upgrade the guest user to authenticated by linking a Facebook ID to it, and then finally test logging in directly with Facebook. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Facebook access token and User ID. This requires you to sign up as a Facebook Developer, create an App and using the [Official Facebook Unity SDK](https://developers.facebook.com/docs/unity/) to integrate with Facebook. After logging in with the SDK, you'll receive an access token and user ID for the user to be sent to the backend.

Configure the `FacebookLoginTest` component of the `BackendIntegrationTest` GameObject to set up the login API endpoint. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Facebook access token and userID to the `AWSGameSDKClient.Instance.LinkFacebookIdToCurrentUser("AccessTokenHere","UserIDHere", this.OnLinkFacebookIdResponse);` and `AWSGameSDKClient.Instance.LoginWithFacebookAccessToken("AccessTokenHere", "UserIdHere", this.OnLoginWithFacebookResponse);` found in `UnitySample/Assets/Samples/FacebookLogin/FacebookLoginTest.cs`.




