# AWS Game Backend Framework Custom Identity Component

- [AWS Game Backend Framework Custom Identity Component](#aws-game-backend-framework-custom-identity-component)
  * [Deploy the Custom Identity Component](#deploy-the-custom-identity-component)
  * [Issuer details](#issuer-details)
  * [API Reference](#api-reference)
    + [GET /login-as-guest](#get-login-as-guest)
    + [GET /refresh-access-token](#get-refresh-access-token)
    + [GET /login-with-steam](#get-login-with-steam)
    + [GET /login-with-apple-id](#get-login-with-apple-id)
    + [GET /login-with-google-play](#get-login-with-google-play)
    + [GET /login-with-facebook](#get-login-with-facebook)

The custom identity component implements is a serverless solution that manages a JSON Web Key Set (JWKS) with rotation and publicly available configuration and public keys. It also supports integration with Steam, Sign in with Apple, Google Play, and Facebook, and can be extended with custom code to more providers such as console platforms.

The solution also allows guest login, and supports linking new identity providers to existing identities, for example upgrading from a guest identity to an authenticated identity with Steam, Apple or Google Play, or using Facebook identities as a link between platforms.

## Deploy the Custom Identity Component

The first thing you need to do is deploy the custom identity component, that will create, rotate and manage Json Web Key Set (JWKS) keys, and provide publicly accessible endpoints to login as guest user, link existing users to new identity providers, and directly login with an identity provider ID such as Steam or Apple ID.

To deploy the custom identity component you'll need the following tools:
* [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) installed and [credentials configured](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
* [Node.js](https://nodejs.org/en/download) installed (required for AWS Cloud Development Kit)
* [AWS Cloud Development Kit (CDK) v2](https://docs.aws.amazon.com/cdk/v2/guide/cli.html) installed. AWS CDK is used to deploy the identity component resources
* [Docker engine](https://docs.docker.com/engine/install/) installed (and started **prior** to opening terminal). The deployment uses Docker for packaging Python backend functions

Optionally, you can add integrations to identity providers by modifying `CustomIdentityComponent/bin/custom_identity_component.ts` and setting a non empty value for the platform specific app ID configuration. Currently supported ones include:
* __Steam__
  * Set `const steamAppId` to your Steam App identifier such as "1234567".
  * Set `const steamWebApiKeySecretArn` to the Arn of a Secrets Manager secret containing your Steam Web Api Key (see Steam docs for details). You can create a secret with the AWS CLI: `aws secretsmanager create-secret --name MySteamWebApiKey --description "Steam Web Api Key" --secret-string "YOURAPIKEYHERE"`
* __Sign in with Apple__
  * Set `const appleIdAppId` to your Apple App Identifier such as "com.mycompany.myapp".
* __Google Play__
  * Set `const googlePlayAppid` to your Google Play app identifier such as "1234567890".
  * Set `const googlePlayClientId` to your Google Play Web application client ID such as "1234567890-xyz123.apps.googleusercontent.com".
  * Set `const googlePlayClientSecretArn` to the Arn of a Secrets Manager secret containing your Client Secret for the Web application client (see [Google Play developer docs](https://developers.google.com/games/services/console/enabling) for details). You can create a secret with the AWS CLI: `aws secretsmanager create-secret --name MyGooglePlayClientSecret --description "Google Play client secret" --secret-string "YOURCLIENTSECRET"`
* __Facebook__
  * Set `const facebookAppId` to the App ID of your Facebook application in developer.facebook.com. You can find this under "Basic Settings" for the app.

When you set a non empty value for one of these App ID:s, the CDK stack will automatically deploy required endpoints and resources for that platform.

To deploy the identity component, run the following commands (Note: on **Windows** make sure to run in Powershell as **Administrator**):
1. `cd CustomIdentityComponent`
2. `npm install` to install CDK app dependencies
3. `cdk bootstrap` to bootstrap your account for CDK, see [Bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html) for more information
4. `cdk synth` to synthesize the CDK app and validate your configuration works
5. `cdk deploy` to deploy the CDK app to your account
6. run the following AWS CLI commands to invoke the GenerateKeys function to generate the first set of public and private keys (rotation will run this weekly). NOTE: You should get a 200 OK if this is successful. Make sure to set a region to the calls if you're not using the default CLI region):
  ```
  fn=$(aws cloudformation describe-stacks --stack-name CustomIdentityComponentStack --query 'Stacks[0].Outputs[?OutputKey==`GenerateKeysFunctionName`].OutputValue' --output text)
  aws lambda invoke --function-name $fn response.json
  ```

After this you should see a CloudFormation stack installed in your AWS account, with an API Gateway REST API for login functionalities, and a Amazon CloudFront endpoint backed with AWS S3 for the public encryption key and authentication configuration. There's also a main UserTable in Amazon DynamoDB to store user info, and identity provide specific tables created for linking the accounts.

## Issuer details

By default, the keys (JWKS) is rotated every 7 days, with both the most recent and the previous key available in the public endpoint for validating JWT:s. 

When logging in as a **Guest** user, the scope of the JWT is *"guest"*. When logging in (or linking) any of the **identity providers**, the scope of the JWT is *"authenticated"*. For both cases, the JWT*audience is *"gamebackend"*. The sample integrations use this information to make sure access is only allowed with the right audience and scopes.

The client will also receive a refresh token with the scope *"refresh"* and audience *"refresh"*. This should be only used with the refresh-access-token API. By default the refresh token is valid for 7 days, and the access token for 15 minutes. The SDK:s provided for Unity and Unreal will automatically refresh the access token, but you are responsible for making sure you don't use a single refresh token beyond the amount if time it is valid.

The issuer is available through an Amazon CloudFront endpoint, and will include a */.well-known/jwks.json* file as well as an */.well-known/openid-configuration* that are stored in Amazon S3. Your backend systems should use these to get the public keys for validating JWT:s. The sample backend components include sample implementation for this, and for example API Gateway HTTP API:s natively support this issuer endpoint for validating JWT:s.

**AWS Web Application Firewall protection**

The API is protected by the default managed rule set provided by AWS for blocking common attacks. In addition, a rate limit rule is applied to allow a maximum of 1000 requests per 5 minutes from a single IP (3.33 transactions per second).

**Logs and Distributed Tracing**

The identity component leverages **Powertools for AWS Lambda (Python)** to generate log output to Amazon CloudWatch Logs. In addition, the tools are used to push tracing information to **AWS X-Ray**. You can find both the logs and the tracing map and individual trace information the **AWS CloudWatch** console. 

## API Reference

The API integrations are built into the SDK:s provided for both Unity and Unreal. For other engines, you can easily integrate by calling the API endpoint with appropriate parameters. The identity component doesn't expect authorization in the header, as it is itself generating the authorization tokens for other backend API:s to consume. It does require valid login information in the form of a guest_secret for guest users, or appropriate authentication tokens when integrating with game platforms.

### GET /login-as-guest

`GET /login-as-guest`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `user_id`   |  No       | When logging in with existing user, the user_id field must be set. When creating a new guest user, leave this empty |
> | `guest_secret`   |  No  | When logging in with existing user, the guest_secret field must be set. When creating a new guest user, leave this empoty   |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'guest_secret': guest_secret,'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `400`         |  `Error: No guest_secret in query string`                            |
> | `401`         | Multiple errors: could not create a validate user                                                               |

### GET /refresh-access-token

`GET /refresh-access-token`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `refresh_token`   |  Yes       | Requires a non-expired refresh_token for refreshing the access token for this user |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `401`         | Multiple errors: couldn't validate token, token missing                                   |


### GET /login-with-steam

`GET /login-with-steam`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `steam_auth_token`   |  Yes       | When logging in with Steam, you always need to provide a valid Steam authentication token |
> | `link_to_existing_user`   |  No  | Set this to `Yes` for linking the Steam identity to existing user. Requires also the `auth_token` field to be set.  |
> | `auth_token`   |  No  | Provide an existing auth_token for a logged in user when linking Steam identity to existing user. Requires also the `link_to_existing_user` to be set.  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'steam_id': steam_id,'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `401`         | Multiple errors: could not create a validate user                |         

### GET /login-with-apple-id

`GET /login-with-apple-id`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `apple_auth_token`   |  Yes       | When logging in with Sign in with Apple, you always need to provide a valid Apple ID authentication token |
> | `link_to_existing_user`   |  No  | Set this to `Yes` for linking the Apple identity to existing user. Requires also the `auth_token` field to be set.  |
> | `auth_token`   |  No  | Provide an existing auth_token for a logged in user when linking Apple identity to existing user. Requires also the `link_to_existing_user` to be set.  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'apple_id': apple_id,'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `401`         | Multiple errors: could not create a validate user                |   

### GET /login-with-google-play

`GET /login-with-google-play`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `google_play_auth_token`   |  Yes       | When logging in with Google Play, you always need to provide a valid Google Play authentication token |
> | `link_to_existing_user`   |  No  | Set this to `Yes` for linking the Google Play identity to existing user. Requires also the `auth_token` field to be set.  |
> | `auth_token`   |  No  | Provide an existing auth_token for a logged in user when linking Google Play identity to existing user. Requires also the `link_to_existing_user` to be set.  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'google_play_id': google_play_id,'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `401`         | Multiple errors: could not create a validate user                |   

### GET /login-with-facebook

`GET /login-with-facebook`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `facebook_access_token`   |  Yes       | When logging in with Facebook, you always need to provide a valid Facebook Access token |
> | `facebook_user_id`   |  Yes       | When logging in with Facebook, you always need to provide a valid Facebook User ID |
> | `link_to_existing_user`   |  No  | Set this to `Yes` for linking the Facebook identity to existing user. Requires also the `auth_token` field to be set.  |
> | `auth_token`   |  No  | Provide an existing auth_token for a logged in user when linking Facebook identity to existing user. Requires also the `link_to_existing_user` to be set.  |

**Responses**

> | http code     | response                                                            |
> |---------------|---------------------------------------------------------------------|
> | `200`         | `{'facebook_id': facebook_id,'user_id': user_id,'auth_token': auth_token,'refresh_token': refresh_token, 'auth_token_expires_in' :auth_token_expires_in,'refresh_token_expires_in' : refresh_token_expires_in}`                                |
> | `401`         | Multiple errors: could not create a validate user                |  


