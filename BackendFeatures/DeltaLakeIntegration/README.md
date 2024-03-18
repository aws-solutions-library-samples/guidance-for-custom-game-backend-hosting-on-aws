# AWS Game Backend Framework Features: Databricks Delta Lake Integration

This backend feature is currently ___EXPERIMENTAL___, and shows how to deploy a backend service to ingest game event telemetry data to [Databricks Delta Lake](https://docs.databricks.com/en/delta/index.html). It is optimized for performance, but not cost currently. If you're sending a high volume of events to the pipeline, it's recommended to modify the solution to batch the requests on the client side. This feature currently comes with a test script and Unreal integration, from which you can then extend to other game engines.

## Required preliminary setup

This backend feature **requires** that you have deployed the [Identity component](../../CustomIdentityComponent/README.md)[^1]. Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/DeltaLakeIntegration/bin/delta_lake_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

## Deploying the Databricks Delta Lake integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands:

1. Navigate to `BackendFeatures/DeltaLakeIntegration/` folder in your terminal or Powershell[^2].
2. Run `npm install` to install CDK app dependencies.
3. Run `cdk deploy --all --require-approval never` to the deploy the backend feature to your AWS account[^3].
4. After the `DeltaLakeIntegrationBackend` has been deployed, capture the value of `IngestionEndpointUrl` found in the outputs of the _DeltaLakeIntegrationBackend_ stack. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

## Integration with Databricks Delta Lake

To integrate with Databricks Delta lake, follow the information in [this blog post](https://www.databricks.com/blog/managing-analyzing-game-data-scale). You will find the **Kinesis Stream Name** and the **Kinesis Stream Arn** in the outputs of the *DeltaLakeIntegrationBackend* CloudFormation Stack, which you will need to integrate with Delta Lake.

## Testing the Databricks Delta Lake integration feature

A sample Python script to generate synthetic game telemetry events has been provided in the `tests` folder. Run the following steps to test the integration:

1. Navigate to `BackendFeatures/DeltaLakeIntegration/tests` folder in your terminal or Powershell[^2].
2. Install the necessary Python packages, by running `python -m pip install -r requirements.txt`.
3. Run the following command to generate 100 synthetic game events[^4]:
    ```bash
    python synthetic_events.py --login-endpoint <`LoginEndpoint` value from the output of the `CustomIdentityComponentStack` stack> --backend-endpoint <`IngestionEndpointUrl` value from the `DeltaLakeIntegrationBackend` stack> --max-count 100 --console
    ```
4. After the script has completed running, open the Delta Live Tables to curate, and analyze the synthetic game event data.



## Integration with the Game Engines

### Unreal Engine integration

To test the integrations with Unreal, **open** the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

* Then **open** the level `BackendFeatures/DatabricksDeltaLakeIntegration`

This is a test level that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the credentials of the logged in user to send test events to the data pipeline and print out the requests and responses.

Configure the `DatabricksDeltaLakeIntegration` component of the `DatabricksDeltaLakeIntegration` Actor to set up API endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Data Pipeline Endpoint` to the endpoint value `DeltaLakeIntegrationBackendEndpointUrlWithoutResource` found in the *DeltaLakeIntegrationBackend* Outputs. **NOTE:** This is a different value than the one used in the test script and it doesn't contain the `put-record` resource as part of the URL (the EventSender will add this).

Press play to test the integration. You'll see the login as a guest user, sending of 5 test events, and the responses from the backend.

**Adding the integration to your custom project:** You can follow the [guidelines found in the Unreal Engine Integration Readme](../../UnrealSample/README.md#adding-the-sdk-to-an-existing-project) to add the AWS Game SDK to your own project. After that, you can use `UnrealSample/Source/BackendFeatures/DatabricksDeltaLakeIntegration/DatabricksDeltaLakeIntegration.cpp` as a reference for how to send events to the data pipeline. It also includes an `EventDataSender` helper class for sending events.

## API reference

All API requests expect the `Authorization` header is set to the JWT value received when logging in. This is automatically done by the AWS Game SDK's for the different game engines when you call the POST and GET requests through their API's.

### POST /put-record

`POST /put-record`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with latencies to the different Regions. Example: `{"event_id": "00006", "event_type": "Login", "updated_at": "2024-02-22 03:03:02", "event_data": "The only thing we have to fear is fear itself."}`  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `"Successfully added event"`                                |
> | `401`         | `"Unauthorized"`                                  |
> | `500`         |  `"Failed"`                            |

---

**Notes:**

[^1]: You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md#deploy-the-custom-identity-component) installed.  
[^2]: On **Windows** make sure to run in Powershell as **Administrator**.  
[^3]: If you are deploying the backend feature in a different AWS Account, or AWS Region from the _CustomIdentityComponentStack_, make sure to run ```cdk bootstrap``` to bootstrap the account for CDK (see [Bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html) for more information).  
[^4]: Run the command with just the `--dry-run` parameter first to verify script functionality.