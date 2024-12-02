import json
import os
import time
import boto3
import requests
import jwt  # Using PyJWT to decode the token
from jwt.algorithms import RSAAlgorithm
from aws_lambda_powertools.utilities import parameters
from aws_lambda_powertools import Logger
from encryption_and_decryption import encrypt

# Initialize clients and logger
dynamodb = boto3.resource('dynamodb')
user_table = dynamodb.Table(os.environ['USER_TABLE_NAME'])
logger = Logger()

# Cognito configuration
CognitoIssuer = f"https://cognito-idp.eu-central-1.amazonaws.com/{os.environ['COGNITO_USER_POOL_ID']}"
jwks_url = f"{CognitoIssuer}/.well-known/jwks.json"
app_client_id = os.environ['COGNITO_APP_CLIENT_ID']

# Token expiration times
access_token_expiration = 900  # 15 minutes
refresh_token_expiration_days = 6  # 6 days

def lambda_handler(event, context):
    # Retrieve access token from the query string
    cognito_access_token = event['queryStringParameters'].get('access_token')
    backend_auth_token = event['queryStringParameters'].get('backend_auth_token')
    requested_linking = event['queryStringParameters'].get('link_existing_user') == 'true'

    if not cognito_access_token:
        return response(400, "Access token is missing from the request")

    # Verify the Cognito access token
    try:
        verified_claims = verify_jwt(cognito_access_token)
        cognito_user_id = verified_claims['sub']
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        return response(401, "Invalid or expired access token")

    # # Decode the Cognito access token to get the 'sub' claim without verifying the signature
    # try:
    #     decoded_token = jwt.decode(cognito_access_token, options={"verify_signature": False}, algorithms=["RS256"])
    #     cognito_user_id = decoded_token['sub']
    # except Exception as e:
    #     logger.error(f"Token decoding failed: {e}")
    #     return response(401, "Invalid access token")

    # Check if the user exists in DynamoDB
    user = get_user_from_dynamodb(cognito_user_id)

    # OPTION 1: Existing user found, return user data with a new JWT token
    if user:
        auth_token, refresh_token, auth_token_exp, refresh_token_exp = generate_tokens(user['UserId'])
        return response(200, {
            "message": "User exists",
            "auth_token": auth_token,
            "refresh_token": refresh_token,
            "auth_token_exp": auth_token_exp,
            "refresh_token_exp": refresh_token_exp
        })

    # OPTION 2: Requested linking to an existing user
    if requested_linking and backend_auth_token:
        linked_user = link_user_to_backend(backend_auth_token, cognito_user_id)
        if linked_user:
            auth_token, refresh_token, auth_token_exp, refresh_token_exp = generate_tokens(linked_user['UserId'])
            return response(200, {
                "message": "User linked successfully",
                "auth_token": auth_token,
                "refresh_token": refresh_token,
                "auth_token_exp": auth_token_exp,
                "refresh_token_exp": refresh_token_exp
            })
        else:
            return response(400, "Failed to link to an existing user")

    # OPTION 3: Create a new user in DynamoDB if not existing and no link requested
    new_user = create_new_user(cognito_user_id)
    auth_token, refresh_token, auth_token_exp, refresh_token_exp = generate_tokens(new_user['UserId'])
    return response(201, {
        "message": "New user created",
        "auth_token": auth_token,
        "refresh_token": refresh_token,
        "auth_token_exp": auth_token_exp,
        "refresh_token_exp": refresh_token_exp
    })

def verify_jwt(token):
    # Fetch the JWKS (JSON Web Key Set) from Cognito
    try:
        jwks = requests.get(jwks_url).json()
    except Exception as e:
        raise Exception(f"Error fetching JWKS: {e}")

    # Decode the JWT header to get the kid (Key ID)
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header['kid']
    
    # Find the public key in the JWKS with a matching kid
    rsa_key = {}
    for key in jwks['keys']:
        if key['kid'] == kid:
            rsa_key = {
                "kty": key['kty'],
                "kid": key['kid'],
                "use": key['use'],
                "n": key['n'],
                "e": key['e']
            }
            break

    if not rsa_key:
        raise Exception("Public key not found in JWKS")

    # Use the public key to verify the JWT
    try:
        public_key = RSAAlgorithm.from_jwk(json.dumps(rsa_key))
        print("Public Key:", public_key)
        payload = jwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            options={"require": ["exp", "iss", "sub"], "verify_aud": True, "verify_signature": True, "verify_issuer": True},
            # audience=app_client_id,
            issuer=CognitoIssuer
        )
        print("Payload:", payload)
        return payload
    except jwt.ExpiredSignatureError:
        raise Exception("Token is expired")
    except jwt.MissingRequiredClaimError:
        raise Exception("Token is missing required claims")
    except jwt.InvalidAudienceError:
        raise Exception("Token has invalid audience")
    except jwt.InvalidIssuerError:
        raise Exception("Token has invalid issuer")
    except jwt.InvalidTokenError:
        raise Exception("Token is invalid")
    except Exception as e:
        raise Exception(f"Token verification failed: {e}")

def get_user_from_dynamodb(cognito_user_id):
    try:
        response = user_table.get_item(Key={'cognito_user_id': cognito_user_id})
        return response.get('Item')
    except Exception as e:
        logger.error(f"Error fetching user from DynamoDB: {e}")
        return None

def link_user_to_backend(backend_auth_token, cognito_user_id):
    # Verify backend auth token and link to the existing Cognito user
    try:
        verified_backend_user = verify_backend_token(backend_auth_token)
        if verified_backend_user:
            # Update the user's cognito_user_id in DynamoDB
            user_table.update_item(
                Key={'UserId': verified_backend_user['UserId']},
                UpdateExpression="SET cognito_user_id = :cognito_user_id",
                ExpressionAttributeValues={':cognito_user_id': cognito_user_id}
            )
            return verified_backend_user
        return None
    except Exception as e:
        logger.error(f"Error linking user: {e}")
        return None

def create_new_user(cognito_user_id):
    new_user_id = str(time.time())  # Unique user ID based on current timestamp
    user = {
        'UserId': new_user_id,
        'cognito_user_id': cognito_user_id,
        'created_at': int(time.time())
    }
    user_table.put_item(Item=user)
    return user

def generate_tokens(user_id):
    payload = {"id": user_id}
    scope = "user_scope"  # Scope can be customized as needed
    auth_token, refresh_token, auth_token_exp, refresh_token_exp = encrypt(
        payload, scope, custom_refresh_token_exp_value=None
    )
    return auth_token, refresh_token, auth_token_exp, refresh_token_exp

def verify_backend_token(token):
    # Here you would implement the actual verification logic for the backend token
    # For example, call another service or verify a custom token
    pass

def response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps(message),
        "headers": {"Content-Type": "application/json"}
    }
