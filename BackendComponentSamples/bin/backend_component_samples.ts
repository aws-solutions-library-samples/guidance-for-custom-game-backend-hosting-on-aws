#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { PythonServerlessHttpApiStack } from '../lib/PythonServerlessHttpApiStack';
import { NodeJsFargateApiStack } from '../lib/NodeJsFargateApiStack';
import { AwsSolutionsChecks } from 'cdk-nag';
import { NagSuppressions } from 'cdk-nag';
import { App, Aspects } from 'aws-cdk-lib';

// TODO: Set your identity component issuer URL endpoint here
const issuerEndpointUrl = "https://d2q5rkfh5acrzc.cloudfront.net"

const app = new cdk.App();

// Sample Python Serverless HTTP API Stack with player data set and get, using the custom identity solution
var pythonServerlessHttpApiStack = new PythonServerlessHttpApiStack(app, 'PythonServerlessHttpApiStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl
});

// Sample Node.js ECS Fargate service hosted behind an ALB with with player data set and get, using the custom identity solution
var nodeJsFargateStack = new NodeJsFargateApiStack(app, 'NodeJsFargateApiStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl
});

// CDK-nag
Aspects.of(app).add(new AwsSolutionsChecks());

// Add nag suppresssion for AwsSolutions-EC23
NagSuppressions.addStackSuppressions(nodeJsFargateStack, [
  { id: 'AwsSolutions-EC23', reason: 'We have to allow 0.0.0.0/0 for the ALB for game client access' }
]);