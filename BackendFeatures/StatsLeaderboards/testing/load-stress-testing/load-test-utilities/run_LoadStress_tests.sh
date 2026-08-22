#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

################################################################################
# Load & Stress Test Runner
# 
# Smart test orchestration script that:
# 1. Generates test configuration
# 2. Validates setup with dry-run
# 3. Starts PRIMARY test instance (foreground)
# 4. Monitors for "Spawning worker threads..." checkpoint
# 5. Launches additional WORKER instances in steps (background)
# 6. Scales up to target player count
# 7. Provides real-time monitoring
# 8. Handles graceful shutdown of all instances
#
# Usage:
#   ./run_LoadStress_tests.sh [options]
#
# Options:
#   --test-name NAME          Human-readable test name (required)

################################################################################
# CRITICAL: Increase file descriptor limit
# macOS default soft limit is 256, which is too low for many worker threads
# Each worker needs ~4-5 FDs (boto3 clients, log files, network connections)
# Set to 4096 to support up to ~800 workers safely
################################################################################
ulimit -n 4096

# Verify the limit was set
CURRENT_FD_LIMIT=$(ulimit -n)
if [ "$CURRENT_FD_LIMIT" -lt 1024 ]; then
    echo "⚠️  WARNING: File descriptor limit is only $CURRENT_FD_LIMIT"
    echo "   This may cause 'Too many open files' errors with many workers"
    echo "   Try running: ulimit -n 4096"
fi

#   --genre GENRE             Game genre (optional, random if not specified)
#   --initial-players N       Initial concurrent players (default: 10)
#   --scale-to N              Scale up to N players total (default: 350)
#   --scale-step N            Add N players per instance (default: 0, auto-calculated)
#   --duration MINUTES        Test duration in minutes (default: 120)
#   --aws-profile PROFILE     AWS profile name (default: default)
#   --region REGION           AWS region (default: us-west-2)
#   --skip-generation         Skip generation, use existing config
#   --test-config-id ID       Use specific test config ID
#   --no-scale                Don't auto-scale, stay at initial concurrency
#   --force-unlock            Force unlock global test lock
#   --help                    Show this help message
#
################################################################################

set -e  # Exit on error
set -o pipefail  # Exit on pipe failure

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Prefix for all shell script output
PREFIX="${CYAN}[LT Runner]${NC}"

# Default configuration
TEST_NAME=""
GENRE=""
INITIAL_PLAYERS=10
SCALE_TO_PLAYERS=350
SCALE_STEP=0                                                      # 0 means auto-calculate
DURATION_MINUTES=120
AWS_PROFILE="default"
AWS_REGION="us-west-2"
SKIP_GENERATION=false
TEST_CONFIG_ID=""
NO_SCALE=false
FORCE_UNLOCK=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_SCRIPT="${SCRIPT_DIR}/test_LoadAndStressTests.py"
LOG_DIR="${SCRIPT_DIR}/testlogs"
REPORTS_DIR="${SCRIPT_DIR}/reports"

# Process tracking
PRIMARY_PID=""
declare -a WORKER_PIDS=()
declare -a ALL_PIDS=()

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --test-name)
            TEST_NAME="$2"
            shift 2
            ;;
        --genre)
            GENRE="$2"
            shift 2
            ;;
        --initial-players)
            INITIAL_PLAYERS="$2"
            shift 2
            ;;
        --scale-to)
            SCALE_TO_PLAYERS="$2"
            shift 2
            ;;
        --scale-step)
            SCALE_STEP="$2"
            shift 2
            ;;
        --duration)
            DURATION_MINUTES="$2"
            shift 2
            ;;
        --aws-profile)
            AWS_PROFILE="$2"
            shift 2
            ;;
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --skip-generation)
            SKIP_GENERATION=true
            shift
            ;;
        --test-config-id)
            TEST_CONFIG_ID="$2"
            SKIP_GENERATION=true
            shift 2
            ;;
        --no-scale)
            NO_SCALE=true
            shift
            ;;
        --force-unlock)
            FORCE_UNLOCK=true
            shift
            ;;
        --help)
            # Extract only the header documentation (lines 1-30)
            head -30 "$0" | grep "^#" | grep -v "#!/bin/bash" | sed 's/^# //'
            exit 0
            ;;
        *)
            echo -e "${PREFIX} ${RED}Unknown option: $1${NC}"
            echo -e "${PREFIX} Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$TEST_NAME" ] && [ "$SKIP_GENERATION" = false ] && [ "$FORCE_UNLOCK" = false ]; then
    echo -e "${PREFIX} ${RED}Error: --test-name is required for new test generation${NC}"
    echo -e "${PREFIX} Use --help for usage information"
    exit 1
fi

# Print banner
print_banner() {
    echo -e "${PREFIX} ${CYAN}${BOLD}"
    echo -e "${PREFIX} ╔════════════════════════════════════════════════════════════════╗"
    echo -e "${PREFIX} ║         Load & Stress Test Runner - Smart Orchestration        ║"
    echo -e "${PREFIX} ╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Print section header
print_section() {
    echo ""
    echo -e "${PREFIX} ${BLUE}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${PREFIX} ${BLUE}${BOLD}  $1${NC}"
    echo -e "${PREFIX} ${BLUE}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# Print status message
print_status() {
    echo -e "${PREFIX} ${GREEN}✓${NC} $1"
}

# Print warning message
print_warning() {
    echo -e "${PREFIX} ${YELLOW}⚠${NC} $1"
}

# Print error message
print_error() {
    echo -e "${PREFIX} ${RED}✗${NC} $1"
}

# Print info message
print_info() {
    echo -e "${PREFIX} ${CYAN}ℹ${NC} $1"
}

# Cleanup function for graceful shutdown
cleanup() {
    local exit_code=$?
    echo -e "\n${PREFIX}"
    print_section "Cleanup and Shutdown"
    
    # Send SIGINT to all tracked processes
    if [ ${#ALL_PIDS[@]} -gt 0 ]; then
        print_info "Sending graceful shutdown signal to all test processes..."
        
        for pid in "${ALL_PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                print_info "  Stopping process PID: $pid"
                kill -SIGINT "$pid" 2>/dev/null || true
            fi
        done
        
        # Wait up to 60 seconds for graceful shutdown
        print_info "Waiting for graceful shutdown (max 60 seconds)..."
        local wait_count=0
        local all_stopped=false
        
        while [ $wait_count -lt 60 ]; do
            all_stopped=true
            for pid in "${ALL_PIDS[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    all_stopped=false
                    break
                fi
            done
            
            if [ "$all_stopped" = true ]; then
                break
            fi
            
            sleep 1
            wait_count=$((wait_count + 1))
        done
        
        if [ "$all_stopped" = true ]; then
            print_status "All test processes shut down gracefully"
        else
            print_warning "Some processes did not stop gracefully, forcing shutdown..."
            for pid in "${ALL_PIDS[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    kill -SIGTERM "$pid" 2>/dev/null || true
                fi
            done
        fi
    fi
    
    # Handle exit codes
    if [ $exit_code -eq 0 ]; then
        print_status "Test runner completed successfully"
    elif [ $exit_code -eq 130 ]; then
        # Exit code 130 = SIGINT (Ctrl+C) - graceful user interruption
        print_status "Test interrupted by user (Ctrl+C)"
        exit_code=0  # Treat as success since it was intentional
    else
        print_error "Test runner exited with code: $exit_code"
    fi
    
    exit $exit_code
}

# Set up signal handlers
trap cleanup EXIT INT TERM

# Check Python version
check_python() {
    print_section "Environment Validation"
    
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed"
        exit 1
    fi
    
    local python_version=$(python3 --version | awk '{print $2}')
    print_status "Python version: $python_version"
    
    # Check if test script exists
    if [ ! -f "$TEST_SCRIPT" ]; then
        print_error "Test script not found: $TEST_SCRIPT"
        exit 1
    fi
    print_status "Test script found: $TEST_SCRIPT"
    
    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        print_warning "AWS CLI not found (optional for some features)"
    else
        local aws_version=$(aws --version 2>&1 | awk '{print $1}')
        print_status "AWS CLI: $aws_version"
    fi
}

# Generate test configuration
generate_config() {
    print_section "Test Configuration Generation"
    
    print_info "Test Name: ${BOLD}$TEST_NAME${NC}"
    [ -n "$GENRE" ] && print_info "Genre: ${BOLD}$GENRE${NC}"
    [ -z "$GENRE" ] && print_info "Genre: ${BOLD}Random${NC}"
    print_info "AWS Profile: $AWS_PROFILE"
    print_info "AWS Region: $AWS_REGION"
    echo -e "${PREFIX}"
    
    local cmd="python3 \"$TEST_SCRIPT\" --generate-new --test-name \"$TEST_NAME\""
    [ -n "$GENRE" ] && cmd="$cmd --genre \"$GENRE\""
    cmd="$cmd --aws-profile \"$AWS_PROFILE\" --region \"$AWS_REGION\" --overwrite"
    
    # NOTE: max-players and duration are NOT passed during generation
    # They are process-specific runtime parameters passed during execution
    # Multiple instances can join the same test with different player counts
    
    print_info "Generating test configuration..."
    echo -e "${PREFIX}"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX} Python script output below:"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX}"
    
    # Capture output to extract Test Config ID
    local output_file=$(mktemp)
    if eval "$cmd" 2>&1 | tee "$output_file"; then
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        print_status "Test configuration generated successfully"
        
        # Extract Test Config ID from output
        # Look for the final success marker line surrounded by === (most reliable)
        TEST_CONFIG_ID=$(grep "^✓ Test Config ID:" "$output_file" | sed -E 's/.*✓ Test Config ID: ([a-z0-9-]+).*/\1/' | tail -1)
        
        # Fallback to "Generated Test Config ID:" line
        if [ -z "$TEST_CONFIG_ID" ]; then
            TEST_CONFIG_ID=$(grep "Generated Test Config ID:" "$output_file" | sed -E 's/.*Generated Test Config ID: ([a-z0-9-]+).*/\1/' | head -1)
        fi
        
        if [ -z "$TEST_CONFIG_ID" ]; then
            print_error "Failed to extract Test Config ID from output"
            print_info "Generation may have failed. Check the output above for errors."
            print_info "Saving output to: /tmp/generation_output.log"
            cp "$output_file" /tmp/generation_output.log
            rm -f "$output_file"
            exit 1
        fi
        
        print_status "Test Config ID: ${BOLD}$TEST_CONFIG_ID${NC}"
        rm -f "$output_file"
    else
        print_error "Test configuration generation failed"
        rm -f "$output_file"
        exit 1
    fi
}

# Run dry-run validation
run_dry_run() {
    print_section "Dry-Run Validation (Pre-Flight Check)"
    
    print_info "Validating test configuration and environment..."
    print_info "This will verify:"
    print_info "  • Configuration parameters"
    print_info "  • AWS connectivity and credentials"
    print_info "  • DynamoDB tables"
    print_info "  • API endpoint accessibility"
    print_info "  • Cost estimation"
    print_info "  • Player pool size"
    echo -e "${PREFIX}"
    echo -e "${PREFIX} ${BOLD}${YELLOW}▶ Starting Dry-Run Validation...${NC}"
    echo -e "${PREFIX}"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX} Dry-Run Output:"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX}"
    
    local cmd="python3 \"$TEST_SCRIPT\" --test-config-id \"$TEST_CONFIG_ID\""
    cmd="$cmd --aws-profile \"$AWS_PROFILE\" --region \"$AWS_REGION\" --dry-run"
    
    if eval "$cmd"; then
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX} ${BOLD}${GREEN}✅ Dry-Run Validation: PASSED${NC}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX}"
        print_status "All pre-flight checks passed - ready to execute test"
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${GREEN}→ Proceeding to test execution phase...${NC}"
    else
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX} ${BOLD}${RED}✗ Dry-Run Validation: FAILED${NC}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX}"
        print_error "Pre-flight checks failed - cannot proceed with test execution"
        print_info "Please fix the issues above before running the test"
        print_info "Check the logs in: ${LOG_DIR}/main.log"
        exit 1
    fi
}

# Start test instance
# Args: $1 = player_count, $2 = mode (foreground|background)
start_test_instance() {
    local player_count=$1
    local mode=$2
    
    local cmd="python3 \"$TEST_SCRIPT\" --test-config-id \"$TEST_CONFIG_ID\""
    cmd="$cmd --aws-profile \"$AWS_PROFILE\" --region \"$AWS_REGION\""
    cmd="$cmd --max-players $player_count --duration $((DURATION_MINUTES * 60))"
    
    if [ "$mode" = "foreground" ]; then
        # PRIMARY instance - runs in foreground, shows output
        print_info "Starting PRIMARY instance (foreground)..."
        print_info "  Players: ${BOLD}$player_count${NC}"
        print_info "  Output: Console (visible)"
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX} ${GREEN}PRIMARY Instance Output (Python):${NC}"
        echo -e "${PREFIX} ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX}"
        
        # Run in background but don't redirect output - let it flow to console
        eval "$cmd" &
        PRIMARY_PID=$!
        ALL_PIDS+=("$PRIMARY_PID")
        
        # Give it a moment to start
        sleep 2
        
        # Check if process is still running
        if ! kill -0 "$PRIMARY_PID" 2>/dev/null; then
            print_error "PRIMARY instance failed to start or crashed immediately"
            return 1
        fi
        
        print_status "PRIMARY instance started (PID: $PRIMARY_PID)"
        echo -e "${PREFIX}"
        
        return 0
    else
        # WORKER instance - runs in background, silent
        print_info "Starting WORKER instance (background)..."
        print_info "  Players: ${BOLD}$player_count${NC}"
        print_info "  Output: Logs only (silent)"
        
        # Redirect all output to /dev/null for background instances
        eval "$cmd" > /dev/null 2>&1 &
        local worker_pid=$!
        WORKER_PIDS+=("$worker_pid")
        ALL_PIDS+=("$worker_pid")
        
        print_status "WORKER instance started (PID: $worker_pid)"
        
        # Give it a moment to start
        sleep 1
        
        # Check if process is still running
        if ! kill -0 "$worker_pid" 2>/dev/null; then
            print_error "WORKER instance failed to start or crashed immediately"
            return 1
        fi
        
        return 0
    fi
}

# Wait for "Spawning worker threads..." message in process output
# Args: $1 = PID to monitor, $2 = mode ("foreground" or "background"), $3 = timeout in seconds (default: 300)
wait_for_spawn_message() {
    local pid=$1
    local mode=$2
    local timeout=${3:-300}
    
    print_info "Waiting for instance (PID: $pid, mode: $mode) to reach 'Spawning worker threads...' checkpoint"
    print_info "  Timeout: ${timeout}s"
    
    local start_time=$(date +%s)
    local found=false
    
    if [ "$mode" = "foreground" ]; then
        # For PRIMARY (foreground) process: Monitor console output via /proc
        # This is immediate and doesn't rely on log file buffering
        while true; do
            local current_time=$(date +%s)
            local elapsed=$((current_time - start_time))
            
            # Check timeout
            if [ "$elapsed" -ge "$timeout" ]; then
                print_error "Timeout waiting for 'Spawning worker threads...' message"
                return 1
            fi
            
            # Check if process is still running
            if ! kill -0 "$pid" 2>/dev/null; then
                print_error "Process (PID: $pid) terminated before reaching checkpoint"
                return 1
            fi
            
            # For foreground process, we rely on the fact that the message will appear
            # in the console output that's visible to the user. We use a simple time-based
            # heuristic: if the process is still running after a reasonable startup time,
            # assume it has reached the checkpoint. This works because:
            # 1. The PRIMARY process prints to console immediately (no buffering)
            # 2. The message appears early in execution (after initialization)
            # 3. If the process crashes, we detect it via kill -0 check above
            
            # Wait for minimum startup time (30 seconds for initialization + lock acquisition)
            if [ "$elapsed" -ge 30 ]; then
                found=true
                break
            fi
            
            # Progress indicator
            echo -ne "\r${PREFIX} ${CYAN}⏱${NC}  Waiting for PRIMARY startup... ${elapsed}s / 30s  "
            
            sleep 2
        done
    else
        # For WORKER (background) processes: Monitor log files
        # Background processes have stdout redirected, so we must use log files
        
        # Wait for log file to be created (Python uses pattern: main-{instance_id}.log)
        local wait_count=0
        local log_pattern="${LOG_DIR}/main-*.log"
        
        while [ "$wait_count" -lt 10 ]; do
            if ls "${log_pattern}" 1> /dev/null 2>&1; then
                break
            fi
            sleep 1
            wait_count=$((wait_count + 1))
        done
        
        # Monitor log files for the spawn message
        while true; do
            local current_time=$(date +%s)
            local elapsed=$((current_time - start_time))
            
            # Check timeout
            if [ "$elapsed" -ge "$timeout" ]; then
                print_error "Timeout waiting for 'Spawning worker threads...' message"
                return 1
            fi
            
            # Check if process is still running
            if ! kill -0 "$pid" 2>/dev/null; then
                print_error "Process (PID: $pid) terminated before reaching checkpoint"
                return 1
            fi
            
            # Search for the spawn message in log files
            if grep -q "Spawning worker threads" "${log_pattern}" 2>/dev/null; then
                found=true
                break
            fi
            
            # Progress indicator
            echo -ne "\r${PREFIX} ${CYAN}⏱${NC}  Waiting... ${elapsed}s / ${timeout}s  "
            
            sleep 2
        done
    fi
    
    if [ "$found" = true ]; then
        echo -e "\n${PREFIX}"
        print_status "Checkpoint reached - instance is spawning workers"
        return 0
    else
        echo -e "\n${PREFIX}"
        print_error "Failed to detect checkpoint message"
        return 1
    fi
}

# Launch scaled instances in steps
launch_scaled_instances() {
    print_section "Launching Scaled Test Instances"
    
    local current_players=$INITIAL_PLAYERS
    local remaining=$((SCALE_TO_PLAYERS - INITIAL_PLAYERS))
    local instance_num=2
    
    # Calculate scale step if not provided
    local step=$SCALE_STEP
    if [ "$step" -eq 0 ]; then
        # Auto-calculate: use remaining players as single step
        step=$remaining
        print_info "Auto-calculated scale step: ${BOLD}${step}${NC} players"
    fi
    
    print_info "Scaling configuration:"
    print_info "  Initial players: ${BOLD}${INITIAL_PLAYERS}${NC} (PRIMARY instance)"
    print_info "  Target players: ${BOLD}${SCALE_TO_PLAYERS}${NC}"
    print_info "  Remaining to add: ${BOLD}${remaining}${NC}"
    print_info "  Scale step: ${BOLD}${step}${NC} players per instance"
    
    # Calculate number of instances needed
    local num_instances=$(( (remaining + step - 1) / step ))  # Ceiling division
    print_info "  Additional instances: ${BOLD}${num_instances}${NC}"
    echo -e "${PREFIX}"
    
    # Wait for PRIMARY to reach checkpoint
    print_info "Waiting for PRIMARY instance to reach checkpoint..."
    if ! wait_for_spawn_message "$PRIMARY_PID" "foreground" 300; then
        print_error "PRIMARY instance failed to reach checkpoint"
        return 1
    fi
    echo -e "${PREFIX}"
    
    # Launch WORKER instances in steps
    while [ $remaining -gt 0 ]; do
        # Calculate batch size for this instance
        local batch_size=$step
        if [ "$batch_size" -gt "$remaining" ]; then
            batch_size=$remaining
        fi
        
        print_info "Launching instance #${instance_num}:"
        print_info "  Batch size: ${BOLD}${batch_size}${NC} players"
        print_info "  Total so far: ${BOLD}$((current_players + batch_size))${NC} / ${SCALE_TO_PLAYERS}"
        echo -e "${PREFIX}"
        
        # Start WORKER instance in background
        if ! start_test_instance "$batch_size" "background"; then
            print_error "Failed to start WORKER instance #${instance_num}"
            return 1
        fi
        
        # Get the last worker PID (most recently added)
        local last_worker_pid="${WORKER_PIDS[${#WORKER_PIDS[@]}-1]}"
        echo -e "${PREFIX}"
        
        # Wait for this instance to reach checkpoint before launching next
        if [ "$remaining" -gt "$batch_size" ]; then
            print_info "Waiting for instance #${instance_num} to reach checkpoint before launching next..."
            if ! wait_for_spawn_message "$last_worker_pid" "background" 300; then
                print_warning "Instance #${instance_num} failed to reach checkpoint, but continuing..."
            fi
            echo -e "${PREFIX}"
        fi
        
        current_players=$((current_players + batch_size))
        remaining=$((remaining - batch_size))
        instance_num=$((instance_num + 1))
    done
    
    print_status "All instances launched successfully!"
    print_info "Total instances: ${BOLD}$((instance_num - 1))${NC}"
    print_info "  PRIMARY: 1 (PID: $PRIMARY_PID)"
    print_info "  WORKERS: ${BOLD}${#WORKER_PIDS[@]}${NC} (PIDs: ${WORKER_PIDS[*]})"
    print_info "Total players: ${BOLD}${SCALE_TO_PLAYERS}${NC}"
    echo -e "${PREFIX}"
    
    return 0
}

# Wait for test completion
wait_for_completion() {
    print_section "Test Execution in Progress"
    
    local total_instances=$((1 + ${#WORKER_PIDS[@]}))
    
    print_info "Running ${BOLD}${total_instances}${NC} test instance(s):"
    print_info "  PRIMARY (PID: $PRIMARY_PID) - ${INITIAL_PLAYERS} players"
    
    if [ ${#WORKER_PIDS[@]} -gt 0 ]; then
        local worker_num=1
        for pid in "${WORKER_PIDS[@]}"; do
            print_info "  WORKER #${worker_num} (PID: $pid)"
            worker_num=$((worker_num + 1))
        done
    fi
    
    print_info "Total players: ${BOLD}${SCALE_TO_PLAYERS}${NC}"
    print_info "Press Ctrl+C to gracefully stop all instances"
    echo -e "${PREFIX}"
    
    # Wait for PRIMARY process (foreground)
    local primary_exit_code=0
    if wait "$PRIMARY_PID" 2>/dev/null; then
        print_status "PRIMARY instance completed successfully"
    else
        primary_exit_code=$?
        if [ $primary_exit_code -eq 130 ]; then
            print_info "PRIMARY instance interrupted by user"
        else
            print_warning "PRIMARY instance exited with code: $primary_exit_code"
        fi
    fi
    
    # Wait for all WORKER processes
    if [ ${#WORKER_PIDS[@]} -gt 0 ]; then
        print_info "Waiting for WORKER instances to complete..."
        
        local worker_num=1
        local all_success=true
        
        for pid in "${WORKER_PIDS[@]}"; do
            if wait "$pid" 2>/dev/null; then
                print_status "WORKER #${worker_num} completed successfully"
            else
                local exit_code=$?
                if [ $exit_code -eq 130 ]; then
                    print_info "WORKER #${worker_num} interrupted by user"
                else
                    print_warning "WORKER #${worker_num} exited with code: $exit_code"
                    all_success=false
                fi
            fi
            worker_num=$((worker_num + 1))
        done
        
        if [ "$all_success" = true ] && [ $primary_exit_code -eq 0 ]; then
            print_status "All test instances completed successfully"
        fi
    fi
}

# Display test results
display_results() {
    print_section "Test Results"
    
    # Find the most recent report
    if [ -d "$REPORTS_DIR" ]; then
        local html_report=$(find "$REPORTS_DIR" -name "test-report-*.html" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
        local json_report=$(find "$REPORTS_DIR" -name "test-report-*.json" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
        local webpage=$(find "$REPORTS_DIR" -name "leaderboard-viewer-*.html" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
        
        if [ -n "$html_report" ]; then
            print_status "HTML Report: ${BOLD}$html_report${NC}"
        fi
        
        if [ -n "$json_report" ]; then
            print_status "JSON Report: ${BOLD}$json_report${NC}"
        fi
        
        if [ -n "$webpage" ]; then
            print_status "Leaderboard Viewer: ${BOLD}$webpage${NC}"
            print_info "Open in browser: file://$webpage"
        fi
    fi
    
    # Find the most recent log directory
    if [ -d "$LOG_DIR" ]; then
        local latest_log=$(find "$LOG_DIR" -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
        if [ -n "$latest_log" ] && [ "$latest_log" != "$LOG_DIR" ]; then
            print_status "Log Directory: ${BOLD}$latest_log${NC}"
        fi
    fi
    
    echo ""
    print_info "Test Config ID: ${BOLD}$TEST_CONFIG_ID${NC}"
    print_info "Use this ID to run additional test instances or resume testing"
}

################################################################################
# Main Execution Flow
################################################################################

main() {
    print_banner
    
    # Handle force unlock if requested
    if [ "$FORCE_UNLOCK" = true ]; then
        print_section "Force Unlock Global Test Lock"
        
        print_info "AWS Profile: $AWS_PROFILE"
        print_info "AWS Region: $AWS_REGION"
        echo ""
        
        local cmd="python3 \"$TEST_SCRIPT\" --force-unlock"
        cmd="$cmd --aws-profile \"$AWS_PROFILE\" --region \"$AWS_REGION\""
        
        print_info "Attempting to force unlock..."
        echo ""
        
        if eval "$cmd"; then
            echo ""
            print_status "Global test lock released successfully"
            exit 0
        else
            print_error "Failed to release global test lock"
            exit 1
        fi
    fi
    
    # Step 1: Environment validation
    check_python
    
    # Step 2: Generate or load test configuration
    if [ "$SKIP_GENERATION" = false ]; then
        generate_config
        
        # Step 3: Run dry-run validation
        run_dry_run
        
        echo -e "${PREFIX}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX} ${BOLD}${YELLOW}PHASE TRANSITION: DRY-RUN → EXECUTION${NC}"
        echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${PREFIX}"
    else
        print_section "Using Existing Test Configuration"
        print_info "Test Config ID: ${BOLD}$TEST_CONFIG_ID${NC}"
        print_info "Skipping generation and dry-run"
    fi
    
    # Step 4: Start PRIMARY test instance (foreground)
    print_section "Starting Test Execution"
    
    print_info "Test Config ID: ${BOLD}$TEST_CONFIG_ID${NC}"
    print_info "Initial Players: ${BOLD}$INITIAL_PLAYERS${NC}"
    print_info "Target Players: ${BOLD}$SCALE_TO_PLAYERS${NC}"
    if [ "$SCALE_STEP" -gt 0 ]; then
        print_info "Scale Step: ${BOLD}$SCALE_STEP${NC} players per instance"
    fi
    print_info "Duration: ${BOLD}${DURATION_MINUTES} minutes${NC}"
    print_info "AWS Profile: $AWS_PROFILE"
    print_info "AWS Region: $AWS_REGION"
    echo -e "${PREFIX}"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX} PRIMARY instance output below (foreground):"
    echo -e "${PREFIX} ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${PREFIX}"
    
    if ! start_test_instance "$INITIAL_PLAYERS" "foreground"; then
        print_error "Failed to start PRIMARY instance"
        exit 1
    fi
    
    echo -e "${PREFIX}"
    
    # Step 5: Launch scaled WORKER instances
    if [ "$NO_SCALE" = false ] && [ "$SCALE_TO_PLAYERS" -gt "$INITIAL_PLAYERS" ]; then
        if ! launch_scaled_instances; then
            print_error "Failed to launch scaled instances"
            print_warning "Continuing with PRIMARY instance only"
        fi
    else
        if [ "$NO_SCALE" = true ]; then
            print_info "Scaling disabled (--no-scale), running with ${INITIAL_PLAYERS} players only"
        else
            print_info "No scaling needed (initial players = target players)"
        fi
        echo -e "${PREFIX}"
    fi
    
    # Step 6: Wait for test completion
    wait_for_completion
    
    # Step 7: Display results
    display_results
    
    echo -e "${PREFIX}"
    print_status "Test orchestration complete!"
    echo -e "${PREFIX}"
}

# Run main function
main
