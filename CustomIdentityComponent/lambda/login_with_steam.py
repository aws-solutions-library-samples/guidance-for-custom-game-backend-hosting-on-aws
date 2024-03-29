# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
from botocore.config import Config
import datetime
import uuid
import os
from encryption_and_decryption import encrypt, decrypt
import json
import requests
from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
from aws_lambda_powertools import Metrics
from aws_lambda_powertools import single_metric
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities import parameters
import time

tracer = Tracer()
logger = Logger()
metrics = Metrics()
metrics.set_default_dimensions(function=os.environ['AWS_LAMBDA_FUNCTION_NAME'])
config = Config(connect_timeout=2, read_timeout=2)
dynamodb = boto3.resource('dynamodb', config=config)

# partner.steam-api.com is the server to server endpoint per https://partner.steamgames.com/doc/webapi_overview#3 
steam_token_validation_api_endpoint = "https://partner.steam-api.com/ISteamUserAuth/AuthenticateUserTicket/v1/"

# Steam Web Api key from Secrets manager, cached between requests for 15 minutes
steam_web_api_secret_max_age = 15 * 60

def record_success_metric():
    metrics.add_metric(name="success", unit=MetricUnit.Count, value=1)


def record_failure_metric(reason: str):
    with single_metric(
        name="failure",
        unit=MetricUnit.Count,
        value=1,
        default_dimensions=metrics.default_dimensions
    ) as metric:
        metric.add_dimension(
            name="reason", value=reason)

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
    
    metrics.add_metric(name="created_user", unit=MetricUnit.Count, value=1)
    
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
        steam_id_user_table_name = os.getenv("STEAM_USER_TABLE")
        steam_id_user_table = dynamodb.Table(steam_id_user_table_name)
        steam_id_user_table.put_item(
        Item={
            'UserId': user_id,
            'SteamId': steam_id,
        })
        return True
    except Exception as e:
        logger.info("Exception adding user to Steam ID table: ", e)

    metrics.add_metric(name="new_steam_user", unit=MetricUnit.Count, value=1)
    
    return False

def link_steam_id_to_existing_user(user_id, steam_id):
    try:
        user_table_name = os.getenv("USER_TABLE")
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

    metrics.add_metric(name="linked_user", unit=MetricUnit.Count, value=1)
    
    return False

def check_steam_token(token: str) -> str:
    response = None
    response_body = {}

    # We will try validating the steam token if we get a 103 error (rare)
    # Note: The Lambda function will time out in the very unlikely case of this error happening 15 times in a row
    while True:
        steam_web_api_key = parameters.get_secret(os.environ['STEAM_WEB_API_KEY_SECRET_ARN'], max_age=steam_web_api_secret_max_age)
        send_time = datetime.datetime.utcnow()
        response = requests.get(steam_token_validation_api_endpoint, 
                                params={'key': steam_web_api_key, 'appid': os.environ['STEAM_APP_ID'], 'ticket': token})
        elapsed_ms = round(((datetime.datetime.utcnow()-send_time).microseconds)/1000)
        logger.debug(f'Steam response', elapsed_ms=elapsed_ms, code=response.status_code, headers=response.headers, body=response.text)

        with single_metric(
            name="duration",
            unit=MetricUnit.Milliseconds,
            value=elapsed_ms,
            default_dimensions=metrics.default_dimensions
        ) as metric:
            metric.add_dimension(name="partner", value='Steam')
            metric.add_dimension(name="api", value='ISteamUserAuth/AuthenticateUserTicket/v1')
        
        if response.status_code != 200:
            logger.error("Steam returned an error", response_code=response.status_code)
            record_failure_metric(f'Steam returned {response.status_code}')
            return None

        response_body = response.json()

        # retry on error code 103 after a second
        if response_body.get('response', {}).get('error', {}).get('errorcode', 0) != 103:
            break

        logger.info("Received 103, retrying after a delay")
        with single_metric(
            name="retry",
            unit=MetricUnit.Count,
            value=1,
            default_dimensions=metrics.default_dimensions
        ) as metric:
            metric.add_dimension(name="partner", value='Steam')
            metric.add_dimension(name="api", value='ISteamUserAuth/AuthenticateUserTicket/v1')
        time.sleep(1)

    response_dict = response_body.get('response', {}).get('params', {})
    if not 'result' in response_dict or response_dict['result'] != 'OK':
        result = response_dict['result']
        logger.info("Result in not OK", result=result)
        record_failure_metric(f'Steam result {result}')
        return None
    
    if not 'steamid' in response_dict:
        logger.error("steamid missing", response=response_dict)
        record_failure_metric(f'steamid missing')
        return None
    steam_user_id = response_dict['steamid']
    
    if 'vacbanned' in response_dict and response_dict['publisherbanned']:
        logger.info("User is VAC Banned", steam_user_id=steam_user_id)
        record_failure_metric(f'VAC Banned')
        return None
    
    if 'publisherbanned' in response_dict and response_dict['publisherbanned']:
        logger.info("User is Publisher Banned", steam_user_id=steam_user_id)
        record_failure_metric(f'Publisher Banned')
        return None
    
    return steam_user_id


# define a lambda function that returns a user_id
@metrics.log_metrics
@tracer.capture_lambda_handler
def lambda_handler(event, context):
    
    steam_auth_token = None

    # Check if we have steam_auth_token in querystrings
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'steam_auth_token' in event['queryStringParameters']:

            steam_auth_token = event['queryStringParameters']['steam_auth_token']
            #logger.info("Received Steam auth token: ", steam_auth_token=steam_auth_token)
            
            # Validate the Steam auth token, and get Steam user ID
            steam_user_id = None
            try:
                steam_user_id = check_steam_token(steam_auth_token)
                
                # Check if we received a valid success response from Steam
                if steam_user_id:
                    logger.info("Received Steam user ID: ", steam_user_id=steam_user_id)
                else:
                    # failure metrics handled by check_steam_token
                    return generate_error('Error: Failed to validate Steam token')

            except Exception as e:
                logger.error("exception in check_steam_token", error=e)
                record_failure_metric(f'exception in check_steam_token {e}')
                return generate_error('Error: Token validation error')

            if steam_user_id != None:
                
                success = False # Indicates the whole process success (existing user or new)

                # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
                existing_user_request_success, user_id = get_existing_user(steam_user_id)
                # If there was a problem getting existing user, abort as we don't want to create duplicate
                if existing_user_request_success is False:
                    record_failure_metric(f'Failed the try getting existing user')
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
                            record_failure_metric(f'Failed to authenticate with existing identity')
                            return generate_error('Error: Failed to authenticate with existing identity')
                        # Set the user_id
                        user_id = decoded_backend_token['sub']
                        # Try to link the new user to an existing user
                        success = link_steam_id_to_existing_user(user_id, steam_user_id)
                        if success is False:
                            record_failure_metric(f'Failed to link new user to existing user')
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
                            record_failure_metric(f'Failed to create user')
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
                    record_success_metric()
                    return generate_success(user_id, steam_user_id, auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    record_failure_metric(f'Invalid request')
    return generate_error('Error: Failed to authenticate')
