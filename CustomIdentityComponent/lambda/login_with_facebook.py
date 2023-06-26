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

# Endpoint to validate the access token received from the user
facebook_validation_endpoint = "https://graph.facebook.com/"

# Creates a new user when there's no existing user for the Facebook ID
@tracer.capture_method
def create_user(facebook_id):

    # generate a unique id
    user_id = str(uuid.uuid4())

    # Check that user_id doesn't exist in DynamoDB table defined in environment variable USER_TABLE
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to write a new item to the table with user_id as partition key
    try:
        table.put_item(
            Item={
                'UserId': user_id,
                'FacebookId': facebook_id
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
def generate_success(user_id, facebook_id, jwt_token, refresh_token, auth_token_expires_in, refresh_token_expires_in):
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'facebook_id' : facebook_id,
            'auth_token' : jwt_token,
            'refresh_token' : refresh_token,
            'auth_token_expires_in' : auth_token_expires_in,
            'refresh_token_expires_in' : refresh_token_expires_in
        }),
        "isBase64Encoded": False
    }

@tracer.capture_method
def find_key_with_kid(key_set, kid):

    # Check if the key is already there
    for key in key_set:
        if key["kid"] == kid:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key)
        
    return None

# Tries to get an existing user from User Table. Reports error if request fails
@tracer.capture_method
def get_existing_user(facebook_id):
    try:
        facebook_id_user_table_name = os.getenv("FACEBOOK_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        facebook_id_user_table = dynamodb.Table(facebook_id_user_table_name)
        facebook_id_user_table_response = facebook_id_user_table.get_item(Key={'FacebookId': facebook_id})
        if 'Item' in facebook_id_user_table_response:
            logger.info("Found existing user in Facebook ID table:", user_id=facebook_id_user_table_response['Item']['UserId'])
            return True, facebook_id_user_table_response['Item']['UserId']
        else:
            return True, None
    except Exception as e:
        logger.info("Exception reading from user table: ", exception=e)

    return False, None

@tracer.capture_method
def add_new_user_to_facebook_id_table(user_id, facebook_id):
    try:
        facebook_id_user_table_name = os.getenv("FACEBOOK_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        facebook_id_user_table = dynamodb.Table(facebook_id_user_table_name)
        facebook_id_user_table.put_item(
        Item={
            'UserId': user_id,
            'FacebookId': facebook_id,
        });
        return True
    except Exception as e:
        logger.info("Exception adding user to Facebook ID table: ", e)
    
    return False

def link_facebook_id_to_existing_user(user_id, facebook_id):
    try:
        user_table_name = os.getenv("USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        user_table = dynamodb.Table(user_table_name)
        # Update existing user
        user_table.update_item(
            Key={
                'UserId': user_id,
            },
            UpdateExpression="set FacebookId = :val1",
            ExpressionAttributeValues={
                ':val1': facebook_id
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

    # Check if we have facebook_auth_token in querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'facebook_access_token' in event['queryStringParameters'] and 'facebook_user_id' in event['queryStringParameters']:

            facebook_auth_token = event['queryStringParameters']['facebook_access_token']
            received_facebook_user_id = event['queryStringParameters']['facebook_user_id']

            # Validate the Facebook auth token, and get Facebook user ID
            validated_facebook_user_id = None
            validated_app_id = None
            try:
                # Validate the user first
                facebook_token_validation_response = requests.get(facebook_validation_endpoint+received_facebook_user_id,
                                                                  params={'access_token': facebook_auth_token})
                facebook_token_validation_response_dict = facebook_token_validation_response.json()

                # Also validate the app ID so we know the token is linked to our app
                facebook_app_validation_response = requests.get(facebook_validation_endpoint+"app", params={'access_token': facebook_auth_token})
                facebook_app_validation_response_dict = facebook_app_validation_response.json()

            except Exception as e:
                print(e)
                return generate_error('Error: Token validation error')
             
            # If the response contains an error field, return error
            if 'error' in facebook_token_validation_response_dict:
                return generate_error('Error: User validation error')
            # If the app request contains an error field, return error
            if 'error' in facebook_app_validation_response_dict:
                return generate_error('Error: App validation error')
            # If the token is not linked to our app, return error
            if 'id' not in facebook_app_validation_response_dict or facebook_app_validation_response_dict['id'] != os.getenv("FACEBOOK_APP_ID"):
                return generate_error('Error: Token not linked to correct app')
            
            # Only add user if we receieived a valid Facebook ID
            if facebook_token_validation_response_dict != None and facebook_token_validation_response_dict['id'] != None:

                # Get the validated ID
                validated_facebook_user_id = facebook_token_validation_response_dict['id']
                
                success = False # Indicates the whole process success (existing user or new)

                # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
                existing_user_request_success, user_id = get_existing_user(validated_facebook_user_id)
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
                        success = link_facebook_id_to_existing_user(user_id, validated_facebook_user_id)
                        if success is False:
                            return generate_error('Error: Failed to link new user to existing user')
                    
                    # OPTION 3: Else If no user yet and we didn't request linking to an existing user, create one and add to user table
                    else:
                        logger.info("No user yet, creating a new one")
                        tries = 0
                        while user_id is None and tries < 10:
                            # Try to create a new user
                            user_id = create_user(validated_facebook_user_id)
                            tries += 1
                        if user_id == None:
                            return generate_error('Error: Failed to create user')
                    
                    # Add user to Appe Id User table in both cases (linking and new user)
                    user_creation_success = add_new_user_to_facebook_id_table(user_id, validated_facebook_user_id)
                
                # Create a JWT payload and encrypt with authenticated scope
                if user_id is not None and success is True:
                    payload = {
                        'sub': user_id,
                    }
                    # Create for scope "authenticated" so backend can differentiate from guest users if needed
                    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "authenticated")
                    # NOTE: We might want to send back all attached identities from user table?
                    return generate_success(user_id, validated_facebook_user_id, auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    return generate_error('Error: Failed to authenticate')
