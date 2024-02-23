# AWS Game Backend Framework Features: Databricks Delta Lake Integration

This backend feature is currently ___EXPERIMENTAL___, and shows how to deploy a backend service to ingest game event telemetry data to [Delta Lake](https://docs.databricks.com/en/delta/index.html). This feature comes with a test script, from which you can then extend to using the Unreal, Unity and Godot Game Engines.

## Required preliminary setup

This backend feature **requires** that you have deployed the [Identity component](../../CustomIdentityComponent/README.md)[^1]. Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/DeltaLakeIntegration/bin/delta_lake_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

Additionally, ensure that you have subscribed to the [Delta Lake Connector for AWS Glue](https://aws.amazon.com/marketplace/pp/prodview-seypofzqhdueq?sr=0-1&ref_=beagle&applicationId=AWSMPContessa) in the **AWS Marketplace**. 

## Deploying the Databricks Delta Lake integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands:

1. Navigate to `BackendFeatures/DeltaLakeIntegration/` folder in your terminal or Powershell[^2].
2. Run `npm install` to install CDK app dependencies.
3. Run `cdk deploy --all --require-approval never` to the deploy the backend feature to your AWS account.
4. After the `DeltaLakeIntegrationBackend` has been deployed, capture the value of `IngestionEndpointUrl` found in the outputs of the _DeltaLakeIntegrationBackend_ stack. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.
5. Open the [AWS Glue console](https://console.aws.amazon.com/glue/home) in your AWS account, and use the left-hand navigation panel to select **ETL Jobs**.
6. Click the checkbox for the **GlueStreamEtlJob**, and click the **Run Job** button to run the streaming ETL operations.

## Testing the Databricks Delta Lake integration feature

A sample Python script to generate synthetic game telemetry events has been provided in the `tests` folder. Run the following steps to test the integration:

1. Navigate to `BackendFeatures/DeltaLakeIntegration/tests` folder in your terminal or Powershell[^2].
2. Install the necessary Python packages, by running `python -m pip install -r requirements.txt`.
3. Run the following command to generate 100 synthetic game events[^3]:
    ```bash
    python synthetic_events.py --login-endpoint <`LoginEndpoint` value from the output of the `CustomIdentityComponentStack` stack> --backend-endpoint <`IngestionEndpointUrl` value from the `DeltaLakeIntegrationBackend` stack> --max-count 100 --console
    ```
4. After the script has completed running, open the [Amazon S3 console](https://console.aws.amazon.com/s3), and navigate to the Bucket that starts with the `deltalakeintegrationbacken-deltalakebucketeb...`. You should see the raw data folder, similar to the following:
    ```bash
    .
    ├── delta_lake_events_db
    │   ├── events
    │   │   ├── _delta_log
    │   │   │   ├── 00000000000000000000.json
    │   │   │   ├── 00000000000000000001.json
    │   │   │   └── ...
    │   │   ├── _delta_log_$folder$
    │   │   ├── event_type=End Game
    │   │   │   ├── part-00000-28c1efba-aa4f-...c000.snappy.parquet
    │   │   │   ├── part-00000-338eeff8-fbf6-...859.c000.snappy.parquet
    │   │   │   └── ...
    │   │   ├── event_type=Login
    │   │   │   ├── part-00000-1b511dc6-1b2d-...725.c000.snappy.parquet
    │   │   │   ├── part-00000-433757c6-31dc-...1a4.c000.snappy.parquet
    │   │   │   └── ...
    │   │   ├── event_type=Logout
    │   │   │   ├── part-00000-3ab206c8-8be1-4...6e8.c000.snappy.parquet
    │   │   │   ├── part-00000-49c5a92b-d9d2-4...9b3.c000.snappy.parquet
    │   │   │   └── ...
    │   │   ├── event_type=New Game
    │   │   │   ├── part-00000-90b31328-3d62-...27c.c000.snappy.parquet
    │   │   │   ├── part-00000-a67fe3d6-6dfd-...13e.c000.snappy.parquet
    │   │   │   └── ...
    │   │   ├── event_type=Resume Game
    │   │   │   ├── part-00000-4609adb0-3705-...56a.c000.snappy.parquet
    │   │   │   ├── part-00000-a74d8992-ec58-...8de.c000.snappy.parquet
    │   │   │   └── ...
    │   │   ├── ...
    │   │   │   └── GlueStreamEtlJob_$folder$
    │   │   └── temp_$folder$
    │   └── events_$folder$
    ├── delta_lake_events_db_$folder$
    └── ...
    ```
5. Following the Solution [README](../../README.md) to integrate the feature into your game engine.

## Integration with the Game Engines

### Unreal Engine integration

To test the integrations with Unreal, **open** the Unreal sample project (`UnrealSample`) in Unreal Engine 5 first.

**NOTE:** On Windows it will prompt you if you don't have Visual Studio installed yet. Once you have Visual Studio installed and set up for Unreal, you can open the project in the Unreal Editor and generate the project files from *Tools -> Generate Visual Studio Project*. On MacOS, you need to do *right click -> Services -> Generate XCode Project* on the uproject file in Finder. If you have problems generating the project files on MacOS, [this forum post](https://forums.unrealengine.com/t/generate-xcode-project-doesnt-do-anything/123149/3) can help run the shell script correctly from your UE installation folder against the project in the terminal.

* Then **open** the level `BackendFeatures/DatabricksDeltaLakeIntegration`

This is a test level that will login as a new guest user if a save file is not present, or login using the user_id and guest_secret found in the save file if available to login as an existing user. It will then use the credentials of the logged in user to send test events to the data pipeline and print out the requests and responses.

Configure the `DatabricksDeltaLakeIntegration` component of the `DatabricksDeltaLakeIntegration` Actor to set up API endpoints. Set `M Login Endpoint` value to the `LoginEndpoint` value found in the CustomIdentityComponentStack Outputs. Then set the `M Data Pipeline Endpoint` to the endpoint value found in the *DeltaLakeIntegrationBackend* Outputs.

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
[^3]: Run the command with just the `--dry-run` parameter first to verify script functionality.