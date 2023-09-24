// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { Stack, StackProps, CfnOutput, Duration } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as secretsmanager from  'aws-cdk-lib/aws-secretsmanager';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from "aws-cdk-lib/aws-events-targets";
import { MethodLoggingLevel } from 'aws-cdk-lib/aws-apigateway';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';

export interface CustomIdentityComponentStackProps extends StackProps {
  // If defined, login-with-apple endpoint will be created
  // This should be com.xx.xx.xx
  appleIdAppId: string;
  // This should be the numeric steamId as string
  steamAppId: string;
  // This is the arn of the Secrets Manager secret in the same region containing the Steam Web Api key
  steamWebApiKeySecretArn: string;
  // This should be the Google play client such as xyz.apps.googleusercontent.com
  googlePlayClientId: string;
  // This should be the Google play App Id (the numeric part of the client ID)
  googlePlayAppId: string;
  // This is the arn of the Secrets Manager secret in the same region containing the Google Play Client secret
  googlePlayClientSecretArn: string;
  // This is the app ID of the facebook app
  facebookAppId: string;
}

export class CustomIdentityComponentStack extends Stack {
  constructor(scope: Construct, id: string, props: CustomIdentityComponentStackProps) {
    super(scope, id, props);

    // The shared policy for basic Lambda access needs for logging. This is similar to the managed Lambda Execution Policy
    const lambdaBasicPolicy = new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
      resources: ['*'],
    });

    // Bucket for logging S3 and CloudFront access
    var loggingBucket = new s3.Bucket(this, 'IdentityComponentLoggingBucket', {
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      serverAccessLogsPrefix: 'logging-bucket-access-logs',
    });

    // Creates an S3 bucket for issuer data such as JWKS and a CloudFront distribution to access the data
    const issuer_bucket = new s3.Bucket(this, 'issuerdatabucket', {
      enforceSSL: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      serverAccessLogsPrefix: 'issuer-access-logs',
      serverAccessLogsBucket: loggingBucket,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // Define a cache policy to cache 0 minutes (new keys need to be accessible immediately)
    const myCachePolicy = new cloudfront.CachePolicy(this, 'IssuerCachePolicy', {
      cachePolicyName: 'IssuerCachePolicy',
      comment: 'A default policy',
      defaultTtl: Duration.minutes(0), // Zero TTL, we'll always go to the source to immediately get new keys
      minTtl: Duration.minutes(0),
      maxTtl: Duration.minutes(0)
    });

    // Define a CloudFront distribution for the issuer data
    const distribution = new cloudfront.Distribution(this, 'IssuerEndpoint', {
      defaultBehavior: { origin: new origins.S3Origin(issuer_bucket), cachePolicy: myCachePolicy},
      enableLogging: true,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      logBucket: loggingBucket
    });
    NagSuppressions.addResourceSuppressions(distribution, [
      { id: 'AwsSolutions-CFR4', reason: 'Not possible to enforce TLS versions for S3 origin' }
    ], true);

    // Issuer endpoint used by customer backend components for validation JWT:s
    new CfnOutput(this, 'IssuerEndpointUrl', { value: "https://"+distribution.domainName });

    // Define a secrets manager secret with name jwk_private_key
    const secret = new secretsmanager.Secret(this, 'JWKPrivateKeySecret');
    NagSuppressions.addResourceSuppressions(secret, [
      { id: 'AwsSolutions-SMG4', reason: 'Automatic rotation not configured because it is handled by a scheduled Lambda.' }
    ], true);

    // Lambda function for generating JWK keys, scheduled to run on a weekly interval by default
    const generate_keys_function_role = new iam.Role(this, 'GenerateKeysFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    generate_keys_function_role.addToPolicy(lambdaBasicPolicy);
    const generate_keys_function = new lambda.Function(this, 'GenerateKeys', {
      role: generate_keys_function_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'generate_keys.lambda_handler',
      timeout: Duration.seconds(300),
      memorySize: 2048,
      environment: {
        "ISSUER_BUCKET": issuer_bucket.bucketName,
        "ISSUER_ENDPOINT": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": secret.secretName,
      }
    });
    issuer_bucket.grantReadWrite(generate_keys_function);
    secret.grantWrite(generate_keys_function);
    NagSuppressions.addResourceSuppressions(generate_keys_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    new CfnOutput(this, 'GenerateKeysFunctionName', { value: generate_keys_function.functionName });

    // Schedule the generate_keys_function to run every seven days for key rotation
    const eventRule = new events.Rule(this, 'scheduleRule', {
      schedule: events.Schedule.rate(Duration.days(7))
    });
    eventRule.addTarget(new targets.LambdaFunction(generate_keys_function))

    // Define a DynamoDB table for user data with partition key user_id
    const user_table = new dynamodb.Table(this, 'UserTable', {
      partitionKey: {
        name: 'UserId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true
    });

    // Define a Web Application Firewall with the standard AWS provided rule set
    const cfnWebACLManaged = new wafv2.CfnWebACL(this,'CustomIdentityWebACL',{
            defaultAction: {
              allow: {}
            },
            scope: 'REGIONAL',
            visibilityConfig: {
              cloudWatchMetricsEnabled: true,
              metricName:'MetricForWebACLCDK',
              sampledRequestsEnabled: true,
            },
            name:'CustomIdentityWebACL',
            rules: [{
              name: 'ManagedWafRules',
              priority: 0,
              statement: {
                managedRuleGroupStatement: {
                  name:'AWSManagedRulesCommonRuleSet', // The standard rule set provided by AWS: https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-baseline.html
                  vendorName:'AWS'
                }
              },
              visibilityConfig: {
                cloudWatchMetricsEnabled: true,
                metricName:'MetricForWebACLCDK-ManagedRules',
                sampledRequestsEnabled: true,
              },
              overrideAction: {
                none: {}
              },
            }]
    });

    const cfnWebACLRateLimit = new wafv2.CfnWebACL(this,'CustomIdentityWebACLRateLimit',{
      defaultAction: {
        allow: {}
      },
      scope: 'REGIONAL',
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName:'MetricForWebACLCDKRateLimit',
        sampledRequestsEnabled: true,
      },
      name:'CustomIdentityWebACLRateLimit',
      rules: [
      // Add rate limiting rule to allow 3.33 TPS from a single IP (1000 per 5 minutes)
      {
        name: 'RateLimitingRule',
        priority: 1,
        action: {
          block: {}
        },
        statement: {
          rateBasedStatement: {
            limit: 1000,
            aggregateKeyType: 'IP'
          }
        },
        visibilityConfig: {
          cloudWatchMetricsEnabled: true,
          metricName:'MetricForWebACLCDK-RateLimiting',
          sampledRequestsEnabled: true,
        }
      }]
    });

    // Define an API Gateway for the authentication component public endpoint
    const logGroup = new logs.LogGroup(this, "CustomIdentityAPiAccessLogs");
    const api_gateway = new apigw.RestApi(this, 'ApiGateway', {
      restApiName: 'CustomIdentityComponentApi',
      description: 'Custom Identity Component API',
      deployOptions: {
        accessLogDestination: new apigw.LogGroupLogDestination(logGroup),
        accessLogFormat: apigw.AccessLogFormat.clf(),
        loggingLevel : MethodLoggingLevel.ERROR,
        tracingEnabled: true,
        stageName: 'prod',
      }
    });
    // cdk-nag suppression for the API Gateway default logs access
    NagSuppressions.addResourceSuppressions(
      api_gateway,[{
          id: 'AwsSolutions-IAM4',
          reason: "We are using the default CW Logs access of API Gateway",
        },],true);

    // Attach the Web Application Firewall with the standard AWS provided rule set
    new wafv2.CfnWebACLAssociation(this,'ApiGatewayWebACLAssociation',{
      resourceArn: api_gateway.deploymentStage.stageArn,
      webAclArn:cfnWebACLManaged.attrArn,
    });
    // Attach the WAF with the rate limit rules
    new wafv2.CfnWebACLAssociation(this,'ApiGatewayWebACLAssociationRateLimit',{
      resourceArn: api_gateway.deploymentStage.stageArn,
      webAclArn:cfnWebACLRateLimit.attrArn,
    });

    // Request validator for the API
    const requestValidator = new apigw.RequestValidator(this, 'CustomIdentityApiRequestValidator', {
      restApi: api_gateway,
      requestValidatorName: 'CustomIdentityApiRequestValidator',
      validateRequestBody: false,
      validateRequestParameters: true
    });

    // Lambda function for guest login
    const login_as_guest_function_role = new iam.Role(this, 'LoginAsGuestFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    login_as_guest_function_role.addToPolicy(lambdaBasicPolicy);
    const login_as_guest_function = new lambda.Function(this, 'LoginAsGuest', {
      role: login_as_guest_function_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'login_as_guest.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 2048,
      environment: {
        "ISSUER_URL": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": secret.secretName,
        "USER_TABLE": user_table.tableName
      }
    });
    secret.grantRead(login_as_guest_function);
    user_table.grantReadWriteData(login_as_guest_function);

    NagSuppressions.addResourceSuppressions(login_as_guest_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
    api_gateway.root.addResource('login-as-guest').addMethod('GET', new apigw.LambdaIntegration(login_as_guest_function),{
      requestParameters: {
        'method.request.querystring.user_id': false,
        'method.request.querystring.guest_secret': false,
      },
      requestValidator: requestValidator
    });

    NagSuppressions.addResourceSuppressions(api_gateway, [
      { id: 'AwsSolutions-APIG2', reason: 'This error is incorrectly reported as all requests are validated.' }
    ], true);

    // Lambda function for refreshing token
    const refresh_access_token_function_role = new iam.Role(this, 'RefreshAccessTokenFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    refresh_access_token_function_role.addToPolicy(lambdaBasicPolicy);
    const refresh_access_token_function = new lambda.Function(this, 'RefreshAccessToken', {
      role: refresh_access_token_function_role,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'refresh_access_token.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 2048,
      environment: {
        "ISSUER_URL": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": secret.secretName,
        "USER_TABLE": user_table.tableName
      }
    });
    secret.grantRead(refresh_access_token_function);
    user_table.grantReadWriteData(refresh_access_token_function);

    NagSuppressions.addResourceSuppressions(refresh_access_token_function_role, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);
    
    // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
    api_gateway.root.addResource('refresh-access-token').addMethod('GET', new apigw.LambdaIntegration(refresh_access_token_function),{
      requestParameters: {
        'method.request.querystring.refresh_token': true
      },
      requestValidator: requestValidator
    });

    // Login endpoint to CloudFormation Output
    new CfnOutput(this, 'LoginEndpoint', { value: api_gateway.url });

    // If Apple ID App ID Defined, add a DynamoDB table and Lambda function for Apple login
    if(props.appleIdAppId != "") {
        this.setupAppleIdLogin(props.appleIdAppId, secret, user_table, distribution, api_gateway, lambdaBasicPolicy, requestValidator);
    }

    // If Steam App ID defined, add a DynamoDB table and Lambda function for Steam login
    if(props.steamAppId != "") {
        this.setupSteamLogin(props.steamAppId, props.steamWebApiKeySecretArn, secret, user_table, distribution, api_gateway, lambdaBasicPolicy, requestValidator);
    }

    // If Google Play App ID defined, add a DynamoDB table and Lambda function for Google Play login
    if(props.googlePlayClientId != "") {
        this.setupGooglePlayLogin(props.googlePlayClientId, props.googlePlayAppId, props.googlePlayClientSecretArn, secret, user_table, distribution, api_gateway, lambdaBasicPolicy, requestValidator);
    }

    // If Facebook App ID defined, add a DynamoDB table and Lambda function for Facebook login
    if(props.facebookAppId != "") {
        this.setupFacebookLogin(props.facebookAppId, secret, user_table, distribution, api_gateway, lambdaBasicPolicy, requestValidator);
    }
  }

  ///// *** IDENTITY PROVIDER SPECIFIC RESOURECE **** //////

  // Sets up Lambda endpoint and DynamoDB table for Apple ID Login
  setupAppleIdLogin(appId : string, secret: secretsmanager.Secret, user_table: dynamodb.Table, distribution: cloudfront.Distribution, api_gateway: apigw.RestApi, lambdaBasicPolicy: iam.PolicyStatement, requestValidator: apigw.RequestValidator) {
    
      // Define a DynamoDB table for AppleIdUsers
      const appleIdUserTable = new dynamodb.Table(this, 'AppleIdUserTable', {
        partitionKey: {
          name: 'AppleId',
          type: dynamodb.AttributeType.STRING
        },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        pointInTimeRecovery: true
      });
 
      // Lambda function for Apple Id login
      const loginWithAppleIdFunctionRole = new iam.Role(this, 'LoginWithAppleIdFunctionRole', {
        assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      });
      loginWithAppleIdFunctionRole.addToPolicy(lambdaBasicPolicy);
      const loginWithAppleIdFunction = new lambda.Function(this, 'LoginWithAppleId', {
        role: loginWithAppleIdFunctionRole,
        code: lambda.Code.fromAsset("lambda", {
          bundling: {
            image: lambda.Runtime.PYTHON_3_11.bundlingImage,
            command: [
              'bash', '-c',
              'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
            ],
        },}),
        runtime: lambda.Runtime.PYTHON_3_11,
        handler: 'login_with_apple_id.lambda_handler',
        timeout: Duration.seconds(15),
        tracing: lambda.Tracing.ACTIVE,
        memorySize: 2048,
        environment: {
          "ISSUER_URL": "https://"+distribution.domainName,
          "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
          "SECRET_KEY_ID": secret.secretName,
          "USER_TABLE": user_table.tableName,
          "APPLE_APP_ID": appId,
          "APPLE_ID_USER_TABLE": appleIdUserTable.tableName
        }
      });
      secret.grantRead(loginWithAppleIdFunction);
      user_table.grantReadWriteData(loginWithAppleIdFunction);
      appleIdUserTable.grantReadWriteData(loginWithAppleIdFunction);

      NagSuppressions.addResourceSuppressions(loginWithAppleIdFunctionRole, [
        { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
      ], true);

      // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
      api_gateway.root.addResource('login-with-apple-id').addMethod('GET', new apigw.LambdaIntegration(loginWithAppleIdFunction),{
        requestParameters: {
          'method.request.querystring.apple_auth_token': true,
          'method.request.querystring.auth_token': false,
          'method.request.querystring.link_to_existing_user': false
        },
        requestValidator: requestValidator
      });
  }

  // Sets up Lambda endpoint and DynamoDB table for Steam ID Login
  setupSteamLogin(appId: string, steamWebApiKeySecretArn: string, privateKeySecret: secretsmanager.Secret, user_table: dynamodb.Table, distribution: cloudfront.Distribution, api_gateway: apigw.RestApi, lambdaBasicPolicy: iam.PolicyStatement, requestValidator: apigw.RequestValidator) {
  
    // Define a DynamoDB table for Steam Users
    const steamIdUserTable = new dynamodb.Table(this, 'SteamUserTable', {
      partitionKey: {
        name: 'SteamId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true
    });

    // Lambda function for Steam Id login
    const loginWithSteamIdFunctionRole = new iam.Role(this, 'LoginWithSteamIdFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    loginWithSteamIdFunctionRole.addToPolicy(lambdaBasicPolicy);
    const loginWithSteamIdFunction = new lambda.Function(this, 'LoginWithSteam', {
      role: loginWithSteamIdFunctionRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'login_with_steam.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 2048,
      environment: {
        "ISSUER_URL": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": privateKeySecret.secretName,
        "USER_TABLE": user_table.tableName,
        "STEAM_APP_ID": appId,
        "STEAM_WEB_API_KEY_SECRET_ARN": steamWebApiKeySecretArn,
        "STEAM_USER_TABLE": steamIdUserTable.tableName
      }
    });
    // Grant access to required resources
    privateKeySecret.grantRead(loginWithSteamIdFunction);
    user_table.grantReadWriteData(loginWithSteamIdFunction);
    steamIdUserTable.grantReadWriteData(loginWithSteamIdFunction);
    // Define IAM policy to access steamWebApiKey secret
    const policy = new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [steamWebApiKeySecretArn]
    });
    loginWithSteamIdFunction.addToRolePolicy(policy);

    NagSuppressions.addResourceSuppressions(loginWithSteamIdFunctionRole, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
    api_gateway.root.addResource('login-with-steam').addMethod('GET', new apigw.LambdaIntegration(loginWithSteamIdFunction),{
      requestParameters: {
        'method.request.querystring.steam_auth_token': true,
        'method.request.querystring.auth_token': false,
        'method.request.querystring.link_to_existing_user': false
      },
      requestValidator: requestValidator
    });
  }

  // Sets up Lambda endpoint and DynamoDB table for Google Play Login
  setupGooglePlayLogin(googlePlayClientId: string, googlePlayAppId: string, googlePlayClientSecretArn: string,
                        privateKeySecret: secretsmanager.Secret, user_table: dynamodb.Table,
                        distribution: cloudfront.Distribution, api_gateway: apigw.RestApi, lambdaBasicPolicy: iam.PolicyStatement, requestValidator: apigw.RequestValidator) {

    // Define a DynamoDB table for Google Play
    const googlePlayUserTable = new dynamodb.Table(this, 'GooglePlayUserTable', {
      partitionKey: {
        name: 'GooglePlayId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true
    });

    // Lambda function for Google Play login
    const loginWithGooglePlayFunctionRole = new iam.Role(this, 'LoginWithGooglePlayFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    loginWithGooglePlayFunctionRole.addToPolicy(lambdaBasicPolicy);
    const loginWithGooglePlayFunction = new lambda.Function(this, 'LoginWithGooglePlay', {
      role:  loginWithGooglePlayFunctionRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'login_with_google_play.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 2048,
      environment: {
        "ISSUER_URL": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": privateKeySecret.secretName,
        "USER_TABLE": user_table.tableName,
        "GOOGLE_PLAY_CLIENT_ID": googlePlayClientId,
        "GOOGLE_PLAY_APP_ID": googlePlayAppId,
        "GOOGLE_PLAY_CLIENT_SECRET_ARN": googlePlayClientSecretArn,
        "GOOGLE_PLAY_USER_TABLE": googlePlayUserTable.tableName
      }
    });
    // Grant access to required resources
    privateKeySecret.grantRead(loginWithGooglePlayFunction);
    user_table.grantReadWriteData(loginWithGooglePlayFunction);
    googlePlayUserTable.grantReadWriteData(loginWithGooglePlayFunction);
    // Define IAM policy to access steamWebApiKey secret
    const policy = new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [googlePlayClientSecretArn]
    });
    loginWithGooglePlayFunction.addToRolePolicy(policy);

    NagSuppressions.addResourceSuppressions(loginWithGooglePlayFunctionRole, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
    api_gateway.root.addResource('login-with-google-play').addMethod('GET', new apigw.LambdaIntegration(loginWithGooglePlayFunction),{
      requestParameters: {
        'method.request.querystring.google_play_auth_token': true,
        'method.request.querystring.auth_token': false,
        'method.request.querystring.link_to_existing_user': false
      },
      requestValidator: requestValidator
    });
  }

   // Sets up Lambda endpoint and DynamoDB table for Facebook Login
   setupFacebookLogin(appId : string, secret: secretsmanager.Secret, user_table: dynamodb.Table, distribution: cloudfront.Distribution, api_gateway: apigw.RestApi, lambdaBasicPolicy: iam.PolicyStatement, requestValidator: apigw.RequestValidator) {
    
    // Define a DynamoDB table for Facebook Users
    const facebookUserTable = new dynamodb.Table(this, 'FacebookUserTable', {
      partitionKey: {
        name: 'FacebookId',
        type: dynamodb.AttributeType.STRING
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true
    });

    // Lambda function for Facebook login
    const loginWithFacebookFunctionRole = new iam.Role(this, 'LoginWithFacebookFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
    });
    loginWithFacebookFunctionRole.addToPolicy(lambdaBasicPolicy);
    const loginWithFacebookFunction = new lambda.Function(this, 'LoginWithFacebook', {
      role: loginWithFacebookFunctionRole,
      code: lambda.Code.fromAsset("lambda", {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --platform manylinux2014_x86_64 --only-binary=:all: -r requirements.txt -t /asset-output && cp -ru . /asset-output'
          ],
      },}),
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'login_with_facebook.lambda_handler',
      timeout: Duration.seconds(15),
      tracing: lambda.Tracing.ACTIVE,
      memorySize: 2048,
      environment: {
        "ISSUER_URL": "https://"+distribution.domainName,
        "POWERTOOLS_SERVICE_NAME": "CustomIdentityComponentApi",
        "SECRET_KEY_ID": secret.secretName,
        "USER_TABLE": user_table.tableName,
        "FACEBOOK_APP_ID" : appId,
        "FACEBOOK_USER_TABLE": facebookUserTable.tableName
      }
    });
    secret.grantRead(loginWithFacebookFunction);
    user_table.grantReadWriteData(loginWithFacebookFunction);
    facebookUserTable.grantReadWriteData(loginWithFacebookFunction);

    NagSuppressions.addResourceSuppressions(loginWithFacebookFunctionRole, [
      { id: 'AwsSolutions-IAM5', reason: 'Using the standard Lambda execution role, all custom access resource restricted.' }
    ], true);

    // Map login_as_guest_function to the api_gateway GET requeste login_as_guest
    api_gateway.root.addResource('login-with-facebook').addMethod('GET', new apigw.LambdaIntegration(loginWithFacebookFunction),{
      requestParameters: {
        'method.request.querystring.facebook_access_token': true,
        'method.request.querystring.facebook_user_id': true,
        'method.request.querystring.auth_token': false,
        'method.request.querystring.link_to_existing_user': false
      },
      requestValidator: requestValidator
    });
}
};