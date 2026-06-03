#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
================================================================================
Clear DynamoDB Datastore for Load Testing
================================================================================

Efficiently clears data from DynamoDB tables used by the load testing system.

Auto-discovers API credentials from CloudFormation and SSM Parameter Store.
No need to specify API URL, API key, studio ID, or game ID manually!

Usage:
    # Clear only load test tables (using default AWS credentials)
    python3 clearDatastore.py --region us-west-2
    
    # Clear only load test tables (using specific profile)
    python3 clearDatastore.py --profile default --region us-west-2

    # Clear everything (all tables and logs)
    python3 clearDatastore.py --profile default --region us-west-2 --all

    # Clear leaderboard config table (auto-discovers credentials)
    python3 clearDatastore.py --profile default --region us-west-2 --lbconfig

    # Clear leaderboard stats table
    python3 clearDatastore.py --profile default --region us-west-2 --lbstats

    # Clear logs only
    python3 clearDatastore.py --profile default --region us-west-2 --logs

    # Dry run to see what would be deleted
    python3 clearDatastore.py --profile default --region us-west-2 --all --dry-run

Features:
    - Auto-discovers API URL from CloudFormation stack outputs
    - Auto-discovers API key, studio ID, and game ID from SSM Parameter Store
    - Uses delete leaderboard API to properly clean up both DynamoDB and MemoryDB
    - Clears test logs directory
    - Parallel batch deletion for maximum speed
    - Progress tracking with real-time updates
    - Dry-run mode to preview what will be deleted
    - Automatic retry with exponential backoff

================================================================================
"""

import argparse
import boto3
import sys
import time
import json
import requests
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.exceptions import ClientError

# Default table names
LOAD_TEST_TABLES = [
    'game-StatsLeaderboards-LoadTestDefinitions',
    'game-StatsLeaderboards-LoadTestPlayerState',
    'game-StatsLeaderboards-LoadTestBatchCoordination',
    'game-StatsLeaderboards-LoadTestMetrics'
]

LEADERBOARD_CONFIG_TABLE = 'game-statsleaderboards-dev-config'
LEADERBOARD_STATS_TABLE = 'game-statsleaderboards-dev-stats'

# Lambda function log group names for CloudWatch log clearing
# Note: These are the actual log group names, not Lambda function names
LAMBDA_LOG_GROUPS = [
    '/aws/lambda/game-statsleaderboards-dev-backend-authorizer',
    '/aws/lambda/game-statsleaderboards-dev-batch-store-stats',
    '/aws/lambda/game-statsleaderboards-dev-developer-registration',
    '/aws/lambda/game-statsleaderboards-dev-get-leaderboard-scores',
    '/aws/lambda/game-statsleaderboards-dev-get-player-lb-standing',
    '/aws/lambda/game-statsleaderboards-dev-get-player-stats',
    '/aws/lambda/game-statsleaderboards-dev-leaderboards-config',
    '/aws/lambda/game-statsleaderboards-dev-rebuild-leaderboard',
    '/aws/lambda/game-statsleaderboards-dev-registration-lambda',
    '/aws/lambda/game-statsleaderboards-dev-reset-leaderboard',
    '/aws/lambda/game-statsleaderboards-dev-store-stats',
    '/loadtest/stats-leaderboards'
]

# Batch deletion settings
BATCH_SIZE = 25  # DynamoDB batch write limit
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# Detect free-threaded Python (GIL disabled) for optimal parallelism
import sys
GIL_DISABLED = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()

if GIL_DISABLED:
    # Free-threaded Python: threads run truly in parallel across cores
    # For I/O-bound DynamoDB work, ~4-6x CPU cores is optimal
    # Too many threads causes context-switching overhead
    import os
    cpu_count = os.cpu_count() or 8
    MAX_WORKERS = cpu_count * 5
    PARALLEL_SCAN_SEGMENTS = cpu_count * 5
    BOOSTED_WORKERS = cpu_count * 6
    BOOSTED_SEGMENTS = cpu_count * 6
    print(f"🚀 Free-threaded Python detected (GIL disabled) — {cpu_count} CPUs, {BOOSTED_WORKERS} boosted workers")
else:
    # Standard Python: GIL limits thread parallelism
    MAX_WORKERS = 10
    PARALLEL_SCAN_SEGMENTS = 10
    BOOSTED_WORKERS = 100
    BOOSTED_SEGMENTS = 100

# Performance Notes:
# - Each scan segment runs in its own worker thread
# - Free-threaded: true parallel execution, scales with CPU cores
# - Standard GIL: threads interleave, limited by GIL + I/O wait
# - This avoids thread contention and DynamoDB throttling
# - Each segment processes ~10% of table independently


def discover_infrastructure(stack_name, aws_session, region):
    """
    Discover infrastructure from CloudFormation stack outputs.
    
    Args:
        stack_name: CloudFormation stack name
        aws_session: boto3 Session object
        region: AWS region
        
    Returns:
        dict: Dictionary of stack outputs
    """
    try:
        cfn = aws_session.client('cloudformation', region_name=region)
        response = cfn.describe_stacks(StackName=stack_name)
        
        if not response.get('Stacks'):
            return {}
        
        stack = response['Stacks'][0]
        
        # Extract outputs into dictionary
        outputs = {}
        for output in stack.get('Outputs', []):
            outputs[output['OutputKey']] = output['OutputValue']
        
        return outputs
    
    except ClientError as e:
        print(f"⚠ Warning: Could not discover infrastructure from CloudFormation: {e}")
        return {}


def get_ssm_parameter(aws_session, region, parameter_name):
    """
    Retrieve parameter from SSM Parameter Store.
    
    Args:
        aws_session: boto3 Session object
        region: AWS region
        parameter_name: Parameter name
        
    Returns:
        dict: Parsed JSON value, or None if not found
    """
    try:
        ssm = aws_session.client('ssm', region_name=region)
        response = ssm.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        
        # Parse the JSON value
        return json.loads(response['Parameter']['Value'])
    
    except ClientError:
        return None
    except (json.JSONDecodeError, KeyError):
        return None


def find_any_api_key(aws_session, region, ssm_prefix):
    """
    Find any API key in SSM Parameter Store (for authentication).
    
    Args:
        aws_session: boto3 Session object
        region: AWS region
        ssm_prefix: SSM parameter prefix
        
    Returns:
        tuple: (api_key, studio_id, game_id) or (None, None, None)
    """
    try:
        ssm = aws_session.client('ssm', region_name=region)
        response = ssm.get_parameters_by_path(
            Path=f"{ssm_prefix}/api-keys",
            Recursive=True,
            WithDecryption=True
        )
        
        if response.get('Parameters'):
            # Use the first parameter found
            param = response['Parameters'][0]
            metadata = json.loads(param['Value'])
            
            return (
                metadata.get('apiKey'),
                metadata.get('studioId'),
                metadata.get('gameId')
            )
        
        return (None, None, None)
    
    except (ClientError, json.JSONDecodeError, KeyError):
        return (None, None, None)


class LeaderboardAPIDeleter:
    """Deletes leaderboards using the API to ensure both DynamoDB and MemoryDB are cleaned."""
    
    def __init__(self, api_base_url, api_key, dry_run=False):
        """
        Initialize leaderboard API deleter.
        
        Args:
            api_base_url: Base URL for the API (e.g., https://xxx.execute-api.us-west-2.amazonaws.com/dev)
            api_key: API key for authentication
            dry_run: If True, only show what would be deleted
        """
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.dry_run = dry_run
    
    def get_all_leaderboards(self, game_id):
        """
        Get all leaderboards for a game.
        
        Args:
            game_id: Game ID
            
        Returns:
            list: List of leaderboard configurations
        """
        url = f"{self.api_base_url}/leaderboards/configs"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
        body = {
            'gameLeaderboardConfigRequest': {
                'gameID': game_id
            }
        }
        
        try:
            response = requests.post(url, headers=headers, json=body, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            leaderboard_response = data.get('gameLeaderboardConfigResponse', {})
            leaderboards = leaderboard_response.get('leaderboardConfigs', [])
            
            return leaderboards
        except requests.exceptions.RequestException as e:
            print(f"✗ Error fetching leaderboards: {e}")
            print(f"  URL: {url}")
            print(f"  Headers: {json.dumps({k: v[:20] + '...' if k in ['Authorization'] else v for k, v in headers.items()}, indent=4)}")
            print(f"  Request Body: {json.dumps(body, indent=4)}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response Status: {e.response.status_code}")
                print(f"  Response Headers: {json.dumps(dict(e.response.headers), indent=4)}")
                try:
                    print(f"  Response Body: {json.dumps(e.response.json(), indent=4)}")
                except:
                    print(f"  Response Body: {e.response.text}")
            return []
    
    def delete_leaderboard(self, leaderboard_name, retry_count=0):
        """
        Delete a single leaderboard using the API.
        
        Args:
            leaderboard_name: Name of the leaderboard to delete
            retry_count: Current retry attempt
            
        Returns:
            bool: True if successful, False otherwise
        """
        if self.dry_run:
            return True
        
        url = f"{self.api_base_url}/leaderboards/config/delete"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }
        body = {
            'gameLeaderboardConfigRequest': {
                'leaderboardName': leaderboard_name
            }
        }
        
        # Use DELETE method (POST doesn't work for this endpoint)
        try:
            print(f"\n  🔄 Attempting DELETE for: {leaderboard_name}")
            print(f"     Method: DELETE")
            print(f"     URL: {url}")
            
            response = requests.delete(url, headers=headers, json=body, timeout=30)
            response.raise_for_status()
            print(f"  ✅ Successfully deleted: {leaderboard_name}")
            return True
            
        except requests.exceptions.HTTPError as e:
            # Retry logic
            if retry_count < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** retry_count)
                time.sleep(delay)
                return self.delete_leaderboard(leaderboard_name, retry_count + 1)
            else:
                # Print detailed error information
                print(f"\n  ✗ Failed to delete {leaderboard_name} after {MAX_RETRIES + 1} attempts")
                print(f"    URL: {url}")
                print(f"    Headers: {json.dumps({k: v[:20] + '...' if k in ['Authorization'] else v for k, v in headers.items()}, indent=6)}")
                print(f"    Request Body: {json.dumps(body, indent=6)}")
                if hasattr(e, 'response') and e.response is not None:
                    print(f"    Response Status: {e.response.status_code}")
                    print(f"    Response Headers: {json.dumps(dict(e.response.headers), indent=6)}")
                    try:
                        print(f"    Response Body: {json.dumps(e.response.json(), indent=6)}")
                    except:
                        print(f"    Response Body: {e.response.text}")
                    
                    # Check if this is an authorization issue
                    if e.response.status_code == 403:
                        error_type = e.response.headers.get('x-amzn-ErrorType', '')
                        if 'IncompleteSignature' in error_type or 'Signature' in error_type:
                            print(f"\n    ⚠ AUTHORIZATION ERROR: The API endpoint appears to be rejecting the request.")
                            print(f"      This could be due to:")
                            print(f"      1. API Gateway resource policy requiring AWS_IAM")
                            print(f"      2. Lambda authorizer not being invoked")
                            print(f"      3. API key format issue")
                            print(f"      Please check the API Gateway configuration and Lambda authorizer logs.")
                return False
                
        except requests.exceptions.RequestException as e:
            if retry_count < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** retry_count)
                time.sleep(delay)
                return self.delete_leaderboard(leaderboard_name, retry_count + 1)
            else:
                print(f"\n  ✗ Failed to delete {leaderboard_name}: {e}")
                return False
    
    def delete_all_leaderboards(self, game_id):
        """
        Delete all leaderboards for a game.
        
        Args:
            game_id: Game ID
            
        Returns:
            dict: Statistics about the deletion including list of failed leaderboard names
        """
        print(f"\n{'='*80}")
        print(f"Deleting leaderboards via API for game: {game_id}")
        print(f"{'='*80}")
        
        # Get all leaderboards
        print("Fetching leaderboards...")
        leaderboards = self.get_all_leaderboards(game_id)
        
        if not leaderboards:
            print("✓ No leaderboards found")
            return {'status': 'empty', 'deleted': 0, 'failed': 0, 'failed_leaderboards': []}
        
        print(f"Found {len(leaderboards)} leaderboards to delete")
        
        if self.dry_run:
            print(f"\n[DRY RUN] Would delete {len(leaderboards)} leaderboards:")
            for lb in leaderboards:
                print(f"  • {lb.get('leaderboardName')}")
            return {'status': 'dry_run', 'deleted': len(leaderboards), 'failed': 0, 'failed_leaderboards': []}
        
        # Delete leaderboards in parallel
        print(f"\nDeleting {len(leaderboards)} leaderboards...")
        print(f"Using {MAX_WORKERS} parallel workers")
        
        deleted_count = 0
        failed_count = 0
        failed_leaderboards = []  # Track which leaderboards failed
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all deletion tasks
            future_to_lb = {
                executor.submit(self.delete_leaderboard, lb.get('leaderboardName')): lb
                for lb in leaderboards
            }
            
            # Process completed tasks
            for i, future in enumerate(as_completed(future_to_lb)):
                lb = future_to_lb[future]
                lb_name = lb.get('leaderboardName')
                try:
                    success = future.result()
                    if success:
                        deleted_count += 1
                    else:
                        failed_count += 1
                        failed_leaderboards.append(lb_name)
                    
                    # Progress update
                    progress = (i + 1) / len(leaderboards) * 100
                    print(f"  Progress: {progress:.1f}% ({deleted_count} deleted, {failed_count} failed)", 
                          end='\r', flush=True)
                    
                except Exception as e:
                    print(f"\n  ✗ Error deleting {lb_name}: {e}")
                    failed_count += 1
                    failed_leaderboards.append(lb_name)
        
        delete_time = time.time() - start_time
        
        print(f"\n\nDeletion complete!")
        print(f"  Deleted: {deleted_count} leaderboards")
        print(f"  Failed: {failed_count} leaderboards")
        if failed_leaderboards:
            print(f"  Failed leaderboard names:")
            for lb_name in failed_leaderboards:
                print(f"    • {lb_name}")
        print(f"  Time: {delete_time:.1f}s")
        
        return {
            'status': 'success',
            'deleted': deleted_count,
            'failed': failed_count,
            'failed_leaderboards': failed_leaderboards,
            'time': delete_time
        }


class TableCleaner:
    """Efficiently clears DynamoDB tables using parallel batch deletion."""
    
    def __init__(self, aws_session, region, dry_run=False):
        """
        Initialize table cleaner.
        
        Args:
            aws_session: boto3 Session object
            region: AWS region
            dry_run: If True, only show what would be deleted
        """
        self.session = aws_session
        self.region = region
        self.dry_run = dry_run
        
        # Configure connection pool to match worker count for maximum parallelism
        # Default boto3 pool is 10 connections — far too low for 100-200 workers
        from botocore.config import Config
        pool_size = BOOSTED_WORKERS + 10  # Match max workers + headroom
        ddb_config = Config(
            max_pool_connections=pool_size,
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
        self.dynamodb = aws_session.resource('dynamodb', region_name=region, config=ddb_config)
        self.client = aws_session.client('dynamodb', region_name=region, config=ddb_config)
    
    def initiate_boost(self, table_name, target_wcu=25000):
        """
        Initiate billing mode switch to provisioned (non-blocking).
        Respects account-level DynamoDB capacity limits.
        Returns True if initiated successfully, False otherwise.
        """
        try:
            billing, gsis = self._get_table_billing_and_gsis(table_name)
            
            if billing != 'PAY_PER_REQUEST':
                print(f"  ℹ Table is already in {billing} mode — boost capacity already available")
                return True
            
            # Check account limits and calculate safe per-unit capacity
            limits = self.client.describe_limits()
            acct_max_rcu = limits.get('AccountMaxReadCapacityUnits', 80000)
            acct_max_wcu = limits.get('AccountMaxWriteCapacityUnits', 80000)
            table_max_rcu = limits.get('TableMaxReadCapacityUnits', 40000)
            table_max_wcu = limits.get('TableMaxWriteCapacityUnits', 40000)
            
            num_units = 1 + len(gsis)  # table + GSIs
            headroom = 5000  # Reserve for other tables
            
            # Optimize: scan uses table RCU only, GSI RCU is unused during deletion.
            # GSIs need WCU because DynamoDB auto-deletes GSI entries when base items are deleted.
            gsi_rcu = 100  # Safety buffer — GSIs aren't read during deletion but may serve live traffic
            table_rcu = min(acct_max_rcu - headroom - (gsi_rcu * len(gsis)), table_max_rcu)
            per_unit_wcu = min((acct_max_wcu - headroom) // num_units, table_max_wcu, target_wcu)
            
            total_rcu = table_rcu + (gsi_rcu * len(gsis))
            total_wcu = per_unit_wcu * num_units
            
            print(f"  Account limits: RCU={acct_max_rcu:,}, WCU={acct_max_wcu:,}")
            print(f"  Table: RCU={table_rcu:,} (scan), WCU={per_unit_wcu:,} (delete)")
            print(f"  GSIs ({len(gsis)}): RCU={gsi_rcu} each (unused), WCU={per_unit_wcu:,} each (auto-delete)")
            print(f"  Total: RCU={total_rcu:,}, WCU={total_wcu:,}")
            print(f"  🚀 Switching to PROVISIONED mode...")
            
            gsi_updates = []
            for gsi in gsis:
                gsi_updates.append({
                    'Update': {
                        'IndexName': gsi['name'],
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': gsi_rcu,
                            'WriteCapacityUnits': per_unit_wcu
                        }
                    }
                })
            
            update_kwargs = {
                'TableName': table_name,
                'BillingMode': 'PROVISIONED',
                'ProvisionedThroughput': {
                    'ReadCapacityUnits': table_rcu,
                    'WriteCapacityUnits': per_unit_wcu
                }
            }
            if gsi_updates:
                update_kwargs['GlobalSecondaryIndexUpdates'] = gsi_updates
            
            self.client.update_table(**update_kwargs)
            print(f"  ✓ Billing mode change initiated (non-blocking)")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if 'LimitExceededException' in error_code:
                print(f"  ⚠ Account capacity limit exceeded: {e}")
                print(f"  ⚠ Try a lower value with --boost-wcu")
            else:
                print(f"  ⚠ Failed to initiate boost: {e}")
            return False
        except Exception as e:
            print(f"  ⚠ Failed to initiate boost: {e}")
            return False
    
    def _get_table_billing_and_gsis(self, table_name):
        """Get current billing mode and GSI details for a table."""
        response = self.client.describe_table(TableName=table_name)
        table_desc = response['Table']
        billing = table_desc.get('BillingModeSummary', {}).get('BillingMode', 'PAY_PER_REQUEST')
        gsis = []
        for gsi in table_desc.get('GlobalSecondaryIndexes', []):
            gsis.append({
                'name': gsi['IndexName'],
                'status': gsi['IndexStatus']
            })
        return billing, gsis
    
    def _wait_for_table_ready(self, table_name, timeout=600):
        """Wait for table and all GSIs to be ACTIVE."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                response = self.client.describe_table(TableName=table_name)
                table_desc = response['Table']
                table_status = table_desc['TableStatus']
                
                gsi_statuses = []
                for gsi in table_desc.get('GlobalSecondaryIndexes', []):
                    gsi_statuses.append((gsi['IndexName'], gsi['IndexStatus']))
                
                all_active = table_status == 'ACTIVE' and all(s == 'ACTIVE' for _, s in gsi_statuses)
                
                if all_active:
                    return True
                
                # Show progress
                gsi_info = ', '.join(f"{n}={s}" for n, s in gsi_statuses) if gsi_statuses else 'none'
                elapsed = int(time.time() - start)
                print(f"  ⏳ Waiting... Table={table_status}, GSIs: [{gsi_info}] ({elapsed}s elapsed)", end='\r', flush=True)
                time.sleep(10)
                
            except Exception as e:
                print(f"\n  ⚠ Error checking table status: {e}")
                time.sleep(5)
        
        print(f"\n  ⚠ Timeout waiting for table to be ready after {timeout}s")
        return False
    
    def _boost_table_capacity(self, table_name, target_wcu=25000):
        """
        Switch table from on-demand to provisioned with high WCU for fast deletion.
        Respects account-level DynamoDB capacity limits.
        
        Returns:
            str: Original billing mode ('PAY_PER_REQUEST' or 'PROVISIONED'), or None on failure
        """
        try:
            original_billing, gsis = self._get_table_billing_and_gsis(table_name)
            
            if original_billing != 'PAY_PER_REQUEST':
                print(f"  ℹ Table is already in {original_billing} mode, skipping boost")
                return original_billing
            
            # Check account limits
            limits = self.client.describe_limits()
            acct_max_rcu = limits.get('AccountMaxReadCapacityUnits', 80000)
            acct_max_wcu = limits.get('AccountMaxWriteCapacityUnits', 80000)
            table_max_rcu = limits.get('TableMaxReadCapacityUnits', 40000)
            table_max_wcu = limits.get('TableMaxWriteCapacityUnits', 40000)
            
            num_units = 1 + len(gsis)
            headroom = 5000
            
            # Optimize: scan uses table RCU only, GSI RCU is unused during deletion.
            gsi_rcu = 100  # Safety buffer for any concurrent GSI reads
            table_rcu = min(acct_max_rcu - headroom - (gsi_rcu * len(gsis)), table_max_rcu)
            per_unit_wcu = min((acct_max_wcu - headroom) // num_units, table_max_wcu, target_wcu)
            
            total_rcu = table_rcu + (gsi_rcu * len(gsis))
            total_wcu = per_unit_wcu * num_units
            
            print(f"  Account limits: RCU={acct_max_rcu:,}, WCU={acct_max_wcu:,}")
            print(f"  Table: RCU={table_rcu:,} (scan), WCU={per_unit_wcu:,} (delete)")
            print(f"  GSIs ({len(gsis)}): RCU={gsi_rcu} each (unused), WCU={per_unit_wcu:,} each (auto-delete)")
            print(f"  Total: RCU={total_rcu:,}, WCU={total_wcu:,}")
            print(f"  🚀 Switching to PROVISIONED mode...")
            
            # Build GSI updates
            gsi_updates = []
            for gsi in gsis:
                gsi_updates.append({
                    'Update': {
                        'IndexName': gsi['name'],
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': gsi_rcu,
                            'WriteCapacityUnits': per_unit_wcu
                        }
                    }
                })
            
            update_kwargs = {
                'TableName': table_name,
                'BillingMode': 'PROVISIONED',
                'ProvisionedThroughput': {
                    'ReadCapacityUnits': table_rcu,
                    'WriteCapacityUnits': per_unit_wcu
                }
            }
            if gsi_updates:
                update_kwargs['GlobalSecondaryIndexUpdates'] = gsi_updates
            
            self.client.update_table(**update_kwargs)
            print(f"  ✓ Billing mode change initiated")
            
            # Wait for table and GSIs to be ready
            print(f"  ⏳ Waiting for table and {len(gsis)} GSI(s) to be ready...")
            if self._wait_for_table_ready(table_name):
                print(f"\n  ✓ Table ready with {per_unit_wcu:,} WCU provisioned")
            else:
                print(f"\n  ⚠ Table may not be fully ready, proceeding anyway")
            
            return original_billing
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if 'LimitExceededException' in error_code:
                print(f"  ⚠ Account WCU limit exceeded. Try a lower value with --boost-wcu")
            else:
                print(f"  ⚠ Failed to boost capacity: {e}")
            return None
        except Exception as e:
            print(f"  ⚠ Failed to boost capacity: {e}")
            return None
    
    def _restore_table_capacity(self, table_name, original_billing):
        """Switch table back to its original billing mode and verify restoration."""
        try:
            if original_billing != 'PAY_PER_REQUEST':
                return  # Nothing to restore
            
            print(f"\n  🔄 Restoring table to on-demand (PAY_PER_REQUEST) mode...")
            self.client.update_table(
                TableName=table_name,
                BillingMode='PAY_PER_REQUEST'
            )
            print(f"  ✓ Billing mode change initiated")
            
            print(f"  ⏳ Waiting for table and GSIs to be ready...")
            if self._wait_for_table_ready(table_name):
                # Verify final state
                final_billing, _ = self._get_table_billing_and_gsis(table_name)
                if final_billing == 'PAY_PER_REQUEST':
                    print(f"\n  ✅ Table verified: restored to on-demand (PAY_PER_REQUEST) mode")
                else:
                    print(f"\n  ⚠ Table billing is {final_billing}, expected PAY_PER_REQUEST — check manually")
            else:
                print(f"\n  ⚠ Table may not be fully ready — check manually")
                
        except Exception as e:
            print(f"  ⚠ Failed to restore billing mode: {e}")
            print(f"  ⚠ IMPORTANT: Table '{table_name}' may still be in PROVISIONED mode!")
            print(f"  ⚠ Run manually: aws dynamodb update-table --table-name {table_name} --billing-mode PAY_PER_REQUEST")
    
    def get_table_info(self, table_name):
        """
        Get table information including key schema.
        
        Args:
            table_name: Name of the table
            
        Returns:
            dict: Table info with key_names and item_count, or None if table doesn't exist
        """
        try:
            response = self.client.describe_table(TableName=table_name)
            table_desc = response['Table']
            
            # Extract key attribute names
            key_names = []
            for key in table_desc['KeySchema']:
                key_names.append(key['AttributeName'])
            
            item_count = table_desc.get('ItemCount', 0)
            
            return {
                'key_names': key_names,
                'item_count': item_count,
                'status': table_desc['TableStatus']
            }
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                return None
            raise
    
    def scan_and_delete_segment(self, table_name, key_names, segment, total_segments, exclude_leaderboards=None, progress_callback=None):
        """
        Scan a single segment of the table and delete items in streaming fashion.
        
        This enables parallel scanning and immediate deletion without storing all keys in memory.
        Uses low-level client API for better parallelism.
        
        Args:
            table_name: Name of the table
            key_names: List of key attribute names
            segment: Segment number (0-based)
            total_segments: Total number of segments
            exclude_leaderboards: List of leaderboard names to exclude from deletion (optional)
            progress_callback: Optional callback function(deleted, excluded, failed) for real-time progress
            
        Returns:
            dict: Statistics about this segment's deletion
        """
        scan_kwargs = {
            'TableName': table_name,
            'ProjectionExpression': ','.join(key_names),
            'ConsistentRead': False,  # Eventually consistent for better performance
            'Segment': segment,
            'TotalSegments': total_segments
        }
        
        # If we need to filter, we need to get the full item
        if exclude_leaderboards and table_name == LEADERBOARD_CONFIG_TABLE:
            # Need to fetch leaderboardName for filtering
            scan_kwargs['ProjectionExpression'] = ','.join(key_names + ['leaderboardName'])
        
        segment_deleted = 0
        segment_excluded = 0
        segment_failed = 0
        batch_buffer = []
        
        try:
            while True:
                # Use low-level client API with retry on throttling
                scan_attempts = 0
                max_scan_retries = 10
                while True:
                    try:
                        t0 = time.time()
                        response = self.client.scan(**scan_kwargs)
                        scan_ms = (time.time() - t0) * 1000
                        break  # Success
                    except ClientError as e:
                        if e.response['Error']['Code'] == 'ProvisionedThroughputExceededException' and scan_attempts < max_scan_retries:
                            scan_attempts += 1
                            delay = min(2 ** scan_attempts, 30)  # Exponential backoff, max 30s
                            time.sleep(delay)
                        else:
                            raise
                
                for item in response.get('Items', []):
                    # Filter out excluded leaderboards if specified
                    if exclude_leaderboards and table_name == LEADERBOARD_CONFIG_TABLE:
                        lb_name_attr = item.get('leaderboardName')
                        if lb_name_attr:
                            # DynamoDB returns typed values like {'S': 'value'}
                            lb_name = lb_name_attr.get('S') if isinstance(lb_name_attr, dict) else lb_name_attr
                            if lb_name in exclude_leaderboards:
                                segment_excluded += 1
                                continue
                    
                    # Extract only the key attributes for deletion
                    # Keep in DynamoDB typed format (client.scan returns typed, batch_write_item expects typed)
                    key_item = {k: item[k] for k in key_names if k in item}
                    batch_buffer.append(key_item)
                    
                    # Delete batch when buffer is full
                    if len(batch_buffer) >= BATCH_SIZE:
                        if not self.dry_run:
                            t1 = time.time()
                            success, failed = self.delete_batch(table_name, batch_buffer)
                            del_ms = (time.time() - t1) * 1000
                            segment_deleted += success
                            segment_failed += len(failed)
                        else:
                            segment_deleted += len(batch_buffer)
                            del_ms = 0
                        
                        # Report progress after each batch deletion (non-blocking)
                        if progress_callback:
                            progress_callback(success if not self.dry_run else len(batch_buffer), 0, len(failed) if not self.dry_run else 0, scan_ms=scan_ms, delete_ms=del_ms)
                        
                        batch_buffer = []
                
                # Check if there are more items to scan
                if 'LastEvaluatedKey' not in response:
                    break
                
                scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            
            # Delete remaining items in buffer
            if batch_buffer:
                if not self.dry_run:
                    success, failed = self.delete_batch(table_name, batch_buffer)
                    segment_deleted += success
                    segment_failed += len(failed)
                else:
                    segment_deleted += len(batch_buffer)
                
                # Report final progress
                if progress_callback:
                    progress_callback(success if not self.dry_run else len(batch_buffer), 0, len(failed) if not self.dry_run else 0)
            
            return {
                'deleted': segment_deleted,
                'excluded': segment_excluded,
                'failed': segment_failed
            }
        
        except Exception as e:
            print(f"\n  ✗ Error in segment {segment}: {e}")
            return {
                'deleted': segment_deleted,
                'excluded': segment_excluded,
                'failed': segment_failed + len(batch_buffer)
            }
    
    def scan_table_keys(self, table_name, key_names):
        """
        DEPRECATED: Use scan_and_delete_segment for better performance.
        
        Scan table and return all item keys.
        
        Args:
            table_name: Name of the table
            key_names: List of key attribute names
            
        Yields:
            dict: Item key
        """
        table = self.dynamodb.Table(table_name)
        
        scan_kwargs = {
            'ProjectionExpression': ','.join(key_names),
            'ConsistentRead': False  # Eventually consistent for better performance
        }
        
        items_scanned = 0
        
        while True:
            response = table.scan(**scan_kwargs)
            
            for item in response.get('Items', []):
                items_scanned += 1
                if items_scanned % 1000 == 0:
                    print(f"  Scanned {items_scanned} items...", end='\r', flush=True)
                yield item
            
            # Check if there are more items to scan
            if 'LastEvaluatedKey' not in response:
                break
            
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        
        if items_scanned > 0:
            print(f"  Scanned {items_scanned} items... Done!     ")
    
    def delete_batch(self, table_name, items, retry_count=0):
        """
        Delete a batch of items from table using raw batch_write_item API.
        
        This uses the low-level client API instead of batch_writer() for better
        parallelism and less blocking.
        
        Args:
            table_name: Name of the table
            items: List of item keys to delete
            retry_count: Current retry attempt
            
        Returns:
            tuple: (success_count, failed_items)
        """
        if not items:
            return 0, []
        
        if self.dry_run:
            return len(items), []
        
        try:
            # Use low-level client API for true async behavior
            request_items = {
                table_name: [
                    {'DeleteRequest': {'Key': item}}
                    for item in items
                ]
            }
            
            response = self.client.batch_write_item(RequestItems=request_items)
            
            # Check for unprocessed items
            unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])
            
            if unprocessed and retry_count < MAX_RETRIES:
                # Retry unprocessed items with exponential backoff
                delay = RETRY_DELAY * (2 ** retry_count)
                time.sleep(delay)
                
                # Extract keys from unprocessed items
                unprocessed_keys = [item['DeleteRequest']['Key'] for item in unprocessed]
                retry_success, retry_failed = self.delete_batch(table_name, unprocessed_keys, retry_count + 1)
                
                success_count = len(items) - len(unprocessed) + retry_success
                return success_count, retry_failed
            
            elif unprocessed:
                # Max retries exceeded
                unprocessed_keys = [item['DeleteRequest']['Key'] for item in unprocessed]
                return len(items) - len(unprocessed), unprocessed_keys
            
            else:
                # All items processed successfully
                return len(items), []
            
        except ClientError as e:
            if retry_count < MAX_RETRIES:
                # Exponential backoff
                delay = RETRY_DELAY * (2 ** retry_count)
                time.sleep(delay)
                return self.delete_batch(table_name, items, retry_count + 1)
            else:
                # Max retries exceeded, return failed items
                return 0, items
    
    def clear_table(self, table_name, exclude_leaderboards=None, segments=None, workers=None):
        """
        Clear all data from a table using parallel scan and streaming delete.
        
        This method uses DynamoDB parallel scan to divide the table into segments,
        then scans and deletes each segment in parallel for maximum performance.
        
        Args:
            table_name: Name of the table to clear
            exclude_leaderboards: List of leaderboard names to exclude from deletion (optional)
            
        Returns:
            dict: Statistics about the deletion
        """
        print(f"\n{'='*80}")
        print(f"Clearing table: {table_name}")
        print(f"{'='*80}")
        
        # Get table info
        table_info = self.get_table_info(table_name)
        
        if table_info is None:
            print(f"⚠ Table '{table_name}' does not exist. Skipping.")
            return {'status': 'not_found', 'deleted': 0, 'failed': 0}
        
        if table_info['status'] != 'ACTIVE':
            print(f"⚠ Table '{table_name}' is not ACTIVE (status: {table_info['status']}). Skipping.")
            return {'status': 'not_active', 'deleted': 0, 'failed': 0}
        
        key_names = table_info['key_names']
        estimated_count = table_info['item_count']
        
        print(f"Table status: {table_info['status']}")
        print(f"Key schema: {', '.join(key_names)}")
        print(f"Estimated items: {estimated_count:,}")
        
        if exclude_leaderboards:
            print(f"⚠ Excluding {len(exclude_leaderboards)} leaderboards from deletion:")
            for lb_name in exclude_leaderboards[:5]:  # Show first 5
                print(f"  • {lb_name}")
            if len(exclude_leaderboards) > 5:
                print(f"  ... and {len(exclude_leaderboards) - 5} more")
        
        if self.dry_run:
            print(f"\n[DRY RUN] Would delete all items from {table_name}")
            if exclude_leaderboards:
                print(f"[DRY RUN] Would exclude {len(exclude_leaderboards)} leaderboards")
            return {'status': 'dry_run', 'deleted': estimated_count, 'failed': 0}
        
        if estimated_count == 0:
            # DynamoDB ItemCount is approximate (updated every ~6 hours)
            # Do a quick scan to verify the table is truly empty
            verify = self.client.scan(TableName=table_name, Limit=1, Select='COUNT')
            if verify['Count'] == 0:
                print("✓ Table is already empty")
                return {'status': 'empty', 'deleted': 0, 'failed': 0}
            else:
                print(f"  ℹ ItemCount shows 0 but table has data — proceeding with deletion")
        
        # Use parallel scan with streaming delete
        num_segments = segments or PARALLEL_SCAN_SEGMENTS
        num_workers = workers or MAX_WORKERS
        
        print(f"\nUsing parallel scan with {num_segments} segments")
        print(f"Each segment scans and deletes in streaming fashion (no memory buffering)")
        print(f"Using {num_workers} parallel workers (1 worker per segment for optimal performance)")
        print(f"Batch size: {BATCH_SIZE} items per DynamoDB BatchWriteItem call")
        print()  # Extra line for progress updates
        
        start_time = time.time()
        
        # Thread-safe counters for real-time progress
        import threading
        progress_lock = threading.Lock()
        progress_data = {
            'deleted': 0,
            'excluded': 0,
            'failed': 0,
            'segments_completed': 0,
            'last_update': time.time()
        }
        
        # Thread-safe latency tracking
        latency_data = {
            'scan_total_ms': 0, 'scan_count': 0,
            'delete_total_ms': 0, 'delete_count': 0
        }
        
        def progress_callback(deleted, excluded, failed, scan_ms=0, delete_ms=0):
            """Thread-safe progress update callback - non-blocking"""
            # Use try-lock to avoid blocking threads on progress updates
            acquired = progress_lock.acquire(blocking=False)
            if not acquired:
                return  # Skip this update if lock is busy
            
            try:
                progress_data['deleted'] += deleted
                progress_data['excluded'] += excluded
                progress_data['failed'] += failed
                if scan_ms > 0:
                    latency_data['scan_total_ms'] += scan_ms
                    latency_data['scan_count'] += 1
                if delete_ms > 0:
                    latency_data['delete_total_ms'] += delete_ms
                    latency_data['delete_count'] += 1
                
                # Update display every 1 second to reduce lock contention
                now = time.time()
                if now - progress_data['last_update'] >= 1.0:
                    progress_data['last_update'] = now
                    elapsed = now - start_time
                    rate = progress_data['deleted'] / elapsed if elapsed > 0 else 0
                    
                    # Calculate estimated completion
                    if rate > 0 and estimated_count > 0:
                        remaining = estimated_count - progress_data['deleted']
                        eta_seconds = remaining / rate
                        eta_str = f"ETA: {int(eta_seconds)}s"
                    else:
                        eta_str = "ETA: calculating..."
                    
                    # Latency stats
                    avg_scan = (latency_data['scan_total_ms'] / latency_data['scan_count']) if latency_data['scan_count'] > 0 else 0
                    avg_del = (latency_data['delete_total_ms'] / latency_data['delete_count']) if latency_data['delete_count'] > 0 else 0
                    
                    print(f"  Deleted: {progress_data['deleted']:,} items | "
                          f"Rate: {rate:.0f} items/s | "
                          f"Scan: {avg_scan:.0f}ms | Del: {avg_del:.0f}ms | "
                          f"Segments: {progress_data['segments_completed']}/{num_segments} | "
                          f"{eta_str}     ",
                          end='\r', flush=True)
            finally:
                progress_lock.release()
        
        # Process segments in parallel
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Submit all segment scan-and-delete tasks
            future_to_segment = {
                executor.submit(
                    self.scan_and_delete_segment,
                    table_name,
                    key_names,
                    segment,
                    num_segments,
                    exclude_leaderboards,
                    progress_callback  # Pass callback for real-time updates
                ): segment
                for segment in range(num_segments)
            }
            
            # Process completed segments
            for future in as_completed(future_to_segment):
                segment = future_to_segment[future]
                try:
                    result = future.result()
                    
                    # Update final counts from segment
                    with progress_lock:
                        progress_data['segments_completed'] += 1
                        # Segment results are already included via callbacks, just track completion
                    
                except Exception as e:
                    print(f"\n  ✗ Error processing segment {segment}: {e}")
                    with progress_lock:
                        progress_data['failed'] += 1
                        progress_data['segments_completed'] += 1
        
        total_time = time.time() - start_time
        
        # Final counts
        total_deleted = progress_data['deleted']
        total_excluded = progress_data['excluded']
        total_failed = progress_data['failed']
        
        print(f"\n\nDeletion complete!")
        print(f"  Deleted: {total_deleted:,} items")
        if total_excluded > 0:
            print(f"  Excluded: {total_excluded:,} items (failed API deletions)")
        print(f"  Failed: {total_failed:,} items")
        print(f"  Total time: {total_time:.1f}s")
        if total_deleted > 0:
            print(f"  Throughput: {total_deleted / total_time:.0f} items/sec")
        
        return {
            'status': 'success',
            'deleted': total_deleted,
            'failed': total_failed,
            'excluded': total_excluded,
            'time': total_time
        }


class CloudWatchLogCleaner:
    """Clears CloudWatch log streams for Lambda functions."""
    
    def __init__(self, aws_session, region, dry_run=False):
        """
        Initialize CloudWatch log cleaner.
        
        Args:
            aws_session: boto3 Session object
            region: AWS region
            dry_run: If True, only show what would be deleted
        """
        self.session = aws_session
        self.region = region
        self.dry_run = dry_run
        self.logs_client = aws_session.client('logs', region_name=region)
    
    def clear_lambda_logs(self, log_group_name):
        """
        Clear all logs for a log group by deleting and recreating it.
        
        This ensures the log group exists (empty) for CloudWatch viewers,
        avoiding "ResourceNotFoundException" errors in the UI.
        
        Args:
            log_group_name: Full log group name (e.g., '/aws/lambda/function-name')
            
        Returns:
            dict: Statistics about the deletion
        """
        print(f"\n{'='*80}")
        print(f"Clearing CloudWatch logs: {log_group_name}")
        print(f"{'='*80}")
        
        try:
            # Check if log group exists
            try:
                response = self.logs_client.describe_log_groups(
                    logGroupNamePrefix=log_group_name,
                    limit=1
                )
                # Check if the specific log group exists in the response
                log_groups = response.get('logGroups', [])
                if not log_groups or not any(lg['logGroupName'] == log_group_name for lg in log_groups):
                    print(f"⚠ Log group does not exist. Creating empty log group...")
                    if not self.dry_run:
                        self.logs_client.create_log_group(logGroupName=log_group_name)
                        print(f"✓ Empty log group created")
                    return {'status': 'created', 'deleted': 0, 'failed': 0}
                
                # Get stream count for reporting
                stream_count = 0
                try:
                    paginator = self.logs_client.get_paginator('describe_log_streams')
                    for page in paginator.paginate(logGroupName=log_group_name):
                        stream_count += len(page.get('logStreams', []))
                except:
                    stream_count = 0  # Ignore errors counting streams
                
                print(f"Log group contains ~{stream_count} log streams")
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    print(f"⚠ Log group does not exist. Creating empty log group...")
                    if not self.dry_run:
                        self.logs_client.create_log_group(logGroupName=log_group_name)
                        print(f"✓ Empty log group created")
                    return {'status': 'created', 'deleted': 0, 'failed': 0}
                raise
            
            if self.dry_run:
                print(f"[DRY RUN] Would delete entire log group (much faster than deleting {stream_count} streams individually)")
                print(f"[DRY RUN] Would recreate empty log group to avoid UI errors")
                return {'status': 'dry_run', 'deleted': stream_count, 'failed': 0}
            
            # Delete the entire log group (single API call!)
            try:
                print(f"Deleting entire log group...")
                self.logs_client.delete_log_group(logGroupName=log_group_name)
                print(f"✓ Log group deleted successfully")
                
                # Recreate empty log group to avoid "ResourceNotFoundException" in CloudWatch viewers
                print(f"Recreating empty log group...")
                self.logs_client.create_log_group(logGroupName=log_group_name)
                print(f"✓ Empty log group created")
                
                return {
                    'status': 'success',
                    'deleted': stream_count,  # Report estimated streams deleted
                    'failed': 0
                }
            
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    print(f"⚠ Log group does not exist (may have been deleted). Creating empty log group...")
                    self.logs_client.create_log_group(logGroupName=log_group_name)
                    print(f"✓ Empty log group created")
                    return {'status': 'created', 'deleted': 0, 'failed': 0}
                raise
        
        except ClientError as e:
            print(f"✗ Error clearing logs: {e}")
            return {'status': 'error', 'deleted': 0, 'failed': 1}
    
    def clear_all_lambda_logs(self, log_group_names):
        """
        Clear logs for multiple log groups in parallel.
        
        Args:
            log_group_names: List of log group names (full paths)
            
        Returns:
            dict: Results for each log group
        """
        print(f"\n{'='*80}")
        print(f"Clearing {len(log_group_names)} CloudWatch log groups in parallel")
        print(f"Using {MAX_WORKERS} parallel workers")
        print(f"{'='*80}")
        
        results = {}
        
        # Delete log groups in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all deletion tasks
            future_to_group = {
                executor.submit(self.clear_lambda_logs, log_group): log_group
                for log_group in log_group_names
            }
            
            # Process completed tasks
            for i, future in enumerate(as_completed(future_to_group)):
                log_group = future_to_group[future]
                try:
                    results[log_group] = future.result()
                    
                    # Progress update
                    progress = (i + 1) / len(log_group_names) * 100
                    completed = sum(1 for r in results.values() if r['status'] in ['success', 'not_found', 'empty'])
                    print(f"\nOverall progress: {progress:.1f}% ({completed}/{len(log_group_names)} log groups processed)")
                    
                except Exception as e:
                    print(f"\n✗ Error processing {log_group}: {e}")
                    results[log_group] = {'status': 'error', 'deleted': 0, 'failed': 1}
        
        return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Clear DynamoDB tables used by load testing system',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Clear only load test tables (using default AWS credentials)
  python3 clearDatastore.py --region us-west-2
  
  # Clear only load test tables (using specific profile)
  python3 clearDatastore.py --profile default --region us-west-2

  # Clear everything (all tables and logs)
  python3 clearDatastore.py --profile default --region us-west-2 --all

  # Clear leaderboard config table (auto-discovers API credentials)
  python3 clearDatastore.py --profile default --region us-west-2 --lbconfig

  # Dry run to see what would be deleted
  python3 clearDatastore.py --profile default --region us-west-2 --all --dry-run
        """
    )
    
    parser.add_argument('--profile', default='default', help='AWS profile name (default: default)')
    parser.add_argument('--region', default='us-west-2', help='AWS region (default: us-west-2)')
    parser.add_argument('--stack-name', default='GameStatsLeaderboardsStack',
                       help='CloudFormation stack name (default: GameStatsLeaderboardsStack)')
    
    # What to clear
    parser.add_argument('--all', action='store_true',
                       help='Clear everything (all tables, logs, and CloudWatch logs)')
    parser.add_argument('--lbconfig', action='store_true', 
                       help='Clear leaderboard config table (deletes leaderboards via API)')
    parser.add_argument('--lbstats', action='store_true',
                       help='Clear leaderboard stats table')
    parser.add_argument('--logs', action='store_true',
                       help='Clear all logs in testlogs directory')
    parser.add_argument('--cloudwatchlogs', action='store_true',
                       help='Clear CloudWatch logs for all Lambda functions')
    
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be deleted without actually deleting')
    parser.add_argument('--boost', action='store_true',
                       help='Temporarily switch tables to provisioned mode with high WCU for faster deletion')
    parser.add_argument('--boost-wcu', type=int, default=25000,
                       help='Write capacity units for boost mode (default: 25000)')
    
    args = parser.parse_args()
    
    # Handle --all flag
    if args.all:
        args.lbconfig = True
        args.lbstats = True
        args.logs = True
        args.cloudwatchlogs = True
    
    # Create AWS session
    try:
        # Use default credentials if profile is "default", otherwise use specified profile
        if args.profile == 'default':
            session = boto3.Session(region_name=args.region)
        else:
            session = boto3.Session(profile_name=args.profile, region_name=args.region)
        
        # Test credentials
        sts = session.client('sts')
        identity = sts.get_caller_identity()
        print(f"\n{'='*80}")
        print(f"AWS Account: {identity['Account']}")
        print(f"User/Role: {identity['Arn']}")
        print(f"Region: {args.region}")
        if args.profile != 'default':
            print(f"Profile: {args.profile}")
        print(f"{'='*80}")
    except Exception as e:
        print(f"✗ Error: Failed to create AWS session: {e}", file=sys.stderr)
        return 1
    
    # Auto-discover API credentials if lbconfig is requested
    api_url = None
    api_key = None
    game_id = None
    
    if args.lbconfig:
        print(f"\nAuto-discovering API credentials...")
        
        # Get API URL from CloudFormation
        outputs = discover_infrastructure(args.stack_name, session, args.region)
        api_url = outputs.get('ApiEndpoint')
        
        if api_url:
            print(f"✓ Discovered API URL: {api_url}")
        else:
            print(f"✗ Error: Could not discover API URL from CloudFormation stack '{args.stack_name}'", 
                  file=sys.stderr)
            return 1
        
        # Determine SSM prefix from API URL
        ssm_prefix = '/game-statsleaderboards-dev'  # Default
        if '/prod/' in api_url:
            ssm_prefix = '/game-statsleaderboards-prod'
        elif '/staging/' in api_url:
            ssm_prefix = '/game-statsleaderboards-staging'
        
        # Get API key from SSM (find any key for authentication)
        api_key, studio_id, game_id = find_any_api_key(session, args.region, ssm_prefix)
        
        if api_key and game_id:
            print(f"✓ Discovered API key from SSM")
            print(f"  Studio ID: {studio_id}")
            print(f"  Game ID: {game_id}")
        else:
            print(f"✗ Error: Could not discover API key from SSM Parameter Store", file=sys.stderr)
            print(f"  Path checked: {ssm_prefix}/api-keys/", file=sys.stderr)
            return 1
    
    # Build list of tables to clear
    tables_to_clear = LOAD_TEST_TABLES.copy()
    
    if args.lbstats:
        tables_to_clear.append(LEADERBOARD_STATS_TABLE)
    
    # Show what will be cleared
    print(f"\nTables to clear ({len(tables_to_clear)}):")
    for table in tables_to_clear:
        print(f"  • {table}")
    
    if args.lbconfig:
        print(f"\nLeaderboard config table cleanup:")
        print(f"  • Will delete leaderboards via API for game: {game_id}")
        print(f"  • Then verify DynamoDB cleanup (removes ALL records)")
    
    if args.logs:
        logs_dir = Path(__file__).parent / 'testlogs'
        print(f"\nLogs to clear:")
        print(f"  • Directory: {logs_dir}")
    
    if args.cloudwatchlogs:
        print(f"\nCloudWatch logs to clear ({len(LAMBDA_LOG_GROUPS)} log groups):")
        for log_group in LAMBDA_LOG_GROUPS:
            print(f"  • {log_group}")
    
    if args.dry_run:
        print(f"\n[DRY RUN MODE] No data will be deleted")
    else:
        print(f"\n⚠ WARNING: This will permanently delete all data!")
        response = input("Continue? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return 0
    
    overall_start = time.time()
    results = {}
    failed_leaderboards = []  # Track leaderboards that failed API deletion
    
    # If boost mode, trigger billing mode change on stats table FIRST (non-blocking)
    # This runs in the background while we process other tables
    cleaner = TableCleaner(session, args.region, dry_run=args.dry_run)
    boost_initiated = False
    billing_was_changed = False
    
    if args.boost and not args.dry_run and args.lbstats:
        print(f"\n{'─'*60}")
        print(f"CAPACITY BOOST: Initiating billing mode change on {LEADERBOARD_STATS_TABLE}")
        print(f"{'─'*60}")
        # Check current billing mode before initiating
        try:
            current_billing, _ = cleaner._get_table_billing_and_gsis(LEADERBOARD_STATS_TABLE)
            needs_switch = (current_billing == 'PAY_PER_REQUEST')
        except Exception:
            needs_switch = True
        
        boost_initiated = cleaner.initiate_boost(LEADERBOARD_STATS_TABLE, target_wcu=args.boost_wcu)
        billing_was_changed = boost_initiated and needs_switch
        
        if boost_initiated:
            print(f"  ✓ Boost active — will continue with other work while GSIs update")
        else:
            print(f"  ⚠ Boost initiation failed — will proceed at normal speed")
        print()
    
    # Delete leaderboards via API first (if requested)
    if args.lbconfig:
        api_deleter = LeaderboardAPIDeleter(api_url, api_key, dry_run=args.dry_run)
        api_result = api_deleter.delete_all_leaderboards(game_id)
        results['leaderboards_api'] = api_result
        
        # Get list of failed leaderboards to exclude from DynamoDB cleanup
        failed_leaderboards = api_result.get('failed_leaderboards', [])
        
        if failed_leaderboards:
            print(f"\n⚠ WARNING: {len(failed_leaderboards)} leaderboards failed API deletion")
            print(f"These will be EXCLUDED from DynamoDB cleanup to preserve data integrity:")
            for lb_name in failed_leaderboards:
                print(f"  • {lb_name}")
            print(f"\nYou must manually investigate and fix these leaderboards!")
        
        # After API deletion, clean up the DynamoDB table (excluding failed ones)
        print(f"\n{'='*80}")
        print(f"Verifying DynamoDB cleanup for: {LEADERBOARD_CONFIG_TABLE}")
        print(f"{'='*80}")
        if failed_leaderboards:
            print("(Excluding leaderboards that failed API deletion)")
        else:
            print("(This ensures ALL records are removed)")
        
        results[LEADERBOARD_CONFIG_TABLE] = cleaner.clear_table(
            LEADERBOARD_CONFIG_TABLE, 
            exclude_leaderboards=failed_leaderboards
        )
    
    # Clear load test tables first (these are usually small/empty)
    for table_name in tables_to_clear:
        if table_name == LEADERBOARD_STATS_TABLE:
            continue  # Process stats table last (after boost is ready)
        results[table_name] = cleaner.clear_table(table_name)
    
    # Now process the stats table (if requested) — boost should be ready by now
    if LEADERBOARD_STATS_TABLE in tables_to_clear:
        if boost_initiated:
            print(f"\n{'─'*60}")
            print(f"CAPACITY BOOST: Waiting for {LEADERBOARD_STATS_TABLE} to be ready...")
            print(f"{'─'*60}")
            if cleaner._wait_for_table_ready(LEADERBOARD_STATS_TABLE):
                print(f"\n  ✓ Table ready with boosted capacity")
            else:
                print(f"\n  ⚠ Table may not be fully ready, proceeding anyway")
            print()
        
        # Use more parallelism when boosted
        if boost_initiated:
            results[LEADERBOARD_STATS_TABLE] = cleaner.clear_table(
                LEADERBOARD_STATS_TABLE, segments=BOOSTED_SEGMENTS, workers=BOOSTED_WORKERS
            )
        else:
            results[LEADERBOARD_STATS_TABLE] = cleaner.clear_table(LEADERBOARD_STATS_TABLE)
        
        # Always restore to on-demand mode when boost flag is used
        if boost_initiated:
            cleaner._restore_table_capacity(LEADERBOARD_STATS_TABLE, 'PAY_PER_REQUEST')
    
    # Clear logs if requested
    if args.logs:
        logs_dir = Path(__file__).parent / 'testlogs'
        print(f"\n{'='*80}")
        print(f"Clearing logs directory: {logs_dir}")
        print(f"{'='*80}")
        
        if logs_dir.exists():
            if args.dry_run:
                # Count files
                file_count = sum(1 for _ in logs_dir.rglob('*') if _.is_file())
                print(f"[DRY RUN] Would delete {file_count} log files")
                results['logs'] = {'status': 'dry_run', 'deleted': file_count, 'failed': 0}
            else:
                try:
                    # Remove entire directory and recreate it
                    shutil.rmtree(logs_dir)
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Recreate workers subdirectory
                    (logs_dir / 'workers').mkdir(exist_ok=True)
                    
                    print("✓ Logs directory cleared")
                    results['logs'] = {'status': 'success', 'deleted': 1, 'failed': 0}
                except Exception as e:
                    print(f"✗ Error clearing logs: {e}")
                    results['logs'] = {'status': 'error', 'deleted': 0, 'failed': 1}
        else:
            print("✓ Logs directory does not exist")
            results['logs'] = {'status': 'not_found', 'deleted': 0, 'failed': 0}
    
    # Clear CloudWatch logs if requested
    if args.cloudwatchlogs:
        cw_cleaner = CloudWatchLogCleaner(session, args.region, dry_run=args.dry_run)
        cw_results = cw_cleaner.clear_all_lambda_logs(LAMBDA_LOG_GROUPS)
        results['cloudwatch_logs'] = cw_results
    
    overall_time = time.time() - overall_start
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    
    if args.logs and 'logs' in results:
        log_result = results['logs']
        status_icon = '✓' if log_result['status'] in ['success', 'not_found', 'dry_run'] else '✗'
        print(f"{status_icon} Logs directory: cleared")
    
    if args.cloudwatchlogs and 'cloudwatch_logs' in results:
        cw_results = results['cloudwatch_logs']
        total_cw_deleted = sum(r['deleted'] for r in cw_results.values())
        total_cw_failed = sum(r['failed'] for r in cw_results.values())
        status_icon = '✓' if total_cw_failed == 0 else '✗'
        print(f"{status_icon} CloudWatch logs: {total_cw_deleted} streams deleted, {total_cw_failed} failed")
    
    if args.lbconfig and 'leaderboards_api' in results:
        api_result = results['leaderboards_api']
        status_icon = '✓' if api_result['status'] in ['success', 'empty', 'dry_run'] else '✗'
        print(f"{status_icon} Leaderboards (via API): {api_result['deleted']} deleted, {api_result['failed']} failed")
        
        if api_result.get('failed_leaderboards'):
            print(f"  ⚠ {len(api_result['failed_leaderboards'])} leaderboards preserved in DynamoDB (API deletion failed)")
    
    total_deleted = sum(r.get('deleted', 0) for k, r in results.items() if k not in ['leaderboards_api', 'logs', 'cloudwatch_logs'])
    total_failed = sum(r.get('failed', 0) for k, r in results.items() if k not in ['leaderboards_api', 'logs', 'cloudwatch_logs'])
    total_excluded = sum(r.get('excluded', 0) for k, r in results.items() if k not in ['leaderboards_api', 'logs', 'cloudwatch_logs'])
    
    for table_name, result in results.items():
        if table_name in ['leaderboards_api', 'logs', 'cloudwatch_logs']:
            continue
            
        status_icon = {
            'success': '✓',
            'empty': '✓',
            'dry_run': '○',
            'not_found': '⚠',
            'not_active': '⚠'
        }.get(result['status'], '✗')
        
        excluded_info = f", {result.get('excluded', 0):,} excluded" if result.get('excluded', 0) > 0 else ""
        print(f"{status_icon} {table_name}: {result['deleted']:,} deleted, {result['failed']:,} failed{excluded_info}")
    
    print(f"\nTotal DynamoDB items deleted: {total_deleted:,}")
    if total_excluded > 0:
        print(f"Total DynamoDB items excluded: {total_excluded:,} (preserved due to API failures)")
    print(f"Total failed: {total_failed:,}")
    print(f"Total time: {overall_time:.1f}s")
    
    if total_failed > 0 or failed_leaderboards:
        if total_failed > 0:
            print(f"\n⚠ Warning: {total_failed} items failed to delete. You may need to retry.")
        if failed_leaderboards:
            print(f"\n⚠ WARNING: {len(failed_leaderboards)} leaderboards require manual intervention!")
            print(f"These leaderboards failed API deletion and were preserved in DynamoDB:")
            for lb_name in failed_leaderboards:
                print(f"  • {lb_name}")
            print(f"\nInvestigate the API errors above and manually delete these leaderboards.")
        return 1
    
    print(f"\n✓ All cleanup completed successfully!")
    return 0


if __name__ == '__main__':
    sys.exit(main())
