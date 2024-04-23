// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { Stack, StackProps, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecs_patterns from "aws-cdk-lib/aws-ecs-patterns";
import { DockerImageAsset } from 'aws-cdk-lib/aws-ecr-assets';
import * as path from 'path';
import * as s3  from 'aws-cdk-lib/aws-s3';
import { NagSuppressions } from 'cdk-nag';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cdk from 'aws-cdk-lib';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';

export interface SimpleWebsocketChatProps extends StackProps {
  // custom identity provider issuer URL
  issuerEndpointUrl: string;
}
// Define a CDK stack
export class SimpleWebsocketChat extends Stack {
  constructor(scope: Construct, id: string, props: SimpleWebsocketChatProps) {
    super(scope, id, props);

    // Define a CloudFormation parameter for the issuer endpoint URL
    const issuerEndpointUrl = new cdk.CfnParameter(this, 'IssuerEndpointUrl', {
      type: 'String',
      description: 'The URL of the issuer endpoint',
      default: props.issuerEndpointUrl,
    });

    // Bucket for logging ELB and VPC access
    var loggingBucket = new s3.Bucket(this, 'IdentityComponentLoggingBucket', {
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      serverAccessLogsPrefix: 'logging-bucket-access-logs',
    });

    // VPC for our Fargate service, using 2 AZs to reduce the amount of NAT Gateways, feel free to use 3 for higher availability
    const vpc = new ec2.Vpc(this, "MyVpc", {
      maxAzs: 2
    });

    // enable flow logs for the VPC
    vpc.addFlowLog("VPCFlowLogs", {
      destination: ec2.FlowLogDestination.toS3(loggingBucket, "node-js-fargate-api-vpc-flow-logs"),
      trafficType: ec2.FlowLogTrafficType.REJECT
    });

    // ECS cluster to host the service in the VPC
    const cluster = new ecs.Cluster(this, "MyCluster", {
      vpc: vpc,
      containerInsights: true
    });

    // Define a security group for our ECS service
    const serviceSecurityGroup = new ec2.SecurityGroup(this, 'ServiceSecurityGroup', {
      vpc: vpc,
      description: 'Security group for ECS service',
      allowAllOutbound: true
    });

    // Define a security group for our ElastiCache cluster
    const cacheSecurityGroup = new ec2.SecurityGroup(this, 'CacheSecurityGroup', {
      vpc: vpc,
      description: 'Security group for ElastiCache cluster',
      allowAllOutbound: true,
    });

    // Allow access from the ECS service security group to the cache security group
    cacheSecurityGroup.connections.allowFrom(serviceSecurityGroup, ec2.Port.tcp(6379));

    // Define a Serverless ElastiCache for Redis cluster to host our chats
    const cfnServerlessCache = new elasticache.CfnServerlessCache(this, 'ServerlessChatCache', {
      engine: 'redis',
      serverlessCacheName: 'simple-websocket-chat-cache',
      securityGroupIds: [cacheSecurityGroup.securityGroupId],
      subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds
    });

    // Define our container image with our custom Node.js server code
    const containerAsset = new DockerImageAsset(this, 'SimpleWebsocketApp', {
      directory: path.join(__dirname, '../SimpleWebsocketApp'),
    });

    // create task definition for our Fargate service
    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      memoryLimitMiB: 2048,
      cpu: 1024
    });
    // Set cpu and memory for Task definition
    taskDefinition.addContainer('backendservice', {
      image: ecs.ContainerImage.fromDockerImageAsset(containerAsset),
      // Add environment variable with issuer endpoint
      environment: {
        ISSUER_ENDPOINT: issuerEndpointUrl.valueAsString,
        REDIS_ENDPOINT: cfnServerlessCache.attrEndpointAddress
      },
      portMappings: [
        {
          containerPort: 80,
          hostPort: 80,
          protocol: ecs.Protocol.TCP
        },
        {
          containerPort: 8080,
          hostPort: 8080,
          protocol: ecs.Protocol.TCP
        }],
      logging: new ecs.AwsLogDriver({
        streamPrefix: 'SimpleWebsocketBackendService',
        logRetention: logs.RetentionDays.ONE_MONTH
      })
    });

    // Add the X-ray daemon to the task definition
    taskDefinition.addContainer('backendservice-xray', {
      containerName: "xray-daemon",
      image: ecs.ContainerImage.fromRegistry('amazon/aws-xray-daemon'),
      portMappings: [
        {
            hostPort: 2000,
            containerPort: 2000,
            protocol: ecs.Protocol.UDP
        }
      ],
      logging: new ecs.AwsLogDriver({
        streamPrefix: 'XRayDaemon'
      }),
      memoryLimitMiB: 256,
      cpu: 32
    });
    // Allow access to X-ray
    taskDefinition.addToTaskRolePolicy(
      new iam.PolicyStatement({
        actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords'],
        effect: iam.Effect.ALLOW,
        resources: ['*']
      })
    );

    NagSuppressions.addResourceSuppressions(taskDefinition, [
      { id: 'AwsSolutions-ECS2', reason: 'Task definition environment variables dont include any secrets or credentials.' }
    ], true);

    NagSuppressions.addResourceSuppressions(taskDefinition, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard ECS execution role from CDK, all custom access resource restricted.' }
    ], true);

    // Create a load-balanced Fargate service and make it public
    var fargateService = new ecs_patterns.ApplicationLoadBalancedFargateService(this, "FargateSampleNodeJsService", {
      cluster: cluster, 
      cpu: 512,
      desiredCount: 3, // We start with 3 nodes in the service for the test
      taskDefinition: taskDefinition,
      memoryLimitMiB: 1024,
      publicLoadBalancer: true,
      securityGroups: [serviceSecurityGroup],
      listenerPort: 80
    });
    
    // Set the target group health check port to 8080 that hosts our health check HTTP server
    fargateService.targetGroup.configureHealthCheck({
      port: "8080"
    });

    // Allow access from the Service ALB security group to the ECS service 80 and 8080 ports
    serviceSecurityGroup.connections.allowFrom(fargateService.loadBalancer, ec2.Port.tcp(80));
    serviceSecurityGroup.connections.allowFrom(fargateService.loadBalancer, ec2.Port.tcp(8080));

    // Add the Websocket listener
    //fargateService.loadBalancer.addListener("WebsocketListener", {
    //  port: 80,
    //  defaultTargetGroups: [fargateService.targetGroup]
    //});

    // Add security group access to port 80 on the load balancer
    fargateService.loadBalancer.connections.allowFromAnyIpv4(ec2.Port.tcp(80));
    // Add security group access to port 80 on the service from the load balancer
    fargateService.service.connections.allowFrom(fargateService.loadBalancer, ec2.Port.tcp(80));

    // Setup scaling based on CPU load for the service scaling between 3 and 10 Tasks
    // NOTE: You would set an appropriate maximum here to match your game's traffic!
    const scalableTaskCount = fargateService.service.autoScaleTaskCount({
      minCapacity: 3,
      maxCapacity: 10
    });
    scalableTaskCount.scaleOnCpuUtilization('CpuUtilizationScaling', {
        targetUtilizationPercent: 80 // We're running individual tasks up to 80% CPU utilization before scaling out
    });

    // enable acess logs for the Fargate service ELB
    fargateService.loadBalancer.logAccessLogs(loggingBucket, "node-js-fargate-api-logs");

  }
}