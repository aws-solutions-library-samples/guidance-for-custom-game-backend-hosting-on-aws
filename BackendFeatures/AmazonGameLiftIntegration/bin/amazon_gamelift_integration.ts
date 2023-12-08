#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AmazonGameLiftIntegrationStack } from '../lib/amazon_gamelift_integration-stack';

const app = new cdk.App();
new AmazonGameLiftIntegrationStack(app, 'AmazonGameLiftIntegrationStack', {
  serverBinaryName: "GameLiftSampleServer"
});