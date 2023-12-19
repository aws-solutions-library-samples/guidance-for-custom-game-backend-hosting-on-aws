import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import * as gamelift from 'aws-cdk-lib/aws-gamelift';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { CfnOutput, Duration } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';

// Define custom stack properties
interface AmazonGameLiftIntegrationBackendProps extends cdk.StackProps {
  issuerEndpointUrl : string;
}

export class AmazonGameLiftIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AmazonGameLiftIntegrationBackendProps) {
    super(scope, id, props);

    // Define an SNS topic as the FlexMatch notification target
    const topic = new sns.Topic(this, 'FlexMatchEventsTopic');

    // Add a policy that allows gamelift.amazonaws.com to publish events
    topic.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['sns:Publish'],
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('gamelift.amazonaws.com')],
      resources: [topic.topicArn],
    }));

    // Export the SNS topic ARN as an output for the GameLift stack to use
    new cdk.CfnOutput(this, 'SnsTopicArn', {
      value: topic.topicArn,
      description: 'The ARN of the SNS topic used for FlexMatch notifications',
      exportName: 'AmazonGameLiftSampleSnsTopicArn',
    });

    //// HTTP API ////

    // HTTP Api for the backend
    const httpApi = new apigateway.CfnApi(this, 'AmazonGameLiftIntegrationApi', {
      name: 'AmazonGameLiftIntegrationApi',
      protocolType: 'HTTP',
      description: 'Amazon GameLift Integration HTTP API',
    });
    // Define a log group for the HTTP Api logs
    const httpApiLogGroup = new logs.LogGroup(this, 'AmazonGameLiftIntegrationApiLogs', {
    });

    // Define a auto deployed Stage for the HTTP Api
    const httpApiStage = new apigateway.CfnStage(this, 'HttpApiProdStage', {
      apiId: httpApi.ref,
      stageName: 'prod',
      autoDeploy: true,
      accessLogSettings: {
        destinationArn: httpApiLogGroup.logGroupArn,
        format: '$context.requestId $context.requestTime $context.resourcePath $context.httpMethod $context.status $context.protocol'
      }
    });

    // Access point for the API
    new CfnOutput(this, 'AmazonGameLiftIntegrationBackendEndpointUrl', { value: httpApi.attrApiEndpoint + "/prod"});
    
    // Authorizer that uses our custom identity solution
    const authorizer = new apigateway.CfnAuthorizer(this, 'BackendAuthorizer', {
      apiId: httpApi.ref,
      name: 'BackendAuthorizer',
      authorizerType: 'JWT',
      identitySource: ['$request.header.Authorization'],
      jwtConfiguration: {
        audience: ['gamebackend'],
        issuer: props.issuerEndpointUrl
      }
    });

    // DATABASE RESOURCES

    // Define a DynamoDB table for matchmaking tickets
    const matchmakingTicketsTable = new cdk.aws_dynamodb.Table(this, 'MatchmakingTable', {
      partitionKey: {
        name: 'TicketID',
        type: cdk.aws_dynamodb.AttributeType.STRING
        },
      billingMode: cdk.aws_dynamodb.BillingMode.PAY_PER_REQUEST, // automatic scaling and billing per request
      pointInTimeRecovery: true, // enable point in time recovery backups
      timeToLiveAttribute: 'ExpirationTime', // TTL field to get rid off old matchmaking tickets. Backend has to set this!
    });

    // LAMBDA FUNCTIONS ///

    // The shared policy for basic Lambda access needs for logging. This is similar to the managed Lambda Execution Policy
    const lambdaBasicPolicy = new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
      resources: ['*'],
    });

    // The shared role for the custom resources that set up Lambda logging
    const logsManagementPolicy = new iam.PolicyStatement({
      actions: ['logs:DeleteRetentionPolicy','logs:PutRetentionPolicy'],
      resources: ['*'],
    } );
    const lambdaLoggingRole = new iam.Role(this, 'LambdaLoggingRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      inlinePolicies: {
        'LambdaLoggingPolicy': new iam.PolicyDocument({
          statements: [logsManagementPolicy],
        }),
        'LambdaBasicPolicy': new iam.PolicyDocument({
          statements: [lambdaBasicPolicy],
        })
      }
    });

    // Backend API functions
    this.create_backend_lambda_functions(lambdaBasicPolicy, httpApi, authorizer, lambdaLoggingRole, matchmakingTicketsTable);

    // Matchmaking tickets processing
    this.create_process_matchmaking(lambdaBasicPolicy, lambdaLoggingRole, topic, matchmakingTicketsTable);

  }


  // Creates the backend APIs as Lambda functions that register to the HttpAPI
  private create_backend_lambda_functions(lambdaBasicPolicy : iam.PolicyStatement, httpApi : apigateway.CfnApi, 
                                          authorizer: apigateway.CfnAuthorizer, lambdaLoggingRole : iam.Role,
                                          matchmakingTicketsTable : cdk.aws_dynamodb.Table) {

    // Define functions to request matchmaking and check match status
    const request_matchmaking_function_role = new iam.Role(this, 'RequestMatchmakingFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    request_matchmaking_function_role.addToPolicy(lambdaBasicPolicy);
    // Add GameLift API access to the role
    request_matchmaking_function_role.addToPolicy(new iam.PolicyStatement({
      actions: ['gamelift:StartMatchmaking'],
      resources: ['*'],
      effect: iam.Effect.ALLOW
    }));
    const request_matchmaking = new lambda.Function(this, 'RequestMatchmaking', {
      role: request_matchmaking_function_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'request_matchmaking.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaLoggingRole,
      environment: {
        "MATCHMAKING_CONFIGURATION": "SampleFlexMatchConfiguration" // NOTE: We're using a fixed name here that we know the other stack will use!
      }
    });

    // Allow the HttpApi to invoke the set_player_data function
    request_matchmaking.addPermission('InvokeRequestMatchmakingFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    NagSuppressions.addResourceSuppressions(request_matchmaking_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Define request matchmaking integration and route
    const requestMatchmakingIntegration = new apigateway.CfnIntegration(this, 'RequestMatchmakingIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: request_matchmaking.functionArn,
      integrationMethod: 'POST',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'RequestMatchmakingRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /request-matchmaking',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + requestMatchmakingIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    const get_match_status_role = new iam.Role(this, 'GetMatchStatusRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    get_match_status_role.addToPolicy(lambdaBasicPolicy);
    const get_match_status = new lambda.Function(this, 'GetMatchStatus', {
      role: get_match_status_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'get_match_status.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaLoggingRole,
      environment: {
        "MATCHMAKING_TICKETS_TABLE" : matchmakingTicketsTable.tableName
      }
    });
    matchmakingTicketsTable.grantReadData(get_match_status);

    // Allow the HttpApi to invoke the get_match_status function
    get_match_status.addPermission('InvokeMatchStatusFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    NagSuppressions.addResourceSuppressions(get_match_status_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Define set-player-data integration and route
    const getMatchStatusIntegration = new apigateway.CfnIntegration(this, 'GetMatchStatusIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: get_match_status.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'GetMatchStatusRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /get-match-status',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + getMatchStatusIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });
  }

  // Defines the Lambda function to process matchmaking tickets and subscribes it to the SNS topic
  private create_process_matchmaking(lambdaBasicPolicy : iam.PolicyStatement, lambdaLoggingRole : iam.Role, subscriptionTopic : sns.Topic, ticketsTable : cdk.aws_dynamodb.Table) {
    
    const process_matchmaking_events_role = new iam.Role(this, 'ProcessMatchmakingEventsRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    process_matchmaking_events_role.addToPolicy(lambdaBasicPolicy);

    const process_matchmaking_events = new lambda.Function(this, 'ProcessMatchmakingEvents', {
      role: process_matchmaking_events_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'process_matchmaking_events.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaLoggingRole,
      environment: {
        "MATCHMAKING_TICKETS_TABLE" : ticketsTable.tableName
      }
    });
    // Add write access to the Matchmaking tickets table
    ticketsTable.grantReadWriteData(process_matchmaking_events);
    
    // subscribe to the topic
    const subscription = new sns.Subscription(this, 'ProcessMatchmakingEventsSubscription', {
      topic: subscriptionTopic,
      protocol: sns.SubscriptionProtocol.LAMBDA,
      endpoint: process_matchmaking_events.functionArn
    });

    // Grant SNS access to invoke the function
    process_matchmaking_events.addPermission('InvokeProcessMatchmakingEventsFunction', {
      principal: new iam.ServicePrincipal('sns.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: subscriptionTopic.topicArn,
      action: 'lambda:InvokeFunction'
    });
  }

}
