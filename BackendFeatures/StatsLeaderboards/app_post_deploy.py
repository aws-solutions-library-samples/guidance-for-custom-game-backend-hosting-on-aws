# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import boto3
import textwrap
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
import time
import os
import hashlib

from aws_cdk import (
    App,
    Stack,
    StackProps,
    Duration,
    RemovalPolicy,
    CfnOutput,
    CustomResource,
    Tags,
    CfnTag,
    Size
)
from aws_cdk import (
    aws_lambda as lambda_,
    aws_cloudwatch as cloudwatch,
    aws_applicationautoscaling as autoscaling,
    aws_iam as iam,
    aws_logs as logs,
    aws_ssm as ssm,
    custom_resources as cr,
    aws_events as events,
    aws_events_targets as targets,
    aws_apigateway as apigw
)
from constructs import Construct


class EnhancedPostDeployResourceDiscovery:
    """Enhanced resource discovery for post-deployment configuration"""
    
    def __init__(self, region: str = None, account: str = None, context: Dict[str, Any] = None):
        self.region = region or os.environ.get('AWS_DEFAULT_REGION', os.environ.get('CDK_DEFAULT_REGION', 'us-west-2'))
        self.account = account or os.environ.get('CDK_DEFAULT_ACCOUNT')
        self.context = context or {}
        
        try:
            self.session = boto3.Session(region_name=self.region)
            self.lambda_client = self.session.client('lambda')
            self.cf_client = self.session.client('cloudformation')
            # Step Functions client removed — state machines no longer used in this project
            self.logs_client = self.session.client('logs')
            self.clients_available = True
            print(f"✅ AWS clients initialized for post-deploy discovery in region: {self.region}")
        except Exception as e:
            print(f"❌ Failed to initialize AWS clients: {e}")
            self.clients_available = False
    
    def discover_base_stack_resources(self, base_stack_name: str, resource_prefix: str, 
                                    debug_mode: bool = False) -> Dict[str, Any]:
        """Comprehensive discovery of base stack resources"""
        
        print("\n" + "="*100)
        print("🔍 POST-DEPLOY RESOURCE DISCOVERY")
        print("="*100)
        print(f"Base Stack: {base_stack_name}")
        print(f"Resource Prefix: {resource_prefix}")
        print("="*100)
        
        if not self.clients_available:
            return self._get_empty_discovery_results()
        
        discovery_results = {
            'base_stack_outputs': self._discover_base_stack_outputs(base_stack_name, debug_mode),
            'lambda_functions': self._discover_lambda_functions(resource_prefix, debug_mode),
            # Step Functions discovery removed — state machines no longer used in this project
            'existing_log_groups': self._discover_existing_log_groups(resource_prefix, debug_mode),
            'provisioned_concurrency': self._discover_provisioned_concurrency(resource_prefix, debug_mode),
            'recommendations': {}
        }
        
        # Generate recommendations
        discovery_results['recommendations'] = self._generate_post_deploy_recommendations(discovery_results)
        
        # Print summary
        self._print_discovery_summary(discovery_results)
        
        return discovery_results

    def _discover_existing_api_gateway(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing API Gateway resources"""
        
        print(f"🔍 Searching for existing REST API resources with prefix: {resource_prefix}")
        
        results = {
            'api_found': False,
            'api_id': None,
            'api_endpoint': None,
            'stages': [],
            'resources': []
        }
        
        try:
            # Use API Gateway v1 client for REST APIs
            apigw_client = boto3.client('apigateway', region_name=self.region)
            
            # List all REST APIs
            response = apigw_client.get_rest_apis()
            
            for api in response.get('items', []):
                api_name = api.get('name', '')
                
                # Check if API name matches our prefix
                if resource_prefix in api_name:
                    api_id = api['id']
                    
                    print(f"   ✅ Found existing REST API: {api_name} ({api_id})")
                    
                    # Get stages
                    try:
                        stages_response = apigw_client.get_stages(restApiId=api_id)
                        stages = stages_response.get('item', [])
                        
                        for stage in stages:
                            stage_name = stage.get('stageName', '')
                            print(f"      - Stage: {stage_name}")
                            results['stages'].append({
                                'name': stage_name,
                                'deployment_id': stage.get('deploymentId', ''),
                                'variables': stage.get('variables', {})
                            })
                    except Exception as e:
                        print(f"      ⚠️ Error getting stages: {e}")
                    
                    # Store API details
                    results['api_found'] = True
                    results['api_id'] = api_id
                    results['api_endpoint'] = f"https://{api_id}.execute-api.{self.region}.amazonaws.com"
                    results['api_name'] = api_name
                    
                    # Only process the first matching API
                    break
            
            if not results['api_found']:
                print(f"   🔍 No REST API found matching prefix {resource_prefix}")
                
        except Exception as e:
            print(f"   ⚠️ Error searching for REST API: {e}")
        
        return results

    def _discover_base_stack_outputs(self, base_stack_name: str, debug_mode: bool = False) -> Dict[str, Any]:
        """Discover base stack outputs with detailed analysis"""
        
        print("\n" + "-"*80)
        print("🔍 BASE STACK OUTPUTS DISCOVERY")
        print("-"*80)
        
        analysis = {
            'stack_exists': False,
            'outputs': {},
            'stack_status': '',
            'required_outputs_present': False,
            'missing_outputs': []
        }
        
        required_outputs = [
            'ApiEndpoint', 'ApiId', 'MemoryDBEndpoint', 'ConfigTableName', 'StatsTableName', 'LambdaRoleArn', 'VpcId'
        ]

        try:
            print(f"🔎 Checking CloudFormation stack: {base_stack_name}")
            response = self.cf_client.describe_stacks(StackName=base_stack_name)
            
            if response['Stacks']:
                stack = response['Stacks'][0]
                analysis['stack_exists'] = True
                analysis['stack_status'] = stack['StackStatus']
                
                print(f"   ✅ Stack found with status: {stack['StackStatus']}")
                
                if stack['StackStatus'] != 'CREATE_COMPLETE' and stack['StackStatus'] != 'UPDATE_COMPLETE':
                    print(f"   ⚠️  Stack status is not complete: {stack['StackStatus']}")
                
                # Get outputs
                stack_outputs = stack.get('Outputs', [])
                for output in stack_outputs:
                    analysis['outputs'][output['OutputKey']] = output['OutputValue']
                    if debug_mode:
                        print(f"   📋 {output['OutputKey']}: {output['OutputValue']}")
                
                # Check required outputs
                missing_outputs = []
                for required_output in required_outputs:
                    if required_output not in analysis['outputs']:
                        missing_outputs.append(required_output)
                
                analysis['missing_outputs'] = missing_outputs
                analysis['required_outputs_present'] = len(missing_outputs) == 0
                
                if missing_outputs:
                    print(f"   ❌ Missing required outputs: {missing_outputs}")
                else:
                    print(f"   ✅ All required outputs present ({len(analysis['outputs'])} total)")
            
        except Exception as e:
            print(f"   ❌ Error discovering base stack: {e}")
            if debug_mode:
                import traceback
                traceback.print_exc()
        
        return analysis
    
    def _discover_lambda_functions(self, resource_prefix: str, debug_mode: bool = False) -> Dict[str, Any]:
        """Discover Lambda functions with detailed analysis"""
        
        print("\n" + "-"*80)
        print("🔍 LAMBDA FUNCTIONS DISCOVERY")
        print("-"*80)
        
        expected_functions = [
            f"{resource_prefix}-backend-authorizer",
            f"{resource_prefix}-developer-registration",
            f"{resource_prefix}-leaderboards-config",
            f"{resource_prefix}-reset-leaderboard",
            f"{resource_prefix}-store-stats",
            f"{resource_prefix}-get-player-stats",
            f"{resource_prefix}-get-leaderboard-scores",
            f"{resource_prefix}-get-player-lb-standing",
            f"{resource_prefix}-rebuild-leaderboard",
            f"{resource_prefix}-batch-store-stats"
        ]
        
        analysis = {
            'expected_count': len(expected_functions),
            'found_functions': {},
            'missing_functions': [],
            'function_details': {},
            'all_functions_present': False
        }
        
        print(f"🔎 Searching for {len(expected_functions)} expected Lambda functions")
        
        for func_name in expected_functions:
            try:
                response = self.lambda_client.get_function(FunctionName=func_name)
                config = response['Configuration']
                
                analysis['found_functions'][func_name] = config['FunctionArn']
                analysis['function_details'][func_name] = {
                    'arn': config['FunctionArn'],
                    'runtime': config['Runtime'],
                    'memory_size': config['MemorySize'],
                    'timeout': config['Timeout'],
                    'last_modified': config['LastModified'],
                    'state': config['State'],
                    'layers': len(config.get('Layers', [])),
                    'environment_vars': len(config.get('Environment', {}).get('Variables', {})),
                    'vpc_config': 'VpcConfig' in config and config['VpcConfig'].get('VpcId') is not None
                }
                
                print(f"   ✅ {func_name}")
                if debug_mode:
                    details = analysis['function_details'][func_name]
                    print(f"      Runtime: {details['runtime']}, Memory: {details['memory_size']}MB")
                    print(f"      Timeout: {details['timeout']}s, Layers: {details['layers']}")
                    print(f"      VPC: {'Yes' if details['vpc_config'] else 'No'}")
                
            except self.lambda_client.exceptions.ResourceNotFoundException:
                analysis['missing_functions'].append(func_name)
                print(f"   ❌ {func_name} - NOT FOUND")
            except Exception as e:
                analysis['missing_functions'].append(func_name)
                print(f"   ❌ {func_name} - ERROR: {e}")
        
        analysis['all_functions_present'] = len(analysis['missing_functions']) == 0
        
        print(f"📊 SUMMARY: {len(analysis['found_functions'])}/{analysis['expected_count']} functions found")
        if analysis['missing_functions']:
            print(f"   Missing: {analysis['missing_functions']}")
        
        return analysis
    
    def _discover_existing_log_groups(self, resource_prefix: str, debug_mode: bool = False) -> Dict[str, Any]:
        """Discover existing CloudWatch log groups"""
        
        print("\n" + "-"*80)
        print("🔍 CLOUDWATCH LOG GROUPS DISCOVERY")
        print("-"*80)
        
        analysis = {
            'lambda_log_groups': {},
            'total_found': 0
        }
        
        try:
            # Check Lambda log groups
            lambda_log_prefix = f"/aws/lambda/{resource_prefix}"
            response = self.logs_client.describe_log_groups(
                logGroupNamePrefix=lambda_log_prefix
            )
            
            for log_group in response['logGroups']:
                log_group_name = log_group['logGroupName']
                analysis['lambda_log_groups'][log_group_name] = {
                    'retention_days': log_group.get('retentionInDays', 'Never expire'),
                    'size_bytes': log_group.get('storedBytes', 0),
                    'creation_time': log_group.get('creationTime', 0)
                }
                analysis['total_found'] += 1
                print(f"   ✅ {log_group_name}")
                if debug_mode:
                    retention = analysis['lambda_log_groups'][log_group_name]['retention_days']
                    print(f"      Retention: {retention}")
            
            print(f"📊 SUMMARY: {analysis['total_found']} log groups found")
            
        except Exception as e:
            print(f"   ❌ Error discovering log groups: {e}")
            if debug_mode:
                import traceback
                traceback.print_exc()
        
        return analysis

    def _discover_provisioned_concurrency(self, resource_prefix: str, debug_mode: bool = False) -> Dict[str, Any]:
        """Discover existing provisioned concurrency configurations - fixed qualifier handling"""
        
        print("\n" + "-"*80)
        print("🔍 PROVISIONED CONCURRENCY DISCOVERY")
        print("-"*80)
        
        critical_functions = [
            f"{resource_prefix}-store-stats",
            f"{resource_prefix}-get-leaderboard-scores", 
            f"{resource_prefix}-get-player-lb-standing"
        ]
        
        analysis = {
            'functions_with_pc': {},
            'functions_without_pc': [],
            'total_provisioned_capacity': 0
        }
        
        for func_name in critical_functions:
            try:
                response = self.lambda_client.list_provisioned_concurrency_configs(
                    FunctionName=func_name
                )
                
                if response['ProvisionedConcurrencyConfigs']:
                    for config in response['ProvisionedConcurrencyConfigs']:
                        # Handle missing 'Qualifier' key gracefully
                        qualifier = config.get('Qualifier', '$LATEST')
                        allocated_concurrency = config.get('AllocatedConcurrency', 0)
                        available_concurrency = config.get('AvailableConcurrency', 0)
                        status = config.get('Status', 'UNKNOWN')
                        
                        analysis['functions_with_pc'][func_name] = {
                            'qualifier': qualifier,
                            'allocated_concurrency': allocated_concurrency,
                            'available_concurrency': available_concurrency,
                            'status': status
                        }
                        analysis['total_provisioned_capacity'] += allocated_concurrency
                        print(f"   ✅ {func_name} - {allocated_concurrency} units (qualifier: {qualifier})")
                        if debug_mode:
                            print(f"      Qualifier: {qualifier}")
                            print(f"      Status: {status}")
                            print(f"      Available: {available_concurrency}")
                else:
                    analysis['functions_without_pc'].append(func_name)
                    print(f"   ❌ {func_name} - No provisioned concurrency")
                    
            except self.lambda_client.exceptions.ResourceNotFoundException:
                analysis['functions_without_pc'].append(func_name)
                print(f"   ❌ {func_name} - Function not found")
            except KeyError as e:
                # ✅ Handle the specific 'Qualifier' key error (this is EXPECTED)
                analysis['functions_without_pc'].append(func_name)
                print(f"   ❌ {func_name} - No provisioned concurrency configured (this is expected)")
                if debug_mode:
                    print(f"      KeyError for {str(e)} is normal when provisioned concurrency is not configured")
            except Exception as e:
                analysis['functions_without_pc'].append(func_name)
                print(f"   ❌ {func_name} - Error: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
        
        print(f"📊 SUMMARY: {len(analysis['functions_with_pc'])}/{len(critical_functions)} functions have provisioned concurrency")
        print(f"   Total provisioned capacity: {analysis['total_provisioned_capacity']} units")
        
        return analysis
    
    def _generate_post_deploy_recommendations(self, discovery_results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate comprehensive recommendations for post-deployment"""
        
        recommendations = {
            'deployment_strategy': 'full',  # 'full', 'partial', 'skip'
            'required_actions': [],
            'optional_actions': [],
            'risk_level': 'low',
            'estimated_time': '10-15 minutes'
        }
        
        base_stack = discovery_results['base_stack_outputs']
        lambda_functions = discovery_results['lambda_functions']

        # Check if base deployment is ready
        if not base_stack['stack_exists'] or not base_stack['required_outputs_present']:
            recommendations['deployment_strategy'] = 'skip'
            recommendations['required_actions'].append('Base stack must be deployed successfully first')
            recommendations['risk_level'] = 'high'
            return recommendations
        
        # Check if Lambda functions are ready
        if not lambda_functions['all_functions_present']:
            recommendations['deployment_strategy'] = 'partial'
            recommendations['required_actions'].append('Some Lambda functions are missing - limited functionality')
            recommendations['risk_level'] = 'medium'
        
        # Generate specific recommendations
        if lambda_functions['all_functions_present']:
            recommendations['optional_actions'].extend([
                'Configure provisioned concurrency for critical functions',
                'Set up comprehensive monitoring and alarms',
                'Configure Lambda Insights and X-Ray tracing'
            ])
        
        return recommendations
    
    def _print_discovery_summary(self, discovery_results: Dict[str, Any]):
        """Print comprehensive discovery summary"""
        
        print("\n" + "="*100)
        print("📋 POST-DEPLOY DISCOVERY SUMMARY")
        print("="*100)
        
        base_stack = discovery_results['base_stack_outputs']
        lambda_functions = discovery_results['lambda_functions']
        recommendations = discovery_results['recommendations']
        
        print(f"🏗️  BASE STACK STATUS:")
        print(f"   Exists: {'✅ Yes' if base_stack['stack_exists'] else '❌ No'}")
        if base_stack['stack_exists']:
            print(f"   Status: {base_stack['stack_status']}")
            print(f"   Required Outputs: {'✅ Complete' if base_stack['required_outputs_present'] else '❌ Missing'}")
            if base_stack['missing_outputs']:
                print(f"   Missing: {base_stack['missing_outputs']}")
        
        print(f"\n🔧 LAMBDA FUNCTIONS STATUS:")
        print(f"   Expected: {lambda_functions['expected_count']}")
        print(f"   Found: {len(lambda_functions['found_functions'])}")
        print(f"   Status: {'✅ Complete' if lambda_functions['all_functions_present'] else '❌ Incomplete'}")
        
        print(f"\n🎯 DEPLOYMENT RECOMMENDATION:")
        print(f"   Strategy: {recommendations['deployment_strategy'].upper()}")
        print(f"   Risk Level: {recommendations['risk_level'].upper()}")
        print(f"   Estimated Time: {recommendations['estimated_time']}")
        
        if recommendations['required_actions']:
            print(f"\n✅ REQUIRED ACTIONS:")
            for action in recommendations['required_actions']:
                print(f"   • {action}")
        
        if recommendations['optional_actions']:
            print(f"\n💡 OPTIONAL ENHANCEMENTS:")
            for action in recommendations['optional_actions']:
                print(f"   • {action}")
        
        print("="*100)
    
    def _get_empty_discovery_results(self) -> Dict[str, Any]:
        """Return empty discovery results when clients are not available"""
        return {
            'base_stack_outputs': {'stack_exists': False, 'outputs': {}, 'required_outputs_present': False},
            'lambda_functions': {'all_functions_present': False, 'found_functions': {}, 'missing_functions': []},
            'existing_log_groups': {'total_found': 0},
            'provisioned_concurrency': {'functions_with_pc': {}, 'total_provisioned_capacity': 0},
            'recommendations': {'deployment_strategy': 'skip', 'required_actions': ['AWS clients not available']}
        }


class GameStatsLeaderboardsMonitoringStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, base_stack_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Set default region if not specified
        self.default_region = "us-west-2"
        if not self.region:
            # If region is not set in the stack, try to get it from environment
            self.region = os.environ.get('AWS_DEFAULT_REGION', os.environ.get('CDK_DEFAULT_REGION', self.default_region))

        # Environment configuration from context
        environment = self.node.try_get_context("environment") or "dev"
        service_name = "game-statsleaderboards"
        resource_prefix = f"{service_name}-{environment}"
        
        # Get deployment mode settings with proper string handling
        force_create_new = self.node.try_get_context("force_create_new")
        skip_resource_discovery = self.node.try_get_context("skip_resource_discovery")
        skip_advanced_features = self.node.try_get_context("skip_advanced_features")
        skip_provisioned_concurrency = self.node.try_get_context("skip_provisioned_concurrency")
        skip_lambda_insights = self.node.try_get_context("skip_lambda_insights")
        skip_monitoring = self.node.try_get_context("skip_monitoring")
        enable_debug_mode = self.node.try_get_context("enable_debug_mode")
        
        # Convert string values to boolean (CDK context passes strings)
        def to_bool(value):
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return bool(value) if value is not None else None

        force_create_new = to_bool(force_create_new)
        skip_resource_discovery = to_bool(skip_resource_discovery)
        skip_advanced_features = to_bool(skip_advanced_features)
        skip_provisioned_concurrency = to_bool(skip_provisioned_concurrency)
        skip_lambda_insights = to_bool(skip_lambda_insights)
        skip_monitoring = to_bool(skip_monitoring)
        enable_debug_mode = to_bool(enable_debug_mode)
        
        # Default values
        if force_create_new is None:
            force_create_new = False
        if skip_resource_discovery is None:
            skip_resource_discovery = False
        if skip_advanced_features is None:
            skip_advanced_features = False
        if skip_provisioned_concurrency is None:
            skip_provisioned_concurrency = False
        if skip_lambda_insights is None:
            skip_lambda_insights = False
        if skip_monitoring is None:
            skip_monitoring = False
        if enable_debug_mode is None:
            enable_debug_mode = False
        
        # Set default region if not specified
        self.default_region = "us-west-2"
        
        # Add common tags
        Tags.of(self).add("Environment", environment)
        Tags.of(self).add("Service", service_name)
        Tags.of(self).add("ManagedBy", "CDK")
        Tags.of(self).add("Component", "PostDeploy")
        Tags.of(self).add("BaseStack", base_stack_name)
        Tags.of(self).add("Application", "GameStatsLeaderboards")

        print(f"\n🚀 POST-DEPLOY CONFIGURATION")
        print(f"   Environment: {environment}")
        print(f"   Base Stack: {base_stack_name}")
        print(f"   Region: {self.region or self.default_region}")
        print(f"   Force Create New: {force_create_new}")
        print(f"   Skip Resource Discovery: {skip_resource_discovery}")
        print(f"   Skip Advanced Features: {skip_advanced_features}")
        print(f"   Skip Provisioned Concurrency: {skip_provisioned_concurrency}")
        print(f"   Skip Lambda Insights: {skip_lambda_insights}")
        print(f"   Skip Monitoring: {skip_monitoring}")
        print(f"   Debug Mode: {enable_debug_mode}")

        # Get resource decisions based on discovery
        resource_decisions = self._get_post_deploy_decisions(
            base_stack_name, resource_prefix, 
            skip_resource_discovery, enable_debug_mode
        )

        # Validate we have required resources before proceeding
        if not self._validate_deployment_readiness(resource_decisions, enable_debug_mode):
            print("❌ ERROR: Base stack resources not ready for post-deployment configuration")
            self._create_minimal_outputs(resource_prefix, "Failed - Base stack not ready")
            return

        print("✅ Base stack resources validated - proceeding with post-deployment")

        # Get base stack outputs and Lambda function ARNs
        base_stack_outputs = resource_decisions['discovery_results']['base_stack_outputs']['outputs']
        lambda_function_arns = resource_decisions['discovery_results']['lambda_functions']['found_functions']

        # Configure advanced Lambda properties
        if not skip_advanced_features:
            try:
                self._configure_lambda_properties(resource_prefix, lambda_function_arns, enable_debug_mode)
            except Exception as e:
                print(f"⚠️  Warning: Could not configure Lambda properties: {e}")
                if enable_debug_mode:
                    import traceback
                    traceback.print_exc()

        # Create dedicated log groups for Lambda functions
        try:
            self._create_lambda_log_groups(resource_prefix, lambda_function_arns, enable_debug_mode)
        except Exception as e:
            print(f"⚠️  Warning: Could not create log groups: {e}")
            if enable_debug_mode:
                import traceback
                traceback.print_exc()

        # Create provisioned concurrency for critical functions
        if not skip_provisioned_concurrency and not skip_advanced_features:
            try:
                self._create_provisioned_concurrency(resource_prefix, lambda_function_arns, enable_debug_mode)
            except Exception as e:
                print(f"⚠️  Warning: Could not create provisioned concurrency: {e}")
                if enable_debug_mode:
                    import traceback
                    traceback.print_exc()
        else:
            print("⏭️  Skipping provisioned concurrency creation")

        # Configure Lambda insights and X-Ray tracing
        if not skip_lambda_insights and not skip_advanced_features:
            try:
                self._configure_lambda_insights_and_tracing(resource_prefix, lambda_function_arns, enable_debug_mode)
            except Exception as e:
                print(f"⚠️  Warning: Could not configure Lambda Insights: {e}")
                if enable_debug_mode:
                    import traceback
                    traceback.print_exc()
        else:
            print("⏭️  Skipping Lambda Insights configuration")

        # Create comprehensive alarms
        if not skip_monitoring:
            try:
                self._create_lambda_alarms(resource_prefix, lambda_function_arns, base_stack_outputs, enable_debug_mode)
                self._create_infrastructure_alarms(resource_prefix, base_stack_outputs, enable_debug_mode)
            except Exception as e:
                print(f"⚠️  Warning: Could not create alarms: {e}")
                if enable_debug_mode:
                    import traceback
                    traceback.print_exc()
        else:
            print("⏭️  Skipping monitoring/alarms creation")

        # Create enhanced dashboard
        if not skip_monitoring:
            try:
                self._create_enhanced_dashboard(
                    resource_prefix, base_stack_outputs, enable_debug_mode
                )
            except Exception as e:
                print(f"⚠️  Warning: Could not create dashboard: {e}")
                if enable_debug_mode:
                    import traceback
                    traceback.print_exc()
        else:
            print("⏭️  Skipping dashboard creation")

        # Create outputs
        self._create_outputs(resource_prefix)

        print("✅ Post-deployment configuration completed successfully")

    def _get_post_deploy_decisions(self, base_stack_name: str, resource_prefix: str, 
                                 skip_resource_discovery: bool, 
                                 enable_debug_mode: bool) -> Dict[str, Any]:
        """Get post-deployment decisions based on discovery"""
        
        if skip_resource_discovery:
            print("🆕 Skipping resource discovery - proceeding with deployment")
            return {
                'proceed_with_deployment': True,
                'discovery_results': None
            }
        
        # Perform discovery
        environment = self.node.try_get_context('environment') or 'dev'
        context = {
            "environment": environment,
            "environments": self.node.try_get_context("environments") or {}
        }
        discovery = EnhancedPostDeployResourceDiscovery(self.region, self.account, context)
        discovery_results = discovery.discover_base_stack_resources(base_stack_name, resource_prefix, enable_debug_mode)
        
        # Make decisions based on discovery
        recommendations = discovery_results['recommendations']
        
        decisions = {
            'proceed_with_deployment': recommendations['deployment_strategy'] != 'skip',
            'deployment_strategy': recommendations['deployment_strategy'],
            'discovery_results': discovery_results
        }
        
        print(f"🤖 Auto-proceeding with {recommendations['deployment_strategy']} deployment")
        
        return decisions

    def _validate_deployment_readiness(self, resource_decisions: Dict[str, Any], debug_mode: bool = False) -> bool:
        """Validate that deployment can proceed"""
        
        if not resource_decisions['proceed_with_deployment']:
            return False
        
        if not resource_decisions.get('discovery_results'):
            return True  # Skip validation if no discovery was performed
        
        discovery_results = resource_decisions['discovery_results']
        base_stack = discovery_results['base_stack_outputs']
        lambda_functions = discovery_results['lambda_functions']
        
        # Check base stack
        if not base_stack['stack_exists'] or not base_stack['required_outputs_present']:
            if debug_mode:
                print(f"   Base stack validation failed:")
                print(f"   - Exists: {base_stack['stack_exists']}")
                print(f"   - Required outputs: {base_stack['required_outputs_present']}")
                if base_stack['missing_outputs']:
                    print(f"   - Missing outputs: {base_stack['missing_outputs']}")
            return False
        
        # Check critical Lambda functions
        environment = self.node.try_get_context('environment') or 'dev'
        required_functions = [
            f"game-statsleaderboards-{environment}-reset-leaderboard",
            f"game-statsleaderboards-{environment}-rebuild-leaderboard"
        ]
        
        for func_name in required_functions:
            if func_name not in lambda_functions['found_functions']:
                if debug_mode:
                    print(f"   Missing critical function: {func_name}")
                return False
        
        return True

    def _create_minimal_outputs(self, resource_prefix: str, status_message: str):
        """Create minimal outputs when deployment fails"""
        CfnOutput(
            self, "PostDeployStatus",
            value=status_message,
            description="Post-deployment configuration status",
            export_name=f"{self.stack_name}-PostDeployStatus"
        )
        
        CfnOutput(
            self, "PostDeployTimestamp",
            value=datetime.now(timezone.utc).isoformat(),
            description="Post-deployment attempt timestamp",
            export_name=f"{self.stack_name}-PostDeployTimestamp"
        )

    def _get_boto3_session(self):
        """Get boto3 session with proper region configuration"""
        region = self.region if hasattr(self, 'region') and self.region else self.default_region
        return boto3.Session(region_name=region)

    # Step Functions state machines have been removed from this project.
    # Reset and rebuild operations use Lambda self-invoke relay for long-running
    # operations, and leaderboard expiry is handled passively via HTTP 423 rejection
    # at write time plus MemoryDB TTL auto-deletion.
    # See Claude_Findings.md Section 5 for the full rationale.

    def _configure_lambda_properties(self, resource_prefix: str, lambda_function_arns: Dict[str, str], debug_mode: bool = False):
        """
        Configure Lambda function properties with correct API calls.
        
        NOTE: Reserved concurrency has been REMOVED to allow all functions to share
        the unreserved concurrency pool (5000 concurrent executions by default).
        This provides better flexibility and resource utilization during load tests.
        """
        
        session = self._get_boto3_session()
        lambda_client = session.client('lambda')  # This creates the boto3 client, not CDK client
        
        print("\n" + "="*80)
        print("⚙️  CONFIGURING LAMBDA PROPERTIES")
        print("="*80)
        print("📌 Removing reserved concurrency to use shared unreserved pool")
        print("")
        
        # Function-specific configurations (retry attempts only, NO reserved concurrency)
        function_configs = {
            f"{resource_prefix}-backend-authorizer": {
                "retry_attempts": 0
            },
            f"{resource_prefix}-developer-registration": {
                "retry_attempts": 0
            },
            f"{resource_prefix}-leaderboards-config": {
                "retry_attempts": 2
            },
            f"{resource_prefix}-reset-leaderboard": {
                "retry_attempts": 2
            },
            f"{resource_prefix}-store-stats": {
                "retry_attempts": 2
            },
            f"{resource_prefix}-get-player-stats": {
                "retry_attempts": 1
            },
            f"{resource_prefix}-get-leaderboard-scores": {
                "retry_attempts": 1
            },
            f"{resource_prefix}-get-player-lb-standing": {
                "retry_attempts": 1
            },
            f"{resource_prefix}-rebuild-leaderboard": {
                "retry_attempts": 2
            },
            f"{resource_prefix}-batch-store-stats": {
                "retry_attempts": 2
            }
        }
        
        for func_name, func_arn in lambda_function_arns.items():
            if func_name not in function_configs:
                continue
                
            config = function_configs[func_name]
            
            try:
                # REMOVE reserved concurrency (if it exists) to use unreserved pool
                try:
                    lambda_client.delete_function_concurrency(
                        FunctionName=func_name
                    )
                    print(f"   ✅ Removed reserved concurrency for {func_name} (now uses unreserved pool)")
                except lambda_client.exceptions.ResourceNotFoundException:
                    # Function doesn't have reserved concurrency - that's fine
                    print(f"   ✅ {func_name} already using unreserved pool (no reserved concurrency)")
                except Exception as e:
                    # Only warn if it's not a "no concurrency configured" error
                    if "ResourceNotFoundException" not in str(e):
                        print(f"   ⚠️  Warning: Could not remove reserved concurrency for {func_name}: {e}")
                
                # Configure event invoke config (retry attempts)
                if 'retry_attempts' in config:
                    try:
                        lambda_client.put_function_event_invoke_config(
                            FunctionName=func_name,
                            MaximumRetryAttempts=config['retry_attempts']
                        )
                        print(f"   ✅ Set event invoke config for {func_name}: retry_attempts={config['retry_attempts']}")
                        
                    except Exception as e:
                        print(f"   ⚠️  Warning: Could not set event invoke config for {func_name}: {e}")
                        if debug_mode:
                            import traceback
                            traceback.print_exc()
                
            except Exception as e:
                print(f"   ⚠️  Warning: Could not configure properties for {func_name}: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
        
        print("")
        print("="*80)
        print("✅ Lambda properties configured - all functions use unreserved pool")
        print("="*80)

    def _create_lambda_log_groups(self, resource_prefix: str, lambda_function_arns: Dict[str, str], debug_mode: bool = False):
        """Create dedicated log groups for Lambda functions"""
        
        session = self._get_boto3_session()
        logs_client = session.client('logs')
        
        for func_name in lambda_function_arns.keys():
            log_group_name = f"/aws/lambda/{func_name}"
            
            try:
                logs_client.create_log_group(
                    logGroupName=log_group_name,
                    tags={
                        'Environment': self.node.try_get_context("environment") or "dev",
                        'Service': 'game-statsleaderboards',
                        'ManagedBy': 'CDK-PostDeploy',
                        'Function': func_name
                    }
                )
                
                logs_client.put_retention_policy(
                    logGroupName=log_group_name,
                    retentionInDays=7
                )
                
                print(f"   ✅ Created log group for {func_name}: {log_group_name}")
                if debug_mode:
                    print(f"      Retention: 7 days")
                
            except logs_client.exceptions.ResourceAlreadyExistsException:
                try:
                    logs_client.put_retention_policy(
                        logGroupName=log_group_name,
                        retentionInDays=7
                    )
                    print(f"   ✅ Updated retention for existing log group: {log_group_name}")
                except Exception as e:
                    print(f"   ⚠️  Warning: Could not set retention for {log_group_name}: {e}")
                    if debug_mode:
                        import traceback
                        traceback.print_exc()
                    
            except Exception as e:
                print(f"   ⚠️  Warning: Could not create log group for {func_name}: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()

    def _configure_lambda_insights_and_tracing(self, resource_prefix: str, lambda_function_arns: Dict[str, str], debug_mode: bool = False):
        """Configure Lambda Insights and X-Ray tracing"""
        
        session = self._get_boto3_session()
        lambda_client = session.client('lambda')
        
        region = session.region_name or self.default_region
        insights_layer_arn = f"arn:aws:lambda:{region}:580247275435:layer:LambdaInsightsExtension:21"
        
        if debug_mode:
            print(f"   🔧 Using Lambda Insights layer: {insights_layer_arn}")
        
        for func_name, func_arn in lambda_function_arns.items():
            try:
                response = lambda_client.get_function(FunctionName=func_name)
                current_config = response['Configuration']
                
                current_layers = [layer['Arn'] for layer in current_config.get('Layers', [])]
                
                if not any('LambdaInsightsExtension' in layer for layer in current_layers):
                    current_layers.append(insights_layer_arn)
                    print(f"   ✅ Adding Lambda Insights layer to {func_name}")
                
                update_params = {
                    'FunctionName': func_name,
                    'Layers': current_layers,
                    'TracingConfig': {
                        'Mode': 'Active'
                    }
                }
                
                lambda_client.update_function_configuration(**update_params)
                print(f"   ✅ Enabled Lambda Insights and X-Ray tracing for {func_name}")
                
                if debug_mode:
                    print(f"      Layers: {len(current_layers)}")
                    print(f"      Tracing: Active")
                
                time.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                print(f"   ⚠️  Warning: Could not configure insights/tracing for {func_name}: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()

    def _create_provisioned_concurrency(self, resource_prefix: str, lambda_function_arns: Dict[str, str], debug_mode: bool = False):
        """Create provisioned concurrency for critical functions - fixed alias issue"""
        
        critical_functions = [
            f"{resource_prefix}-store-stats",
            f"{resource_prefix}-get-leaderboard-scores", 
            f"{resource_prefix}-get-player-lb-standing"
        ]
        
        session = self._get_boto3_session()
        lambda_client = session.client('lambda')
        autoscaling_client = session.client('application-autoscaling')
        
        for func_name in critical_functions:
            if func_name not in lambda_function_arns:
                if debug_mode:
                    print(f"   ⚠️  Function {func_name} not found for provisioned concurrency")
                continue
                
            try:
                # First publish a version of the function
                try:
                    version_response = lambda_client.publish_version(
                        FunctionName=func_name,
                        Description=f"Version for provisioned concurrency - {datetime.now(timezone.utc).isoformat()}"
                    )
                    version_number = version_response['Version']
                    if debug_mode:
                        print(f"   ✅ Published version {version_number} for {func_name}")
                except Exception as e:
                    print(f"   ⚠️  Warning: Could not publish version for {func_name}: {e}")
                    continue
                
                # Set provisioned concurrency directly on the VERSION (not alias)
                try:
                    lambda_client.put_provisioned_concurrency_config(
                        FunctionName=func_name,
                        Qualifier=version_number,  # Use version number directly
                        ProvisionedConcurrentExecutions=5
                    )
                    print(f"   ✅ Set provisioned concurrency for {func_name} version {version_number}")
                    if debug_mode:
                        print(f"      Provisioned concurrency: 5 on version {version_number}")
                except lambda_client.exceptions.ResourceConflictException:
                    print(f"   ℹ️  Provisioned concurrency already exists for {func_name}")

                # Set up auto-scaling on the version
                resource_id = f"function:{func_name}:{version_number}"
                
                try:
                    autoscaling_client.register_scalable_target(
                        ServiceNamespace='lambda',
                        ResourceId=resource_id,
                        ScalableDimension='lambda:function:ProvisionedConcurrency',
                        MinCapacity=5,
                        MaxCapacity=50
                    )
                    
                    autoscaling_client.put_scaling_policy(
                        PolicyName=f"{func_name}-v{version_number}-scaling-policy",
                        ServiceNamespace='lambda',
                        ResourceId=resource_id,
                        ScalableDimension='lambda:function:ProvisionedConcurrency',
                        PolicyType='TargetTrackingScaling',
                        TargetTrackingScalingPolicyConfiguration={
                            'TargetValue': 0.7,
                            'PredefinedMetricSpecification': {
                                'PredefinedMetricType': 'LambdaProvisionedConcurrencyUtilization'
                            },
                            'ScaleOutCooldown': 300,
                            'ScaleInCooldown': 300
                        }
                    )
                    print(f"   ✅ Created auto-scaling for {func_name}")
                    if debug_mode:
                        print(f"      Min capacity: 5, Max capacity: 50, Target: 70%")
                    
                except autoscaling_client.exceptions.ValidationException as e:
                    if "already exists" not in str(e):
                        print(f"   ⚠️  Warning: Could not create auto-scaling for {func_name}: {e}")
                        if debug_mode:
                            import traceback
                            traceback.print_exc()
                
            except Exception as e:
                print(f"   ⚠️  Warning: Could not set up provisioned concurrency for {func_name}: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()

    def _create_lambda_alarms(self, resource_prefix: str, lambda_function_arns: Dict[str, str], base_outputs: Dict[str, str], debug_mode: bool = False):
        """Create comprehensive Lambda function alarms"""
        
        alarm_count = 0
        
        for func_name, func_arn in lambda_function_arns.items():
            try:
                # Error rate alarm
                cloudwatch.Alarm(
                    self, f"{func_name}-errors",
                    metric=cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Errors",
                        dimensions_map={"FunctionName": func_name},
                        statistic="Sum"
                    ),
                    threshold=10,
                    evaluation_periods=2,
                    alarm_description=f"High error rate for {func_name}",
                    alarm_name=f"{func_name}-errors",
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING
                )
                
                # Duration alarm
                cloudwatch.Alarm(
                    self, f"{func_name}-duration",
                    metric=cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Duration",
                        dimensions_map={"FunctionName": func_name},
                        statistic="Average"
                    ),
                    threshold=25000,
                    evaluation_periods=2,
                    alarm_description=f"High duration for {func_name}",
                    alarm_name=f"{func_name}-duration",
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING
                )
                
                # Throttle alarm
                cloudwatch.Alarm(
                    self, f"{func_name}-throttles",
                    metric=cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Throttles",
                        dimensions_map={"FunctionName": func_name},
                        statistic="Sum"
                    ),
                    threshold=5,
                    evaluation_periods=1,
                    alarm_description=f"Function throttling for {func_name}",
                    alarm_name=f"{func_name}-throttles",
                    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING
                )
                
                alarm_count += 3
                
                if debug_mode:
                    print(f"   ✅ Created 3 alarms for {func_name}")
                
            except Exception as e:
                print(f"   ⚠️  Warning: Could not create alarms for {func_name}: {e}")
                if debug_mode:
                    import traceback
                    traceback.print_exc()
        
        print(f"✅ Created {alarm_count} Lambda function alarms")

    def _create_infrastructure_alarms(self, resource_prefix: str, base_outputs: Dict[str, str], debug_mode: bool = False):
        """Create infrastructure alarms"""
        
        memorydb_endpoint = base_outputs.get('MemoryDBEndpoint', '')
        if memorydb_endpoint:
            # Extract cluster name from endpoint
            try:
                cluster_name = memorydb_endpoint.split('.')[1]  # Get the second part
                if debug_mode:
                    print(f"   🔧 Extracted cluster name: {cluster_name} from endpoint: {memorydb_endpoint}")
            except (IndexError, AttributeError):
                # Fallback to constructed name
                cluster_name = f"{resource_prefix}-cluster"
                if debug_mode:
                    print(f"   ⚠️  Could not extract cluster name from endpoint, using fallback: {cluster_name}")
        else:
            cluster_name = f"{resource_prefix}-cluster"
            if debug_mode:
                print(f"   ⚠️  No MemoryDB endpoint found, using fallback cluster name: {cluster_name}")        

        try:
            cloudwatch.Alarm(
                self, f"{resource_prefix}-memorydb-cpu",
                metric=cloudwatch.Metric(
                    namespace="AWS/MemoryDB",
                    metric_name="CPUUtilization",
                    dimensions_map={"ClusterName": cluster_name},
                    statistic="Average"
                ),
                threshold=80,
                evaluation_periods=3,
                alarm_description="High CPU utilization on MemoryDB cluster",
                alarm_name=f"{resource_prefix}-memorydb-cpu-high",
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING
            )
            
            cloudwatch.Alarm(
                self, f"{resource_prefix}-memorydb-memory",
                metric=cloudwatch.Metric(
                    namespace="AWS/MemoryDB",
                    metric_name="DatabaseMemoryUsagePercentage",
                    dimensions_map={"ClusterName": cluster_name},
                    statistic="Average"
                ),
                threshold=85,
                evaluation_periods=2,
                alarm_description="High memory usage on MemoryDB cluster",
                alarm_name=f"{resource_prefix}-memorydb-memory-high",
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING
            )
            
            print("✅ Created MemoryDB infrastructure alarms")
            if debug_mode:
                print(f"   ✅ CPU alarm for cluster: {cluster_name}")
                print(f"   ✅ Memory alarm for cluster: {cluster_name}")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not create MemoryDB alarms: {e}")
            if debug_mode:
                import traceback
                traceback.print_exc()

    def _create_enhanced_dashboard(
        self, resource_prefix: str, base_outputs: Dict[str, str], debug_mode: bool = False
    ):
        """Create enhanced monitoring dashboard"""

        memorydb_endpoint = base_outputs.get('MemoryDBEndpoint', '')
        if memorydb_endpoint:
            # Extract cluster name from endpoint
            try:
                cluster_name = memorydb_endpoint.split('.')[1]  # Get the second part
                if debug_mode:
                    print(f"   🔧 Extracted cluster name: {cluster_name} from endpoint: {memorydb_endpoint}")
            except (IndexError, AttributeError):
                # Fallback to constructed name
                cluster_name = f"{resource_prefix}-cluster"
                if debug_mode:
                    print(f"   ⚠️  Could not extract cluster name from endpoint, using fallback: {cluster_name}")
        else:
            cluster_name = f"{resource_prefix}-cluster"
            if debug_mode:
                print(f"   ⚠️  No MemoryDB endpoint found, using fallback cluster name: {cluster_name}")

        try:
            widgets = []

            # Get API name from outputs for REST API metrics
            api_endpoint = base_outputs.get('ApiEndpoint', '')
            api_id = base_outputs.get('ApiId', '')

            if api_id:
                # REST API specific widgets
                widgets.append([
                    cloudwatch.GraphWidget(
                        title="REST API Performance",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway",
                                metric_name="Count",
                                dimensions_map={"ApiName": f"{resource_prefix}-restapi"},
                                statistic="Sum",
                                label="Request Count"
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway", 
                                metric_name="Latency",
                                dimensions_map={"ApiName": f"{resource_prefix}-restapi"},
                                statistic="Average",
                                label="Latency"
                            )
                        ],
                        right=[
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway",
                                metric_name="4XXError",
                                dimensions_map={"ApiName": f"{resource_prefix}-restapi"},
                                statistic="Sum",
                                label="4XX Errors"
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway",
                                metric_name="5XXError", 
                                dimensions_map={"ApiName": f"{resource_prefix}-restapi"},
                                statistic="Sum",
                                label="5XX Errors"
                            )
                        ],
                        width=12,
                        height=6
                    )
                ])

            # Lambda performance widgets
            widgets.append([
                cloudwatch.GraphWidget(
                    title="Lambda Function Performance",
                    left=[
                        cloudwatch.Metric(
                            namespace="AWS/Lambda",
                            metric_name="Duration",
                            dimensions_map={"FunctionName": f"{resource_prefix}-store-stats"},
                            statistic="Average",
                            label="Store Stats Duration"
                        ),
                        cloudwatch.Metric(
                            namespace="AWS/Lambda",
                            metric_name="Duration",
                            dimensions_map={"FunctionName": f"{resource_prefix}-get-leaderboard-scores"},
                            statistic="Average",
                            label="Get Leaderboard Scores Duration"
                        )
                    ],
                    right=[
                        cloudwatch.Metric(
                            namespace="AWS/Lambda",
                            metric_name="Errors",
                            dimensions_map={"FunctionName": f"{resource_prefix}-store-stats"},
                            statistic="Sum",
                            label="Store Stats Errors"
                        ),
                        cloudwatch.Metric(
                            namespace="AWS/Lambda",
                            metric_name="Errors",
                            dimensions_map={"FunctionName": f"{resource_prefix}-get-leaderboard-scores"},
                            statistic="Sum",
                            label="Get Leaderboard Scores Errors"
                        )
                    ],
                    width=12,
                    height=6
                )
            ])
            
            dashboard = cloudwatch.Dashboard(
                self, f"{resource_prefix}-enhanced-dashboard",
                dashboard_name=f"{resource_prefix}-enhanced-monitoring",
                widgets=widgets
            )
            
            print("✅ Created enhanced monitoring dashboard")
            if debug_mode:
                print(f"   Dashboard name: {resource_prefix}-enhanced-monitoring")
                print(f"   Widgets: {len(widgets)}")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not create dashboard: {e}")
            if debug_mode:
                import traceback
                traceback.print_exc()

    def _create_outputs(self, resource_prefix: str):
        """Create outputs for the post-deploy stack"""

        CfnOutput(
            self, "MonitoringDashboard",
            value=f"https://console.aws.amazon.com/cloudwatch/home?region={self.region or self.default_region}#dashboards:name={resource_prefix}-enhanced-monitoring",
            description="Enhanced monitoring dashboard URL",
            export_name=f"{self.stack_name}-MonitoringDashboard"
        )

        CfnOutput(
            self, "PostDeployStatus",
            value="Complete",
            description="Post-deployment configuration status",
            export_name=f"{self.stack_name}-PostDeployStatus"
        )

        CfnOutput(
            self, "PostDeployTimestamp",
            value=datetime.now(timezone.utc).isoformat(),
            description="Post-deployment completion timestamp",
            export_name=f"{self.stack_name}-PostDeployTimestamp"
        )


# CDK App for post-deployment
app = App()

# Get base stack name from context or use default
base_stack_name = app.node.try_get_context("base_stack_name") or "GameStatsLeaderboardsStack"

GameStatsLeaderboardsMonitoringStack(
    app, "GameStatsLeaderboardsMonitoringStack",
    base_stack_name=base_stack_name,
    env={
        "account": app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        "region": app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or "us-west-2"
    },
    description="Post core deployment configurations for Game Stats and Leaderboards Stack - Monitoring, Alarms, Provisioned Concurrency, and Lambda Insights"
)

app.synth()