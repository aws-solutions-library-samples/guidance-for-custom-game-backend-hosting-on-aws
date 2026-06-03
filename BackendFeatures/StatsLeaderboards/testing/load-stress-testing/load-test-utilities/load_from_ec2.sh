#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Configuration — edit these
TEST_CONFIG_ID="test-20260220-160546-c3pm"
REGION="us-west-2"
MAX_PLAYERS=108
NUM_PROCESSES=10
DELAY_BETWEEN=360                           # seconds between each process launch
SLEEP_FOR_PROCESS=90                        # seconds to wait before reporting the process that was started last
                                            # between these two, we sleep a total of 450 seconds (7.5 mins) between two executions

mkdir -p testlogs

for i in $(seq 1 "$NUM_PROCESSES"); do
  echo ""
  LOGFILE="testlogs/launcher-process-${i}.log"
  echo "[$(date '+%H:%M:%S')] Starting process $i of $NUM_PROCESSES (log: $LOGFILE)..."
  nohup python3 -u test_LoadAndStressTests.py \
    --test-config-id "$TEST_CONFIG_ID" \
    --region "$REGION" \
    --max-players "$MAX_PLAYERS" > "$LOGFILE" 2> >(tee -a "$LOGFILE" >&2) &
  sleep "$SLEEP_FOR_PROCESS"
  echo "Process $i started (PID: $!)"
  if [ "$i" -lt "$NUM_PROCESSES" ]; then
    echo "Sleeping ${DELAY_BETWEEN}s before next..."
    sleep "$DELAY_BETWEEN"
  fi
done
echo ""
echo "All $NUM_PROCESSES processes launched."
