# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import json
import os
import boto3

from aws_lambda_powertools import Tracer
from aws_lambda_powertools import Logger
import time
tracer = Tracer()
logger = Logger()

@tracer.capture_lambda_handler
def lambda_handler(event, context):

    # Get the SNS message (we're expecting this function is not called by anything else)
    message = json.loads(event['Records'][0]['Sns']['Message'])
    #logger.info(message)

    # Get the matchmaking status from the SNS message
    matchmaking_status = message['detail']['type']
    logger.info(f"Matchmaking status: {matchmaking_status}")
    
    # We only store status that is useful for the client
    if (matchmaking_status == 'MatchmakingSucceeded' or matchmaking_status == 'PotentialMatchCreated' or matchmaking_status == 'MatchmakingSearching' or
        matchmaking_status == 'MatchmakingFailed' or matchmaking_status == 'MatchmakingTimedOut' or matchmaking_status == 'MatchmakingCancelled'):

        # Iterate through the tickets
        for ticket in message['detail']['tickets']:
            process_ticket(ticket, matchmaking_status, message['detail']['gameSessionInfo'])

    else:
         logger.info("Not storing this status to DynamoDB")

@tracer.capture_method
def process_ticket(ticket, matchmaking_status, gamesession_info):
        
        logger.info(f"Ticket: {ticket}")

        # Check if the ticket is already in DynamoDB
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(os.environ['MATCHMAKING_TICKETS_TABLE'])
        response = table.get_item(
            Key={
                'TicketID': ticket['ticketId']
            }
        )

        if 'Item' in response:
            logger.info("Found an existing ticket")

            if is_matchmaking_status_newer(matchmaking_status, response['Item']['MatchmakingStatus']):
                logger.info("Newer status found")
                # if matchmaking succeeded, we have all the info
                if matchmaking_status == 'MatchmakingSucceeded':
                     # NOTE: We KNOW there is EXACTLY one player ticket in each. If you're doing multiple players per ticket, you need to manage that properly!
                     write_ticket_to_dynamoDB(ticket['ticketId'], matchmaking_status, gamesession_info['players'][0]['playerSessionId'], gamesession_info['ipAddress'], gamesession_info['dnsName'], gamesession_info['port'])
                # else we just have the ticketId and the status
                else:
                    write_ticket_to_dynamoDB(ticket['ticketId'], matchmaking_status)
        else:
            logger.info("No ticket yet in the database, write the status")
            # if matchmaking succeeded, we have all the info
            if matchmaking_status == 'MatchmakingSucceeded':
                # NOTE: We KNOW there is EXACTLY one player ticket in each. If you're doing multiple players per ticket, you need to manage that properly!
                write_ticket_to_dynamoDB(ticket['ticketId'], matchmaking_status, gamesession_info['players'][0]['playerSessionId'], gamesession_info['ipAddress'], gamesession_info['dnsName'], gamesession_info['port'])
            # else we just have the ticketId and the status
            else:
                write_ticket_to_dynamoDB(ticket['ticketId'], matchmaking_status)
             
@tracer.capture_method
def write_ticket_to_dynamoDB(ticket_id, matchmaking_status, player_session_id = None, ipAddress = None, dnsName = None, port = None):
     
    # Write the ticket to DynamoDB
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['MATCHMAKING_TICKETS_TABLE'])

    # Define an epoch time 3 hours from now for automatically deleting old tickets
    epoch_time = int(time.time()) + 10800

    # We expect we have all the info if we have a succeeded matchmaking status
    if matchmaking_status == 'MatchmakingSucceeded':
        table.put_item(
            Item={
                'TicketID': ticket_id,
                'MatchmakingStatus': matchmaking_status,
                'PlayerSessionId': player_session_id,
                'IpAddress': ipAddress,
                'DnsName': dnsName,
                'Port': port,
                'ExpirationTime': epoch_time
            }
        )
    # Else we just have the ticketId and the status
    else:
        table.put_item(
            Item={
                'TicketID': ticket_id,
                'MatchmakingStatus': matchmaking_status,
                'ExpirationTime': epoch_time
            }
        )

def is_matchmaking_status_newer(new_status, old_status):
     
     # Succeeded, Cancelled, TimedOut and Failed arealways the final state
     if new_status == 'MatchmakingSucceeded' or new_status == 'MatchmakingFailed' or new_status == 'MatchmakingTimedOut' or new_status == 'MatchmakingCancelled':
         return True
     
     # Searching is always the oldest out of the ones we store
     if old_status == 'MatchmakingSearching':
          return True
     
     # Don't write searching if we already have a potential match created
     if new_status == 'MatchmakingSearching' and old_status == 'PotentialMatchCreated':
          return False
     
     # Don't write PotentialMatchCreated if we're already in one of the end states
     if new_status == 'PotentialMatchCreated' and (old_status == 'MatchmakingSucceeded' or old_status == 'MatchmakingFailed' or old_status == 'MatchmakingTimedOut' or old_status == 'MatchmakingCancelled'):
          return False

