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
    #print(event)

    # We expect a successful JWT authorization has been done
    user_id = None
    try:
        user_id = event['requestContext']['authorizer']['jwt']['claims']['sub']
        print("user_id: ", user_id)
    except Exception as e:
        print("Exception: ", e)
        return error_response("user_id not available in claims")
    
    # Check if the event has querystrings
    if 'queryStringParameters' not in event:
        # Return 500 error if the event has no querystring.
        return error_response("No querystrings provided")
    
    # Check if querystrings has a player_name
    if 'player_name' not in event['queryStringParameters']:
        # Return 500 error if the event has no querystring.
        return error_response("No player_name provided")
    
    # Write the new player name to DynamoDB table in environment variable PLAYER_DATA_TABLE
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['PLAYER_DATA_TABLE'])
    table.put_item(
        Item={
        'UserID': user_id,
        'PlayerName': event['queryStringParameters']['player_name']
    })

    return {
        "statusCode": 200,
        "body": json.dumps("Successfully updated player data"),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
    }
