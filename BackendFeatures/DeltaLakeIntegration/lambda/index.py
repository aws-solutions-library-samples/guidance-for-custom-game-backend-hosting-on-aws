import os
import json
import boto3

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError

# Global variables
tracer = Tracer()
logger = Logger()
client = boto3.client("kinesis", region_name=os.environ["AWS_REGION"])

def error_response(message):
    return {
        "statusCode": 500,
        "body": json.dumps(message),
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Credentials': True
        },
    }

@tracer.capture_lambda_handler
def lambda_handler(event, context):
    logger.info(event)

    # We expect a successful JWT authorization to be successful
    user_id = None
    try:
        user_id = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        logger.info(f"user_id: {user_id}")
    except Exception as e:
        logger.error(f"Exception: {e}")
        return error_response("'user_id' not found in claims")
    
    # Confirm request body
    if "body" not in event:
        return error_response("Request data not found")
    
    # Put the message body into the Kinesis Stream
    payload = json.loads(event["body"])
    key=f"{payload['event_id']}"
    try:
        response = client.put_record(
            StreamName=os.environ["STREAM_NAME"],
            Data=f"{json.dumps(payload)}\n", # JSON lines format
            PartitionKey=key
        )
        logger.info(f"Kinesis Response: {response}")
    except ClientError as e:
        message = e.response["Error"]["Message"]
        logger.error(message)
        return error_response("Error putting the record into kinesis")

    return {
        "statusCode": 200,
        "body": json.dumps("Successfully added event"),
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Credentials": True
        }
    }