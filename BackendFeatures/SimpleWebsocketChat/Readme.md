# AWS Game Backend Framework Features: Simple WebSocket Chat

This feature of the AWS Game Backend Framework showcases how you can host a WebSocket backend on AWS for simple chat application that supports the following features:

* Set your user name (stored in an ElastiCache for Redis Serverless cluster)
* Join a channel (using Pub/Sub mechanism of Redis)
* Leave a channel
* Send a message to a channel
* Utilizes the WebSocketClient functionality in the Unity and Unreal SDK:s for authenticated WebSocket communication

While this is a simple sample application, it is designed for scale. The chat channels are managed with ElastiCache for Redis Serverless that automatically scales based on demand. The Node.js backend is hosted on Amazon ECS Fargate as a stateless application, which allows you to configure scaling based on selected metrics. See [Scaling considerations](#scaling-considerations) for more details.

## Considerations

There are some key considerations when you start working towards a more production ready setup:

* You're always responsible for your own production configuration, including any load, reliability, and security testing. **This solution is for sample purposes only**.
* We are using encrypted WebSocket connections over Amazon CloudFront, but the communication from CloudFront to the Application Load Balancer is not encrypted. You should set up your own certificates on the ALB level to make that connection encrypted as well.
* Client reconnects are not implemented, you should capture disconnect from the server and implement reconnect based on your game's needs
* We are not limiting access to join channels, you should implement any logic that makes sense for your game to validate on the backend side which channels the player can join
* We are allowing players to set any chat name they want. You might want to grab this name from a database instead and have control on for example the uniqueness of these names
* We are not filtering the chat traffic in any way. You can implement content moderation tooling on the backend side to control what is written in the chat
* Unsubscribing the server containers from redis channels is disabled because of a rare issue with redis when the server is under heavy load (see comments under `SimpleWebSocketApp/RedisManager.js` for more details). This does **not** affect client joining and leaving channels which works as intended.
 
**Note on VPC implementation of the feature:**

This feature deploys a VPC which includes resources such as NAT Gateways that generate cost. This makes it easy to test the feature, but you likely want to share a VPC between multiple components and provide that as a parameter to the different CDK applications.

## Architecture

Here's the high level architecture for the solution:

![High Level Reference Architecture](WebsocketChatArchitecture.png)

Key things to note:

* The AWS Game SDK for Unity and Unreal include a WebSocket connection option for any WebSocket needs, which is utilized by this implementation
* Client connects with a secure WebSocket connection (wss) to a CloudFront distribution that accelerates the connection at the edge
* CloudFront routes the traffic to an Application Load Balancer that routes the WebSocket connection to a cluster of Amazon ECS Fargate Tasks
* A Node.js application will validate the authentication token received from the client as part of the connection. It will validate the token with the public keys provided by the Identity Component. Any invalid connection will be terminated
* After connection is established, the client and server can send any messages both directions over the WebSocket connection
* Amazon ElastiCache for Redis Serverless is used to manage the chat channels. The servers will use Redis Pub/Sub features to send and receive messages

## Required preliminary setup

This backend feature **requires** that you have deployed the [Identity component](../../CustomIdentityComponent/README.md)[^1]. Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/SimpleWebSocketChat/bin/simple_websocket_chat.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

## Deploying the Simple WebSocket Chat feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands:

1. Make sure you have __Docker running__ before you open the terminal, as the deployment process creates a Docker image
2. Navigate to `BackendFeatures/SimpleWebSocketChat/` folder in your terminal or Powershell[^2].
3. Run `npm install` to install CDK app dependencies.
4. Run `cdk deploy --all --require-approval never` to the deploy the backend feature to your AWS account[^3].
5. After the `SimpleWebsocketChat` stack has been deployed, capture the value of `WebSocketEndpoint` found in the outputs of the _SimpleWebsocketChat_ stack. You can find it in the CloudFormation console, or in the terminal after deploying the component.

## Testing the Simple WebSocket Chat feature

You can quickly test that the solution is correctly deployed on a Linux or MacOS terminal by first installing [websocat](https://github.com/vi/websocat), setting up the correct endpoints in the script below, and running it. You should get a response of a successful connection (`{"message":"Successfully connected!"}`):

```bash
# SET THESE FIRST
login_endpoint=https://YOURENDPOINT/prod/
websocket_endpoint=wss://YOURENDPOINT.cloudfront.net

# GET A USER AND CONNECT
auth_token=$(curl $login_endpoint/login-as-guest | jq -j '.auth_token')
websocat "$websocket_endpoint/?auth_token=$auth_token"
```
## Integration with the Game Engines

### Unity integration

To test the integrations with Unity, **open** the Unity sample project (`UnitySample`) with Unity 2021 (or above).

* Then **open** the scene `BackendFeatures/SimpleWebsocketChat/SimpleWebsocketChat.unity`

This is a test level that will login as a new guest user if a PlayePrefs configuration is not present. It has a UI to 1/ set name, 2/ join channels, 3/ leave channels, and 4/ send messages. You can see the output of using the UI in the output and when you join a channel and a message is sent to that, it will be passed to the client. 

Configure the `SimpleWebsocketChat` component of the `SimpleWebsocketChatIntegration` GameObject to set up API endpoints. Set `Login Endpoint Url` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs, and the `Websocket Endpoint Url` to the `WebSocketEndpoint` value found in the *SimpleWebsocketChat* Outputs.

Press play to test the integration. You'll see the login and WebSocket connection happen. You can then use the UI to test the chat application.

**Key code files:**
* `UnitySample/Assets/AWSGameSDK/WebSocketClient.cs`: A WebSocket client class that can be used by any WebSocket integration.
* `UnitySample/Assets/BackendFeatures/SimpleWebsocketChat/ChatSerializationClasses.cs`: The data structure for messages between client and server that are sent over in JSON format
* `UnitySample/Assets/BackendFeatures/SimpleWebsocketChat/SimpleWebsocketChat.cs`: The main class for the chat application

### Unreal Engine integration

To test the integrations with Unreal, **open** the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

* Then **open** the level `BackendFeatures/SimpleWebSocketChat`

This is a test level that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the credentials of the logged in user to test the WebSocket connection to the chat application, set name, join channel, send message, and leave channel.

Configure the `SimpleWebsocketChat` component of the `SimpleWebsocketChat` Actor to set up API and WebSocket endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Websocket Endpoint Url` to the endpoint value `WebSocketEndpoint` found in the *SimpleWebsocketChat* Outputs.

Press play to test the integration. You'll see the login as a guest user and starting the websocket connection with this user. Then you'll see joining a channel, sending and receiving a message on the channel, and leaving the channel in the end.

**Adding the integration to your custom project:** You can follow the [guidelines found in the Unreal Engine Integration Readme](../../UnrealSample/README.md#adding-the-sdk-to-an-existing-project) to add the AWS Game SDK to your own project. After that, you can use `UnrealSample/Source/UnrealSample/BackendFeatures/SimpleWebsocketChat/SimpleWebsocketChat.cpp.cpp` as a reference for how to implement the WebSocket connection.

**Key code files:**
* `UnrealSample/Source/UnrealSample/AWSGameSDK/WebSocketClient.cpp`: A WebSocket client class that can be used by any WebSocket integration.
* `UnrealSample/Source/UnrealSample/BackendFeatures/SimpleWebsocketChat/SimpleWebsocketChat.cpp`: The main class for the chat application

## WebSocket message reference

The initial connection to the websocket expects to receive the `auth_token` as a URL Parameter, for example `wss://abcdefghijklm.cloudfront.net/?auth_token=eyMYTOKEN`.

Server will disconnect any client that doesn't send an auth token that validates correctly against the public key found in the Identity component endpoint. After this, the messages use a JSON format for the different features of the chat application.

### Message types

#### type: set-name

Sets the name of the user. This must be called before any messages can be sent to any channel as the broadcasted messages will have the name included.

Message content:

`{ "type" : "set-name", "payload" : { "username" : "YOUR NAME" }}`

#### type: join

Joins the defined channel. After this, all messages sent to this channel will be sent over the WebSocket to this user

Message content:

`{ "type" : "join", "payload" : { "channel" : "YOUR CHANNEL" }}`

#### type: leave

Leaves the defined channel. After this, no messages are received from this channel. User will also disconnect from all channels when disconnecting from the backend.

Message content:

`{ "type" : "leave", "payload" : { "channel" : "YOUR CHANNEL" }}`

#### type: message

Sends a message to the defined channel. The message is broadcasted to all users who have joined the channel.

Message content:

`{ "type" : "message", "payload" : { "channel" : "YOUR CHANNEL", "message" : "YOUR MESSAGE" }}`

## Scaling Considerations

The solution has been load tested with 1500 clients generating a total of 1500 chat messages per second. With players sending on average 1 message per minute (which would be a lot), this would map to 90 000 CCU. Note that the amount of Websocket connections to the Fargate service would still be 60x higher in that scenario, which requires separate testing.

With this amount of traffic, the Fargate application consumed 61% CPU and 16.2% memory on average across the 3 Tasks initially launched. The ECS Service is configured to scale to up to 10 Tasks and scaling targets 80% average CPU utilization. The ElastiCache for Redis Serverless cluster scales automatically, and didn't show any issues with this traffic. Also, testing with a sample Unity client, the player experience was fast and responsive.

Make sure to do extensive load testing for your own needs, and configure the resources and scaling to match your needs. The solution is intended for sample purposes only, and you're always responsible for your own production environments.

---

**Notes:**

[^1]: You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md#deploy-the-custom-identity-component) installed.  
[^2]: On **Windows** make sure to run in Powershell as **Administrator**.  
[^3]: If you are deploying the backend feature in a different AWS Account, or AWS Region from the _CustomIdentityComponentStack_, make sure to run ```cdk bootstrap``` to bootstrap the account for CDK (see [Bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html) for more information).  
[^4]: Run the command with just the `--dry-run` parameter first to verify script functionality.
