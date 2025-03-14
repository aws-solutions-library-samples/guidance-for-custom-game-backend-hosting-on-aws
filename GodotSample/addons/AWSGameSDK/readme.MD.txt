# AWS Game Backend Framework Godot 4 SDK

The AWS Game Backend Framework Godot 4 SDK provides integrations to the custom identity component, managed refreshing of access tokens, helper methods for calling custom backend features, and samples for integrating with different game platforms.

# SDK Overview

## Initializing the SDK

The AWS Game SDK has to be configured in your Godot 4 project in _Project Settings -> Autoload_ with the name _AwsGameSdk_.

The Initializing and accessing the AWS Game SDK within Godot 4 (see the Godot 4 Integration Samples for sample code):

```python
# Get the SDK and Init
self.aws_game_sdk = get_node("/root/AwsGameSdk")
self.aws_game_sdk.init(self.login_endpoint, self.on_login_error)
```

## SDK Public API

The public API for the SDK includes the following methods. Most of them will require you to provide a callback for results (see the Godot 4 Integration Samples for sample code):

```text
func init(login_endpoint, login_error_callback)
func login_as_new_guest_user(login_callback)
func login_as_guest(user_id, guest_secret, login_callback)
func login_with_refresh_token(refresh_token, login_callback = null)
func link_steam_id_to_current_user(steam_token, login_callback_steam)
func login_with_steam_token(steam_token, login_callback)
func link_apple_id_to_current_user(apple_auth_token, login_callback_apple)
func login_with_apple_id_token(apple_auth_token, login_callback)
func link_google_play_id_to_current_user(google_play_auth_token, login_callback_google)
func login_with_google_play_token(google_play_auth_token, login_callback)
func link_facebook_id_to_current_user(facebook_access_token, facebook_user_id, login_callback_facebook)
func login_with_facebook_access_token(facebook_access_token, facebook_user_id, login_callback)
func backend_get_request(url, resource, query_parameters, callback)
func backend_post_request(url, resource, request_body, callback):
```

## Adding the SDK to an existing project

To add the SDK to an existing project:

1. Drag and drop the folder `AWSGameSDK` to your Godot 4 project
2. Open _Project Settings -> Autoload_, select the script `AWSGameSDK.gd` with the directory search and select _Open_. Make sure the name is _AwsGameSdk_ and select _Add_.
3. Integrate with the SDK from your custom code (see Godot 4 Integration Samples for example integrations)

# Godot 4 Integration Samples

To test the integrations with Godot 4, open the Godot 4 sample project (`GodotSample`) with Godot 4.

## Guest Identity and Rest API test scene

* Open the scene `Samples/GuestIdentityAndRestApiBackend/GuestIdentityAndRestApiBackend.tscn`

This is a simple test scene that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the logged in user to set player name and get player name in sequence to test the HTTP API integration

Configure the `Samples/GuestIdentityAndRestApiBackend/GuestIdentityAndRestApiBackend.gd` script to set up API endpoints. Set `const login_endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs, and the `const backend_endpoint` to the `BackendEndpointUrl` value found in the PythonServerlessHttpApiStack Outputs or the `FargateSampleNodeJsServiceServiceURL` found in the NodeJsFargateApiStack Outputs. Both backend sample integrations support the same functionality to validate tokens and set and get player data.

Press "Run current scene" to test the integration. You'll see the login and backend call activity in the Output console.

## Steam test scene

* Open the scene `Samples/SteamIdLogin/SteamIdLogin.tscn`

This is a simple test scene that will login a guest user, upgrade the guest user to authenticated by linking a Steam ID to it, and then finally test logging in directly with Steam. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Steam ID token. This requires you to sign up as a Steam Developer, create an App and the [Godot Steam SDK](https://godotsteam.com/) to integrate with Steam. You'll then use either _GetAuthTicketForWebApi_ (new) or _GetAuthSessionTicket_ (old) to retrieve a ticket to validate with the identity component API.

Configure the `Samples/SteamIdLogin/SteamLogin.gd` script to set up the login API endpoint. Set `const login_endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Steam token to the `self.aws_game_sdk.link_steam_id_to_current_user("YourTokenHere", self.on_link_steam_id_response)` and `self.aws_game_sdk.login_with_steam_token("YourTokenHere", self.on_login_with_steam_response)` found in `Samples/SteamIdLogin/SteamLogin.gd`.

## Facebook test scene

* Open the scene `Samples/FacebookLogin/FacebookLogin.tscn`

This is a simple test scene that will login a guest user, upgrade the guest user to authenticated by linking a Facebook ID to it, and then finally test logging in directly with Facebook. It doesn't call any custom backend functionalities.

To use this sample, you will need a valid Facebook access token and Facebook User ID. This requires you to sign up as a Facebook Developer, create an App and using one of the community integrations with Facebook to login (such as [this](https://github.com/DrMoriarty/godot-facebook)). After calling _Login_ with Facebook you'll receive and access token and user ID which need to be passed to the SDK integrations.

Configure the `Samples/FacebookLogin/FacebookLogin.gd` script to set up the login API endpoint. Set `const login_endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs.

You will need to pass a valid Facebook access token and User ID to the `self.aws_game_sdk.link_facebook_id_to_current_user("AcceessTokenHere", "UserIdHere", self.on_link_facebook_id_response)` and `self.aws_game_sdk.login_with_facebook_access_token("AccessTokenHere", "UserIdHere", self.on_login_with_facebook_response)` found in `Samples/FacebookLign/FacebookLogin.gd`.

## Google Play and Sign in with Apple testing

The sample project does not come with Google Play or Sign in with Apple sample scenes and scripts. Currently, Godot 4 integrations with Apple ID or Google Play are not easily available. If you build such integration yourself, you can use the APIs provided in the SDK to log in with Google Play and Apple ID, as these functionalities are fully supported by the SDK.

The following SDK functions can be used for this in a very similar fashion as with the Steam and Facebook integrations:

```text
func link_apple_id_to_current_user(apple_auth_token, login_callback_apple)
func login_with_apple_id_token(apple_auth_token, login_callback)
func link_google_play_id_to_current_user(google_play_auth_token, login_callback_google)
func login_with_google_play_token(google_play_auth_token, login_callback)
```
