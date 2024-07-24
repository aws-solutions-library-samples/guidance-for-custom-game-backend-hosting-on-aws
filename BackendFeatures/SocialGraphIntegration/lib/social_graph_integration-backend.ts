// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as neptune from '@aws-cdk/aws-neptune-alpha';

import { Construct } from 'constructs';

// Custom stack properties
export interface GameBackendSocialIntegrationBackendProps extends cdk.StackProps {
  vpc: ec2.Vpc;
}

// Social graph backend integration with Amazon Neptune
export class SocialGraphIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: GameBackendSocialIntegrationBackendProps) {
    super(scope, id, props);

    // Create a new security group
    const socialIntegrationSG = new ec2.SecurityGroup(this, 'ClusterSG', {
        vpc: props.vpc,
        description: 'Security group for the Neptune cluster',
        allowAllOutbound: true
    });

    // Add an ingress rule to the security group
    socialIntegrationSG.addIngressRule(
        socialIntegrationSG,
        ec2.Port.tcp(8182),
        'Neptune port 8182'
    );

    // Neptune cluster
    const neptuneCluster = new neptune.DatabaseCluster(this, 'NeptuneCluster', {
      vpc: props.vpc,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      instanceType: neptune.InstanceType.R6G_LARGE,
      iamAuthentication: true,
      autoMinorVersionUpgrade: true,
      deletionProtection: false,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      securityGroups: [socialIntegrationSG]
    });

    // Output the cluster endpoint
    new cdk.CfnOutput(this, 'NeptuneClusterEndpoint', {
        description: 'Neptune cluster endpoint',
        value: neptuneCluster.clusterEndpoint.socketAddress,
    });

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
    const playerPostFunction = new lambda.Function(this, 'PlayerPostFunction', {
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
      handler: 'player_post.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'PlayerPostFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(playerPostFunction);

    const playerGetFunction = new lambda.Function(this, 'PlayerGetFunction', {
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
      handler: 'player_get.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'PlayerGetFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(playerGetFunction);

    const playerDeleteFunction = new lambda.Function(this, 'PlayerDeleteFunction', {
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
      handler: 'player_delete.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'PlayerDeleteFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(playerDeleteFunction);

    // Friend functions
    const friendPostFunction = new lambda.Function(this, 'FriendPostFunction', {
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
      handler: 'friend_post.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'FriendPostFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(friendPostFunction);

    const friendDeleteFunction = new lambda.Function(this, 'FriendDeleteFunction', {
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
      handler: 'friend_delete.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'FriendDeleteFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(friendDeleteFunction);

    // Friends functions
    const friendsGetFunction = new lambda.Function(this, 'FriendsGetFunction', {
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
      handler: 'friends_get.lambda_handler',
      timeout: cdk.Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 1024,
      logRetention: logs.RetentionDays.ONE_MONTH,
      logRetentionRole: lambdaRuntimeRole,
      vpcSubnets: { subnets: props.vpc.privateSubnets },
      vpc: props.vpc,
      securityGroups: [socialIntegrationSG],
      environment: {
        'NEPTUNE_ENDPOINT': neptuneCluster.clusterEndpoint.socketAddress,
        'USE_IAM': 'true',
        'POWERTOOLS_SERVICE_NAME': 'FriendsGetFunction',
        'POWERTOOLS_LOG_LEVEL': 'INFO',
      }
    });
    neptuneCluster.grantConnect(friendsGetFunction);

    // API Gateway
    const apiGwLogGroup = new logs.LogGroup(this, "SocialGraphIntegrationApiLogs",
    {
      logGroupName: '/aws/apigateway/SocialGraphIntegrationApiLogs',
      retention: logs.RetentionDays.ONE_MONTH,
    });
    
    const apiGateway = new apigw.RestApi(this, 'ApiGateway', {
      restApiName: 'SocialGraphIntegrationApi',
      description: 'Social Graph Integration API',
      deployOptions: {
        accessLogDestination: new apigw.LogGroupLogDestination(apiGwLogGroup),
        accessLogFormat: apigw.AccessLogFormat.clf(),
        loggingLevel : apigw.MethodLoggingLevel.ERROR,
        tracingEnabled: true,
        stageName: 'prod',
      }
    });

    // Map player functions to API gateway
    const apiPlayerResource = apiGateway.root.addResource('player');
    apiPlayerResource.addMethod('POST', new apigw.LambdaIntegration(playerPostFunction));
    apiPlayerResource.addMethod('GET', new apigw.LambdaIntegration(playerGetFunction));
    apiPlayerResource.addMethod('DELETE', new apigw.LambdaIntegration(playerDeleteFunction));

    // Map friend functions to API gateway
    const apiFriendResource = apiGateway.root.addResource('friend');
    apiFriendResource.addMethod('POST', new apigw.LambdaIntegration(friendPostFunction));
    apiFriendResource.addMethod('DELETE', new apigw.LambdaIntegration(friendDeleteFunction));

    // Map friends functions to API gateway
    const apiFriendsResource = apiGateway.root.addResource('friends');
    apiFriendsResource.addMethod('GET', new apigw.LambdaIntegration(friendsGetFunction));
  }
}
