# CloudWatch Logs Insights Saved Queries for Load Testing Triage

This document contains CloudWatch Logs Insights queries for triaging load test issues.

## Prerequisites

- Log Group: `/loadtest/stats-leaderboards`
- AWS Profile: dev_aws_profile
- Region: us-west-2

## How to Use

1. Open CloudWatch Logs Insights in AWS Console
2. Select log group: `/loadtest/stats-leaderboards`
3. Copy and paste the query
4. Adjust time range as needed
5. Click "Run query"

---

## Query 1: Find All Errors by EventID

**Purpose:** Find all error details for a specific EventID across all log streams

**Use Case:** When you see an error in main logs with an EventID, use this to find full details

```
fields @timestamp, @logStream, @message
| filter @message like /EventID: YOUR_EVENT_ID_HERE/
| sort @timestamp desc
| limit 100
```

**How to use:**
- Replace `YOUR_EVENT_ID_HERE` with actual EventID (e.g., `20260202-225059-b1x7`)
- This searches across ALL log streams (main and workers)

---

## Query 2: Find Worker-Specific Errors

**Purpose:** Find all errors from a specific worker instance

**Use Case:** When investigating issues from a particular worker

```
fields @timestamp, @message
| filter @logStream like /worker-46-instance-30/
| filter @message like /ERROR/ or @message like /WARNING/
| sort @timestamp desc
| limit 100
```

**How to use:**
- Replace `worker-46-instance-30` with your worker log stream name
- Shows all ERROR and WARNING messages from that worker

---

## Query 3: API Error Summary by Status Code

**Purpose:** Count errors by HTTP status code to identify patterns

**Use Case:** Understanding what types of errors are occurring most frequently

```
fields @timestamp, @message
| filter @message like /API Error/
| parse @message /returned (?<status_code>\d+)/
| stats count() by status_code
| sort count desc
```

**Output:** Shows count of each status code (429, 500, 404, etc.)

---

## Query 4: High Latency API Calls

**Purpose:** Find API calls that took longer than 2500ms

**Use Case:** Identifying performance bottlenecks

```
fields @timestamp, @logStream, @message
| filter @message like /High latency/
| parse @message /took (?<latency>\d+)ms/
| sort latency desc
| limit 50
```

**Output:** Shows slowest API calls with latency

---

## Query 5: Errors in Specific Time Window

**Purpose:** Find all errors that occurred within a specific minute

**Use Case:** When you know the approximate time of an incident

```
fields @timestamp, @logStream, @message
| filter @message like /ERROR/
| filter @timestamp >= "2026-02-02T22:50:00" and @timestamp <= "2026-02-02T22:51:00"
| sort @timestamp asc
| limit 200
```

**How to use:**
- Replace timestamps with your incident time window
- Use ISO 8601 format: `YYYY-MM-DDTHH:MM:SS`

---

## Query 6: Throttling Events (429 errors)

**Purpose:** Find all throttling events to understand API Gateway limits

**Use Case:** Diagnosing rate limiting issues

```
fields @timestamp, @logStream, @message
| filter @message like /429/ or @message like /throttled/
| parse @message /\[Worker-(?<worker_id>\d+)\]/
| stats count() by worker_id
| sort count desc
```

**Output:** Shows which workers experienced the most throttling

---

## Query 7: Validation Failures

**Purpose:** Find consistency validation failures

**Use Case:** Identifying data consistency issues

```
fields @timestamp, @logStream, @message
| filter @message like /VALIDATION FAILURE/
| sort @timestamp desc
| limit 100
```

**Output:** Shows all validation failures where data consistency checks failed

---

## Query 8: Cross-Reference Main to Worker Logs

**Purpose:** Find the worker log stream for a specific error from main log

**Use Case:** Following the cross-reference link from main log to worker log

```
fields @timestamp, @message
| filter @logStream like /worker-46-instance-30/
| filter @message like /EventID: 20260202-225059-b1x7/
| sort @timestamp desc
| limit 10
```

**How to use:**
- Replace `worker-46-instance-30` with the log stream from error message
- Replace EventID with the one from the error
- This shows the full API call details

---

## Query 9: Error Rate Over Time

**Purpose:** Visualize error rate over time to identify spikes

**Use Case:** Understanding when errors started occurring

```
fields @timestamp
| filter @message like /ERROR/
| stats count() by bin(5m)
```

**Output:** Shows error count in 5-minute buckets (adjust bin size as needed)

---

## Query 10: Worker Activity Summary

**Purpose:** See which workers are most active and their error rates

**Use Case:** Load distribution analysis

```
fields @timestamp, @message
| parse @message /\[Worker-(?<worker_id>\d+)\]/
| parse @message /\[PlayerWorker-(?<player_worker_id>\d+)\]/
| stats count() as total_events, 
        count(@message like /ERROR/) as errors,
        count(@message like /WARNING/) as warnings
  by coalesce(worker_id, player_worker_id) as worker
| sort total_events desc
```

**Output:** Shows activity and error counts per worker

---

## Query 11: Find All Logs for Specific Player

**Purpose:** Track all API calls for a specific player ID

**Use Case:** Debugging player-specific issues

```
fields @timestamp, @logStream, @message
| filter @message like /player_000123/
| sort @timestamp asc
| limit 100
```

**How to use:**
- Replace `player_000123` with actual player ID
- Shows chronological sequence of all API calls for that player

---

## Query 12: Network and Timeout Errors

**Purpose:** Find network connectivity and timeout issues

**Use Case:** Diagnosing infrastructure problems

```
fields @timestamp, @logStream, @message
| filter @message like /timeout/ or @message like /network error/
| sort @timestamp desc
| limit 100
```

**Output:** Shows all timeout and network-related errors

---

## Saving Queries in AWS Console

To save these queries in CloudWatch Logs Insights:

1. Open CloudWatch Console → Logs → Insights
2. Select log group: `/loadtest/stats-leaderboards`
3. Paste a query from above
4. Click "Save" button (top right)
5. Give it a descriptive name (e.g., "Find Errors by EventID")
6. Add to folder: "Load Testing Triage"

## Quick Triage Workflow

When investigating an error:

1. **Start with main logs** - Look for ERROR messages with EventID
2. **Use Query 1** - Search for that EventID across all logs
3. **Use Query 8** - Follow cross-reference to worker log stream
4. **Use Query 3** - Check if it's a pattern (multiple status codes)
5. **Use Query 5** - Look at time window around the error
6. **Check X-Ray** - Use EventID as filter in X-Ray console

## X-Ray Console Filters

In X-Ray console, you can filter traces using annotations:

- `annotation.event_id = "20260202-225059-b1x7"`
- `annotation.worker_id = "46"`
- `annotation.api_type = "storePlayerStatsAndScores"`
- `annotation.test_config_id = "test-20260202-130955-hgwc"`
- `http.status = 500`

Combine filters for precise trace discovery:
```
annotation.event_id = "20260202-225059-b1x7" AND http.status >= 400
```
