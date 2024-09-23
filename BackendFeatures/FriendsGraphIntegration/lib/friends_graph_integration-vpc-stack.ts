// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';

import { Construct } from 'constructs';

// Custom stack properties
export interface GameBackendFriendsIntegrationVpcStackProps extends cdk.StackProps {
    // custom identity provider issuer URL
    issuerEndpointUrl: string,
  }

export class GameBackendFriendsIntegrationVpcStack extends cdk.Stack {
    readonly vpc: ec2.Vpc;

    constructor(scope: Construct, id: string, props: GameBackendFriendsIntegrationVpcStackProps) {
        super(scope, id, props);

        // Define a CloudFormation parameter for the issuer endpoint URL
        const issuerEndpointUrl = new cdk.CfnParameter(this, 'IssuerEndpointUrl', {
            type: 'String',
            description: 'The URL of the issuer endpoint',
            default: props.issuerEndpointUrl,
        });

        // Create a new VPC
        this.vpc = new ec2.Vpc(this, 'VPC', {
            cidr: '10.192.10.0/16',
            natGateways: 1,
            subnetConfiguration: [
                {
                    name: 'Public',
                    subnetType: ec2.SubnetType.PUBLIC,
                    cidrMask: 24
                },
                {
                    name: 'Private',
                    subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidrMask: 24
                }
            ]
        });

        new cdk.CfnOutput(this, 'VPCId', {
            description: 'VPC ID',
            value: this.vpc.vpcId
        });
    }
}