#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { CustomIdentityComponentStack } from '../lib/custom_identity_component-stack';
import { App, Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { NagSuppressions } from 'cdk-nag';

// Set to Apple App ID value (such as com.mycompany.myapp) if you want to provision the login-with-apple-id endpoint
// An empty value "" required to NOT deploy the Apple ID login endpoint
const appleIdAppId = ""
// Set to steam app ID value such as "1234567" if you want to provision the login-with-steam endpoint
// An empty value "" required to NOT deploy the Steam login endpoint
const steamAppId = ""
// Set this to the arn of a Steam secret you created with the aws cli (see https://partner.steamgames.com/doc/webapi_overview/auth for details on creating the API key)
// using aws secretsmanager create-secret --name MySteamWebApiKey --description "Steam Web Api Key" --secret-string "YOURAPIKEY"
const steamWebApiKeySecretArn = ""
// Set this to the Web Application client ID of your Google Play app, such as xyz.apps.googleusercontent.com
// An empty value "" required to Not deploy the Google Play login endpoint
const googlePlayClientId = ""
// Set to the Google play App Id (the numeric part of the client ID)
const googlePlayAppid = ""
// Set this to the arn of a Google Play Client secret you created with the aws cli (You need to create a Web application Client ID and secret in https://console.cloud.google.com/apis/credentials)
// using aws secretsmanager create-secret --name MyGooglePlayClientSecret --description "Google Play client secret" --secret-string "YOURCLIENTSECRET"
const googlePlayClientSecretArn = ""

const app = new cdk.App();
var identityComponentStack = new CustomIdentityComponentStack(app, 'CustomIdentityComponentStack', {
    env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: process.env.CDK_DEFAULT_REGION},
    appleIdAppId: appleIdAppId,
    steamAppId: steamAppId,
    steamWebApiKeySecretArn: steamWebApiKeySecretArn,
    googlePlayClientId: googlePlayClientId,
    googlePlayAppId: googlePlayAppid,
    googlePlayClientSecretArn: googlePlayClientSecretArn
  });

  // CDK-nag
  Aspects.of(app).add(new AwsSolutionsChecks());

  // Suppressions
  NagSuppressions.addStackSuppressions(identityComponentStack, [
    { id: 'AwsSolutions-APIG4', reason: 'The API has to be publicly accessible as it is built for user login and authentication for custom identities.' },
    { id: 'AwsSolutions-COG4', reason: 'The API cannot use Cognito User Pools as it is an API built for login and authentication for custom identities.' },
  ]);