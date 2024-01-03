# AWS Game Backend Framework Features: Amazon GameLift Integration

- [AWS Game Backend Framework Features: Amazon GameLift Integration](#aws-game-backend-framework-features-amazon-gamelift-integration)
- [Preliminary setup](#preliminary-setup)
- [Deploying the Amazon GameLift integration feature](#deploying-the-amazon-gamelift-integration-feature)
- [Architecture](#architecture)
- [Integration with the Game Engines](#integration-with-the-game-engines)
   * [Unreal Engine integration](#unreal-engine-integration)
   * [Unity integration](#unity-integration)
   * [Godot integration](#godot-integration)
- [Solution overview](#solution-overview)
   * [Sample Game Server](#sample-game-server)
   * [The Serverless Backend](#the-serverless-backend)
   * [Amazon GameLift resources](#amazon-gamelift-resources)
- [API Reference](#api-reference)
- [Unity and Unreal Game Server Builds with GameLift Plugins](#unity-and-unreal-game-server-builds-with-gamelift-plugins)

This backend feature integration shows how to deploy a backend service that interacts with Amazon GameLift, as well as all the required Amazon GameLift resources. The feature comes with a simple sample game server for testing, from which you can then extend to using the Unreal and Unity GameLift Plugins for running a headless version of your game on Amazon GameLift.

# Preliminary setup

This backend feature requires that you have [deployed the Identity component](../../CustomIdentityComponent/README.md). Once that is done, set the `const ISSUER_ENDPOINT` in `BackendFeatures/AmazonGameLiftIntegration/bin/amazon_gamelift_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

Make sure that you have Docker running before opening any terminals or Powershell as both the backend depoyment as well as game server build process will use Docker. You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md) installed.

# Deploying the Amazon GameLift integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands (Note: on **Windows** make sure to run in Powershell as **Administrator**):

1. Navigate to `BackendFeatures/AmazonGameLiftIntegration/SimpleServer/` folder in your terminal
2. You can now either build the game server in a container (takes between 10-30 minutes with all dependencies) or download the prebuilt binary which shouldn't take more than a few seconds.
  * Option 1: Run `./copy_prebuilt_game_server_binary.sh` to download the prebuilt binary and extract it to the `LinuxServerBuild` folder.
  * Option 2: Run `./buildserver.sh` to build the game server in a container (which is then copied to the `LinuxServerBuild` folder). This takes time as the Amazon GameLift C++ Server SDK is downloaded and built along with other dependencies before building the sample C++ server.
3. Navigate to `BackendFeatures/AmazonGameLiftIntegration` by running `cd ..`
4. Run `npm install` to install CDK app dependencies
5. Run `cdk synth` to synthesize the CDK app and validate your configuration works
6. Run `cdk deploy --all` to deploy both the backend APIs as well as the Amazon GameLift resources CDK apps to your account. You will need to accept the deployment. This will take around 45 minutes.

# Architecture

The architecture diagram below shows the main steps of integration from the game engine to the backend and the game servers hosted on Amazon GameLift. See the main Readme of the project for details on how the Custom Identity Component is implemented.

![High Level Reference Architecture](AmazonGameLiftIntegrationArchitecture.png)

# Integration with the Game Engines

## Unreal Engine integration

To test the integrations with Unreal, **open** the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

* Then **open** the level `BackendFeatures/AmazonGameLiftIntegration`

This is a test level that will measure latencies to 3 predefined locations (same as the default setup for the fleet). It will then login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the logged to request matchmaking (sending the latency data that was measured in a separate thread), and start polling for the match status. Once the match is created successfully, it will start a thread with a simple TCP client to connect to the sample server, send the player session ID for validation, and receive the response.

Configure the `AmazonGameLiftIntegration` component of the `AmazonGameLiftIntegration` Actor to set up API endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Gamelift Integration Backend Endpoint Url` to the endpoint `AmazonGameLiftIntegrationBackendEndpointUrl` value found in the *AmazonGameLiftIntegrationBackend* Outputs.

Press play to test the integration. You'll see the login, backend call activity, latency data, and game server connection in the Output Log as well as key items on on screen log as well.

## Unity integration

To test the integrations with Unity, **open** the Unity sample project (`UnitySample`) with Unity 2021 (or above).

* Then **open** the scene `BackendFeatures/AmazonGameLiftIntegration/AmazonGameLiftIntegration.unity`

This is a test level that will measure latencies to 3 predefined locations (same as the default setup for the fleet). It will then login as a new guest user if the login information is not yet in PlayerPrefs, or login using the user_id and guest_secret found in PlayerPrefs to login as an existing user. It will then use the logged to request matchmaking (sending the latency data that was measured), and start polling for the match status. Once the match is created successfully, it will start aa simple TCP client to connect to the sample server, send the player session ID for validation, and receive the response.

Configure the `AmazonGameLiftIntegration` component of the `AmazonGameLiftIntegration` GameObject to set up API endpoints. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs, and the `GameLift Integration Backend Endpoint Url` to the `AmazonGameLiftIntegrationBackendEndpointUrl` value found in the *AmazonGameLiftIntegrationBackend* Outputs.

Press play to test the integration. You'll see the login, backend call activity, latency data, and game server connection in the Output Log as well as key items on on screen log as well.

## Godot integration

TODO: Not implemented yet

# Solution overview

TODO

## Sample Game Server

The sample game server code and build scripts can be found in `BackendFeatures/AmazonGameLiftIntegration/SimpleServer`. 

The sample game server is a C++ application, that integrates with the Amazon GameLift Server SDK 5. It sets up the game server based on server port passed on command line arguments (from the Amazon GameLift fleet configuration), and starts logging to the log file configured in the CloudWatch agent configuration to collect realtime logs to CloudWatch logs. It will then start waiting for Amazon GameLift to trigger the callback for game sessions started, after which it will start a 60 second timer to simulate the game session. During this time, it will accept TCP socket connections in a separate thread, in which it expects to receive the player session ID, which is validated with the GameLift Server SDK to make sure the correct users are connecting to the server. It's worth noting that the sample game server doesn't actually implement any simulation logic, which you would typically do yourself with a physics engine, or using a headless build from your game engine. It's also recommended to use UDP-based networking libraries (native to the game engines or other) for fast paced realtime games.

You will build the game server binary in a container environment with Docker, which will download and build all depencies and build the server itself. Optionally you can download the prebuilt binary to get started faster. The sample server can be useful if you're building a custom C++ game server (for a custom C++ engine for example). For the game engines including Unity and Unreal, you will likely want to run a headless version of the game on the server side. See the [Unity and Unreal Game Server Builds with GameLift Plugins](#unity-and-unreal-game-server-builds-with-gamelift-plugins) for details on how to leverage the Amazon GameLift plugins for this.

## The Serverless Backend

TODO

## Amazon GameLift resources

TODO: Remember to explain CW Agent configuration as well

## Amazon CloudWatch logs and metrics

TODO

# API Reference

All API requests expect the `Authorization` header is set to the JWT value received when logging in. This is automatically done by the AWS Game SDK's for the different game engines when you call the POST and GET requests through their API's.

### POST /request-matchmaking

`POST /request-matchmaking`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with latencies to the different Regions. Example: `{ "latencyInMs": { "us-east-1" : 10, "us-west-2" : 20, "eu-west-1" : 30 }}`  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{"TicketId": "abc", "ConfigurationName": "SampleFlexMatchConfiguration", "ConfigurationArn": "arn:aws:gamelift:us-east-1:1234567890:matchmakingconfiguration/SampleFlexMatchConfiguration", "Status": "ABC", "StartTime": "abc", "Players": [{"PlayerId": "abc", "PlayerAttributes": {}, "LatencyInMs": {"eu-west-1": 10, "us-east-1": 20, "us-west-2": 30}}]}`                                |
> | `500`         |  `"Matchmaking request failed"`                            |

### GET /get-match-status

`GET /get-match-status`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `ticketId`   |  Yes       | The ticket ID received when matchmaking started. |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `"MatchmakingStatus": "STATUS", "Port": "1234", "IpAddress": "11.111.111.111", "DnsName": "abcd.compute.amazonaws.com", "PlayerSessionId": "psess-12345"}`. **NOTE:** You will only receive the *MatchmakingStatus* unless the matchmaking has succeeded and the status is *MatchmakingSucceeded* in which case you'll receive all the fields.                                |
> | `400`         |  `"TicketId not found in DynamoDB table"`                            |
> | `500`         |  `"user_id not available in claims"`                            |
> | `500`         |  `"ticketId not available in querystrings"`                            |


# Unity and Unreal Game Server Builds with GameLift Plugins

TODO: Explain the steps to replace the game server build with a Unity or Unreal build using the GameLift plugins for the engines

