#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
set -e

# =============================================================================
# Resource Check & Cleanup Script for Game Stats & Leaderboards System
# =============================================================================
#
# Why this script exists:
#   When iterating on CDK deployments during development (deploy, tear down,
#   modify, redeploy), CloudFormation sometimes fails to fully clean up all
#   resources -- orphaned MemoryDB clusters, VPC ENIs, security groups, KMS
#   keys, and SSM parameters can linger and block subsequent deployments.
#   This script finds those remnants so you can clean them up.
#
# Two modes of operation:
#
#   "check" (default) -- READ-ONLY audit. Scans your AWS account for any
#       resources belonging to the Stats & Leaderboards deployment and reports
#       what it finds. Never modifies or deletes anything.
#
#   "cleanup" -- Audit + interactive cleanup. Performs the same scan as "check",
#       then lists all found resources and asks for confirmation before deleting.
#       You can review the full list and choose to proceed (automatic deletion)
#       or decline (prints the commands for manual execution instead).
#
# Typical workflow:
#   1. Run `cdk destroy` to tear down the stack
#   2. Run `./check_resources.sh dev check` to see what's left behind
#   3. If remnants found, run `./check_resources.sh dev cleanup` to remove them
#   4. Run `./check_resources.sh dev check` again to confirm a clean slate
#   5. Proceed with your next `cdk deploy`
#
# What it scans:
#   - CloudFormation stacks (GameStatsLeaderboardsStack, monitoring stack)
#   - Lambda functions and layers
#   - API Gateway REST APIs
#   - DynamoDB tables (config + stats)
#   - MemoryDB clusters, subnet groups, ACLs, users, parameter groups
#   - VPC resources (VPCs, subnets, NAT gateways, security groups)
#   - SSM Parameter Store parameters (API keys, configuration)
#   - Secrets Manager secrets (MemoryDB credentials)
#   - IAM roles and policies
#   - KMS keys and aliases
#   - CloudWatch log groups, dashboards, alarms
#   - WAF Web ACLs
#   - Application Insights applications and resource groups
#
# Usage:
#   ./check_resources.sh [environment] [action] [--profile <name>] [--region <region>]
#
# Arguments:
#   environment   Deployment environment name (default: dev)
#                 Must match the ENVIRONMENT used during deployment.
#   action        "check"   -- read-only audit, no modifications (default)
#                 "cleanup" -- audit + interactive cleanup with confirmation
#
# Flags:
#   --profile <name>   AWS CLI profile to use (e.g., --profile myprofile)
#   --region <region>  AWS region to scan (e.g., --region us-east-1)
#   --help, -h         Show this help text
#
#   Flags can appear in any order, before or after the positional arguments.
#
# Examples:
#   ./check_resources.sh                                        # Audit dev in us-west-2
#   ./check_resources.sh dev check --profile myprofile          # Audit with specific profile
#   ./check_resources.sh dev cleanup --profile myprofile        # Cleanup with specific profile
#   ./check_resources.sh --region eu-west-1 staging check       # Flags before positional args
#   ./check_resources.sh dev cleanup --profile p --region r     # All options combined
#
# Important:
#   - This script is intended for DEVELOPMENT and TESTING stages to help
#     developers iterate on CDK deployments. It is NOT intended for use
#     against production environments.
#   - In "check" mode, the script is strictly read-only -- it only calls
#     AWS describe/list/get APIs and never modifies any resources.
#   - In "cleanup" mode, deletions require explicit interactive confirmation
#     (y/N prompt). Declining prints the commands for manual review instead.
#   - Requires AWS CLI v2 with permissions to describe/list the resource types
#     listed above. Cleanup mode additionally requires delete permissions.
#   - Each AWS API call has a 20-second timeout to prevent hanging.
# =============================================================================

# Defaults
ENVIRONMENT="dev"
ACTION="check"
REGION="${AWS_DEFAULT_REGION:-us-west-2}"
AWS_PROFILE_ARG=""

# Parse arguments -- supports both positional and flag-based invocation:
#   ./check_resources.sh dev cleanup --profile myprofile --region us-east-1
#   ./check_resources.sh --profile myprofile --region us-east-1 dev cleanup
#   ./check_resources.sh dev --profile myprofile
POSITIONAL_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --profile)
            AWS_PROFILE_ARG="--profile $2"
            export AWS_PROFILE="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            export AWS_DEFAULT_REGION="$2"
            shift 2
            ;;
        --help|-h)
            head -77 "$0" | tail -n +4 | sed 's/^# \?//'
            exit 0
            ;;
        -*)
            echo "Error: Unknown flag '$1'"
            echo "Usage: $0 [environment] [check|cleanup] [--profile <name>] [--region <region>]"
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Assign positional args (environment, action)
if [ ${#POSITIONAL_ARGS[@]} -ge 1 ]; then
    ENVIRONMENT="${POSITIONAL_ARGS[0]}"
fi
if [ ${#POSITIONAL_ARGS[@]} -ge 2 ]; then
    ACTION="${POSITIONAL_ARGS[1]}"
fi

# Validate action parameter
if [ "$ACTION" != "check" ] && [ "$ACTION" != "cleanup" ]; then
    echo "Error: Invalid action '$ACTION'. Must be 'check' or 'cleanup'."
    echo "Usage: $0 [environment] [check|cleanup] [--profile <name>] [--region <region>]"
    exit 1
fi

PREFIX="game-statsleaderboards-$ENVIRONMENT"
STACK_NAME="GameStatsLeaderboardsStack"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Global variables
TOTAL_RESOURCES=0
CLEANUP_COMMANDS_FILE="/tmp/cleanup_commands_$$"

# Function to print colored output
print_header() {
    echo -e "${PURPLE} $1${NC}"
}

print_section() {
    echo -e "${BLUE}$1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${CYAN}ℹ️  $1${NC}"
}

print_separator() {
    echo "================================================================================"
}

print_header_with_separator() {
    echo -e "${PURPLE}$1${NC}"
    print_separator
}

# Function to safely execute AWS commands with timeout
safe_aws_call() {
    local service_name=$1
    local aws_command=$2
    shift 2
    
    echo "🔍  Checking $service_name..."
    
    # Create a temporary file for the output
    local temp_output="/tmp/aws_output_$$"
    local temp_error="/tmp/aws_error_$$"
    
    # Run AWS command with timeout
    if run_with_timeout 20 aws $aws_command "$@" > "$temp_output" 2> "$temp_error"; then
        local output=$(cat "$temp_output" 2>/dev/null)
        if [ -n "$output" ] && [ "$output" != "null" ] && [ "$output" != "[]" ]; then
            echo "$output"
            # Count resources found
            local count=$(echo "$output" | grep -c . 2>/dev/null || echo "0")
            if [ "$count" -gt 0 ]; then
                TOTAL_RESOURCES=$((TOTAL_RESOURCES + count))
            fi
            rm -f "$temp_output" "$temp_error" 2>/dev/null
            return 0
        else
            print_info "No lingering $service_name resources found"
        fi
    else
        local exit_code=$?
        if [ $exit_code -eq 124 ]; then
            print_warning "$service_name check timed out (20s)"
        else
            local error_msg=$(cat "$temp_error" 2>/dev/null | head -1)
            if [[ "$error_msg" == *"does not exist"* ]] || [[ "$error_msg" == *"not found"* ]]; then
                print_info "No lingering $service_name resources found"
            else
                print_info "No lingering $service_name resources found or access denied"
            fi
        fi
    fi
    
    rm -f "$temp_output" "$temp_error" 2>/dev/null
    return 1
}

# Function to add cleanup command
add_cleanup_command() {
    local resource_type=$1
    local command=$2
    local description=$3
    echo "$resource_type|$command|$description" >> "$CLEANUP_COMMANDS_FILE"
}

# Determine the timeout command available on this system.
# macOS does not ship with `timeout`; use `gtimeout` from coreutils if available,
# otherwise fall back to running commands without a timeout wrapper.
if command -v timeout &> /dev/null; then
    TIMEOUT_CMD="timeout"
elif command -v gtimeout &> /dev/null; then
    TIMEOUT_CMD="gtimeout"
else
    # No timeout available -- define a no-op wrapper that just runs the command
    TIMEOUT_CMD=""
fi

# Helper: run a command with a timeout if possible, or directly if not
run_with_timeout() {
    local seconds=$1
    shift
    if [ -n "$TIMEOUT_CMD" ]; then
        $TIMEOUT_CMD "${seconds}s" "$@"
    else
        "$@"
    fi
}

# Initialize
initialize() {
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI not found. Please install AWS CLI."
        exit 1
    fi

    if ! run_with_timeout 10 aws sts get-caller-identity &> /dev/null; then
        print_error "AWS credentials not configured or network issue."
        print_info "Verify your credentials: aws sts get-caller-identity --profile <your-profile>"
        exit 1
    fi

    print_success "AWS credentials verified"
    > "$CLEANUP_COMMANDS_FILE"
}

# Check CloudFormation Stack
check_cloudformation() {
    print_section "📦 CLOUDFORMATION STACK"
    
    if safe_aws_call "CloudFormation Stack" cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].{StackName:StackName,Status:StackStatus,CreationTime:CreationTime}" \
        --output table; then
        add_cleanup_command "CloudFormation Stack" "aws cloudformation delete-stack --stack-name '$STACK_NAME' --region '$REGION'" "Delete CloudFormation stack"
    fi
    echo ""
}

# Check Secrets Manager
check_secrets() {
    print_section "🔐 SECRETS MANAGER"
    
    if safe_aws_call "Secrets Manager" secretsmanager list-secrets \
        --query "SecretList[?contains(Name, '$PREFIX')].{Name:Name,ARN:ARN,CreatedDate:CreatedDate}" \
        --output table; then
        
        # Get secrets for cleanup
        local secrets=$(run_with_timeout 10 aws secretsmanager list-secrets --query "SecretList[?contains(Name, '$PREFIX')].Name" --output text 2>/dev/null || echo "")
        for secret in $secrets; do
            if [ "$secret" != "" ] && [ "$secret" != "None" ]; then
                add_cleanup_command "Secret: $secret" "aws secretsmanager delete-secret --secret-id '$secret' --force-delete-without-recovery --region '$REGION'" "Force delete secret"
            fi
        done
    fi
    echo ""
}

# Check MemoryDB
check_memorydb() {
    print_section "🗄️ MEMORYDB"
    
    # Clusters
    if safe_aws_call "MemoryDB Clusters" memorydb describe-clusters \
        --query "Clusters[?contains(Name, '$PREFIX')].{Name:Name,Status:Status,Engine:Engine}" \
        --output table; then
        
        local clusters=$(run_with_timeout 10 aws memorydb describe-clusters --query "Clusters[?contains(Name, '$PREFIX')].Name" --output text 2>/dev/null || echo "")
        for cluster in $clusters; do
            if [ "$cluster" != "" ] && [ "$cluster" != "None" ]; then
                add_cleanup_command "MemoryDB Cluster: $cluster" "aws memorydb delete-cluster --cluster-name '$cluster' --region '$REGION'" "Delete MemoryDB cluster"
            fi
        done
    fi
    
    # Snapshots
    if safe_aws_call "MemoryDB Snapshots" memorydb describe-snapshots \
        --query "Snapshots[?contains(Name, '$PREFIX')].{Name:Name,Status:Status,CreationTime:CreationTime}" \
        --output table; then
        
        local snapshots=$(run_with_timeout 10 aws memorydb describe-snapshots --query "Snapshots[?contains(Name, '$PREFIX')].Name" --output text 2>/dev/null || echo "")
        for snapshot in $snapshots; do
            if [ "$snapshot" != "" ] && [ "$snapshot" != "None" ]; then
                add_cleanup_command "MemoryDB Snapshot: $snapshot" "aws memorydb delete-snapshot --snapshot-name '$snapshot' --region '$REGION'" "Delete MemoryDB snapshot"
            fi
        done
    fi
    
    # Subnet Groups
    if safe_aws_call "MemoryDB Subnet Groups" memorydb describe-subnet-groups \
        --query "SubnetGroups[?contains(SubnetGroupName, '$PREFIX')].{Name:SubnetGroupName,VpcId:VpcId}" \
        --output table; then
        
        local subnet_groups=$(run_with_timeout 10 aws memorydb describe-subnet-groups --query "SubnetGroups[?contains(SubnetGroupName, '$PREFIX')].SubnetGroupName" --output text 2>/dev/null || echo "")
        for sg in $subnet_groups; do
            if [ "$sg" != "" ] && [ "$sg" != "None" ]; then
                add_cleanup_command "MemoryDB Subnet Group: $sg" "aws memorydb delete-subnet-group --subnet-group-name '$sg' --region '$REGION'" "Delete MemoryDB subnet group"
            fi
        done
    fi
    
    # Parameter Groups
    if safe_aws_call "MemoryDB Parameter Groups" memorydb describe-parameter-groups \
        --query "ParameterGroups[?contains(ParameterGroupName, '$PREFIX')].{Name:ParameterGroupName,Family:Family,Description:Description}" \
        --output table; then
        
        local param_groups=$(run_with_timeout 10 aws memorydb describe-parameter-groups --query "ParameterGroups[?contains(ParameterGroupName, '$PREFIX')].ParameterGroupName" --output text 2>/dev/null || echo "")
        for pg in $param_groups; do
            if [ "$pg" != "" ] && [ "$pg" != "None" ]; then
                add_cleanup_command "MemoryDB Parameter Group: $pg" "aws memorydb delete-parameter-group --parameter-group-name '$pg' --region '$REGION'" "Delete MemoryDB parameter group"
            fi
        done
    fi
    
    # ACLs
    if safe_aws_call "MemoryDB ACLs" memorydb describe-acls \
        --query "ACLs[?contains(ACLName, '$PREFIX')].{Name:ACLName,Status:Status,UserNames:UserNames}" \
        --output table; then
        
        local acls=$(run_with_timeout 10 aws memorydb describe-acls --query "ACLs[?contains(ACLName, '$PREFIX')].ACLName" --output text 2>/dev/null || echo "")
        for acl in $acls; do
            if [ "$acl" != "" ] && [ "$acl" != "None" ]; then
                add_cleanup_command "MemoryDB ACL: $acl" "aws memorydb delete-acl --acl-name '$acl' --region '$REGION'" "Delete MemoryDB ACL"
            fi
        done
    fi
    
    # Users
    if safe_aws_call "MemoryDB Users" memorydb describe-users \
        --query "Users[?UserName=='glide-user' || contains(UserName, '$PREFIX')].{UserName:UserName,Status:Status,AccessString:AccessString}" \
        --output table; then
        
        local users=$(run_with_timeout 10 aws memorydb describe-users --query "Users[?UserName=='glide-user' || contains(UserName, '$PREFIX')].UserName" --output text 2>/dev/null || echo "")
        for user in $users; do
            if [ "$user" != "" ] && [ "$user" != "None" ]; then
                add_cleanup_command "MemoryDB User: $user" "aws memorydb delete-user --user-name '$user' --region '$REGION'" "Delete MemoryDB user"
            fi
        done
    fi
    
    # Additional check specifically for glide-user (hardcoded in infrastructure)
    print_info "Checking for hardcoded glide-user..."
    if run_with_timeout 10 aws memorydb describe-users --query "Users[?UserName=='glide-user']" --output text 2>/dev/null | grep -q "glide-user"; then
        # Check if we already added it to avoid duplicates
        if ! grep -q "MemoryDB User: glide-user" "$CLEANUP_COMMANDS_FILE" 2>/dev/null; then
            TOTAL_RESOURCES=$((TOTAL_RESOURCES + 1))
            add_cleanup_command "MemoryDB User: glide-user" "aws memorydb delete-user --user-name 'glide-user' --region '$REGION'" "Delete hardcoded MemoryDB glide-user"
            print_info "Found hardcoded glide-user"
        fi
    fi
    echo ""
}

# Check DynamoDB
check_dynamodb() {
    print_section "🗃️ DYNAMODB"
    
    if safe_aws_call "DynamoDB Tables" dynamodb list-tables \
        --query "TableNames[?contains(@, '$PREFIX')]" \
        --output table; then
        
        # Check each expected table
        for table_type in "config" "stats"; do
            local table_name="$PREFIX-$table_type"
            if safe_aws_call "DynamoDB Table: $table_name" dynamodb describe-table \
                --table-name "$table_name" \
                --query "Table.{TableName:TableName,Status:TableStatus,ItemCount:ItemCount}" \
                --output table; then
                add_cleanup_command "DynamoDB Table: $table_name" "aws dynamodb delete-table --table-name '$table_name' --region '$REGION'" "Delete DynamoDB table"
            fi
        done
    fi
    
    # Check for DynamoDB backups
    if safe_aws_call "DynamoDB Backups" dynamodb list-backups \
        --query "BackupSummaries[?contains(TableName, '$PREFIX')].{BackupArn:BackupArn,TableName:TableName,BackupName:BackupName,BackupStatus:BackupStatus,BackupCreationDateTime:BackupCreationDateTime}" \
        --output table; then
        
        local backup_arns=$(run_with_timeout 10 aws dynamodb list-backups --query "BackupSummaries[?contains(TableName, '$PREFIX')].BackupArn" --output text 2>/dev/null || echo "")
        for backup_arn in $backup_arns; do
            if [ "$backup_arn" != "" ] && [ "$backup_arn" != "None" ]; then
                add_cleanup_command "DynamoDB Backup: $backup_arn" "aws dynamodb delete-backup --backup-arn '$backup_arn' --region '$REGION'" "Delete DynamoDB backup"
            fi
        done
    fi
    echo ""
}

# Check Lambda
check_lambda() {
    print_section "⚡ LAMBDA"
    
    if safe_aws_call "Lambda Functions" lambda list-functions \
        --query "Functions[?contains(FunctionName, '$PREFIX')].{FunctionName:FunctionName,Runtime:Runtime,LastModified:LastModified}" \
        --output table; then
        
        local functions=$(run_with_timeout 10 aws lambda list-functions --query "Functions[?contains(FunctionName, '$PREFIX')].FunctionName" --output text 2>/dev/null || echo "")
        for func in $functions; do
            if [ "$func" != "" ] && [ "$func" != "None" ]; then
                add_cleanup_command "Lambda Function: $func" "aws lambda delete-function --function-name '$func' --region '$REGION'" "Delete Lambda function"
            fi
        done
    fi
    
    # Lambda Layers - IMPROVED VERSION
    print_info "Checking Lambda Layers and their versions..."
    if safe_aws_call "Lambda Layers" lambda list-layers \
        --query "Layers[?contains(LayerName, '$PREFIX')].{LayerName:LayerName,LatestMatchingVersion:LatestMatchingVersion.Version}" \
        --output table; then
        
        local layers=$(run_with_timeout 10 aws lambda list-layers --query "Layers[?contains(LayerName, '$PREFIX')].LayerName" --output text 2>/dev/null || echo "")
        for layer in $layers; do
            if [ "$layer" != "" ] && [ "$layer" != "None" ]; then
                print_info "Processing layer: $layer"
                
                # Get all versions for this layer
                local versions=$(run_with_timeout 10 aws lambda list-layer-versions \
                    --layer-name "$layer" \
                    --query "LayerVersions[].Version" \
                    --output text 2>/dev/null || echo "")
                
                if [ "$versions" != "" ] && [ "$versions" != "None" ]; then
                    local version_count=0
                    for version in $versions; do
                        if [ "$version" != "" ] && [ "$version" != "None" ]; then
                            version_count=$((version_count + 1))
                            add_cleanup_command "Lambda Layer: $layer (v$version)" \
                                "aws lambda delete-layer-version --layer-name '$layer' --version-number '$version' --region '$REGION'" \
                                "Delete Lambda layer version $version"
                        fi
                    done
                    print_info "Found $version_count versions for layer $layer"
                    TOTAL_RESOURCES=$((TOTAL_RESOURCES + version_count))
                else
                    print_warning "Could not retrieve versions for layer $layer"
                    # Fallback: try to delete version 1 (most common case)
                    add_cleanup_command "Lambda Layer: $layer (v1)" \
                        "aws lambda delete-layer-version --layer-name '$layer' --version-number 1 --region '$REGION'" \
                        "Delete Lambda layer version 1 (fallback)"
                fi
            fi
        done
    fi
    echo ""
}

# Check API Gateway
check_api_gateway() {
    print_section "🌐 API GATEWAY"
    
    if safe_aws_call "API Gateway" apigateway get-rest-apis \
        --query "items[?contains(name, '$PREFIX') || contains(name, 'game-statsleaderboards')].{id:id,name:name,createdDate:createdDate}" \
        --output table; then
        
        local api_ids=$(run_with_timeout 10 aws apigateway get-rest-apis --query "items[?contains(name, '$PREFIX') || contains(name, 'game-statsleaderboards')].id" --output text 2>/dev/null || echo "")
        for api_id in $api_ids; do
            if [ "$api_id" != "" ] && [ "$api_id" != "None" ]; then
                add_cleanup_command "API Gateway: $api_id" "aws apigateway delete-rest-api --rest-api-id '$api_id' --region '$REGION'" "Delete API Gateway"
            fi
        done
    fi
    echo ""
}

# Check VPC
check_vpc() {
    print_section "🌐 VPC"
    
    if safe_aws_call "VPCs" ec2 describe-vpcs \
        --filters "Name=tag:Service,Values=game-statsleaderboards" "Name=tag:Environment,Values=$ENVIRONMENT" \
        --query "Vpcs[].{VpcId:VpcId,State:State,CidrBlock:CidrBlock}" \
        --output table; then
        
        local vpc_id=$(run_with_timeout 10 aws ec2 describe-vpcs --filters "Name=tag:Service,Values=game-statsleaderboards" "Name=tag:Environment,Values=$ENVIRONMENT" --query "Vpcs[0].VpcId" --output text 2>/dev/null || echo "")
        if [ "$vpc_id" != "" ] && [ "$vpc_id" != "None" ] && [ "$vpc_id" != "null" ]; then
            add_cleanup_command "VPC: $vpc_id" "aws ec2 delete-vpc --vpc-id '$vpc_id' --region '$REGION'" "Delete VPC (after dependencies)"
            
            # Check subnets
            safe_aws_call "VPC Subnets" ec2 describe-subnets \
                --filters "Name=vpc-id,Values=$vpc_id" \
                --query "Subnets[].{SubnetId:SubnetId,AvailabilityZone:AvailabilityZone,CidrBlock:CidrBlock}" \
                --output table
        fi
    fi
    echo ""
}

# Check CloudWatch Log Groups - ENHANCED VERSION
check_logs() {
    print_section "📝 CLOUDWATCH LOGS"
    
    local found_logs=false
    local log_groups_found=0
    
    # Method 1: Check Lambda-specific log groups
    print_info "Checking Lambda-specific log groups..."
    if run_with_timeout 20 aws logs describe-log-groups \
        --log-group-name-prefix "/aws/lambda/$PREFIX" \
        --query "logGroups[].{logGroupName:logGroupName,creationTime:creationTime,storedBytes:storedBytes}" \
        --output table 2>/dev/null | grep -v "^$"; then
        
        found_logs=true
        local lambda_log_groups=$(run_with_timeout 10 aws logs describe-log-groups \
            --log-group-name-prefix "/aws/lambda/$PREFIX" \
            --query "logGroups[].logGroupName" \
            --output text 2>/dev/null || echo "")
        
        for log_group in $lambda_log_groups; do
            if [ "$log_group" != "" ] && [ "$log_group" != "None" ]; then
                log_groups_found=$((log_groups_found + 1))
                add_cleanup_command "Log Group: $log_group" \
                    "aws logs delete-log-group --log-group-name '$log_group' --region '$REGION'" \
                    "Delete Lambda log group"
            fi
        done
    fi
    
    # Method 2: Check API Gateway log groups
    print_info "Checking API Gateway log groups..."
    if run_with_timeout 20 aws logs describe-log-groups \
        --log-group-name-prefix "/aws/apigateway" \
        --query "logGroups[?contains(logGroupName, '$PREFIX') || contains(logGroupName, 'game-statsleaderboards')].{logGroupName:logGroupName,creationTime:creationTime,storedBytes:storedBytes}" \
        --output table 2>/dev/null | grep -v "^$"; then
        
        found_logs=true
        local apigw_log_groups=$(run_with_timeout 10 aws logs describe-log-groups \
            --log-group-name-prefix "/aws/apigateway" \
            --query "logGroups[?contains(logGroupName, '$PREFIX') || contains(logGroupName, 'game-statsleaderboards')].logGroupName" \
            --output text 2>/dev/null || echo "")
        
        for log_group in $apigw_log_groups; do
            if [ "$log_group" != "" ] && [ "$log_group" != "None" ]; then
                # Check if we already added this log group to avoid duplicates
                if ! grep -q "Log Group: $log_group" "$CLEANUP_COMMANDS_FILE" 2>/dev/null; then
                    log_groups_found=$((log_groups_found + 1))
                    add_cleanup_command "Log Group: $log_group" \
                        "aws logs delete-log-group --log-group-name '$log_group' --region '$REGION'" \
                        "Delete API Gateway log group"
                fi
            fi
        done
    fi
    
    # Method 3: Search for log groups containing project identifiers using pattern matching
    print_info "Searching for log groups with project identifiers..."
    local search_patterns=("$PREFIX" "game-statsleaderboards")
    
    for pattern in "${search_patterns[@]}"; do
        if run_with_timeout 20 aws logs describe-log-groups \
            --log-group-name-pattern "$pattern" \
            --query "logGroups[].{logGroupName:logGroupName,creationTime:creationTime,storedBytes:storedBytes}" \
            --output table 2>/dev/null | grep -v "^$"; then
            
            found_logs=true
            local pattern_log_groups=$(run_with_timeout 10 aws logs describe-log-groups \
                --log-group-name-pattern "$pattern" \
                --query "logGroups[].logGroupName" \
                --output text 2>/dev/null || echo "")
            
            for log_group in $pattern_log_groups; do
                if [ "$log_group" != "" ] && [ "$log_group" != "None" ]; then
                    # Check if we already added this log group to avoid duplicates
                    if ! grep -q "Log Group: $log_group" "$CLEANUP_COMMANDS_FILE" 2>/dev/null; then
                        log_groups_found=$((log_groups_found + 1))
                        add_cleanup_command "Log Group: $log_group" \
                            "aws logs delete-log-group --log-group-name '$log_group' --region '$REGION'" \
                            "Delete log group (pattern match: $pattern)"
                    fi
                fi
            done
        fi
    done
    
    # Update total resources count
    if [ "$log_groups_found" -gt 0 ]; then
        TOTAL_RESOURCES=$((TOTAL_RESOURCES + log_groups_found))
        print_success "Total log groups found: $log_groups_found"
    fi
    
    if [ "$found_logs" = false ]; then
        print_info "No lingering CloudWatch log groups found"
    fi
    
    echo ""
}

# Check IAM
check_iam() {
    print_section "👤 IAM"
    
    if safe_aws_call "IAM Roles" iam list-roles \
        --query "Roles[?contains(RoleName, '$PREFIX')].{RoleName:RoleName,CreateDate:CreateDate}" \
        --output table; then
        
        local roles=$(run_with_timeout 10 aws iam list-roles --query "Roles[?contains(RoleName, '$PREFIX')].RoleName" --output text 2>/dev/null || echo "")
        for role in $roles; do
            if [ "$role" != "" ] && [ "$role" != "None" ]; then
                add_cleanup_command "IAM Role: $role" "aws iam delete-role --role-name '$role' --region '$REGION'" "Delete IAM role (detach policies first)"
            fi
        done
    fi
    echo ""
}

# Check KMS Keys
check_kms() {
    print_section "🔑 KMS"
    
    local found_keys_file=$(mktemp)
    local found_aliases_file=$(mktemp)
    local cleanup_keys_file=$(mktemp)
    
    # Cleanup temp files on exit
    trap "rm -f $found_keys_file $found_aliases_file $cleanup_keys_file" EXIT
    
    # First, find all KMS aliases with our prefix (specific pattern from app.py)
    local target_alias="alias/$PREFIX-key"
    print_info "Checking KMS aliases with prefix '$PREFIX'..."
    print_info "Checking for specific KMS alias pattern: $target_alias"
    if safe_aws_call "KMS Aliases" kms list-aliases \
        --query "Aliases[?contains(AliasName, '$PREFIX')].{AliasName:AliasName,TargetKeyId:TargetKeyId}" \
        --output table; then
        
        # Get aliases and their target key IDs
        local alias_output=$(run_with_timeout 10 aws kms list-aliases \
            --query "Aliases[?contains(AliasName, '$PREFIX')].[AliasName,TargetKeyId]" \
            --output text 2>/dev/null || echo "")
        
        if [ "$alias_output" != "" ]; then
            echo "$alias_output" | while read -r alias_name target_key_id; do
                if [ "$alias_name" != "" ] && [ "$alias_name" != "None" ] && [ "$alias_name" != "null" ]; then
                    echo "$alias_name" >> "$found_aliases_file"
                    add_cleanup_command "KMS Alias: $alias_name" "aws kms delete-alias --alias-name '$alias_name' --region '$REGION'" "Delete KMS alias"
                fi
                if [ "$target_key_id" != "" ] && [ "$target_key_id" != "None" ] && [ "$target_key_id" != "null" ]; then
                    echo "$target_key_id" >> "$found_keys_file"
                fi
            done
        fi
    fi
    
    # Check specifically for the exact alias pattern used by app.py
    print_info "Checking for exact alias pattern: $target_alias"
    if run_with_timeout 10 aws kms describe-key --key-id "$target_alias" --query "KeyMetadata.KeyId" --output text 2>/dev/null; then
        local exact_key_id=$(run_with_timeout 10 aws kms describe-key --key-id "$target_alias" --query "KeyMetadata.KeyId" --output text 2>/dev/null)
        if [ "$exact_key_id" != "" ] && [ "$exact_key_id" != "None" ]; then
            echo "$exact_key_id" >> "$found_keys_file"
            # Check if we already added this alias
            if ! grep -q "KMS Alias: $target_alias" "$CLEANUP_COMMANDS_FILE" 2>/dev/null; then
                add_cleanup_command "KMS Alias: $target_alias" "aws kms delete-alias --alias-name '$target_alias' --region '$REGION'" "Delete exact KMS alias"
            fi
        fi
    fi
    
    # Second, find KMS keys by tags (more comprehensive approach)
    print_info "Checking KMS keys with project tags..."
    local key_count=0
    local checked_count=0
    
    # Get all customer-managed keys (exclude AWS managed keys)
    for key_id in $(run_with_timeout 30 aws kms list-keys --query "Keys[].KeyId" --output text 2>/dev/null); do
        if [ "$key_id" != "" ] && [ "$key_id" != "None" ]; then
            checked_count=$((checked_count + 1))
            
            # Skip if we already found this key via alias
            if grep -q "^$key_id$" "$found_keys_file" 2>/dev/null; then
                continue
            fi
            
            # Check key metadata to ensure it's customer-managed
            local key_info=$(run_with_timeout 5 aws kms describe-key --key-id "$key_id" \
                --query "{KeyManager:KeyMetadata.KeyManager,KeyState:KeyMetadata.KeyState}" \
                --output text 2>/dev/null || echo "")
            
            if echo "$key_info" | grep -q "CUSTOMER"; then
                local key_state=$(echo "$key_info" | awk '{print $2}')
                
                # Check if key has our project tags
                local has_project_tag=false
                if run_with_timeout 5 aws kms list-resource-tags --key-id "$key_id" \
                    --query "Tags[?Key=='Service' && Value=='game-statsleaderboards']" \
                    --output text 2>/dev/null | grep -q "game-statsleaderboards"; then
                    has_project_tag=true
                fi
                
                # Also check for other common project identifiers in tags
                if [ "$has_project_tag" = "false" ]; then
                    if run_with_timeout 5 aws kms list-resource-tags --key-id "$key_id" \
                        --query "Tags[?contains(Value, '$PREFIX')]" \
                        --output text 2>/dev/null | grep -q "$PREFIX"; then
                        has_project_tag=true
                    fi
                fi
                
                if [ "$has_project_tag" = "true" ]; then
                    key_count=$((key_count + 1))
                    TOTAL_RESOURCES=$((TOTAL_RESOURCES + 1))
                    echo "$key_id" >> "$found_keys_file"
                    
                    # Check if key is already scheduled for deletion
                    if [ "$key_state" != "PendingDeletion" ]; then
                        echo "$key_id" >> "$cleanup_keys_file"
                        add_cleanup_command "KMS Key: $key_id" \
                            "aws kms schedule-key-deletion --key-id '$key_id' --pending-window-in-days 7 --region '$REGION'" \
                            "Schedule KMS key for deletion (7 days)"
                    else
                        print_info "KMS Key $key_id is already scheduled for deletion"
                    fi
                fi
            fi
        fi
        
        # Limit to prevent timeout on accounts with many keys
        if [ "$checked_count" -gt 100 ]; then
            print_info "Limiting KMS key check to first 100 keys to prevent timeout"
            break
        fi
    done
    
    # Third, check for any keys referenced by aliases that might not have tags
    print_info "Cross-checking alias target keys..."
    if [ -f "$found_keys_file" ]; then
        while read -r target_key; do
            if [ "$target_key" != "" ]; then
                # Check if we already scheduled this key for deletion
                if grep -q "^$target_key$" "$cleanup_keys_file" 2>/dev/null; then
                    continue
                fi
                
                # Verify this key exists and get its state
                local key_info=$(run_with_timeout 5 aws kms describe-key --key-id "$target_key" \
                    --query "{KeyManager:KeyMetadata.KeyManager,KeyState:KeyMetadata.KeyState}" \
                    --output text 2>/dev/null || echo "")
                
                if echo "$key_info" | grep -q "CUSTOMER"; then
                    local key_state=$(echo "$key_info" | awk '{print $2}')
                    
                    if [ "$key_state" != "PendingDeletion" ]; then
                        key_count=$((key_count + 1))
                        TOTAL_RESOURCES=$((TOTAL_RESOURCES + 1))
                        echo "$target_key" >> "$cleanup_keys_file"
                        add_cleanup_command "KMS Key (from alias): $target_key" \
                            "aws kms schedule-key-deletion --key-id '$target_key' --pending-window-in-days 7 --region '$REGION'" \
                            "Schedule KMS key for deletion (7 days)"
                    fi
                fi
            fi
        done < "$found_keys_file"
    fi
    
    # Summary
    local alias_count=0
    if [ -f "$found_aliases_file" ]; then
        alias_count=$(wc -l < "$found_aliases_file" 2>/dev/null || echo "0")
    fi
    
    if [ "$alias_count" -gt 0 ]; then
        echo "Found $alias_count KMS aliases with prefix '$PREFIX'"
    fi
    if [ "$key_count" -gt 0 ]; then
        echo "Found $key_count KMS keys for cleanup"
    fi
    if [ "$alias_count" -eq 0 ] && [ "$key_count" -eq 0 ]; then
        print_info "No KMS resources found with project identifiers"
    fi
    
    # Cleanup temp files
    rm -f "$found_keys_file" "$found_aliases_file" "$cleanup_keys_file"
    echo ""
}

# Check WAF Web ACLs
check_waf() {
    print_section "🛡️ WAF"
    
    if safe_aws_call "WAF Web ACLs" wafv2 list-web-acls \
        --scope REGIONAL \
        --query "WebACLs[?contains(Name, '$PREFIX')].{Name:Name,Id:Id,Description:Description}" \
        --output table; then
        
        local waf_ids=$(run_with_timeout 10 aws wafv2 list-web-acls --scope REGIONAL --query "WebACLs[?contains(Name, '$PREFIX')].Id" --output text 2>/dev/null || echo "")
        for waf_id in $waf_ids; do
            if [ "$waf_id" != "" ] && [ "$waf_id" != "None" ]; then
                add_cleanup_command "WAF Web ACL: $waf_id" "aws wafv2 delete-web-acl --scope REGIONAL --id '$waf_id' --lock-token \$(aws wafv2 get-web-acl --scope REGIONAL --id '$waf_id' --query 'LockToken' --output text) --region '$REGION'" "Delete WAF Web ACL"
            fi
        done
    fi
    echo ""
}

# Check SSM Parameters
check_ssm() {
    print_section "⚙️ SSM"
    
    if safe_aws_call "SSM Parameters" ssm describe-parameters \
        --parameter-filters "Key=Name,Option=BeginsWith,Values=/$PREFIX" \
        --query "Parameters[].{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}" \
        --output table; then
        
        local params=$(run_with_timeout 10 aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/$PREFIX" --query "Parameters[].Name" --output text 2>/dev/null || echo "")
        for param in $params; do
            if [ "$param" != "" ] && [ "$param" != "None" ]; then
                add_cleanup_command "SSM Parameter: $param" "aws ssm delete-parameter --name '$param' --region '$REGION'" "Delete SSM parameter"
            fi
        done
    fi
    echo ""
}

# Check CloudWatch Resources
check_cloudwatch() {
    print_section "📊 CLOUDWATCH"
    
    # Dashboards
    if safe_aws_call "CloudWatch Dashboards" cloudwatch list-dashboards \
        --query "DashboardEntries[?contains(DashboardName, '$PREFIX')].{DashboardName:DashboardName,LastModified:LastModified}" \
        --output table; then
        
        local dashboards=$(run_with_timeout 10 aws cloudwatch list-dashboards --query "DashboardEntries[?contains(DashboardName, '$PREFIX')].DashboardName" --output text 2>/dev/null || echo "")
        for dashboard in $dashboards; do
            if [ "$dashboard" != "" ] && [ "$dashboard" != "None" ]; then
                add_cleanup_command "CloudWatch Dashboard: $dashboard" "aws cloudwatch delete-dashboards --dashboard-names '$dashboard' --region '$REGION'" "Delete CloudWatch dashboard"
            fi
        done
    fi
    
    # Alarms
    if safe_aws_call "CloudWatch Alarms" cloudwatch describe-alarms \
        --query "MetricAlarms[?contains(AlarmName, '$PREFIX')].{AlarmName:AlarmName,StateValue:StateValue,MetricName:MetricName}" \
        --output table; then
        
        local alarms=$(run_with_timeout 10 aws cloudwatch describe-alarms --query "MetricAlarms[?contains(AlarmName, '$PREFIX')].AlarmName" --output text 2>/dev/null || echo "")
        for alarm in $alarms; do
            if [ "$alarm" != "" ] && [ "$alarm" != "None" ]; then
                add_cleanup_command "CloudWatch Alarm: $alarm" "aws cloudwatch delete-alarms --alarm-names '$alarm' --region '$REGION'" "Delete CloudWatch alarm"
            fi
        done
    fi
    echo ""
}

# Check Application Insights and Resource Groups
check_application_insights() {
    print_section "📊 APPLICATION INSIGHTS & RESOURCE GROUPS"
    
    # Application Insights
    if safe_aws_call "Application Insights" application-insights list-applications \
        --query "ApplicationInfoList[?contains(ResourceGroupName, '$PREFIX')].{ResourceGroupName:ResourceGroupName,LifeCycle:LifeCycle}" \
        --output table; then
        
        local apps=$(run_with_timeout 10 aws application-insights list-applications --query "ApplicationInfoList[?contains(ResourceGroupName, '$PREFIX')].ResourceGroupName" --output text 2>/dev/null || echo "")
        for app in $apps; do
            if [ "$app" != "" ] && [ "$app" != "None" ]; then
                add_cleanup_command "Application Insights: $app" "aws application-insights delete-application --resource-group-name '$app' --region '$REGION'" "Delete Application Insights application"
            fi
        done
    fi
    
    # Resource Groups
    if safe_aws_call "Resource Groups" resource-groups list-groups \
        --query "GroupIdentifiers[?contains(GroupName, '$PREFIX')].{GroupName:GroupName,GroupArn:GroupArn}" \
        --output table; then
        
        local groups=$(run_with_timeout 10 aws resource-groups list-groups --query "GroupIdentifiers[?contains(GroupName, '$PREFIX')].GroupName" --output text 2>/dev/null || echo "")
        for group in $groups; do
            if [ "$group" != "" ] && [ "$group" != "None" ]; then
                add_cleanup_command "Resource Group: $group" "aws resource-groups delete-group --group-name '$group' --region '$REGION'" "Delete resource group"
            fi
        done
    fi
    echo ""
}

# Show resource summary table and, in cleanup mode, offer to delete
show_resource_summary_and_cleanup() {
    if [ ! -f "$CLEANUP_COMMANDS_FILE" ] || [ ! -s "$CLEANUP_COMMANDS_FILE" ]; then
        print_header_with_separator "RESOURCE SUMMARY"
        printf "   %-30s: %s\n" "Total Lingering Resources Found" "$TOTAL_RESOURCES"
        echo ""
        print_success " No lingering resources found - environment is clean"
        echo ""
        return
    fi

    # Show summary table (both modes)
    print_header_with_separator "RESOURCE SUMMARY"
    printf "   %-30s: %s\n" "Total Lingering Resources Found" "$TOTAL_RESOURCES"
    echo ""

    print_warning " Found $TOTAL_RESOURCES lingering resources"
    echo ""

    # Show resource listing (both modes)
    echo "Lingering resources found:"
    echo ""
    printf "%-4s %-50s %-20s\n" "No." "Resource" "Type"
    echo "--------------------------------------------------------------------------------"

    local counter=1
    while IFS='|' read -r resource_type command description; do
        local resource_name=$(echo "$resource_type" | cut -d':' -f2- | sed 's/^ *//')
        local resource_category=$(echo "$resource_type" | cut -d':' -f1)

        printf "%-4s %-50s %-20s\n" "$counter." "$resource_name" "$resource_category"
        counter=$((counter + 1))
    done < "$CLEANUP_COMMANDS_FILE"

    echo ""

    # In "check" mode, stop here -- read-only, no cleanup offered
    if [ "$ACTION" = "check" ]; then
        print_info " Run with 'cleanup' action to remove these resources:"
        print_info "   ./check_resources.sh $ENVIRONMENT cleanup"
        echo ""
        return
    fi

    # In "cleanup" mode, prompt for interactive cleanup
    print_warning " These lingering resources will be PERMANENTLY DELETED if you proceed."
    echo ""
    read -p "Do you want to proceed with automatic cleanup? (y/N): " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        perform_automatic_cleanup
    else
        echo ""
        print_info " Automatic cleanup declined. Here are the commands for manual execution:"
        show_manual_cleanup_commands
    fi
}

# Perform automatic cleanup
perform_automatic_cleanup() {
    print_header_with_separator "🗑️ PERFORMING AUTOMATIC CLEANUP"
    
    echo "Executing cleanup commands..."
    echo ""
    
    local success_count=0
    local failure_count=0
    local counter=1
    
    while IFS='|' read -r resource_type command description; do
        echo "[$counter] Deleting: $resource_type"
        echo "    Command: $command"
        
        if eval "$command" 2>/dev/null; then
            print_success "   ✅ Successfully deleted: $resource_type"
            success_count=$((success_count + 1))
        else
            print_error "   ❌ Failed to delete: $resource_type"
            failure_count=$((failure_count + 1))
        fi
        echo ""
        
        # Small delay between deletions to avoid rate limiting
        sleep 2
        counter=$((counter + 1))
    done < "$CLEANUP_COMMANDS_FILE"
    
    # Show cleanup summary
    print_separator
    echo "Cleanup Summary:"
    printf "   %-20s: %s\n" "Successfully deleted" "$success_count"
    printf "   %-20s: %s\n" "Failed to delete" "$failure_count"
    printf "   %-20s: %s\n" "Total processed" "$((success_count + failure_count))"
    echo ""
    
    if [ "$failure_count" -gt 0 ]; then
        print_warning " Some resources failed to delete. This may be due to dependencies or permissions."
        print_info " You may need to delete them manually or re-run the script after a few minutes."
    else
        print_success " All lingering resources have been successfully cleaned up!"
    fi
}

# Show manual cleanup commands
show_manual_cleanup_commands() {
    print_header_with_separator "🗑️ MANUAL CLEANUP COMMANDS"
    
    echo "Copy and execute these commands manually to delete specific resources:"
    echo ""
    
    local counter=1
    while IFS='|' read -r resource_type command description; do
        printf "%2d. %s\n" "$counter" "$resource_type"
        printf "    Command: %s\n" "$command"
        echo ""
        counter=$((counter + 1))
    done < "$CLEANUP_COMMANDS_FILE"
}

# Cleanup temp files
cleanup_temp() {
    rm -f "$CLEANUP_COMMANDS_FILE" /tmp/aws_output_$$ /tmp/aws_error_$$ 2>/dev/null || true
}

# Main function
main() {
    print_header_with_separator "🔍 LINGERING RESOURCE CHECK FOR: $PREFIX"
    print_header "📍 Region: $REGION"
    print_header "🎯 Action: $ACTION"
    echo ""
    
    trap cleanup_temp EXIT
    
    initialize
    
    # Run checks
    check_cloudformation
    check_secrets
    check_memorydb
    check_dynamodb
    check_lambda
    check_api_gateway
    check_vpc
    check_logs
    check_iam
    check_kms
    check_waf
    check_ssm
    check_cloudwatch
    check_application_insights
    
    # Show results and handle cleanup
    show_resource_summary_and_cleanup
    
    print_separator
    print_success " Lingering resource check complete for environment: $ENVIRONMENT"
    print_separator
}

# Run main function
main "$@"
