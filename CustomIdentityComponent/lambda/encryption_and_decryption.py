# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import jwt
import requests
import time
import boto3
import os

# Access token expiration in seconds
access_token_expiration = 900

# Refresh token expiration in days
# (NOTE: make sure if you increase this that you also increase the rotation of the keys in the CDK app to avoid having a refresh token without a matching public key)
refresh_token_expiration_days = 6

# Private key refresh rate (should be relatively often to pick up new keys after rotation)
private_key_refresh_rate = 900

# Private key from Secrets manager, cached between requests for 15 minutes
private_key = None
last_private_key_refresh = 0

# Current key set from Issuer endpoint
jwks_key_set = None

def refresh_private_key():
    global private_key, last_private_key_refresh
    # get private key from AWS Secrets Manager
    secret_name = os.environ['SECRET_KEY_ID']
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager')
    get_secret_value_response = client.get_secret_value(
        SecretId=secret_name
    )
    private_key = get_secret_value_response['SecretString']
    last_private_key_refresh = int(time.time())

def is_kid_in_jwks_key_set(kid):
    global jwks_key_set

    # If we don't have keys at all, just return False
    if jwks_key_set == None:
        return False
    
    # Check if the key is in the set already
    for key in jwks_key_set["keys"]:
        if key["kid"] == kid:
            return True
    return False

def refresh_jwks_key_set(issuer_url):
    global jwks_key_set
    # get jwks key set from Issuer endpoint
    response = requests.get(issuer_url + "/.well-known/jwks.json")
    jwks_key_set = response.json()

def get_jwks_key_with_kid(kid):
    global jwks_key_set

    # If we don't have keys at all, just return None
    if jwks_key_set == None:
        return None

    # Check if the key is in the set already
    for key in jwks_key_set["keys"]:
        if key["kid"] == kid:
            return key
    return None

def encrypt(payload, scope, custom_refresh_token_exp_value=None):
    global private_key, last_private_key_refresh
    # get time difference between current time and last_private_key_refresh
    current_time = int(time.time())
    time_difference = current_time - last_private_key_refresh
    print("Time difference between current time and last_private_key_refresh: ", time_difference)

    # check if we need to refresh the private key (every 15 minutes). We're ok to sign with the old key for a few minutes as long as the public key is in the jwks.json
    if time_difference > private_key_refresh_rate or private_key == None:
        print("Refreshing private key")
        refresh_private_key()

    # Create both an auth token and a refresh token for returning
    auth_token, auth_token_expires_in = encrypt_payload(payload, private_key, scope, "gamebackend", access_token_expiration, scope)

    # If we didn't receive a custom exp value, generate one based on our days config, otherwise use the custom one (that is based on previously created refresh token)
    refresh_token, refresh_token_expires_in = encrypt_payload(payload, private_key, "refresh", "refresh", refresh_token_expiration_days * 24 * 60 * 60, scope, custom_refresh_token_exp_value)
    
    # Return the tokens and their expiration in seconds
    return auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in

def encrypt_payload(payload, private_key, scope, audience, expiration_in_seconds, access_token_scope, custom_refresh_token_exp_value=None):

    # add exp to payload with current time + expiration time in seconds, EXCEPT if we have an existing exp time for a refresh token
    if custom_refresh_token_exp_value == None:
        payload["exp"] = int(time.time()) + expiration_in_seconds
    else:
        payload["exp"] = custom_refresh_token_exp_value

    # add iss to payload from environment variable ISSUER_URL
    payload["iss"] = os.environ['ISSUER_URL']

    # add the kid for validation
    key_dict = json.loads(private_key)
    payload["kid"] = key_dict["kid"]

    # We don't have defined what audience will receive the token, so we'll just set it to "gamebackend"
    payload["aud"] = audience

    # Not before field and issued field, we just use current time as the token is immediately usable
    payload["nbf"] = int(time.time())
    payload["iat"] = int(time.time())

    # Scope for the request
    payload["scope"] = scope

    # For refresh tokens, add the access token scope
    if audience == "refresh":
        payload["access_token_scope"] = access_token_scope

    # Get the RSA key that works with the PyJWT library
    signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(private_key)

    # Encode the payload with RS256
    try:
        encoded_token = jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": key_dict["kid"]})
    except Exception as e:
        # Encoding failed, return None
        print("Error",e)
        return None
    
    # Calculate the amount of seconds to expiration
    seconds_to_expiration = payload["exp"] - int(time.time())
    
    # Return the encoded token
    return encoded_token, seconds_to_expiration

def decrypt(encoded_payload):
    return decrypt_payload(encoded_payload, os.environ['ISSUER_URL'], "gamebackend")

def decrypt_refresh_token(encoded_payload):
    return decrypt_payload(encoded_payload, os.environ['ISSUER_URL'], "refresh")

# NOTE: This would actually be client side code, we won't have this in the auth module
def decrypt_payload(encoded_payload, issuer_url, audience):

    #print("Encoded payload: " + encoded_payload)
    decoded_non_verified = None
    try:
        # Decode without verification to get the jwk_uri first (not the final validation!)
        decoded_non_verified = jwt.decode(encoded_payload, options={"verify_signature": False}, audience=audience, algorithms=["RS256"])
    except Exception as e:
        # Decoding failed, return None
        print("Error decoding: ",e)
        return None

    # make sure issuers match
    iss = decoded_non_verified["iss"]
    print("iss: ", iss)
    if iss != issuer_url:
        print("Issuers don't match!")
        return None

    # If the kid is not in the jwks_key_set, we need to get it from the issuer
    if not is_kid_in_jwks_key_set(decoded_non_verified["kid"]):
        print("kid not in jwks_key_set, refresh the keys")
        refresh_jwks_key_set(issuer_url)

    # If we're still missing the key, just return None
    decryption_jwks_key = get_jwks_key_with_kid(decoded_non_verified["kid"])

    # If we still didn't get a key, return None
    if decryption_jwks_key == None:
        print("Error getting key")
        return None

    # Get the RSA key that works with the PyJWT library using the first key in the list
    decryption_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(decryption_jwks_key))

    # Decode the payload with RS256
    try:
        decoded_token = jwt.decode(encoded_payload, decryption_key, audience=audience, algorithms=["RS256"])
    except Exception as e:
        # Decoding failed, return None
        print("Error decoding",e)
        return None
    
    # Return the encoded token
    return decoded_token


