#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { AmazonGameLiftIntegrationStack } from '../lib/amazon_gamelift_integration-gamelift-resources';
import { AmazonGameLiftIntegrationBackend } from '../lib/amazon_gamelift_integration-backend';
import { AwsSolutionsChecks } from 'cdk-nag';
import { NagSuppressions } from 'cdk-nag';
import { App, Aspects, Tags } from 'aws-cdk-lib';

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Application'] = 'GameBackendAmazonGameLiftIntegration';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

// TODO: Set this to your issuer endpoint URL
const ISSUER_ENDPOINT = "https://xxxxx.cloudfront.net";

const app = new cdk.App();

// The backend APIs for matchmaking etc.
var backend = new AmazonGameLiftIntegrationBackend(app, 'AmazonGameLiftIntegrationBackend', {
  issuerEndpointUrl: ISSUER_ENDPOINT
});

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  Tags.of(backend).add(key, tags[key]);
});

// All the Amazon GameLift resources including fleet, queue, and matchmaking
var gamelift = new AmazonGameLiftIntegrationStack(app, 'AmazonGameLiftIntegrationGameLiftResources', {
  serverBinaryName: "GameLiftSampleServer"
});

// CDK-nag
Aspects.of(app).add(new AwsSolutionsChecks());