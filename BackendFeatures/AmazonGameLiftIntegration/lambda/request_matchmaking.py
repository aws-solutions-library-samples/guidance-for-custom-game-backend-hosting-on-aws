# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os
import boto3

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
tracer = Tracer()
logger = Logger()

MATCHMAKING_CONFIGURATION = os.environ['MATCHMAKING_CONFIGURATION']

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
    #print(event)

    # We expect a successful JWT authorization has been done
    user_id = None
    try:
        user_id = event['requestContext']['authorizer']['jwt']['claims']['sub']
        print("user_id: ", user_id)
    except Exception as e:
        print("Exception: ", e)
        return error_response("user_id not available in claims")
    
    if 'body' not in event:
        return error_response("No latency data provided")
    
    print("event['body']: ", event['body'])
    
    # If the event body is string, load it as a dictionary
    if isinstance(event['body'], str):
        print("Event body is not a dictionary, convert")
        event['body']= eval(event['body'])

    # Get the latency json from the event post attributes
    latency_json = event['body']['latencyInMs']

    print("latency_json: ", latency_json)

    # Request matchmaking through GameLift
    client = boto3.client('gamelift')
    response = client.start_matchmaking(
        ConfigurationName=MATCHMAKING_CONFIGURATION,
        Players=[
            {
                'PlayerId': user_id,
                'LatencyInMs': latency_json
            }
        ]
    )

    if 'MatchmakingTicket' not in response:
        return error_response("Matchmaking request failed")
    
    return {
        "statusCode": 200,
        "body": json.dumps(response['MatchmakingTicket'], default=str),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        }
    }
