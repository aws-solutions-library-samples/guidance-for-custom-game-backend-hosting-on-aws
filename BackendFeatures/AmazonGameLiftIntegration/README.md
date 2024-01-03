# AWS Game Backend Framework Features: Amazon GameLift Integration

- [AWS Game Backend Framework Features: Amazon GameLift Integration](#aws-game-backend-framework-features-amazon-gamelift-integration)
- [Preliminary setup](#preliminary-setup)
- [Deploying the Amazon GameLift integration feature](#deploying-the-amazon-gamelift-integration-feature)
- [Integration with the Game Engines](#integration-with-the-game-engines)
   * [Unreal Engine integration](#unreal-engine-integration)
   * [Unity integration](#unity-integration)
   * [Godot integration](#godot-integration)
- [Architecture](#architecture)
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

# Integration with the Game Engines

## Unreal Engine integration

To test the integrations with Unreal, **open** the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

* Then **open** the level `BackendFeatures/AmazonGameLiftIntegration`

This is a test level that will measure latencies to 3 predefined locations (same as the default setup for the fleet). It will then login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the logged to request matchmaking (sending the latency data that was measured in a separate thread), and start polling for the match status. Once the match is created successfully, it will start a thread with a simple TCP socket to connect to the sample server, send the player session ID for validation, and receive the response.

Configure the `AmazonGameLiftIntegration` component of the `AmazonGameLiftIntegration` Actor to set up API endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Gamelift Integration Backend Endpoint Url` to the endpoint `AmazonGameLiftIntegrationBackendEndpointUrl` value found in the *AmazonGameLiftIntegrationBackend* Outputs.

Press play to test the integration. You'll see the login, backend call activity, latency data, and game server connection in the Output Log as well as key items on on screen log as well.

## Unity integration

## Godot integration

# Architecture

The architecture diagram below shows the main steps of integration from the game engine to the backend and the game servers hosted on Amazon GameLift. See the main Readme of the project for details on how the Custom Identity Component is implemented.

![High Level Reference Architecture](AmazonGameLiftIntegrationArchitecture.png)

# Solution overview

TODO

## Sample Game Server

TODO

## The Serverless Backend

## Amazon GameLift resources

TODO: Remember to explain CW Agent configuration as well

# API Reference

TODO: Explain Backend APIs

# Unity and Unreal Game Server Builds with GameLift Plugins

TODO: Explain the steps to replace the game server build with a Unity or Unreal build using the GameLift plugins for the engines

