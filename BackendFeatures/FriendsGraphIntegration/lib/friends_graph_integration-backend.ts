// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigatewayv2';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as neptune from '@aws-cdk/aws-neptune-alpha';

import { Construct } from 'constructs';

// Custom stack properties
export interface GameBackendFriendsIntegrationBackendProps extends cdk.StackProps {
  // custom identity provider issuer URL
  issuerEndpointUrl: string,
  vpc: ec2.Vpc;
}

// Friends graph backend integration with Amazon Neptune
export class FriendsGraphIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GameBackendFriendsIntegrationBackendProps) {
    super(scope, id, props);

    // Define a CloudFormation parameter for the issuer endpoint URL
    const issuerEndpointUrl = new cdk.CfnParameter(this, 'IssuerEndpointUrl', {
      type: 'String',
      description: 'The URL of the issuer endpoint',
      default: props.issuerEndpointUrl,
    });

    //#region --SECURITY GROUP

    // Create a new security group
    const friendsIntegrationSG = new ec2.SecurityGroup(this, 'ClusterSG', {
        vpc: props.vpc,
        description: 'Security group for the Neptune cluster',
        allowAllOutbound: true
    });

    // Add an ingress rule to the security group
    friendsIntegrationSG.addIngressRule(
        friendsIntegrationSG,
        ec2.Port.tcp(8182),
        'Neptune port 8182'
    );

    //#endregion

    //#region --NEPTUNE CLUSTER

    // Neptune cluster
    const neptuneCluster = new neptune.DatabaseCluster(this, 'NeptuneCluster', {
      vpc: props.vpc,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      instanceType: neptune.InstanceType.R6G_LARGE,
      iamAuthentication: true,
      autoMinorVersionUpgrade: true,
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      securityGroups: [friendsIntegrationSG]
    });

    // Output the cluster endpoint
    new cdk.CfnOutput(this, 'NeptuneClusterEndpoint', {
        description: 'Neptune cluster endpoint',
        value: neptuneCluster.clusterEndpoint.socketAddress,
    });

    //#endregion

    //#region --LAMBDA FUNCTIONS

    // Lambda function roles
    const lambdaRuntimePolicy = new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents','logs:DeleteRetentionPolicy','logs:PutRetentionPolicy'],
      resources: ['*'],
    });
    const lambdaRuntimeRole = new iam.Role(this, 'LambdaRuntimeRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [ iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole') ]
    });
    lambdaRuntimeRole.addToPolicy(lambdaRuntimePolicy);
    iam.ManagedPolicy.fromManagedPolicyName

    // Lambda functions

    // Player functions
    const setPlayerFunction = new lambda.Function(this, 'SetPlayerFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'set_player.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'SetPlayerFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(setPlayerFunction);

    const getPlayerFunction = new lambda.Function(this, 'GetPlayerFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'get_player.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'GetPlayerFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(getPlayerFunction);

    /*  
    NOTE: DeletePlayerFunction is provided to show how to drop a player vertex
    but is not mapped to a API gateway resource. This method might generally not
    be called by the game client.
    */
    const deletePlayerFunction = new lambda.Function(this, 'DeletePlayerFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'delete_player.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'DeletePlayerFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(deletePlayerFunction);

    // Friend functions
    const setFriendFunction = new lambda.Function(this, 'SetFriendFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'set_friend.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'SetFriendFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(setFriendFunction);

    const deleteFriendFunction = new lambda.Function(this, 'DeleteFriendFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'delete_friend.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'DeleteFriendFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(deleteFriendFunction);

    // Friends functions
    const getFriendsFunction = new lambda.Function(this, 'GetFriendsFunction', {
      role: lambdaRuntimeRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'get_friends.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [friendsIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'GetFriendsFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(getFriendsFunction);

    //#endregion

    //#region --API GATEWAY

    // HTTP Api for the backend
    const httpApi = new apigateway.CfnApi(this, 'FriendsGraphIntegrationHttpApi', {
      name: 'FriendsGraphIntegrationHttpApi',
      protocolType: 'HTTP',
      description: 'Friends Graph Integration HTTP API',
    });

    // Define a log group for the HTTP Api logs
    const httpApiLogGroup = new logs.LogGroup(this, 'FriendsGraphIntegrationHttpApiLogs', {
    });

    // Stage for the HTTP Api
    const httpApiStage = new apigateway.CfnStage(this, 'FriendsGraphIntegrationHttpApiProdStage', {
      apiId: httpApi.ref,
      stageName: 'prod',
      autoDeploy: true,
      accessLogSettings: {
        destinationArn: httpApiLogGroup.logGroupArn,
        format: '$context.requestId $context.requestTime $context.resourcePath $context.httpMethod $context.status $context.protocol'
      }
    });

    // Access point for the API
    new cdk.CfnOutput(this, 'BackendEndpointUrl', { value: httpApi.attrApiEndpoint + "/prod"});

    // Authorizer that uses our custom identity solution
    const authorizer = new apigateway.CfnAuthorizer(this, 'BackendAuthorizer', {
      apiId: httpApi.ref,
      name: 'BackendAuthorizer',
      authorizerType: 'JWT',
      identitySource: ['$request.header.Authorization'],
      jwtConfiguration: {
        audience: ['gamebackend'],
        issuer: issuerEndpointUrl.valueAsString,
      }
    });

    //#endregion

    //#region --API GATEWAY INVOKE LAMBDA PERMISSIONS

    // Allow the HttpApi to invoke the setPlayerFunction function
    setPlayerFunction.addPermission('InvokeSetPlayerFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    // Allow the HttpApi to invoke the getPlayerFunction function
    getPlayerFunction.addPermission('InvokeGetPlayerFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    // Allow the HttpApi to invoke the setFriendFunction function
    setFriendFunction.addPermission('InvokeSetFriendFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    // Allow the HttpApi to invoke the deleteFriendFunction function
    deleteFriendFunction.addPermission('InvokeDeleteFriendFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    // Allow the HttpApi to invoke the getFriendsFunction function
    getFriendsFunction.addPermission('InvokeGetFriendsFunction', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      sourceAccount: this.account,
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:${httpApi.ref}/prod/*`,
      action: 'lambda:InvokeFunction'
    });

    //#endregion

    //#region --MAP API GATEWAY LAMBDA ROUTE

    // Define setPlayer integration and route
    const setPlayerIntegration = new apigateway.CfnIntegration(this, 'SetPlayerIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: setPlayerFunction.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'SetPlayerRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /set-player',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + setPlayerIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    // Define getPlayer integration and route
    const getPlayerIntegration = new apigateway.CfnIntegration(this, 'GetPlayerIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: getPlayerFunction.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'GetPlayerRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /get-player',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + getPlayerIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    // Define setFriend integration and route
    const setFriendIntegration = new apigateway.CfnIntegration(this, 'SetFriendIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: setFriendFunction.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'SetFriendRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /set-friend',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + setFriendIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    // Define deleteFriend integration and route
    const deleteFriendIntegration = new apigateway.CfnIntegration(this, 'DeleteFriendIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: deleteFriendFunction.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'DeleteFriendRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /delete-friend',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + deleteFriendIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    // Define getFriends integration and route
    const getFriendsIntegration = new apigateway.CfnIntegration(this, 'GetFriendsIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: getFriendsFunction.functionArn,
      integrationMethod: 'GET',
      payloadFormatVersion: '2.0'
    });

    new apigateway.CfnRoute(this, 'GetFriendsRoute', {
      apiId: httpApi.ref,
      routeKey: 'GET /get-friends',
      authorizationType: 'JWT',
      authorizerId: authorizer.ref,
      target: "integrations/" + getFriendsIntegration.ref,
      authorizationScopes: ["guest", "authenticated"]
    });

    //#endregion

  }
}
