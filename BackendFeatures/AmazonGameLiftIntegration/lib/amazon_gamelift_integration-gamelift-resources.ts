import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import * as gamelift from 'aws-cdk-lib/aws-gamelift';
import * as sns from 'aws-cdk-lib/aws-sns';
import { CfnFleet } from 'aws-cdk-lib/aws-appstream';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';

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

    // Define the GameLift Queue
    var queue = this.defineGameLiftQueue(fleet);

    // Import the SNS topic ARN from the backend stack
    const topicArn = cdk.Fn.importValue('AmazonGameLiftSampleSnsTopicArn');

    // Define the FlexMatch configuration and rule set
    const matchmakingConfiguration = this.defineFlexMatchConfiguration(queue, topicArn);

    // Define the CloudWatch dashboard
    this.defineCloudWatchDashboard(fleet, matchmakingConfiguration, locations, props.serverBinaryName);
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
      path: path.join(__dirname, '../LinuxServerBuild'),
    });
    gameliftBuildRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:GetObjectVersion'],
        resources: [asset.bucket.bucketArn+'/'+asset.s3ObjectKey],
      }));
    const now = new Date();
    const gameliftBuild = new gamelift.CfnBuild(this, "Build", {
          name: 'AmazonGameLiftSampleServer'+ now.toUTCString(),
          operatingSystem: 'AMAZON_LINUX_2023',
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
            maxSize: 4,
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
      applyCapacity: "ON_CREATE_AND_UPDATE", // By default the location scaling limits are not applied on create, but we want to enable that
      // Add a target-based scaling policy and target 30% available game sessions
      scalingPolicies: [{
          policyType: 'TargetBased',
          name: "ScalingPolicy",
          metricName: "PercentAvailableGameSessions",
          targetConfiguration: {
            targetValue: 30
          } 
        }],
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

    // Define a CloudFormation output value for the fleet ARN
    new cdk.CfnOutput(this, 'SampleGameLiftFleetID', {
      value: gameLiftFleet.ref,
      exportName: 'SampleGameLiftFleetID',
    });

    return gameLiftFleet;
  }

  defineGameLiftQueue(fleet: gamelift.CfnFleet) {
  
    // The GameLift Fleet Queue
    const cfnGameSessionQueue = new gamelift.CfnGameSessionQueue(this, 'Sample GameLift Queue', {
      name: 'SampleGameLiftQueue',
      
      // Set the multi-region fleet as the destination
      destinations: [{
        destinationArn: 'arn:aws:gamelift:'+this.region+':'+this.account+':fleet/'+fleet.ref,
      }],
      // Try to find a < 100ms latency location for the first 5 seconds. FlexMatch should already target this too
      playerLatencyPolicies: [{
        maximumIndividualPlayerLatencyMilliseconds: 100,
        policyDurationSeconds: 5,
        },{
          maximumIndividualPlayerLatencyMilliseconds: 20000
        }
      ],
      timeoutInSeconds: 20 // Timeout after 20 seconds of searching for a session
    });

    return cfnGameSessionQueue;
  }

  defineFlexMatchConfiguration(queue: gamelift.CfnGameSessionQueue, topicArn: string) {

    // Define FlexMatch rule set that matches based on similar skill level and optimizes latency
    const cfnMatchmakingRuleSet = new gamelift.CfnMatchmakingRuleSet(this, 'MyCfnMatchmakingRuleSet', {
      name: 'SampleRuleSet',
      ruleSetBody: `{
        "name": "simplerule",
        "ruleLanguageVersion": "1.0",
        "playerAttributes": [{
            "name": "skill",
            "type": "number",
            "default": 10
        }],
        "teams": [{
            "name": "oneteam",
            "maxPlayers": 5,
            "minPlayers": 1
        }], \
        "rules": [{
            "name": "FairSkill",
            "description": "The average skill of players is within 10 points from the average skill of all players in the match",
            "type": "distance",
            // get skill value for each player
            "measurements": [ "teams[oneteam].players.attributes[skill]" ],
            // get skill values for all players and average to produce an overall average
            "referenceValue": "avg(teams[oneteam].players.attributes[skill])",
            "maxDistance": 10
        },{
          "name": "FastConnection",
          "description": "Prefer matches with fast player connections first",
          "type": "latency",
          "maxLatency": 80
        }],
        "expansions": [{
              "target": "rules[FastConnection].maxLatency",
              "steps": [{
                  "waitTimeSeconds": 5,
                  "value": 1000
            }]
        }]
      }`
    });

    // Define FlexMatch configuration
    const cfnMatchmakingConfiguration = new gamelift.CfnMatchmakingConfiguration(this, 'Sample FlexMatch Configuration', {
      name: 'SampleFlexMatchConfiguration',
      description: 'Sample FlexMatch Configuration',
      gameSessionQueueArns: [queue.attrArn],
      requestTimeoutSeconds: 20,
      acceptanceRequired: false,
      ruleSetName: 'SampleRuleSet',
      backfillMode: 'AUTOMATIC', // We use automatic backfill to allow starting with one player and fill the sessions up once to max players
      notificationTarget: topicArn // Receive FlexMatch events to this topic to inform players
    });
    cfnMatchmakingConfiguration.node.addDependency(queue);

    return cfnMatchmakingConfiguration;
    
  }

  // Define the CloudWatch Dashboard for GameLift
  defineCloudWatchDashboard(fleet: cdk.aws_gamelift.CfnFleet, matchmakingConfiguration: gamelift.CfnMatchmakingConfiguration, locations: string[], serverBinaryName : string){

    // Define a CloudWatch Dashboard
    const dashboard = new cloudwatch.Dashboard(this, 'AmazonGameLiftGameServerMetricsGlobal', {
      dashboardName: 'AmazonGameLiftGameServerMetricsGlobal',
    });

    const firstRowWidgets = [];

    // Widget for current matchmaking tickest (max 5 minutes)
    const matchmakingTicketWidget = new cloudwatch.SingleValueWidget({
      title: 'Current Matchmaking Tickets',
      metrics: [new cloudwatch.Metric({
        namespace: 'AWS/GameLift',
        metricName: 'CurrentTickets',
        dimensionsMap: {
          ConfigurationName: matchmakingConfiguration.name
        },
        statistic: 'Maximum',
        period: cdk.Duration.minutes(5)
      })],
      width: 5
    });

    firstRowWidgets.push(matchmakingTicketWidget);

    // Create the PercentageAvailableGameSessions SingleValueWidget for all locations in location list
    for (const location of locations) {
      firstRowWidgets.push(new cloudwatch.SingleValueWidget({
        title: 'Percentage Available Game Sessions ('+location+')',
        metrics: [new cloudwatch.Metric({
          namespace: 'AWS/GameLift',
          metricName: 'PercentAvailableGameSessions',
          dimensionsMap: {
            FleetId: fleet.ref,
            Location: location
          },
          statistic: 'Average',
          period: cdk.Duration.minutes(5)
        })],
        width: 5
      }));
    }

    // Add the first row widgets to the dashboard
    dashboard.addWidgets(...firstRowWidgets);

    const avgCpuUsageWidgets = [];

    // GraphWidgets for CPU usage
    for (const location of locations) {
      avgCpuUsageWidgets.push(new cloudwatch.GraphWidget({
        title: 'Average CPU Usage ('+location+')',
        left: [
          new cloudwatch.MathExpression({
            expression: "AVG(SEARCH('{CWAgent,host,pattern,process_name} process_name=\""+serverBinaryName+"\" MetricName=\"procstat_cpu_usage\"', 'Average', 300))",
            period: cdk.Duration.minutes(5),
            searchRegion: location,
            label: 'Average CPU Usage ('+location+')'
          })
        ],
        width: 7
      }));
    }

    // Add the second row widgets to the dashboard
    dashboard.addWidgets(...avgCpuUsageWidgets);

    const cpuUsageWidgets = [];

    // GraphWidgets for CPU usage
    for (const location of locations) {
      cpuUsageWidgets.push(new cloudwatch.GraphWidget({
        title: 'CPU Usage per session ('+location+')',
        left: [
          new cloudwatch.MathExpression({
            expression: "SEARCH('{CWAgent,host,pattern,process_name} process_name=\""+serverBinaryName+"\" MetricName=\"procstat_cpu_usage\"', 'Average', 300)",
            period: cdk.Duration.minutes(5),
            searchRegion: location,
            label: 'CPU Usage ('+location+')'
          })
        ],
        width: 7
      }));
    }

    // Add the third row widgets to the dashboard
    dashboard.addWidgets(...cpuUsageWidgets);

    const avgMemUsageWidgets = [];
    // GraphWidget for Memory usage
    for (const location of locations) {
      avgMemUsageWidgets.push(new cloudwatch.GraphWidget({
        title: 'Average Memory Usage ('+location+')',
        left: [
          new cloudwatch.MathExpression({
            expression: "AVG(SEARCH('{CWAgent,host,pattern,process_name} process_name=\""+serverBinaryName+"\" MetricName=\"procstat_memory_rss\"', 'Average', 300))",
            period: cdk.Duration.minutes(5),
            searchRegion: location,
            label: 'Average Memory Usage ('+location+')'
          })
        ],
        width: 7
      }));
    }
    // Add the fourth row widgets to the dashboard
    dashboard.addWidgets(...avgMemUsageWidgets);

    const memUsageWidgets = [];
    // GraphWidget for Memory usage
    for (const location of locations) {
      memUsageWidgets.push(new cloudwatch.GraphWidget({
        title: 'Memory Usage per session ('+location+')',
        left: [
          new cloudwatch.MathExpression({
            expression: "SEARCH('{CWAgent,host,pattern,process_name} process_name=\""+serverBinaryName+"\" MetricName=\"procstat_memory_rss\"', 'Average', 300)",
            period: cdk.Duration.minutes(5),
            searchRegion: location,
            label: 'Memory Usage ('+location+')'
          })
        ],
        width: 7
      }));
    }
    // Add the fifth row widgets to the dashboard
    dashboard.addWidgets(...memUsageWidgets);


  }
}
