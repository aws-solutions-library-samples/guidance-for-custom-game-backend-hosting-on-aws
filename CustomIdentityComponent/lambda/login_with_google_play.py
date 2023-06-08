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

google_play_token_creation_api_endpoint = "https://accounts.google.com/o/oauth2/token"
google_play_token_validation_api_endpoint = "https://www.googleapis.com/games/v1/applications/"+os.environ['GOOGLE_PLAY_APP_ID']+"/verify/"

# Google Play Client Secret from Secrets manager, cached between requests for 15 minutes
google_play_client_secret = None
last_google_play_client_secret_refresh = 0

def refresh_google_play_client_secret_if_needed():
    global google_play_client_secret, last_google_play_client_secret_refresh

    # get time difference between current time and last_private_key_refresh
    current_time = int(time.time())
    time_difference = current_time - last_google_play_client_secret_refresh
    print("Time difference between current time and last_google_play_client_secret_refresh: ", time_difference)

    # check if we need to refresh the private key (every 30 minutes as this changes rarely if ever)
    if time_difference > 1800 or google_play_client_secret == None:
        print("Refreshing Google Play Client Secret")

        # get private key from AWS Secrets Manager
        secret_arn= os.environ['GOOGLE_PLAY_CLIENT_SECRET_ARN']
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager')
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_arn
        )
        google_play_client_secret = get_secret_value_response['SecretString']
        last_google_play_client_secret_refresh = int(time.time())

# Creates a new user when there's no existing user for the Google Play ID
@tracer.capture_method
def create_user(google_play_id):

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
                'GooglePlayId': google_play_id # NOTE: You might want to add other information from the Google Play API response too
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
def generate_success(user_id, google_play_id, jwt_token, refresh_token, auth_token_expires_in, refresh_token_expires_in):
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'google_play_id' : google_play_id,
            'auth_token' : jwt_token,
            'refresh_token': refresh_token,
            'auth_token_expires_in': auth_token_expires_in,
            'refresh_token_expires_in': refresh_token_expires_in
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
def get_existing_user(google_play_id):
    try:
        google_play_user_table_name = os.getenv("GOOGLE_PLAY_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        google_play_user_table = dynamodb.Table(google_play_user_table_name)
        google_play_user_table_response = google_play_user_table.get_item(Key={'GooglePlayId':google_play_id})
        if 'Item' in google_play_user_table_response:
            logger.info("Found existing user in Google Play User table:", user_id=google_play_user_table_response['Item']['UserId'])
            return True, google_play_user_table_response['Item']['UserId']
        else:
            return True, None
    except Exception as e:
        logger.info("Exception reading from user table: ", exception=e)

    return False, None

@tracer.capture_method
def add_new_user_to_google_play_table(user_id, google_play_id):
    try:
        google_play_user_table_name = os.getenv("GOOGLE_PLAY_USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        google_play_user_table = dynamodb.Table(google_play_user_table_name)
        google_play_user_table.put_item(
        Item={
            'UserId': user_id,
            'GooglePlayId': google_play_id # NOTE: You might want to add other information from the Google Play API response too
        });
        return True
    except Exception as e:
        logger.info("Exception adding user to Google Play User table: ", e)
    
    return False

def link_google_play_to_existing_user(user_id, google_play_id):
    try:
        user_table_name = os.getenv("USER_TABLE");
        dynamodb = boto3.resource('dynamodb')
        user_table = dynamodb.Table(user_table_name)
        # Update existing user
        user_table.update_item(
            Key={
                'UserId': user_id,
            },
            UpdateExpression="set GooglePlayId = :val1",
            ExpressionAttributeValues={
                ':val1': google_play_id
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
    
    google_play_auth_token = None

    # Make sure we have the Google Play Client Secret for token validation
    refresh_google_play_client_secret_if_needed()

    # Check if we have google_play_auth_token in querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'google_play_auth_token' in event['queryStringParameters']:
            
            # The token received from client needs to be exchanged for a bearer token
            google_play_auth_token = event['queryStringParameters']['google_play_auth_token']

            # Generate and validate the Google Play token and get user ID
            google_play_user_id = None
            try:
                # Generate the Bearer token
                token_generation_params = {'grant_type': 'authorization_code', 'code': google_play_auth_token, 'client_id': os.environ['GOOGLE_PLAY_CLIENT_ID'], 'client_secret': google_play_client_secret}
                token_generation_response = requests.post(google_play_token_creation_api_endpoint, token_generation_params)
                #logger.info(token_generation_response.content)
                token_generation_response_dict = token_generation_response.json()
                if 'access_token' in token_generation_response_dict:
                    google_play_auth_bearer_token = token_generation_response_dict['access_token']
                else:
                    return generate_error('Error: Failed to generate Google Play bearer token')
                # Get the user information with the bearer token
                token_validation_header = {'Authorization': 'Bearer ' + google_play_auth_bearer_token}
                google_play_token_validation_response = requests.get(google_play_token_validation_api_endpoint, headers=token_validation_header)
                #logger.info(google_play_token_validation_response.content)
                google_play_token_validation_response_dict = google_play_token_validation_response.json()
                if 'player_id' in google_play_token_validation_response_dict:
                    google_play_user_id = google_play_token_validation_response_dict['player_id']
                else:
                    return generate_error('Error: Failed to validate Google Play bearer token')
            except Exception as e:
                print(e)
                return generate_error('Error: Token validation error')

            if google_play_user_id != None:
                
                success = False # Indicates the whole process success (existing user or new)

                # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
                existing_user_request_success, user_id = get_existing_user(google_play_user_id)
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
                        success = link_google_play_to_existing_user(user_id, google_play_user_id)
                        if success is False:
                            return generate_error('Error: Failed to link new user to existing user')
                    
                    # OPTION 3: Else If no user yet and we didn't request linking to an existing user, create one and add to user table
                    else:
                        logger.info("No user yet, creating a new one")
                        tries = 0
                        while user_id is None and tries < 10:
                            # Try to create a new user
                            user_id = create_user(google_play_user_id)
                            tries += 1
                        if user_id == None:
                            return generate_error('Error: Failed to create user')
                    
                    # Add user to Appe Id User table in both cases (linking and new user)
                    user_creation_success = add_new_user_to_google_play_table(user_id, google_play_user_id)
                
                # Create a JWT payload and encrypt with authenticated scope
                if user_id is not None and success is True:
                    payload = {
                        'sub': user_id,
                    }
                    # Create for scope "authenticated" so backend can differentiate from guest users if needed
                    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "authenticated")
                    # NOTE: We might want to send back all attached identities from user table?
                    return generate_success(user_id, google_play_user_id, auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    return generate_error('Error: Failed to authenticate')
