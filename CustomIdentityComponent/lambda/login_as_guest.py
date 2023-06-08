# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import uuid
import os
from encryption_and_decryption import encrypt
import json

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
tracer = Tracer()
logger = Logger()

# define create_user function
@tracer.capture_method
def create_user():

    # generate a unique id
    user_id = str(uuid.uuid4())
    
    # generate a random secret
    guest_secret = str(uuid.uuid4())+"-"+str(uuid.uuid4())

    # Check that user_id doesn't exist in DynamoDB table defined in environment variable USER_TABLE
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to write a new iteam to the table with user_id as partition key
    try:
        table.put_item(
            Item={
                'UserId': user_id,
                'GuestSecret': guest_secret
            },
            ConditionExpression='attribute_not_exists(UserId)'
        )
    except:
        logger.info("User already exists")
        return None
    
    return user_id, guest_secret

''' Checks that the user exists in the table and guest secrets match '''
@tracer.capture_method
def check_user_exists(existing_user_id, guest_secret):
    # Check that the user actually exists in the table and the guest_secret matches
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to read the item with user_id as partition key
    try:
        response = table.get_item(
            Key={
                'UserId': existing_user_id
            }
        )
        # Check that the guest_secret matches
        if 'Item' in response and response['Item']['GuestSecret'] == guest_secret:
            logger.info("Guest secret matches")
            return True

    except Exception as e:
        logger.exception("Error checking user exists: ", e)
        return False

    return False

# define a lambda function that returns a user_id
@tracer.capture_lambda_handler
def lambda_handler(event, context):

    # Check if the event has an existing user_id
    user_id = None
    guest_secret = None

    # Check if we have user_id and guest_secret in the event querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'user_id' in event['queryStringParameters'] and 'guest_secret' in event['queryStringParameters']:
            user_id = event['queryStringParameters']['user_id']
            logger.info("Existing user id: ", user_id=user_id)
            # Check that the user actually exists and the guest_secret matches
            if check_user_exists(user_id, event['queryStringParameters']['guest_secret']) == False:
                # return 500 error
                return {
                    'statusCode': 401,
                    'body': 'Error: Could not validate user'
                }
            guest_secret = event['queryStringParameters']['guest_secret']
        # If user_id in event but no guest_secret, return an error
        elif 'user_id' in event['queryStringParameters'] and 'guest_secret' not in event['queryStringParameters']:
            return {
                'statusCode': 400,
                'body': 'Error: No guest_secret in query string'
            }
    

    if user_id is None:
        logger.info("Creating a new user")
        # We'll try to create a user max 10 times finding a unique user_id  
        tries = 0
        while user_id is None and tries < 10:
            # Try to create a new user
            user_id, guest_secret = create_user()
            tries += 1

    # At this point we either have a user_id we received from the event or we created a new one, or we failed at creating one
    if user_id is None:
        # return 500 error
        return {
            'statusCode': 401,
            'body': 'Error: Could not create user'
        }
    
    logger.append_keys(user_id=user_id)
    
    # Create a JWT payload and encrypt with guest scope
    payload = {
        'sub': user_id
    }
    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "guest")

    # Return jwt
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'guest_secret' : guest_secret,
            'user_id' : user_id,
            'auth_token' : auth_token,
            'refresh_token' : refresh_token,
            'auth_token_expires_in' : auth_token_expires_in,
            'refresh_token_expires_in' : refresh_token_expires_in
        }),
        "isBase64Encoded": False
    }