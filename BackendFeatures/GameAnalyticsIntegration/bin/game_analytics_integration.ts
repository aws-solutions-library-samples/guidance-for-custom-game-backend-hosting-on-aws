#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DeltaLakeIntegrationBackend } from '../lib/delta_lake_integration-backend';
import {App, Aspects, Tags} from 'aws-cdk-lib';

// Set these tags to values that make sense to your company. You can define applicable tags as billing tags as well: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/custom-tags.html
let tags: { [key: string]: string } = {};
tags['Applicaiton'] = 'GameBackendDeltaLakeIntegration';
tags['Owner'] = 'MyTeam';
tags['Environment'] = 'Dev';
tags['CostCenter'] = '1000';

// TODO: Set this to your issuer endpoint URL
const ISSUER_ENDPOINT = 'https://d18cni1darfohw.cloudfront.net';

// TODO: Set this to the name of the the ETL Script
const ETL_SCRIPT = 'datalake_writes.py';

const app = new App();

var backend = new DeltaLakeIntegrationBackend(app, 'DeltaLakeIntegrationBackend', {
  issuerEndpointUrl: ISSUER_ENDPOINT,
  etlScriptName: ETL_SCRIPT
});

// Apply all the tags in the tags object to the stack
Object.keys(tags).forEach(key => {
  Tags.of(backend).add(key, tags[key]);
});
