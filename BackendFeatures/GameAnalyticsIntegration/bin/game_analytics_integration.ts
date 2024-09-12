#!/usr/bin/env node
import 'source-map-support/register';
import { AnalyticsIntegrationBackend } from '../lib/game_analytics_integration-stack';
import {App, Aspects, Tags} from 'aws-cdk-lib';

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Applicaiton'] = 'GameBackendAnalyticsIntegration';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

// TODO: Set this to your issuer endpoint URL
const ISSUER_ENDPOINT = '';

// TODO: Set this to the name of the the ETL Script. (Current default is to write new data to Apache Iceberg)
const STREAMING_ETL_SCRIPT = 'iceberg_writes.py';

const app = new App();

var backend = new AnalyticsIntegrationBackend(app, 'AnalyticsIntegrationBackend', {
  issuerEndpointUrl: ISSUER_ENDPOINT,
  etlScriptName: STREAMING_ETL_SCRIPT
});

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  Tags.of(backend).add(key, tags[key]);
});
