#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { PythonServerlessHttpApiStack } from '../lib/PythonServerlessHttpApiStack';
import { NodeJsFargateApiStack } from '../lib/NodeJsFargateApiStack';
import { AwsSolutionsChecks } from 'cdk-nag';
import { NagSuppressions } from 'cdk-nag';
import { App, Aspects, Tags } from 'aws-cdk-lib';

// TODO: Set your identity component issuer URL endpoint here
const issuerEndpointUrl = "https://YOURENDPOINT.cloudfront.net"

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Application'] = 'GameBackend';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

const app = new cdk.App();

// Sample Python Serverless HTTP API Stack with player data set and get, using the custom identity solution
var pythonServerlessHttpApiStack = new PythonServerlessHttpApiStack(app, 'PythonServerlessHttpApiStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl
});

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  Tags.of(pythonServerlessHttpApiStack).add(key, tags[key]);
});

// Sample Node.js ECS Fargate service hosted behind an ALB with with player data set and get, using the custom identity solution
var nodeJsFargateStack = new NodeJsFargateApiStack(app, 'NodeJsFargateApiStack', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl
});

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  Tags.of(nodeJsFargateStack).add(key, tags[key]);
});

// CDK-nag
Aspects.of(app).add(new AwsSolutionsChecks());

// Add nag suppresssion for AwsSolutions-EC23
NagSuppressions.addStackSuppressions(nodeJsFargateStack, [
  { id: 'AwsSolutions-EC23', reason: 'We have to allow 0.0.0.0/0 for the ALB for game client access' }
]);