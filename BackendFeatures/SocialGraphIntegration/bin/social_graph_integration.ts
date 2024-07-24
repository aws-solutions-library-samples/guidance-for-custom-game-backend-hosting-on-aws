#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { SocialGraphIntegrationBackend } from '../lib/social_graph_integration-backend';
import { GameBackendSocialIntegrationVpcStack } from '../lib/social_graph_integration-vpc-stack';

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Application'] = 'GameBackendSocialGraphIntegration';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

const app = new cdk.App();

const vpcStack = new GameBackendSocialIntegrationVpcStack(app, 'SocialGraphIntegrationVPC');
var backend = new SocialGraphIntegrationBackend(app, 'SocialGraphIntegrationBackend', {
  vpc: vpcStack.vpc,
});
backend.addDependency(vpcStack);

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  cdk.Tags.of(backend).add(key, tags[key]);
});