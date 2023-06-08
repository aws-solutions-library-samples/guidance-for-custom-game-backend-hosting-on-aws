// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { Stack, StackProps, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecs_patterns from "aws-cdk-lib/aws-ecs-patterns";
import { DockerImageAsset } from 'aws-cdk-lib/aws-ecr-assets';
import * as path from 'path';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3  from 'aws-cdk-lib/aws-s3';
import { NagSuppressions } from 'cdk-nag';

export interface NodeJsFargateApiStackProps extends StackProps {
  // custom identity provider issuer URL
  issuerEndpointUrl: string;
}
// Define a CDK stack
export class NodeJsFargateApiStack extends Stack {
  constructor(scope: Construct, id: string, props: NodeJsFargateApiStackProps) {
    super(scope, id, props);

    // Bucket for logging ELB and VPC access
    var loggingBucket = new s3.Bucket(this, 'IdentityComponentLoggingBucket', {
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      serverAccessLogsPrefix: 'logging-bucket-access-logs',
    });

    // VPC for our Fargate service
    const vpc = new ec2.Vpc(this, "MyVpc", {
      maxAzs: 3
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

    // Define a DynamoDB table to store player data
    const playerDataTable = new dynamodb.Table(this, 'NodeJsContainerSamplePlayerDataTable', {
      partitionKey: {
        name: 'UserID',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST, // automatic scaling and billing per request
      pointInTimeRecovery: true, // enable point in time recovery backups
    });

    // Define our container image with our custom Node.js server code
    const containerAsset = new DockerImageAsset(this, 'NodeJsFargateApiApp', {
      directory: path.join(__dirname, '../NodeJsFargateApi'),
    });

    const taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDef');
    taskDefinition.addContainer('backendservice', {
      image: ecs.ContainerImage.fromDockerImageAsset(containerAsset),
      // Add environment variable with issuer endpoint
      environment: {
        ISSUER_ENDPOINT: props.issuerEndpointUrl,
        PLAYER_DATA_TABLE_NAME: playerDataTable.tableName
      },
      portMappings: [
        {
          containerPort: 80,
          hostPort: 80,
          protocol: ecs.Protocol.TCP
        }],
      logging: new ecs.AwsLogDriver({
        streamPrefix: 'NodeJsFargateSampleBackendService'
      })
    });
    // Allow the backend service to read and write data to the player data table
    playerDataTable.grantReadWriteData(taskDefinition.taskRole);

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
    });

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