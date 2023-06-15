# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import boto3
import os

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

    # We expect a successful JWT authorization has been done
    user_id = None
    try:
        user_id = event['requestContext']['authorizer']['jwt']['claims']['sub']
        print("user_id: ", user_id)
    except Exception as e:
        print("Exception: ", e)
        return error_response("user_id not available in claims")
    
    # Get player name from DynamoDB table in environment variable PLAYER_DATA_TABLE
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['PLAYER_DATA_TABLE'])
    try:
        response = table.get_item(Key={'UserID': user_id})
        #print("response from DynamoDB: ", response)
        if 'Item' in response:
            return {
                "statusCode": 200,
                "body": json.dumps(response['Item']),
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Credentials': True
                },
            }
    except Exception as e:
        print("Exception: ", e)
        return error_response("Error getting player data")
