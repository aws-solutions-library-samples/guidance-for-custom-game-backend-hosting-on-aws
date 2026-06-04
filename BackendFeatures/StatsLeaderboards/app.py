# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import sys
import os
import hashlib
import json
import time
import textwrap
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

from aws_cdk import (
    App,
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    CustomResource,
    Tags,
    CfnTag,
    Size,
    CfnParameter,
    Token,
    Fn
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_iam as iam,
    aws_memorydb as memorydb,
    aws_secretsmanager as secretsmanager,
    aws_ec2 as ec2,
    aws_logs as logs,
    custom_resources as cr,
    aws_cloudwatch as cloudwatch,
    aws_ssm as ssm,
    aws_kms as kms,
    aws_wafv2 as wafv2,
    aws_applicationinsights as appinsights,
    aws_resourcegroups as resourcegroups,
)
from constructs import Construct
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class CloudFormationHelper:
    """Helper class for CloudFormation operations with proper error handling"""
    
    @staticmethod
    def check_stack_exists(stack_name: str, cf_client) -> Tuple[bool, str, Dict]:
        """Check if CloudFormation stack exists with proper error categorization"""
        try:
            response = cf_client.describe_stacks(StackName=stack_name)
            if response['Stacks']:
                stack = response['Stacks'][0]
                return True, stack['StackStatus'], stack
            return False, 'DOES_NOT_EXIST', {}
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'ValidationError' and 'does not exist' in str(e):
                # This is expected for new deployments
                return False, 'DOES_NOT_EXIST', {}
            else:
                # Unexpected error - re-raise
                raise e
    
    @staticmethod
    def get_stack_resources(stack_name: str, cf_client, aws_cache=None) -> Tuple[bool, str, List[Dict]]:
        """Get stack resources with proper error handling and optional caching"""
        try:
            response = cf_client.describe_stack_resources(StackName=stack_name)
            return True, 'SUCCESS', response.get('StackResources', [])
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'ValidationError' and 'does not exist' in str(e):
                # Expected for new deployments
                return False, 'DOES_NOT_EXIST', []
            else:
                # Unexpected error - re-raise
                raise e
    
    @staticmethod
    def handle_stack_operation_error(operation: str, stack_name: str, error: Exception) -> Dict:
        """Categorize and handle CloudFormation operation errors"""
        if isinstance(error, ClientError):
            error_code = error.response.get('Error', {}).get('Code', '')
            if error_code == 'ValidationError' and 'does not exist' in str(error):
                return {
                    'error_type': 'EXPECTED_NEW_DEPLOYMENT',
                    'severity': 'info',
                    'message': f'Stack {stack_name} does not exist - expected for new deployments',
                    'action': 'continue_with_deployment'
                }
        
        return {
            'error_type': 'CLOUDFORMATION_ERROR',
            'severity': 'error',
            'message': str(error),
            'action': 'investigate_and_retry'
        }


class EnhancedResourceDiscovery:
    """Enhanced resource discovery with context-based decisions (no interactivity)"""

    def __init__(self, region: str = None, account: str = None, context: Dict[str, Any] = None):
        self.region = region or os.environ.get('AWS_DEFAULT_REGION', os.environ.get('CDK_DEFAULT_REGION', 'us-west-2'))
        self.account = account or os.environ.get('CDK_DEFAULT_ACCOUNT')
        self.context = context or {}
        
        try:
            self.session = boto3.Session(region_name=self.region)
            self.ec2_client = self.session.client('ec2')
            self.dynamodb_client = self.session.client('dynamodb')
            self.memorydb_client = self.session.client('memorydb')
            self.iam_client = self.session.client('iam')
            self.kms_client = self.session.client('kms')
            self.ssm_client = self.session.client('ssm')
            self.cf_client = self.session.client('cloudformation')
            self.clients_available = True
            print(f"✅ AWS client components initialized for region: {self.region}")
        except Exception as e:
            print(f"⚠️  Warning: Could not initialize AWS client components: {e}")
            self.clients_available = False
    
    def perform_comprehensive_discovery(self, service_name: str, environment: str, 
                                    resource_prefix: str, force_create_new: bool = False) -> Dict[str, Any]:
        """Perform comprehensive resource discovery with context-based decisions"""
        
        print("\n" + "="*100)
        print("🔍 COMPREHENSIVE RESOURCE DISCOVERY & ANALYSIS")
        print("="*100)
        print(f"Service: {service_name}")
        print(f"Environment: {environment}")
        print(f"Resource Prefix: {resource_prefix}")
        print(f"Force Create New: {force_create_new}")
        print(f"Region: {self.region}")
        print(f"Account: {self.account}")
        print("="*100)
        
        if not self.clients_available:
            print("❌ AWS clients not available - defaulting to create new resources")
            return self._get_empty_discovery_results()
        
        discovery_results = {
            'vpc_analysis': self._discover_and_analyze_vpcs(service_name, environment, force_create_new),
            'dynamodb_analysis': self._discover_and_analyze_dynamodb_tables(resource_prefix, force_create_new),
            'memorydb_analysis': self._discover_and_analyze_memorydb_clusters(resource_prefix, force_create_new, environment),
            'kms_analysis': self._discover_existing_kms_keys(resource_prefix),
            'iam_analysis': self._discover_existing_iam_roles(resource_prefix),
            'lambda_application_analysis': self._discover_existing_lambda_applications(resource_prefix),
            'secrets_manager_analysis': self._discover_existing_secrets_manager_secrets(resource_prefix),
            'api_gateway_analysis': self._discover_and_analyze_api_gateway(resource_prefix, force_create_new),
            'recommendations': {}
        }
        
        # Generate comprehensive recommendations
        discovery_results['recommendations'] = self._generate_comprehensive_recommendations(discovery_results, force_create_new)
        
        # Print comprehensive summary
        self._print_discovery_summary(discovery_results)
        
        return discovery_results
    
    def get_resource_decisions(self, discovery_results: Dict[str, Any], force_create_new: bool = False) -> Dict[str, Any]:
        """Get resource decisions based on discovery results and context (no user interaction)"""
        
        print("\n" + "="*100)
        print("🎯 AUTOMATED RESOURCE SELECTION")
        print("="*100)
        
        if force_create_new:
            print("🆕 FORCE CREATE NEW MODE - All resources will be recreated")
            return {
                'vpc_decision': 'create_new_with_replacement',
                'memorydb_decision': 'create_new_with_replacement',
                'dynamodb_decisions': {
                    'config': 'create_new_with_replacement',
                    'stats': 'create_new_with_replacement'
                },
                'kms_decision': 'create_new_with_replacement',
                'iam_decision': 'create_new_with_replacement',
                'lambda_application_decision': 'create_new_with_replacement',
                'replacement_strategy': 'force_replacement'
            }
        
        # Auto-decision based on discovery results
        decisions = {
            'vpc_decision': self._auto_decide_vpc(discovery_results['vpc_analysis']),
            'memorydb_decision': self._auto_decide_memorydb(discovery_results['memorydb_analysis']),
            'dynamodb_decisions': self._auto_decide_dynamodb(discovery_results['dynamodb_analysis']),
            'kms_decision': self._auto_decide_kms(discovery_results['kms_analysis']),
            'iam_decision': self._auto_decide_iam(discovery_results['iam_analysis']),
            'lambda_application_decision': self._auto_decide_lambda_application(discovery_results['lambda_application_analysis']),
            'secrets_manager_decision': self._auto_decide_secrets_manager(discovery_results['secrets_manager_analysis']),
            'api_gateway_decision': self._auto_decide_api_gateway(discovery_results['api_gateway_analysis']),
            'replacement_strategy': 'smart_reuse'
        }
        
        print("🤖 AUTOMATED DECISIONS:")
        print(f"   VPC: {decisions['vpc_decision']}")
        print(f"   MemoryDB: {decisions['memorydb_decision']}")
        for table_type, decision in decisions['dynamodb_decisions'].items():
            print(f"   DynamoDB {table_type.title()}: {decision}")
        print(f"   KMS: {decisions['kms_decision']}")
        print(f"   IAM: {decisions['iam_decision']}")
        print(f"   Lambda Application: {decisions['lambda_application_decision']}")
        print(f"   Secrets Manager: {decisions['secrets_manager_decision']}")
        print(f"   API Gateway: {decisions['api_gateway_decision']}")
        print(f"   Strategy: {decisions['replacement_strategy']}")
        
        return decisions
    
    def _auto_decide_vpc(self, vpc_analysis: Dict[str, Any]) -> str:
        """Auto-decide VPC action based on analysis"""
        if vpc_analysis['suitable_vpcs']:
            best_vpc = max(vpc_analysis['suitable_vpcs'], key=lambda x: x['suitability']['score'])
            if best_vpc['suitability']['score'] >= 80:
                print(f"   🎯 Auto-selected VPC {best_vpc['vpc_id']} (Score: {best_vpc['suitability']['score']}/100)")
                return 'reuse'
            else:
                print(f"   🎯 Best VPC score too low ({best_vpc['suitability']['score']}/100) - creating new")
                return 'create_new'
        else:
            print("   🎯 No suitable VPCs found - creating new")
            return 'create_new'
    
    def _auto_decide_memorydb(self, memorydb_analysis: Dict[str, Any]) -> str:
        """Auto-decide MemoryDB action based on analysis"""
        if memorydb_analysis['cluster_exists'] and memorydb_analysis['suitability']:
            if memorydb_analysis['suitability']['score'] >= 70:
                print(f"   🎯 Auto-selected existing MemoryDB cluster (Score: {memorydb_analysis['suitability']['score']}/100)")
                return 'reuse'
            else:
                print(f"   🎯 Existing cluster score too low ({memorydb_analysis['suitability']['score']}/100) - creating new")
                return 'create_new'
        else:
            print("   🎯 No suitable MemoryDB cluster found - creating new")
            return 'create_new'
    
    def _auto_decide_dynamodb(self, dynamodb_analysis: Dict[str, Any]) -> Dict[str, str]:
        """Auto-decide DynamoDB actions based on analysis"""
        decisions = {}
        
        for table_type, table_analysis in dynamodb_analysis['table_analysis'].items():
            if table_analysis['exists'] and table_analysis['suitability']:
                if table_analysis['suitability']['score'] >= 60:
                    print(f"   🎯 Auto-selected existing {table_type} table (Score: {table_analysis['suitability']['score']}/100)")
                    decisions[table_type] = 'reuse'
                else:
                    print(f"   🎯 Existing {table_type} table score too low ({table_analysis['suitability']['score']}/100) - creating new")
                    decisions[table_type] = 'create_new'
            else:
                print(f"   🎯 No suitable {table_type} table found - creating new")
                decisions[table_type] = 'create_new'
        
        return decisions

    def _auto_decide_kms(self, kms_analysis: Dict[str, Any]) -> str:
        """Auto-decide KMS action based on analysis with improved logic"""
        
        keys_found = kms_analysis.get('keys_found', False)
        alias_found = kms_analysis.get('alias_found', False)
        key_details = kms_analysis.get('key_details', {})
        
        print(f"🔍 KMS Decision Analysis:")
        print(f"   Keys Found: {keys_found}")
        print(f"   Alias Found: {alias_found}")
        print(f"   Key Details Count: {len(key_details)}")
        
        # Only reuse if we have both a key AND the correct alias
        if keys_found and alias_found:
            # Check if we have the target alias
            target_alias = kms_analysis.get('target_alias', '')
            if target_alias in key_details:
                key_info = key_details[target_alias]
                key_state = key_info.get('key_state', 'Unknown')
                
                if key_state == 'Enabled':
                    print(f"   🎯 Auto-selected existing KMS key with correct alias and enabled state")
                    return 'reuse'
                else:
                    print(f"   🎯 Found KMS key but state is {key_state} - creating new")
                    return 'create_new'
            else:
                print(f"   🎯 Found KMS key but not with target alias {target_alias} - creating new")
                return 'create_new'
        else:
            if keys_found and not alias_found:
                print(f"   🎯 Found KMS key but no matching alias - creating new with proper alias")
            else:
                print(f"   🎯 No suitable KMS key found - creating new")
            return 'create_new'

    def _auto_decide_iam(self, iam_analysis: Dict[str, Any]) -> str:
        """Auto-decide IAM action based on analysis"""
        if iam_analysis['roles_found']:
            print(f"   🎯 Auto-selected existing IAM role")
            return 'reuse'
        else:
            print("   🎯 No suitable IAM role found - creating new")
            return 'create_new'

    def _auto_decide_lambda_application(self, lambda_app_analysis: Dict[str, Any]) -> str:
        """Auto-decide Lambda Application action based on analysis"""
        
        application_found = lambda_app_analysis.get('application_found', False)
        resource_group_found = lambda_app_analysis.get('resource_group_found', False)
        
        print(f"🔍 Lambda Application Decision Analysis:")
        print(f"   Application Found: {application_found}")
        print(f"   Resource Group Found: {resource_group_found}")
        
        if application_found and resource_group_found:
            print(f"   🎯 Auto-selected existing Lambda Application and Resource Group")
            return 'reuse'
        elif resource_group_found and not application_found:
            print(f"   🎯 Resource Group exists but no Application - will reuse Resource Group and create Application")
            return 'reuse_partial'
        else:
            print("   🎯 No suitable Lambda Application found - creating new")
            return 'create_new'

    def _auto_decide_secrets_manager(self, secrets_analysis: Dict[str, Any]) -> str:
        """Auto-decide Secrets Manager action based on analysis"""
        if secrets_analysis['secrets_found']:
            print(f"   🎯 Auto-selected existing Secrets Manager secrets")
            return 'reuse'
        else:
            print("   🎯 No suitable Secrets Manager secrets found - creating new")
            return 'create_new'

    def _auto_decide_api_gateway(self, api_analysis: Dict[str, Any]) -> str:
        """Auto-decide API Gateway action based on analysis"""
        if api_analysis['api_exists'] and api_analysis['suitability']:
            if api_analysis['suitability']['score'] >= 60:
                print(f"   🎯 Auto-selected existing API Gateway (Score: {api_analysis['suitability']['score']}/100)")
                return 'reuse'
            else:
                print(f"   🎯 Existing API Gateway score too low ({api_analysis['suitability']['score']}/100) - creating new")
                return 'create_new'
        else:
            print("   🎯 No suitable API Gateway found - creating new")
            return 'create_new'

    def _discover_and_analyze_vpcs(self, service_name: str, environment: str, force_create_new: bool = False) -> Dict[str, Any]:
        """Comprehensive VPC discovery and analysis"""
        
        print("\n" + "-"*80)
        print("🔍 VPC DISCOVERY & ANALYSIS")
        print("-"*80)

        if force_create_new:
            print("🆕 Force create new mode - skipping VPC discovery")
            return {
                'existing_vpcs': [],
                'suitable_vpcs': [],
                'vpc_details': {},
                'recommendation': 'create_new_with_replacement',
                'reasons': ['Force create new mode enabled']
            }

        analysis = {
            'existing_vpcs': [],
            'suitable_vpcs': [],
            'vpc_details': {},
            'recommendation': 'create_new',
            'reasons': []
        }
        
        try:
            # First try with exact tag matching
            print(f"🔎 Searching for VPCs with exact tags:")
            print(f"   - Service: {service_name}")
            print(f"   - Environment: {environment}")
            
            response = self.ec2_client.describe_vpcs(
                Filters=[
                    {'Name': 'tag:Service', 'Values': [service_name]},
                    {'Name': 'tag:Environment', 'Values': [environment]},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
            
            vpcs_with_exact_tags = response['Vpcs']
            print(f"📊 Found {len(vpcs_with_exact_tags)} VPC(s) with exact matching tags")
            
            # If no VPCs found with exact tags, try with more flexible matching
            if not vpcs_with_exact_tags:
                print(f"🔎 No VPCs found with exact tags, trying more flexible matching")
                
                # Get all available VPCs
                response = self.ec2_client.describe_vpcs(
                    Filters=[
                        {'Name': 'state', 'Values': ['available']}
                    ]
                )
                
                all_vpcs = response['Vpcs']
                print(f"📊 Found {len(all_vpcs)} total available VPCs")
                
                # Filter VPCs that have any relevant tags
                vpcs_with_relevant_tags = []
                for vpc in all_vpcs:
                    tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
                    
                    # Check for partial tag matches
                    service_match = False
                    env_match = False
                    
                    for key, value in tags.items():
                        # Check for service name in any tag
                        if service_name.lower() in key.lower() or service_name.lower() in value.lower():
                            service_match = True
                        
                        # Check for environment in any tag
                        if environment.lower() in key.lower() or environment.lower() in value.lower():
                            env_match = True
                    
                    # If either service or environment matches, consider this VPC
                    if service_match or env_match:
                        vpcs_with_relevant_tags.append(vpc)
                
                print(f"📊 Found {len(vpcs_with_relevant_tags)} VPC(s) with relevant tags")
                
                # Use these VPCs if we found any
                if vpcs_with_relevant_tags:
                    vpcs_to_analyze = vpcs_with_relevant_tags
                else:
                    # If still no matches, just use all VPCs as a last resort
                    print(f"🔎 No VPCs found with relevant tags, analyzing all available VPCs")
                    vpcs_to_analyze = all_vpcs
            else:
                # Use VPCs with exact tags
                vpcs_to_analyze = vpcs_with_exact_tags
            
            # Analyze all candidate VPCs
            for i, vpc in enumerate(vpcs_to_analyze, 1):
                vpc_id = vpc['VpcId']
                vpc_cidr = vpc['CidrBlock']
                
                print(f"\n--- VPC {i}: {vpc_id} ---")
                print(f"   CIDR Block: {vpc_cidr}")
                
                tags = {tag['Key']: tag['Value'] for tag in vpc.get('Tags', [])}
                print(f"   Tags: {json.dumps(tags, indent=6)}")
                
                vpc_details = self._get_comprehensive_vpc_details(vpc_id)
                analysis['vpc_details'][vpc_id] = vpc_details
                
                suitability = self._analyze_vpc_suitability(vpc_id, vpc_details, environment)
                
                vpc_info = {
                    'vpc_id': vpc_id,
                    'vpc_cidr': vpc_cidr,
                    'tags': tags,
                    'details': vpc_details,
                    'suitability': suitability
                }
                
                analysis['existing_vpcs'].append(vpc_info)
                
                if suitability['suitable']:
                    analysis['suitable_vpcs'].append(vpc_info)
                    print(f"   ✅ SUITABLE for reuse (Score: {suitability['score']}/100)")
                else:
                    print(f"   ❌ NOT suitable for reuse (Score: {suitability['score']}/100)")
                
                self._print_vpc_detailed_analysis(vpc_info)
            
            if analysis['suitable_vpcs']:
                analysis['recommendation'] = 'reuse_existing'
                analysis['reasons'].append(f"Found {len(analysis['suitable_vpcs'])} suitable VPC(s)")
            else:
                analysis['recommendation'] = 'create_new'
                if analysis['existing_vpcs']:
                    analysis['reasons'].append("Found VPCs but none are suitable for reuse")
                else:
                    analysis['reasons'].append("No existing VPCs found with matching tags")
            
        except Exception as e:
            print(f"❌ Error during VPC discovery: {e}")
            analysis['reasons'].append(f"Discovery failed: {str(e)}")
        
        return analysis

    def _discover_and_analyze_api_gateway(self, resource_prefix: str, force_create_new: bool = False) -> Dict[str, Any]:
        """Comprehensive API Gateway discovery and analysis"""
        
        print("\n" + "-"*80)
        print("🔍 API GATEWAY DISCOVERY & ANALYSIS")
        print("-"*80)
        
        if force_create_new:
            print("🆕 Force create new mode - skipping API Gateway discovery")
            return {
                'api_exists': False,
                'api_details': None,
                'suitability': None,
                'recommendation': 'create_new_with_replacement'
            }
        
        analysis = {
            'api_exists': False,
            'api_details': None,
            'suitability': None,
            'recommendation': 'create_new'
        }
        
        try:
            apigw_client = self.session.client('apigateway')
            
            # Search for existing REST APIs
            response = apigw_client.get_rest_apis()
            
            for api in response.get('items', []):
                api_name = api.get('name', '')
                
                # Check if API name matches our resource prefix
                if resource_prefix in api_name or 'game-statsleaderboards' in api_name:
                    api_id = api['id']
                    
                    print(f"✅ Found existing REST API: {api_name} ({api_id})")
                    
                    # Get detailed API information
                    api_details = self._get_comprehensive_api_details(api_id, apigw_client)
                    suitability = self._analyze_api_suitability(api_details)
                    
                    analysis.update({
                        'api_exists': True,
                        'api_details': api_details,
                        'suitability': suitability,
                        'recommendation': 'reuse' if suitability['suitable'] else 'create_new'
                    })
                    
                    if suitability['suitable']:
                        print(f"   ✅ SUITABLE for reuse (Score: {suitability['score']}/100)")
                    else:
                        print(f"   ❌ NOT suitable for reuse (Score: {suitability['score']}/100)")
                    
                    self._print_api_detailed_analysis(api_details, suitability)
                    break
            
            if not analysis['api_exists']:
                print(f"   🔍 No existing REST API found matching prefix: {resource_prefix}")
                
        except Exception as e:
            print(f"   ❌ Error during API Gateway discovery: {e}")
            analysis['recommendation'] = 'create_new'
        
        return analysis

    def _get_comprehensive_api_details(self, api_id: str, apigw_client) -> Dict[str, Any]:
        """Get comprehensive API Gateway details"""
        
        details = {
            'api_id': api_id,
            'stages': [],
            'resources': [],
            'deployments': [],
            'authorizers': []
        }
        
        try:
            # Get API info
            api_response = apigw_client.get_rest_api(restApiId=api_id)
            details.update({
                'name': api_response.get('name', ''),
                'description': api_response.get('description', ''),
                'created_date': api_response.get('createdDate', ''),
                'endpoint_configuration': api_response.get('endpointConfiguration', {}),
                'policy': api_response.get('policy', '')
            })
            
            # Get stages
            stages_response = apigw_client.get_stages(restApiId=api_id)
            details['stages'] = stages_response.get('item', [])
            
            # Get resources
            resources_response = apigw_client.get_resources(restApiId=api_id)
            details['resources'] = resources_response.get('items', [])
            
            # Get deployments
            deployments_response = apigw_client.get_deployments(restApiId=api_id)
            details['deployments'] = deployments_response.get('items', [])
            
            # Get authorizers
            try:
                authorizers_response = apigw_client.get_authorizers(restApiId=api_id)
                details['authorizers'] = authorizers_response.get('items', [])
            except Exception:
                details['authorizers'] = []
                
        except Exception as e:
            print(f"⚠️ Error getting API details for {api_id}: {e}")
        
        return details

    def _analyze_api_suitability(self, api_details: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze API Gateway suitability for reuse"""
        
        issues = []
        warnings = []
        recommendations = []
        score = 100
        
        # Check if API has the expected structure
        resources = api_details.get('resources', [])
        stages = api_details.get('stages', [])
        
        # Check for required paths
        resource_paths = [r.get('path', '') for r in resources]
        expected_paths = ['/developer', '/leaderboards', '/leaderboards/config', '/leaderboards/stats']
        
        missing_paths = [path for path in expected_paths if path not in resource_paths]
        if missing_paths:
            issues.append(f"Missing required API paths: {', '.join(missing_paths)}")
            score -= 30
        
        # Check stages
        if not stages:
            issues.append("No deployment stages found")
            score -= 20
        else:
            stage_names = [s.get('stageName', '') for s in stages]
            if 'dev' not in stage_names and 'prod' not in stage_names:
                warnings.append("No standard environment stages (dev/prod) found")
                score -= 10
        
        # Check authorizers
        authorizers = api_details.get('authorizers', [])
        if not authorizers:
            warnings.append("No authorizers found - security may be compromised")
            score -= 15
        
        suitable = len(issues) == 0 and score >= 60
        
        return {
            'suitable': suitable,
            'score': max(0, score),
            'issues': issues,
            'warnings': warnings,
            'recommendations': recommendations
        }

    def _print_api_detailed_analysis(self, api_details: Dict[str, Any], suitability: Dict[str, Any]):
        """Print detailed API analysis"""
        
        print(f"   📊 DETAILED ANALYSIS:")
        print(f"      Suitability Score: {suitability['score']}/100")
        print(f"      API ID: {api_details['api_id']}")
        print(f"      Name: {api_details.get('name', 'N/A')}")
        print(f"      Stages: {len(api_details.get('stages', []))}")
        print(f"      Resources: {len(api_details.get('resources', []))}")
        print(f"      Deployments: {len(api_details.get('deployments', []))}")
        print(f"      Authorizers: {len(api_details.get('authorizers', []))}")
        
        if suitability['issues']:
            print(f"      🚨 BLOCKING ISSUES:")
            for issue in suitability['issues']:
                print(f"         • {issue}")
        
        if suitability['warnings']:
            print(f"      ⚠️  WARNINGS:")
            for warning in suitability['warnings']:
                print(f"         • {warning}")

    def _discover_existing_secrets_manager_secrets(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing Secrets Manager secrets"""
        
        print(f"🔍 Searching for existing Secrets Manager secrets with prefix: {resource_prefix}")
        
        analysis = {
            'secrets_found': False,
            'secret_details': {},
            'target_secrets': {
                'memorydb_password': f"{resource_prefix}-memorydb-password"
            }
        }
        
        try:
            secretsmanager_client = self.session.client('secretsmanager')
            
            # List all secrets and filter by our naming pattern
            paginator = secretsmanager_client.get_paginator('list_secrets')
            
            for page in paginator.paginate():
                for secret in page.get('SecretList', []):
                    secret_name = secret.get('Name', '')
                    secret_arn = secret.get('ARN', '')
                    
                    # Check if this secret matches our resource prefix
                    if resource_prefix in secret_name:
                        # Determine secret type
                        secret_type = 'unknown'
                        if 'memorydb-password' in secret_name:
                            secret_type = 'memorydb_password'
                        elif 'password' in secret_name:
                            secret_type = 'password'
                        
                        analysis['secret_details'][secret_type] = {
                            'name': secret_name,
                            'arn': secret_arn,
                            'description': secret.get('Description', ''),
                            'created_date': secret.get('CreatedDate', ''),
                            'last_accessed_date': secret.get('LastAccessedDate', ''),
                            'last_changed_date': secret.get('LastChangedDate', ''),
                            'last_rotated_date': secret.get('LastRotatedDate', ''),
                            'tags': secret.get('Tags', [])
                        }
                        
                        analysis['secrets_found'] = True
                        print(f"   ✅ Found existing secret: {secret_name} (Type: {secret_type})")
            
            if not analysis['secrets_found']:
                print(f"   🔍 No existing secrets found with prefix: {resource_prefix}")
                
        except Exception as e:
            print(f"   ⚠️ Error discovering Secrets Manager secrets: {e}")
        
        return analysis

    def _get_comprehensive_vpc_details(self, vpc_id: str) -> Dict[str, Any]:
        """Get comprehensive VPC details"""
        
        details = {
            'subnets': {'public': [], 'private': [], 'isolated': []},
            'route_tables': [],
            'internet_gateways': [],
            'nat_gateways': [],
            'vpc_endpoints': [],
            'security_groups': [],
            'availability_zones': set(),
            'total_subnets': 0,
            'has_internet_access': False,
            'has_private_internet_access': False
        }
        
        try:
            # Get subnets with detailed analysis
            subnets_response = self.ec2_client.describe_subnets(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            
            for subnet in subnets_response['Subnets']:
                subnet_info = {
                    'subnet_id': subnet['SubnetId'],
                    'cidr_block': subnet['CidrBlock'],
                    'availability_zone': subnet['AvailabilityZone'],
                    'available_ip_count': subnet['AvailableIpAddressCount'],
                    'map_public_ip': subnet.get('MapPublicIpOnLaunch', False),
                    'tags': {tag['Key']: tag['Value'] for tag in subnet.get('Tags', [])}
                }
                
                details['availability_zones'].add(subnet['AvailabilityZone'])
                details['total_subnets'] += 1
                
                # Classify subnet type
                if subnet_info['map_public_ip']:
                    details['subnets']['public'].append(subnet_info)
                else:
                    # Check if it's truly private (has route to NAT) or isolated
                    route_tables = self._get_subnet_route_tables(subnet['SubnetId'])
                    has_nat_route = any(
                        route.get('NatGatewayId') for rt in route_tables 
                        for route in rt.get('Routes', [])
                    )
                    
                    if has_nat_route:
                        details['subnets']['private'].append(subnet_info)
                    else:
                        details['subnets']['isolated'].append(subnet_info)
            
            # Get Internet Gateways
            igw_response = self.ec2_client.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
            )
            details['internet_gateways'] = igw_response['InternetGateways']
            details['has_internet_access'] = len(details['internet_gateways']) > 0
            
            # Get NAT Gateways
            nat_response = self.ec2_client.describe_nat_gateways(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            active_nat_gateways = [ng for ng in nat_response['NatGateways'] if ng['State'] == 'available']
            details['nat_gateways'] = active_nat_gateways
            details['has_private_internet_access'] = len(active_nat_gateways) > 0
            
            # Get VPC Endpoints
            vpce_response = self.ec2_client.describe_vpc_endpoints(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            details['vpc_endpoints'] = vpce_response['VpcEndpoints']
            
            # Get Security Groups
            sg_response = self.ec2_client.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
            )
            details['security_groups'] = sg_response['SecurityGroups']
            
            # Convert set to list for JSON serialization
            details['availability_zones'] = list(details['availability_zones'])
            
        except Exception as e:
            print(f"⚠️  Error getting VPC details for {vpc_id}: {e}")
        
        return details
    
    def _get_subnet_route_tables(self, subnet_id: str) -> List[Dict]:
        """Get route tables associated with a subnet"""
        try:
            response = self.ec2_client.describe_route_tables(
                Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
            )
            return response['RouteTables']
        except Exception:
            return []
    
    def _analyze_vpc_suitability(self, vpc_id: str, vpc_details: Dict[str, Any], environment: str = None) -> Dict[str, Any]:
        """Comprehensive VPC suitability analysis"""
        
        issues = []
        warnings = []
        recommendations = []
        score = 100  # Start with perfect score
        
        # Get environment from context to adjust suitability criteria
        environment = environment or self.context.get("environment", "dev")
        
        # Check for public subnets
        public_count = len(vpc_details['subnets']['public'])
        if public_count == 0:
            issues.append("No public subnets found - API Gateway needs public access")
            score -= 30
        elif public_count < 2:
            if environment in ["prod", "staging"]:
                warnings.append("Only 1 public subnet - not recommended for production/staging high availability")
                score -= 10
            else:
                warnings.append("Only 1 public subnet - consider multi-AZ for high availability")
                score -= 5
        
        # Check for private subnets
        private_count = len(vpc_details['subnets']['private'])
        if private_count == 0:
            issues.append("No private subnets found - Lambda functions need private subnets")
            score -= 30
        elif private_count < 2:
            if environment in ["prod", "staging"]:
                warnings.append("Only 1 private subnet - not recommended for production/staging high availability")
                score -= 10
            else:
                warnings.append("Only 1 private subnet - consider multi-AZ for high availability")
                score -= 5
        
        # Check availability zones
        az_count = len(vpc_details['availability_zones'])
        if az_count < 2:
            if environment in ["prod", "staging"]:
                issues.append("Subnets span fewer than 2 AZs - MemoryDB requires multi-AZ for production/staging")
                score -= 30
            else:
                warnings.append("Subnets span fewer than 2 AZs - MemoryDB prefers multi-AZ")
                score -= 15
        elif az_count < 3 and environment in ["prod", "staging"]:
            warnings.append("Only 2 AZs used - production/staging environments should use 3 AZs for better resilience")
            score -= 5
        
        # Check Internet Gateway
        if not vpc_details['has_internet_access']:
            issues.append("No Internet Gateway - required for public subnet access")
            score -= 20
        
        # Check NAT Gateway for private subnet internet access
        if private_count > 0 and not vpc_details['has_private_internet_access']:
            if environment in ["prod", "staging"]:
                warnings.append("No NAT Gateway - private subnets won't have internet access (required for production/staging)")
                score -= 15
            else:
                warnings.append("No NAT Gateway - private subnets won't have internet access")
                score -= 10
            recommendations.append("Add NAT Gateway for Lambda function internet access")
        
        # Check VPC endpoints (cost optimization)
        endpoint_services = [ep.get('ServiceName', '') for ep in vpc_details['vpc_endpoints']]
        if 'dynamodb' not in str(endpoint_services).lower():
            recommendations.append("Add DynamoDB VPC endpoint for cost optimization")
            score -= 2
        if 's3' not in str(endpoint_services).lower():
            recommendations.append("Add S3 VPC endpoint for cost optimization")
            score -= 2
        
        # Check security groups count (too many might indicate complexity)
        sg_count = len(vpc_details['security_groups'])
        if sg_count > 50:
            warnings.append(f"High number of security groups ({sg_count}) - may indicate complex setup")
            score -= 3
        
        # Adjust suitability threshold based on environment
        suitability_threshold = 70
        if environment == 'dev':
            suitability_threshold = 60  # More lenient for dev
        elif environment in ['staging', 'prod']:
            suitability_threshold = 70  # Strict for staging and prod
        
        # Calculate final suitability
        suitable = len(issues) == 0 and score >= suitability_threshold
        
        return {
            'suitable': suitable,
            'score': max(0, score),
            'issues': issues,
            'warnings': warnings,
            'recommendations': recommendations,
            'analysis_details': {
                'public_subnets': public_count,
                'private_subnets': private_count,
                'isolated_subnets': len(vpc_details['subnets']['isolated']),
                'availability_zones': az_count,
                'has_internet_gateway': vpc_details['has_internet_access'],
                'has_nat_gateway': vpc_details['has_private_internet_access'],
                'vpc_endpoints_count': len(vpc_details['vpc_endpoints']),
                'security_groups_count': sg_count,
                'environment': environment,
                'suitability_threshold': suitability_threshold
            }
        }
    
    def _print_vpc_detailed_analysis(self, vpc_info: Dict[str, Any]):
        """Print detailed VPC analysis"""
        
        details = vpc_info['details']
        suitability = vpc_info['suitability']
        
        print(f"   📊 DETAILED ANALYSIS:")
        print(f"      Suitability Score: {suitability['score']}/100")
        print(f"      Public Subnets: {len(details['subnets']['public'])}")
        print(f"      Private Subnets: {len(details['subnets']['private'])}")
        print(f"      Isolated Subnets: {len(details['subnets']['isolated'])}")
        print(f"      Availability Zones: {len(details['availability_zones'])} ({', '.join(details['availability_zones'])})")
        print(f"      Internet Gateway: {'✅ Yes' if details['has_internet_access'] else '❌ No'}")
        print(f"      NAT Gateway: {'✅ Yes' if details['has_private_internet_access'] else '❌ No'}")
        print(f"      VPC Endpoints: {len(details['vpc_endpoints'])}")
        print(f"      Security Groups: {len(details['security_groups'])}")
        
        if suitability['issues']:
            print(f"      🚨 BLOCKING ISSUES:")
            for issue in suitability['issues']:
                print(f"         • {issue}")
        
        if suitability['warnings']:
            print(f"      ⚠️  WARNINGS:")
            for warning in suitability['warnings']:
                print(f"         • {warning}")
        
        if suitability['recommendations']:
            print(f"      💡 RECOMMENDATIONS:")
            for rec in suitability['recommendations']:
                print(f"         • {rec}")
    
    def _discover_and_analyze_dynamodb_tables(self, resource_prefix: str, force_create_new: bool = False) -> Dict[str, Any]:
        """Comprehensive DynamoDB table discovery and analysis"""
        
        print("\n" + "-"*80)
        print("🔍 DYNAMODB TABLE DISCOVERY & ANALYSIS")
        print("-"*80)
        
        if force_create_new:
            print("🆕 Force create new mode - skipping DynamoDB discovery")
            return {
                'table_analysis': {
                    'config': {'exists': False, 'recommendation': 'create_new_with_replacement'},
                    'stats': {'exists': False, 'recommendation': 'create_new_with_replacement'}
                },
                'suitable_tables': [],
                'recommendation': {
                    'config': 'create_new_with_replacement',
                    'stats': 'create_new_with_replacement'
                },
                'overall_recommendation': 'create_new_with_replacement'
            }
        
        table_names = {
            'config': f"{resource_prefix}-config",
            'stats': f"{resource_prefix}-stats"
        }
        
        analysis = {
            'table_analysis': {},
            'suitable_tables': [],
            'recommendation': {},
            'overall_recommendation': 'create_new'
        }
        
        for table_type, table_name in table_names.items():
            print(f"\n🔎 Analyzing {table_type.upper()} table: {table_name}")
            
            table_info = self._get_comprehensive_table_details(table_name)
            
            if table_info:
                suitability = self._analyze_table_suitability(table_info, table_type)
                
                table_analysis = {
                    'exists': True,
                    'details': table_info,
                    'suitability': suitability,
                    'recommendation': 'reuse' if suitability['suitable'] else 'create_new'
                }
                
                if suitability['suitable']:
                    analysis['suitable_tables'].append(table_type)
                    print(f"   ✅ SUITABLE for reuse (Score: {suitability['score']}/100)")
                else:
                    print(f"   ❌ NOT suitable for reuse (Score: {suitability['score']}/100)")
                
                self._print_table_detailed_analysis(table_info, suitability)
                
            else:
                table_analysis = {
                    'exists': False,
                    'details': None,
                    'suitability': None,
                    'recommendation': 'create_new'
                }
                print(f"   ❌ Table does not exist - will create new")
            
            analysis['table_analysis'][table_type] = table_analysis
            analysis['recommendation'][table_type] = table_analysis['recommendation']
        
        return analysis
    
    def _get_comprehensive_table_details(self, table_name: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive DynamoDB table details"""
        
        try:
            response = self.dynamodb_client.describe_table(TableName=table_name)
            table = response['Table']
            
            # Get additional table information
            try:
                backup_response = self.dynamodb_client.describe_continuous_backups(TableName=table_name)
                backup_info = backup_response['ContinuousBackupsDescription']
            except Exception:
                backup_info = {}
            
            try:
                tags_response = self.dynamodb_client.list_tags_of_resource(ResourceArn=table['TableArn'])
                tags = {tag['Key']: tag['Value'] for tag in tags_response.get('Tags', [])}
            except Exception:
                tags = {}
            
            return {
                'table_name': table['TableName'],
                'table_arn': table['TableArn'],
                'table_status': table['TableStatus'],
                'creation_date': table['CreationDateTime'].isoformat() if 'CreationDateTime' in table else None,
                'billing_mode': table.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED'),
                'item_count': table.get('ItemCount', 0),
                'table_size_bytes': table.get('TableSizeBytes', 0),
                'provisioned_throughput': table.get('ProvisionedThroughput', {}),
                'global_secondary_indexes': table.get('GlobalSecondaryIndexes', []),
                'local_secondary_indexes': table.get('LocalSecondaryIndexes', []),
                'stream_specification': table.get('StreamSpecification', {}),
                'sse_description': table.get('SSEDescription', {}),
                'backup_info': backup_info,
                'tags': tags,
                'key_schema': table.get('KeySchema', []),
                'attribute_definitions': table.get('AttributeDefinitions', [])
            }
            
        except self.dynamodb_client.exceptions.ResourceNotFoundException:
            return None
        except Exception as e:
            print(f"⚠️  Error getting table details for {table_name}: {e}")
            return None
    
    def _analyze_table_suitability(self, table_info: Dict[str, Any], table_type: str) -> Dict[str, Any]:
        """Comprehensive table suitability analysis"""
        
        issues = []
        warnings = []
        recommendations = []
        score = 100
        
        # Check table status
        if table_info['table_status'] != 'ACTIVE':
            issues.append(f"Table status is {table_info['table_status']}, not ACTIVE")
            score -= 50
        
        # Check if table has data
        item_count = table_info['item_count']
        table_size_mb = table_info['table_size_bytes'] / (1024 * 1024)
        
        if item_count > 0:
            if item_count > 10000:
                warnings.append(f"Table contains {item_count:,} items - significant data may be affected")
                score -= 15
            else:
                warnings.append(f"Table contains {item_count:,} items - existing data will be preserved")
                score -= 5
        
        if table_size_mb > 100:
            warnings.append(f"Table size is {table_size_mb:.1f} MB - consider data backup before reuse")
            score -= 5
        
        # Check billing mode
        billing_mode = table_info['billing_mode']
        if billing_mode != 'PAY_PER_REQUEST':
            warnings.append(f"Table uses {billing_mode} billing - may have different cost implications")
            recommendations.append("Consider switching to on-demand billing for variable workloads")
            score -= 10
        
        # Check encryption
        sse_status = table_info['sse_description'].get('Status', 'DISABLED')
        if sse_status == 'DISABLED':
            warnings.append("Table encryption is disabled - security risk")
            recommendations.append("Enable encryption at rest for better security")
            score -= 15
        
        # Check point-in-time recovery
        pitr_status = table_info['backup_info'].get('PointInTimeRecoveryDescription', {}).get('PointInTimeRecoveryStatus', 'DISABLED')
        if pitr_status == 'DISABLED':
            warnings.append("Point-in-time recovery is disabled - data recovery risk")
            recommendations.append("Enable point-in-time recovery for data protection")
            score -= 10
        
        # Check key schema compatibility
        key_schema_issues = self._validate_key_schema(table_info['key_schema'], table_type)
        if key_schema_issues:
            issues.extend(key_schema_issues)
            score -= 30
        
        # Check GSI compatibility
        gsi_issues = self._validate_gsi_schema(table_info['global_secondary_indexes'], table_type)
        if gsi_issues:
            issues.extend(gsi_issues)
            score -= 20
        
        suitable = len(issues) == 0 and score >= 60
        
        return {
            'suitable': suitable,
            'score': max(0, score),
            'issues': issues,
            'warnings': warnings,
            'recommendations': recommendations,
            'analysis_details': {
                'item_count': item_count,
                'size_mb': table_size_mb,
                'billing_mode': billing_mode,
                'encryption_status': sse_status,
                'pitr_status': pitr_status,
                'gsi_count': len(table_info['global_secondary_indexes']),
                'lsi_count': len(table_info['local_secondary_indexes'])
            }
        }
    
    def _validate_key_schema(self, key_schema: List[Dict], table_type: str) -> List[str]:
        """Validate key schema compatibility"""
        issues = []
        
        expected_schemas = {
            'config': [{'AttributeName': 'leaderboardName', 'KeyType': 'HASH'}],
            'stats': [
                {'AttributeName': 'playerID', 'KeyType': 'HASH'},
                {'AttributeName': 'sortKey', 'KeyType': 'RANGE'}
            ]
        }
        
        expected = expected_schemas.get(table_type, [])
        if key_schema != expected:
            issues.append(f"Key schema mismatch for {table_type} table")
        
        return issues
    
    def _validate_gsi_schema(self, gsi_list: List[Dict], table_type: str) -> List[str]:
        """Validate GSI schema compatibility"""
        issues = []
        
        # Define expected GSIs for each table type
        expected_gsis = {
            'config': ['gameID-gameMode-index'],
            'stats': ['leaderboardName-timestamp-index', 'gameID-gameMode-index']        }
        
        expected = expected_gsis.get(table_type, [])
        existing_gsi_names = [gsi['IndexName'] for gsi in gsi_list]
        
        for expected_gsi in expected:
            if expected_gsi not in existing_gsi_names:
                issues.append(f"Missing required GSI: {expected_gsi}")
        
        return issues
    
    def _print_table_detailed_analysis(self, table_info: Dict[str, Any], suitability: Dict[str, Any]):
        """Print detailed table analysis"""
        
        print(f"   📊 DETAILED ANALYSIS:")
        print(f"      Suitability Score: {suitability['score']}/100")
        print(f"      Status: {table_info['table_status']}")
        print(f"      Items: {table_info['item_count']:,}")
        print(f"      Size: {table_info['table_size_bytes'] / (1024 * 1024):.1f} MB")
        print(f"      Billing: {table_info['billing_mode']}")
        print(f"      Encryption: {table_info['sse_description'].get('Status', 'DISABLED')}")
        print(f"      PITR: {table_info['backup_info'].get('PointInTimeRecoveryDescription', {}).get('PointInTimeRecoveryStatus', 'DISABLED')}")
        print(f"      GSIs: {len(table_info['global_secondary_indexes'])}")
        print(f"      LSIs: {len(table_info['local_secondary_indexes'])}")
        
        if suitability['issues']:
            print(f"      🚨 BLOCKING ISSUES:")
            for issue in suitability['issues']:
                print(f"         • {issue}")
        
        if suitability['warnings']:
            print(f"      ⚠️  WARNINGS:")
            for warning in suitability['warnings']:
                print(f"         • {warning}")
        
        if suitability['recommendations']:
            print(f"      💡 RECOMMENDATIONS:")
            for rec in suitability['recommendations']:
                print(f"         • {rec}")
    
    def _discover_and_analyze_memorydb_clusters(self, resource_prefix: str, force_create_new: bool = False, environment: str = None) -> Dict[str, Any]:
        """Comprehensive MemoryDB cluster discovery and analysis"""
        
        print("\n" + "-"*80)
        print("🔍 MEMORYDB CLUSTER DISCOVERY & ANALYSIS")
        print("-"*80)
        
        cluster_name = f"{resource_prefix}-cluster"
        
        if force_create_new:
            print("🆕 Force create new mode - skipping MemoryDB discovery")
            return {
                'cluster_exists': False,
                'cluster_details': None,
                'suitability': None,
                'recommendation': 'create_new_with_replacement'
            }
        
        print(f"🔎 Searching for MemoryDB cluster: {cluster_name}")
        
        analysis = {
            'cluster_exists': False,
            'cluster_details': None,
            'suitability': None,
            'recommendation': 'create_new'
        }
        
        try:
            response = self.memorydb_client.describe_clusters(ClusterName=cluster_name)
            
            if response['Clusters']:
                cluster = response['Clusters'][0]
                print(f"   ✅ Found cluster: {cluster_name}")
                
                cluster_details = self._get_comprehensive_cluster_details(cluster)
                suitability = self._analyze_cluster_suitability(cluster_details, environment)
                
                analysis.update({
                    'cluster_exists': True,
                    'cluster_details': cluster_details,
                    'suitability': suitability,
                    'recommendation': 'reuse' if suitability['suitable'] else 'create_new'
                })
                
                if suitability['suitable']:
                    print(f"   ✅ SUITABLE for reuse (Score: {suitability['score']}/100)")
                else:
                    print(f"   ❌ NOT suitable for reuse (Score: {suitability['score']}/100)")
                
                self._print_cluster_detailed_analysis(cluster_details, suitability)
            else:
                print(f"   ❌ Cluster not found: {cluster_name}")
                
        except self.memorydb_client.exceptions.ClusterNotFoundFault:
            print(f"   ❌ Cluster not found: {cluster_name}")
        except Exception as e:
            print(f"   ❌ Error searching for cluster: {e}")
            analysis['recommendation'] = 'create_new'
        
        return analysis
    
    def _get_comprehensive_cluster_details(self, cluster: Dict[str, Any]) -> Dict[str, Any]:
        """Get comprehensive MemoryDB cluster details"""
        
        return {
            'cluster_name': cluster['Name'],
            'cluster_arn': cluster['ARN'],
            'status': cluster['Status'],
            'engine': cluster.get('Engine', ''),
            'engine_version': cluster.get('EngineVersion', ''),
            'node_type': cluster.get('NodeType', ''),
            'num_shards': cluster.get('NumberOfShards', 0),
            'num_replicas_per_shard': cluster.get('NumReplicasPerShard', 0),
            'tls_enabled': cluster.get('TLSEnabled', False),
            'endpoint': cluster.get('ClusterEndpoint', {}).get('Address', ''),
            'port': cluster.get('ClusterEndpoint', {}).get('Port', 6379),
            'parameter_group_name': cluster.get('ParameterGroupName', ''),
            'subnet_group_name': cluster.get('SubnetGroupName', ''),
            'security_group_ids': cluster.get('SecurityGroupIds', []),
            'maintenance_window': cluster.get('MaintenanceWindow', ''),
            'snapshot_retention_limit': cluster.get('SnapshotRetentionLimit', 0),
            'snapshot_window': cluster.get('SnapshotWindow', ''),
            'auto_minor_version_upgrade': cluster.get('AutoMinorVersionUpgrade', False),
            'data_tiering': cluster.get('DataTiering', ''),
            'acl_name': cluster.get('ACLName', ''),
            'kms_key_id': cluster.get('KmsKeyId', ''),
            'description': cluster.get('Description', '')
        }

    def _analyze_cluster_suitability(self, cluster_details: Dict[str, Any], environment: str = None) -> Dict[str, Any]:
        """Comprehensive cluster suitability analysis"""
        
        issues = []
        warnings = []
        recommendations = []
        score = 100
        
        # Get environment from context to adjust suitability criteria
        environment = environment or self.context.get("environment", "dev")
        
        # Check cluster status - Accept more valid states
        status = cluster_details['status']
        valid_operational_states = ['available', 'snapshotting', 'backing-up', 'modifying']
        invalid_states = ['creating', 'deleting', 'failed', 'incompatible-parameters', 'incompatible-network']
        
        if status in invalid_states:
            issues.append(f"Cluster status is '{status}' - not suitable for use")
            score -= 50
        elif status in ['snapshotting', 'backing-up']:
            warnings.append(f"Cluster status is '{status}' - temporarily busy but operational")
            print(f"   ℹ️  MemoryDB cluster is {status} - this is a valid operational state")
            score -= 5  # Minor penalty for temporary state
        elif status == 'modifying':
            warnings.append(f"Cluster status is '{status}' - configuration changes in progress")
            score -= 10  # Slightly higher penalty for modification state
        elif status != 'available':
            warnings.append(f"Cluster status is '{status}' - may have limited functionality")
            score -= 15
        
        # Check engine
        engine = cluster_details['engine'].lower()
        if engine not in ['valkey', 'redis']:
            issues.append(f"Unsupported engine: {engine} (expected Valkey or Redis)")
            score -= 40
        elif engine == 'redis':
            warnings.append("Using Redis engine - Valkey is recommended for better performance")
            score -= 10
        
        # Check engine version
        engine_version = cluster_details['engine_version']
        if engine_version:
            major_version = engine_version.split('.')[0]
            if int(major_version) < 7:
                warnings.append(f"Engine version {engine_version} is older - version 7+ recommended")
                score -= 15
        
        # Check TLS
        if not cluster_details['tls_enabled']:
            warnings.append("TLS is disabled - security risk for production workloads")
            recommendations.append("Enable TLS for secure connections")
            score -= 20
        
        # Check node type - be more lenient for dev environment
        node_type = cluster_details['node_type']
        if 't4g' in node_type or 't3' in node_type:
            if environment in ['prod', 'staging']:
                warnings.append(f"Using burstable instance type {node_type} - not suitable for production/staging")
                score -= 15
            else:
                warnings.append(f"Using burstable instance type {node_type} - acceptable for {environment}")
                score -= 5  # Reduced penalty for non-prod environments
        
        # Check sharding configuration - be more lenient for dev environment
        num_shards = cluster_details['num_shards']
        num_replicas = cluster_details['num_replicas_per_shard']
        
        if num_shards == 0:
            issues.append("No shards configured")
            score -= 30
        elif num_shards == 1 and num_replicas == 0:
            if environment in ['prod', 'staging']:
                warnings.append("Single node configuration - no high availability for production/staging")
                recommendations.append("Add replicas for high availability")
                score -= 20
            else:
                warnings.append(f"Single node configuration - acceptable for {environment}")
                recommendations.append("Add replicas for high availability")
                score -= 10  # Reduced penalty for non-prod environments
        
        # Check backup configuration
        retention_limit = cluster_details['snapshot_retention_limit']
        if retention_limit == 0:
            warnings.append("Snapshot retention is disabled - data recovery risk")
            recommendations.append("Enable snapshot retention for data protection")
            score -= 10
        
        # Check maintenance window
        if not cluster_details['maintenance_window']:
            recommendations.append("Set maintenance window to control update timing")
            score -= 2
        
        # Adjust suitability threshold based on environment
        suitability_threshold = 70
        if environment == 'dev':
            suitability_threshold = 60  # More lenient for dev
        elif environment in ['staging', 'prod']:
            suitability_threshold = 70  # Strict for staging and prod
        
        # Only consider blocking issues as true blockers, not warnings
        suitable = len(issues) == 0 and score >= suitability_threshold
        
        print(f"   🔍 Suitability analysis for {environment} environment (threshold: {suitability_threshold})")
        print(f"   🔢 Final score: {score}/100 - {'SUITABLE' if suitable else 'NOT SUITABLE'}")
        
        return {
            'suitable': suitable,
            'score': max(0, score),
            'issues': issues,
            'warnings': warnings,
            'recommendations': recommendations,
            'analysis_details': {
                'status': status,
                'engine': engine,
                'engine_version': engine_version,
                'node_type': node_type,
                'tls_enabled': cluster_details['tls_enabled'],
                'num_shards': num_shards,
                'num_replicas': num_replicas,
                'backup_retention_days': retention_limit,
                'environment': environment,
                'suitability_threshold': suitability_threshold
            }
        }

    def _print_cluster_detailed_analysis(self, cluster_details: Dict[str, Any], suitability: Dict[str, Any]):
        """Print detailed cluster analysis"""
        
        print(f"   📊 DETAILED ANALYSIS:")
        print(f"      Suitability Score: {suitability['score']}/100")
        print(f"      Status: {cluster_details['status']}")
        print(f"      Engine: {cluster_details['engine']} {cluster_details['engine_version']}")
        print(f"      Node Type: {cluster_details['node_type']}")
        print(f"      Shards: {cluster_details['num_shards']}")
        print(f"      Replicas per Shard: {cluster_details['num_replicas_per_shard']}")
        print(f"      TLS Enabled: {'✅ Yes' if cluster_details['tls_enabled'] else '❌ No'}")
        print(f"      Endpoint: {cluster_details['endpoint']}:{cluster_details['port']}")
        print(f"      Backup Retention: {cluster_details['snapshot_retention_limit']} days")
        print(f"      Auto Minor Version Upgrade: {'✅ Yes' if cluster_details['auto_minor_version_upgrade'] else '❌ No'}")
        
        if suitability['issues']:
            print(f"      🚨 BLOCKING ISSUES:")
            for issue in suitability['issues']:
                print(f"         • {issue}")
        
        if suitability['warnings']:
            print(f"      ⚠️  WARNINGS:")
            for warning in suitability['warnings']:
                print(f"         • {warning}")
        
        if suitability['recommendations']:
            print(f"      💡 RECOMMENDATIONS:")
            for rec in suitability['recommendations']:
                print(f"         • {rec}")

    def _discover_existing_kms_keys(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing KMS keys with improved alias handling"""
        
        print(f"🔍 Searching for existing KMS keys with prefix: {resource_prefix}")
        
        analysis = {
            'keys_found': False,
            'key_details': {},
            'alias_found': False,
            'target_alias': f"alias/{resource_prefix}-key"
        }
        
        try:
            kms_client = self.session.client('kms')
            
            print(f"🔎 Looking for KMS alias: {analysis['target_alias']}")
            
            # List aliases to find our key
            paginator = kms_client.get_paginator('list_aliases')
            
            for page in paginator.paginate():
                for alias in page.get('Aliases', []):
                    alias_name = alias.get('AliasName', '')
                    
                    # Check for exact match
                    if alias_name == analysis['target_alias']:
                        key_id = alias.get('TargetKeyId')
                        if key_id:
                            print(f"✅ Found exact KMS alias match: {alias_name} -> {key_id}")
                            
                            # Get additional key details
                            try:
                                key_details = kms_client.describe_key(KeyId=key_id)
                                key_metadata = key_details['KeyMetadata']
                                
                                analysis['alias_found'] = True
                                analysis['keys_found'] = True
                                analysis['key_details'][alias_name] = {
                                    'key_id': key_id,
                                    'key_arn': key_metadata.get('Arn', ''),
                                    'alias_arn': alias.get('AliasArn', ''),
                                    'creation_date': alias.get('CreationDate', ''),
                                    'last_updated_date': alias.get('LastUpdatedDate', ''),
                                    'key_state': key_metadata.get('KeyState', 'Unknown'),
                                    'key_usage': key_metadata.get('KeyUsage', 'Unknown'),
                                    'description': key_metadata.get('Description', ''),
                                    'enabled': key_metadata.get('Enabled', False)
                                }
                                
                                print(f"   Key State: {key_metadata.get('KeyState', 'Unknown')}")
                                print(f"   Key Usage: {key_metadata.get('KeyUsage', 'Unknown')}")
                                print(f"   Enabled: {key_metadata.get('Enabled', False)}")
                                print(f"   Description: {key_metadata.get('Description', 'No description')}")
                                
                            except Exception as e:
                                print(f"⚠️ Could not get key details for {key_id}: {e}")
                                # Still record the basic alias info
                                analysis['alias_found'] = True
                                analysis['keys_found'] = True
                                analysis['key_details'][alias_name] = {
                                    'key_id': key_id,
                                    'alias_arn': alias.get('AliasArn', ''),
                                    'creation_date': alias.get('CreationDate', ''),
                                    'last_updated_date': alias.get('LastUpdatedDate', ''),
                                    'error': str(e)
                                }
                            
                            break
                    
                    # Also check for partial matches (in case of naming variations)
                    elif resource_prefix in alias_name and 'key' in alias_name:
                        key_id = alias.get('TargetKeyId')
                        print(f"🔍 Found similar KMS alias: {alias_name} -> {key_id}")
                        
                        if not analysis['keys_found']:  # Only use if we haven't found exact match
                            analysis['keys_found'] = True
                            analysis['key_details'][alias_name] = {
                                'key_id': key_id,
                                'alias_arn': alias.get('AliasArn', ''),
                                'creation_date': alias.get('CreationDate', ''),
                                'last_updated_date': alias.get('LastUpdatedDate', ''),
                                'match_type': 'partial'
                            }
            
            if not analysis['alias_found']:
                print(f"🔍 No KMS alias found matching: {analysis['target_alias']}")
                
                # Try to find keys by tags as fallback
                try:
                    print(f"🔍 Searching for KMS keys by tags...")
                    
                    # List keys and check their tags
                    keys_response = kms_client.list_keys()
                    
                    for key_info in keys_response.get('Keys', []):
                        key_id = key_info['KeyId']
                        
                        try:
                            # Get key tags
                            tags_response = kms_client.list_resource_tags(KeyId=key_id)
                            tags = {tag['TagKey']: tag['TagValue'] for tag in tags_response.get('Tags', [])}
                            
                            # Check if this key has relevant tags
                            if (tags.get('Service') == 'game-statsleaderboards' or 
                                tags.get('Purpose') == 'GameStatsLeaderboards' or
                                resource_prefix in tags.get('Name', '')):
                                
                                print(f"✅ Found KMS key by tags: {key_id}")
                                
                                # Get key details
                                key_details = kms_client.describe_key(KeyId=key_id)
                                key_metadata = key_details['KeyMetadata']
                                
                                analysis['keys_found'] = True
                                analysis['key_details'][f"key-{key_id}"] = {
                                    'key_id': key_id,
                                    'key_arn': key_metadata.get('Arn', ''),
                                    'key_state': key_metadata.get('KeyState', 'Unknown'),
                                    'description': key_metadata.get('Description', ''),
                                    'enabled': key_metadata.get('Enabled', False),
                                    'tags': tags,
                                    'match_type': 'by_tags'
                                }
                                
                                break  # Use the first match
                                
                        except Exception as e:
                            # Skip keys we can't access
                            continue
                            
                except Exception as e:
                    print(f"⚠️ Error searching KMS keys by tags: {e}")
                    
        except Exception as e:
            print(f"❌ Error during KMS key discovery: {e}")
            analysis['error'] = str(e)
        
        # Summary
        if analysis['keys_found']:
            print(f"📊 KMS Discovery Summary:")
            print(f"   Keys Found: {len(analysis['key_details'])}")
            print(f"   Exact Alias Match: {'✅' if analysis['alias_found'] else '❌'}")
            print(f"   Target Alias: {analysis['target_alias']}")
        else:
            print(f"📊 No KMS keys found matching criteria")
        
        return analysis

    def _discover_existing_iam_roles(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing IAM roles"""
        
        print(f"🔍 Searching for existing IAM roles with prefix: {resource_prefix}")
        
        analysis = {
            'roles_found': False,
            'role_details': {},
            'target_role': f"{resource_prefix}-lambda-role"
        }
        
        try:
            iam_client = self.session.client('iam')
            
            # Try to get the specific role
            try:
                response = iam_client.get_role(RoleName=analysis['target_role'])
                role = response['Role']
                
                analysis['roles_found'] = True
                analysis['role_details'][analysis['target_role']] = {
                    'role_name': role['RoleName'],
                    'role_arn': role['Arn'],
                    'creation_date': role['CreateDate'].isoformat() if 'CreateDate' in role else '',
                    'assume_role_policy': role.get('AssumeRolePolicyDocument', ''),
                    'path': role.get('Path', '/')
                }
                print(f"   ✅ Found IAM role: {analysis['target_role']}")
                
            except iam_client.exceptions.NoSuchEntityException:
                print(f"   🔍 No IAM role found: {analysis['target_role']}")
                
        except Exception as e:
            print(f"   ⚠️ Error discovering IAM roles: {e}")
        
        return analysis

    def _discover_existing_lambda_applications(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing Lambda Applications and Resource Groups"""
        
        print(f"🔍 Searching for existing Lambda Applications with prefix: {resource_prefix}")
        
        analysis = {
            'application_found': False,
            'resource_group_found': False,
            'application_details': {},
            'resource_group_details': {},
            'target_resource_group': f"{resource_prefix}-lambda-functions"
        }
        
        try:
            # Check for Resource Group first
            rg_client = self.session.client('resource-groups')
            
            # Try exact match first
            try:
                response = rg_client.get_group(GroupName=analysis['target_resource_group'])
                group = response['Group']
                
                analysis['resource_group_found'] = True
                analysis['resource_group_details'] = {
                    'name': group['Name'],
                    'arn': group['GroupArn'],
                    'description': group.get('Description', ''),
                    'creation_date': group.get('CreationDate', '')
                }
                print(f"   ✅ Found Resource Group: {analysis['target_resource_group']}")
                
            except rg_client.exceptions.NotFoundException:
                print(f"   🔍 Exact Resource Group not found: {analysis['target_resource_group']}")
                
                # Try to find similar resource groups
                try:
                    response = rg_client.list_groups()
                    
                    for group_info in response.get('GroupIdentifiers', []):
                        group_name = group_info.get('GroupName', '')
                        if resource_prefix in group_name and 'lambda' in group_name.lower():
                            # Found a similar group, get details
                            try:
                                group_response = rg_client.get_group(GroupName=group_name)
                                group = group_response['Group']
                                
                                analysis['resource_group_found'] = True
                                analysis['resource_group_details'] = {
                                    'name': group['Name'],
                                    'arn': group['GroupArn'],
                                    'description': group.get('Description', ''),
                                    'creation_date': group.get('CreationDate', '')
                                }
                                analysis['target_resource_group'] = group_name  # Update target
                                print(f"   ✅ Found similar Resource Group: {group_name}")
                                break
                                
                            except Exception as e:
                                print(f"   ⚠️ Error getting details for group {group_name}: {e}")
                                continue
                    
                    if not analysis['resource_group_found']:
                        print(f"   🔍 No similar Resource Groups found")
                        
                except Exception as e:
                    print(f"   ⚠️ Error listing resource groups: {e}")
            
            # Check for Application Insights Application
            try:
                appinsights_client = self.session.client('application-insights')
                
                # List all applications
                response = appinsights_client.list_applications()
                
                target_resource_group = analysis['target_resource_group']
                
                for app in response.get('ApplicationInfoList', []):
                    app_resource_group = app.get('ResourceGroupName', '')
                    
                    # Check exact match first
                    if app_resource_group == target_resource_group:
                        analysis['application_found'] = True
                        analysis['application_details'] = {
                            'resource_group_name': app_resource_group,
                            'creation_time': app.get('CreationTime', ''),
                            'lifecycle': app.get('LifeCycle', ''),
                            'remarks': app.get('Remarks', ''),
                            'application_arn': app.get('ApplicationArn', '')
                        }
                        print(f"   ✅ Found Application Insights Application for: {app_resource_group}")
                        break
                    
                    # Check partial match
                    elif resource_prefix in app_resource_group and 'lambda' in app_resource_group.lower():
                        analysis['application_found'] = True
                        analysis['application_details'] = {
                            'resource_group_name': app_resource_group,
                            'creation_time': app.get('CreationTime', ''),
                            'lifecycle': app.get('LifeCycle', ''),
                            'remarks': app.get('Remarks', ''),
                            'application_arn': app.get('ApplicationArn', '')
                        }
                        print(f"   ✅ Found similar Application Insights Application: {app_resource_group}")
                        break
                
                if not analysis['application_found']:
                    print(f"   🔍 No Application Insights Application found for: {target_resource_group}")
                    
            except Exception as e:
                print(f"   ⚠️ Error checking Application Insights: {e}")
                
            # Additional check: Look for CloudFormation-managed applications
            if not analysis['application_found'] and hasattr(self, 'cf_client'):
                try:
                    # This would be handled by the CloudFormation resource discovery
                    print(f"   🔍 Checking CloudFormation for Application Insights resources...")
                    
                except Exception as e:
                    print(f"   ⚠️ Error checking CloudFormation for applications: {e}")
                
        except Exception as e:
            print(f"   ⚠️ Error discovering Lambda Applications: {e}")
        
        return analysis

    def _generate_comprehensive_recommendations(self, discovery_results: Dict[str, Any], force_create_new: bool = False) -> Dict[str, Any]:
        """Generate comprehensive recommendations based on discovery results"""
        
        recommendations = {
            'overall_strategy': 'mixed',  # 'reuse_all', 'create_all', 'mixed'
            'vpc_recommendation': discovery_results['vpc_analysis']['recommendation'],
            'dynamodb_recommendations': discovery_results['dynamodb_analysis']['recommendation'],
            'memorydb_recommendation': discovery_results['memorydb_analysis']['recommendation'],
            'cost_impact': 'medium',
            'risk_level': 'low',
            'deployment_time_estimate': '15-25 minutes',
            'summary': []
        }
        
        if force_create_new:
            recommendations['overall_strategy'] = 'create_all_with_replacement'
            recommendations['cost_impact'] = 'high'
            recommendations['risk_level'] = 'high'
            recommendations['deployment_time_estimate'] = '25-35 minutes'
            recommendations['summary'].append("Force replacement mode - all resources will be recreated")
            return recommendations
        
        # Analyze overall strategy
        reuse_count = 0
        total_resources = 4  # VPC, MemoryDB, DynamoDB (3 tables counted as 1), IAM/KMS
        
        if discovery_results['vpc_analysis']['recommendation'] == 'reuse_existing':
            reuse_count += 1
        if discovery_results['memorydb_analysis']['recommendation'] == 'reuse':
            reuse_count += 1
        if any(rec == 'reuse' for rec in discovery_results['dynamodb_analysis']['recommendation'].values()):
            reuse_count += 1
        
        if reuse_count == 0:
            recommendations['overall_strategy'] = 'create_all'
            recommendations['cost_impact'] = 'high'
            recommendations['deployment_time_estimate'] = '20-30 minutes'
            recommendations['summary'].append("Creating all new resources - clean deployment")
        elif reuse_count == total_resources:
            recommendations['overall_strategy'] = 'reuse_all'
            recommendations['cost_impact'] = 'low'
            recommendations['deployment_time_estimate'] = '10-15 minutes'
            recommendations['summary'].append("Reusing all existing resources - fast deployment")
        else:
            recommendations['overall_strategy'] = 'mixed'
            recommendations['cost_impact'] = 'medium'
            recommendations['deployment_time_estimate'] = '15-25 minutes'
            recommendations['summary'].append("Mixed approach - some reuse, some new resources")
        
        return recommendations

    def _print_discovery_summary(self, discovery_results: Dict[str, Any]):
        """Print comprehensive discovery summary"""
        
        print("\n" + "="*100)
        print("📋 COMPREHENSIVE DISCOVERY SUMMARY")
        print("="*100)
        
        vpc_analysis = discovery_results['vpc_analysis']
        dynamodb_analysis = discovery_results['dynamodb_analysis']
        memorydb_analysis = discovery_results['memorydb_analysis']
        kms_analysis = discovery_results.get('kms_analysis', {})
        iam_analysis = discovery_results.get('iam_analysis', {})
        lambda_app_analysis = discovery_results.get('lambda_application_analysis', {})
        secrets_analysis = discovery_results.get('secrets_manager_analysis', {})
        apigw_analysis = discovery_results.get('api_gateway_analysis', {})
        recommendations = discovery_results['recommendations']
        
        print(f"🏗️  INFRASTRUCTURE ANALYSIS:")
        print(f"   VPC: {len(vpc_analysis['existing_vpcs'])} found, {len(vpc_analysis['suitable_vpcs'])} suitable → {vpc_analysis['recommendation'].upper()}")
        print(f"   MemoryDB: {'EXISTS' if memorydb_analysis['cluster_exists'] else 'NOT FOUND'} → {memorydb_analysis['recommendation'].upper()}")
        print(f"   KMS Key: {'EXISTS' if kms_analysis.get('keys_found') else 'NOT FOUND'} → {'REUSE' if kms_analysis.get('alias_found') else 'CREATE NEW'}")
        print(f"   IAM Role: {'EXISTS' if iam_analysis.get('roles_found') else 'NOT FOUND'} → {'REUSE' if iam_analysis.get('roles_found') else 'CREATE NEW'}")
        print(f"   Lambda Application: {'EXISTS' if lambda_app_analysis.get('application_found') else 'NOT FOUND'} → {'REUSE' if lambda_app_analysis.get('application_found') and lambda_app_analysis.get('resource_group_found') else 'CREATE NEW'}")
        print(f"   Secrets Manager: {'EXISTS' if secrets_analysis.get('secrets_found') else 'NOT FOUND'} → {'REUSE' if secrets_analysis.get('secrets_found') else 'CREATE NEW'}")
        print(f"   API Gateway: {'EXISTS' if apigw_analysis.get('api_exists') else 'NOT FOUND'} → {'REUSE' if apigw_analysis.get('api_exists') else 'CREATE NEW'}")
        print(f"   Resource Group: {'EXISTS' if lambda_app_analysis.get('resource_group_found') else 'NOT FOUND'}")
        print(f"   DynamoDB Tables:")
        for table_type, rec in dynamodb_analysis['recommendation'].items():
            exists = dynamodb_analysis['table_analysis'][table_type]['exists']
            print(f"      {table_type.title()}: {'EXISTS' if exists else 'NOT FOUND'} → {rec.upper()}")
        
        print(f"\n🎯 OVERALL RECOMMENDATION:")
        print(f"   Strategy: {recommendations['overall_strategy'].upper()}")
        print(f"   Cost Impact: {recommendations['cost_impact'].upper()}")
        print(f"   Risk Level: {recommendations['risk_level'].upper()}")
        print(f"   Estimated Deployment Time: {recommendations['deployment_time_estimate']}")
        
        if recommendations['summary']:
            print(f"\n💡 SUMMARY:")
            for summary_point in recommendations['summary']:
                print(f"   • {summary_point}")
        
        # Additional resource details
        if kms_analysis.get('keys_found'):
            key_details = kms_analysis.get('key_details', {})
            for alias, details in key_details.items():
                print(f"\n🔑 KMS Key Details:")
                print(f"   Alias: {alias}")
                print(f"   Key ID: {details.get('key_id', 'N/A')}")
        
        if iam_analysis.get('roles_found'):
            role_details = iam_analysis.get('role_details', {})
            for role_name, details in role_details.items():
                print(f"\n👤 IAM Role Details:")
                print(f"   Role Name: {role_name}")
                print(f"   Role ARN: {details.get('role_arn', 'N/A')}")
        
        if lambda_app_analysis.get('resource_group_found'):
            rg_details = lambda_app_analysis.get('resource_group_details', {})
            print(f"\n📦 Resource Group Details:")
            print(f"   Name: {rg_details.get('name', 'N/A')}")
            print(f"   ARN: {rg_details.get('arn', 'N/A')}")
        
        if lambda_app_analysis.get('application_found'):
            app_details = lambda_app_analysis.get('application_details', {})
            print(f"\n📊 Application Insights Details:")
            print(f"   Resource Group: {app_details.get('resource_group_name', 'N/A')}")
            print(f"   Lifecycle: {app_details.get('lifecycle', 'N/A')}")
        
        print("="*100)

    def _get_empty_discovery_results(self) -> Dict[str, Any]:
        """Return empty discovery results when clients are not available"""
        return {
            'vpc_analysis': {'recommendation': 'create_new', 'existing_vpcs': [], 'suitable_vpcs': []},
            'dynamodb_analysis': {'recommendation': {'config': 'create_new', 'stats': 'create_new'}},
            'memorydb_analysis': {'recommendation': 'create_new', 'cluster_exists': False},
            'recommendations': {'overall_strategy': 'create_all'}
        }

class GameStatsLeaderboardsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Initialize CloudFormation client for resource discovery
        try:
            self.session = boto3.Session(region_name=self.region)
            self.cf_client = self.session.client('cloudformation')
            print(f"✅ CloudFormation client initialized for region: {self.region}")
        except Exception as e:
            print(f"⚠️  Warning: Could not initialize CloudFormation client: {e}")
            self.cf_client = None

        self._debug_stack_info()

        # Environment configuration from context
        environment = self.node.try_get_context("environment") or "dev"
        config = self.node.try_get_context("environments").get(environment) if self.node.try_get_context("environments") else {}
        service_name = "game-statsleaderboards"
        resource_prefix = f"{service_name}-{environment}"

        # Get deployment mode settings with proper string handling
        force_create_new = self.node.try_get_context("force_create_new")
        skip_resource_discovery = self.node.try_get_context("skip_resource_discovery")
        enable_resource_reuse = self.node.try_get_context("enable_resource_reuse")
        create_lambda_application = self.node.try_get_context("create_lambda_application")

        # Convert string values to boolean (CDK context passes strings)
        def to_bool(value):
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return bool(value) if value is not None else None

        force_create_new = to_bool(force_create_new)
        skip_resource_discovery = to_bool(skip_resource_discovery)
        enable_resource_reuse = to_bool(enable_resource_reuse)
        create_lambda_application = to_bool(create_lambda_application)

        # Force delete conflicting resources if needed
        if force_create_new:
            self._force_delete_conflicting_resources(resource_prefix)

        # Default values
        if enable_resource_reuse is None:
            enable_resource_reuse = True
        if create_lambda_application is None:
            create_lambda_application = True
        if force_create_new is None:
            force_create_new = False
        if skip_resource_discovery is None:
            skip_resource_discovery = False

        # Add common tags
        Tags.of(self).add("Environment", environment)
        Tags.of(self).add("Service", service_name)
        Tags.of(self).add("ManagedBy", "CDK")
        Tags.of(self).add("ValkeyGlideVersion", "2.0.1+")
        Tags.of(self).add("StackType", "Core")
        Tags.of(self).add("Application", "GameStatsLeaderboards")

        # Initialize a flag to track if registration has been created
        self._registration_provider = None
        self._developer_registration_created = False
        
        # Developer Registration Parameters with validation
        self.studio_name = CfnParameter(
            self, "StudioName",
            type="String",
            description="Name of the game studio (e.g., 'Cosmic Games')",
            min_length=1,
            max_length=100,
            allowed_pattern=r'^[a-zA-Z0-9\s\-_.()!&@]+$',
            constraint_description="Only letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, dollar, and at signs are allowed"
        )
        
        self.contact_email = CfnParameter(
            self, "ContactEmail",
            type="String", 
            description="Contact email for the studio (e.g., 'contact@cosmicgames.com')",
            allowed_pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
            constraint_description="Must be a valid email address"
        )
        
        self.game_title = CfnParameter(
            self, "GameTitle",
            type="String",
            description="Title of the game (e.g., 'Stellar Odyssey')",
            min_length=1,
            max_length=100,
            allowed_pattern=r'^[a-zA-Z0-9\s\-_.()!&@]+$',
            constraint_description="Only letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, dollar, and at signs are allowed"
        )
        
        self.game_genre = CfnParameter(
            self, "GameGenre",
            type="String",
            description="Genre of the game (e.g., 'space-rpg')",
            min_length=1,
            max_length=50,
            allowed_pattern=r'^[a-zA-Z0-9\s\-_.()!&@]+$',
            constraint_description="Only letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, dollar, and at signs are allowed"
        )

        # Create comprehensive resource group for ALL resources
        comprehensive_resource_group = self._create_comprehensive_resource_group(resource_prefix, environment)

        print(f"\n🚀 DEPLOYING GAME STATS & LEADERBOARDS STACK")
        print(f"   Environment: {environment}")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account}")
        print(f"   Force Create New: {force_create_new}")
        print(f"   Enable Resource Reuse: {enable_resource_reuse}")
        print(f"   Create Lambda Application: {create_lambda_application}")

        # Print replacement warnings if needed
        if force_create_new:
            self._print_replacement_warnings()

        # Get resource decisions based on discovery and context
        resource_decisions = self._get_resource_decisions(
            service_name, environment, resource_prefix, 
            force_create_new, skip_resource_discovery, enable_resource_reuse
        )

        # Store decisions for reference
        self.resource_decisions = resource_decisions

        if self._should_skip_deployment():
            print("🎯 Skipping all resource creation - everything is already deployed and up to date")

            # Create minimal outputs for compatibility
            CfnOutput(
                self, "DeploymentStatus",
                value="no-changes-needed",
                description="All resources are up to date - no deployment was needed",
                export_name=f"{self.stack_name}-DeploymentStatus"
            )
            
            CfnOutput(
                self, "LastCheckTimestamp", 
                value=datetime.now(timezone.utc).isoformat(),
                description="Timestamp of last deployment check",
                export_name=f"{self.stack_name}-LastCheck"
            )
            
            # Create a simple custom resource that does nothing but allows CDK to "deploy"
            noop_resource = CustomResource(
                self, f"{resource_prefix}-noop-deployment",
                service_token=self._create_noop_provider().service_token,
                properties={
                    "Action": "NO_OP",
                    "Timestamp": datetime.now(timezone.utc).isoformat(),
                    "Message": "All resources are up to date - no changes needed"
                }
            )
            
            return  # Exit early

        # Create KMS key (preferably, always create new for improved security)
        kms_key = self._create_kms_key(resource_prefix)
        
        # Create or reuse VPC based on decisions
        vpc, memorydb_sg, vpc_reused = self._handle_vpc_creation(
            resource_prefix, service_name, environment, resource_decisions
        )
        
        # Create or reuse MemoryDB cluster based on decisions
        memorydb_cluster, memorydb_password, memorydb_reused = self._handle_memorydb_creation(
            resource_prefix, vpc, memorydb_sg, config, resource_decisions
        )
        
        # Create or reuse DynamoDB tables based on decisions
        config_table, stats_table, tables_reused = self._handle_dynamodb_creation(
            resource_prefix, kms_key, config, resource_decisions, environment
        )

        # Create developer registration during deployment with error handling
        print(f"🔍 DEBUG: About to check if developer registration should be created")

        developer_registration = None
        if self._should_create_developer_registration(resource_prefix, environment):
            print(f"🔍 DEBUG: Proceeding with developer registration creation")
            developer_registration = self._create_developer_registration_during_deployment(
                resource_prefix, environment
            )
            self._developer_registration_created = True
            print(f"✅ Developer registration created successfully")
        else:
            print(f"⚠️ Developer registration creation skipped - already exists or conditions not met")
            # Don't create mock objects - let the deployment fail if registration is required
            raise Exception("Developer registration is required but conditions not met")

        # Create Lambda Layer with shared dependencies
        shared_layer = self._create_shared_layer(resource_prefix)
        
        # Create Lambda execution role
        lambda_roles = self._create_lambda_roles(
            resource_prefix, config_table, stats_table, memorydb_password
        )
        
        # Create Lambda Application (if enabled)
        lambda_application = None
        resource_group = None
        if create_lambda_application:
            # Check if we should reuse existing Lambda Application
            lambda_app_decision = resource_decisions.get('lambda_application_decision', 'create_new')
            
            if lambda_app_decision == 'reuse':
                print(f"🔄 Skipping Lambda Application creation - reusing existing resources")
                # Create mock objects for compatibility but don't deploy them
                
                discovery_results = resource_decisions.get('discovery_results', {})
                lambda_app_analysis = discovery_results.get('lambda_application_analysis', {})
                rg_details = lambda_app_analysis.get('resource_group_details', {})
                rg_name = rg_details.get('name', f"{resource_prefix}-lambda-functions")
                
                # Create a mock resource group object for reference
                class MockResourceGroup:
                    def __init__(self, name, region, account):
                        self.name = name
                        self.region = region
                        self.account = account
                        self.attr_arn = f"arn:aws:resource-groups:{self.region}:{self.account}:group/{name}"

                class MockApplication:
                    def __init__(self, rg_name):
                        self.resource_group_name = rg_name
                
                resource_group = MockResourceGroup(rg_name, self.region, self.account)
                lambda_application = MockApplication(rg_name)
                
                print(f"✅ Using existing Lambda Application: {rg_name}")
            else:
                print(f"🆕 Creating new Lambda Application")
                resource_group, lambda_application = self._create_lambda_application(resource_prefix, environment)
        
        # Create Lambda functions
        lambda_functions = self._create_lambda_functions_with_application(
            resource_prefix, vpc, lambda_roles, config_table, stats_table,
            memorydb_cluster, memorydb_password, shared_layer, config, lambda_application, resource_group
        )

        # Create or reuse REST API with smart WAF handling
        api = self._handle_api_gateway_creation(resource_prefix, environment, lambda_functions, resource_decisions)

        # Update API_ENDPOINT in Lambda functions only if needed
        self._update_lambda_api_endpoints(lambda_functions, api, environment)
        
        # Store configuration in SSM
        if not self._should_skip_resource_creation('ssm_parameters', resource_decisions):
            self._create_ssm_parameters(
                resource_prefix, memorydb_cluster, memorydb_password, 
                config_table, stats_table, api, environment
            )
        else:
            print(f"🔄 Skipping SSM parameters creation - reusing existing parameters")
        
        # Enable SSM Parameter Store higher throughput for better performance
        self._enable_ssm_higher_throughput(resource_prefix)
        
        # WAF creation is handled in _handle_api_gateway_creation method
        # Removed duplicate call to avoid construct ID collision
        
        # Create basic monitoring dashboard
        self._create_basic_monitoring(resource_prefix, api, memorydb_cluster)
        
        # Create comprehensive outputs
        self._create_comprehensive_outputs(
            api, memorydb_cluster, config_table, stats_table, shared_layer, lambda_roles, vpc, memorydb_password,
            vpc_reused, memorydb_reused, tables_reused, developer_registration, lambda_application, resource_group, environment, comprehensive_resource_group
        )
        
        # Print deployment summary
        self._print_deployment_summary(vpc_reused, memorydb_reused, tables_reused, create_lambda_application)

    def _create_rest_api(
        self, resource_prefix: str, environment: str, lambda_functions: Dict[str, lambda_.Function]
    ) -> apigw.RestApi:
        """Create REST API with Lambda authorizer and proper staging with automatic deployment"""
        
        print(f"🆕 Creating new REST API: {resource_prefix}-restapi")
        print(f"🎯 Target stage: {environment}")

        # Validate environment to prevent naming conflicts
        valid_environments = ['dev', 'staging', 'prod']
        if environment not in valid_environments:
            raise ValueError(f"Environment '{environment}' must be one of: {valid_environments}")

        # Full request/response body logging (API Gateway "data trace"). Default:
        # off in prod, on in dev/staging. Operators can override per deploy with CDK
        # context: `-c data_trace_enabled=true` (or false). Accepts bool or string.
        data_trace_override = self.node.try_get_context("data_trace_enabled")
        if data_trace_override is None:
            data_trace_enabled = environment != "prod"
        else:
            data_trace_enabled = str(data_trace_override).lower() in ("true", "1", "yes")

        # Create backend Lambda authorizer (StudioAPI Key via SSM Parameter Store)
        authorizer = apigw.RequestAuthorizer(
            self, f"{resource_prefix}-authorizer",
            handler=lambda_functions["backend_authorizer"],
            identity_sources=[ apigw.IdentitySource.header('Authorization') ],
            authorizer_name=f"{resource_prefix}-authorizer",
            results_cache_ttl=Duration.minutes(5)
        )
        
        # Potential INTEGRATION POINT — Player Authorizer
        # Uses auth/playerAuthorizer.py — add your player token validation there,
        # or replace this with your own authorizer Lambda function.
        player_authorizer = apigw.RequestAuthorizer(
            self, f"{resource_prefix}-player-apigw-authorizer",
            handler=lambda_functions["player_authorizer"],
            identity_sources=[ apigw.IdentitySource.header('Authorization') ],
            authorizer_name=f"{resource_prefix}-player-authorizer",
            results_cache_ttl=Duration.minutes(5)
        )
        
        # Create REST API with proper CORS configuration - NO automatic deployment
        api = apigw.RestApi(
            self, f"{resource_prefix}-rest-api",
            rest_api_name=f"{resource_prefix}-restapi",
            description="REST API for game stats and leaderboards component",
            deploy=False,
            default_cors_preflight_options=apigw.CorsOptions(                                   # Updated CORS configuration for CDK v2
                allow_origins=["*"],                                                            # Use array instead of deprecated Cors.ALL_ORIGINS
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],                      # Explicit resource methods
                allow_headers=[
                    "Content-Type",
                    "Authorization", 
                    "X-Api-Key",
                    "X-Glide-Client-Id",
                    "X-Amz-Date",
                    "X-Amz-Security-Token"
                ],
                allow_credentials=True
            ),
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
            min_compression_size=Size.kibibytes(1)
        )
        
        # Create API resources and methods
        self._create_api_resources_and_methods(api, authorizer, player_authorizer, lambda_functions, resource_prefix)

        # Create a single deployment with unique logical ID
        deployment = apigw.Deployment(
            self, f"{resource_prefix}-api-deployment-{environment}",  # Environment-specific ID
            api=api,
            description=f"Deployment for {environment} environment - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        # Create a single stage with proper configuration
        # THROTTLING: We intentionally do NOT set explicit throttling_rate_limit or
        # throttling_burst_limit here. This lets the stage inherit the account-level
        # API Gateway defaults (10,000 rate / 5,000 burst for standard accounts).
        # This ensures the deployment works on any AWS account without requiring
        # manual Service Quotas increases. If a customer has requested higher limits,
        # they automatically benefit. Explicit stage limits can be set post-deployment
        # via the console or CLI if needed for a specific environment.
        stage = apigw.Stage(
            self, f"{resource_prefix}-api-stage-{environment}",  # Environment-specific ID
            deployment=deployment,
            stage_name=environment,
            description=f"API stage for {environment} environment",
            caching_enabled=environment in ["prod", "staging"],
            cache_cluster_enabled=environment in ["prod", "staging"],
            cache_cluster_size="0.5" if environment in ["prod", "staging"] else None,
            variables={
                "environment": environment,
                "glideVersion": "2.0.1",
                "lambdaRuntime": "python3.13"
            },
            method_options={
                "/*/*": apigw.MethodDeploymentOptions(
                    logging_level=apigw.MethodLoggingLevel.INFO,
                    # data_trace logs full request/response bodies to CloudWatch. Off by
                    # default in prod (avoids logging player payloads and reduces log cost);
                    # on in dev/staging for debugging. Override with CDK context
                    # -c data_trace_enabled=true|false.
                    data_trace_enabled=data_trace_enabled,
                    metrics_enabled=True,
                    caching_enabled=environment in ["prod", "staging"]
                )
            }
        )

        # ✅ Ensure proper dependency order
        deployment.node.add_dependency(api)
        stage.node.add_dependency(deployment)

        print(f"✅ Created REST API deployment chain:")
        print(f"   📦 API: {api.rest_api_name} ({api.rest_api_id})")
        print(f"   🚀 Deployment: {deployment.deployment_id}")
        print(f"   🎭 Stage: {stage.stage_name}")
        print(f"   🌐 Endpoint: https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/{environment}/")
        print(f"   🚀 ** Note that API ID will be available after deployment completes, so it may be listed incorrectly above")
        print(f"   📋 ** Check CloudFormation outputs for the final endpoint URL")

        return api

    def _create_api_resources_and_methods(
        self, api: apigw.RestApi, authorizer: apigw.RequestAuthorizer,
        player_authorizer: apigw.RequestAuthorizer,
        lambda_functions: Dict[str, lambda_.Function], resource_prefix: str
    ):
        """Create API resources and methods with Lambda integrations"""
        
        # Create request/response models for developer registration
        
        # Developer Registration Request Model
        developer_registration_request_model = api.add_model(
            "DeveloperRegistrationRequest",
            content_type="application/json",
            model_name="DeveloperRegistrationRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Developer Registration Request",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegRequest": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "studioName": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                min_length=1,
                                max_length=100,
                                description="Name of the game development studio"
                            ),
                            "contactEmail": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                format="email",
                                description="Contact email of the developer"
                            ),
                            "gameTitle": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                min_length=1,
                                max_length=200,
                                description="Title of the game"
                            ),
                            "gameGenre": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                min_length=1,
                                max_length=50,
                                description="Genre of the game"
                            )
                        },
                        required=["studioName", "contactEmail", "gameTitle", "gameGenre"]
                    )
                },
                required=["devRegRequest"]
            )
        )
        
        # Developer Registration Response Model
        developer_registration_response_model = api.add_model(
            "DeveloperRegistrationResponse",
            content_type="application/json",
            model_name="DeveloperRegistrationResponse",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Developer Registration Response",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegResponse": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "success": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.BOOLEAN,
                                description="Whether the registration was successful"
                            ),
                            "message": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                description="Success or error message"
                            ),
                            "registration": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.OBJECT,
                                properties={
                                    "studioId": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "gameId": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "studioName": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "gameTitle": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "apiKey": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "permissions": apigw.JsonSchema(
                                        type=apigw.JsonSchemaType.ARRAY,
                                        items=apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                                    ),
                                    "rateLimit": apigw.JsonSchema(type=apigw.JsonSchemaType.NUMBER),
                                    "environment": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "registrationDate": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                                }
                            ),
                            "usage": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.OBJECT,
                                properties={
                                    "apiEndpoint": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "documentation": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                                    "supportEmail": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                                }
                            )
                        }
                    )
                },
                required=["devRegResponse"]
            )
        )
        
        # Key Regeneration Request Model
        key_regeneration_request_model = api.add_model(
            "KeyRegenerationRequest",
            content_type="application/json",
            model_name="KeyRegenerationRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="API Key Regeneration Request",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegRequest": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "studioId": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                min_length=1,
                                description="Studio identifier"
                            ),
                            "gameId": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                min_length=1,
                                description="Game identifier"
                            ),
                            "contactEmail": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.STRING,
                                format="email",
                                description="Contact email for verification"
                            )
                        },
                        required=["studioId", "gameId", "contactEmail"]
                    )
                },
                required=["devRegRequest"]
            )
        )
        
        # Key Regeneration Response Model
        key_regeneration_response_model = api.add_model(
            "KeyRegenerationResponse",
            content_type="application/json",
            model_name="KeyRegenerationResponse",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="API Key Regeneration Response",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegResponse": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "success": apigw.JsonSchema(type=apigw.JsonSchemaType.BOOLEAN),
                            "message": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "newApiKey": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "regenerationDate": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "note": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                        }
                    )
                },
                required=["devRegResponse"]
            )
        )
        
        # Developer Info Response Model
        developer_info_response_model = api.add_model(
            "DeveloperInfoResponse",
            content_type="application/json",
            model_name="DeveloperInfoResponse",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Developer Information Response",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegResponse": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "studioId": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "gameId": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "studioName": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "gameTitle": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "gameGenre": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "permissions": apigw.JsonSchema(
                                type=apigw.JsonSchemaType.ARRAY,
                                items=apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                            ),
                            "rateLimit": apigw.JsonSchema(type=apigw.JsonSchemaType.NUMBER),
                            "status": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "registrationDate": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "lastKeyRotation": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "environment": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                        }
                    )
                },
                required=["devRegResponse"]
            )
        )
        
        # Error Response Model
        error_response_model = api.add_model(
            "ErrorResponse",
            content_type="application/json",
            model_name="ErrorResponse",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Error Response",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegResponse": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "error": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "message": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "timestamp": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                        }
                    )
                },
                required=["devRegResponse"]
            )
        )

        # Key Revocation Response Model
        key_revocation_response_model = api.add_model(
            "KeyRevocationResponse",
            content_type="application/json",
            model_name="KeyRevocationResponse",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="API Key Revocation Response",
                type=apigw.JsonSchemaType.OBJECT,
                properties={
                    "devRegResponse": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.OBJECT,
                        properties={
                            "success": apigw.JsonSchema(type=apigw.JsonSchemaType.BOOLEAN),
                            "message": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING),
                            "revocationDate": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING)
                        }
                    )
                },
                required=["devRegResponse"]
            )
        )

        # ----------------------------------------------------------------------
        # Request models for the mutating /leaderboards endpoints. These validate
        # the wrapper key and required fields at the edge (defense-in-depth +
        # avoids invoking Lambda on malformed payloads). The handlers still do the
        # detailed value validation (bounds, patterns, config matching). Optional
        # fields are intentionally not enumerated so the model does not reject
        # valid requests as the API evolves; playerScore has no declared type
        # because it accepts both numbers and time strings.
        # ----------------------------------------------------------------------
        STR = apigw.JsonSchemaType.STRING
        OBJ = apigw.JsonSchemaType.OBJECT
        ARR = apigw.JsonSchemaType.ARRAY

        leaderboard_config_request_model = api.add_model(
            "LeaderboardConfigRequest",
            content_type="application/json",
            model_name="LeaderboardConfigRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Leaderboard Config Request",
                type=OBJ,
                properties={
                    "gameLeaderboardConfigRequest": apigw.JsonSchema(
                        type=OBJ,
                        properties={
                            "gameID": apigw.JsonSchema(type=STR, min_length=1),
                            "gameMode": apigw.JsonSchema(type=STR, min_length=1),
                            "leaderboardName": apigw.JsonSchema(type=STR, min_length=1),
                            "statAttributeForLeaderboard": apigw.JsonSchema(type=STR, min_length=1),
                            "leaderboardType": apigw.JsonSchema(type=STR, min_length=1),
                            "scoreStrategy": apigw.JsonSchema(type=STR, min_length=1),
                        },
                        required=["gameID", "gameMode", "leaderboardName",
                                  "statAttributeForLeaderboard", "leaderboardType", "scoreStrategy"],
                    )
                },
                required=["gameLeaderboardConfigRequest"],
            ),
        )

        store_stats_request_model = api.add_model(
            "StoreStatsRequest",
            content_type="application/json",
            model_name="StoreStatsRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Store Stats Request",
                type=OBJ,
                properties={
                    "gameReportBody": apigw.JsonSchema(
                        type=OBJ,
                        properties={
                            "playerID": apigw.JsonSchema(type=STR, min_length=1),
                            "gameID": apigw.JsonSchema(type=STR, min_length=1),
                            "gameMode": apigw.JsonSchema(type=STR, min_length=1),
                            "leaderboardName": apigw.JsonSchema(type=STR, min_length=1),
                            "fullRawGameReport": apigw.JsonSchema(type=OBJ),
                        },
                        required=["playerID", "gameID", "gameMode", "playerScore",
                                  "leaderboardName", "fullRawGameReport"],
                    )
                },
                required=["gameReportBody"],
            ),
        )

        batch_store_stats_request_model = api.add_model(
            "BatchStoreStatsRequest",
            content_type="application/json",
            model_name="BatchStoreStatsRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Batch Store Stats Request",
                type=OBJ,
                properties={
                    "batchGameReportBody": apigw.JsonSchema(
                        type=OBJ,
                        properties={
                            "gameReports": apigw.JsonSchema(
                                type=ARR,
                                min_items=1,
                                items=apigw.JsonSchema(
                                    type=OBJ,
                                    properties={
                                        "playerID": apigw.JsonSchema(type=STR, min_length=1),
                                        "gameID": apigw.JsonSchema(type=STR, min_length=1),
                                        "gameMode": apigw.JsonSchema(type=STR, min_length=1),
                                        "leaderboardName": apigw.JsonSchema(type=STR, min_length=1),
                                    },
                                    required=["playerID", "gameID", "gameMode",
                                              "playerScore", "leaderboardName"],
                                ),
                            )
                        },
                        required=["gameReports"],
                    )
                },
                required=["batchGameReportBody"],
            ),
        )

        reset_leaderboard_request_model = api.add_model(
            "ResetLeaderboardRequest",
            content_type="application/json",
            model_name="ResetLeaderboardRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Reset Leaderboard Request",
                type=OBJ,
                properties={
                    "resetLeaderboardRequest": apigw.JsonSchema(
                        type=OBJ,
                        properties={
                            "leaderboardName": apigw.JsonSchema(type=STR, min_length=1),
                        },
                        required=["leaderboardName"],
                    )
                },
                required=["resetLeaderboardRequest"],
            ),
        )

        rebuild_leaderboard_request_model = api.add_model(
            "RebuildLeaderboardRequest",
            content_type="application/json",
            model_name="RebuildLeaderboardRequest",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Rebuild Leaderboard Request",
                type=OBJ,
                properties={
                    "rebuildLeaderboardRequest": apigw.JsonSchema(
                        type=OBJ,
                        properties={
                            "leaderboardName": apigw.JsonSchema(type=STR, min_length=1),
                        },
                        required=["leaderboardName"],
                    )
                },
                required=["rebuildLeaderboardRequest"],
            ),
        )

        # Update API structure with proper request/response models
        api_structure = {
            "developer": {
                "methods": {},                                  # No API at root level
                "resources": {
                    "register": {
                        "methods": {
                            "POST": ("developer_registration", True, "register", {
                                "request_models": {"application/json": developer_registration_request_model},
                                "response_models": {
                                    "201": developer_registration_response_model,
                                    "400": error_response_model,
                                    "409": error_response_model,
                                    "500": error_response_model
                                }
                            })
                        }
                    },
                    "regenerate-key": {
                        "methods": {
                            "POST": ("developer_registration", True, "regenerate-key", {
                                "request_models": {"application/json": key_regeneration_request_model},
                                "response_models": {
                                    "200": key_regeneration_response_model,
                                    "400": error_response_model,
                                    "403": error_response_model,
                                    "404": error_response_model,
                                    "500": error_response_model
                                }
                            })
                        }
                    },
                    "info": {
                        "methods": {
                            "GET": ("developer_registration", True, "info", {
                                "request_parameters": {
                                    "method.request.querystring.studioId": True,
                                    "method.request.querystring.gameId": True
                                },
                                "response_models": {
                                    "200": developer_info_response_model,
                                    "400": error_response_model,
                                    "404": error_response_model,
                                    "500": error_response_model
                                }
                            })
                        }
                    },
                    "revoke": {
                        "methods": {
                            "PUT": ("developer_registration", True, "revoke", {
                                "request_models": {"application/json": key_regeneration_request_model},
                                "response_models": {
                                    "200": key_revocation_response_model,
                                    "400": error_response_model,
                                    "403": error_response_model,
                                    "404": error_response_model,
                                    "500": error_response_model
                                }
                            })
                        }
                    }
                }
            },
            "leaderboards": {
                "resources": {
                    "config": {
                        "methods": {
                            "POST": ("leaderboards_config", True, "create", {
                                "request_models": {"application/json": leaderboard_config_request_model}
                            }),
                            "GET": ("leaderboards_config", True, "get", {}),
                            "PUT": ("leaderboards_config", True, "update", {
                                "request_models": {"application/json": leaderboard_config_request_model}
                            }),
                            "DELETE": ("leaderboards_config", True, "delete", {})
                        },
                        "resources": {
                            "create": {
                                "methods": {
                                    "POST": ("leaderboards_config", True, "create", {
                                        "request_models": {"application/json": leaderboard_config_request_model}
                                    })
                                }
                            },
                            "get": {
                                "methods": {
                                    "POST": ("leaderboards_config", True, "get", {})
                                }
                            },
                            "all": {
                                "methods": {
                                    "POST": ("leaderboards_config", True, "get-all", {})
                                }
                            },
                            "update": {
                                "methods": {
                                    "PUT": ("leaderboards_config", True, "update", {
                                        "request_models": {"application/json": leaderboard_config_request_model}
                                    }),
                                    "POST": ("leaderboards_config", True, "update", {
                                        "request_models": {"application/json": leaderboard_config_request_model}
                                    })
                                }
                            },
                            "delete": {
                                "methods": {
                                    "DELETE": ("leaderboards_config", True, "delete", {}),
                                    "POST": ("leaderboards_config", True, "delete", {})
                                }
                            }
                        }
                    },
                    "configs": {
                        "methods": {
                            "GET": ("leaderboards_config", True, "get-all", {}),
                            "POST": ("leaderboards_config", True, "get-all", {})
                        }
                    },
                    "stats": {
                        "methods": {
                            "POST": ("player_store_stats", "player", "store", {
                                "request_models": {"application/json": store_stats_request_model}
                            })
                        },
                        "resources": {
                            "batch": {
                                "methods": {
                                    "POST": ("batch_store_stats", True, "batch-store", {
                                        "request_models": {"application/json": batch_store_stats_request_model}
                                    })
                                }
                            }
                        }
                    },
                    "player": {
                        "resources": {
                            "stats": {
                                "methods": {
                                    "POST": ("get_player_stats", "player", "get-player-stats", {})
                                }
                            },
                            "standing": {
                                "methods": {
                                    "POST": ("get_player_lb_standing", "player", "get-player-lb-standing", {})
                                }
                            }
                        }
                    },
                    "scores": {
                        "methods": {
                            "POST": ("get_leaderboard_scores", "player", "get-scores", {})
                        }
                    },
                    "admin": {
                        "resources": {
                            "reset": {
                                "methods": {
                                    "POST": ("reset_leaderboard", True, "reset", {
                                        "request_models": {"application/json": reset_leaderboard_request_model}
                                    })
                                }
                            },
                            "rebuild": {
                                "methods": {
                                    "POST": ("rebuild_leaderboard", True, "rebuild", {
                                        "request_models": {"application/json": rebuild_leaderboard_request_model}
                                    })
                                }
                            }
                        }
                    }
                }
            }
        }
        
        # Create resources and methods recursively
        self._create_resources_recursive(api, api.root, api_structure, authorizer, player_authorizer, lambda_functions)
        
        print(f"✅ Created REST API resources and methods with proper request/response models")

    def _create_resources_recursive(
        self, api: apigw.RestApi, parent_resource: apigw.Resource, 
        structure: Dict, authorizer: apigw.RequestAuthorizer,
        player_authorizer: apigw.RequestAuthorizer,
        lambda_functions: Dict[str, lambda_.Function]
    ):
        """Recursively create API resources and methods"""
        
        for resource_name, resource_config in structure.items():
            # Create resource
            resource = parent_resource.add_resource(resource_name)
            
            # Add methods if defined
            if "methods" in resource_config:
                for method, method_config in resource_config["methods"].items():
                    # Handle both old and new method configuration formats properly
                    func_name = None
                    requires_auth = False
                    operation = ""
                    api_config = {}
                    
                    if isinstance(method_config, tuple):
                        if len(method_config) == 4:
                            # New format: (func_name, requires_auth, operation, api_config)
                            func_name, requires_auth, operation, api_config = method_config
                        elif len(method_config) == 3:
                            # Old format: (func_name, requires_auth, operation)
                            func_name, requires_auth, operation = method_config
                            api_config = {}
                        else:
                            # Handle unexpected tuple lengths safely
                            print(f"⚠️ Unexpected method config format for {method}: {method_config}")
                            if len(method_config) >= 2:
                                func_name = method_config[0]
                                requires_auth = method_config[1]
                                operation = method_config[2] if len(method_config) > 2 else method.lower()
                            else:
                                print(f"❌ Invalid method config for {method}: {method_config}")
                                continue
                    else:
                        # Handle non-tuple configurations
                        print(f"⚠️ Non-tuple method config for {method}: {method_config}")
                        continue
                    
                    # Validate that we have the required function
                    if not func_name or func_name not in lambda_functions:
                        print(f"❌ Lambda function '{func_name}' not found for method {method}")
                        continue
                    
                    target_function = lambda_functions[func_name]
                    
                    # Create Lambda integration with proper response handling
                    integration = apigw.LambdaIntegration(
                        target_function,
                        proxy=True,
                        allow_test_invoke=True
                    )
                    
                    # Add method with proper models and validation
                    method_options = {}
                    
                    # Set authorization
                    if requires_auth == "player":
                        method_options["authorization_type"] = apigw.AuthorizationType.CUSTOM
                        method_options["authorizer"] = player_authorizer
                    elif requires_auth:
                        method_options["authorization_type"] = apigw.AuthorizationType.CUSTOM
                        method_options["authorizer"] = authorizer
                    else:
                        method_options["authorization_type"] = apigw.AuthorizationType.NONE
                    
                    # Add request models if specified
                    if "request_models" in api_config:
                        method_options["request_models"] = api_config["request_models"]
                    
                    # Add request parameters if specified
                    if "request_parameters" in api_config:
                        method_options["request_parameters"] = api_config["request_parameters"]
                    
                    # Add request validator for models
                    if "request_models" in api_config or "request_parameters" in api_config:
                        if not hasattr(self, '_request_validator'):
                            self._request_validator = api.add_request_validator(
                                "RequestValidator",
                                validate_request_body=True,
                                validate_request_parameters=True
                            )
                        method_options["request_validator"] = self._request_validator
                    
                    # Add method responses if specified
                    if "response_models" in api_config:
                        method_responses = []
                        for status_code, response_model in api_config["response_models"].items():
                            method_responses.append(apigw.MethodResponse(
                                status_code=status_code,
                                response_models={"application/json": response_model} if response_model else None,
                                response_parameters={
                                    "method.response.header.X-Request-ID": True,
                                    "method.response.header.Content-Type": True
                                }
                            ))
                        method_options["method_responses"] = method_responses
                    
                    try:
                        resource.add_method(method, integration, **method_options)
                        print(f"✅ Added {method} method to /{resource_name} -> {func_name}")
                    except Exception as e:
                        print(f"❌ Error adding {method} method to /{resource_name}: {e}")

            # Recursively create sub-resources
            if "resources" in resource_config:
                self._create_resources_recursive(api, resource, resource_config["resources"], authorizer, player_authorizer, lambda_functions)

    def _update_lambda_api_endpoints(self, lambda_functions: Dict[str, lambda_.Function], 
                                api: apigw.RestApi, environment: str):
        """Update Lambda functions with the actual API endpoint after API creation - only if needed"""
        
        # Check if Lambda functions were reused
        resource_decisions = getattr(self, 'resource_decisions', {})
        lambda_decision = resource_decisions.get('lambda_decision', 'create_new')
        
        if lambda_decision == 'reuse':
            print(f"🔄 Lambda functions were reused - skipping API endpoint updaters")
            return
        
        api_endpoint = f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/{environment}/"
        
        print(f"🆕 Creating API endpoint updaters for new Lambda functions")
        
        for func_name, function in lambda_functions.items():
            try:
                self._create_api_endpoint_updater(function, api_endpoint, func_name)
            except Exception as e:
                print(f"⚠️ Could not create API endpoint updater for {func_name}: {e}")

    def _create_api_endpoint_updater(self, function: lambda_.Function, api_endpoint: str, func_name: str):
        """Create custom resource to update Lambda environment variables"""
        
        updater_code = textwrap.dedent(f'''
            import boto3
            import json

            def handler(event, context):
                if event['RequestType'] == 'Create' or event['RequestType'] == 'Update':
                    lambda_client = boto3.client('lambda')
                    
                    try:
                        # Get current function configuration
                        response = lambda_client.get_function_configuration(
                            FunctionName='{function.function_name}'
                        )
                        
                        # Update environment variables
                        current_env = response.get('Environment', {{}}).get('Variables', {{}})
                        current_env['API_ENDPOINT'] = '{api_endpoint}'
                        
                        # Update function configuration
                        lambda_client.update_function_configuration(
                            FunctionName='{function.function_name}',
                            Environment={{'Variables': current_env}}
                        )
                        
                        return {{'Status': 'SUCCESS', 'PhysicalResourceId': 'api-endpoint-updater-{func_name}'}}
                        
                    except Exception as e:
                        print(f"Error updating function: {{e}}")
                        return {{'Status': 'FAILED', 'Reason': str(e)}}
                
                return {{'Status': 'SUCCESS', 'PhysicalResourceId': 'api-endpoint-updater-{func_name}'}}
        ''').strip()

        # Create log group first to avoid deprecation warning
        updater_log_group = logs.LogGroup(
            self, f"ApiEndpointUpdater{func_name.replace('_', '')}-logs",
            log_group_name=f"/aws/lambda/ApiEndpointUpdater{func_name.replace('_', '')}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )

        updater_lambda = lambda_.Function(
            self, f"ApiEndpointUpdater{func_name.replace('_', '')}",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(updater_code),
            timeout=Duration.minutes(2),
            log_group=updater_log_group
        )

        updater_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:GetFunctionConfiguration",
                    "lambda:UpdateFunctionConfiguration"
                ],
                resources=[function.function_arn]
            )
        )

        updater_provider = cr.Provider(
            self, f"ApiEndpointUpdaterProvider{func_name.replace('_', '')}",
            on_event_handler=updater_lambda
        )

        CustomResource(
            self, f"ApiEndpointUpdaterResource{func_name.replace('_', '')}",
            service_token=updater_provider.service_token,
            properties={
                "FunctionName": function.function_name,
                "ApiEndpoint": api_endpoint
            }
        )

    def _create_waf_protection(self, resource_prefix: str, api: apigw.RestApi):
        """Create WAF for REST API protection with proper resource ARN and dependencies"""
        
        # Get the current environment for stage name
        environment = self.node.try_get_context("environment") or "dev"
        
        # Create Web ACL for REST API
        web_acl = wafv2.CfnWebACL(
            self, f"{resource_prefix}-waf",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="CommonRuleSetMetric"
                    )
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="WAFOptimizedRateLimit",
                    priority=2,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=5000,
                            aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        sampled_requests_enabled=True,
                        cloud_watch_metrics_enabled=True,
                        metric_name="WAFRateLimitMetric"
                    )
                )
            ],
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                sampled_requests_enabled=True,
                cloud_watch_metrics_enabled=True,
                metric_name=f"{resource_prefix}-waf-metric"
            )
        )

        # Define the Lambda code as a variable first
        lambda_code = textwrap.dedent('''
            import boto3
            import json
            import time
            import logging

            logger = logging.getLogger()
            logger.setLevel(logging.INFO)

            def handler(event, context):
                try:
                    request_type = event['RequestType']
                    properties = event['ResourceProperties']
                    
                    logger.info(f"Request type: {request_type}")
                    logger.info(f"Properties: {json.dumps(properties, default=str)}")
                                      
                    wafv2_client = boto3.client('wafv2')
                    
                    if request_type == 'Create' or request_type == 'Update':
                        time.sleep(15)
                        
                        web_acl_arn = properties['WebAclArn']
                        resource_arn = properties['ResourceArn']
                        
                        logger.info(f"Associating WebACL {web_acl_arn} with resource {resource_arn}")
                        
                        # Check if association already exists
                        try:
                            existing = wafv2_client.get_web_acl_for_resource(ResourceArn=resource_arn)
                            if existing.get('WebACL', {}).get('ARN') == web_acl_arn:
                                logger.info("Association already exists")
                                return {
                                    'Status': 'SUCCESS',
                                    'PhysicalResourceId': f"waf-association-{properties['ApiId']}-{properties['StageName']}",
                                    'Data': {'AssociationStatus': 'ALREADY_EXISTS'}
                                }
                        except wafv2_client.exceptions.WAFNonexistentItemException:
                            # No existing association, proceed
                            pass
                        
                        wafv2_client.associate_web_acl(
                            WebACLArn=web_acl_arn,
                            ResourceArn=resource_arn
                        )
                        
                        logger.info("Association successful")
                        
                        return {
                            'Status': 'SUCCESS',
                            'PhysicalResourceId': f"waf-association-{properties['ApiId']}-{properties['StageName']}",
                            'Data': {'AssociationStatus': 'SUCCESS'}
                        }
                        
                    elif request_type == 'Delete':
                        try:
                            resource_arn = properties['ResourceArn']
                            logger.info(f"Disassociating WebACL from resource {resource_arn}")
                            wafv2_client.disassociate_web_acl(ResourceArn=resource_arn)
                            logger.info("Disassociation successful")
                        except Exception as e:
                            logger.warning(f"Error during disassociation (may be expected): {e}")
                        
                        return {
                            'Status': 'SUCCESS',
                            'PhysicalResourceId': f"waf-association-{properties['ApiId']}-{properties['StageName']}"
                        }
                        
                except Exception as e:
                    logger.error(f"Error: {e}")
                    return {
                        'Status': 'FAILED',
                        'Reason': str(e),
                        'PhysicalResourceId': f"waf-association-{properties.get('ApiId', 'unknown')}-{properties.get('StageName', 'unknown')}"
                    }
        ''').strip()

        # Create a custom resource to handle WAF association after API deployment
        waf_association_handler = lambda_.Function(
            self, f"{resource_prefix}-waf-association-handler",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(lambda_code),
            timeout=Duration.minutes(3)
        )
        
        # Grant permissions to the Lambda function
        waf_association_handler.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "wafv2:AssociateWebACL",
                    "wafv2:DisassociateWebACL",
                    "wafv2:GetWebACLForResource"
                ],
                resources=["*"]
            )
        )
        
        # Create custom resource provider
        waf_provider = cr.Provider(
            self, f"{resource_prefix}-waf-provider",
            on_event_handler=waf_association_handler
        )
        
        # Create custom resource for WAF association
        waf_association = CustomResource(
            self, f"{resource_prefix}-waf-association",
            service_token=waf_provider.service_token,
            properties={
                "WebAclArn": web_acl.attr_arn,
                "ResourceArn": f"arn:aws:apigateway:{self.region}::/restapis/{api.rest_api_id}/stages/{environment}",
                "ApiId": api.rest_api_id,
                "StageName": environment
            }
        )
        
        # Ensure WAF association waits for both WebACL and API to be ready
        waf_association.node.add_dependency(web_acl)
        waf_association.node.add_dependency(api)
        
        print("✅ Created WAF protection for REST API")

    def _should_skip_deployment(self) -> bool:
        """Check if we should skip deployment because everything is being reused"""
        
        resource_decisions = getattr(self, 'resource_decisions', {})
        
        # Check if all major resources are being reused
        vpc_reused = resource_decisions.get('vpc_decision') == 'reuse'
        memorydb_reused = resource_decisions.get('memorydb_decision') == 'reuse'
        kms_reused = resource_decisions.get('kms_decision') == 'reuse'
        iam_reused = resource_decisions.get('iam_decision') == 'reuse'
        lambda_app_reused = resource_decisions.get('lambda_application_decision') == 'reuse'
        lambda_reused = resource_decisions.get('lambda_decision') == 'reuse'
        api_reused = resource_decisions.get('api_gateway_decision') == 'reuse'
        
        # Check DynamoDB tables
        dynamodb_decisions = resource_decisions.get('dynamodb_decisions', {})
        tables_reused = all(decision == 'reuse' for decision in dynamodb_decisions.values())
        
        all_reused = (vpc_reused and memorydb_reused and kms_reused and iam_reused and 
                    lambda_app_reused and lambda_reused and api_reused and tables_reused)
        
        if all_reused:
            print("\n" + "🎯" * 40)
            print("🎯 ALL RESOURCES ARE BEING REUSED - NO DEPLOYMENT NEEDED")
            print("🎯" * 40)
            print("✅ Stack is already up to date!")
            print("✅ No changes required!")
            return True
        
        return False

    def _create_noop_deployment_marker(self, resource_prefix: str):
        """Create a minimal resource that can be safely updated without affecting infrastructure"""
        
        # Create a deployment marker that changes with each deployment
        deployment_id = hashlib.md5(f"{datetime.now(timezone.utc).isoformat()}-{resource_prefix}".encode()).hexdigest()[:8]
        
        deployment_marker = ssm.StringParameter(
            self, f"{resource_prefix}-deployment-marker",
            parameter_name=f"/{resource_prefix}/deployment/last-check",
            string_value=json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "deployment_id": deployment_id,
                "status": "no-changes-needed",
                "stack_name": self.stack_name,
                "region": self.region,
                "account": self.account
            }, indent=2),
            description="Deployment marker - safe to update, indicates last deployment check",
            tier=ssm.ParameterTier.STANDARD
        )
        
        # Add a tag that changes with each deployment
        Tags.of(deployment_marker).add("LastDeploymentCheck", deployment_id)
        Tags.of(deployment_marker).add("DeploymentTimestamp", datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
        
        return deployment_marker

    def _create_noop_provider(self) -> cr.Provider:
        """Create a custom resource provider that does nothing"""
        
        noop_code = textwrap.dedent('''
            import json

            def handler(event, context):
                print(f"No-op deployment check: {event}")
                
                return {
                    'Status': 'SUCCESS',
                    'PhysicalResourceId': f"noop-{event.get('RequestId', 'unknown')}",
                    'Data': {
                        'Message': 'All resources are up to date - no deployment needed',
                        'Timestamp': event.get('ResourceProperties', {}).get('Timestamp', ''),
                        'Action': 'NO_OP'
                    }
                }
        ''').strip()
        
        noop_lambda = lambda_.Function(
            self, "NoOpLambda",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(noop_code),
            timeout=Duration.seconds(30)
        )
        
        return cr.Provider(
            self, "NoOpProvider",
            on_event_handler=noop_lambda
        )

    # Custom Resource Provider for Developer Registration
    def _create_developer_registration_provider(self, resource_prefix: str, suffix: str = "") -> cr.Provider:
        """Create bulletproof custom resource provider for developer registration"""
        
        provider_construct_id = f"{resource_prefix}-registration-provider{suffix}"
        lambda_construct_id = f"{resource_prefix}-registration-lambda{suffix}"
        
        print(f"🔧 Creating bulletproof registration provider: {provider_construct_id}")
        
        # Ultra-robust Lambda code with extensive error handling
        registration_code = textwrap.dedent('''
            import json
            import boto3
            import secrets
            import re
            import traceback
            from datetime import datetime, timezone
            
            def handler(event, context):
                """Bulletproof handler with comprehensive error handling"""
                try:
                    print(f"=== REGISTRATION LAMBDA START ===")
                    print(f"Event: {json.dumps(event, default=str)}")
                    print(f"Context: {context}")
                    
                    request_type = event.get('RequestType', 'Unknown')
                    properties = event.get('ResourceProperties', {})
                    physical_resource_id = event.get('PhysicalResourceId')
                    
                    print(f"Request Type: {request_type}")
                    print(f"Properties: {json.dumps(properties, default=str)}")
                    
                    # Route to appropriate handler
                    if request_type == 'Create':
                        return handle_create(properties)
                    elif request_type == 'Update':
                        return handle_update(properties, physical_resource_id)
                    elif request_type == 'Delete':
                        return handle_delete(properties, physical_resource_id)
                    else:
                        return create_error_response(f"Unknown request type: {request_type}", properties)
                        
                except Exception as e:
                    print(f"CRITICAL ERROR in handler: {str(e)}")
                    print(f"Full traceback: {traceback.format_exc()}")
                    return create_error_response(str(e), event.get('ResourceProperties', {}))
            
            def handle_create(properties):
                """Handle CREATE request with bulletproof error handling"""
                try:
                    print(f"=== HANDLING CREATE ===")
                    
                    # Step 1: Validate all required properties
                    required_props = ['StudioName', 'ContactEmail', 'GameTitle', 'GameGenre', 'Environment', 'SSMPrefix']
                    missing_props = []
                    
                    for prop in required_props:
                        value = properties.get(prop)
                        if not value or not str(value).strip():
                            missing_props.append(prop)
                    
                    if missing_props:
                        raise ValueError(f"Missing or empty required properties: {missing_props}")
                    
                    # Step 2: Extract and validate properties
                    studio_name = str(properties['StudioName']).strip()
                    contact_email = str(properties['ContactEmail']).strip()
                    game_title = str(properties['GameTitle']).strip()
                    game_genre = str(properties['GameGenre']).strip()
                    environment = str(properties['Environment']).strip()
                    ssm_prefix = str(properties['SSMPrefix']).strip()
                    default_rate_limit = int(properties.get('DefaultRateLimit', 1000))
                    
                    print(f"Validated properties:")
                    print(f"  Studio: '{studio_name}' (len: {len(studio_name)})")
                    print(f"  Game: '{game_title}' (len: {len(game_title)})")
                    print(f"  Environment: '{environment}'")
                    print(f"  SSM Prefix: '{ssm_prefix}'")
                    
                    # Step 3: Generate IDs using SAME logic as developerRegistration.py
                    studio_id = sanitize_for_id(studio_name)
                    game_id = sanitize_for_id(game_title)
                    
                    print(f"Generated IDs:")
                    print(f"  StudioId: '{studio_id}' (len: {len(studio_id)})")
                    print(f"  GameId: '{game_id}' (len: {len(game_id)})")
                    
                    # Validate generated IDs
                    if not studio_id or len(studio_id) < 2:
                        raise ValueError(f"Invalid studio ID generated from '{studio_name}': '{studio_id}'")
                    if not game_id or len(game_id) < 2:
                        raise ValueError(f"Invalid game ID generated from '{game_title}': '{game_id}'")
                    
                    # Step 4: Generate API key using SAME logic as developerRegistration.py
                    api_key = generate_secure_api_key(studio_name, game_id, environment)
                    print(f"Generated API key: {api_key[:15]}... (len: {len(api_key)})")
                    
                    # Step 5: Create API key data
                    current_time = datetime.now(timezone.utc).isoformat()
                    api_key_data = {
                        'apiKey': api_key,
                        'studioId': studio_id,
                        'gameId': game_id,
                        'studioName': studio_name,
                        'gameTitle': game_title,
                        'gameGenre': game_genre,
                        'contactEmail': contact_email,
                        'permissions': ['read', 'write'],
                        'rateLimit': default_rate_limit,
                        'status': 'active',
                        'environment': environment,
                        'createdAt': current_time,
                        'lastUpdated': current_time,
                        'registrationDate': current_time,
                        'lastKeyRotation': current_time,
                        'keyRotationSchedule': 90
                    }
                    
                    # Step 6: Construct SSM parameter name with validation
                    ssm_parameter_name = construct_ssm_parameter_name(ssm_prefix, studio_id, game_id)
                    print(f"SSM Parameter Name: '{ssm_parameter_name}' (len: {len(ssm_parameter_name)})")
                    
                    # Step 7: Store in SSM with comprehensive error handling
                    store_result = store_in_ssm(ssm_parameter_name, api_key_data, studio_name, game_title, environment)
                    if not store_result['success']:
                        raise Exception(f"SSM storage failed: {store_result['error']}")
                    
                    print(f"Successfully stored in SSM: {ssm_parameter_name}")
                    
                    # Step 8: Create response
                    physical_resource_id = f"dev-reg-{studio_id}-{game_id}"
                    
                    success_response = {
                        'Status': 'SUCCESS',
                        'PhysicalResourceId': physical_resource_id,
                        'Data': {
                            'StudioId': studio_id,
                            'GameId': game_id,
                            'ApiKey': api_key,
                            'SSMParameterName': ssm_parameter_name,
                            'StudioName': studio_name,
                            'GameTitle': game_title,
                            'ContactEmail': contact_email,
                            'Environment': environment,
                            'RegistrationDate': current_time,
                            'Status': 'active'
                        }
                    }
                    
                    print(f"=== CREATE SUCCESS ===")
                    print(f"Response Data Keys: {list(success_response['Data'].keys())}")
                    print(f"ApiKey: {success_response['Data']['ApiKey'][:15]}...")
                    print(f"StudioId: {success_response['Data']['StudioId']}")
                    print(f"GameId: {success_response['Data']['GameId']}")
                    
                    return success_response
                    
                except Exception as e:
                    print(f"ERROR in handle_create: {str(e)}")
                    print(f"Full traceback: {traceback.format_exc()}")
                    return create_error_response(str(e), properties)
            
            def handle_update(properties, physical_resource_id):
                """Handle UPDATE request"""
                print(f"=== HANDLING UPDATE ===")
                result = handle_create(properties)
                if result['Status'] == 'SUCCESS' and physical_resource_id:
                    result['PhysicalResourceId'] = physical_resource_id
                return result
            
            def handle_delete(properties, physical_resource_id):
                """Handle DELETE request"""
                print(f"=== HANDLING DELETE ===")
                try:
                    # Optional cleanup - don't fail if cleanup fails
                    print(f"Deleting registration: {physical_resource_id}")
                    return {
                        'Status': 'SUCCESS',
                        'PhysicalResourceId': physical_resource_id or 'dev-reg-deleted'
                    }
                except Exception as e:
                    print(f"Warning during delete: {e}")
                    return {
                        'Status': 'SUCCESS',  # Always succeed on delete
                        'PhysicalResourceId': physical_resource_id or 'dev-reg-deleted'
                    }
            
            def sanitize_for_id(input_string):
                """
                Sanitize input string for use as studioId or gameId - EXACT SAME LOGIC as developerRegistration.py
                """
                if not input_string:
                    return ""
                
                # Remove all special characters, keep only alphanumeric, spaces, hyphens, underscores, periods
                # This will strip out @, &, !, (), etc. that we allow in display names
                sanitized = re.sub(r'[^a-zA-Z0-9\\s\\-_.]', '', input_string)

                # Convert to lowercase and replace spaces/underscores/periods with hyphens
                sanitized = sanitized.lower().replace(' ', '-').replace('_', '-').replace('.', '-')

                # Clean up multiple consecutive hyphens
                sanitized = re.sub(r'-+', '-', sanitized)

                # Remove leading/trailing hyphens
                sanitized = sanitized.strip('-')
                
                # Ensure we have something left
                if not sanitized:
                    # If everything was stripped, generate a fallback
                    sanitized = "studio" if "studio" in input_string.lower() else "game"
                
                return sanitized
            
            def generate_secure_api_key(studio_name, game_id, environment):
                """Generate secure API key - EXACT SAME LOGIC as developerRegistration.py"""
                try:
                    # Create secure random component
                    secure_random = secrets.token_urlsafe(16)
                    
                    # Create readable prefixes
                    studio_prefix = re.sub(r'[^a-zA-Z0-9]', '', studio_name)[:8].lower()
                    game_prefix = re.sub(r'[^a-zA-Z0-9]', '', game_id)[:8].lower()
                    
                    # Ensure we have valid prefixes
                    if not studio_prefix:
                        studio_prefix = "studio"
                    if not game_prefix:
                        game_prefix = "game"
                    
                    # Format: prefix_prefix_env_securetoken
                    api_key = f"{studio_prefix}_{game_prefix}_{environment}_{secure_random}"
                    
                    print(f"Generated API key components:")
                    print(f"  Studio prefix: {studio_prefix}")
                    print(f"  Game prefix: {game_prefix}")
                    print(f"  Environment: {environment}")
                    print(f"  Secure token: {secure_random[:10]}...")
                    
                    return api_key
                    
                except Exception as e:
                    print(f"Error generating API key: {e}")
                    # Fallback to simple secure key
                    return f"apikey_{environment}_{secrets.token_urlsafe(24)}"
            
            def construct_ssm_parameter_name(ssm_prefix, studio_id, game_id):
                """Construct and validate SSM parameter name - EXACT SAME LOGIC as developerRegistration.py"""
                # Clean prefix
                clean_prefix = str(ssm_prefix).strip()
                if not clean_prefix.startswith('/'):
                    clean_prefix = '/' + clean_prefix
                if clean_prefix.endswith('/'):
                    clean_prefix = clean_prefix.rstrip('/')
                
                # Construct parameter name
                parameter_name = f"{clean_prefix}/api-keys/{studio_id}-{game_id}"
                
                # Validate length (SSM limit is 2048)
                if len(parameter_name) > 2048:
                    raise ValueError(f"SSM parameter name too long: {len(parameter_name)} chars")
                
                # Validate format
                if not re.match(r'^/[a-zA-Z0-9/_-]+$', parameter_name):
                    raise ValueError(f"Invalid SSM parameter name format: {parameter_name}")
                
                return parameter_name

            def store_in_ssm(parameter_name, api_key_data, studio_name, game_title, environment):
                """Store data in SSM with comprehensive error handling"""
                try:
                    print(f"Storing in SSM: {parameter_name}")
                    
                    # Initialize SSM client
                    ssm = boto3.client('ssm')
                    
                    # Convert to JSON and validate size
                    parameter_value = json.dumps(api_key_data, separators=(',', ':'))
                    value_size = len(parameter_value)
                    
                    print(f"Parameter value size: {value_size} bytes")
                    
                    if value_size > 4096:  # SSM SecureString limit
                        return {
                            'success': False,
                            'error': f"Parameter value too large: {value_size} bytes (limit: 4096)"
                        }

                    # Prepare tags
                    tags = [
                        {'Key': 'Environment', 'Value': environment},
                        {'Key': 'Service', 'Value': 'game-statsleaderboards'},
                        {'Key': 'Type', 'Value': 'api-key'},
                        {'Key': 'CreatedBy', 'Value': 'cdk-deployment'},
                        {'Key': 'StudioId', 'Value': api_key_data['studioId']},
                        {'Key': 'GameId', 'Value': api_key_data['gameId']}
                    ]
                    
                    # Check if parameter exists first, then handle accordingly
                    parameter_exists = False
                    try:
                        ssm.get_parameter(Name=parameter_name)
                        parameter_exists = True
                        print(f"Parameter exists, will update without tags")
                    except ssm.exceptions.ParameterNotFound:
                        parameter_exists = False
                        print(f"Parameter does not exist, will create with tags")
                    
                    if parameter_exists:
                        # Update existing parameter (without tags due to AWS limitation)
                        response = ssm.put_parameter(
                            Name=parameter_name,
                            Value=parameter_value,
                            Type='SecureString',
                            Description=f"API key for {studio_name} - {game_title} ({environment})",
                            Overwrite=True,
                            Tier='Standard'
                            # No Tags when Overwrite=True
                        )
                        
                        # Add tags separately for existing parameter
                        try:
                            ssm.add_tags_to_resource(
                                ResourceType='Parameter',
                                ResourceId=parameter_name,
                                Tags=tags
                            )
                            print(f"Added tags to existing parameter")
                        except Exception as tag_error:
                            print(f"Warning: Could not add tags to existing parameter: {tag_error}")
                            # Don't fail the whole operation for tagging issues
                    else:
                        # Create new parameter with tags
                        response = ssm.put_parameter(
                            Name=parameter_name,
                            Value=parameter_value,
                            Type='SecureString',
                            Description=f"API key for {studio_name} - {game_title} ({environment})",
                            Overwrite=False,            # Must be False when creating with tags
                            Tier='Standard',
                            Tags=tags
                        )
                    
                    print(f"SSM put_parameter response: {response}")
                    
                    return {'success': True, 'response': response}
                    
                except Exception as e:
                    error_msg = f"SSM storage error: {str(e)}"
                    print(f"ERROR: {error_msg}")
                    print(f"Full traceback: {traceback.format_exc()}")
                    return {'success': False, 'error': error_msg}
            
            def create_error_response(error_message, properties):
                """Create consistent error response"""
                try:
                    # Try to extract basic info for physical ID
                    studio_name = properties.get('StudioName', 'unknown')
                    game_title = properties.get('GameTitle', 'unknown')
                    
                    studio_id = sanitize_for_id(studio_name)
                    game_id = sanitize_for_id(game_title)
                    
                    physical_id = f"dev-reg-{studio_id}-{game_id}"
                    
                except Exception:
                    physical_id = f"dev-reg-error-{int(datetime.now().timestamp())}"
                
                error_response = {
                    'Status': 'FAILED',
                    'Reason': str(error_message)[:1000],  # CloudFormation limit
                    'PhysicalResourceId': physical_id,
                    'Data': {
                        'StudioId': sanitize_for_id(properties.get('StudioName', 'error')),
                        'GameId': sanitize_for_id(properties.get('GameTitle', 'error')),
                        'ApiKey': f'ERROR_DURING_CREATION_{int(datetime.now().timestamp())}',
                        'SSMParameterName': 'error',
                        'StudioName': properties.get('StudioName', 'Error'),
                        'GameTitle': properties.get('GameTitle', 'Error'),
                        'ContactEmail': properties.get('ContactEmail', 'error@example.com'),
                        'Environment': properties.get('Environment', 'dev'),
                        'ErrorMessage': str(error_message)[:500]
                    }
                }
                
                print(f"=== ERROR RESPONSE ===")
                print(f"Error: {error_message}")
                print(f"Physical ID: {physical_id}")
                print(f"Response: {json.dumps(error_response, default=str)}")
                
                return error_response
        ''').strip()
        
        # Create log group for the registration Lambda
        registration_log_group = logs.LogGroup(
            self, f"{lambda_construct_id}-logs",
            log_group_name=f"/aws/lambda/{lambda_construct_id}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create Lambda with extended timeout and memory
        registration_lambda = lambda_.Function(
            self, lambda_construct_id,
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(registration_code),
            timeout=Duration.minutes(10),  # Extended timeout
            memory_size=512,  # More memory for processing
            description=f"Bulletproof developer registration provider for {resource_prefix}",
            retry_attempts=0,
            log_group=registration_log_group,
            environment={
                'PYTHONPATH': '/var/runtime:/var/task:/opt/python',
                'AWS_LAMBDA_LOG_LEVEL': 'INFO'
            }
        )
        
        print(f"✅ Created bulletproof registration Lambda: {lambda_construct_id}")
        
        # Grant comprehensive SSM permissions
        # Note: DescribeParameters is a list operation and requires wildcard resource
        registration_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ssm:PutParameter",
                    "ssm:GetParameter",
                    "ssm:DeleteParameter",
                    "ssm:AddTagsToResource",
                    "ssm:ListTagsForResource"
                ],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/{resource_prefix}/*",
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/{resource_prefix.replace('-', '_')}/*"  # Handle underscore variants
                ]
            )
        )
        
        # DescribeParameters requires wildcard resource (it's a list operation)
        registration_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ssm:DescribeParameters"
                ],
                resources=["*"]
            )
        )
        
        # Grant Lambda permissions to update authorizer environment variables
        # This enables high-performance authorization (10,000 TPS) by keeping parameter names in sync
        registration_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:GetFunctionConfiguration",
                    "lambda:UpdateFunctionConfiguration"
                ],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:{resource_prefix}-backend-authorizer"
                ]
            )
        )
        
        # Grant CloudWatch Logs permissions
        registration_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{lambda_construct_id}*"]
            )
        )
        
        print(f"✅ Added comprehensive permissions to registration Lambda")
        
        try:
            # Create provider with ONLY the required parameters
            provider = cr.Provider(
                self, provider_construct_id,
                on_event_handler=registration_lambda,
                # All the problematic parameters that require isCompleteHandler
                # queryInterval, totalTimeout, waiterStateMachineLogOptions, disableWaiterStateMachineLogging
                # These are only valid when isCompleteHandler is provided
            )
            
            print(f"✅ Created bulletproof registration provider: {provider_construct_id}")
            return provider
            
        except Exception as e:
            print(f"❌ Error creating registration provider: {e}")
            raise e

    def _should_create_developer_registration(self, resource_prefix: str, environment: str) -> bool:
        """Comprehensive check to determine if developer registration should be created"""
        
        print(f"🔍 DEBUG: _should_create_developer_registration called")
        
        # Check 0: Context override to disable registration
        disable_registration = self.node.try_get_context("disable_developer_registration")
        if disable_registration and str(disable_registration).lower() in ('true', '1', 'yes', 'on'):
            print(f"🔍 DEBUG: Developer registration disabled by context")
            return False
        
        # Check 1: Instance flag
        if hasattr(self, '_developer_registration_created') and self._developer_registration_created:
            print(f"🔍 DEBUG: Registration already created in this stack instance (flag = True)")
            return False
        
        # Check 2: Check if SSM parameter already exists (optional - for awareness)
        try:
            import boto3
            ssm_client = boto3.client('ssm', region_name=self.region)
            
            # We don't know the exact studio/game IDs yet, so we'll check for any parameters
            response = ssm_client.get_parameters_by_path(
                Path=f"/{resource_prefix}/api-keys",
                Recursive=True,
                MaxResults=1  # Just check if any exist
            )
            
            if response.get('Parameters'):
                print(f"🔍 DEBUG: Found existing SSM parameters - registration may already exist")
                print(f"🔍 DEBUG: Existing parameters: {len(response['Parameters'])} found")
                # Don't return False here - we still want to create if the flag says we should
            else:
                print(f"🔍 DEBUG: No existing SSM parameters found")
                
        except Exception as e:
            print(f"🔍 DEBUG: Could not check SSM parameters: {e}")
            # Continue anyway - this is just for awareness
        
        # Check 3: Verify we have required parameters
        required_params = ['studio_name', 'contact_email', 'game_title', 'game_genre']
        for param in required_params:
            if not hasattr(self, param):
                print(f"❌ DEBUG: Missing required parameter: {param}")
                return False
        
        print(f"✅ DEBUG: All checks passed - should create developer registration")
        return True

    # Create developer registration during deployment
    def _create_developer_registration_during_deployment(self, resource_prefix: str, 
                                                        environment: str) -> CustomResource:
        """Create developer registration automatically during deployment using SSM Parameter Store ONLY"""
        
        print(f"🔍 DEBUG: _create_developer_registration_during_deployment called")
        print(f"🔍 DEBUG: resource_prefix = {resource_prefix}")
        print(f"🔍 DEBUG: environment = {environment}")
        
        registration_provider = None
        
        # Try to get existing provider first
        try:
            # Check if we already created this provider
            if hasattr(self, '_registration_provider') and self._registration_provider is not None:
                registration_provider = self._registration_provider
                print(f"🔄 Reusing existing registration provider")
            else:
                # Create the custom resource provider only once
                registration_provider = self._create_developer_registration_provider(resource_prefix)
                if registration_provider is not None:
                    # Cache it to prevent duplicate creation
                    self._registration_provider = registration_provider
                    print(f"🆕 Created new registration provider")
                else:
                    raise Exception("Provider creation returned None")
                    
        except Exception as e:
            print(f"❌ CRITICAL: Failed to create registration provider: {e}")
            # Don't create mock objects - let it fail properly
            raise Exception(f"Could not create developer registration provider: {e}")
        
        # Validate that we have a valid provider
        if registration_provider is None:
            raise Exception("Registration provider is None - cannot create custom resource")
        
        if not hasattr(registration_provider, 'service_token'):
            raise Exception("Registration provider does not have service_token attribute")
        
        # Create unique construct ID to prevent conflicts
        import time
        unique_timestamp = str(int(time.time()))[-6:]
        construct_id = f"{resource_prefix}-developer-registration-{unique_timestamp}"
        
        print(f"🔍 DEBUG: Creating CustomResource with construct_id = {construct_id}")
        
        # Create the custom resource with proper error handling
        registration_resource = CustomResource(
            self, construct_id,
            service_token=registration_provider.service_token,
            properties={
                'StudioName': self.studio_name.value_as_string,
                'ContactEmail': self.contact_email.value_as_string,
                'GameTitle': self.game_title.value_as_string,
                'GameGenre': self.game_genre.value_as_string,
                'Environment': environment,
                'SSMPrefix': f"/{resource_prefix}",
                'DefaultRateLimit': str(2000 if environment == 'prod' else (1500 if environment == 'staging' else 1000))
            },
            resource_type="Custom::DeveloperRegistration"
        )
        
        # Add explicit dependency on the provider to ensure proper creation order
        registration_resource.node.add_dependency(registration_provider)
        
        print(f"✅ DEBUG: CustomResource created successfully with ID: {construct_id}")
        
        return registration_resource

    def _validate_discovered_resources_consistency(self, discovery_results: Dict[str, Any]) -> bool:
        """Validate that all discovered resources are consistent and safe to reuse together"""
        
        try:
            vpc_analysis = discovery_results.get('vpc_analysis', {})
            memorydb_analysis = discovery_results.get('memorydb_analysis', {})
            
            if not vpc_analysis.get('suitable_vpcs') or not memorydb_analysis.get('cluster_exists'):
                return True  # No consistency check needed if resources don't exist
            
            # Get the selected VPC
            selected_vpc = max(vpc_analysis['suitable_vpcs'], key=lambda x: x['suitability']['score'])
            vpc_id = selected_vpc['vpc_id']
            
            # Check if MemoryDB cluster is in the correct VPC
            cluster_details = memorydb_analysis.get('cluster_details', {})
            cluster_subnet_group = cluster_details.get('subnet_group_name', '')
            
            if cluster_subnet_group:
                # Verify subnet group belongs to the selected VPC
                try:
                    memorydb_client = boto3.client('memorydb', region_name=self.region)
                    response = memorydb_client.describe_subnet_groups(SubnetGroupName=cluster_subnet_group)
                    
                    if response['SubnetGroups']:
                        subnet_group = response['SubnetGroups'][0]
                        subnet_ids = subnet_group.get('Subnets', [])
                        
                        # Check if subnets belong to the selected VPC
                        ec2_client = boto3.client('ec2', region_name=self.region)
                        for subnet_info in subnet_ids:
                            subnet_id = subnet_info.get('SubnetIdentifier', '')
                            if subnet_id:
                                subnet_response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
                                if subnet_response['Subnets']:
                                    subnet_vpc_id = subnet_response['Subnets'][0]['VpcId']
                                    if subnet_vpc_id != vpc_id:
                                        print(f"⚠️ Resource consistency issue: MemoryDB cluster is in VPC {subnet_vpc_id}, but selected VPC is {vpc_id}")
                                        return False
                    
                except Exception as e:
                    print(f"⚠️ Could not validate VPC-MemoryDB consistency: {e}")
                    return True  # Allow deployment to proceed with warning
            
            return True
            
        except Exception as e:
            print(f"⚠️ Resource consistency validation failed: {e}")
            return True  # Allow deployment to proceed

    def _print_replacement_warnings(self):
        """Print warnings about resource replacement"""
        
        print("\n" + "⚠️ "*20)
        print("🚨 FORCE REPLACEMENT MODE ENABLED")
        print("⚠️ "*20)
        print("This will:")
        print("• DELETE existing MemoryDB clusters (DATA LOSS)")
        print("• DELETE existing DynamoDB tables (DATA LOSS)")  
        print("• CREATE new resources with different names")
        print("• May cause DOWNTIME during replacement")
        print("⚠️ "*20)
        print("Proceeding in 10 seconds... OR abort this now by hitting the CTRL+C, if you don't want to proceed...")
        time.sleep(10)

    def _get_resource_decisions(self, service_name: str, environment: str, resource_prefix: str,
                            force_create_new: bool, skip_resource_discovery: bool,
                            enable_resource_reuse: bool) -> Dict[str, Any]:
        """Get resource decisions based on discovery and context (no user interaction)"""

        # If not using resource reuse or forcing new creation, create everything new
        if not enable_resource_reuse or force_create_new or skip_resource_discovery:
            reason = []
            if not enable_resource_reuse:
                reason.append("resource reuse disabled")
            if force_create_new:
                reason.append("forced to create new")
            if skip_resource_discovery:
                reason.append("resource discovery skipped")

            print(f"🆕 Creating all new resources ({', '.join(reason)})")

            replacement_strategy = 'force_replacement' if force_create_new else 'create_new'

            return {
                'vpc_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'memorydb_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'dynamodb_decisions': {
                    'config': 'create_new_with_replacement' if force_create_new else 'create_new',
                    'stats': 'create_new_with_replacement' if force_create_new else 'create_new'
                },
                'kms_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'iam_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'lambda_application_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'lambda_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'api_gateway_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'secrets_manager_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'ssm_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'waf_decision': 'create_new_with_replacement' if force_create_new else 'create_new',
                'replacement_strategy': replacement_strategy,
                'discovery_results': None,
                'force_create_new': force_create_new,
                'skip_resource_discovery': skip_resource_discovery,
                'enable_resource_reuse': enable_resource_reuse
            }

        # Check for existing resources in CloudFormation
        cfn_vpc_resources = self._check_existing_vpc_resources_in_cfn(resource_prefix)
        cfn_memorydb_resources = self._check_existing_memorydb_resources_in_cfn(resource_prefix)
        cfn_lambda_resources = self._check_existing_lambda_resources_in_cfn(resource_prefix)
        cfn_api_gateway = self._check_existing_api_gateway_in_cfn(resource_prefix)

        # Discover existing resources
        lambda_functions = self._discover_existing_lambda_functions(resource_prefix)
        api_gateway = self._discover_existing_api_gateway(resource_prefix)

        # Perform discovery and get automated decisions
        print("🔍 Performing resource discovery with automated decision making")
        context = {
            "environment": environment,
            "environments": self.node.try_get_context("environments") or {}
        }
        discovery = EnhancedResourceDiscovery(self.region, self.account, context)
        discovery_results = discovery.perform_comprehensive_discovery(service_name, environment, resource_prefix, force_create_new)

        # Get automated decisions (no user interaction)
        user_decisions = discovery.get_resource_decisions(discovery_results, force_create_new)

        # Use discovery results consistently
        user_decisions['discovery_results'] = discovery_results
        user_decisions['cfn_vpc_resources'] = cfn_vpc_resources
        user_decisions['cfn_memorydb_resources'] = cfn_memorydb_resources
        user_decisions['cfn_lambda_resources'] = cfn_lambda_resources
        user_decisions['cfn_api_gateway'] = cfn_api_gateway
        user_decisions['lambda_functions'] = lambda_functions
        user_decisions['api_gateway'] = api_gateway
        user_decisions['force_create_new'] = force_create_new
        user_decisions['skip_resource_discovery'] = skip_resource_discovery
        user_decisions['enable_resource_reuse'] = enable_resource_reuse

        # Consistent decision logic - use discovery results
        kms_analysis = discovery_results.get('kms_analysis', {})
        iam_analysis = discovery_results.get('iam_analysis', {})
        lambda_app_analysis = discovery_results.get('lambda_application_analysis', {})
        secrets_analysis = discovery_results.get('secrets_manager_analysis', {})
        api_gateway_analysis = discovery_results.get('api_gateway_analysis', {})
        existing_ssm_params = self._check_existing_ssm_parameters_in_cfn(resource_prefix)
        existing_waf = self._check_existing_waf_in_cfn(resource_prefix)


        # Override decisions based on actual discovery results
        if kms_analysis.get('keys_found') and kms_analysis.get('alias_found'):
            user_decisions['kms_decision'] = 'reuse'
            print("✅ Found existing KMS key - will reuse")
        else:
            user_decisions['kms_decision'] = 'create_new'
            print("🆕 No existing KMS key found - will create new")

        if iam_analysis.get('roles_found'):
            user_decisions['iam_decision'] = 'reuse'
            print("✅ Found existing IAM role - will reuse")
        else:
            user_decisions['iam_decision'] = 'create_new'
            print("🆕 No existing IAM role found - will create new")

        # Secrets Manager decision
        if secrets_analysis.get('secrets_found'):
            user_decisions['secrets_manager_decision'] = 'reuse'
            print("✅ Found existing Secrets Manager secrets - will reuse")
        else:
            user_decisions['secrets_manager_decision'] = 'create_new'
            print("🆕 No existing Secrets Manager secrets found - will create new")

        # Lambda App decision
        if lambda_app_analysis.get('application_found') and lambda_app_analysis.get('resource_group_found'):
            user_decisions['lambda_application_decision'] = 'reuse'
            print("✅ Found existing Lambda Application and Resource Group - will reuse")
        else:
            user_decisions['lambda_application_decision'] = 'create_new'
            print("🆕 No existing Lambda Application found - will create new")

        # Add Lambda and API Gateway decisions
        if lambda_functions['functions_found'] and not force_create_new:
            user_decisions['lambda_decision'] = 'reuse'
            print("✅ Found existing Lambda functions - will attempt to reuse")
        else:
            user_decisions['lambda_decision'] = 'create_new'
            print("🆕 No existing Lambda functions found or force create new enabled - will create new")

        # API Gateway decision with enhanced logic
        if ((api_gateway['api_found'] or cfn_api_gateway['api_found'] or api_gateway_analysis.get('api_exists')) 
            and not force_create_new):
            user_decisions['api_gateway_decision'] = 'reuse'
            print("✅ Found existing API Gateway - will attempt to reuse")
        else:
            user_decisions['api_gateway_decision'] = 'create_new'
            print("🆕 No existing API Gateway found or force create new enabled - will create new")

        # Add SSM and WAF decisions
        user_decisions['ssm_decision'] = 'reuse' if not force_create_new and enable_resource_reuse else 'create_new'
        user_decisions['waf_decision'] = 'reuse' if not force_create_new and enable_resource_reuse else 'create_new'

        # SSM decision - check for existing SSM parameters in CloudFormation
        if existing_ssm_params and not force_create_new and enable_resource_reuse:
            user_decisions['ssm_decision'] = 'reuse'
            print("✅ Found existing SSM parameters - will reuse where possible")
        else:
            user_decisions['ssm_decision'] = 'create_new'
            print("🆕 No existing SSM parameters found or reuse disabled - will create new")

        # WAF decision
        if existing_waf and not force_create_new and enable_resource_reuse:
            user_decisions['waf_decision'] = 'reuse'
            print("✅ Found existing WAF - will reuse where possible")
        else:
            user_decisions['waf_decision'] = 'create_new'
            print("🆕 No existing WAF found or reuse disabled - will create new")

        # Override MemoryDB decision if we found existing resources in CloudFormation
        if cfn_memorydb_resources['cluster_found'] and user_decisions['memorydb_decision'] != 'reuse':
            print("⚠️ Found existing MemoryDB resources in CloudFormation but discovery suggests creating new")
            print("   Overriding decision to 'reuse' to prevent resource conflicts")
            user_decisions['memorydb_decision'] = 'reuse'

        # Override VPC decision if we found existing resources in CloudFormation
        if cfn_vpc_resources['vpc_found'] and user_decisions['vpc_decision'] != 'reuse_existing':
            print("⚠️ Found existing VPC resources in CloudFormation but discovery suggests creating new")
            print("   Overriding decision to 'reuse_existing' to prevent resource conflicts")
            user_decisions['vpc_decision'] = 'reuse_existing'

        # Override API Gateway decision if we found existing resources in CloudFormation
        if cfn_api_gateway['api_found'] and user_decisions['api_gateway_decision'] != 'reuse':
            print("⚠️ Found existing API Gateway resources in CloudFormation but discovery suggests creating new")
            print("   Overriding decision to 'reuse' to prevent resource conflicts")
            user_decisions['api_gateway_decision'] = 'reuse'

        return user_decisions

    def _check_existing_ssm_parameters_in_cfn(self, resource_prefix: str) -> bool:
        """Check for existing SSM parameters in CloudFormation"""

        try:
            if not self.cf_client:
                return False

            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)

            if stack_exists:
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    
                    if (resource_type == 'AWS::SSM::Parameter' and 
                        resource_prefix in physical_id and 
                        'config' in physical_id):
                        print(f"   ✅ Found existing SSM parameter in CloudFormation: {physical_id}")
                        return True
            
            return False

        except Exception as e:
            print(f"   ⚠️ Error checking existing SSM parameters in CloudFormation: {e}")
            return False

    def _check_existing_waf_in_cfn(self, resource_prefix: str) -> bool:
        """Check for existing WAF resources in CloudFormation"""

        try:
            if not self.cf_client:
                return False

            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)

            if stack_exists:
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    logical_id = resource.get('LogicalId', '')
                    
                    if (resource_type == 'AWS::WAFv2::WebACL' and 
                        resource_prefix in logical_id.lower()):
                        print(f"   ✅ Found existing WAF in CloudFormation: {logical_id}")
                        return True

            return False

        except Exception as e:
            print(f"   ⚠️ Error checking existing WAF in CloudFormation: {e}")
            return False

    def _handle_vpc_creation(self, resource_prefix: str, service_name: str, environment: str, 
                           resource_decisions: Dict[str, Any]) -> tuple[ec2.Vpc, ec2.SecurityGroup, bool]:
        """Handle VPC creation with replacement support"""
        
        replacement_strategy = resource_decisions.get('replacement_strategy', 'smart_reuse')
        
        if (resource_decisions['vpc_decision'] == 'reuse' and 
            'discovery_results' in resource_decisions and
            resource_decisions['discovery_results'] and
            resource_decisions['discovery_results']['vpc_analysis']['suitable_vpcs']):
            
            # Reuse existing VPC
            suitable_vpcs = resource_decisions['discovery_results']['vpc_analysis']['suitable_vpcs']
            selected_vpc = max(suitable_vpcs, key=lambda x: x['suitability']['score'])
            
            print(f"🔄 Reusing existing VPC: {selected_vpc['vpc_id']}")
            
            vpc_details = selected_vpc['details']
            vpc_id = selected_vpc['vpc_id']

            # Get route table IDs for subnets
            public_subnet_route_table_ids = []
            private_subnet_route_table_ids = []

            try:
                ec2_client = boto3.client('ec2', region_name=self.region)
                
                # Get route tables for public subnets
                for subnet in vpc_details['subnets']['public']:
                    subnet_id = subnet['subnet_id']
                    route_tables = ec2_client.describe_route_tables(
                        Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
                    )
                    if route_tables['RouteTables']:
                        public_subnet_route_table_ids.append(route_tables['RouteTables'][0]['RouteTableId'])
                    else:
                        public_subnet_route_table_ids.append(None)
                
                # Get route tables for private subnets
                for subnet in vpc_details['subnets']['private']:
                    subnet_id = subnet['subnet_id']
                    route_tables = ec2_client.describe_route_tables(
                        Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
                    )
                    if route_tables['RouteTables']:
                        private_subnet_route_table_ids.append(route_tables['RouteTables'][0]['RouteTableId'])
                    else:
                        private_subnet_route_table_ids.append(None)
                        
            except Exception as e:
                print(f"⚠️ Could not get route table IDs: {e}")

            vpc = ec2.Vpc.from_vpc_attributes(
                self, f"{resource_prefix}-existing-vpc",
                vpc_id=vpc_id,
                availability_zones=vpc_details['availability_zones'],
                public_subnet_ids=[s['subnet_id'] for s in vpc_details['subnets']['public']],
                private_subnet_ids=[s['subnet_id'] for s in vpc_details['subnets']['private']],
                public_subnet_route_table_ids=public_subnet_route_table_ids,
                private_subnet_route_table_ids=private_subnet_route_table_ids
            )

            # Try to find existing security group first
            existing_sg = self._discover_existing_security_group(resource_prefix, vpc_id)
            
            if existing_sg and replacement_strategy != 'force_replacement':
                print(f"🔄 Reusing existing MemoryDB security group")
                memorydb_sg = existing_sg
            else:
                print(f"🆕 Creating new MemoryDB security group in existing VPC")
                memorydb_sg = self._create_memorydb_security_group(resource_prefix, vpc, selected_vpc['vpc_cidr'])
            
            return vpc, memorydb_sg, True
        
        else:
            # Create new VPC (with replacement if needed)
            if replacement_strategy == 'force_replacement':
                # Add timestamp to ensure new resource creation
                timestamp = datetime.now().strftime("%Y%m%d%H%M")
                resource_suffix = f"-{timestamp}"
                print(f"🔄 Creating new VPC with replacement strategy (suffix: {resource_suffix})")
            else:
                resource_suffix = ""
                print("🆕 Creating new VPC infrastructure")
            
            vpc, memorydb_sg = self._create_new_vpc_with_suffix(resource_prefix, resource_suffix)
            return vpc, memorydb_sg, False

    def _handle_memorydb_creation(self, resource_prefix: str, vpc: ec2.Vpc, security_group: ec2.SecurityGroup, 
                                config: Dict[str, Any], resource_decisions: Dict[str, Any]) -> tuple:
        """Handle MemoryDB creation or reuse based on decisions"""
        
        if (resource_decisions['memorydb_decision'] == 'reuse' and 
            resource_decisions.get('discovery_results') and
            resource_decisions['discovery_results']['memorydb_analysis']['cluster_exists']):
            
            cluster_details = resource_decisions['discovery_results']['memorydb_analysis']['cluster_details']
            cluster_name = cluster_details['cluster_name']
            
            print(f"🔄 Reusing existing MemoryDB cluster: {cluster_name}")
            
            # Create a reference to existing cluster with enhanced provider
            existing_cluster_resource = CustomResource(
                self, f"{resource_prefix}-existing-memorydb-ref",
                service_token=self._create_memorydb_import_provider().service_token,
                properties={
                    "ClusterName": cluster_name,
                    "Action": "IMPORT",
                    "ResourcePrefix": resource_prefix,
                    "Environment": self.node.try_get_context("environment") or "dev"
                }
            )
            
            # Create mock cluster object for compatibility
            class ExistingMemoryDBCluster:
                def __init__(self, cluster_name: str, endpoint: str):
                    self.cluster_name = cluster_name
                    self.attr_cluster_endpoint_address = endpoint
            
            memorydb_cluster = ExistingMemoryDBCluster(cluster_name, cluster_details['endpoint'])
            
            # Try to find existing secret or create new one
            secret_name = f"{resource_prefix}-memorydb-password"
            try:
                memorydb_password = secretsmanager.Secret.from_secret_name_v2(
                    self, f"{resource_prefix}-existing-memorydb-password",
                    secret_name
                )
                print(f"✅ Found existing MemoryDB password secret: {secret_name}")
            except Exception as e:
                print(f"⚠️ Could not find existing password secret: {e}")
                print("🆕 Creating new password secret for existing MemoryDB cluster")
                memorydb_password = self._create_memorydb_password(resource_prefix)
            
            print(f"✅ Successfully imported existing MemoryDB cluster and related resources")
            return memorydb_cluster, memorydb_password, True
            
        else:
            # If we're not reusing, check if this is due to force_create_new
            if resource_decisions.get('force_create_new'):
                print("⚠️ Force create new mode enabled - creating new MemoryDB cluster")
            elif resource_decisions.get('skip_resource_discovery'):
                print("⚠️ Resource discovery skipped - creating new MemoryDB cluster")
            elif not resource_decisions.get('discovery_results', {}).get('memorydb_analysis', {}).get('cluster_exists'):
                print("🆕 No existing MemoryDB cluster found - creating new one")
            else:
                # Existing cluster was found but deemed unsuitable
                suitability = resource_decisions['discovery_results']['memorydb_analysis'].get('suitability', {})
                score = suitability.get('score', 0)
                issues = suitability.get('issues', [])
                
                print(f"⚠️ Existing MemoryDB cluster found but not suitable (Score: {score}/100)")
                for issue in issues:
                    print(f"   - {issue}")
            
            print("🆕 Creating new MemoryDB cluster")
            cluster, password = self._create_new_memorydb_cluster(
                resource_prefix, vpc, security_group, config, resource_decisions
            )
            return cluster, password, False

    def _handle_dynamodb_creation(self, resource_prefix: str, kms_key: kms.Key, config: Dict[str, Any],
                                resource_decisions: Dict[str, Any], environment: str = "dev") -> tuple:
        """Handle DynamoDB creation with replacement support """
        
        # Updated table names - removed developer table
        table_names = {
            'config': f"{resource_prefix}-config",
            'stats': f"{resource_prefix}-stats"
        }
        
        replacement_strategy = resource_decisions.get('replacement_strategy', 'smart_reuse')
        
        tables = {}
        tables_reused = {}
        
        for table_type, table_name in table_names.items():
            decision = resource_decisions['dynamodb_decisions'].get(table_type, 'create_new')
            
            if decision == 'reuse':
                print(f"🔄 Reusing existing DynamoDB table: {table_name}")
                tables[table_type] = dynamodb.TableV2.from_table_name(
                    self, f"{resource_prefix}-existing-{table_type}-table",
                    table_name
                )
                tables_reused[table_type] = True
                
            elif decision == 'create_new_with_replacement':
                # Force replacement by changing logical ID
                timestamp = datetime.now().strftime("%Y%m%d%H%M")
                replacement_suffix = f"-{timestamp}"
                new_table_name = f"{table_name}{replacement_suffix}"
                
                print(f"🔄 Creating replacement DynamoDB table: {new_table_name}")
                tables[table_type] = self._create_new_dynamodb_table_with_suffix(
                    resource_prefix, table_type, new_table_name, kms_key, replacement_suffix, environment
                )
                tables_reused[table_type] = False
                
            else:
                print(f"🆕 Creating new DynamoDB table: {table_name}")
                tables[table_type] = self._create_new_dynamodb_table(
                    resource_prefix, table_type, table_name, kms_key, environment
                )
                tables_reused[table_type] = False
        
        # Return only config and stats tables (no developer table)
        return tables['config'], tables['stats'], tables_reused

    def _create_kms_key(self, resource_prefix: str) -> kms.Key:
        """Create KMS key with proper alias that shows up correctly in console"""
        
        alias_name = f"alias/{resource_prefix}-key"
        
        # Check if we should reuse existing KMS key
        resource_decisions = getattr(self, 'resource_decisions', {})
        kms_decision = resource_decisions.get('kms_decision', 'create_new')
        
        if kms_decision == 'reuse':
            discovery_results = resource_decisions.get('discovery_results', {})
            kms_analysis = discovery_results.get('kms_analysis', {})
            
            if kms_analysis.get('keys_found') and kms_analysis.get('alias_found'):
                key_details = kms_analysis.get('key_details', {})
                alias_details = key_details.get(alias_name, {})  # Use full alias name
                key_id = alias_details.get('key_id')
                
                if key_id:
                    print(f"🔄 Reusing existing KMS key: {alias_name} (Key ID: {key_id})")
                    
                    # Import existing KMS key
                    return kms.Key.from_key_arn(
                        self, f"{resource_prefix}-existing-kms-key",
                        key_arn=f"arn:aws:kms:{self.region}:{self.account}:key/{key_id}"
                    )
        
        print(f"🆕 Creating new KMS key with alias: {alias_name}")

        # Create the key with proper configuration
        key = kms.Key(
            self, f"{resource_prefix}-kms-key",
            description=f"KMS key for {resource_prefix} encryption - Game Stats & Leaderboards",
            enable_key_rotation=True,
            multi_region=False,
            removal_policy=RemovalPolicy.RETAIN,
            pending_window=Duration.days(16),
            # Add key policy for better security
            policy=iam.PolicyDocument(
                statements=[
                    # Allow root account full access
                    iam.PolicyStatement(
                        sid="Enable IAM User Permissions",
                        effect=iam.Effect.ALLOW,
                        principals=[iam.AccountRootPrincipal()],
                        actions=["kms:*"],
                        resources=["*"]
                    ),
                    # Allow CloudFormation to manage the key
                    iam.PolicyStatement(
                        sid="Allow CloudFormation Management",
                        effect=iam.Effect.ALLOW,
                        principals=[iam.ServicePrincipal("cloudformation.amazonaws.com")],
                        actions=[
                            "kms:Describe*",
                            "kms:List*",
                            "kms:Get*",
                            "kms:CreateAlias",
                            "kms:DeleteAlias",
                            "kms:UpdateAlias"
                        ],
                        resources=["*"]
                    ),
                    # Allow Lambda and other services to use the key
                    iam.PolicyStatement(
                        sid="Allow AWS Services",
                        effect=iam.Effect.ALLOW,
                        principals=[
                            iam.ServicePrincipal("lambda.amazonaws.com"),
                            iam.ServicePrincipal("dynamodb.amazonaws.com"),
                            iam.ServicePrincipal("secretsmanager.amazonaws.com"),
                            iam.ServicePrincipal("ssm.amazonaws.com")
                        ],
                        actions=[
                            "kms:Decrypt",
                            "kms:DescribeKey",
                            "kms:Encrypt",
                            "kms:GenerateDataKey",
                            "kms:GenerateDataKeyWithoutPlaintext",
                            "kms:ReEncrypt*"
                        ],
                        resources=["*"]
                    )
                ]
            )
        )
        
        # Create alias using CfnAlias for better control and explicit dependency management
        # This is the key fix - using CfnAlias instead of Alias construct
        try:
            alias_resource = kms.CfnAlias(
                self, f"{resource_prefix}-kms-key-alias-cfn",
                alias_name=alias_name,
                target_key_id=key.key_id  # Use key_id instead of key reference for better dependency
            )
            
            # Ensure proper creation order with explicit dependency
            alias_resource.add_dependency(key.node.default_child)
            
            print(f"✅ Created KMS alias resource with explicit dependency")
            
        except Exception as e:
            print(f"⚠️ Error creating KMS alias: {e}")
            # Continue without alias - the key will still work
            print(f"   Continuing deployment - key will be accessible by ARN")
        
        # Add tags to both key and alias for better identification
        Tags.of(key).add("Name", f"{resource_prefix}-encryption-key")
        Tags.of(key).add("Purpose", "GameStatsLeaderboards")
        Tags.of(key).add("Environment", self.node.try_get_context("environment") or "dev")
        Tags.of(key).add("Service", "game-statsleaderboards")
        Tags.of(key).add("ManagedBy", "CDK")
        Tags.of(key).add("AliasName", alias_name)
        
        # Also tag the alias resource if it was created successfully
        try:
            if 'alias_resource' in locals():
                Tags.of(alias_resource).add("Name", f"{resource_prefix}-encryption-key-alias")
                Tags.of(alias_resource).add("Purpose", "GameStatsLeaderboards")
                Tags.of(alias_resource).add("Environment", self.node.try_get_context("environment") or "dev")
                Tags.of(alias_resource).add("Service", "game-statsleaderboards")
                Tags.of(alias_resource).add("ManagedBy", "CDK")
        except Exception as e:
            print(f"⚠️ Warning: Could not tag alias resource: {e}")
        
        print(f"✅ Created KMS key with alias: {alias_name}")
        print(f"   Key will be visible in KMS console with alias: {alias_name}")
        print(f"   Alias creation order: Key -> CfnAlias (with explicit dependency)")
        
        return key

    def _create_new_vpc_with_suffix(self, resource_prefix: str, suffix: str = "") -> tuple[ec2.Vpc, ec2.SecurityGroup]:
        """Create new VPC with optional suffix for replacement"""
        
        vpc = ec2.Vpc(
            self, f"{resource_prefix}-vpc{suffix}",
            max_azs=3,
            nat_gateways=1,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ],
            enable_dns_hostnames=True,
            enable_dns_support=True
        )
        
        # Check for existing VPC endpoints in CloudFormation
        cfn_resources = getattr(self, 'resource_decisions', {}).get('cfn_vpc_resources', {})
        vpc_endpoint_found = cfn_resources.get('vpc_endpoint_found', False)
        
        # Only create endpoints if they don't already exist in CloudFormation
        if not vpc_endpoint_found or suffix:
            print(f"🆕 Creating VPC endpoints for cost optimization")
            
            # VPC Endpoints for cost optimization
            vpc.add_gateway_endpoint(f"DynamoDBEndpoint{suffix}",
                service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
            )
            vpc.add_gateway_endpoint(f"S3Endpoint{suffix}",
                service=ec2.GatewayVpcEndpointAwsService.S3
            )
            
            # Interface endpoints for better GLIDE performance
            vpc.add_interface_endpoint(f"SecretsManagerEndpoint{suffix}",
                service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER
            )
            vpc.add_interface_endpoint(f"SSMEndpoint{suffix}",
                service=ec2.InterfaceVpcEndpointAwsService.SSM
            )
        else:
            print(f"🔄 VPC endpoints already exist in CloudFormation - skipping creation")
        
        # Create security group for MemoryDB
        memorydb_sg = self._create_memorydb_security_group_with_suffix(resource_prefix, vpc, vpc.vpc_cidr_block, suffix)
        
        return vpc, memorydb_sg

    def _create_memorydb_security_group(self, resource_prefix: str, vpc: ec2.Vpc, vpc_cidr: str) -> ec2.SecurityGroup:
        """Create security group for MemoryDB"""
        
        memorydb_sg = ec2.SecurityGroup(
            self, f"{resource_prefix}-memorydb-sg",
            vpc=vpc,
            description="Security group for MemoryDB Valkey cluster optimized for GLIDE connections",
            allow_all_outbound=False
        )
        
        # Allow Valkey access from VPC
        memorydb_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc_cidr),
            ec2.Port.tcp(6379),
            "Allow Valkey GLIDE access from VPC"
        )
        
        memorydb_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc_cidr),
            ec2.Port.tcp_range(6380, 6389),
            "Allow MemoryDB cluster communication"
        )
        
        return memorydb_sg

    def _create_memorydb_security_group_with_suffix(self, resource_prefix: str, vpc: ec2.Vpc, vpc_cidr: str, suffix: str = "") -> ec2.SecurityGroup:
        """Create security group for MemoryDB with optional suffix"""
        
        memorydb_sg = ec2.SecurityGroup(
            self, f"{resource_prefix}-memorydb-sg{suffix}",
            vpc=vpc,
            description="Security group for MemoryDB Valkey cluster optimized for GLIDE connections",
            allow_all_outbound=False
        )
        
        # Allow Valkey access from VPC
        memorydb_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc_cidr),
            ec2.Port.tcp(6379),
            "Allow Valkey GLIDE access from VPC"
        )
        
        memorydb_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc_cidr),
            ec2.Port.tcp_range(6380, 6389),
            "Allow MemoryDB cluster communication"
        )
        
        return memorydb_sg

    def _create_new_memorydb_cluster(
        self, resource_prefix: str, vpc: ec2.Vpc, security_group: ec2.SecurityGroup, 
        config: Dict[str, Any], resource_decisions: Dict[str, Any]
    ) -> tuple[memorydb.CfnCluster, secretsmanager.Secret]:
        """Create new MemoryDB cluster"""
        
        replacement_strategy = resource_decisions.get('replacement_strategy', 'smart_reuse')
        
        # Add suffix for replacement
        if replacement_strategy == 'force_replacement':
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            suffix = f"-{timestamp}"
        else:
            suffix = ""
        
        # Check for existing subnet group first
        existing_subnet_group = None
        subnet_group_name = f"{resource_prefix}-subnet-group"
        
        try:
            memorydb_client = boto3.client('memorydb', region_name=self.region)
            
            try:
                response = memorydb_client.describe_subnet_groups(SubnetGroupName=subnet_group_name)
                if response['SubnetGroups']:
                    existing_subnet_group = response['SubnetGroups'][0]
                    print(f"✅ Found existing subnet group: {subnet_group_name}")
            except memorydb_client.exceptions.SubnetGroupNotFoundFault:
                print(f"🆕 Subnet group not found: {subnet_group_name}")
            except Exception as e:
                print(f"⚠️ Error checking subnet group: {e}")
        except Exception as e:
            print(f"⚠️ Error initializing MemoryDB client: {e}")
        
        # Create or reference subnet group - Proper CDK v2 approach
        if existing_subnet_group and replacement_strategy != 'force_replacement':
            print(f"🔄 Reusing existing subnet group: {subnet_group_name}")
            # For existing subnet groups, we need to create a new CfnSubnetGroup resource
            # that references the existing one by using the same name
            subnet_group = memorydb.CfnSubnetGroup(
                self, f"{resource_prefix}-memorydb-subnet-group-ref{suffix}",
                subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
                subnet_group_name=subnet_group_name,  # Use existing name
                description="Subnet group for MemoryDB Valkey cluster with GLIDE optimization (reusing existing)"
            )
        else:
            # Create new subnet group
            new_subnet_group_name = f"{resource_prefix}-subnet-group{suffix}"
            subnet_group = memorydb.CfnSubnetGroup(
                self, f"{resource_prefix}-memorydb-subnet-group{suffix}",
                subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
                subnet_group_name=new_subnet_group_name,
                description="Subnet group for MemoryDB Valkey cluster with GLIDE optimization"
            )
            print(f"🆕 Created new subnet group: {new_subnet_group_name}")
        
        # Check for existing parameter group
        existing_param_group = None
        param_group_name = f"{resource_prefix}-glide-params"
        
        try:
            try:
                response = memorydb_client.describe_parameter_groups(ParameterGroupName=param_group_name)
                if response['ParameterGroups']:
                    existing_param_group = response['ParameterGroups'][0]
                    print(f"✅ Found existing parameter group: {param_group_name}")
            except memorydb_client.exceptions.ParameterGroupNotFoundFault:
                print(f"🆕 Parameter group not found: {param_group_name}")
            except Exception as e:
                print(f"⚠️ Error checking parameter group: {e}")
        except Exception as e:
            print(f"⚠️ Error checking parameter group: {e}")
        
        # Create or reference parameter group - Proper CDK v2 approach
        if existing_param_group and replacement_strategy != 'force_replacement':
            print(f"🔄 Reusing existing parameter group: {param_group_name}")
            # Create a new CfnParameterGroup that references the existing one
            parameter_group = memorydb.CfnParameterGroup(
                self, f"{resource_prefix}-memorydb-params-ref{suffix}",
                parameter_group_name=param_group_name,  # Use existing name
                family="memorydb_valkey7",
                description="Parameter group optimized for Valkey GLIDE 2.0.1+ client (reusing existing)",
                parameters={
                    "maxmemory-policy": "allkeys-lru",
                    "timeout": "300",
                    "tcp-keepalive": "300",
                    "tcp-backlog": "511",
                    "maxclients": "65000",
                    "client-output-buffer-limit-replica-soft-limit": "256mb",
                    "client-output-buffer-limit-replica-hard-limit": "512mb",
                    "client-output-buffer-limit-replica-soft-seconds": "60",
                    "hz": "10",
                    "dynamic-hz": "yes",
                    "rdbcompression": "yes",
                    "rdbchecksum": "yes",
                    "repl-backlog-size": "16mb",
                    "repl-backlog-ttl": "3600",
                    "replica-read-only": "yes",
                    "loglevel": "notice",
                    "syslog-enabled": "no"
                }
            )
        else:
            # Create new parameter group
            new_param_group_name = f"{resource_prefix}-glide-params{suffix}"
            parameter_group = memorydb.CfnParameterGroup(
                self, f"{resource_prefix}-memorydb-params{suffix}",
                parameter_group_name=new_param_group_name,
                family="memorydb_valkey7",
                description="Parameter group optimized for Valkey GLIDE 2.0.1+ client",
                parameters={
                    "maxmemory-policy": "allkeys-lru",
                    "timeout": "300",
                    "tcp-keepalive": "300",
                    "tcp-backlog": "511",
                    "maxclients": "65000",
                    "client-output-buffer-limit-replica-soft-limit": "256mb",
                    "client-output-buffer-limit-replica-hard-limit": "512mb",
                    "client-output-buffer-limit-replica-soft-seconds": "60",
                    "hz": "10",
                    "dynamic-hz": "yes",
                    "rdbcompression": "yes",
                    "rdbchecksum": "yes",
                    "repl-backlog-size": "16mb",
                    "repl-backlog-ttl": "3600",
                    "replica-read-only": "yes",
                    "loglevel": "notice",
                    "syslog-enabled": "no"
                }
            )
            print(f"🆕 Created new parameter group: {new_param_group_name}")
        
        # Enhanced password with GLIDE-compatible characters
        memorydb_password = self._create_memorydb_password(resource_prefix, suffix)
        
        # Check for existing user
        existing_user = None
        user_name = "glide-user"
        
        try:
            try:
                response = memorydb_client.describe_users(UserName=user_name)
                if response['Users']:
                    existing_user = response['Users'][0]
                    print(f"✅ Found existing user: {user_name}")
            except memorydb_client.exceptions.UserNotFoundFault:
                print(f"🆕 User not found: {user_name}")
            except Exception as e:
                print(f"⚠️ Error checking user: {e}")
        except Exception as e:
            print(f"⚠️ Error checking user: {e}")
        
        # Create or reference user - Proper CDK v2 approach
        if existing_user and replacement_strategy != 'force_replacement':
            print(f"🔄 Updating existing user: {user_name}")
            # Update existing user with new password
            memorydb_user = memorydb.CfnUser(
                self, f"{resource_prefix}-memorydb-user-update{suffix}",
                user_name=user_name,
                authentication_mode={
                    "Type": "password",
                    "Passwords": [memorydb_password.secret_value_from_json("password").unsafe_unwrap()]
                },
                access_string="on ~* &* +@all -@dangerous +client +info +config|get"
            )
        else:
            # Create new user
            memorydb_user = memorydb.CfnUser(
                self, f"{resource_prefix}-memorydb-user{suffix}",
                user_name="glide-user",
                authentication_mode={
                    "Type": "password",
                    "Passwords": [memorydb_password.secret_value_from_json("password").unsafe_unwrap()]
                },
                access_string="on ~* &* +@all -@dangerous +client +info +config|get"
            )
            print(f"🆕 Created new user: {memorydb_user.user_name}")
        
        # Check for existing ACL
        existing_acl = None
        acl_name = f"{resource_prefix}-glide-acl"
        
        try:
            try:
                response = memorydb_client.describe_acls(ACLName=acl_name)
                if response['ACLs']:
                    existing_acl = response['ACLs'][0]
                    print(f"✅ Found existing ACL: {acl_name}")
            except memorydb_client.exceptions.ACLNotFoundFault:
                print(f"🆕 ACL not found: {acl_name}")
            except Exception as e:
                print(f"⚠️ Error checking ACL: {e}")
        except Exception as e:
            print(f"⚠️ Error checking ACL: {e}")
        
        # Create or reference ACL - Proper CDK v2 approach
        if existing_acl and replacement_strategy != 'force_replacement':
            print(f"🔄 Updating existing ACL: {acl_name}")
            memorydb_acl = memorydb.CfnACL(
                self, f"{resource_prefix}-memorydb-acl-update{suffix}",
                acl_name=acl_name,
                user_names=[memorydb_user.user_name]
            )
        else:
            # Create new ACL
            new_acl_name = f"{resource_prefix}-glide-acl{suffix}"
            memorydb_acl = memorydb.CfnACL(
                self, f"{resource_prefix}-memorydb-acl{suffix}",
                acl_name=new_acl_name,
                user_names=[memorydb_user.user_name]
            )
            print(f"🆕 Created new ACL: {new_acl_name}")
        
        # Cluster with GLIDE-optimized settings
        node_type = config.get("memorydb_node_type", "db.r7g.large")
        num_shards = config.get("memorydb_shards", 1)
        num_replicas = config.get("memorydb_replicas", 2)
        
        # Get resource names properly
        subnet_group_name_ref = subnet_group.subnet_group_name
        parameter_group_name_ref = parameter_group.parameter_group_name
        acl_name_ref = memorydb_acl.acl_name
        
        cluster_name = f"{resource_prefix}-cluster{suffix}"
        
        memorydb_cluster = memorydb.CfnCluster(
            self, f"{resource_prefix}-memorydb{suffix}",
            cluster_name=cluster_name,
            node_type=node_type,
            engine="valkey",
            engine_version="7.2",
            num_shards=num_shards,
            num_replicas_per_shard=num_replicas,
            subnet_group_name=subnet_group_name_ref,
            acl_name=acl_name_ref,
            security_group_ids=[security_group.security_group_id],
            parameter_group_name=parameter_group_name_ref,
            tls_enabled=True,
            auto_minor_version_upgrade=True,
            data_tiering="false",
            maintenance_window="sun:05:00-sun:06:00",
            snapshot_retention_limit=7,
            snapshot_window="03:00-04:00",
            final_snapshot_name=f"{resource_prefix}-cluster-snapshot{suffix}",
            description="MemoryDB Valkey cluster for Games Stats and Leaderboards"
        )

        # Set dependencies
        memorydb_cluster.add_dependency(memorydb_acl)
        memorydb_acl.add_dependency(memorydb_user)

        # Create custom resource to handle proper deletion order
        try:
            deletion_handler = self._create_memorydb_deletion_handler(
                resource_prefix, cluster_name, subnet_group_name_ref, 
                parameter_group_name_ref, acl_name_ref, suffix
            )
            
            # Make deletion handler depend on cluster
            deletion_handler.node.add_dependency(memorydb_cluster)
            deletion_handler.node.add_dependency(subnet_group)
            deletion_handler.node.add_dependency(parameter_group)
            deletion_handler.node.add_dependency(memorydb_acl)
            
            print(f"✅ Created MemoryDB deletion handler for proper cleanup order")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not create deletion handler: {e}")
            print(f"   Continuing without deletion handler - manual cleanup may be required")

        return memorydb_cluster, memorydb_password

    def _create_memorydb_deletion_handler(
        self, resource_prefix: str, cluster_name: str, subnet_group_name: str, 
        param_group_name: str, acl_name: str, suffix: str = ""
    ) -> CustomResource:
        """Create custom resource to handle proper MemoryDB deletion order"""
        
        deletion_code = textwrap.dedent(f'''
            import boto3
            import json
            import time
            
            def handler(event, context):
                try:
                    request_type = event['RequestType']
                    properties = event['ResourceProperties']
                    
                    print(f"MemoryDB deletion handler called: {{request_type}}")
                    
                    if request_type == 'Delete':
                        memorydb = boto3.client('memorydb')
                        
                        cluster_name = properties['ClusterName']
                        subnet_group_name = properties['SubnetGroupName']
                        param_group_name = properties['ParameterGroupName']
                        acl_name = properties['ACLName']
                        
                        print(f"Handling deletion for cluster: {{cluster_name}}")
                        
                        # Wait for cluster to be deleted first
                        max_wait_time = 720         # 12 minutes < 15 minutes (Lambda execution max. time)
                        wait_interval = 30
                        elapsed_time = 0
                        
                        while elapsed_time < max_wait_time:
                            try:
                                response = memorydb.describe_clusters(ClusterName=cluster_name)
                                if response['Clusters']:
                                    cluster_status = response['Clusters'][0]['Status']
                                    print(f"Cluster status: {{cluster_status}}")
                                    
                                    if cluster_status in ['deleting']:
                                        print(f"Cluster is deleting, waiting...")
                                        time.sleep(wait_interval)
                                        elapsed_time += wait_interval
                                        continue
                                    elif cluster_status in ['available', 'creating', 'modifying']:
                                        print(f"Cluster still exists with status {{cluster_status}}")
                                        time.sleep(wait_interval)
                                        elapsed_time += wait_interval
                                        continue
                                else:
                                    print("Cluster not found - deletion complete")
                                    break
                            except memorydb.exceptions.ClusterNotFoundFault:
                                print("Cluster not found - deletion complete")
                                break
                            except Exception as e:
                                print(f"Error checking cluster status: {{e}}")
                                break
                        
                        # Now try to clean up dependent resources if they still exist
                        try:
                            # Check and delete subnet group
                            try:
                                memorydb.describe_subnet_groups(SubnetGroupName=subnet_group_name)
                                print(f"Subnet group {{subnet_group_name}} still exists after cluster deletion")
                            except memorydb.exceptions.SubnetGroupNotFoundFault:
                                print(f"Subnet group {{subnet_group_name}} already deleted")
                            except Exception as e:
                                print(f"Error checking subnet group: {{e}}")
                            
                            # Check and delete parameter group
                            try:
                                memorydb.describe_parameter_groups(ParameterGroupName=param_group_name)
                                print(f"Parameter group {{param_group_name}} still exists after cluster deletion")
                            except memorydb.exceptions.ParameterGroupNotFoundFault:
                                print(f"Parameter group {{param_group_name}} already deleted")
                            except Exception as e:
                                print(f"Error checking parameter group: {{e}}")
                                
                        except Exception as e:
                            print(f"Error during cleanup: {{e}}")
                    
                    return {{
                        'Status': 'SUCCESS',
                        'PhysicalResourceId': f'memorydb-deletion-handler-{{cluster_name}}'
                    }}
                    
                except Exception as e:
                    print(f"Error in deletion handler: {{e}}")
                    return {{
                        'Status': 'SUCCESS',  # Return success to allow stack deletion
                        'PhysicalResourceId': f'memorydb-deletion-handler-{{properties.get("ClusterName", "unknown")}}'
                    }}
        ''').strip()
        
        # Create log group for the deletion handler Lambda (fixes deprecation warning)
        deletion_log_group = logs.LogGroup(
            self, f"{resource_prefix}-memorydb-deletion-handler{suffix}-logs",
            log_group_name=f"/aws/lambda/{resource_prefix}-memorydb-deletion-handler{suffix}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )

        deletion_lambda = lambda_.Function(
            self, f"{resource_prefix}-memorydb-deletion-handler{suffix}",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(deletion_code),
            timeout=Duration.minutes(14),  # Long wait... 14 minutes (840 seconds) - within Lambda's 15-minute limit
            description=f"MemoryDB deletion handler for {resource_prefix}",
            log_group=deletion_log_group
        )
        
        deletion_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "memorydb:DescribeClusters",
                    "memorydb:DescribeSubnetGroups",
                    "memorydb:DescribeParameterGroups",
                    "memorydb:DescribeACLs"
                ],
                resources=["*"]
            )
        )
        
        deletion_provider = cr.Provider(
            self, f"{resource_prefix}-memorydb-deletion-provider{suffix}",
            on_event_handler=deletion_lambda
            # Removed deprecated log_retention parameter - using log_group on Lambda instead
        )
        
        return CustomResource(
            self, f"{resource_prefix}-memorydb-deletion-handler-resource{suffix}",
            service_token=deletion_provider.service_token,
            properties={
                "ClusterName": cluster_name,
                "SubnetGroupName": subnet_group_name,
                "ParameterGroupName": param_group_name,
                "ACLName": acl_name
            }
        )

    def _create_memorydb_password(self, resource_prefix: str, suffix: str = "") -> secretsmanager.Secret:
        """Create MemoryDB password secret or reuse existing"""
        
        secret_name = f"{resource_prefix}-memorydb-password{suffix}"
        
        # Check if we should reuse existing secret
        resource_decisions = getattr(self, 'resource_decisions', {})
        secrets_decision = resource_decisions.get('secrets_manager_decision', 'create_new')
        
        if secrets_decision == 'reuse':
            discovery_results = resource_decisions.get('discovery_results', {})
            secrets_analysis = discovery_results.get('secrets_manager_analysis', {})
            
            if secrets_analysis.get('secrets_found'):
                secret_details = secrets_analysis.get('secret_details', {})
                memorydb_secret_details = secret_details.get('memorydb_password', {})
                existing_secret_name = memorydb_secret_details.get('name')
                
                if existing_secret_name:
                    print(f"🔄 Reusing existing Secrets Manager secret: {existing_secret_name}")
                    
                    # Import existing secret
                    return secretsmanager.Secret.from_secret_name_v2(
                        self, f"{resource_prefix}-existing-memorydb-password{suffix}",
                        existing_secret_name
                    )
        
        print(f"🆕 Creating new Secrets Manager secret: {secret_name}")
        
        return secretsmanager.Secret(
            self, f"{resource_prefix}-memorydb-password{suffix}",
            secret_name=secret_name,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({
                    "username": "glide-user",
                    "password": "",
                    "engine": "valkey",
                    "host": "",
                    "port": 6379,
                    "tls": True
                }),
                generate_string_key="password",
                exclude_characters=r"\"@/\\`'\\\\\\\\\\\${}[]();",
                password_length=32,
                exclude_punctuation=False,
                include_space=False,
                require_each_included_type=True
            ),
            description="Credential for MemoryDB Valkey cluster"
        )

    def _create_memorydb_import_provider(self) -> cr.Provider:
        """Create custom resource provider for MemoryDB import operations"""
        
        import_code = textwrap.dedent('''
            import boto3
            import json
            import time

            def handler(event, context):
                try:
                    request_type = event['RequestType']
                    properties = event['ResourceProperties']
                    cluster_name = properties['ClusterName']
                    action = properties.get('Action', 'IMPORT')
                    
                    print(f"Request type: {request_type}, Cluster: {cluster_name}, Action: {action}")
                    
                    if request_type == 'Create':
                        memorydb = boto3.client('memorydb')
                        
                        # Get cluster details
                        try:
                            response = memorydb.describe_clusters(ClusterName=cluster_name)
                            
                            if response['Clusters']:
                                cluster = response['Clusters'][0]
                                
                                # Get related resources
                                subnet_group_name = cluster.get('SubnetGroupName')
                                parameter_group_name = cluster.get('ParameterGroupName')
                                acl_name = cluster.get('ACLName')
                                
                                # Get subnet group details
                                subnet_group_details = {}
                                if subnet_group_name:
                                    try:
                                        sg_response = memorydb.describe_subnet_groups(SubnetGroupName=subnet_group_name)
                                        if sg_response['SubnetGroups']:
                                            subnet_group_details = sg_response['SubnetGroups'][0]
                                    except Exception as e:
                                        print(f"Warning: Could not get subnet group details: {e}")
                                
                                # Get parameter group details
                                parameter_group_details = {}
                                if parameter_group_name:
                                    try:
                                        pg_response = memorydb.describe_parameter_groups(ParameterGroupName=parameter_group_name)
                                        if pg_response['ParameterGroups']:
                                            parameter_group_details = pg_response['ParameterGroups'][0]
                                    except Exception as e:
                                        print(f"Warning: Could not get parameter group details: {e}")
                                
                                # Get ACL details
                                acl_details = {}
                                if acl_name:
                                    try:
                                        acl_response = memorydb.describe_acls(ACLName=acl_name)
                                        if acl_response['ACLs']:
                                            acl_details = acl_response['ACLs'][0]
                                    except Exception as e:
                                        print(f"Warning: Could not get ACL details: {e}")
                                
                                # Return comprehensive details
                                return {
                                    'Status': 'SUCCESS',
                                    'PhysicalResourceId': cluster_name,
                                    'Data': {
                                        'ClusterName': cluster['Name'],
                                        'Endpoint': cluster.get('ClusterEndpoint', {}).get('Address', ''),
                                        'Status': cluster['Status'],
                                        'SubnetGroupName': subnet_group_name,
                                        'ParameterGroupName': parameter_group_name,
                                        'ACLName': acl_name,
                                        'SubnetGroupDetails': json.dumps(subnet_group_details),
                                        'ParameterGroupDetails': json.dumps(parameter_group_details),
                                        'ACLDetails': json.dumps(acl_details)
                                    }
                                }
                            else:
                                return {
                                    'Status': 'FAILED',
                                    'Reason': f'Cluster {cluster_name} not found'
                                }
                        except Exception as e:
                            print(f"Error getting cluster details: {e}")
                            return {
                                'Status': 'FAILED',
                                'Reason': f'Error getting cluster details: {str(e)}'
                            }
                    
                    elif request_type == 'Update':
                        # For updates, just return success with the current details
                        memorydb = boto3.client('memorydb')
                        
                        try:
                            response = memorydb.describe_clusters(ClusterName=cluster_name)
                            
                            if response['Clusters']:
                                cluster = response['Clusters'][0]
                                return {
                                    'Status': 'SUCCESS',
                                    'PhysicalResourceId': cluster_name,
                                    'Data': {
                                        'ClusterName': cluster['Name'],
                                        'Endpoint': cluster.get('ClusterEndpoint', {}).get('Address', ''),
                                        'Status': cluster['Status']
                                    }
                                }
                            else:
                                return {
                                    'Status': 'FAILED',
                                    'Reason': f'Cluster {cluster_name} not found during update'
                                }
                        except Exception as e:
                            print(f"Error during update: {e}")
                            return {
                                'Status': 'FAILED',
                                'Reason': f'Error during update: {str(e)}'
                            }
                    
                    elif request_type == 'Delete':
                        # For delete, we don't actually delete the cluster, just return success
                        return {
                            'Status': 'SUCCESS',
                            'PhysicalResourceId': cluster_name
                        }
                    
                    return {'Status': 'SUCCESS', 'PhysicalResourceId': cluster_name}
                    
                except Exception as e:
                    print(f"Unexpected error: {e}")
                    return {
                        'Status': 'FAILED',
                        'Reason': str(e)
                    }
        ''').strip()
        
        import_lambda = lambda_.Function(
            self, "MemoryDBImportLambda",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(import_code),
            timeout=Duration.minutes(5)
        )
        
        import_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "memorydb:DescribeClusters",
                    "memorydb:DescribeUsers",
                    "memorydb:DescribeACLs",
                    "memorydb:DescribeSubnetGroups",
                    "memorydb:DescribeParameterGroups",
                    "memorydb:DescribeParameters"
                ],
                resources=["*"]
            )
        )
        
        return cr.Provider(
            self, "MemoryDBImportProvider",
            on_event_handler=import_lambda
        )

    def _create_new_dynamodb_table(
        self, resource_prefix: str, table_type: str, table_name: str, kms_key: kms.Key, environment: str = "dev"
    ) -> dynamodb.TableV2:
        """Create new DynamoDB table based on type"""
        
        if table_type == 'config':
            return dynamodb.TableV2(
                self, f"{resource_prefix}-config-table",
                table_name=table_name,
                partition_key=dynamodb.Attribute(
                    name="leaderboardName",
                    type=dynamodb.AttributeType.STRING
                ),
                billing=dynamodb.Billing.on_demand(),
                removal_policy=RemovalPolicy.RETAIN,
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True,
                    recovery_period_in_days=35
                ),
                table_class=dynamodb.TableClass.STANDARD_INFREQUENT_ACCESS,
                encryption=dynamodb.TableEncryptionV2.customer_managed_key(kms_key),
                deletion_protection=environment in ["prod", "staging"],
                global_secondary_indexes=[
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="gameID-gameMode-index",
                        partition_key=dynamodb.Attribute(
                            name="gameID",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="gameMode",
                            type=dynamodb.AttributeType.STRING
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    )
                ]
            )
        
        elif table_type == 'stats':
            return dynamodb.TableV2(
                self, f"{resource_prefix}-stats-table",
                table_name=table_name,
                partition_key=dynamodb.Attribute(
                    name="playerID",
                    type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="sortKey",
                    type=dynamodb.AttributeType.STRING
                ),
                billing=dynamodb.Billing.on_demand(),
                removal_policy=RemovalPolicy.RETAIN,
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True,
                    recovery_period_in_days=35
                ),
                table_class=dynamodb.TableClass.STANDARD,
                encryption=dynamodb.TableEncryptionV2.customer_managed_key(kms_key),
                deletion_protection=environment in ["prod", "staging"],
                global_secondary_indexes=[
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="leaderboardName-timestamp-index",
                        partition_key=dynamodb.Attribute(
                            name="leaderboardName",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="timestamp",
                            type=dynamodb.AttributeType.NUMBER
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    ),
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="gameID-gameMode-index",
                        partition_key=dynamodb.Attribute(
                            name="gameID",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="gameMode",
                            type=dynamodb.AttributeType.STRING
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    )
                ]
            )

    def _create_new_dynamodb_table_with_suffix(
        self, resource_prefix: str, table_type: str, table_name: str, kms_key: kms.Key, suffix: str = "", environment: str = "dev"
    ) -> dynamodb.TableV2:
        """Create new DynamoDB table with suffix for replacement"""
        
        # Use the existing _create_new_dynamodb_table method but with modified construct ID
        if table_type == 'config':
            return dynamodb.TableV2(
                self, f"{resource_prefix}-config-table{suffix}",  # Modified construct ID
                table_name=table_name,
                partition_key=dynamodb.Attribute(
                    name="leaderboardName",
                    type=dynamodb.AttributeType.STRING
                ),
                billing=dynamodb.Billing.on_demand(),
                removal_policy=RemovalPolicy.DESTROY if suffix else RemovalPolicy.RETAIN,  # Allow deletion for replacements
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True,
                    recovery_period_in_days=35
                ),
                table_class=dynamodb.TableClass.STANDARD_INFREQUENT_ACCESS,
                encryption=dynamodb.TableEncryptionV2.customer_managed_key(kms_key),
                deletion_protection=environment in ["prod", "staging"],
                global_secondary_indexes=[
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="gameID-gameMode-index",
                        partition_key=dynamodb.Attribute(
                            name="gameID",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="gameMode",
                            type=dynamodb.AttributeType.STRING
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    )
                ]
            )
        
        elif table_type == 'stats':
            return dynamodb.TableV2(
                self, f"{resource_prefix}-stats-table{suffix}",
                table_name=table_name,
                partition_key=dynamodb.Attribute(
                    name="playerID",
                    type=dynamodb.AttributeType.STRING
                ),
                sort_key=dynamodb.Attribute(
                    name="sortKey",
                    type=dynamodb.AttributeType.STRING
                ),
                billing=dynamodb.Billing.on_demand(),
                removal_policy=RemovalPolicy.DESTROY if suffix else RemovalPolicy.RETAIN,
                point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True,
                    recovery_period_in_days=35
                ),
                table_class=dynamodb.TableClass.STANDARD,
                encryption=dynamodb.TableEncryptionV2.customer_managed_key(kms_key),
                deletion_protection=environment in ["prod", "staging"],
                global_secondary_indexes=[
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="leaderboardName-timestamp-index",
                        partition_key=dynamodb.Attribute(
                            name="leaderboardName",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="timestamp",
                            type=dynamodb.AttributeType.NUMBER
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    ),
                    dynamodb.GlobalSecondaryIndexPropsV2(
                        index_name="gameID-gameMode-index",
                        partition_key=dynamodb.Attribute(
                            name="gameID",
                            type=dynamodb.AttributeType.STRING
                        ),
                        sort_key=dynamodb.Attribute(
                            name="gameMode",
                            type=dynamodb.AttributeType.STRING
                        ),
                        projection_type=dynamodb.ProjectionType.ALL
                    )
                ]
            )

        else:
            raise ValueError(f"Unknown table type: {table_type}")

    def _create_shared_layer(self, resource_prefix: str) -> lambda_.LayerVersion:
        """Create shared Lambda Layer with automatic versioning or reuse existing"""
        
        # Check if we should reuse existing layer
        resource_decisions = getattr(self, 'resource_decisions', {})
        lambda_decision = resource_decisions.get('lambda_decision', 'create_new')
        
        if lambda_decision == 'reuse':
            print(f"🔄 Attempting to reuse existing Lambda Layer")

            try:
                lambda_client = boto3.client('lambda', region_name=self.region)

                # List layers with our prefix
                response = lambda_client.list_layers()
                
                for layer in response.get('Layers', []):
                    layer_name = layer.get('LayerName', '')
                    if resource_prefix in layer_name and 'valkey-glide' in layer_name:
                        # Get the latest version
                        versions_response = lambda_client.list_layer_versions(LayerName=layer_name)
                        if versions_response.get('LayerVersions'):
                            latest_version = versions_response['LayerVersions'][0]
                            layer_arn = latest_version['LayerVersionArn']
                            
                            print(f"🔄 Reusing existing Lambda Layer: {layer_name}")
                            print(f"   Layer ARN: {layer_arn}")
                            
                            # Import existing layer
                            return lambda_.LayerVersion.from_layer_version_arn(
                                self, f"{resource_prefix}-existing-valkey-glide-layer",
                                layer_arn
                            )
                
                print(f"🔍 No existing layer found - creating new one")
                
            except Exception as e:
                print(f"⚠️ Error checking for existing layer: {e}")
                print(f"🆕 Creating new Lambda Layer")
        
        # Generate a hash of the layer content for versioning
        layer_hash = hashlib.md5(f"{resource_prefix}-{datetime.now().strftime('%Y%m%d')}".encode()).hexdigest()[:8]
        
        return lambda_.LayerVersion(
            self, f"{resource_prefix}-valkey-glide-layer",
            layer_version_name=f"{resource_prefix}-valkey-glide-layer-{layer_hash}",
            code=lambda_.Code.from_asset("layers/valkey-glide-layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_13],
            compatible_architectures=[lambda_.Architecture.X86_64],
            description=f"Valkey-GLIDE 2.0.1+ and shared dependencies for game stats & leaderboards system (v{layer_hash})",
            removal_policy=RemovalPolicy.RETAIN
        )

    def _create_lambda_roles(
        self, resource_prefix: str, config_table: dynamodb.TableV2,
        stats_table: dynamodb.TableV2, memorydb_password: secretsmanager.Secret
    ) -> Dict[str, iam.Role]:
        """Create least-privilege Lambda execution roles, grouped by access pattern.

        Returns a dict keyed by role group; ROLE_GROUP (in
        _create_lambda_functions_with_application) maps each function to a key.
        """
        region, account = self.region, self.account
        ssm_arn = f"arn:aws:ssm:{region}:{account}:parameter/{resource_prefix}/*"

        def fn_arn(name):
            return f"arn:aws:lambda:{region}:{account}:function:{resource_prefix}-{name}"

        # Reuse an existing single role if discovery selected it (legacy deployments)
        resource_decisions = getattr(self, 'resource_decisions', {})
        if resource_decisions.get('iam_decision') == 'reuse':
            iam_analysis = resource_decisions.get('discovery_results', {}).get('iam_analysis', {})
            if iam_analysis.get('roles_found'):
                role_arn = iam_analysis.get('role_details', {}).get(
                    f"{resource_prefix}-lambda-role", {}).get('role_arn')
                if role_arn:
                    print(f"Reusing existing IAM role for all functions: {resource_prefix}-lambda-role")
                    existing = iam.Role.from_role_arn(
                        self, f"{resource_prefix}-existing-lambda-role", role_arn=role_arn)
                    return {key: existing for key in (
                        "player_auth", "backend_auth", "developer_registration",
                        "query_read", "stats_writer", "lb_admin")}

        print(f"Creating least-privilege IAM roles for {resource_prefix}")

        def base(name, vpc=False):
            managed = [
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
            ]
            if vpc:
                managed.append(iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"))
            return iam.Role(
                self, f"{resource_prefix}-{name}", role_name=f"{resource_prefix}-{name}",
                assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"), managed_policies=managed)

        def add_memorydb(r):
            memorydb_password.grant_read(r)
            r.add_to_policy(iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["memorydb:DescribeClusters", "memorydb:DescribeUsers", "memorydb:DescribeACLs"],
                resources=["*"]))

        def add_ssm_read(r):
            r.add_to_policy(iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
                resources=[ssm_arn]))
            r.add_to_policy(iam.PolicyStatement(
                effect=iam.Effect.ALLOW, actions=["ssm:DescribeParameters"], resources=["*"]))
            # Decrypt SecureString parameters (default aws/ssm key); scoped to SSM use only
            r.add_to_policy(iam.PolicyStatement(
                effect=iam.Effect.ALLOW, actions=["kms:Decrypt"], resources=["*"],
                conditions={"StringEquals": {"kms:ViaService": f"ssm.{region}.amazonaws.com"}}))

        roles = {}

        # 1. player_authorizer -- logs + X-Ray only (stub; integrators add perms here)
        roles["player_auth"] = base("player-auth-role")

        # 2. backend_authorizer -- SSM read + self-heal own function config
        r = base("backend-auth-role")
        add_ssm_read(r)
        r.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["lambda:GetFunctionConfiguration", "lambda:UpdateFunctionConfiguration"],
            resources=[fn_arn("backend-authorizer")]))
        roles["backend_auth"] = r

        # 3. developer_registration -- SSM read/write/delete/tag + update authorizer config
        r = base("developer-registration-role")
        add_ssm_read(r)
        r.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["ssm:PutParameter", "ssm:DeleteParameter",
                     "ssm:AddTagsToResource", "ssm:ListTagsForResource"],
            resources=[ssm_arn]))
        r.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["lambda:GetFunctionConfiguration", "lambda:UpdateFunctionConfiguration"],
            resources=[fn_arn("backend-authorizer")]))
        # Flush the authorizer cache on key revoke/regenerate so invalidated API keys
        # are not honored for up to the authorizer's 5-minute results_cache_ttl. Roles are
        # built before the REST API exists, so the API id is not yet known; scope to this
        # account's REST API stages in-region.
        r.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["apigateway:FlushStageAuthorizersCache"],
            resources=[f"arn:aws:apigateway:{region}::/restapis/*/stages/*"]))
        roles["developer_registration"] = r

        # 4. read-only player queries (config R + stats R + MemoryDB + VPC)
        r = base("lb-query-read-role", vpc=True)
        config_table.grant_read_data(r)
        stats_table.grant_read_data(r)
        add_memorydb(r)
        roles["query_read"] = r

        # 5. stats writers (config R + stats R/W + MemoryDB + VPC)
        r = base("stats-writer-role", vpc=True)
        config_table.grant_read_data(r)
        stats_table.grant_read_write_data(r)
        add_memorydb(r)
        roles["stats_writer"] = r

        # 6. leaderboard admin (config R/W + stats R + MemoryDB + VPC + self-invoke relay)
        r = base("lb-admin-role", vpc=True)
        config_table.grant_read_write_data(r)
        stats_table.grant_read_data(r)
        add_memorydb(r)
        r.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["lambda:InvokeFunction"],
            resources=[fn_arn("reset-leaderboard"), fn_arn("rebuild-leaderboard")]))
        roles["lb_admin"] = r

        return roles

    def _create_lambda_application(self, resource_prefix: str, environment: str) -> tuple[resourcegroups.CfnGroup, appinsights.CfnApplication]:
        """Create Lambda Application with explicit resource group or reuse existing"""
        
        # Check if we should reuse existing Lambda Application
        resource_decisions = getattr(self, 'resource_decisions', {})
        lambda_app_decision = resource_decisions.get('lambda_application_decision', 'create_new')
        
        if lambda_app_decision == 'reuse':
            discovery_results = resource_decisions.get('discovery_results', {})
            lambda_app_analysis = discovery_results.get('lambda_application_analysis', {})
            
            if lambda_app_analysis.get('application_found') and lambda_app_analysis.get('resource_group_found'):
                print(f"🔄 Reusing existing Lambda Application and Resource Group")
                
                # Get existing resource group details
                rg_details = lambda_app_analysis.get('resource_group_details', {})
                rg_name = rg_details.get('name', f"{resource_prefix}-lambda-functions")
                rg_arn = rg_details.get('arn', '')
                
                # Import existing resource group
                resource_group = resourcegroups.CfnGroup(
                    self, f"{resource_prefix}-existing-lambda-resource-group",
                    name=rg_name,
                    description=f"Resource group for {resource_prefix} Lambda functions (existing)",
                    resource_query=resourcegroups.CfnGroup.ResourceQueryProperty(
                        type="TAG_FILTERS_1_0",
                        query=resourcegroups.CfnGroup.QueryProperty(
                            resource_type_filters=["AWS::Lambda::Function"],
                            tag_filters=[
                                resourcegroups.CfnGroup.TagFilterProperty(
                                    key="Application",
                                    values=["GameStatsLeaderboards"]
                                ),
                                resourcegroups.CfnGroup.TagFilterProperty(
                                    key="Environment", 
                                    values=[environment]
                                )
                            ]
                        )
                    ),
                    tags=[
                        CfnTag(key="Environment", value=environment),
                        CfnTag(key="Service", value="game-statsleaderboards"),
                        CfnTag(key="ManagedBy", value="CDK"),
                        CfnTag(key="Reused", value="true")
                    ]
                )
                
                # Import existing Application Insights application
                app_insights_app = appinsights.CfnApplication(
                    self, f"{resource_prefix}-existing-lambda-application",
                    resource_group_name=rg_name,
                    auto_configuration_enabled=True,
                    cwe_monitor_enabled=True,
                    ops_center_enabled=True,
                    tags=[
                        CfnTag(key="Environment", value=environment),
                        CfnTag(key="Service", value="game-statsleaderboards"),
                        CfnTag(key="ManagedBy", value="CDK"),
                        CfnTag(key="Application", value="GameStatsLeaderboards"),
                        CfnTag(key="Reused", value="true")
                    ]
                )
                
                return resource_group, app_insights_app
        
        print(f"🆕 Creating new Lambda Application: {resource_prefix}")
        
        # Create resource group first
        resource_group = resourcegroups.CfnGroup(
            self, f"{resource_prefix}-lambda-resource-group",
            name=f"{resource_prefix}-lambda-functions",
            description=f"Resource group for {resource_prefix} Lambda functions",
            resource_query=resourcegroups.CfnGroup.ResourceQueryProperty(
                type="TAG_FILTERS_1_0",
                query=resourcegroups.CfnGroup.QueryProperty(
                    resource_type_filters=["AWS::Lambda::Function"],
                    tag_filters=[
                        resourcegroups.CfnGroup.TagFilterProperty(
                            key="Application",
                            values=["GameStatsLeaderboards"]
                        ),
                        resourcegroups.CfnGroup.TagFilterProperty(
                            key="Environment", 
                            values=[environment]
                        )
                    ]
                )
            ),
            tags=[
                CfnTag(key="Environment", value=environment),
                CfnTag(key="Service", value="game-statsleaderboards"),
                CfnTag(key="ManagedBy", value="CDK")
            ]
        )

        # Create Application Insights application
        app_insights_app = appinsights.CfnApplication(
            self, f"{resource_prefix}-lambda-application",
            resource_group_name=resource_group.name,
            auto_configuration_enabled=True,
            cwe_monitor_enabled=True,
            ops_center_enabled=True,
            tags=[
                CfnTag(key="Environment", value=environment),
                CfnTag(key="Service", value="game-statsleaderboards"),
                CfnTag(key="ManagedBy", value="CDK"),
                CfnTag(key="Application", value="GameStatsLeaderboards")
            ]
        )
        
        # Add dependency
        app_insights_app.add_dependency(resource_group)
        
        return resource_group, app_insights_app

    def _discover_api_key_parameters(self, resource_prefix: str) -> list:
        """
        Discover existing API key parameters in SSM Parameter Store.
        Used to populate API_KEY_PARAMETER_NAMES environment variable for high-performance authorization.
        
        Returns:
            List of parameter names (e.g., ["/game-statsleaderboards-dev/api-keys/studio1-game1"])
        """
        try:
            ssm_client = boto3.client('ssm', region_name=self.region)
            parameter_names = []
            
            paginator = ssm_client.get_paginator('get_parameters_by_path')
            for page in paginator.paginate(
                Path=f"/{resource_prefix}/api-keys/",
                Recursive=False,
                WithDecryption=False
            ):
                for parameter in page.get('Parameters', []):
                    param_name = parameter.get('Name', '')
                    if param_name:
                        parameter_names.append(param_name)
            
            if parameter_names:
                print(f"✓ Discovered {len(parameter_names)} API key parameter(s) for high-performance authorization")
            else:
                print(f"ℹ️  No API key parameters found yet (normal for first deployment)")
            
            return parameter_names
            
        except Exception as e:
            print(f"⚠️  Could not discover API key parameters: {str(e)}")
            print(f"   Authorizer will use fallback discovery (100 TPS instead of 10,000 TPS)")
            return []

    def _create_lambda_functions_with_application(
        self, resource_prefix: str, vpc: ec2.Vpc, roles: Dict[str, iam.Role],
        config_table: dynamodb.TableV2, stats_table: dynamodb.TableV2,
        memorydb_cluster, memorydb_password: secretsmanager.Secret,
        shared_layer: lambda_.LayerVersion, config: Dict[str, Any], 
        lambda_application: appinsights.CfnApplication = None,
        resource_group: resourcegroups.CfnGroup = None
    ) -> Dict[str, lambda_.Function]:
        """
        Create Lambda functions with consolidated environment variables
        """
        
        # Get current environment
        environment = self.node.try_get_context("environment") or "dev"

        # Check if we should reuse existing Lambda functions
        resource_decisions = getattr(self, 'resource_decisions', {})
        lambda_decision = resource_decisions.get('lambda_decision', 'create_new')
        lambda_functions_info = resource_decisions.get('lambda_functions', {})
        replacement_strategy = resource_decisions.get('replacement_strategy', 'smart_reuse')

        # Get MemoryDB endpoint (handle both new and existing clusters)
        if hasattr(memorydb_cluster, 'attr_cluster_endpoint_address'):
            memorydb_endpoint = memorydb_cluster.attr_cluster_endpoint_address
        else:
            memorydb_endpoint = getattr(memorydb_cluster, 'attr_cluster_endpoint_address', 'localhost')

        # Single consolidated environment variables object
        base_environment_vars = {
            # Core Infrastructure (single source of truth)
            "ENVIRONMENT": environment,
            "AWS_REGION_NAME": self.region,
            "AWS_ACCOUNT_ID": self.account,
            "STACK_NAME": self.stack_name,
            "RESOURCE_PREFIX": resource_prefix,

            # API Gateway (will be updated after API creation)
            "API_ENDPOINT": f"https://{{api_id}}.execute-api.{self.region}.amazonaws.com/{environment}/",
            "API_STAGE": environment,

            # DynamoDB Tables
            "CONFIG_TABLE_NAME": config_table.table_name,
            "STATS_TABLE_NAME": stats_table.table_name,
            "gameLeaderboardsConfigTablename": config_table.table_name,
            "gameStatsAndScoresTablename": stats_table.table_name,

            # MemoryDB
            "gameLeaderboardsMemoryDBName": getattr(memorydb_cluster, 'cluster_name', f"{resource_prefix}-cluster"),
            "MEMORYDB_CLUSTER_ENDPOINT": memorydb_endpoint,
            "MEMORYDB_PORT": "6379",
            "MEMORYDB_SECRET_ARN": memorydb_password.secret_arn,
            "MEMORYDB_CLUSTER_NAME": getattr(memorydb_cluster, 'cluster_name', f"{resource_prefix}-cluster"),

            # SSM Parameters (single source) - ENHANCED FOR API KEY MANAGEMENT
            "SSM_PARAMETER_PREFIX": f"/{resource_prefix}",

            # GLIDE Configuration (consolidated)
            "VALKEY_USE_TLS": "true",
            "VALKEY_CLUSTER_MODE": "true",
            "GLIDE_CLIENT_NAME": f"game-statsleaderboards-{environment}-glide",
            "GLIDE_VERSION": "2.0.1",
            "GLIDE_CONNECTION_TIMEOUT_MS": "3000",  # Optimized: 60% faster connection establishment
            "GLIDE_REQUEST_TIMEOUT_MS": "5000",     # Increased for failover resilience (was 2500ms)
            "GLIDE_SOCKET_TIMEOUT_MS": "3000",
            "GLIDE_RECONNECT_STRATEGY": "ExponentialBackoff",
            "GLIDE_MAX_RECONNECT_ATTEMPTS": "3",
            "GLIDE_RETRY_ATTEMPTS": "2",
            "GLIDE_RETRY_DELAY_MS": "100",
            "GLIDE_DATABASE_ID": "0",
            "GLIDE_READ_FROM": "Primary",
            "GLIDE_CONNECTION_POOL_SIZE": "500" if environment in ['staging', 'prod'] else "1000",  # Prod-like for staging
            "GLIDE_KEEP_ALIVE_ENABLED": "true",
            "GLIDE_KEEP_ALIVE_INTERVAL_MS": "30000",
            "GLIDE_LOG_LEVEL": "INFO",
            "GLIDE_METRICS_ENABLED": "true",
            "GLIDE_CIRCUIT_BREAKER_ENABLED": "true",
            "GLIDE_CIRCUIT_BREAKER_THRESHOLD": "5",
            "GLIDE_CIRCUIT_BREAKER_TIMEOUT_MS": "60000",
            
            # PowerTools Configuration
            "POWERTOOLS_SERVICE_NAME": "game-statsleaderboards",
            "POWERTOOLS_METRICS_NAMESPACE": f"GameStatsLeaderboards/{environment.title()}",
            "POWERTOOLS_LOGGER_LOG_EVENT": "true",
            "LOG_LEVEL": "DEBUG" if environment == 'dev' else "INFO",  # INFO for staging and prod
            
            # Application Context
            "LAMBDA_APPLICATION_NAME": lambda_application.resource_group_name if lambda_application else "",
            "APPLICATION_INSIGHTS_ENABLED": "true" if lambda_application else "false",
            "RESOURCE_GROUP_NAME": resource_group.name if resource_group else "",
            
            # Environment-Specific Settings (single calculation)
            "DEFAULT_RATE_LIMIT": str(2000 if environment == 'prod' else (1500 if environment == 'staging' else 1000)),
            "KEY_ROTATION_DAYS": str(90 if environment == 'prod' else (60 if environment == 'staging' else 30)),
            
            # Service Discovery
            "SERVICE_VERSION": "1.0.0",
            "DEPLOYMENT_TIMESTAMP": datetime.now(timezone.utc).isoformat(),
            
            # Validation Configuration - Enable comprehensive validation for testing
            "VALIDATE_VALKEY": "true",
            "INCLUDE_LEADERBOARD_DATA": "true"
        }
        
        functions = {}
        
        # Function configurations with application grouping
        function_configs = [
            ("backend_authorizer", "auth", "backendAuthorizer.lambda_handler", 10, 256, False, "Authentication"),
            ("player_authorizer", "auth", "playerAuthorizer.lambda_handler", 10, 256, False, "Authentication"),
            ("developer_registration", "backend", "developerRegistration.lambda_handler", 30, 256, False, "DeveloperManagement"),
            ("leaderboards_config", "backend", "leaderboardsConfig.lambda_handler", 15, 512, True, "LeaderboardManagement"),
            ("reset_leaderboard", "backend", "resetLeaderboard.lambda_handler", 60, 512, True, "LeaderboardOperations"),
            ("player_store_stats", "player", "storePlayerStatsAndScores.lambda_handler", 30, 512, True, "PlayerOperations"),
            ("get_player_stats", "player", "getPlayerStatsAndScores.lambda_handler", 15, 512, True, "PlayerOperations"),
            ("get_leaderboard_scores", "player", "getLeaderboardScores.lambda_handler", 15, 512, True, "PlayerOperations"),
            ("get_player_lb_standing", "player", "getPlayerLBStanding.lambda_handler", 15, 512, True, "PlayerOperations"),
            ("rebuild_leaderboard", "backend", "rebuildLeaderboard.lambda_handler", 60, 512, True, "LeaderboardOperations"),
            ("batch_store_stats", "backend", "batchStoreStatsAndScores.lambda_handler", 60, 512, True, "BatchOperations"),
        ]
        
        # Map each function to its least-privilege role group (see _create_lambda_roles)
        ROLE_GROUP = {
            "player_authorizer": "player_auth",
            "backend_authorizer": "backend_auth",
            "developer_registration": "developer_registration",
            "leaderboards_config": "lb_admin",
            "reset_leaderboard": "lb_admin",
            "rebuild_leaderboard": "lb_admin",
            "batch_store_stats": "stats_writer",
            "player_store_stats": "stats_writer",
            "get_player_stats": "query_read",
            "get_leaderboard_scores": "query_read",
            "get_player_lb_standing": "query_read",
        }

        for func_name, code_path, handler, timeout_seconds, mem_size, needs_vpc, component in function_configs:
            timeout = Duration.seconds(timeout_seconds)
            function_key = func_name.replace('_', '-')
            
            # Check if we should reuse this function
            should_reuse = (
                lambda_decision == 'reuse' and 
                lambda_functions_info.get('functions_found') and 
                function_key in lambda_functions_info.get('functions', {}) and
                replacement_strategy != 'force_replacement'
            )
            
            # Create function-specific environment by copying base and adding function-specific vars
            func_env = base_environment_vars.copy()
            func_env.update({
                "FUNCTION_NAME": func_name,
                "COMPONENT": component,
                "GLIDE_CLIENT_NAME": f"game-statsleaderboards-{func_name}-{environment}-glide",
                "POWERTOOLS_METRICS_NAMESPACE": f"GameStatsLeaderboards/{component}"
            })
            
            # Add function-specific overrides only where needed
            if func_name == "backend_authorizer":
                # Discover existing API key parameters for high-performance authorization (10,000 TPS)
                api_key_param_names = self._discover_api_key_parameters(resource_prefix)

                func_env.update({
                    "JWT_SECRET": "your-jwt-secret-key-change-in-production",
                    "ALLOWED_API_KEYS": "demo-key-1,demo-key-2,demo-key-3",
                    "POWERTOOLS_SERVICE_NAME": "backend-authorizer",
                    "API_KEY_PARAMETER_NAMES": json.dumps(api_key_param_names)  # For 10,000 TPS performance
                })
            elif func_name == "player_authorizer":
                # Potential INTEGRATION POINT — add environment variables for your player auth
                # (e.g., JWT secret, OAuth endpoint, session store table name)
                func_env.update({
                    "POWERTOOLS_SERVICE_NAME": "player-authorizer",
                    "STUDIO_ID": "",   # Set after deployment or read from SSM
                    "GAME_ID": "",     # Set after deployment or read from SSM
                })
            elif func_name == "developer_registration":
                func_env.update({
                    "POWERTOOLS_SERVICE_NAME": "developer-registration",
                    "AUTHORIZER_FUNCTION_NAME": f"{resource_prefix}-backend-authorizer"  # For updating env vars
                })
            
            # Add application-specific tags
            function_tags = {
                "Application": "GameStatsLeaderboards",
                "Component": component,
                "Environment": environment,
                "Service": "game-statsleaderboards"
            }
            
            if should_reuse:
                # Get existing function details
                existing_function = lambda_functions_info['functions'].get(function_key, {})
                function_name = existing_function.get('function_name')
                
                if function_name:
                    print(f"🔄 Reusing existing Lambda function: {function_name}")
                    
                    # Import existing function
                    function = lambda_.Function.from_function_name(
                        self, f"{resource_prefix}-{function_key}-ref",
                        function_name
                    )
                    
                    functions[func_name] = function
                    continue
            
            # Create log group for the function (fixes deprecation warning)
            log_group = logs.LogGroup(
                self, f"{resource_prefix}-{function_key}-logs",
                log_group_name=f"/aws/lambda/{resource_prefix}-{function_key}",
                retention=logs.RetentionDays.ONE_WEEK if environment == 'dev' else logs.RetentionDays.ONE_MONTH,  # ONE_MONTH for staging and prod
                removal_policy=RemovalPolicy.DESTROY
            )

            # Create new function with consolidated environment variables and proper log group
            if needs_vpc:
                function = lambda_.Function(
                    self, f"{resource_prefix}-{function_key}",
                    function_name=f"{resource_prefix}-{function_key}",
                    code=lambda_.Code.from_asset(code_path),
                    handler=handler,
                    runtime=lambda_.Runtime.PYTHON_3_13,
                    architecture=lambda_.Architecture.X86_64,
                    timeout=timeout,
                    memory_size=mem_size,
                    environment=func_env,
                    vpc=vpc,
                    vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                    role=roles[ROLE_GROUP[func_name]],
                    layers=[shared_layer],
                    log_group=log_group
                )
            else:
                function = lambda_.Function(
                    self, f"{resource_prefix}-{function_key}",
                    function_name=f"{resource_prefix}-{function_key}",
                    code=lambda_.Code.from_asset(code_path),
                    handler=handler,
                    runtime=lambda_.Runtime.PYTHON_3_13,
                    architecture=lambda_.Architecture.X86_64,
                    timeout=timeout,
                    memory_size=mem_size,
                    environment=func_env,
                    role=roles[ROLE_GROUP[func_name]],
                    layers=[shared_layer],
                    log_group=log_group
                )
            
            # Add tags to function
            for key, value in function_tags.items():
                Tags.of(function).add(key, value)
            
            functions[func_name] = function
        
        if lambda_decision == 'reuse' and lambda_functions_info.get('functions_found'):
            print(f"✅ Reused {len(lambda_functions_info.get('functions', {}))} existing Lambda functions and created {len(functions) - len(lambda_functions_info.get('functions', {}))} new functions")
        else:
            print(f"✅ Created {len(functions)} new Lambda functions with consolidated environment variables and proper log groups" + 
                (f" grouped under application: {lambda_application.resource_group_name}" if lambda_application else ""))
        
        return functions

    def _create_ssm_parameters(
        self, resource_prefix: str, memorydb_cluster,
        memorydb_password: secretsmanager.Secret, config_table: dynamodb.TableV2,
        stats_table: dynamodb.TableV2, api: apigw.RestApi, environment: str
    ):
        """
        Create or reuse SSM parameters with smart conflict resolution
        """
        
        # Get MemoryDB endpoint (handle both new and existing clusters)
        if hasattr(memorydb_cluster, 'attr_cluster_endpoint_address'):
            memorydb_endpoint = memorydb_cluster.attr_cluster_endpoint_address
        else:
            memorydb_endpoint = getattr(memorydb_cluster, 'attr_cluster_endpoint_address', 'localhost')
        
        # Check existing SSM parameters first
        try:
            existing_params = self._check_existing_ssm_parameters(resource_prefix)
            print(f"🔍 Found {len(existing_params)} existing SSM parameters")
        except Exception as e:
            print(f"⚠️ Could not check existing SSM parameters: {e}")
            existing_params = {}
        
        # Single consolidated configuration object
        consolidated_config = {
            # Core Infrastructure
            "core": {
                "environment": environment,
                "region": self.region,
                "account_id": self.account,
                "stack_name": self.stack_name,
                "resource_prefix": resource_prefix,
                "deployment_timestamp": datetime.now(timezone.utc).isoformat(),
                "service_version": "1.0.0",
                "glide_version": "2.0.1"
            },
            
            # API Gateway
            "api": {
                "id": api.rest_api_id,
                "endpoint": f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/{environment}/",
                "endpoint_root": f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/",
                "stage": environment,
                "type": "REST",
                "stages_available": ["dev", "staging", "prod"]
            },
            
            # DynamoDB Tables
            "dynamodb": {
                "gameLeaderboardsConfigTablename": config_table.table_name,
                "gameStatsAndScoresTablename": stats_table.table_name
            },
            
            # MemoryDB Configuration
            "memorydb": {
                "endpoint": memorydb_endpoint,
                "port": "6379",
                "secret_arn": memorydb_password.secret_arn,
                "cluster_name": getattr(memorydb_cluster, 'cluster_name', f"{resource_prefix}-cluster"),
                "tls_enabled": "true"
            },
            
            # GLIDE Configuration (consolidated)
            "glide": {
                "version": "2.0.1",
                "tls_enabled": "true",
                "client_name": f"game-statsleaderboards-{environment}-glide",
                "connection_timeout_ms": "5000",
                "request_timeout_ms": "3000",
                "socket_timeout_ms": "3000",
                "reconnect_strategy": "ExponentialBackoff",
                "max_reconnect_attempts": "3",
                "retry_attempts": "2",
                "retry_delay_ms": "100",
                "database_id": "0",
                "read_from": "Primary",
                "connection_pool_size": "500" if environment in ['staging', 'prod'] else "1000",  # Prod-like for staging
                "keep_alive_enabled": "true",
                "keep_alive_interval_ms": "30000",
                "log_level": "INFO",
                "metrics_enabled": "true",
                "circuit_breaker_enabled": "true",
                "circuit_breaker_threshold": "5",
                "circuit_breaker_timeout_ms": "60000"
            },
            
            # Environment-Specific Settings
            "environment_config": {
                "rate_limits": {
                    "dev": 1000,
                    "staging": 1500,
                    "prod": 2000
                },
                "key_rotation_days": {
                    "dev": 30,
                    "staging": 60,
                    "prod": 90
                },
                "lambda_memory": {
                    "dev": 512,
                    "staging": 1024,
                    "prod": 1024
                },
                "current_rate_limit": 2000 if environment == 'prod' else (1500 if environment == 'staging' else 1000),
                "current_key_rotation_days": 90 if environment == 'prod' else (60 if environment == 'staging' else 30)
            },
            
            # Lambda Configuration
            "lambda": {
                "runtime": "python3.13",
                "architecture": "x86_64"
            }
        }
        
        # Create or update consolidated parameter
        consolidated_param_name = f"/{resource_prefix}/config/consolidated"
        if consolidated_param_name in existing_params:
            print(f"🔄 Updating existing consolidated SSM parameter: {consolidated_param_name}")
            # Import existing parameter and update it
            consolidated_parameter = ssm.StringParameter.from_string_parameter_name(
                self, f"{resource_prefix}-existing-consolidated-config",
                consolidated_param_name
            )
        else:
            print(f"🆕 Creating new consolidated SSM parameter: {consolidated_param_name}")
            consolidated_parameter = ssm.StringParameter(
                self, f"{resource_prefix}-consolidated-config",
                parameter_name=consolidated_param_name,
                string_value=json.dumps(consolidated_config, indent=2),
                description=f"Consolidated configuration for {resource_prefix} - all settings in one place",
                tier=ssm.ParameterTier.STANDARD
            )
        
        # Create individual parameters only for frequently accessed values
        critical_parameters = [
            # Most frequently accessed by Lambda functions
            (f"/{resource_prefix}/core/environment", environment, "Current deployment environment"),
            (f"/{resource_prefix}/api/endpoint", consolidated_config["api"]["endpoint"], "API Gateway endpoint - frequently accessed"),
            (f"/{resource_prefix}/memorydb/endpoint", memorydb_endpoint, "MemoryDB endpoint - frequently accessed"),
            (f"/{resource_prefix}/memorydb/secret-arn", memorydb_password.secret_arn, "MemoryDB credentials - frequently accessed")
        ]
        
        created_count = 0
        reused_count = 0
        
        for param_name, param_value, description in critical_parameters:
            # Create unique construct ID by including the full path structure
            param_path_parts = param_name.strip('/').split('/')
            # Skip the resource prefix part and join the remaining parts
            unique_suffix = '-'.join(param_path_parts[1:])  # Skip first part (resource_prefix)
            
            if param_name in existing_params:
                print(f"🔄 Reusing existing SSM parameter: {param_name}")
                # Import existing parameter with unique construct ID
                ssm.StringParameter.from_string_parameter_name(
                    self, f"{resource_prefix}-existing-{unique_suffix}-param",
                    param_name
                )
                reused_count += 1
            else:
                print(f"🆕 Creating new SSM parameter: {param_name}")
                ssm.StringParameter(
                    self, f"{resource_prefix}-{unique_suffix}-param",
                    parameter_name=param_name,
                    string_value=str(param_value),
                    description=description,
                    tier=ssm.ParameterTier.STANDARD
                )
                created_count += 1
        
        print(f"✅ SSM Parameters: {reused_count} reused, {created_count} created")
        print(f"   📋 Parameters follow CloudFormation stack lifecycle")
        print(f"   🎯 Consolidated config: {consolidated_param_name}")

    def _check_existing_ssm_parameters(self, resource_prefix: str) -> Dict[str, str]:
        """Check for existing SSM parameters to avoid conflicts"""
        
        existing_params = {}
        
        try:
            if not self.cf_client:
                return existing_params
                
            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
            
            if stack_exists:
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    
                    if resource_type == 'AWS::SSM::Parameter' and resource_prefix in physical_id:
                        existing_params[physical_id] = resource.get('LogicalId', '')
                        print(f"   ✅ Found existing SSM parameter: {physical_id}")
            
        except Exception as e:
            print(f"   ⚠️ Error checking existing SSM parameters: {e}")
        
        return existing_params

    def _enable_ssm_higher_throughput(self, resource_prefix: str):
        """
        Enable SSM Parameter Store higher throughput mode to prevent throttling during cold starts.
        
        This increases SSM throughput from 40 TPS to 1,000+ TPS, which is critical for:
        - Lambda authorizer cold starts that fetch API keys from SSM
        - Preventing 403 authorization errors during load test bursts
        - Supporting hundreds of concurrent Lambda invocations
        """
        
        print(f"\n🚀 Configuring SSM Parameter Store higher throughput")
        
        # Create Lambda function to enable SSM higher throughput
        ssm_config_lambda = lambda_.Function(
            self, f"{resource_prefix}-ssm-config-lambda",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline("""
import boto3
import json
import cfnresponse

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    request_type = event.get('RequestType', 'Create')
    region = event['ResourceProperties']['Region']
    account = event['ResourceProperties']['Account']
    
    ssm_client = boto3.client('ssm', region_name=region)
    
    try:
        if request_type in ['Create', 'Update']:
            # Enable SSM Parameter Store higher throughput
            setting_id = f"arn:aws:ssm:{region}:{account}:servicesetting/ssm/parameter-store/high-throughput-enabled"
            
            print(f"Enabling SSM higher throughput: {setting_id}")
            
            response = ssm_client.update_service_setting(
                SettingId=setting_id,
                SettingValue='true'
            )
            
            print(f"✅ SSM higher throughput enabled successfully")
            print(f"Response: {json.dumps(response, default=str)}")
            
            # Verify the setting
            verify_response = ssm_client.get_service_setting(SettingId=setting_id)
            setting_value = verify_response['ServiceSetting']['SettingValue']
            
            print(f"✅ Verified SSM higher throughput: {setting_value}")
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'SettingValue': setting_value,
                'Message': 'SSM higher throughput enabled successfully'
            })
            
        elif request_type == 'Delete':
            # On delete, we keep higher throughput enabled (no need to disable)
            print(f"Delete request - keeping SSM higher throughput enabled")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'Message': 'SSM higher throughput kept enabled (no action taken)'
            })
            
    except Exception as e:
        error_msg = f"Error configuring SSM higher throughput: {str(e)}"
        print(f"❌ {error_msg}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': error_msg
        })
"""),
            timeout=Duration.seconds(30),
            description="Configure SSM Parameter Store higher throughput mode",
            initial_policy=[
                iam.PolicyStatement(
                    actions=[
                        "ssm:UpdateServiceSetting",
                        "ssm:GetServiceSetting"
                    ],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:servicesetting/ssm/parameter-store/high-throughput-enabled"
                    ],
                    effect=iam.Effect.ALLOW
                )
            ]
        )
        
        # Create custom resource provider
        ssm_config_provider = cr.Provider(
            self, f"{resource_prefix}-ssm-config-provider",
            on_event_handler=ssm_config_lambda,
            log_retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create custom resource to trigger the configuration
        ssm_config_resource = CustomResource(
            self, f"{resource_prefix}-ssm-higher-throughput",
            service_token=ssm_config_provider.service_token,
            properties={
                "Region": self.region,
                "Account": self.account,
                "Timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        
        print(f"✅ SSM higher throughput configuration will be applied during deployment")
        print(f"   This increases SSM throughput from 40 TPS to 1,000+ TPS")
        print(f"   Critical for preventing 403 errors during Lambda cold start bursts")

    def _check_existing_memorydb_resources_in_cfn(self, resource_prefix: str) -> Dict[str, Any]:
        """Check for existing MemoryDB resources in CloudFormation to prevent recreation"""
        
        print(f"🔍 Checking for existing MemoryDB resources in CloudFormation")
        
        results = {
            'cluster_found': False,
            'subnet_group_found': False,
            'parameter_group_found': False,
            'acl_found': False,
            'user_found': False,
            'logical_ids': {},
            'physical_ids': {}
        }
        
        try:
            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
            
            if stack_exists:
                # Look for MemoryDB resources
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    logical_id = resource.get('LogicalId', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    
                    if resource_type == 'AWS::MemoryDB::Cluster' and resource_prefix in physical_id:
                        results['cluster_found'] = True
                        results['logical_ids']['cluster'] = logical_id
                        results['physical_ids']['cluster'] = physical_id
                        print(f"   ✅ Found existing MemoryDB cluster in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::MemoryDB::SubnetGroup' and resource_prefix in physical_id:
                        results['subnet_group_found'] = True
                        results['logical_ids']['subnet_group'] = logical_id
                        results['physical_ids']['subnet_group'] = physical_id
                        print(f"   ✅ Found existing subnet group in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::MemoryDB::ParameterGroup' and resource_prefix in physical_id:
                        results['parameter_group_found'] = True
                        results['logical_ids']['parameter_group'] = logical_id
                        results['physical_ids']['parameter_group'] = physical_id
                        print(f"   ✅ Found existing parameter group in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::MemoryDB::ACL' and resource_prefix in physical_id:
                        results['acl_found'] = True
                        results['logical_ids']['acl'] = logical_id
                        results['physical_ids']['acl'] = physical_id
                        print(f"   ✅ Found existing ACL in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::MemoryDB::User' and 'glide-user' in physical_id:
                        results['user_found'] = True
                        results['logical_ids']['user'] = logical_id
                        results['physical_ids']['user'] = physical_id
                        print(f"   ✅ Found existing user in stack: {physical_id}")
            else:
                print(f"   ℹ️  Stack does not exist - expected for new deployments")
        
        except Exception as e:
            print(f"   ⚠️ Error checking CloudFormation resources: {e}")
        
        return results

    def _handle_api_gateway_creation(self, resource_prefix: str, environment: str, 
                                lambda_functions: Dict[str, lambda_.Function],
                                resource_decisions: Dict[str, Any]) -> apigw.RestApi:
        """Handle API Gateway creation with smart reuse"""
        
        api_decision = resource_decisions.get('api_gateway_decision', 'create_new')
        
        if api_decision == 'reuse':
            # Get existing API details from discovery
            discovery_results = resource_decisions.get('discovery_results', {})
            api_analysis = discovery_results.get('api_gateway_analysis', {})
            
            if api_analysis.get('api_exists'):
                api_details = api_analysis.get('api_details', {})
                existing_api_id = api_details.get('api_id')
                
                if existing_api_id:
                    print(f"🔄 Reusing existing REST API: {existing_api_id}")
                    
                    # Import existing API
                    api = apigw.RestApi.from_rest_api_id(
                        self, f"{resource_prefix}-existing-rest-api",
                        existing_api_id
                    )
                    
                    # Check WAF using the skip function
                    if not self._should_skip_resource_creation('waf', resource_decisions):
                        if not self._check_existing_waf_association(existing_api_id, environment):
                            print(f"🆕 Creating WAF for existing API")
                            self._create_waf_protection(resource_prefix, api)
                        else:
                            print(f"🔄 WAF already associated with existing API")
                    else:
                        print(f"🔄 Skipping WAF creation - reusing existing WAF")
                    
                    return api
        
        # Create new API
        print(f"🆕 Creating new REST API: {resource_prefix}-restapi")
        api = self._create_rest_api(resource_prefix, environment, lambda_functions)
        
        # Create WAF for new API - check if should skip
        if not self._should_skip_resource_creation('waf', resource_decisions):
            self._create_waf_protection(resource_prefix, api)
        else:
            print(f"🔄 Skipping WAF creation for new API - reusing existing WAF")
        
        return api

    def _check_existing_waf_association(self, api_id: str, environment: str) -> bool:
        """Check if WAF is already associated with the API"""
        
        try:
            wafv2_client = boto3.client('wafv2', region_name=self.region)
            
            # Check if there's a WebACL associated with this API stage
            resource_arn = f"arn:aws:apigateway:{self.region}::/restapis/{api_id}/stages/{environment}"
            
            try:
                response = wafv2_client.get_web_acl_for_resource(ResourceArn=resource_arn)
                if response.get('WebACL'):
                    print(f"   ✅ Found existing WAF association for API {api_id}")
                    return True
            except wafv2_client.exceptions.WAFNonexistentItemException:
                print(f"   🔍 No existing WAF association for API {api_id}")
                return False
                
        except Exception as e:
            print(f"   ⚠️ Error checking WAF association: {e}")
            return False
        
        return False

    def _should_skip_resource_creation(self, resource_type: str, resource_decisions: Dict[str, Any]) -> bool:
        """Determine if a resource should be skipped because it's being reused"""
        
        decision_map = {
            'ssm_parameters': 'ssm_decision',
            'api_gateway': 'api_gateway_decision', 
            'waf': 'waf_decision',
            'lambda_functions': 'lambda_decision'
        }
        
        decision_key = decision_map.get(resource_type)
        if not decision_key:
            return False
            
        decision = resource_decisions.get(decision_key, 'create_new')
        return decision == 'reuse'

    def _discover_existing_security_group(self, resource_prefix: str, vpc_id: str) -> Optional[ec2.ISecurityGroup]:
        """Discover existing security group for MemoryDB in the VPC"""
        
        print(f"🔍 Searching for existing MemoryDB security group in VPC {vpc_id}")
        
        try:
            ec2_client = boto3.client('ec2', region_name=self.region)
            
            # Try multiple search patterns for security groups
            search_patterns = [
                f"{resource_prefix}-memorydb-sg",
                f"*{resource_prefix}*memorydb*",
                f"*memorydb*{resource_prefix}*",
                f"*valkey*{resource_prefix}*",
                f"{resource_prefix}*memorydb*"
            ]
            
            for pattern in search_patterns:
                try:
                    # Search by group name pattern
                    response = ec2_client.describe_security_groups(
                        Filters=[
                            {'Name': 'vpc-id', 'Values': [vpc_id]},
                            {'Name': 'group-name', 'Values': [pattern]}
                        ]
                    )
                    
                    if response['SecurityGroups']:
                        sg = response['SecurityGroups'][0]
                        sg_id = sg['GroupId']
                        sg_name = sg['GroupName']
                        
                        print(f"✅ Found existing security group: {sg_name} ({sg_id}) using pattern: {pattern}")
                        
                        # Import the security group
                        imported_sg = ec2.SecurityGroup.from_security_group_id(
                            self, f"{resource_prefix}-existing-memorydb-sg",
                            sg_id,
                            allow_all_outbound=False
                        )
                        
                        return imported_sg
                        
                except Exception as e:
                    if "InvalidGroup.NotFound" not in str(e):
                        print(f"⚠️ Error searching with pattern {pattern}: {e}")
                    continue
            
            # If no exact matches, search by tags
            try:
                response = ec2_client.describe_security_groups(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]},
                        {'Name': 'tag:Service', 'Values': ['game-statsleaderboards']},
                        {'Name': 'tag:Component', 'Values': ['MemoryDB', 'memorydb', 'Valkey', 'valkey']}
                    ]
                )
                
                if response['SecurityGroups']:
                    sg = response['SecurityGroups'][0]
                    sg_id = sg['GroupId']
                    sg_name = sg['GroupName']
                    
                    print(f"✅ Found existing security group by tags: {sg_name} ({sg_id})")
                    
                    # Import the security group
                    imported_sg = ec2.SecurityGroup.from_security_group_id(
                        self, f"{resource_prefix}-existing-memorydb-sg-tagged",
                        sg_id,
                        allow_all_outbound=False
                    )
                    
                    return imported_sg
                    
            except Exception as e:
                print(f"⚠️ Error searching by tags: {e}")
            
            # Final attempt: search all security groups in VPC and check descriptions
            try:
                response = ec2_client.describe_security_groups(
                    Filters=[
                        {'Name': 'vpc-id', 'Values': [vpc_id]}
                    ]
                )
                
                for sg in response['SecurityGroups']:
                    sg_name = sg['GroupName'].lower()
                    sg_desc = sg.get('Description', '').lower()
                    
                    # Check if it's likely a MemoryDB security group
                    memorydb_indicators = ['memorydb', 'valkey', 'redis', 'glide']
                    service_indicators = ['game', 'stats', 'leaderboard']
                    
                    if any(indicator in sg_name or indicator in sg_desc for indicator in memorydb_indicators):
                        if any(indicator in sg_name or indicator in sg_desc for indicator in service_indicators):
                            sg_id = sg['GroupId']
                            print(f"✅ Found likely MemoryDB security group: {sg['GroupName']} ({sg_id})")
                            
                            # Import the security group
                            imported_sg = ec2.SecurityGroup.from_security_group_id(
                                self, f"{resource_prefix}-discovered-memorydb-sg",
                                sg_id,
                                allow_all_outbound=False
                            )
                            
                            return imported_sg
                            
            except Exception as e:
                print(f"⚠️ Error in final search attempt: {e}")
            
            print(f"🔍 No matching security group found in VPC {vpc_id}")
            return None
                    
        except Exception as e:
            print(f"⚠️ Error searching for security groups: {e}")
            return None

    def _check_existing_vpc_resources_in_cfn(self, resource_prefix: str) -> Dict[str, Any]:
        """Check for existing VPC resources in CloudFormation to prevent recreation"""
        
        print(f"🔍 Checking for existing VPC resources in CloudFormation")
        
        results = {
            'vpc_found': False,
            'security_group_found': False,
            'nat_gateway_found': False,
            'internet_gateway_found': False,
            'subnet_found': False,
            'route_table_found': False,
            'vpc_endpoint_found': False,
            'logical_ids': {},
            'physical_ids': {},
            'total_resources_found': 0
        }
        
        try:
            if not self.cf_client:
                print(f"   ⚠️ CloudFormation client not available")
                return results
                
            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
            
            print(f"   🔍 Stack exists: {stack_exists}, Status: {status}")
            print(f"   🔍 Total resources in stack: {len(resources)}")
            
            if stack_exists and resources:
                # Debug: Print all resource types found
                resource_types = {}
                for resource in resources:
                    resource_type = resource.get('ResourceType', 'Unknown')
                    resource_types[resource_type] = resource_types.get(resource_type, 0) + 1
                
                print(f"   📊 Resource types in stack:")
                for res_type, count in sorted(resource_types.items()):
                    print(f"      {res_type}: {count}")
                
                # Look for VPC resources with broader matching
                vpc_resource_types = [
                    'AWS::EC2::VPC',
                    'AWS::EC2::SecurityGroup', 
                    'AWS::EC2::NatGateway',
                    'AWS::EC2::InternetGateway',
                    'AWS::EC2::Subnet',
                    'AWS::EC2::RouteTable',
                    'AWS::EC2::VPCEndpoint',
                    'AWS::EC2::Route',
                    'AWS::EC2::SubnetRouteTableAssociation',
                    'AWS::EC2::VPCGatewayAttachment',
                    'AWS::EC2::EIP'  # For NAT Gateway
                ]
                
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    logical_id = resource.get('LogicalId', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    resource_status = resource.get('ResourceStatus', '')
                    
                    # Check if this is a VPC-related resource
                    if resource_type in vpc_resource_types:
                        results['total_resources_found'] += 1
                        print(f"   🔍 Found {resource_type}: {logical_id} -> {physical_id} ({resource_status})")
                    
                    if resource_type == 'AWS::EC2::VPC':
                        results['vpc_found'] = True
                        results['logical_ids']['vpc'] = logical_id
                        results['physical_ids']['vpc'] = physical_id
                        print(f"   ✅ Found existing VPC in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::SecurityGroup':
                        # Check if it's related to our resource prefix or MemoryDB
                        if (resource_prefix in logical_id.lower() or 
                            'memorydb' in logical_id.lower() or 
                            'valkey' in logical_id.lower() or
                            'glide' in logical_id.lower()):
                            results['security_group_found'] = True
                            if 'security_groups' not in results['logical_ids']:
                                results['logical_ids']['security_groups'] = []
                                results['physical_ids']['security_groups'] = []
                            results['logical_ids']['security_groups'].append(logical_id)
                            results['physical_ids']['security_groups'].append(physical_id)
                            print(f"   ✅ Found existing security group in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::NatGateway':
                        results['nat_gateway_found'] = True
                        if 'nat_gateways' not in results['logical_ids']:
                            results['logical_ids']['nat_gateways'] = []
                            results['physical_ids']['nat_gateways'] = []
                        results['logical_ids']['nat_gateways'].append(logical_id)
                        results['physical_ids']['nat_gateways'].append(physical_id)
                        print(f"   ✅ Found existing NAT Gateway in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::InternetGateway':
                        results['internet_gateway_found'] = True
                        results['logical_ids']['internet_gateway'] = logical_id
                        results['physical_ids']['internet_gateway'] = physical_id
                        print(f"   ✅ Found existing Internet Gateway in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::Subnet':
                        results['subnet_found'] = True
                        if 'subnets' not in results['logical_ids']:
                            results['logical_ids']['subnets'] = []
                            results['physical_ids']['subnets'] = []
                        results['logical_ids']['subnets'].append(logical_id)
                        results['physical_ids']['subnets'].append(physical_id)
                        print(f"   ✅ Found existing subnet in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::RouteTable':
                        results['route_table_found'] = True
                        if 'route_tables' not in results['logical_ids']:
                            results['logical_ids']['route_tables'] = []
                            results['physical_ids']['route_tables'] = []
                        results['logical_ids']['route_tables'].append(logical_id)
                        results['physical_ids']['route_tables'].append(physical_id)
                        print(f"   ✅ Found existing route table in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::EC2::VPCEndpoint':
                        results['vpc_endpoint_found'] = True
                        if 'vpc_endpoints' not in results['logical_ids']:
                            results['logical_ids']['vpc_endpoints'] = []
                            results['physical_ids']['vpc_endpoints'] = []
                        results['logical_ids']['vpc_endpoints'].append(logical_id)
                        results['physical_ids']['vpc_endpoints'].append(physical_id)
                        print(f"   ✅ Found existing VPC endpoint in stack: {physical_id}")
                
                print(f"   📊 VPC Resources Summary:")
                print(f"      Total VPC-related resources: {results['total_resources_found']}")
                print(f"      VPC: {'✅' if results['vpc_found'] else '❌'}")
                print(f"      Security Groups: {'✅' if results['security_group_found'] else '❌'}")
                print(f"      NAT Gateways: {'✅' if results['nat_gateway_found'] else '❌'}")
                print(f"      Internet Gateway: {'✅' if results['internet_gateway_found'] else '❌'}")
                print(f"      Subnets: {'✅' if results['subnet_found'] else '❌'}")
                print(f"      Route Tables: {'✅' if results['route_table_found'] else '❌'}")
                print(f"      VPC Endpoints: {'✅' if results['vpc_endpoint_found'] else '❌'}")
                
            else:
                if not stack_exists:
                    print(f"   ℹ️  Stack does not exist - expected for new deployments")
                else:
                    print(f"   ⚠️  Stack exists but no resources found")
        
        except Exception as e:
            print(f"   ❌ Error checking CloudFormation resources: {e}")
            import traceback
            print(f"   🔍 Full error traceback:")
            traceback.print_exc()
        
        return results

    def _discover_existing_lambda_functions(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing Lambda functions that match our naming pattern"""
        
        print(f"🔍 Searching for existing Lambda functions with prefix: {resource_prefix}")

        results = {
            'functions_found': False,
            'function_count': 0,
            'functions': {}
        }
        
        try:
            lambda_client = boto3.client('lambda', region_name=self.region)
            
            # Use paginator to handle large number of functions
            paginator = lambda_client.get_paginator('list_functions')
            page_iterator = paginator.paginate()
            
            for page in page_iterator:
                for function in page['Functions']:
                    function_name = function['FunctionName']
                    
                    # Check if function name matches our prefix
                    if resource_prefix in function_name:
                        # Extract the function type from the name
                        # Example: game-statsleaderboards-dev-backend-authorizer -> backend-authorizer
                        name_parts = function_name.split(resource_prefix + '-')
                        if len(name_parts) > 1:
                            function_type = name_parts[1]
                            
                            # Store function details
                            results['functions'][function_type] = {
                                'function_name': function_name,
                                'function_arn': function['FunctionArn'],
                                'runtime': function['Runtime'],
                                'handler': function['Handler'],
                                'last_modified': function['LastModified'],
                                'memory_size': function['MemorySize'],
                                'timeout': function['Timeout'],
                                'environment': function.get('Environment', {}).get('Variables', {})
                            }
                            
                            print(f"   ✅ Found existing Lambda function: {function_name}")
                            results['function_count'] += 1
            
            if results['function_count'] > 0:
                results['functions_found'] = True
                print(f"   📊 Found {results['function_count']} Lambda functions matching prefix {resource_prefix}")
            else:
                print(f"   🔍 No Lambda functions found matching prefix {resource_prefix}")
                
        except Exception as e:
            print(f"   ⚠️ Error searching for Lambda functions: {e}")
        
        return results

    def _check_existing_lambda_resources_in_cfn(self, resource_prefix: str) -> Dict[str, Any]:
        """Check for existing Lambda resources in CloudFormation to prevent recreation"""
        
        print(f"🔍 Checking for existing Lambda resources in CloudFormation")
        
        results = {
            'functions_found': False,
            'function_count': 0,
            'application_found': False,
            'resource_group_found': False,
            'functions': {},
            'logical_ids': {},
            'physical_ids': {}
        }
        
        try:
            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
            
            if stack_exists:
                # Look for Lambda resources
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    logical_id = resource.get('LogicalId', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    
                    if resource_type == 'AWS::Lambda::Function' and resource_prefix in physical_id:
                        # Extract function type from physical ID
                        name_parts = physical_id.split(resource_prefix + '-')
                        if len(name_parts) > 1:
                            function_type = name_parts[1]
                            
                            results['functions'][function_type] = {
                                'logical_id': logical_id,
                                'physical_id': physical_id
                            }
                            
                            if 'lambda_functions' not in results['logical_ids']:
                                results['logical_ids']['lambda_functions'] = []
                                results['physical_ids']['lambda_functions'] = []
                            
                            results['logical_ids']['lambda_functions'].append(logical_id)
                            results['physical_ids']['lambda_functions'].append(physical_id)
                            results['function_count'] += 1
                            print(f"   ✅ Found existing Lambda function in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApplicationInsights::Application' and resource_prefix in logical_id:
                        results['application_found'] = True
                        results['logical_ids']['application'] = logical_id
                        results['physical_ids']['application'] = physical_id
                        print(f"   ✅ Found existing Lambda Application in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ResourceGroups::Group' and resource_prefix in logical_id:
                        results['resource_group_found'] = True
                        results['logical_ids']['resource_group'] = logical_id
                        results['physical_ids']['resource_group'] = physical_id
                        print(f"   ✅ Found existing Resource Group in stack: {physical_id}")
                
                if results['function_count'] > 0:
                    results['functions_found'] = True
            else:
                print(f"   ℹ️  Stack does not exist - expected for new deployments")
        
        except Exception as e:
            print(f"   ⚠️ Error checking CloudFormation resources: {e}")
        
        return results

    def _discover_existing_api_gateway(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover existing API Gateway REST API resources"""
        
        print(f"🔍 Searching for existing REST API resources with prefix: {resource_prefix}")
        
        results = {
            'api_found': False,
            'api_id': None,
            'api_name': None,
            'stages': [],
            'resources': [],
            'deployments': []
        }
        
        try:
            # Use the correct client for REST API (not v2)
            apigw_client = boto3.client('apigateway', region_name=self.region)
            
            # List all REST APIs
            response = apigw_client.get_rest_apis()
            
            for api in response.get('items', []):
                api_name = api.get('name', '')
                
                # Check if API name matches our prefix
                if resource_prefix in api_name or 'game-statsleaderboards' in api_name:
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
                                'stage_variables': stage.get('variables', {}),
                                'throttle_settings': stage.get('throttleSettings', {})
                            })
                    except Exception as e:
                        print(f"      ⚠️ Error getting stages: {e}")
                    
                    # Get resources
                    try:
                        resources_response = apigw_client.get_resources(restApiId=api_id)
                        resources = resources_response.get('items', [])
                        
                        for resource in resources:
                            path_part = resource.get('pathPart', '')
                            resource_path = resource.get('path', '')
                            print(f"      - Resource: {resource_path}")
                            results['resources'].append({
                                'id': resource.get('id', ''),
                                'path': resource_path,
                                'path_part': path_part,
                                'resource_methods': resource.get('resourceMethods', {})
                            })
                    except Exception as e:
                        print(f"      ⚠️ Error getting resources: {e}")
                    
                    # Get deployments
                    try:
                        deployments_response = apigw_client.get_deployments(restApiId=api_id)
                        deployments = deployments_response.get('items', [])
                        
                        for deployment in deployments:
                            deployment_id = deployment.get('id', '')
                            created_date = deployment.get('createdDate', '')
                            print(f"      - Deployment: {deployment_id} ({created_date})")
                            results['deployments'].append({
                                'id': deployment_id,
                                'created_date': created_date,
                                'description': deployment.get('description', '')
                            })
                    except Exception as e:
                        print(f"      ⚠️ Error getting deployments: {e}")
                    
                    # Store API details
                    results['api_found'] = True
                    results['api_id'] = api_id
                    results['api_name'] = api_name
                    
                    # Only process the first matching API
                    break
            
            if not results['api_found']:
                print(f"   🔍 No REST API found matching prefix {resource_prefix}")
                
        except Exception as e:
            print(f"   ⚠️ Error searching for REST API: {e}")
        
        return results

    def _check_existing_api_gateway_in_cfn(self, resource_prefix: str) -> Dict[str, Any]:
        """Check for existing REST API resources in CloudFormation"""
        
        print(f"🔍 Checking for existing REST API resources in CloudFormation")
        
        results = {
            'api_found': False,
            'stage_found': False,
            'deployment_found': False,
            'authorizer_found': False,
            'method_found': False,
            'resource_found': False,
            'logical_ids': {},
            'physical_ids': {},
            'total_resources_found': 0
        }
        
        try:
            if not self.cf_client:
                print(f"   ⚠️ CloudFormation client not available")
                return results
                
            cf_helper = CloudFormationHelper()
            stack_exists, status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
            
            if stack_exists and resources:
                # Look for REST API resources
                rest_api_resource_types = [
                    'AWS::ApiGateway::RestApi',
                    'AWS::ApiGateway::Stage',
                    'AWS::ApiGateway::Deployment',
                    'AWS::ApiGateway::Resource',
                    'AWS::ApiGateway::Method',
                    'AWS::ApiGateway::Authorizer',
                    'AWS::ApiGateway::RequestValidator',
                    'AWS::ApiGateway::Model',
                    'AWS::ApiGateway::GatewayResponse',
                    'AWS::ApiGateway::UsagePlan',
                    'AWS::ApiGateway::ApiKey'
                ]
                
                for resource in resources:
                    resource_type = resource.get('ResourceType', '')
                    logical_id = resource.get('LogicalId', '')
                    physical_id = resource.get('PhysicalResourceId', '')
                    resource_status = resource.get('ResourceStatus', '')
                    
                    # Check if this is a REST API resource
                    if resource_type in rest_api_resource_types:
                        results['total_resources_found'] += 1
                        print(f"   🔍 Found {resource_type}: {logical_id} -> {physical_id} ({resource_status})")
                    
                    if resource_type == 'AWS::ApiGateway::RestApi':
                        if (resource_prefix in logical_id.lower() or 
                            'game' in logical_id.lower() or
                            'stats' in logical_id.lower()):
                            results['api_found'] = True
                            results['logical_ids']['api'] = logical_id
                            results['physical_ids']['api'] = physical_id
                            print(f"   ✅ Found existing REST API in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApiGateway::Stage':
                        if resource_prefix in logical_id.lower():
                            results['stage_found'] = True
                            if 'stages' not in results['logical_ids']:
                                results['logical_ids']['stages'] = []
                                results['physical_ids']['stages'] = []
                            results['logical_ids']['stages'].append(logical_id)
                            results['physical_ids']['stages'].append(physical_id)
                            print(f"   ✅ Found existing REST API Stage in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApiGateway::Deployment':
                        if resource_prefix in logical_id.lower():
                            results['deployment_found'] = True
                            if 'deployments' not in results['logical_ids']:
                                results['logical_ids']['deployments'] = []
                                results['physical_ids']['deployments'] = []
                            results['logical_ids']['deployments'].append(logical_id)
                            results['physical_ids']['deployments'].append(physical_id)
                            print(f"   ✅ Found existing REST API Deployment in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApiGateway::Authorizer':
                        if resource_prefix in logical_id.lower():
                            results['authorizer_found'] = True
                            if 'authorizers' not in results['logical_ids']:
                                results['logical_ids']['authorizers'] = []
                                results['physical_ids']['authorizers'] = []
                            results['logical_ids']['authorizers'].append(logical_id)
                            results['physical_ids']['authorizers'].append(physical_id)
                            print(f"   ✅ Found existing REST API Authorizer in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApiGateway::Method':
                        if resource_prefix in logical_id.lower():
                            results['method_found'] = True
                            if 'methods' not in results['logical_ids']:
                                results['logical_ids']['methods'] = []
                                results['physical_ids']['methods'] = []
                            results['logical_ids']['methods'].append(logical_id)
                            results['physical_ids']['methods'].append(physical_id)
                            print(f"   ✅ Found existing REST API Method in stack: {physical_id}")
                    
                    elif resource_type == 'AWS::ApiGateway::Resource':
                        if resource_prefix in logical_id.lower():
                            results['resource_found'] = True
                            if 'api_resources' not in results['logical_ids']:
                                results['logical_ids']['api_resources'] = []
                                results['physical_ids']['api_resources'] = []
                            results['logical_ids']['api_resources'].append(logical_id)
                            results['physical_ids']['api_resources'].append(physical_id)
                            print(f"   ✅ Found existing REST API Resource in stack: {physical_id}")
                
                print(f"   📊 REST API Resources Summary:")
                print(f"      Total REST API resources: {results['total_resources_found']}")
                print(f"      REST API: {'✅' if results['api_found'] else '❌'}")
                print(f"      Stages: {'✅' if results['stage_found'] else '❌'}")
                print(f"      Deployments: {'✅' if results['deployment_found'] else '❌'}")
                print(f"      Authorizers: {'✅' if results['authorizer_found'] else '❌'}")
                print(f"      Methods: {'✅' if results['method_found'] else '❌'}")
                print(f"      Resources: {'✅' if results['resource_found'] else '❌'}")
                    
            else:
                if not stack_exists:
                    print(f"   ℹ️  Stack does not exist - expected for new deployments")
                else:
                    print(f"   ⚠️  Stack exists but no resources found")
        
        except Exception as e:
            print(f"   ❌ Error checking CloudFormation resources: {e}")
        
        return results

    def _debug_stack_info(self):
        """Debug method to print stack information with proper error handling"""
        print(f"🔍 Stack Debug Information:")
        print(f"   Stack Name: {self.stack_name}")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account}")
        print(f"   CF Client Available: {self.cf_client is not None}")

        if self.cf_client:
            try:
                # Use CloudFormationHelper consistent with the rest of the codebase
                cf_helper = CloudFormationHelper()
                stack_exists, status, stack_details = cf_helper.check_stack_exists(self.stack_name, self.cf_client)
                
                if stack_exists:
                    print(f"   Stack Status: {status}")
                    print(f"   Creation Time: {stack_details.get('CreationTime', 'Unknown')}")
                    print(f"   Stack ID: {stack_details.get('StackId', 'Unknown')}")
                    print(f"   Description: {stack_details.get('Description', 'No description')}")
                    
                    # Get stack resources using the helper with caching
                    resources_exist, resources_status, resources = cf_helper.get_stack_resources(self.stack_name, self.cf_client)
                    if resources_exist:
                        print(f"   Total Resources: {len(resources)}")
                        
                        # Count resource types for debugging
                        resource_types = {}
                        for resource in resources:
                            res_type = resource.get('ResourceType', 'Unknown')
                            resource_types[res_type] = resource_types.get(res_type, 0) + 1
                        
                        if resource_types:
                            print(f"   Resource Types Summary:")
                            for res_type, count in sorted(resource_types.items()):
                                print(f"      {res_type}: {count}")
                    else:
                        print(f"   Resources Status: {resources_status}")
                else:
                    print(f"   ✅ Stack does not exist - expected for new deployments")
                    print(f"   Status: {status}")
                    
            except Exception as e:
                # Use the error handler from CloudFormationHelper
                error_info = CloudFormationHelper.handle_stack_operation_error(
                    "debug_stack_info", self.stack_name, e
                )
                
                if error_info['error_type'] == 'EXPECTED_NEW_DEPLOYMENT':
                    print(f"   ✅ {error_info['message']}")
                else:
                    print(f"   ⚠️ {error_info['message']}")
        else:
            print(f"   ⚠️ CloudFormation client not initialized - running in offline mode")

    # An alternative approach to discovering VPC resources
    def _discover_vpc_resources_directly(self, resource_prefix: str) -> Dict[str, Any]:
        """Discover VPC resources directly via AWS APIs as fallback"""
        
        print(f"🔍 Direct VPC resource discovery (fallback)")
        
        results = {
            'vpcs_found': [],
            'security_groups_found': [],
            'subnets_found': [],
            'route_tables_found': [],
            'internet_gateways_found': [],
            'nat_gateways_found': []
        }
        
        try:
            ec2_client = boto3.client('ec2', region_name=self.region)
            
            # Find VPCs with our tags
            vpcs_response = ec2_client.describe_vpcs(
                Filters=[
                    {'Name': 'tag:Service', 'Values': ['game-statsleaderboards']},
                    {'Name': 'state', 'Values': ['available']}
                ]
            )
            
            for vpc in vpcs_response['Vpcs']:
                vpc_id = vpc['VpcId']
                results['vpcs_found'].append(vpc_id)
                print(f"   ✅ Found VPC directly: {vpc_id}")
                
                # Find associated resources
                # Security Groups
                sg_response = ec2_client.describe_security_groups(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )
                for sg in sg_response['SecurityGroups']:
                    results['security_groups_found'].append(sg['GroupId'])
                    print(f"   ✅ Found Security Group: {sg['GroupId']}")
                
                # Subnets
                subnet_response = ec2_client.describe_subnets(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )
                for subnet in subnet_response['Subnets']:
                    results['subnets_found'].append(subnet['SubnetId'])
                    print(f"   ✅ Found Subnet: {subnet['SubnetId']}")
            
            print(f"   📊 Direct Discovery Summary:")
            print(f"      VPCs: {len(results['vpcs_found'])}")
            print(f"      Security Groups: {len(results['security_groups_found'])}")
            print(f"      Subnets: {len(results['subnets_found'])}")
            
        except Exception as e:
            print(f"   ❌ Error in direct VPC discovery: {e}")
        
        return results

    def _create_basic_monitoring(
        self, resource_prefix: str, api: apigw.RestApi, memorydb_cluster
    ):
        """Create basic monitoring dashboard for REST API"""
        
        cluster_name = getattr(memorydb_cluster, 'cluster_name', f"{resource_prefix}-cluster")
        
        dashboard = cloudwatch.Dashboard(
            self, f"{resource_prefix}-basic-dashboard",
            dashboard_name=f"{resource_prefix}-basic-metrics",
            widgets=[
                [
                    cloudwatch.GraphWidget(
                        title="API Gateway Requests",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway",
                                metric_name="Count",
                                dimensions_map={"ApiName": api.rest_api_name},
                                statistic="Sum"
                            )
                        ],
                        width=12,
                        height=6
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="API Gateway Latency",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/ApiGateway",
                                metric_name="Latency",
                                dimensions_map={"ApiName": api.rest_api_name},
                                statistic="Average"
                            )
                        ],
                        width=12,
                        height=6
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="MemoryDB CPU Utilization",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/MemoryDB",
                                metric_name="CPUUtilization",
                                dimensions_map={"ClusterName": cluster_name},
                                statistic="Average"
                            )
                        ],
                        width=12,
                        height=6
                    )
                ]
            ]
        )

    def _create_comprehensive_outputs(
        self, api: apigw.RestApi, memorydb_cluster, config_table: dynamodb.TableV2, 
        stats_table: dynamodb.TableV2, shared_layer: lambda_.LayerVersion, 
        lambda_roles: Dict[str, iam.Role], vpc: ec2.Vpc, memorydb_password: secretsmanager.Secret, 
        vpc_reused: bool, memorydb_reused: bool, tables_reused: Dict[str, bool], 
        developer_registration: CustomResource, lambda_application: appinsights.CfnApplication = None,
        resource_group: resourcegroups.CfnGroup = None, environment: str = "dev", 
        comprehensive_resource_group: resourcegroups.CfnGroup = None
    ):
        """Create comprehensive outputs with enhanced error handling for developer registration"""
        
        # Get MemoryDB endpoint
        if hasattr(memorydb_cluster, 'attr_cluster_endpoint_address'):
            memorydb_endpoint = memorydb_cluster.attr_cluster_endpoint_address
        else:
            memorydb_endpoint = getattr(memorydb_cluster, 'attr_cluster_endpoint_address', 'localhost')
        
        # Core REST API outputs
        CfnOutput(
            self, "ApiEndpoint",
            value=f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/{environment}/",
            description="REST API endpoint URL optimized with current environment stage",
            export_name=f"{self.stack_name}-ApiEndpoint"
        )

        CfnOutput(
            self, "ApiEndpointRoot",
            value=f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/",
            description="REST API root endpoint URL",
            export_name=f"{self.stack_name}-ApiEndpointRoot"
        )

        CfnOutput(
            self, "ApiId",
            value=api.rest_api_id,
            description="REST API ID",
            export_name=f"{self.stack_name}-ApiId"
        )

        CfnOutput(
            self, "ApiStage",
            value=environment,
            description="Current API stage name",
            export_name=f"{self.stack_name}-ApiStage"
        )

        # Lambda Application outputs
        if lambda_application:
            CfnOutput(
                self, "LambdaApplicationName",
                value=lambda_application.resource_group_name,
                description="Lambda Application name for function grouping",
                export_name=f"{self.stack_name}-LambdaApplicationName"
            )

        # Resource Group outputs (if created)
        if resource_group:
            CfnOutput(
                self, "ResourceGroupName",
                value=resource_group.name,
                description="Resource Group name for Lambda functions",
                export_name=f"{self.stack_name}-ResourceGroupName"
            )

        CfnOutput(
            self, "SharedLayerArn",
            value=shared_layer.layer_version_arn,
            description="Shared Lambda Layer ARN with Valkey-GLIDE dependencies",
            export_name=f"{self.stack_name}-SharedLayerArn"
        )
        
        CfnOutput(
            self, "MemoryDBEndpoint",
            value=memorydb_endpoint,
            description="MemoryDB Valkey cluster endpoint for GLIDE client",
            export_name=f"{self.stack_name}-MemoryDBEndpoint"
        )
        
        # DynamoDB Tables
        CfnOutput(
            self, "ConfigTableName",
            value=config_table.table_name,
            description="DynamoDB configuration table name",
            export_name=f"{self.stack_name}-ConfigTable"
        )
        
        CfnOutput(
            self, "StatsTableName",
            value=stats_table.table_name,
            description="DynamoDB stats table name",
            export_name=f"{self.stack_name}-StatsTable"
        )
        
        CfnOutput(
            self, "LambdaRoleArn", 
            value=",".join(r.role_arn for r in lambda_roles.values()),
            description="Lambda execution role ARN",
            export_name=f"{self.stack_name}-LambdaRoleArn"
        )
        
        CfnOutput(
            self, "VpcId",
            value=vpc.vpc_id,
            description="VPC ID for Lambda functions",
            export_name=f"{self.stack_name}-VpcId"
        )
        
        CfnOutput(
            self, "MemoryDBSecretArn",
            value=memorydb_password.secret_arn,
            description="MemoryDB password secret ARN",
            export_name=f"{self.stack_name}-MemoryDBSecretArn"
        )
        
        # Infrastructure reuse status
        CfnOutput(
            self, "VpcReused",
            value="true" if vpc_reused else "false",
            description="Whether existing VPC was reused",
            export_name=f"{self.stack_name}-VpcReused"
        )
        
        CfnOutput(
            self, "MemoryDBReused",
            value="true" if memorydb_reused else "false",
            description="Whether existing MemoryDB cluster was reused",
            export_name=f"{self.stack_name}-MemoryDBReused"
        )
        
        # Updated tables reused to exclude developer table
        filtered_tables_reused = {k: v for k, v in tables_reused.items() if k != 'developer'}
        CfnOutput(
            self, "TablesReused",
            value=json.dumps(filtered_tables_reused),
            description="Which DynamoDB tables were reused (config, stats only)",
            export_name=f"{self.stack_name}-TablesReused"
        )
        
        CfnOutput(
            self, "StackName",
            value=self.stack_name,
            description="CloudFormation stack name",
            export_name=f"{self.stack_name}-StackName"
        )
        
        CfnOutput(
            self, "Region",
            value=self.region,
            description="AWS region where stack is deployed",
            export_name=f"{self.stack_name}-Region"
        )
        
        CfnOutput(
            self, "GLIDEVersion",
            value="2.0.1",
            description="Valkey GLIDE client version used",
            export_name=f"{self.stack_name}-GLIDEVersion"
        )
        
        CfnOutput(
            self, "SSMParameterPrefix",
            value=f"/{self.stack_name.lower()}/",
            description="SSM parameter prefix for configuration and API key management",
            export_name=f"{self.stack_name}-SSMPrefix"
        )

        CfnOutput(
            self, "ComprehensiveResourceGroupName",
            value=comprehensive_resource_group.name,
            description="Resource Group containing ALL stack resources",
            export_name=f"{self.stack_name}-ComprehensiveResourceGroup"
        )

        # Developer Registration Outputs with PROPER CloudFormation Reference handling
        print(f"🔍 DEBUG: Creating developer registration outputs...")
        print(f"🔍 DEBUG: developer_registration object: {developer_registration}")
        print(f"🔍 DEBUG: developer_registration type: {type(developer_registration)}")
        
        try:
            # Validate that the custom resource exists and has the expected structure
            if developer_registration is None:
                raise Exception("Developer registration custom resource is None")
            
            # Check if this is a real CustomResource or a mock
            if hasattr(developer_registration, '__class__') and 'Mock' in developer_registration.__class__.__name__:
                raise Exception("Developer registration is a mock object - real registration failed")
            
            if not hasattr(developer_registration, 'get_att'):
                raise Exception("Developer registration custom resource does not have get_att method")
            
            print(f"🔍 DEBUG: Attempting to get attributes from custom resource...")
            
            # Get attribute references - these are CloudFormation references
            api_key_ref = developer_registration.get_att("ApiKey")
            studio_id_ref = developer_registration.get_att("StudioId")
            game_id_ref = developer_registration.get_att("GameId")
            
            print(f"🔍 DEBUG: Successfully got attribute references")
            print(f"🔍 DEBUG: api_key_ref type: {type(api_key_ref)}")
            print(f"🔍 DEBUG: studio_id_ref type: {type(studio_id_ref)}")
            print(f"🔍 DEBUG: game_id_ref type: {type(game_id_ref)}")
            
            # Create the outputs using proper CloudFormation reference handling
            CfnOutput(
                self, "StudioAPIKey",
                value=Token.as_string(api_key_ref),
                description="Your Studio API Key - use this for all API requests",
                export_name=f"{self.stack_name}-StudioAPIKey"
            )

            CfnOutput(
                self, "StudioId",
                value=Token.as_string(studio_id_ref),
                description="Your Studio ID",
                export_name=f"{self.stack_name}-StudioId"
            )

            CfnOutput(
                self, "GameId",
                value=Token.as_string(game_id_ref),
                description="Your Game ID",
                export_name=f"{self.stack_name}-GameId"
            )

            # Create a comprehensive quick start guide using proper string concatenation
            api_endpoint_url = f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/{environment}/"

            # Use Fn.join for proper CloudFormation string concatenation
            CfnOutput(
                self, "QuickStartGuide",
                value=Fn.join("", [
                    "Your studio is ready! Use API Key: ",
                    Token.as_string(api_key_ref),
                    " with Studio ID: ",
                    Token.as_string(studio_id_ref),
                    " and Game ID: ",
                    Token.as_string(game_id_ref),
                    " at endpoint: ",
                    api_endpoint_url
                ]),
                description="Quick start information for using your API with all required credentials",
                export_name=f"{self.stack_name}-QuickStartGuide"
            )

            print(f"✅ DEBUG: Successfully created all developer registration outputs")
            
        except Exception as e:
            print(f"❌ ERROR: Could not create developer registration outputs: {e}")
            print(f"🔍 DEBUG: Exception type: {type(e)}")
            print(f"🔍 DEBUG: Exception details: {str(e)}")
            
            # Instead of creating fallback outputs, FAIL THE DEPLOYMENT
            # This forces the issue to be fixed rather than hidden
            raise Exception(f"Developer registration failed and is required for stack deployment: {e}")

        CfnOutput(
            self, "DeploymentTimestamp",
            value=datetime.now(timezone.utc).isoformat(),
            description="Timestamp when stack was deployed",
            export_name=f"{self.stack_name}-DeploymentTimestamp"
        )

    def _create_comprehensive_resource_group(self, resource_prefix: str, environment: str) -> resourcegroups.CfnGroup:
        """Create comprehensive resource group for ALL stack resources"""
        
        print(f"🆕 Creating comprehensive resource group for all stack resources")
        
        # Create resource group that captures ALL resources created by this stack
        comprehensive_resource_group = resourcegroups.CfnGroup(
            self, f"{resource_prefix}-all-resources-group",
            name=f"{resource_prefix}-all-resources",
            description=f"All resources created by {resource_prefix} stack for complete resource management",
            resource_query=resourcegroups.CfnGroup.ResourceQueryProperty(
                type="CLOUDFORMATION_STACK_1_0",                    # replaced from - type="TAG_FILTERS_1_0",
                query=resourcegroups.CfnGroup.QueryProperty(
                    resource_type_filters=["AWS::AllSupported"],    # This captures ALL AWS resource types
                    stack_identifier=self.stack_id,
                                                                    # tag_filters=[
                                                                    #     resourcegroups.CfnGroup.TagFilterProperty(
                                                                    #         key="aws:cloudformation:stack-name",
                                                                    #         values=[self.stack_name]
                                                                    #     )
                                                                    # ]
                )
            ),
            tags=[
                CfnTag(key="Environment", value=environment),
                CfnTag(key="Service", value="game-statsleaderboards"),
                CfnTag(key="ManagedBy", value="CDK"),
                CfnTag(key="Application", value="GameStatsLeaderboards"),
                CfnTag(key="ResourceGroupType", value="Comprehensive"),
                CfnTag(key="StackName", value=self.stack_name)
            ]
        )
        
        # Also create a resource group by service tags as backup
        service_resource_group = resourcegroups.CfnGroup(
            self, f"{resource_prefix}-service-resources-group", 
            name=f"{resource_prefix}-service-resources",
            description=f"All {resource_prefix} service resources grouped by service tags",
            resource_query=resourcegroups.CfnGroup.ResourceQueryProperty(
                type="TAG_FILTERS_1_0",
                query=resourcegroups.CfnGroup.QueryProperty(
                    resource_type_filters=["AWS::AllSupported"],
                    tag_filters=[
                        resourcegroups.CfnGroup.TagFilterProperty(
                            key="Service",
                            values=["game-statsleaderboards"]
                        ),
                        resourcegroups.CfnGroup.TagFilterProperty(
                            key="Environment",
                            values=[environment]
                        )
                    ]
                )
            ),
            tags=[
                CfnTag(key="Environment", value=environment),
                CfnTag(key="Service", value="game-statsleaderboards"),
                CfnTag(key="ManagedBy", value="CDK"),
                CfnTag(key="Application", value="GameStatsLeaderboards"),
                CfnTag(key="ResourceGroupType", value="ServiceBased")
            ]
        )
        
        return comprehensive_resource_group

    def _force_delete_conflicting_resources(self, resource_prefix: str):
        """Force delete conflicting resources that prevent deployment"""
        
        print(f"🗑️ Checking for conflicting resources to force delete...")
        
        try:
            # Check and delete conflicting Secrets Manager secrets
            secretsmanager_client = boto3.client('secretsmanager', region_name=self.region)
            
            secret_names_to_check = [
                f"{resource_prefix}-memorydb-password"
            ]
            
            for secret_name in secret_names_to_check:
                try:
                    # Check if secret exists
                    secretsmanager_client.describe_secret(SecretId=secret_name)
                    
                    print(f"⚠️ Found conflicting secret: {secret_name}")
                    
                    # Force delete without recovery period
                    secretsmanager_client.delete_secret(
                        SecretId=secret_name,
                        ForceDeleteWithoutRecovery=True
                    )
                    
                    print(f"🗑️ Force deleted secret: {secret_name}")
                    
                except secretsmanager_client.exceptions.ResourceNotFoundException:
                    # Secret doesn't exist, which is what we want
                    pass
                except Exception as e:
                    print(f"⚠️ Error handling secret {secret_name}: {e}")
            
        except Exception as e:
            print(f"⚠️ Error in force delete operation: {e}")

    def _print_deployment_summary(self, vpc_reused: bool, memorydb_reused: bool, tables_reused: Dict[str, bool], lambda_application_created: bool):
        """Print deployment summary with REST API info"""
        
        # Get actual resource decisions
        resource_decisions = getattr(self, 'resource_decisions', {})
        
        print("\n" + "="*80)
        print("🎉 DEPLOYMENT SUMMARY")
        print("="*80)
        print(f"VPC: {'🔄 Reused existing' if vpc_reused else '🆕 Created new'}")
        print(f"MemoryDB for Valkey: {'🔄 Reused existing' if memorydb_reused else '🆕 Created new'}")
        
        for table_type, reused in tables_reused.items():
            status = '🔄 Reused existing' if reused else '🆕 Created new'
            print(f"DynamoDB {table_type.title()} table: {status}")
        
        # Use actual decisions for Lambda functions
        lambda_decision = resource_decisions.get('lambda_decision', 'create_new')
        lambda_functions_status = '🔄 Reused existing (10 functions)' if lambda_decision == 'reuse' else '🆕 Created new (10 functions)'
        print(f"Lambda Functions: {lambda_functions_status}")
        
        # Use actual decisions for Lambda Application
        lambda_app_decision = resource_decisions.get('lambda_application_decision', 'create_new')
        if lambda_app_decision == 'reuse':
            print(f"Lambda Application: 🔄 Reused existing (Application Insights enabled)")
        else:
            print(f"Lambda Application: 🆕 Created new (Application Insights enabled)")
        
        # REST API is always new in this migration
        print(f"REST API: 🆕 Created new with multi-stage deployment (dev, staging, prod)")
        print(f"WAF Protection: 🆕 Created new (REST API compatible)")
        print("="*80)
        
        # Check API Gateway service quotas and provide recommendations
        self._check_api_gateway_quotas()

    def _check_api_gateway_quotas(self):
        """
        Check API Gateway account-level service quotas and provide recommendations.
        
        Based on load testing with ~4,400 concurrent players across 46 instances:
        - Each concurrent player generates ~1.3 API requests/sec
        - 1,000 concurrent players ≈ 1,300 sustained RPS
        
        Minimum recommended quotas (benchmarked for 1,000 concurrent players):
        - Rate limit:  >= 1,500 req/sec (1,300 RPS + ~15% headroom)
        - Burst limit: >= 2,500 requests (handles matchmaking waves, event starts, reconnects)
        
        These are MINIMUM values for the Stats & Leaderboards system alone.
        The customer's account may serve other APIs that share these account-level quotas.
        """
        MIN_RATE = 1500
        MIN_BURST = 2500
        BASELINE_PLAYERS = 1000
        
        try:
            apigw_client = boto3.client('apigateway', region_name=self.region)
            account_info = apigw_client.get_account()
            throttle = account_info.get('throttleSettings', {})
            
            acct_rate = throttle.get('rateLimit', 0)
            acct_burst = throttle.get('burstLimit', 0)
            
            print("")
            print("📊 API Gateway Service Quota Check")
            print("-" * 60)
            print(f"   Account Rate Limit:          {acct_rate:,.0f} req/sec")
            print(f"   Account Burst Limit:         {acct_burst:,} requests")
            print(f"   Minimum Required Rate:       {MIN_RATE:,} req/sec")
            print(f"   Minimum Required Burst:      {MIN_BURST:,} requests")
            print(f"   Baseline:                    {BASELINE_PLAYERS:,} concurrent players")
            
            warnings = []
            if acct_rate < MIN_RATE:
                warnings.append(
                    f"   ⚠️  Account rate limit ({acct_rate:,.0f} req/sec) is below the "
                    f"minimum required ({MIN_RATE:,} req/sec) for {BASELINE_PLAYERS:,} concurrent players."
                )
            if acct_burst < MIN_BURST:
                warnings.append(
                    f"   ⚠️  Account burst limit ({acct_burst:,} requests) is below the "
                    f"minimum required ({MIN_BURST:,} requests) for handling player spike patterns."
                )
            
            if warnings:
                print("")
                for w in warnings:
                    print(w)
                print("")
                print("   ⛔ LIMITATION: The Stats & Leaderboards system has been load-tested")
                print(f"   and benchmarked for a minimum of {BASELINE_PLAYERS:,} concurrent players,")
                print(f"   which requires at least {MIN_RATE:,} req/sec rate and {MIN_BURST:,} burst.")
                print("   Your current account quotas do not meet these minimums.")
                print("")
                print("   💡 To request a quota increase:")
                print("      1. Open the AWS Service Quotas console → Amazon API Gateway")
                print("      2. Request increase for 'Throttle rate' (minimum: {0:,} req/sec)".format(MIN_RATE))
                print("      3. Request increase for 'Throttle burst rate' (minimum: {0:,} requests)".format(MIN_BURST))
                print("      Note: These quotas are shared across ALL APIs in the account.")
                print("      For higher player counts, scale proportionally (~1.3 RPS per player).")
                print("")
                print("   ℹ️  Our load test used an aggressive request pattern (~1.3 RPS/player).")
                print("      Your actual usage may be lower depending on how your game integrates")
                print("      with the Stats & Leaderboards system (e.g., score submission frequency,")
                print("      leaderboard query patterns, batch vs. individual calls).")
                print("      Requesting a higher quota does not incur any cost — you only pay for")
                print("      what you actually consume.")
            else:
                print("")
                print(f"   ✅ Account quotas meet the minimum requirements for")
                print(f"      {BASELINE_PLAYERS:,} concurrent players ({MIN_RATE:,} rate / {MIN_BURST:,} burst).")
                print(f"      Benchmarked at ~1.3 RPS per concurrent player.")
                print(f"      Your actual usage may differ based on your game's integration pattern.")
                print(f"      Requesting additional quota is free — you only pay for what you consume.")
            
            print("-" * 60)
            
        except Exception as e:
            print(f"\n   ℹ️  Could not check API Gateway quotas: {e}")
            print(f"   ℹ️  The Stats & Leaderboards system has been benchmarked for")
            print(f"       {BASELINE_PLAYERS:,} concurrent players, requiring minimum quotas of:")
            print(f"       - Rate:  {MIN_RATE:,} req/sec")
            print(f"       - Burst: {MIN_BURST:,} requests")
            print(f"   ℹ️  Your actual usage may differ based on your game's integration pattern.")
            print(f"       Requesting additional quota is free — you only pay for what you consume.")
            print(f"   ℹ️  Verify your account limits via AWS Service Quotas console.")

# CDK App
app = App()

# Environment-specific configuration
environments = {
    "dev": {
        "memorydb_node_type": "db.r6g.large",
        "memorydb_shards": 1,
        "memorydb_replicas": 1,
        "lambda_memory": 512  # NOTE: Not currently used - Lambda memory is hardcoded in function_configs
    },
    "staging": {
        "memorydb_node_type": "db.r7g.large",
        "memorydb_shards": 1,
        "memorydb_replicas": 2,
        "lambda_memory": 1024  # NOTE: Not currently used - Lambda memory is hardcoded in function_configs
    },
    "prod": {
        "memorydb_node_type": "db.r7g.xlarge",
        "memorydb_shards": 2,
        "memorydb_replicas": 2,
        "lambda_memory": 1024  # NOTE: Not currently used - Lambda memory is hardcoded in function_configs
    }
}

# Set context for environments
app.node.set_context("environments", environments)

GameStatsLeaderboardsStack(
    app, "GameStatsLeaderboardsStack",
    env={
        "account": app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        "region": app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or "us-west-2"
    },
    description="AWS Custom Game Backend Framework - Stats & Leaderboards Component (Core Infra)"
)

app.synth()