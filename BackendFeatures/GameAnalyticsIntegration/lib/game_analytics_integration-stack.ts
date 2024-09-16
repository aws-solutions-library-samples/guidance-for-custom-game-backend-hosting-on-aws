import * as cdk from 'aws-cdk-lib';
import { RemovalPolicy, Duration, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as deployment from 'aws-cdk-lib/aws-s3-deployment';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as glue from 'aws-cdk-lib/aws-glue';
import * as api from 'aws-cdk-lib/aws-apigatewayv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';

// Define custom stack properties
interface AnalyticsIntegrationBackendProps extends cdk.StackProps {
  issuerEndpointUrl : string;
  etlScriptName : string;
}

export class AnalyticsIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AnalyticsIntegrationBackendProps) {
    super(scope, id, props);

    // let streamDbName : string = 'analytics_stream_db';
    let dbName : string = 'analytics_events_db';
    let streamTableName : string = 'analytics_stream_table';
    let connectionName : string = 'iceberg-connection';
    let glueJobName: string = 'GlueStreamEtlJob';

    // Create an S3 Bucket to serve as the delta lake object store
    const s3Bucket = new s3.Bucket(this, 'AnalyticsBucket', {
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
      sources: [deployment.Source.asset('etl')],
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

    // Create specific IAM Role for the `recordHandler` Lambda Function
    const recordHandlerRole = new iam.Role(this, 'RecordFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com')
    });
    recordHandlerRole.addToPolicy(lambdaBasicsPolicy);

    // Create the Glue Streaming ETL Job IAM Role
    const glueRole = new iam.Role(this, 'GlueJobRole', {
      roleName: 'StreamingEtlGlueRole',
      assumedBy: new iam.ServicePrincipal('glue.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSGlueServiceRole'),
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
                's3:GetBucketLocation',
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
        DynamoDBAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: [
                'dynamodb:BatchGetItem',
                'dynamodb:DescribeStream',
                'dynamodb:DescribeTable',
                'dynamodb:GetItem',
                'dynamodb:Query',
                'dynamodb:Scan',
                'dynamodb:BatchWriteItem',
                'dynamodb:CreateTable',
                'dynamodb:DeleteTable',
                'dynamodb:UpdateTable',
                'dynamodb:PutItem',
                'dynamodb:UpdateItem',
                'dynamodb:DeleteItem',
              ],
              effect: iam.Effect.ALLOW,
              resources: ['*'],
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
                  resourceName: 'StreamingEtlGlueRole',
                }),
              ],
              conditions: {
                StringLike: {
                  'iam:PassedToService': 'glue.amazonaws.com',
                },
              },
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
        name: `${dbName}`,
        description: 'Kinesis Stream Database'
      },
    });
    streamDB.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create the stream data Glue Table
    const streamTable = new glue.CfnTable(this, 'IngestStreamTable', {
      catalogId: cdk.Aws.ACCOUNT_ID,
      databaseName: dbName,
      tableInput: {
        name: streamTableName,
        description: 'Kinesis Stream Table',
        parameters: {
          'classification': 'json'
        },
        tableType: 'EXTERNAL_TABLE',
        storageDescriptor: {
          inputFormat: 'org.apache.hadoop.mapred.TextInputFormat',
          columns: [
            {
              name: 'event_id',
              type: 'string',
            },
            {
              name: 'event_type',
              type: 'string',
            },
            {
              name: 'updated_at',
              type: 'string',
            },
            {
              name: 'event_data',
              type: 'string',
            },
          ],
          location: kinesisStream.streamName,
          outputFormat: 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
          parameters:{
            'streamARN': kinesisStream.streamArn,
            'typeOfData': 'kinesis',
          },
          serdeInfo: {
            serializationLibrary: 'org.openx.data.jsonserde.JsonSerDe'
          },
        },
      },
    });
    streamTable.addDependency(streamDB);
    streamTable.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create the Apache Iceberg connection for the Glue Job
    new glue.CfnConnection(this, 'GlueIcebergConnection', {
      catalogId: cdk.Aws.ACCOUNT_ID,
      connectionInput: {
        name: connectionName,
        description: 'Apache Iceberg Connector for Glue 3.0',
        connectionType: 'MARKETPLACE',
        connectionProperties: {
          'CONNECTOR_TYPE': 'MARKETPLACE',
          'CONNECTOR_URL': 'https://709825985650.dkr.ecr.us-east-1.amazonaws.com/amazon-web-services/glue/iceberg:0.14.0-glue3.0-2',
          'CONNECTOR_CLASS_NAME': 'iceberg',
        },
      },
    });

    // Create the Glue Stream ETL job
    new glue.CfnJob(this, 'GlueStreamingEtlJob', {
      name: glueJobName,
      description: 'AWS Glue Job to load the data from Kinesis Data Streams to Apache Iceberg Table in S3',
      command: {
        name: 'gluestreaming',
        pythonVersion: '3',
        scriptLocation: `${s3Bucket.s3UrlForObject('scripts')}/${props.etlScriptName}`,
      },
      role: glueRole.roleArn,
      connections: {
        connections: [connectionName],
      },
      defaultArguments: {
        '--catalog': 'job_catalog',
        '--database_name': dbName,
        '--table_name': 'events_table',
        '--primary_key': 'event_id',
        '--partition_key': 'event_type',
        '--kinesis_table_name': streamTableName,
        '--kinesis_stream_arn': kinesisStream.streamArn,
        '--starting_position_of_kinesis_iterator': 'LATEST',
        '--iceberg_s3_path': s3Bucket.s3UrlForObject(`${dbName}/game-events`),
        '--aws_region': cdk.Aws.REGION,
        '--lock_table_name': 'iceberg_lock',
        '--window_size': '100 seconds',
        '--extra-jars': s3Bucket.s3UrlForObject('assets/aws-sdk-java-2.17.224.jar'),
        '--extra-jars-first': 'true',
        '--enable-metrics': 'true',
        '--enable-spark-ui': 'true',
        '--spark-event-logs-path': s3Bucket.s3UrlForObject(`${dbName}/game-events/spark_history_logs/`),
        '--enable-job-insights': 'false',
        '--enable-glue-datacatalog': 'true',
        '--enable-continuous-cloudwatch-log': 'true',
        '--job-bookmark-option': 'job-bookmark-disable',
        '--job-language': 'python',
        '--TempDir': s3Bucket.s3UrlForObject(`${dbName}/game-events/temp`),
      },
      executionProperty: {
        maxConcurrentRuns: 1,
      },
      glueVersion: '3.0',
      maxRetries: 0,
      timeout: 2880,
      // NOTE: Use the following to scale compute resources for large ingest data/multiple put records
      workerType: 'G.1X',
      numberOfWorkers: 2,
    });

    // Create the HTTP API for data ingestion from the game client
    const httpApi = new api.CfnApi(this, 'DataIngestionApi', {
      name: 'DataIngestionHttpApi',
      description: 'Serverless API endpoint for data ingestion',
      protocolType: 'HTTP',
    });

    // Create the CloudWatch Log Group for the HTTP API logs
    const apiLogs = new logs.LogGroup(this, 'IngestionApiLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // Define the API auto deployment stage
    new api.CfnStage(this, 'DataIngestionApiStage', {
      apiId: httpApi.ref,
      stageName: 'prod',
      autoDeploy: true,
      accessLogSettings: {
        destinationArn: apiLogs.logGroupArn,
        format: '$context.requestId $context.requestTime $context.resourcePath $context.httpMethod $context.status $context.protocol',
      }
    });

    // Declare the Authorizer for the custom identity solution
    const authorizer = new api.CfnAuthorizer(this, 'BackendAuthorizer', {
      apiId: httpApi.ref,
      name: 'BackendAuthorizer',
      authorizerType: 'JWT',
      identitySource: ['$request.header.Authorization'],
      jwtConfiguration: {
        audience: ['gamebackend'],
        issuer: props.issuerEndpointUrl,
      }
    });

    // Create the `put_record` function to handle multiple event records into the Kinesis Stream
    const recordHandler = new lambda.Function(this, 'RecordHandler', {
      role: recordHandlerRole,
      code: lambda.Code.fromAsset('lambda', {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c', 'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
          ],
        },
      }),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.lambda_handler',
      timeout: Duration.seconds(30),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaLoggingRole,
      environment: {
        'STREAM_NAME': kinesisStream.streamName,
      },
    });
    recordHandler.addPermission('InvokeRecordHandler', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: cdk.Aws.ACCOUNT_ID,
      sourceArn: `arn:aws:execute-api:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction',
    });
    kinesisStream.grantWrite(recordHandler);
    
    // Define the integration for the `put_record` function
    const integration = new api.CfnIntegration(this, 'RecordHandlerIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: recordHandler.functionArn,
      integrationMethod: 'POST',
      payloadFormatVersion: '2.0',
    });

    new api.CfnRoute(this, 'PutRecordRoute', {
      apiId: httpApi.ref,
      routeKey: 'POST /put-record',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: `integrations/${integration.ref}`,
      authorizationScopes: ['guest', 'authenticated']
    });

    // Outputs
    new CfnOutput(this, 'GlueJobName', {value: glueJobName});
    new CfnOutput(this, 'AnalyticsIntegrationBackendEndpointUrl', {value: `${httpApi.attrApiEndpoint}/prod/put-record`});
  }
}
