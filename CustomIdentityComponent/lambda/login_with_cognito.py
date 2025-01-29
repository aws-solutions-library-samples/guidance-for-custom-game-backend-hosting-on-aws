import json
import os
import uuid
import boto3
import requests
import jwt  # Using PyJWT to decode the token
from botocore.config import Config
from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
from aws_lambda_powertools import Metrics
from aws_lambda_powertools import single_metric
from aws_lambda_powertools.metrics import MetricUnit
from jwt.algorithms import RSAAlgorithm
from encryption_and_decryption import encrypt, decrypt

# Initialize clients and logger
tracer = Tracer()
logger = Logger()
metrics = Metrics()
metrics.set_default_dimensions(function=os.environ['AWS_LAMBDA_FUNCTION_NAME'])
config = Config(connect_timeout=2, read_timeout=2)
dynamodb = boto3.resource('dynamodb')
user_table = dynamodb.Table(os.environ['USER_TABLE'])
client = boto3.client('cognito-idp')

# Cognito configuration
CognitoIssuer = f"https://cognito-idp.eu-central-1.amazonaws.com/{os.environ['COGNITO_USER_POOL_ID']}"
jwks_url = f"{CognitoIssuer}/.well-known/jwks.json"
app_client_id = os.environ['COGNITO_APP_CLIENT_ID']

# Token expiration times
access_token_expiration = 900  # 15 minutes
refresh_token_expiration_days = 6  # 6 days

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

# Creates a new user when there's no existing user for the Cognito User ID
@tracer.capture_method
def create_user(cognito_user_id):

    # generate a unique id
    user_id = str(uuid.uuid4())

    # Check that user_id doesn't exist in DynamoDB table defined in environment variable USER_TABLE
    table = dynamodb.Table(os.environ['USER_TABLE'])
    # Try to write a new item to the table with user_id as partition key
    try:
        table.put_item(
            Item={
                'UserId': user_id,
                'CognitoId': cognito_user_id 
            },
            ConditionExpression='attribute_not_exists(UserId)'
        )
        return user_id
    except:
        logger.info("User already exists")
        return None
    
    metrics.add_metric(name="created_user", unit=MetricUnit.Count, value=1)

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
def generate_success(user_id, cognito_id, jwt_token, refresh_token, auth_token_expires_in, refresh_token_expires_in):
    return {
        'statusCode': 200,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
        'body': json.dumps({
            'user_id' : user_id,
            'cognito_id' : cognito_id,
            'auth_token' : jwt_token,
            'refresh_token' : refresh_token,
            'auth_token_expires_in' : auth_token_expires_in,
            'refresh_token_expires_in' : refresh_token_expires_in
        }),
        "isBase64Encoded": False
    }

# Tries to get an existing user from User Table. Reports error if request fails
@tracer.capture_method
def get_existing_user(cognito_id):
    try:
        cognito_user_table_name = os.getenv("COGNITO_USER_TABLE")
        cognito_user_table = dynamodb.Table(cognito_user_table_name)
        cognito_user_table_response = cognito_user_table.get_item(Key={'CognitoId': cognito_id})
        if 'Item' in cognito_user_table_response:
            logger.info("Found existing user in Cognito ID table:", user_id=cognito_user_table_response['Item']['UserId'])
            return True, cognito_user_table_response['Item']['UserId']
        else:
            return False, None
    except Exception as e:
        logger.info("Exception reading from user table: ", exception=e)
    
    return False, None

@tracer.capture_method
def add_new_user_to_cognito_table(user_id, cognito_id):
    try:
        cognito_id_user_table_name = os.getenv("COGNITO_USER_TABLE")
        cognito_id_user_table = dynamodb.Table(cognito_id_user_table_name)
        cognito_id_user_table.put_item(
        Item={
            'UserId': user_id,
            'CognitoId': cognito_id,
        })
        return True
    except Exception as e:
        logger.info("Exception adding user to Cognito ID table: ", e)

    metrics.add_metric(name="new_cognito_user", unit=MetricUnit.Count, value=1)
    
    return False

@tracer.capture_method
def link_cognito_id_to_existing_user(user_id, cognito_id):
    try:
        user_table_name = os.getenv("USER_TABLE")
        user_table = dynamodb.Table(user_table_name)
        # Update existing user
        user_table.update_item(
            Key={
                'UserId': user_id,
            },
            UpdateExpression="set CognitoId = :val1",
            ExpressionAttributeValues={
                ':val1': cognito_id
            },
            ConditionExpression='attribute_exists(UserId)'
        )
        return True
    except Exception as e:
        logger.info("Exception linking user to existing user: ", e)

    metrics.add_metric(name="linked_user", unit=MetricUnit.Count, value=1)
    
    return False

@tracer.capture_method
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

def response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps(message),
        "headers": {"Content-Type": "application/json"}
    }

@tracer.capture_method
def sign_in_to_cognito(username, password):
    try:
        response = client.initiate_auth(
            ClientId=app_client_id,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password
            }
        )
        return response
    except Exception as e:
        logger.error(f"Error during authentication: {e}")
        return None

@tracer.capture_method
def sign_up_to_cognito(username, password, email):
    try:
        response = client.sign_up(
            ClientId=app_client_id,
            Username=username,
            Password=password,
            UserAttributes=[
                {'Name': 'email', 'Value': email}
            ]
        )
        return response
    except Exception as e:
        logger.error(f"Error during sign up: {e}")
        return None
    
@tracer.capture_method
def confirm_sign_up(username, confirmation_code):
    try:
        response = client.confirm_sign_up(
            ClientId=app_client_id,
            Username=username,
            ConfirmationCode=confirmation_code
        )
        return response
    except Exception as e:
        logger.error(f"Error during confirmation: {e}")
        return None
    
@tracer.capture_method
def sign_out_from_cognito(access_token):
    print("Sign Out Function Envoked")
    try:
        response = client.global_sign_out(
            AccessToken=access_token
        )
        print("Sign Out Response:", response)
        return response 
    except Exception as e:
        logger.error(f"Error during sign out: {e}")
        return None
    
@tracer.capture_method
def forgot_password_cognito(username):
    try:
        response = client.forgot_password(
            ClientId=app_client_id,
            Username=username
        )
        return response
    except Exception as e:
        logger.error(f"Error during forgot password: {e}")
        return None

@tracer.capture_method
def confirm_forgot_password(username, confirmation_code, password):
    try:
        response = client.confirm_forgot_password(
            ClientId=app_client_id,
            Username=username,
            ConfirmationCode=confirmation_code,
            Password=password
        )
        return response
    except Exception as e:
        logger.error(f"Error during confirmation: {e}")
        return None

@metrics.log_metrics
@tracer.capture_lambda_handler
def lambda_handler(event, context):

    username = None
    password = None
    email = None
    signin = None
    signup = None
    signup_confirmation_code = None
    signout = None
    forgot_password = None
    reset_password = None
    reset_password_code = None
    access_token = None

    if 'username' in event['body']:
        username = event['body']['username']
    if 'password' in event['body']:
        password = event['body']['password']
    if 'email' in event['body']:
        email = event['body']['email']
    if 'signin' in event['body']:
        signin = event['body']['signin']
    if 'signup' in event['body']:
        signup = event['body']['signup']
    if 'signup_confirmation_code' in event['body']:
        signup_confirmation_code = event['body']['confirmation_code']
    if 'signout' in event['body']:
        signout = event['body']['signout']
    if 'forgot_password' in event['body']:
        forgot_password = event['body']['forgot_password']
    if 'reset_password' in event['body']:
        reset_password = event['body']['reset_password']
    if 'reset_password_code' in event['body']:  
        reset_password_code = event['body']['reset_password_code']
    if 'access_token' in event['body']:    
        access_token = event['body']['access_token']

    print("Event:", event)
    print("Username:", username)
    print("Password:", password)

    print("Email:", email)
    print("SignIn", signin)

    if signup == "True":
        print("Sign Up Function envoked")
        auth_result = sign_up_to_cognito(username, password, email)
        if auth_result is None:
            return response(400, "Cognito Sign Up failed")
        if 'UserConfirmed' in auth_result and auth_result['UserConfirmed'] is not False:
            return response(200, "Cognito Sign Up successful, please confirm your code")
        else:
            return response(200, "Cognito Sign Up successful")

    if signup_confirmation_code is not None:
        auth_result = confirm_sign_up(username, signup_confirmation_code)
        if auth_result is None:
            return response(400, "Cognito Confirmation failed")
        else:
            return response(200, "Cognito Confirmation successful")
        
    if forgot_password == "True":
        auth_result = forgot_password_cognito(username)
        if auth_result is None:
            return response(400, "Cognito Forgot Password failed")
        else:
            return response(200, "Cognito Forgot Password successful")
        
    if reset_password == "True":
        auth_result = confirm_forgot_password(username, reset_password_code, password)
        if auth_result is None:
            return response(400, "Cognito Reset Password failed")
        else:
            return response(200, "Cognito Reset Password successful")

    if signout == "True" and access_token is not None:
        auth_result = sign_out_from_cognito(access_token)
        if auth_result is None:
            return response(400, "Cognito Sign Out failed")
        else:
            return response(200, "Cognito Sign Out successful")

    # Retrieve access token from Cognito auth
    if signin == "True":
        print("Sign In Function envoked")
        auth_result = sign_in_to_cognito(username, password)
        print("Auth result", auth_result)
        cognito_access_token = auth_result.get('AuthenticationResult', {}).get('AccessToken')
        print("Cognito Access Token", cognito_access_token)

        if not cognito_access_token:
            return response(400, "Cognito Access Token is missing from the auth response")

        # Validate the Cognito auth token, and get Cognito user ID
        cognito_user_id = None
        try:
            verified_claims = verify_jwt(cognito_access_token)
            cognito_user_id = verified_claims['sub']
            print('cognito user id', cognito_user_id)
        except Exception as e:
            logger.error(f"Token verification failed: {e}")
            return response(401, "Invalid or expired access token")

        if cognito_user_id is not None:

            success = False # Indicates the whole process success (existing user or new)

            # OPTION 1: Try to get an existing user. This overrides any requests to link accounts
            print('Logging in existing user')
            existing_user_request_success, user_id = get_existing_user(cognito_user_id)
            print('User id:', user_id)
            print('Existing user request success:', existing_user_request_success)
            # If there was a problem getting existing user, abort as we don't want to create duplicate
            if existing_user_request_success is False:
                record_failure_metric(f'Failed the try getting existing user')
                # return generate_error('Error: Failed the try getting existing user')
            else:
                success = True # Successfully tried getting existing user, might still be None (not found)
            
            # If no existing user, we are either linking to one or creating a new one
            if user_id is None:
                if 'queryStringParameters' in event:
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
                        success = link_cognito_id_to_existing_user(user_id, cognito_user_id)
                        if success is False:
                            record_failure_metric(f'Failed to link new user to existing user')
                            return generate_error('Error: Failed to link new user to existing user')
                    
                # OPTION 3: Else If no user yet and we didn't request linking to an existing user, create one and add to user table
                else:
                    logger.info("No user yet, creating a new one")
                    tries = 0
                    while user_id is None and tries < 10:
                        # Try to create a new user
                        user_id = create_user(cognito_user_id)
                        tries += 1
                    success = True
                    if user_id is None:
                        record_failure_metric(f'Failed to create user')
                        return generate_error('Error: Failed to create user')
                # Add user to Cognito Id User table in both cases (linking and new user)
                user_creation_success = add_new_user_to_cognito_table(user_id, cognito_user_id)

        # Create a JWT payload and encrypt with authenticated scope
        if user_id is not None and success is True:
            print("USER ID IS NOT NONE AND SUCCESS IS TRUE")
            payload = {
                'sub': user_id,
            }
            # Create for scope "authenticated" so backend can differentiate from guest users if needed
            auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in = encrypt(payload, "authenticated")
            # NOTE: We might want to send back all attached identities from user table?
            record_success_metric()
            return generate_success(user_id, cognito_user_id, auth_token, refresh_token, auth_token_expires_in, refresh_token_expires_in)

    # Failed to return success, return final error
    record_failure_metric(f'Invalid request')
    return generate_error('Error: Failed to authenticate')