# AWS Game Backend Framework Features: Amazon GameLift Integration

- [Preliminary setup](#preliminary-setup)
  
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
6. Run `cdk deploy --all` to deploy both the backend APIs as well as the Amazon GameLift resources CDK apps to your account. You will need to accept the deployment.
7. Run `./setup_fleet_scaling.sh` to scale to configure minimum, maximum and desired instances per location. See the script for configuring this to your needs.

## Architecture

TODO: Architecture diagram

![High Level Reference Architecture](TODO.png)

## Solution overview

TODO

### Sample Game Server

TODO

### The Serverless Backend

### Amazon GameLift resources

TODO: Remember to explain CW Agent configuration as well

## API Reference

TODO: Explain Backend APIs

## Unity and Unreal Game Server Builds

TODO: Explain the steps to replace the game server build with a Unity or Unreal build using the GameLift plugins for the engines

