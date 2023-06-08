# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import uuid
import jwt
from jwcrypto import jwk
import boto3
import os

def lambda_handler(event, context):

    # Generate a new RS256 key pair and random UUID for key ID
    kid_value = str(uuid.uuid4())
    key = jwk.JWK.generate(kty='RSA', size=2048, alg='RS256', use='sig', kid=kid_value)
    public_key = key.export_public()
    private_key = key.export_private()

    s3 = boto3.client('s3')
    
    # try to get the S3 object '.well-known/jwks.json' in os.environ['ISSUER_BUCKET']
    old_key = None
    try:
        old_key = s3.get_object(Bucket=os.environ['ISSUER_BUCKET'], Key='.well-known/jwks.json')
    except Exception as e:
        print(e)
        print("No .well-known/jwks.json object exists in the S3 bucket yet")

    old_key_dict = None
    if old_key != None:
        # Convert old_key from JSON to dictionary
        old_key_dict = json.loads(old_key['Body'].read())
        print("Old key found:")
        print(old_key_dict)

    # Get the first key only from old keys
    previous_key = None
    if old_key_dict != None and 'keys' in old_key_dict:
        previous_key = old_key_dict['keys'][0]

    # prepare the key(s) as a list for publi key endpoint
    public_keys_dict = None
    if previous_key != None:
        public_keys_dict = {"keys": [json.loads(public_key), previous_key]}
    else:
        public_keys_dict = {"keys": [json.loads(public_key)]}

    # Upload public key to S3 bucket in environment variable "issuer_bucket"
    #Add content type application/json to the object
    s3.put_object(Body=json.dumps(public_keys_dict), Bucket=os.environ['ISSUER_BUCKET'], Key='.well-known/jwks.json', ContentType='application/json')

    # Add new private key to Secrets Manager
    secrets_manager = boto3.client('secretsmanager')
    secrets_manager.put_secret_value(SecretId=os.environ['SECRET_KEY_ID'], SecretString=private_key)

    # Define openid-configuration
    openid_configuration = {
        "issuer": os.environ['ISSUER_ENDPOINT'],
        "jwks_uri": os.environ['ISSUER_ENDPOINT']+"/.well-known/jwks.json",
        "id_token_signing_alg_values_supported":["RS256"],
        "scopes_supported":["guest", "authenticated"]
    }

    # Add open-id configuration to S3 bucket with key .well-known/openid-configuration
    s3.put_object(Body=json.dumps(openid_configuration), Bucket=os.environ['ISSUER_BUCKET'], Key='.well-known/openid-configuration', ContentType='application/json')
