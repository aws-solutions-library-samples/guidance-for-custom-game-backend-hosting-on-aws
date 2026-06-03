#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Comprehensive CloudWatch Dashboard Creator

Creates a single-pane-of-glass monitoring dashboard for the Stats & Leaderboards system.
Includes metrics for:
- Lambda functions (all functions)
- DynamoDB tables (Config, Stats, Test Definitions, Test Metrics, Player State)
- API Gateway (requests, latency, errors)
- MemoryDB for Valkey (CPU, memory, connections, commands)
- Overall system health

Usage:
    python create_comprehensive_dashboard.py --stack-name game-statsleaderboards-dev --region us-west-2
    python create_comprehensive_dashboard.py --stack-name game-statsleaderboards-dev --region us-west-2 --force
"""

import argparse
import boto3
import json
import sys
from typing import Dict, List, Any, Optional
from datetime import datetime


class ComprehensiveDashboardCreator:
    """Creates comprehensive CloudWatch dashboard for Stats & Leaderboards system"""
    
    DASHBOARD_NAME_TEMPLATE = "{stack_name}-comprehensive-monitoring"
    
    # Lambda function suffixes (will be prefixed with stack name)
    LAMBDA_FUNCTIONS = [
        "backend-authorizer",
        "developer-registration",
        "leaderboards-config",
        "batch-store-stats",
        "store-stats",
        "get-leaderboard-scores",
        "get-player-stats",
        "get-player-lb-standing",
        "rebuild-leaderboard",
        "reset-leaderboard"
    ]
    
    # DynamoDB table suffixes
    DYNAMODB_TABLES = [
        "config",
        "stats",
        "test-definitions",
        "test-metrics",
        "player-state"
    ]
    
    def __init__(self, stack_name: str, region: str, profile: Optional[str] = None):
        """
        Initialize dashboard creator.
        
        Args:
            stack_name: CloudFormation stack name (e.g., 'game-statsleaderboards-dev')
            region: AWS region
            profile: AWS profile name (optional)
        """
        self.stack_name = stack_name
        self.region = region
        self.profile = profile
        
        # Create boto3 session
        session_kwargs = {'region_name': region}
        if profile:
            session_kwargs['profile_name'] = profile
        self.session = boto3.Session(**session_kwargs)
        
        # Create clients
        self.cloudwatch = self.session.client('cloudwatch')
        self.cloudformation = self.session.client('cloudformation')
        self.lambda_client = self.session.client('lambda')
        self.dynamodb = self.session.client('dynamodb')
        self.apigateway = self.session.client('apigateway')
        
        # Dashboard name
        self.dashboard_name = self.DASHBOARD_NAME_TEMPLATE.format(stack_name=stack_name)
        
        # Discovered resources
        self.resources = {
            'lambda_functions': [],
            'dynamodb_tables': [],
            'api_gateway_id': None,
            'api_gateway_name': None,
            'memorydb_cluster': None
        }
    
    def discover_resources(self):
        """Discover all resources from CloudFormation stack"""
        print(f"\n🔍 Discovering resources from stack: {self.stack_name}")
        
        try:
            # Get stack outputs
            response = self.cloudformation.describe_stacks(StackName=self.stack_name)
            stack = response['Stacks'][0]
            outputs = {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])}
            
            # Extract API Gateway info
            api_endpoint = outputs.get('ApiEndpoint', '')
            if api_endpoint:
                # Extract API ID from endpoint URL
                # Format: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/
                parts = api_endpoint.split('.')
                if len(parts) > 0:
                    self.resources['api_gateway_id'] = parts[0].split('//')[1]
            
            self.resources['api_gateway_name'] = outputs.get('ApiName', f"{self.stack_name}-api")
            
            # Extract MemoryDB cluster name
            memorydb_endpoint = outputs.get('MemoryDBEndpoint', '')
            if memorydb_endpoint:
                # Format: clustercfg.{cluster-name}.{random}.memorydb.{region}.amazonaws.com:6379
                parts = memorydb_endpoint.split('.')
                if len(parts) > 1:
                    self.resources['memorydb_cluster'] = parts[1]
            
            print(f"  ✓ API Gateway: {self.resources['api_gateway_name']}")
            print(f"  ✓ MemoryDB Cluster: {self.resources['memorydb_cluster']}")
            
        except Exception as e:
            print(f"  ⚠ Warning: Could not get stack outputs: {e}")
        
        # Discover Lambda functions
        self._discover_lambda_functions()
        
        # Discover DynamoDB tables
        self._discover_dynamodb_tables()
    
    def _discover_lambda_functions(self):
        """Discover Lambda functions"""
        print(f"\n  Discovering Lambda functions...")
        
        try:
            # List all Lambda functions with stack name prefix
            paginator = self.lambda_client.get_paginator('list_functions')
            for page in paginator.paginate():
                for function in page['Functions']:
                    func_name = function['FunctionName']
                    if func_name.startswith(self.stack_name):
                        self.resources['lambda_functions'].append(func_name)
            
            print(f"    ✓ Found {len(self.resources['lambda_functions'])} Lambda functions")
            for func in self.resources['lambda_functions']:
                print(f"      - {func}")
        
        except Exception as e:
            print(f"    ⚠ Warning: Could not discover Lambda functions: {e}")
    
    def _discover_dynamodb_tables(self):
        """Discover DynamoDB tables"""
        print(f"\n  Discovering DynamoDB tables...")
        
        try:
            # List all tables with stack name prefix
            paginator = self.dynamodb.get_paginator('list_tables')
            for page in paginator.paginate():
                for table_name in page['TableNames']:
                    if table_name.startswith(self.stack_name):
                        self.resources['dynamodb_tables'].append(table_name)
            
            print(f"    ✓ Found {len(self.resources['dynamodb_tables'])} DynamoDB tables")
            for table in self.resources['dynamodb_tables']:
                print(f"      - {table}")
        
        except Exception as e:
            print(f"    ⚠ Warning: Could not discover DynamoDB tables: {e}")
    
    def check_dashboard_exists(self) -> bool:
        """Check if dashboard already exists"""
        try:
            self.cloudwatch.get_dashboard(DashboardName=self.dashboard_name)
            return True
        except self.cloudwatch.exceptions.ResourceNotFound:
            return False
        except Exception as e:
            print(f"⚠ Warning: Could not check dashboard existence: {e}")
            return False
    
    def delete_dashboard(self):
        """Delete existing dashboard"""
        try:
            self.cloudwatch.delete_dashboards(DashboardNames=[self.dashboard_name])
            print(f"  ✓ Deleted existing dashboard: {self.dashboard_name}")
        except Exception as e:
            print(f"  ⚠ Warning: Could not delete dashboard: {e}")
    
    def create_dashboard(self):
        """Create comprehensive CloudWatch dashboard"""
        print(f"\n📊 Creating comprehensive dashboard: {self.dashboard_name}")
        
        # Build dashboard body
        dashboard_body = {
            "widgets": []
        }
        
        row = 0
        
        # Row 1: System Overview (24 width total)
        dashboard_body["widgets"].extend(self._create_system_overview_widgets(row))
        row += 6
        
        # Row 2: API Gateway Metrics (24 width total)
        dashboard_body["widgets"].extend(self._create_api_gateway_widgets(row))
        row += 6
        
        # Row 3: MemoryDB Metrics (24 width total)
        dashboard_body["widgets"].extend(self._create_memorydb_widgets(row))
        row += 6
        
        # Row 4+: Lambda Functions (6 per row, 4 rows = 24 functions max)
        dashboard_body["widgets"].extend(self._create_lambda_widgets(row))
        row += (len(self.resources['lambda_functions']) // 4 + 1) * 6
        
        # Next rows: DynamoDB Tables (6 per row)
        dashboard_body["widgets"].extend(self._create_dynamodb_widgets(row))
        
        # Create dashboard
        try:
            self.cloudwatch.put_dashboard(
                DashboardName=self.dashboard_name,
                DashboardBody=json.dumps(dashboard_body)
            )
            print(f"  ✓ Dashboard created successfully!")
            print(f"\n🔗 Dashboard URL:")
            print(f"   https://console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards:name={self.dashboard_name}")
        
        except Exception as e:
            print(f"  ❌ Error creating dashboard: {e}")
            raise
    
    def _create_system_overview_widgets(self, row: int) -> List[Dict]:
        """Create system overview widgets"""
        widgets = []
        
        # Widget 1: Total API Requests (8 width)
        if self.resources['api_gateway_name']:
            widgets.append({
                "type": "metric",
                "x": 0,
                "y": row,
                "width": 8,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/ApiGateway", "Count", {"stat": "Sum", "label": "Total Requests"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": self.region,
                    "title": "📊 Total API Requests",
                    "period": 300,
                    "yAxis": {
                        "left": {
                            "label": "Requests"
                        }
                    }
                }
            })
        
        # Widget 2: Overall Error Rate (8 width)
        if self.resources['api_gateway_name']:
            widgets.append({
                "type": "metric",
                "x": 8,
                "y": row,
                "width": 8,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/ApiGateway", "4XXError", {"stat": "Sum", "label": "4XX Errors", "color": "#ff7f0e"}],
                        [".", "5XXError", {"stat": "Sum", "label": "5XX Errors", "color": "#d62728"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": self.region,
                    "title": "⚠️ API Error Rate",
                    "period": 300,
                    "yAxis": {
                        "left": {
                            "label": "Errors"
                        }
                    }
                }
            })
        
        # Widget 3: System Health Score (8 width)
        widgets.append({
            "type": "metric",
            "x": 16,
            "y": row,
            "width": 8,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/Lambda", "Errors", {"stat": "Sum", "label": "Lambda Errors"}],
                    ["AWS/DynamoDB", "SystemErrors", {"stat": "Sum", "label": "DynamoDB Errors"}]
                ],
                "view": "timeSeries",
                "stacked": True,
                "region": self.region,
                "title": "🏥 System Health (Lower is Better)",
                "period": 300
            }
        })
        
        return widgets

    def _create_api_gateway_widgets(self, row: int) -> List[Dict]:
        """Create API Gateway monitoring widgets"""
        widgets = []
        
        if not self.resources['api_gateway_name']:
            return widgets
        
        api_name = self.resources['api_gateway_name']
        
        # Widget 1: Request Count by Method (8 width)
        widgets.append({
            "type": "metric",
            "x": 0,
            "y": row,
            "width": 8,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/ApiGateway", "Count", {"stat": "Sum", "label": "Total Requests"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "🌐 API Gateway - Requests",
                "period": 60,
                "yAxis": {
                    "left": {
                        "label": "Requests/min"
                    }
                }
            }
        })
        
        # Widget 2: Latency (p50, p95, p99) (8 width)
        widgets.append({
            "type": "metric",
            "x": 8,
            "y": row,
            "width": 8,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/ApiGateway", "Latency", {"stat": "p50", "label": "p50"}],
                    ["...", {"stat": "p95", "label": "p95"}],
                    ["...", {"stat": "p99", "label": "p99"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "⏱️ API Gateway - Latency Percentiles",
                "period": 60,
                "yAxis": {
                    "left": {
                        "label": "Milliseconds"
                    }
                }
            }
        })
        
        # Widget 3: Integration Latency (8 width)
        widgets.append({
            "type": "metric",
            "x": 16,
            "y": row,
            "width": 8,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/ApiGateway", "IntegrationLatency", {"stat": "Average", "label": "Avg Integration Latency"}],
                    ["...", {"stat": "p99", "label": "p99 Integration Latency"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "🔗 API Gateway - Integration Latency",
                "period": 60,
                "yAxis": {
                    "left": {
                        "label": "Milliseconds"
                    }
                }
            }
        })
        
        return widgets
    
    def _create_memorydb_widgets(self, row: int) -> List[Dict]:
        """Create MemoryDB (Valkey) monitoring widgets"""
        widgets = []
        
        if not self.resources['memorydb_cluster']:
            return widgets
        
        cluster_name = self.resources['memorydb_cluster']
        
        # Widget 1: CPU Utilization (6 width)
        widgets.append({
            "type": "metric",
            "x": 0,
            "y": row,
            "width": 6,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/MemoryDB", "CPUUtilization", {"stat": "Average"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "💻 MemoryDB - CPU Utilization",
                "period": 60,
                "yAxis": {
                    "left": {
                        "min": 0,
                        "max": 100,
                        "label": "Percent"
                    }
                }
            }
        })
        
        # Widget 2: Memory Utilization (6 width)
        widgets.append({
            "type": "metric",
            "x": 6,
            "y": row,
            "width": 6,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/MemoryDB", "DatabaseMemoryUsagePercentage", {"stat": "Average"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "💾 MemoryDB - Memory Usage",
                "period": 60,
                "yAxis": {
                    "left": {
                        "min": 0,
                        "max": 100,
                        "label": "Percent"
                    }
                }
            }
        })
        
        # Widget 3: Commands Processed (6 width)
        widgets.append({
            "type": "metric",
            "x": 12,
            "y": row,
            "width": 6,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/MemoryDB", "CommandsProcessed", {"stat": "Sum", "label": "Commands/min"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "⚡ MemoryDB - Commands Processed",
                "period": 60,
                "yAxis": {
                    "left": {
                        "label": "Commands"
                    }
                }
            }
        })
        
        # Widget 4: Current Connections (6 width)
        widgets.append({
            "type": "metric",
            "x": 18,
            "y": row,
            "width": 6,
            "height": 6,
            "properties": {
                "metrics": [
                    ["AWS/MemoryDB", "CurrConnections", {"stat": "Average"}]
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": self.region,
                "title": "🔌 MemoryDB - Active Connections",
                "period": 60,
                "yAxis": {
                    "left": {
                        "label": "Connections"
                    }
                }
            }
        })
        
        return widgets
    
    def _create_lambda_widgets(self, row: int) -> List[Dict]:
        """Create Lambda function monitoring widgets"""
        widgets = []
        
        if not self.resources['lambda_functions']:
            return widgets
        
        # Create 6-width widgets, 4 per row
        for idx, func_name in enumerate(self.resources['lambda_functions']):
            col = (idx % 4) * 6
            current_row = row + (idx // 4) * 6
            
            # Each Lambda gets: Invocations, Errors, Duration in one widget
            widgets.append({
                "type": "metric",
                "x": col,
                "y": current_row,
                "width": 6,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/Lambda", "Invocations", {"stat": "Sum", "label": "Invocations"}],
                        [".", "Errors", {"stat": "Sum", "label": "Errors", "yAxis": "right"}],
                        [".", "Duration", {"stat": "Average", "label": "Avg Duration (ms)"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": self.region,
                    "title": f"λ {func_name.replace(self.stack_name + '-', '')}",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Count / ms"
                        },
                        "right": {
                            "label": "Errors"
                        }
                    }
                }
            })
        
        return widgets
    
    def _create_dynamodb_widgets(self, row: int) -> List[Dict]:
        """Create DynamoDB table monitoring widgets"""
        widgets = []
        
        if not self.resources['dynamodb_tables']:
            return widgets
        
        # Create 6-width widgets, 4 per row
        for idx, table_name in enumerate(self.resources['dynamodb_tables']):
            col = (idx % 4) * 6
            current_row = row + (idx // 4) * 6
            
            # Each table gets: Read/Write capacity, Throttles
            widgets.append({
                "type": "metric",
                "x": col,
                "y": current_row,
                "width": 6,
                "height": 6,
                "properties": {
                    "metrics": [
                        ["AWS/DynamoDB", "ConsumedReadCapacityUnits", {"stat": "Sum", "label": "Read Units"}],
                        [".", "ConsumedWriteCapacityUnits", {"stat": "Sum", "label": "Write Units"}],
                        [".", "UserErrors", {"stat": "Sum", "label": "User Errors", "yAxis": "right"}]
                    ],
                    "view": "timeSeries",
                    "stacked": False,
                    "region": self.region,
                    "title": f"📦 {table_name.replace(self.stack_name + '-', '')}",
                    "period": 60,
                    "yAxis": {
                        "left": {
                            "label": "Capacity Units"
                        },
                        "right": {
                            "label": "Errors"
                        }
                    }
                }
            })
        
        return widgets


def main():
    parser = argparse.ArgumentParser(
        description='Create comprehensive CloudWatch dashboard for Stats & Leaderboards system'
    )
    parser.add_argument('--stack-name', required=True, help='CloudFormation stack name')
    parser.add_argument('--region', default='us-west-2', help='AWS region (default: us-west-2)')
    parser.add_argument('--profile', default='default', help='AWS profile name (default: default)')
    parser.add_argument('--force', action='store_true', help='Force recreate dashboard without prompting')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("COMPREHENSIVE CLOUDWATCH DASHBOARD CREATOR")
    print("=" * 80)
    print(f"Stack Name: {args.stack_name}")
    print(f"Region: {args.region}")
    print(f"Profile: {args.profile}")
    print("=" * 80)
    
    # Create dashboard creator
    creator = ComprehensiveDashboardCreator(
        stack_name=args.stack_name,
        region=args.region,
        profile=args.profile
    )
    
    # Discover resources
    creator.discover_resources()
    
    # Check if dashboard exists
    dashboard_exists = creator.check_dashboard_exists()
    
    if dashboard_exists:
        print(f"\n⚠️  Dashboard already exists: {creator.dashboard_name}")
        
        if not args.force:
            response = input("\nDo you want to delete and recreate it? (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                print("\n❌ Aborted by user")
                sys.exit(0)
        
        print("\n🗑️  Deleting existing dashboard...")
        creator.delete_dashboard()
    
    # Create dashboard
    creator.create_dashboard()
    
    print("\n" + "=" * 80)
    print("✅ DASHBOARD CREATION COMPLETE")
    print("=" * 80)
    print(f"\nDashboard Name: {creator.dashboard_name}")
    print(f"Region: {args.region}")
    print(f"\nResources Monitored:")
    print(f"  - Lambda Functions: {len(creator.resources['lambda_functions'])}")
    print(f"  - DynamoDB Tables: {len(creator.resources['dynamodb_tables'])}")
    print(f"  - API Gateway: {creator.resources['api_gateway_name']}")
    print(f"  - MemoryDB Cluster: {creator.resources['memorydb_cluster']}")
    print("\n" + "=" * 80)


if __name__ == '__main__':
    main()
