// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { Stack, StackProps, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { CfnOutput } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb'
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as logs from 'aws-cdk-lib/aws-logs';

export interface ServerlessHttpApiStackProps extends StackProps {
  // custom identity provider issuer URL
  issuerEndpointUrl: string;
}

export class PythonServerlessHttpApiStack extends Stack {
  constructor(scope: Construct, id: string, props: ServerlessHttpApiStackProps) {
    super(scope, id, props);

    // HTTP Api for the backend
    const httpApi = new apigateway.CfnApi(this, 'PythonServerlessHttpApi', {
      name: 'PythonServerlessHttpApi',
      protocolType: 'HTTP',
      description: 'Python Serverless HTTP API',
    });
    // Define a log group for the HTTP Api logs
    const httpApiLogGroup = new logs.LogGroup(this, 'PythonServerlessHttpApiLogs', {
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
    new CfnOutput(this, 'BackendEndpointUrl', { value: httpApi.attrApiEndpoint + "/prod"});
   
    // Authorizer that uses our custom identity solution
    const authorizer = new apigateway.CfnAuthorizer(this, 'BackendAuthorizer', {
      apiId: httpApi.ref,
      name: 'BackendAuthorizer',
      authorizerType: 'JWT',
      identitySource: ['$request.header.Authorization'],
      jwtConfiguration: {
        audience: ['gamebackend'],
        issuer: props.issuerEndpointUrl,
      }
    });

    // Define a DynamoDB table to store player data
    const playerDataTable = new dynamodb.Table(this, 'PlayerDataTable', {
      partitionKey: {
        name: 'UserID',
        type: dynamodb.AttributeType.STRING
        },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // automatic scaling and billing per request
      pointInTimeRecovery: true, // enable point in time recovery backups
    });

    // The shared policy for basic Lambda access needs for logging. This is similar to the managed Lambda Execution Policy
    const lambdaBasicPolicy = new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
      resources: ['*'],
    });

    // Define simple functions to set and get player data
    const set_player_data_function_role = new iam.Role(this, 'SetPlayerDataFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    set_player_data_function_role.addToPolicy(lambdaBasicPolicy);
    const set_player_data = new lambda.Function(this, 'SetPlayerData', {
      role: set_player_data_function_role,
      code: lambda.Code.fromAsset("PythonServerlessHttpApiLambda", {}),
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: 'set_player_data.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      environment: {
        "PLAYER_DATA_TABLE": playerDataTable.tableName
      }
    });
    playerDataTable.grantReadWriteData(set_player_data);

    // Allow the HttpApi to invoke the set_player_data function
    set_player_data.addPermission('InvokeSetPlayerDataFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    NagSuppressions.addResourceSuppressions(set_player_data_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    const get_player_data_function_role = new iam.Role(this, 'GetPlayerDataFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    get_player_data_function_role.addToPolicy(lambdaBasicPolicy);
    const get_player_data = new lambda.Function(this, 'GetPlayerData', {
      role: get_player_data_function_role,
      code: lambda.Code.fromAsset("PythonServerlessHttpApiLambda", {}),
      runtime: lambda.Runtime.PYTHON_3_10,
      handler: 'get_player_data.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      environment: {
        "PLAYER_DATA_TABLE": playerDataTable.tableName
      }
    });
    playerDataTable.grantReadData(get_player_data);

    // Allow the HttpApi to invoke the get_player_data function
    get_player_data.addPermission('InvokeGetPlayerDataFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    NagSuppressions.addResourceSuppressions(get_player_data_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Define set-player-data integration and route
    const setPlayerDataIntegration = new apigateway.CfnIntegration(this, 'SetPlayerDataIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: set_player_data.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'SetPlayerDataRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /set-player-data',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + setPlayerDataIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    // Define get-player-data integration and route
    const getPlayerDataIntegration = new apigateway.CfnIntegration(this, 'GetPlayerDataIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: get_player_data.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'GetPlayerDataRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /get-player-data',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + getPlayerDataIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });
  }
}
