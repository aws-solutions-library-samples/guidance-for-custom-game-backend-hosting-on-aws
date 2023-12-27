#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AmazonGameLiftIntegrationStack } from '../lib/amazon_gamelift_integration-gamelift-resources';
import { AmazonGameLiftIntegrationBackend } from '../lib/amazon_gamelift_integration-backend';

// TODO: Set this to your issuer endpoint URL
const ISSUER_ENDPOINT = "https://xxxxx.cloudfront.net";

const app = new cdk.App();

// The backend APIs for matchmaking etc.
new AmazonGameLiftIntegrationBackend(app, 'AmazonGameLiftIntegrationBackend', {
  issuerEndpointUrl: ISSUER_ENDPOINT
});

// All the Amazon GameLift resources including fleet, queue, and matchmaking
new AmazonGameLiftIntegrationStack(app, 'AmazonGameLiftIntegrationGameLiftResources', {
  serverBinaryName: "GameLiftSampleServer"
});