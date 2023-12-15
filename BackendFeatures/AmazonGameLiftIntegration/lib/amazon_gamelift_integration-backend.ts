import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { NagSuppressions } from 'cdk-nag';
import * as assets from 'aws-cdk-lib/aws-s3-assets';
import * as path from 'path';
import * as gamelift from 'aws-cdk-lib/aws-gamelift';
import * as sns from 'aws-cdk-lib/aws-sns';

// Define custom stack properties
interface AmazonGameLiftIntegrationBackendProps extends cdk.StackProps {
  //serverBinaryName : string;
}

export class AmazonGameLiftIntegrationBackend extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AmazonGameLiftIntegrationBackendProps) {
    super(scope, id, props);

    // Define an SNS topic as the FlexMatch notification target
    const topic = new sns.Topic(this, 'FlexMatchEventsTopic');
    // Add a policy that allows gamelift.amazonaws.com to publish events
    topic.addToResourcePolicy(new iam.PolicyStatement({
      actions: ['sns:Publish'],
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('gamelift.amazonaws.com')],
      resources: [topic.topicArn],
    }));

    // Export the SNS topic ARN as an output
    new cdk.CfnOutput(this, 'SnsTopicArn', {
      value: topic.topicArn,
      description: 'The ARN of the SNS topic used for FlexMatch notifications',
      exportName: 'AmazonGameLiftSampleSnsTopicArn',
    });

  }

}
