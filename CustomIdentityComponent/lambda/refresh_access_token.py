# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import uuid
import os
from encryption_and_decryption import encrypt, decrypt_refresh_token
import json

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
tracer = Tracer()
logger = Logger()

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

# define a lambda function that returns a user_id
@tracer.capture_lambda_handler
def lambda_handler(event, context):

    user_id = None
    scope = None
    existing_exp_value = None
    # Check if we have a refresh token in the request
    if 'queryStringParameters' in event and event['queryStringParameters'] is not None:         
        if 'refresh_token' in event['queryStringParameters']:
            try:
                refresh_token = event['queryStringParameters']['refresh_token']
                logger.info("Received a valid refresh token")

                # decrypt the refresh token
                decoded_refresh_token = decrypt_refresh_token(refresh_token)
                if decoded_refresh_token is None:
                    return generate_error('Error: Failed to validate refresh token')
                # Set the user_id and access scope
                user_id = decoded_refresh_token['sub']
                scope = decoded_refresh_token['access_token_scope']
                existing_exp_value = decoded_refresh_token['exp']
            except:
                return generate_error('Error: Failed to validate refresh token')
    else:
        return generate_error('Error: No refresh token provided')   

    if user_id is None:
        return generate_error('Error: Failed to validate refresh token')    
           
    # Create a JWT payload and encrypt with the scope from the refresh token
    payload = {
        'sub': user_id
    }
    # Note, we will generate a new refresh token as well every time
    auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, scope, existing_exp_value)

    # Return jwt
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'auth_token' : auth_token,
            'refresh_token' : refresh_token,
            'auth_token_expires_in' : auth_token_expires_in,
            'refresh_token_expires_in' : refresh_token_expires_in
        }),
        "isBase64Encoded": False
    }