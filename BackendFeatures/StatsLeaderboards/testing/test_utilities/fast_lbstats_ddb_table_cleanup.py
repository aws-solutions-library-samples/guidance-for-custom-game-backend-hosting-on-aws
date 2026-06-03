#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Use this with utmost care, this script is intended to clean up all test data in the below configured DynamoDB table
# It uses some hard-coded parameters intentionally, so when you really want to use it, you should configure it appropriately

import boto3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
TABLE_NAME = "game-statsleaderboards-dev-stats"     # 
PROFILE = "default"
REGION = "us-west-2"
BATCH_SIZE = 25  # DynamoDB batch limit
MAX_WORKERS = 10  # Parallel threads

def batch_delete_items(dynamodb, items_batch):
    """Delete a batch of items"""
    if not items_batch:
        return 0
    
    request_items = {
        TABLE_NAME: [
            {'DeleteRequest': {'Key': {'playerID': {'S': item['playerID']['S']}, 
                                     'sortKey': {'S': item['sortKey']['S']}}}}
            for item in items_batch
        ]
    }
    
    try:
        response = dynamodb.batch_write_item(RequestItems=request_items)
        # Handle unprocessed items
        unprocessed = response.get('UnprocessedItems', {})
        if unprocessed:
            print(f"⚠️  {len(unprocessed.get(TABLE_NAME, []))} unprocessed items")
        return len(items_batch)
    except Exception as e:
        print(f"❌ Batch delete failed: {e}")
        return 0

def main():
    print("=== Fast DynamoDB Cleanup (Python + Batch Operations) ===")
    
    # Initialize DynamoDB client
    session = boto3.Session(profile_name=PROFILE)
    dynamodb = session.client('dynamodb', region_name=REGION)
    
    # Count items
    print("1. Counting items...")
    response = dynamodb.scan(TableName=TABLE_NAME, Select='COUNT')
    total_items = response['Count']
    print(f"Total items: {total_items}")
    
    if total_items == 0:
        print("Table is already empty!")
        return
    
    # Scan and collect all items
    print("2. Scanning items...")
    all_items = []
    paginator = dynamodb.get_paginator('scan')
    
    for page in paginator.paginate(
        TableName=TABLE_NAME,
        ProjectionExpression='playerID, sortKey'
    ):
        all_items.extend(page['Items'])
    
    print(f"Scanned {len(all_items)} items")
    
    # Create batches
    batches = [all_items[i:i + BATCH_SIZE] for i in range(0, len(all_items), BATCH_SIZE)]
    print(f"Created {len(batches)} batches of {BATCH_SIZE} items each")
    
    # Delete in parallel batches
    print("3. Deleting items in parallel batches...")
    deleted_count = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_batch = {
            executor.submit(batch_delete_items, dynamodb, batch): i 
            for i, batch in enumerate(batches)
        }
        
        for future in as_completed(future_to_batch):
            batch_num = future_to_batch[future]
            try:
                batch_deleted = future.result()
                deleted_count += batch_deleted
                print(f"✓ Batch {batch_num + 1}/{len(batches)} - Deleted {batch_deleted} items")
            except Exception as e:
                print(f"❌ Batch {batch_num + 1} failed: {e}")
    
    elapsed = time.time() - start_time
    
    # Final count
    print("4. Final verification...")
    response = dynamodb.scan(TableName=TABLE_NAME, Select='COUNT')
    remaining = response['Count']
    
    print(f"\n=== Results ===")
    print(f"Items deleted: {deleted_count}")
    print(f"Items remaining: {remaining}")
    print(f"Time taken: {elapsed:.2f} seconds")
    print(f"Rate: {deleted_count/elapsed:.1f} items/second")

if __name__ == "__main__":
    main()
