#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Create CloudWatch Dashboard for Load Testing Observability

This script creates a comprehensive CloudWatch dashboard with:
- Error rate metrics
- Latency percentiles
- API Gateway metrics
- Lambda metrics
- X-Ray service map integration
"""

import boto3
import json
import argparse
from datetime import datetime

# Dashboard configuration
DASHBOARD_NAME = "LoadTest-Stats-Leaderboards-Observability"
LOG_GROUP_NAME = "/loadtest/stats-leaderboards"
API_GATEWAY_NAME = "GameStatsLeaderboardsAPI"  # Update with your API Gateway name
LAMBDA_FUNCTION_PREFIX = "GameStatsLeaderboards"  # Update with your Lambda prefix

def create_dashboard_body(region, api_gateway_id):
    """
    Create the dashboard body JSON.
    
    Args:
        region: AWS region
        api_gateway_id: API Gateway ID for metrics
    
    Returns:
        dict: Dashboard body configuration
    """
    
    dashboard = {
        "widgets": [
            # Row 1: Error Metrics
            {
                "type": "metric",
                "x": 0,
                "y": 0,
                "width": 12,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/Logs", "IncomingLogEvents", {"stat": "Sum", "label": "Total Log Events"}],
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": region,
                    "title": "Log Events Rate",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Count"
                        }
                    }
                }
            },
            {
                "type": "log",
                "x": 12,
                "y": 0,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /ERROR/\n| stats count() by bin(1m)",
                    "region": region,
                    "title": "Error Rate (per minute)",
                    "stacked": False,
                    "view": "timeSeries"
                }
            },
            
            # Row 2: Error Breakdown by Status Code
            {
                "type": "log",
                "x": 0,
                "y": 6,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /API Error/\n| parse @message /returned (?<status_code>\\d+)/\n| stats count() by status_code",
                    "region": region,
                    "title": "Errors by Status Code",
                    "stacked": False,
                    "view": "pie"
                }
            },
            {
                "type": "log",
                "x": 12,
                "y": 6,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /High latency/\n| parse @message /took (?<latency>\\d+)ms/\n| stats avg(latency), max(latency), min(latency) by bin(1m)",
                    "region": region,
                    "title": "High Latency API Calls",
                    "stacked": False,
                    "view": "timeSeries"
                }
            },
            
            # Row 3: Worker Activity
            {
                "type": "log",
                "x": 0,
                "y": 12,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| parse @message /\\[Worker-(?<worker_id>\\d+)\\]/\n| stats count() as total_events, count(@message like /ERROR/) as errors by worker_id\n| sort total_events desc\n| limit 20",
                    "region": region,
                    "title": "Top 20 Active Workers",
                    "stacked": False,
                    "view": "table"
                }
            },
            {
                "type": "log",
                "x": 12,
                "y": 12,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /429/ or @message like /throttled/\n| stats count() by bin(1m)",
                    "region": region,
                    "title": "Throttling Events (429)",
                    "stacked": False,
                    "view": "timeSeries"
                }
            },
            
            # Row 4: Validation Failures
            {
                "type": "log",
                "x": 0,
                "y": 18,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /VALIDATION FAILURE/\n| fields @timestamp, @message\n| sort @timestamp desc\n| limit 50",
                    "region": region,
                    "title": "Recent Validation Failures",
                    "stacked": False,
                    "view": "table"
                }
            },
            {
                "type": "log",
                "x": 12,
                "y": 18,
                "width": 12,
                "height": 6,
                "properties": {
                    "query": f"SOURCE '{LOG_GROUP_NAME}'\n| filter @message like /timeout/ or @message like /network error/\n| stats count() by bin(1m)",
                    "region": region,
                    "title": "Network & Timeout Errors",
                    "stacked": False,
                    "view": "timeSeries"
                }
            },
            
            # Row 5: API Gateway Metrics
            {
                "type": "metric",
                "x": 0,
                "y": 24,
                "width": 8,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/ApiGateway", "Count", {"stat": "Sum", "label": "Total Requests"}],
                        [".", "4XXError", {"stat": "Sum", "label": "4XX Errors"}],
                        [".", "5XXError", {"stat": "Sum", "label": "5XX Errors"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": region,
                    "title": "API Gateway Request Metrics",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Count"
                        }
                    }
                }
            },
            {
                "type": "metric",
                "x": 8,
                "y": 24,
                "width": 8,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/ApiGateway", "Latency", {"stat": "Average", "label": "Avg Latency"}],
                        ["...", {"stat": "p50", "label": "p50"}],
                        ["...", {"stat": "p90", "label": "p90"}],
                        ["...", {"stat": "p99", "label": "p99"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": region,
                    "title": "API Gateway Latency",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Milliseconds"
                        }
                    }
                }
            },
            {
                "type": "metric",
                "x": 16,
                "y": 24,
                "width": 8,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/ApiGateway", "IntegrationLatency", {"stat": "Average", "label": "Avg Integration Latency"}],
                        ["...", {"stat": "p90", "label": "p90"}],
                        ["...", {"stat": "p99", "label": "p99"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": region,
                    "title": "API Gateway Integration Latency",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Milliseconds"
                        }
                    }
                }
            }
        ]
    }
    
    return dashboard


def get_saved_queries():
    """
    Get list of CloudWatch Logs Insights saved queries.
    
    Returns:
        list: List of query definitions with name and query string
    """
    return [
        {
            "name": "Find Errors by EventID",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /EventID: /
| sort @timestamp desc
| limit 100"""
        },
        {
            "name": "Error Rate Over Time",
            "query": f"""fields @timestamp
| filter @message like /ERROR/
| stats count() by bin(5m)"""
        },
        {
            "name": "Errors by Status Code",
            "query": f"""fields @timestamp, @message
| filter @message like /API Error/
| parse @message /returned (?<status_code>\\d+)/
| stats count() by status_code
| sort count desc"""
        },
        {
            "name": "High Latency API Calls",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /High latency/
| parse @message /took (?<latency>\\d+)ms/
| sort latency desc
| limit 50"""
        },
        {
            "name": "Throttling Events (429)",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /429/ or @message like /throttled/
| parse @message /\\[Worker-(?<worker_id>\\d+)\\]/
| stats count() by worker_id
| sort count desc"""
        },
        {
            "name": "Validation Failures",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /VALIDATION FAILURE/
| sort @timestamp desc
| limit 100"""
        },
        {
            "name": "Worker Activity Summary",
            "query": f"""fields @timestamp, @message
| parse @message /\\[Worker-(?<worker_id>\\d+)\\]/
| parse @message /\\[PlayerWorker-(?<player_worker_id>\\d+)\\]/
| stats count() as total_events, 
        count(@message like /ERROR/) as errors,
        count(@message like /WARNING/) as warnings
  by coalesce(worker_id, player_worker_id) as worker
| sort total_events desc
| limit 20"""
        },
        {
            "name": "Network and Timeout Errors",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /timeout/ or @message like /network error/
| sort @timestamp desc
| limit 100"""
        },
        {
            "name": "Errors in Time Window",
            "query": f"""fields @timestamp, @logStream, @message
| filter @message like /ERROR/
| sort @timestamp asc
| limit 200"""
        },
        {
            "name": "Worker-Specific Errors",
            "query": f"""fields @timestamp, @message
| filter @message like /ERROR/ or @message like /WARNING/
| sort @timestamp desc
| limit 100"""
        }
    ]


def create_saved_queries(profile, region):
    """
    Create saved queries in CloudWatch Logs Insights.
    
    Args:
        profile: AWS profile name
        region: AWS region
    
    Returns:
        tuple: (success_count, total_count)
    """
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        logs = session.client('logs')
        
        queries = get_saved_queries()
        success_count = 0
        
        print(f"\n📝 Creating {len(queries)} saved queries...")
        
        for query_def in queries:
            try:
                response = logs.put_query_definition(
                    name=query_def['name'],
                    queryString=query_def['query'],
                    logGroupNames=[LOG_GROUP_NAME]
                )
                print(f"   ✅ {query_def['name']}")
                success_count += 1
            except Exception as e:
                print(f"   ⚠️  {query_def['name']}: {e}")
        
        return success_count, len(queries)
        
    except Exception as e:
        print(f"❌ Error creating saved queries: {e}")
        return 0, len(get_saved_queries())


def create_dashboard(profile, region, api_gateway_id):
    """
    Create the CloudWatch dashboard.
    
    Args:
        profile: AWS profile name
        region: AWS region
        api_gateway_id: API Gateway ID
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        cloudwatch = session.client('cloudwatch')
        
        dashboard_body = create_dashboard_body(region, api_gateway_id)
        
        response = cloudwatch.put_dashboard(
            DashboardName=DASHBOARD_NAME,
            DashboardBody=json.dumps(dashboard_body)
        )
        
        print(f"✅ Dashboard '{DASHBOARD_NAME}' created successfully!")
        print(f"   Region: {region}")
        print(f"   Profile: {profile}")
        print(f"\n📊 View dashboard at:")
        print(f"   https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#dashboards:name={DASHBOARD_NAME}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error creating dashboard: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Create CloudWatch Dashboard for Load Testing Observability"
    )
    parser.add_argument(
        '--profile',
        required=True,
        help='AWS profile name (e.g., dev_aws_profile)'
    )
    parser.add_argument(
        '--region',
        default='us-west-2',
        help='AWS region (default: us-west-2)'
    )
    parser.add_argument(
        '--api-gateway-id',
        required=True,
        help='API Gateway ID for metrics (e.g., abc123xyz)'
    )
    
    args = parser.parse_args()
    
    print(f"Creating CloudWatch Observability Setup...")
    print(f"  Dashboard Name: {DASHBOARD_NAME}")
    print(f"  Log Group: {LOG_GROUP_NAME}")
    print(f"  Region: {args.region}")
    print(f"  Profile: {args.profile}")
    print(f"  API Gateway ID: {args.api_gateway_id}")
    print()
    
    # Create dashboard
    dashboard_success = create_dashboard(args.profile, args.region, args.api_gateway_id)
    
    # Create saved queries
    query_success_count, query_total_count = create_saved_queries(args.profile, args.region)
    
    print()
    if dashboard_success and query_success_count == query_total_count:
        print("✅ Complete observability setup successful!")
        print(f"\n📊 Dashboard: {DASHBOARD_NAME}")
        print(f"📝 Saved Queries: {query_success_count}/{query_total_count}")
        print("\n📝 Next steps:")
        print("   1. Open the dashboard URL above")
        print("   2. Access saved queries in CloudWatch Logs Insights")
        print("   3. Customize time range as needed")
        print("   4. Share with team members")
    elif dashboard_success:
        print(f"⚠️  Partial success: Dashboard created, but only {query_success_count}/{query_total_count} queries saved")
    else:
        print("❌ Setup failed!")
        exit(1)


if __name__ == '__main__':
    main()
