# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os
import boto3

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
tracer = Tracer()
logger = Logger()

def error_response(message):
    return {
        "statusCode": 500,
        "body": json.dumps(message),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
    }

@tracer.capture_lambda_handler
def lambda_handler(event, context):

    # Get the SNS message (we're expecting this function is not called by anything else)
    message = json.loads(event['Records'][0]['Sns']['Message'])
    logger.info(message)

    # Get the matchmaking status from the SNS message
    matchmaking_status = message['detail']['type']

    logger.info(f"Matchmaking status: {matchmaking_status}")


