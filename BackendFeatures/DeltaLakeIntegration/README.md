# AWS Game Backend Framework Features: Databricks Delta Lake Integration

This backend feature shows how to deploy a backend service to ingest game event telemetry data to [Delta Lake](https://docs.databricks.com/en/delta/index.html). This feature comes with a test script, from which you can then extend to using the Unreal, Unity and Godot Game Engines.

## Required preliminary setup

This backend feature **requires** that you have deployed the [Identity component](../../CustomIdentityComponent/README.md)[^1]. Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/DeltaLakeIntegration/bin/delta_lake_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

Additionally, ensure that you have subscribed to the [Delta Lake Connector for AWS Glue](https://aws.amazon.com/marketplace/pp/prodview-seypofzqhdueq?sr=0-1&ref_=beagle&applicationId=AWSMPContessa) in the **AWS Marketplace**. 

## Deploying the Databricks Delta Lake integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands:

1. Navigate to `BackendFeatures/DeltaLakeIntegration/` folder in your terminal or Powershell[^2].
2. Run `npm install` to install CDK app dependencies.
3. Run `cdk deploy --all --require-approval never` to the deploy the backend feature to your AWS account.
4. After the `DeltaLakeIntegrationBackend` has been deployed, open the [AWS Glue console](https://console.aws.amazon.com/glue/home) in your AWS account, and use the left-hand navigation panel to select **ETL Jobs**.
5. Click the checkbox for the **GlueStreamEtlJob**, and click the **Run Job** button.

### Notes:

[^1]: You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md#deploy-the-custom-identity-component) installed.
[^2]: On **Windows** make sure to run in Powershell as **Administrator**.