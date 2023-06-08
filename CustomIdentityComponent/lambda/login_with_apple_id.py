# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import uuid
import os
import jwt
from encryption_and_decryption import encrypt, decrypt
import json
import requests
from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
import time

tracer = Tracer()
logger = Logger()

apple_public_key_url = "https://appleid.apple.com/auth/keys"
apple_public_keys = None
last_apple_public_key_refresh = 0

# Method for requesting the latest Apple public keys (cached for 15 minutes)
@tracer.capture_method
def get_apple_public_keys():
    global apple_public_keys
    global last_apple_public_key_refresh
    # time since last apple public key refresh
    now = int(time.time())
    time_since_last_refresh = now - last_apple_public_key_refresh

    # If we don't have a public key or it's 15 minutes from last refresh fetch it
    if apple_public_keys is None or time_since_last_refresh > 900:
        logger.info("Refreshing Apple public key set")
        key_payload = requests.get(apple_public_key_url).json()
        #logger.info("Received key set: ", key=key_payload)
        apple_public_keys = key_payload["keys"]
        last_apple_public_key_refresh = int(time.time())

    return apple_public_keys

# Creates a new user when there's no existing user for the Apple ID
@tracer.capture_method
def create_user(apple_id):

    # generate a unique id
    user_id = str(uuid.uuid4())

    # Check that user_id doesn't exist in DynamoDB table defined in environment variable USER_TABLE
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to write a new iteam to the table with user_id as partition key
    try:
        table.put_item(
            Item={
                'UserId': user_id,
                'AppleId': apple_id
            },
            ConditionExpression='attribute_not_exists(UserId)'
        )
    except:
        logger.info("User already exists")
        return None
    
    return user_id

@tracer.capture_method
def generate_error(message):
    return {
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'statusCode': 401,
        'body': message
    }

@tracer.capture_method
def generate_success(user_id, apple_id, jwt_token, refresh_token, auth_token_expires_in, refresh_token_expires_in):
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'apple_id' : apple_id,
            'auth_token' : jwt_token,
            'refresh_token' : refresh_token,
            'auth_token_expires_in' : auth_token_expires_in,
            'refresh_token_expires_in' : refresh_token_expires_in
        }),
        "isBase64Encoded": False
    }

@tracer.capture_method
def find_key_with_kid(key_set, kid):
    for key in key_set:
        if key["kid"] == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return None

# Tries to get an existing user from User Table. Reports error if request fails
@tracer.capture_method
def get_existing_user(apple_id):
    try:
        apple_id_user_table_name = os.getenv("APPLE_ID_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        apple_id_user_table = dynamodb.Table(apple_id_user_table_name)
        apple_id_user_table_response = apple_id_user_table.get_item(Key={'AppleId': apple_id})
        if 'Item' in apple_id_user_table_response:
            logger.info("Found existing user in Apple ID table:", user_id=apple_id_user_table_response['Item']['UserId'])
            return True, apple_id_user_table_response['Item']['UserId']
        else:
            return True, None
    except Exception as e:
        logger.info("Exception reading from user table: ", exception=e)

    return False, None

@tracer.capture_method
def add_new_user_to_apple_id_table(user_id, apple_id):
    try:
        apple_id_user_table_name = os.getenv("APPLE_ID_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        apple_id_user_table = dynamodb.Table(apple_id_user_table_name)
        apple_id_user_table.put_item(
        Item={
            'UserId': user_id,
            'AppleId': apple_id,
        });
        return True
    except Exception as e:
        logger.info("Exception adding user to Apple ID table: ", e)
    
    return False

def link_apple_id_to_existing_user(user_id, apple_id):
    try:
        user_table_name = os.getenv("USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        user_table = dynamodb.Table(user_table_name)
        # Update existing user
        user_table.update_item(
            Key={
                'UserId': user_id,
            },
            UpdateExpression="set AppleId = :val1",
            ExpressionAttributeValues={
                ':val1': apple_id
            },
            ConditionExpression='attribute_exists(UserId)'
        )
        return True
    except Exception as e:
        logger.info("Exception linking user to existing user: ", e)
    
    return False

# define a lambda function that returns a user_id
@tracer.capture_lambda_handler
def lambda_handler(event, context):

    # Get Apple public key
    apple_key_set = get_apple_public_keys()

    # Get the audience (our app identifier)
    my_audience = os.getenv("APPLE_APP_ID")
    logger.info("Audience: ", audience=my_audience)
    
    apple_auth_token = None

    # Check if we have apple_auth_token in querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'apple_auth_token' in event['queryStringParameters']:

            apple_auth_token = event['queryStringParameters']['apple_auth_token']
            #logger.info("Received Apple auth token: ", apple_auth_token=apple_auth_token)
            
            # Validate the Apple auth token, and get Apple user ID (sub)
            decoded_apple_auth_token = None
            try:
                # Get kid from header and find the right key from key set
                kid = jwt.get_unverified_header(apple_auth_token)['kid']
                public_key = find_key_with_kid(apple_key_set, kid)
                # Decode the token
                decoded_apple_auth_token = jwt.decode(apple_auth_token, public_key, audience=my_audience, algorithms=["RS256"])
            except Exception as e:
                print(e)
                return generate_error('Error: Token validation error')
            
            if decoded_apple_auth_token != None:
                
                success = False # Indicates the whole process success (existing user or new)

                # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
                existing_user_request_success, user_id = get_existing_user(decoded_apple_auth_token['sub'])
                # If there was a problem getting existing user, abort as we don't want to create duplicate
                if existing_user_request_success is False:
                    return generate_error('Error: Failed the try getting existing user')
                else:
                    success = True # Successfully tried getting existing user, might still be None (not found)
                
                # If no existing user, we are either linking to one or creating a new one
                if user_id == None:
                    query_params = event['queryStringParameters']

                    # OPTION 2: Check if client sent a backend auth_token and requested linking to an existing user
                    if 'auth_token' in query_params and 'link_to_existing_user' in query_params and query_params['link_to_existing_user'] == "Yes":
                        # Validate the auth_token
                        decoded_backend_token = decrypt(query_params['auth_token'])
                        if decoded_backend_token is None:
                            return generate_error('Error: Failed to authenticate with existing identity')
                        # Set the user_id
                        user_id = decoded_backend_token['sub']
                        # Try to link the new user to an existing user
                        success = link_apple_id_to_existing_user(user_id, decoded_apple_auth_token['sub'])
                        if success is False:
                            return generate_error('Error: Failed to link new user to existing user')
                    
                    # OPTION 3: Else If no user yet and we didn't request linking to an existing user, create one and add to user table
                    else:
                        logger.info("No user yet, creating a new one")
                        tries = 0
                        while user_id is None and tries < 10:
                            # Try to create a new user
                            user_id = create_user(decoded_apple_auth_token['sub'])
                            tries += 1
                        if user_id == None:
                            return generate_error('Error: Failed to create user')
                    
                    # Add user to Appe Id User table in both cases (linking and new user)
                    user_creation_success = add_new_user_to_apple_id_table(user_id, decoded_apple_auth_token['sub'])
                
                # Create a JWT payload and encrypt with authenticated scope
                if user_id is not None and success is True:
                    payload = {
                        'sub': user_id,
                    }
                    # Create for scope "authenticated" so backend can differentiate from guest users if needed
                    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "authenticated")
                    # NOTE: We might want to send back all attached identities from user table?
                    return generate_success(user_id, decoded_apple_auth_token['sub'], auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    return generate_error('Error: Failed to authenticate')
