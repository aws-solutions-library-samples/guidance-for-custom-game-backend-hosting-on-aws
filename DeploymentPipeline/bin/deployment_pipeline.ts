#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DeploymentPipelineStack } from '../lib/deployment_pipeline-stack';

const app = new cdk.App();
new DeploymentPipelineStack(app, 'DeploymentPipelineStack', {
});