# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os
import boto3

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
tracer = Tracer()
logger = Logger()

def error_response(message, code):
    return {
        "statusCode": code,
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
        return error_response("user_id not available in claims", 500)
    
    # Check that we have ticketId in the querystrings
    if 'ticketId' not in event['queryStringParameters']:
        return error_response("ticketId not available in querystrings", 500)
    
    ticketId = event['queryStringParameters']['ticketId']

    # Check if we received an item to the DynamoDB table for the ticketId
    client = boto3.client('dynamodb')
    response = client.get_item(
        TableName=os.environ['MATCHMAKING_TICKETS_TABLE'],
        Key={
            'TicketID': {
                'S': ticketId
            }
        }
    )

    if 'Item' not in response:
        return error_response("TicketId not found in DynamoDB table", 400)
    
    # Extract MatchmakingStatus, Port, IPAddress and DnsEndpoint from the response to a dictionary
    response_to_client = {
        'MatchmakingStatus': response['Item']['MatchmakingStatus']['S']
    }

    # If response conatins Port, IPAddress and DnsEndpoint, add them to the dictionary
    if 'Port' in response['Item']:
        response_to_client['Port'] = response['Item']['Port']['N']
    if 'IpAddress' in response['Item']:
        response_to_client['IpAddress'] = response['Item']['IpAddress']['S']
    if 'DnsName'in response['Item']:
        response_to_client['DnsName'] = response['Item']['DnsName']['S']
    if 'PlayerSessionId' in response['Item']:
        response_to_client['PlayerSessionId'] = response['Item']['PlayerSessionId']['S']
    return {
        "statusCode": 200,
        "body": json.dumps(response_to_client, default=str),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        }
    }
