import * as cdk from 'aws-cdk-lib';
import { RemovalPolicy, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as deployment from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as lambda from 'aws-cdk-lib/aws-lambda';

// Define custom stack properties
interface DeltaLakeIntegrationBackendProps extends cdk.StackProps {
  issuerEndpointUrl : string;
  etlScriptName : string;
}

export class DeltaLakeIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DeltaLakeIntegrationBackendProps) {
    super(scope, id, props);

    let streamDbName : string = 'delta_lake_stream_db';
    let streamTableName : string = 'kinesis_stream_table';

    // Create an S3 Bucket to serve as the delta lake object store
    const s3Bucket = new s3.Bucket(this, 'DeltaLakeBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Deploy SDK asset `aws-sdk-java-2.17.224.jar` deployment for Glue Streaming job
    new deployment.BucketDeployment(this, 'AssetsDeployment', {
      sources: [deployment.Source.asset('assets')],
      destinationBucket: s3Bucket,
      destinationKeyPrefix: 'assets'
    });

    // Deploy the script assets for the Glue streaming job
    new deployment.BucketDeployment(this, 'ScriptsDeployment', {
      sources: [deployment.Source.asset('scripts')],
      destinationBucket: s3Bucket,
      destinationKeyPrefix: 'scripts'
    });

    // Create the Lambda logging shared policy
    const lambdaBasicsPolicy = new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
      effect: iam.Effect.ALLOW,
      resources: ['*']
    });

    // Create the shared Lambda execution policy
    const lambdaLoggingPolicy = new iam.PolicyStatement({
      actions: ['logs:DeleteRetentionPolicy','logs:PutRetentionPolicy'],
      effect: iam.Effect.ALLOW,
      resources: ['*']
    });

    // Create a shared IAM Role for for Lambda execution and logging
    const lambdaLoggingRole = new iam.Role(this, 'LambdaLoggingRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      inlinePolicies:{
        'LambdaLoggingPolicy': new iam.PolicyDocument({
          statements: [lambdaLoggingPolicy],
        }),
        'LambdaBasicsPolicy': new iam.PolicyDocument({
          statements: [lambdaBasicsPolicy],
        })
      }
    });

    // Create specific IAM Role for the `record` Lambda Function
    const recordHandlerRole = new iam.Role(this, 'RecordFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com')
    });
    recordHandlerRole.addToPolicy(lambdaBasicsPolicy);

    // Create the Glue Job IAM Role
    const glueRole = new iam.Role(this, 'GlueJobRole', {
      roleName: 'DeltalakeGlueRole',
      assumedBy: new iam.ServicePrincipal('glue.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSGlueServiceRol'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMReadOnlyAccess'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEC2ContainerRegistryReadOnly'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AWSGlueConsoleFullAccess'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonKinesisReadOnlyAccess'),
      ],
      inlinePolicies: {
        S3Access: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                's3:ListBucket',
                's3:GetBucketAcl',
                's3:GetObject',
                's3:PutObject',
                's3:DeleteObject'
              ],
              effect: iam.Effect.ALLOW,
              resources: [
                s3Bucket.bucketArn,
                `${s3Bucket.bucketArn}/*`
              ],
            }),
          ],
        }),
        PassRole: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                'iam:PassRole',
              ],
              effect: iam.Effect.ALLOW,
              resources: [
                this.formatArn({
                  service: 'iam',
                  region: '',
                  resource: 'role',
                  resourceName: 'DeltalakeGlueRole',
                }),
              ],
            }),
          ],
        }),
      },
    });

    // Create the Kinesis Data Stream for data ingest
    const kinesisStream = new kinesis.Stream(this, 'IngestStream', {
      streamName: 'DataIngestStream',
      streamMode: kinesis.StreamMode.ON_DEMAND,
      retentionPeriod: Duration.hours(24),
    });
    kinesisStream.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create a Glue Catalog to store the stream data table
    const streamDB = new glue.CfnDatabase(this, 'IngestStreamDatabase', {
      catalogId: cdk.Aws.ACCOUNT_ID,
      databaseInput: {
        name: `${streamDbName}`,
      },
    });
    streamDB.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create the stream data Glue Table
    const streamTable = new glue.CfnTable(this, 'IngestStreamTable', {
      catalogId: cdk.Aws.ACCOUNT_ID,
      databaseName: `${streamDbName}`,
      tableInput: 
      
      
      
      
      {
        name: `${streamTableName}`,
        parameters: {
          classification: 'json'
        },
        tableType: 'EXTERNAL_TABLE',
        storageDescriptor: 
      }
    });

  }
}
