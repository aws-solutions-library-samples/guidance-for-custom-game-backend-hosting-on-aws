#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { FriendsGraphIntegrationBackend } from '../lib/friends_graph_integration-backend';
import { GameBackendFriendsIntegrationVpcStack } from '../lib/friends_graph_integration-vpc-stack';

// TODO: Set your identity component issuer URL endpoint here
const issuerEndpointUrl = "https://YOURENDPOINT.cloudfront.net"

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Application'] = 'GameBackendFriendsGraphIntegration';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

const app = new cdk.App();

const vpcStack = new GameBackendFriendsIntegrationVpcStack(app, 'FriendsGraphIntegrationVPC', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl,
});

var backend = new FriendsGraphIntegrationBackend(app, 'FriendsGraphIntegrationBackend', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
  issuerEndpointUrl: issuerEndpointUrl,
  vpc: vpcStack.vpc,
});
backend.addDependency(vpcStack);

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  cdk.Tags.of(vpcStack).add(key, tags[key]);
});
Object.keys(tags).forEach(key => {
  cdk.Tags.of(backend).add(key, tags[key]);
});