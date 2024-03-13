import * as cdk from 'aws-cdk-lib';
import { RemovalPolicy, Duration, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kinesis from 'aws-cdk-lib/aws-kinesis';
import * as api from 'aws-cdk-lib/aws-apigatewayv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as lambda from 'aws-cdk-lib/aws-lambda';

// Define custom stack properties
interface DeltaLakeIntegrationBackendProps extends cdk.StackProps {
  issuerEndpointUrl : string;
}

export class DeltaLakeIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DeltaLakeIntegrationBackendProps) {
    super(scope, id, props);

    // Create an S3 Bucket to serve as the delta lake object store
    const s3Bucket = new s3.Bucket(this, 'DeltaLakeBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      versioned: true,
      removalPolicy: RemovalPolicy.RETAIN,
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

    // Create the Kinesis Data Stream for data ingest
    const kinesisStream = new kinesis.Stream(this, 'IngestStream', {
      streamName: 'DataIngestStream',
      streamMode: kinesis.StreamMode.ON_DEMAND,
      retentionPeriod: Duration.hours(24),
    });
    kinesisStream.applyRemovalPolicy(RemovalPolicy.DESTROY);

    // Create the HTTP API for data ingestion from the game client
    const httpApi = new api.CfnApi(this, 'DataIngestionApi', {
      name: 'DataIngestionHttpApi',
      description: 'Serverless API endpoint for data ingestion',
      protocolType: 'HTTP',
    });

    // Create the CloudWatch Log Group for the HTTP API logs
    const apiLogs = new logs.LogGroup(this, 'IngestionApiLogs', {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.RETAIN,
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
      memorySize: 256,
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
    new CfnOutput(this, 'DeltaLakeIntegrationBackendEndpointUrl', {value: `${httpApi.attrApiEndpoint}/prod/put-record`});
    new CfnOutput(this, 'DeltaLakeIntegrationBackendEndpointUrlWithoutResource', {value: `${httpApi.attrApiEndpoint}/prod/`});
  }
}
