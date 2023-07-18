# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
from botocore.config import Config
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
config = Config(connect_timeout=2, read_timeout=2)
dynamodb = boto3.resource('dynamodb', config=config)

steam_token_validation_api_endpoint = "https://api.steampowered.com/ISteamUserAuth/AuthenticateUserTicket/v1/"

# Steam Web Api key from Secrets manager, cached between requests for 15 minutes
steam_web_api_key = None
last_steam_web_api_key_refresh = 0

def refresh_steam_web_api_key_if_needed():
    global steam_web_api_key, last_steam_web_api_key_refresh

    # get time difference between current time and last_private_key_refresh
    current_time = int(time.time())
    time_difference = current_time - last_steam_web_api_key_refresh
    print("Time difference between current time and last_steam_web_api_key_refresh: ", time_difference)

    # check if we need to refresh the private key (every 30 minutes as this changes rarely if ever)
    if time_difference > 1800 or steam_web_api_key == None:
        print("Refreshing Steam web api key")

        # get private key from AWS Secrets Manager
        secret_arn= os.environ['STEAM_WEB_API_KEY_SECRET_ARN']
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager')
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_arn
        )
        steam_web_api_key = get_secret_value_response['SecretString']
        last_steam_web_api_key_refresh = int(time.time())

# Creates a new user when there's no existing user for the Steam ID
@tracer.capture_method
def create_user(steam_id):

    # generate a unique id
    user_id = str(uuid.uuid4())

    # Check that user_id doesn't exist in DynamoDB table defined in environment variable USER_TABLE
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to write a new iteam to the table with user_id as partition key
    try:
        table.put_item(
            Item={
                'UserId': user_id,
                'SteamId': steam_id # NOTE: You might want to add other information from the Steam API response too
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
def generate_success(user_id, steam_id, jwt_token, refresh_token, auth_token_expires_in, refresh_token_expires_in):
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'steam_id' : steam_id,
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
def get_existing_user(steam_id):
    try:
        steam_user_table_name = os.getenv("STEAM_USER_TABLE");
        steam_user_table = dynamodb.Table(steam_user_table_name)
        steam_user_table_response = steam_user_table.get_item(Key={'SteamId':steam_id})
        if 'Item' in steam_user_table_response:
            logger.info("Found existing user in Steam ID table:", user_id=steam_user_table_response['Item']['UserId'])
            return True, steam_user_table_response['Item']['UserId']
        else:
            return True, None
    except Exception as e:
        logger.info("Exception reading from user table: ", exception=e)

    return False, None

@tracer.capture_method
def add_new_user_to_steam_table(user_id, steam_id):
    try:
        steam_id_user_table_name = os.getenv("STEAM_USER_TABLE");
        steam_id_user_table = dynamodb.Table(steam_id_user_table_name)
        steam_id_user_table.put_item(
        Item={
            'UserId': user_id,
            'SteamId': steam_id, # NOTE: You might want to add other information from the Steam API response too
        });
        return True
    except Exception as e:
        logger.info("Exception adding user to Steam ID table: ", e)
    
    return False

def link_steam_id_to_existing_user(user_id, steam_id):
    try:
        user_table_name = os.getenv("USER_TABLE");
        user_table = dynamodb.Table(user_table_name)
        # Update existing user
        user_table.update_item(
            Key={
                'UserId': user_id,
            },
            UpdateExpression="set SteamId = :val1",
            ExpressionAttributeValues={
                ':val1': steam_id
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
    
    steam_auth_token = None

    # Make sure we have the Steam Web Api Key for token validation
    refresh_steam_web_api_key_if_needed()

    # Check if we have steam_auth_token in querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'steam_auth_token' in event['queryStringParameters']:

            steam_auth_token = event['queryStringParameters']['steam_auth_token']
            #logger.info("Received Steam auth token: ", steam_auth_token=steam_auth_token)
            
            # Validate the Steam auth token, and get Steam user ID
            steam_token_validation_response_dict = None
            steam_user_id = None
            try:
                steam_ticket_validation_response = requests.get(steam_token_validation_api_endpoint, params={'key': steam_web_api_key,
                                                                 'appid': os.environ['STEAM_APP_ID'], 'ticket': steam_auth_token})
                #logger.info(steam_ticket_validation_response.content)
                steam_token_validation_response_dict = steam_ticket_validation_response.json()
                
                # Check if we received a valid success response from Steam
                if 'params' in steam_token_validation_response_dict['response'] and 'ownersteamid' in steam_token_validation_response_dict['response']['params']:
                    steam_user_id = steam_token_validation_response_dict['response']['params']['ownersteamid']
                    logger.info("Received Steam user ID: ", steam_user_id=steam_user_id)
                else:
                    return generate_error('Error: Failed to validate Steam token')


            except Exception as e:
                print(e)
                return generate_error('Error: Token validation error')

            if steam_user_id != None:
                
                success = False # Indicates the whole process success (existing user or new)

                # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
                existing_user_request_success, user_id = get_existing_user(steam_user_id)
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
                        success = link_steam_id_to_existing_user(user_id, steam_user_id)
                        if success is False:
                            return generate_error('Error: Failed to link new user to existing user')
                    
                    # OPTION 3: Else If no user yet and we didn't request linking to an existing user, create one and add to user table
                    else:
                        logger.info("No user yet, creating a new one")
                        tries = 0
                        while user_id is None and tries < 10:
                            # Try to create a new user
                            user_id = create_user(steam_user_id)
                            tries += 1
                        if user_id == None:
                            return generate_error('Error: Failed to create user')
                    
                    # Add user to Appe Id User table in both cases (linking and new user)
                    user_creation_success = add_new_user_to_steam_table(user_id, steam_user_id)
                
                # Create a JWT payload and encrypt with authenticated scope
                if user_id is not None and success is True:
                    payload = {
                        'sub': user_id,
                    }
                    # Create for scope "authenticated" so backend can differentiate from guest users if needed
                    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "authenticated")
                    # NOTE: We might want to send back all attached identities from user table?
                    return generate_success(user_id, steam_user_id, auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    return generate_error('Error: Failed to authenticate')
