import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';

export class DeploymentPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Define an IAM role for codebuild that has Admin user access to allow deploying all the resources
    const codeBuildRole = new iam.Role(this, 'CodeBuildRole', {
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AdministratorAccess')],
    });

    // Define the CodeBuild project
    const codeBuildProject = new codebuild.Project(this, 'GameBackendCodeBuildProject', {
      role: codeBuildRole,
      projectName: 'GameBackendCodeBuildProject',
      source: codebuild.Source.gitHub({
        owner: 'aws-solutions-library-samples',
        repo: 'guidance-for-custom-game-backend-hosting-on-aws',
        cloneDepth: 1,
      }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.MEDIUM,
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          install: {
            'runtime-versions': {
              nodejs: '18.x',
              python: '3.x',
            },
            'commands': [
              'npm install aws-cdk -g'
            ]
          },
          build: {
            commands: [
              'cd CustomIdentityComponent',
              'npm install',
              'cdk bootstrap',
              'cdk deploy --require-approval never',
              'fn=$(aws cloudformation describe-stacks --stack-name CustomIdentityComponentStack --query \'Stacks[0].Outputs[?OutputKey==`GenerateKeysFunctionName`].OutputValue\' --output text)',
              'aws lambda invoke --function-name $fn response.json',
              "issuer_endpoint=$(aws cloudformation describe-stacks --stack-name CustomIdentityComponentStack --query 'Stacks[0].Outputs[?OutputKey==`IssuerEndpointUrl`].OutputValue' --output text)",
              "cd ..",
              "cd BackendComponentSamples",
              "npm install",
              "cdk deploy PythonServerlessHttpApiStack --require-approval never --parameters IssuerEndpointUrl=$issuer_endpoint"
          ],
          },
        }
      }),
    });
  }
}