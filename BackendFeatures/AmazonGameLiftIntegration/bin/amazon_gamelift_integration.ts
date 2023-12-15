#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AmazonGameLiftIntegrationStack } from '../lib/amazon_gamelift_integration-gamelift-resources';
import { AmazonGameLiftIntegrationBackend } from '../lib/amazon_gamelift_integration-backend';

const app = new cdk.App();

// The backend APIs for matchmaking etc.
new AmazonGameLiftIntegrationBackend(app, 'AmazonGameLiftIntegrationBackend', {
});

// All the Amazon GameLift resources including fleet, queue, and matchmaking
new AmazonGameLiftIntegrationStack(app, 'AmazonGameLiftIntegrationGameLiftResources', {
  serverBinaryName: "GameLiftSampleServer"
});