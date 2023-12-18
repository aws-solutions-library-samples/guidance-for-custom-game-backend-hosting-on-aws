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
    
    # Check that we have ticketId in the querystrings
    if 'ticketId' not in event['queryStringParameters']:
        return error_response("ticketId not available in querystrings")
    
    ticketId = event['queryStringParameters']['ticketId']

    # Check if we received an item to the DynamoDB table for the ticketId
    client = boto3.client('dynamodb')
    response = client.get_item(
        TableName=os.environ['MATCHMAKING_TICKETS_TABLE'],
        Key={
            'TicketId': {
                'S': ticketId
            }
        }
    )

    if 'Item' not in response:
        return error_response("TicketId not found in DynamoDB table")

    return {
        "statusCode": 200,
        "body": json.dumps(response, default=str),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
    }
