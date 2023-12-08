import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import * as gamelift from 'aws-cdk-lib/aws-gamelift';
// import * as sqs from 'aws-cdk-lib/aws-sqs';

// Define custom stack properties
interface AmazonGameLiftIntegrationStackProps extends cdk.StackProps {
  serverBinaryName : string;
}

export class AmazonGameLiftIntegrationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AmazonGameLiftIntegrationStackProps) {
    super(scope, id, props);

    // Define the GameLift Fleet IAM role that's used for sending CloudWatch logs and metrics
    var fleetRole = this.defineFleetIAMRole();

    // Set up the game server build
    var gameServerBuild = this.defineGameServerBuild();

    // String list of location to deploy to
    var locations = ["us-east-1", "us-west-2", "eu-west-1"];

    // Define the GameLift fleet
    var fleet = this.defineGameLiftFleet(locations, gameServerBuild, fleetRole, props.serverBinaryName);
  }

  // Defines an IAM Role with access to CloudWatch Logs and metrics that can be assumed by a GameLift Fleet
  defineFleetIAMRole() {

    const fleetRole = new iam.Role(this, 'GameLiftFleetRole', {
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal("gamelift.amazonaws.com"),
        new iam.ServicePrincipal("ec2.amazonaws.com"))
    });

    // Set up the CloudWatch Agent policy (same as the managed policy CloudWatchAgentServerPolicy)
    fleetRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData','ec2:DescribeVolumes','ec2:DescribeTags','logs:PutLogEvents','logs:DescribeLogStreams','logs:DescribeLogGroups',
                  'logs:CreateLogStream','logs:CreateLogGroup'],
        resources: ['*'],
      }));
    fleetRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ['ssm:GetParameter'],
          resources: ['arn:aws:ssm:*:*:parameter/AmazonCloudWatch-'],
        }));
    // cdk-nag suppression for the standard CloudWatch Agent policy of the fleet role
    NagSuppressions.addResourceSuppressions(
      fleetRole,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: "We are using a policy similar to the standard managed Lambda policy to access logs",
        },
      ],
      true
    );

    return fleetRole;
  }

  // Defines the Linux server build for GameLift
  defineGameServerBuild() {

    const gameliftBuildRole = new iam.Role(this, 'GameLiftBuildRole', {
      assumedBy: new iam.ServicePrincipal('gamelift.amazonaws.com'),
    });
    const asset = new assets.Asset(this, 'BuildAsset', {
      path: path.join(__dirname, './LinuxServerBuild'),
    });
    gameliftBuildRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:GetObjectVersion'],
        resources: [asset.bucket.bucketArn+'/'+asset.s3ObjectKey],
      }));
    const now = new Date();
    const gameliftBuild = new gamelift.CfnBuild(this, "Build", {
          name: 'AmazonGameLiftSampleServer'+ now.toUTCString(),
          operatingSystem: 'AMAZON_LINUX_2',
          serverSdkVersion: '5.0.0',
          storageLocation: {
            bucket: asset.bucket.bucketName,
            key: asset.s3ObjectKey,
            roleArn: gameliftBuildRole.roleArn,
          },
          version: now.toUTCString()
      });
    
    gameliftBuild.node.addDependency(asset);
    gameliftBuild.node.addDependency(gameliftBuildRole);
    gameliftBuild.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN); //Retain old builds
    
    return gameliftBuild;
  }

  // Defines the multi-location GameLift fleet
  defineGameLiftFleet(locations: string[], gameServerBuild: gamelift.CfnBuild, instanceRole: iam.Role, serverBinaryName: string) {

    var buildId = gameServerBuild.ref;

    // Define a list of location definitions
    const locationDefinitions = [];
    for (const location of locations) {
      locationDefinitions.push({
          location: location,
          locationCapacity: {
            desiredEc2Instances: 1,
            maxSize: 1,
            minSize: 1
            }
      });
    } 

    // The multi-region fleet
    const gameLiftFleet = new gamelift.CfnFleet(this, 'Sample Multi-location GameLift fleet', {
      buildId: buildId,
      name: 'SampleAmazonGameLiftFleet',
      description: 'Sample Amazon GameLift Fleet',
      ec2InboundPermissions: [{
          fromPort: 1935,
          ipRange: '0.0.0.0/0',
          protocol: 'TCP',
          toPort: 1935,
        }, {
          fromPort: 7777,
          ipRange: '0.0.0.0/0',
          protocol: 'TCP',
          toPort: 7777,
      }],
      ec2InstanceType: 'c6i.large',
      fleetType: 'ON_DEMAND',
      instanceRoleArn: instanceRole.roleArn,
      instanceRoleCredentialsProvider: 'SHARED_CREDENTIAL_FILE', // We need the credentials file for the fleet role to use CloudWatch agent
      locations: locationDefinitions,
      // NOTE: Set this to FullProtection for production Fleets.
      // Once you do that, GameLift will NOT be able to scale down and terminate your previous Fleet when doing a redeployment with CDK
      // You can instead configure CDK to retain the old fleets with a Removal Policy set to RETAIN. And then terminate them more controlled when empty from players
      newGameSessionProtectionPolicy: 'NoProtection',
      // NOTE: We're defining two fixed game server processes which match with the logs defined for CloudWatch Agent.
      // When you configure a different set of processes (1-50), make sure to modify the CloudWatch Agent configuration found in LinuxServerBuild folder
      runtimeConfiguration: {
        serverProcesses: [{
          concurrentExecutions: 1,
          launchPath: '/local/game/'+serverBinaryName,
          // NOTE: Make sure to adjust these properties to your server configuration
          parameters: '-logFile /local/game/logs/myserver1935.log -port 1935'
        },{
          concurrentExecutions: 1,
          launchPath: '/local/game/'+serverBinaryName,
          // NOTE: Make sure to adjust these properties to your server configuration
          parameters: '-logFile /local/game/logs/myserver7777.log -port 7777'
        }]
      }
    });
    gameLiftFleet.node.addDependency(gameServerBuild);
  }

}
