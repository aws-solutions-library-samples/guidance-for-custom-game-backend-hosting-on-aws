#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Use this with utmost care, this script is intended to clean up all data in the specified Amazon DynamoDB table

import boto3
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_table_key_schema(dynamodb, table_name):
    """Get the key schema and attribute types for a table"""
    try:
        response = dynamodb.describe_table(TableName=table_name)
        table = response['Table']
        
        key_schema = table['KeySchema']
        attributes = {attr['AttributeName']: attr['AttributeType'] 
                     for attr in table['AttributeDefinitions']}
        
        return key_schema, attributes
    except Exception as e:
        print(f"❌ Error getting table schema: {e}")
        sys.exit(1)

def build_key_from_item(item, key_schema, attributes):
    """Build a DynamoDB key from an item based on the table's key schema"""
    key = {}
    for key_attr in key_schema:
        attr_name = key_attr['AttributeName']
        attr_type = attributes[attr_name]
        key[attr_name] = {attr_type: item[attr_name][attr_type]}
    return key

def batch_delete_items(dynamodb, table_name, items_batch, key_schema, attributes):
    """Delete a batch of items"""
    if not items_batch:
        return 0
    
    request_items = {
        table_name: [
            {'DeleteRequest': {'Key': build_key_from_item(item, key_schema, attributes)}}
            for item in items_batch
        ]
    }
    
    try:
        response = dynamodb.batch_write_item(RequestItems=request_items)
        unprocessed = response.get('UnprocessedItems', {})
        if unprocessed:
            print(f"⚠️  {len(unprocessed.get(table_name, []))} unprocessed items")
        return len(items_batch)
    except Exception as e:
        print(f"❌ Batch delete failed: {e}")
        return 0

def main():
    # Default values
    default_profile = 'default'
    default_region = 'us-east-1'
    
    if len(sys.argv) < 2:
        print("Usage: python3 ddb_table_cleanup.py <table-name> [profile] [region]")
        print("Example: python3 ddb_table_cleanup.py my-table default us-west-2")
        print()
        print("Defaults (if not specified):")
        print(f"  profile: '{default_profile}'")
        print(f"  region:  '{default_region}'")
        sys.exit(1)
    
    table_name = sys.argv[1]
    profile = sys.argv[2] if len(sys.argv) > 2 else default_profile
    region = sys.argv[3] if len(sys.argv) > 3 else default_region
    
    print(f"=== Universal Amazon DynamoDB Table Data Cleanup ===")
    print(f"Amazon DynamoDB Table: {table_name}")
    print(f"Profile: {profile}")
    print(f"Region: {region}")
    print()
    
    # Initialize DynamoDB client
    session = boto3.Session(profile_name=profile)
    dynamodb = session.client('dynamodb', region_name=region)
    
    # Get table schema
    print("1. Getting table schema...")
    key_schema, attributes = get_table_key_schema(dynamodb, table_name)
    
    key_attrs = [k['AttributeName'] for k in key_schema]
    print(f"Key attributes: {key_attrs}")
    
    # Count items
    print("2. Counting items...")
    response = dynamodb.scan(TableName=table_name, Select='COUNT')
    total_items = response['Count']
    print(f"Total items: {total_items}")
    
    if total_items == 0:
        print("Amazon DynamoDB Table is already empty!")
        return
    
    # Scan items (only key attributes)
    print("3. Scanning key attributes...")
    all_items = []
    projection = ', '.join(key_attrs)
    
    paginator = dynamodb.get_paginator('scan')
    for page in paginator.paginate(
        TableName=table_name,
        ProjectionExpression=projection
    ):
        all_items.extend(page['Items'])
    
    # Show summary and ask for confirmation
    batches = [all_items[i:i + 25] for i in range(0, len(all_items), 25)]
    print(f"\n=== DELETION SUMMARY ===")
    print(f"Amazon DynamoDB Table: {table_name}")
    print(f"Profile: {profile}")
    print(f"Region: {region}")
    print(f"Items to delete: {len(all_items)}")
    print(f"Batches: {len(batches)} (25 items each)")
    print(f"Key attributes: {key_attrs}")
    print()
    print("⚠️  This will PERMANENTLY DELETE all items from the Amazon DynamoDB Table!")
    print("⚠️  This action cannot be undone!")
    print()
    
    confirm = input("Are you sure you want to proceed? (type 'yes' to confirm): ").strip().lower()
    if confirm != 'yes':
        print("❌ Operation cancelled by user")
        return
    
    # Create batches and delete
    print(f"4. Deleting {len(all_items)} items in {len(batches)} batches...")
    
    deleted_count = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(batch_delete_items, dynamodb, table_name, batch, key_schema, attributes)
            for batch in batches
        ]
        
        for i, future in enumerate(as_completed(futures)):
            deleted_count += future.result()
            print(f"✓ Batch {i + 1}/{len(batches)} completed")
    
    elapsed = time.time() - start_time
    
    # Final verification
    print("5. Verifying deletion...")
    final_response = dynamodb.scan(TableName=table_name, Select='COUNT')
    remaining_items = final_response['Count']
    
    print(f"\n=== COMPLETION SUMMARY ===")
    print(f"Amazon DynamoDB Table: {table_name}")
    print(f"Original items: {total_items}")
    print(f"Items deleted: {deleted_count}")
    print(f"Items remaining: {remaining_items}")
    print(f"Batches processed: {len(batches)}")
    print(f"Time taken: {elapsed:.2f} seconds")
    print(f"Deletion rate: {deleted_count/elapsed:.1f} items/second")
    
    if remaining_items == 0:
        print("✅ SUCCESS: Amazon DynamoDB Table is now empty!")
    else:
        print(f"⚠️  WARNING: {remaining_items} items still remain in table")
    
    print("=== Operation Complete ===")

if __name__ == "__main__":
    main()
