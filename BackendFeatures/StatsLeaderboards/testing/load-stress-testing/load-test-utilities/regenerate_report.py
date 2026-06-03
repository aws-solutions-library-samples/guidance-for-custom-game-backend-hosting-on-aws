#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Regenerate HTML report for a completed test run.

Auto-discovers the latest (or specified) test run from DynamoDB and the JSON report.
Uses UTC-aware CloudWatch queries, instance enrichment from metrics table,
aggregate RPS from DynamoDB, and live leaderboard queries.

Usage:
    python3 regenerate_report.py                                    # Latest test run
    python3 regenerate_report.py --test-config-id test-20260220-... # Specific test
    python3 regenerate_report.py --profile myprofile --region eu-west-1
"""

import boto3
import json
import time
import requests
import traceback
import argparse
import sys
import glob
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# Table names
TEST_DEFINITIONS_TABLE = 'game-StatsLeaderboards-LoadTestDefinitions'
TEST_METRICS_TABLE = 'game-StatsLeaderboards-LoadTestMetrics'
PLAYER_STATE_TABLE = 'game-StatsLeaderboards-LoadTestPlayerState'
PLAYERS_PER_THREAD = 3

def discover_test_run(session, region, test_config_id=None):
    """
    Auto-discover test run parameters from DynamoDB METADATA or JSON report.
    If test_config_id is None, finds the most recent test run.
    Returns dict with all needed parameters.
    """
    dynamodb = session.resource('dynamodb', region_name=region)
    ddb_client = session.client('dynamodb', region_name=region)
    def_table = dynamodb.Table(TEST_DEFINITIONS_TABLE)
    
    if test_config_id:
        print(f"🔍 Looking up test: {test_config_id}")
    else:
        # Find the most recent test by scanning METADATA records
        print(f"🔍 Finding most recent test run...")
        from boto3.dynamodb.conditions import Key
        
        # Scan for METADATA records and find the most recent
        resp = ddb_client.scan(
            TableName=TEST_DEFINITIONS_TABLE,
            FilterExpression='ConfigCategory = :cat',
            ExpressionAttributeValues={':cat': {'S': 'METADATA'}},
            ProjectionExpression='TestConfigID,LastModified,TestName,#s',
            ExpressionAttributeNames={'#s': 'Status'}
        )
        
        items = resp.get('Items', [])
        if not items:
            print("❌ No test runs found in DynamoDB")
            sys.exit(1)
        
        # Sort by LastModified descending
        items.sort(key=lambda x: float(x.get('LastModified', {}).get('N', '0')), reverse=True)
        latest = items[0]
        test_config_id = latest['TestConfigID']['S']
        print(f"   Found: {test_config_id} (status: {latest.get('Status', {}).get('S', 'unknown')})")
    
    # Get METADATA record
    resp = def_table.get_item(Key={'TestConfigID': test_config_id, 'ConfigCategory': 'METADATA'})
    metadata = resp.get('Item', {})
    
    if not metadata:
        print(f"❌ Test config {test_config_id} not found")
        sys.exit(1)
    
    test_name = metadata.get('TestName', 'Unknown')
    
    # Try to find the JSON report for PRIMARY metrics
    reports_dir = Path(__file__).parent / 'reports'
    json_reports = sorted(reports_dir.glob(f'test_report_{test_config_id}_*.json'), reverse=True)
    
    primary_metrics = None
    start_time = None
    end_time = None
    primary_max_pw = 0
    primary_max_bw = 0
    max_players = 100
    
    if json_reports:
        print(f"   Found JSON report: {json_reports[0].name}")
        with open(json_reports[0]) as f:
            report_data = json.load(f)
        
        start_time = report_data.get('start_time')
        end_time = report_data.get('end_time')
        max_players = report_data.get('configuration', {}).get('max_players', 100)
        
        if report_data.get('metrics'):
            m = report_data['metrics']
            primary_metrics = {
                'rps': m.get('rps', 0), 'success_rate': m.get('success_rate', 0),
                'avg_latency': m.get('avg_latency', 0), 'p50_latency': m.get('p50_latency', 0),
                'p95_latency': m.get('p95_latency', 0), 'p99_latency': m.get('p99_latency', 0),
                'total_requests': m.get('total_requests', 0),
            }
        
        wt = report_data.get('worker_threads', {})
        primary_max_pw = wt.get('player', 0)
        primary_max_bw = wt.get('batch', 0)
    
    # If no JSON report, estimate times from metrics table
    if not start_time or not end_time:
        print("   No JSON report found — estimating times from DynamoDB metrics...")
        metrics_table = dynamodb.Table(TEST_METRICS_TABLE)
        from boto3.dynamodb.conditions import Key, Attr
        
        # Get earliest and latest metric timestamps
        resp = metrics_table.query(
            KeyConditionExpression=Key('TestConfigID').eq(test_config_id),
            FilterExpression=Attr('MetricType').eq('INSTANCE_METRICS'),
            Limit=1, ScanIndexForward=True
        )
        if resp.get('Items'):
            start_time = float(resp['Items'][0].get('MetricTimestamp', 0))
        
        resp = metrics_table.query(
            KeyConditionExpression=Key('TestConfigID').eq(test_config_id),
            FilterExpression=Attr('MetricType').eq('INSTANCE_METRICS'),
            Limit=1, ScanIndexForward=False
        )
        if resp.get('Items'):
            end_time = float(resp['Items'][0].get('MetricTimestamp', 0))
    
    if not start_time or not end_time:
        print("❌ Could not determine test start/end times")
        sys.exit(1)
    
    print(f"   Test: {test_name}")
    print(f"   Period: {datetime.fromtimestamp(start_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} → "
          f"{datetime.fromtimestamp(end_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"   Duration: {(end_time - start_time) / 3600:.1f} hours")
    
    return {
        'test_config_id': test_config_id,
        'test_name': test_name,
        'start_time': start_time,
        'end_time': end_time,
        'max_players': max_players,
        'primary_metrics': primary_metrics,
        'primary_max_pw': primary_max_pw,
        'primary_max_bw': primary_max_bw,
    }


# ============================================================
# Parse CLI args
# ============================================================
parser = argparse.ArgumentParser(description='Regenerate HTML test report')
parser.add_argument('--test-config-id', default=None, help='Test config ID (default: latest)')
parser.add_argument('--profile', default='default', help='AWS profile')
parser.add_argument('--region', default='us-west-2', help='AWS region')
args = parser.parse_args()

REGION = args.region
AWS_PROFILE = args.profile

session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
cloudwatch = session.client('cloudwatch', region_name=REGION)
dynamodb = session.resource('dynamodb', region_name=REGION)

# Discover test run
test_info = discover_test_run(session, REGION, args.test_config_id)
TEST_CONFIG_ID = test_info['test_config_id']
TEST_NAME = test_info['test_name']
START_TIME = test_info['start_time']
END_TIME = test_info['end_time']
MAX_PLAYERS_PER_INSTANCE = test_info['max_players']
PRIMARY_METRICS = test_info['primary_metrics'] or {
    'rps': 0, 'success_rate': 0, 'avg_latency': 0, 'p50_latency': 0,
    'p95_latency': 0, 'p99_latency': 0, 'total_requests': 0,
}
PRIMARY_MAX_PW = test_info['primary_max_pw']
PRIMARY_MAX_BW = test_info['primary_max_bw']
PRIMARY_PEAK_PLAYERS = PRIMARY_MAX_PW * PLAYERS_PER_THREAD
GENRE = 'RPG Action'


# ============================================================
# 1. CloudWatch metrics (UTC-aware)
# ============================================================
print("=" * 60)
print("1. Collecting CloudWatch metrics...")
start_dt = datetime.fromtimestamp(START_TIME, tz=timezone.utc) - timedelta(minutes=5)
end_dt = datetime.fromtimestamp(END_TIME, tz=timezone.utc) + timedelta(minutes=10)
print(f"   Range: {start_dt.isoformat()} → {end_dt.isoformat()}")

lambda_functions = [
    'game-statsleaderboards-dev-backend-authorizer',
    'game-statsleaderboards-dev-batch-store-stats',
    'game-statsleaderboards-dev-developer-registration',
    'game-statsleaderboards-dev-get-leaderboard-scores',
    'game-statsleaderboards-dev-get-player-lb-standing',
    'game-statsleaderboards-dev-get-player-stats',
    'game-statsleaderboards-dev-leaderboards-config',
    'game-statsleaderboards-dev-rebuild-leaderboard',
    'game-statsleaderboards-dev-reset-leaderboard',
    'game-statsleaderboards-dev-store-stats'
]

lambda_metrics = {}
for fn in lambda_functions:
    fm = {}
    try:
        for mn, st in [('Invocations','Sum'),('Errors','Sum'),('Throttles','Sum')]:
            r = cloudwatch.get_metric_statistics(Namespace='AWS/Lambda',MetricName=mn,Dimensions=[{'Name':'FunctionName','Value':fn}],StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=[st])
            fm[mn.lower()] = sum(d[st] for d in r['Datapoints'])
        r = cloudwatch.get_metric_statistics(Namespace='AWS/Lambda',MetricName='Duration',Dimensions=[{'Name':'FunctionName','Value':fn}],StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=['Average','Maximum'])
        fm['avg_duration_ms'] = sum(d['Average'] for d in r['Datapoints'])/len(r['Datapoints']) if r['Datapoints'] else 0
        fm['max_duration_ms'] = max((d['Maximum'] for d in r['Datapoints']), default=0)
        r = cloudwatch.get_metric_statistics(Namespace='AWS/Lambda',MetricName='ConcurrentExecutions',Dimensions=[{'Name':'FunctionName','Value':fn}],StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=['Average','Maximum'])
        fm['avg_concurrency'] = sum(d['Average'] for d in r['Datapoints'])/len(r['Datapoints']) if r['Datapoints'] else 0
        fm['max_concurrency'] = max((d['Maximum'] for d in r['Datapoints']), default=0)
    except Exception as e:
        print(f"   ⚠ {fn}: {e}")
    lambda_metrics[fn] = fm

print(f"   Lambda invocations: {sum(m.get('invocations',0) for m in lambda_metrics.values()):,.0f}")

api_name, api_stage = 'game-statsleaderboards-dev-restapi', 'dev'
apigw = {}
try:
    dims = [{'Name':'ApiName','Value':api_name},{'Name':'Stage','Value':api_stage}]
    for mn,st,k in [('Count','Sum','total_requests'),('4XXError','Sum','4xx_errors'),('5XXError','Sum','5xx_errors')]:
        r = cloudwatch.get_metric_statistics(Namespace='AWS/ApiGateway',MetricName=mn,Dimensions=dims,StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=[st])
        apigw[k] = sum(d[st] for d in r['Datapoints'])
    r = cloudwatch.get_metric_statistics(Namespace='AWS/ApiGateway',MetricName='Latency',Dimensions=dims,StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=['Average','Maximum'],ExtendedStatistics=['p99'])
    if r['Datapoints']:
        apigw['avg_latency_ms'] = sum(d['Average'] for d in r['Datapoints'])/len(r['Datapoints'])
        apigw['max_latency_ms'] = max(d['Maximum'] for d in r['Datapoints'])
        p99_values = [d.get('ExtendedStatistics',{}).get('p99',0) for d in r['Datapoints']]
        apigw['p99_avg_ms'] = sum(p99_values)/len(p99_values) if p99_values else 0
        apigw['p99_worst_ms'] = max(p99_values) if p99_values else 0
    r = cloudwatch.get_metric_statistics(Namespace='AWS/ApiGateway',MetricName='Count',Dimensions=dims,StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Sum'])
    apigw['peak_rps'] = max((d['Sum']/60.0 for d in r['Datapoints']), default=0)
except Exception as e:
    print(f"   ⚠ APIGW: {e}")

print(f"   APIGW requests: {apigw.get('total_requests',0):,.0f}, peak RPS: {apigw.get('peak_rps',0):.1f}")

# Collect time-series data for charts (1-minute granularity)
print("   Collecting time-series for charts...")
chart_data = {'timestamps': [], 'apigw_rps': [], 'apigw_latency': [], 'lambda_concurrency': [], 'ddb_read': [], 'ddb_write': []}
try:
    # API Gateway RPS (Count per minute / 60)
    r = cloudwatch.get_metric_statistics(Namespace='AWS/ApiGateway',MetricName='Count',
        Dimensions=[{'Name':'ApiName','Value':api_name},{'Name':'Stage','Value':api_stage}],
        StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Sum'])
    rps_points = {d['Timestamp']: d['Sum']/60.0 for d in r['Datapoints']}

    # API Gateway Latency
    r = cloudwatch.get_metric_statistics(Namespace='AWS/ApiGateway',MetricName='Latency',
        Dimensions=[{'Name':'ApiName','Value':api_name},{'Name':'Stage','Value':api_stage}],
        StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Average'])
    lat_points = {d['Timestamp']: d['Average'] for d in r['Datapoints']}

    # Lambda total concurrent executions (sum across all functions — use account-level)
    r = cloudwatch.get_metric_statistics(Namespace='AWS/Lambda',MetricName='ConcurrentExecutions',
        Dimensions=[],StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Maximum'])
    conc_points = {d['Timestamp']: d['Maximum'] for d in r['Datapoints']}

    # DynamoDB consumed capacity (stats table — the busiest)
    r = cloudwatch.get_metric_statistics(Namespace='AWS/DynamoDB',MetricName='ConsumedReadCapacityUnits',
        Dimensions=[{'Name':'TableName','Value':'game-statsleaderboards-dev-stats'}],
        StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Sum'])
    ddb_r_points = {d['Timestamp']: d['Sum']/60.0 for d in r['Datapoints']}  # per-second

    r = cloudwatch.get_metric_statistics(Namespace='AWS/DynamoDB',MetricName='ConsumedWriteCapacityUnits',
        Dimensions=[{'Name':'TableName','Value':'game-statsleaderboards-dev-stats'}],
        StartTime=start_dt,EndTime=end_dt,Period=60,Statistics=['Sum'])
    ddb_w_points = {d['Timestamp']: d['Sum']/60.0 for d in r['Datapoints']}

    # Merge all timestamps and sort
    all_ts = sorted(set(list(rps_points.keys()) + list(lat_points.keys()) + list(conc_points.keys()) + list(ddb_r_points.keys()) + list(ddb_w_points.keys())))
    for ts in all_ts:
        chart_data['timestamps'].append(ts.strftime('%H:%M'))
        chart_data['apigw_rps'].append(round(rps_points.get(ts, 0), 1))
        chart_data['apigw_latency'].append(round(lat_points.get(ts, 0), 1))
        chart_data['lambda_concurrency'].append(round(conc_points.get(ts, 0), 0))
        chart_data['ddb_read'].append(round(ddb_r_points.get(ts, 0), 1))
        chart_data['ddb_write'].append(round(ddb_w_points.get(ts, 0), 1))

    print(f"   Chart data: {len(chart_data['timestamps'])} data points")
except Exception as e:
    print(f"   ⚠ Chart data collection: {e}")

ddb_tables = ['game-statsleaderboards-dev-config','game-statsleaderboards-dev-stats']
ddb_cw = {}
for tn in ddb_tables:
    tm = {}
    try:
        for mn,pfx in [('ConsumedReadCapacityUnits','read'),('ConsumedWriteCapacityUnits','write')]:
            r = cloudwatch.get_metric_statistics(Namespace='AWS/DynamoDB',MetricName=mn,Dimensions=[{'Name':'TableName','Value':tn}],StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=['Sum','Average'])
            tm[f'total_{pfx}_capacity'] = sum(d['Sum'] for d in r['Datapoints']) if r['Datapoints'] else 0
            tm[f'avg_{pfx}_capacity'] = (sum(d['Average'] for d in r['Datapoints'])/len(r['Datapoints'])) if r['Datapoints'] else 0
        for mn,k in [('UserErrors','user_errors'),('SystemErrors','system_errors'),('ThrottledRequests','throttled_requests')]:
            r = cloudwatch.get_metric_statistics(Namespace='AWS/DynamoDB',MetricName=mn,Dimensions=[{'Name':'TableName','Value':tn}],StartTime=start_dt,EndTime=end_dt,Period=300,Statistics=['Sum'])
            tm[k] = sum(d['Sum'] for d in r['Datapoints'])
    except Exception as e:
        print(f"   ⚠ {tn}: {e}")
    ddb_cw[tn] = tm


# ============================================================
# 2. DynamoDB instance metrics (ALL instances, not just INSTANCE# records)
# ============================================================
print("\n2. Collecting DynamoDB instance metrics...")
from boto3.dynamodb.conditions import Key, Attr

metrics_table = dynamodb.Table(TEST_METRICS_TABLE)
resp = metrics_table.query(
    KeyConditionExpression=Key('TestConfigID').eq(TEST_CONFIG_ID) & Key('MetricTimestamp').between(int(START_TIME), int(END_TIME)),
    FilterExpression=Attr('MetricType').eq('INSTANCE_METRICS')
)
all_metrics = resp.get('Items', [])
while 'LastEvaluatedKey' in resp:
    resp = metrics_table.query(
        KeyConditionExpression=Key('TestConfigID').eq(TEST_CONFIG_ID) & Key('MetricTimestamp').between(int(START_TIME), int(END_TIME)),
        FilterExpression=Attr('MetricType').eq('INSTANCE_METRICS'),
        ExclusiveStartKey=resp['LastEvaluatedKey']
    )
    all_metrics.extend(resp.get('Items', []))

print(f"   {len(all_metrics)} metric records")

# Group by instance
raw_by_inst = {}
for m in all_metrics:
    iid = str(m.get('InstanceID', 'unknown'))
    raw_by_inst.setdefault(iid, []).append(m)

# Build instance list from METRICS (authoritative source, not INSTANCE# records)
instances = []
sum_total_reqs = 0
sum_peak_players = 0
sum_peak_pw = 0
sum_peak_bw = 0
for iid in sorted(raw_by_inst.keys(), key=lambda x: int(x.replace('instance-','')) if x.startswith('instance-') else 999):
    recs = raw_by_inst[iid]
    latest = max(recs, key=lambda m: int(m.get('MetricTimestamp',0) or 0))
    mp = max(int(m.get('ActivePlayers',0) or 0) for m in recs)
    pw = max(int(m.get('PlayerWorkers',0) or 0) for m in recs)
    bw = max(int(m.get('BatchWorkers',0) or 0) for m in recs)
    tr = int(latest.get('TotalRequests',0) or 0)
    sr = float(latest.get('SuccessRate',1.0) or 1.0)
    instances.append({'id':iid, 'max_players':mp, 'workers':pw, 'batch':bw, 'total_reqs':tr, 'success':int(tr*sr), 'failed':tr-int(tr*sr), 'records':len(recs)})
    sum_total_reqs += tr
    sum_peak_players += mp
    sum_peak_pw += pw
    sum_peak_bw += bw

# Add PRIMARY (instance-1, not in DynamoDB metrics)
sum_peak_players += PRIMARY_PEAK_PLAYERS
sum_peak_pw += PRIMARY_MAX_PW
sum_peak_bw += PRIMARY_MAX_BW
total_instances = len(instances) + 1  # +1 for PRIMARY

# Aggregate RPS from timestamps
by_ts = {}
for m in all_metrics:
    ts = int(m.get('MetricTimestamp',0) or 0)
    by_ts.setdefault(ts, []).append(float(m.get('RPS',0) or 0))
agg_rps = [sum(v) for v in by_ts.values() if v]
# Add PRIMARY's RPS contribution to each sample
agg_rps_with_primary = [r + PRIMARY_METRICS['rps'] for r in agg_rps] if agg_rps else []
avg_agg_rps = sum(agg_rps_with_primary)/len(agg_rps_with_primary) if agg_rps_with_primary else 0
peak_agg_rps = max(agg_rps_with_primary) if agg_rps_with_primary else 0

cw_peak = apigw.get('peak_rps',0)
final_peak_rps = max(cw_peak, peak_agg_rps)
peak_rps_src = 'CloudWatch' if cw_peak >= peak_agg_rps else 'DynamoDB aggregate'

print(f"   Instances: {total_instances} (45 WORKER + 1 PRIMARY)")
print(f"   Peak players: {sum_peak_players:,}, Peak PW: {sum_peak_pw}, Peak BW: {sum_peak_bw}")
print(f"   Total requests (all workers): {sum_total_reqs:,.0f}")
print(f"   Aggregate avg RPS: {avg_agg_rps:.1f}, Peak: {final_peak_rps:.1f} ({peak_rps_src})")


# ============================================================
# 3. Leaderboard config + event schedule from DynamoDB
# ============================================================
print("\n3. Collecting leaderboard & event config...")
def_table = dynamodb.Table(TEST_DEFINITIONS_TABLE)

lb_resp = def_table.get_item(Key={'TestConfigID': TEST_CONFIG_ID, 'ConfigCategory': 'LEADERBOARD_CONFIG'})
lb_config = lb_resp.get('Item', {}).get('ConfigData', {})
leaderboards = lb_config.get('leaderboards', [])

ev_resp = def_table.get_item(Key={'TestConfigID': TEST_CONFIG_ID, 'ConfigCategory': 'EVENT_SCHEDULE'})
ev_config = ev_resp.get('Item', {}).get('ConfigData', {})
events = ev_config.get('events', [])

# Also check LEADERBOARD_CREATION for event leaderboards that were created
lbc_resp = def_table.get_item(Key={'TestConfigID': TEST_CONFIG_ID, 'ConfigCategory': 'LEADERBOARD_CREATION'})
lb_creation = lbc_resp.get('Item', {}).get('ConfigData', {})
created_lbs = lb_creation.get('created_leaderboards', lb_creation.get('leaderboards', []))

print(f"   Permanent leaderboards: {len(leaderboards)}")
print(f"   Events: {len(events)}")
print(f"   Created leaderboards (incl events): {len(created_lbs) if isinstance(created_lbs, list) else 'dict'}")

# Get API config for live queries
infra_resp = def_table.get_item(Key={'TestConfigID': TEST_CONFIG_ID, 'ConfigCategory': 'METADATA'})
metadata = infra_resp.get('Item', {})
# API config is stored in the config loaded at runtime; get from METADATA
api_base_url = metadata.get('ApiBaseUrl', 'https://uukvtdz1ee.execute-api.us-west-2.amazonaws.com/dev')
api_key = metadata.get('ApiKey', '')
game_id = metadata.get('GameId', '')

# If not in METADATA, try loading from the test config
if not api_key:
    # Query all config categories to find API credentials
    cfg_resp = def_table.query(KeyConditionExpression=Key('TestConfigID').eq(TEST_CONFIG_ID))
    for item in cfg_resp.get('Items', []):
        cd = item.get('ConfigData', {})
        if cd.get('ApiKey'):
            api_key = cd['ApiKey']
            api_base_url = cd.get('ApiBaseUrl', api_base_url)
            game_id = cd.get('GameId', game_id)
            break

print(f"   API: {api_base_url}")
print(f"   Game ID: {game_id}")
print(f"   API Key: {'[found]' if api_key else '[NOT FOUND]'}")


# ============================================================
# 4. Query live leaderboard standings
# ============================================================
print("\n4. Querying live leaderboard standings...")

# Build list of all leaderboards to query (permanent + event)
all_lb_names = []
lb_meta = {}  # lb_name -> {gameMode, scoreStrategy, ...}

for lb in leaderboards:
    name = lb.get('leaderboardName', '')
    mode = lb.get('gameMode', '')
    if name:
        all_lb_names.append(name)
        lb_meta[name] = {'gameMode': mode, 'scoreStrategy': lb.get('scoreStrategy',''), 'type': 'permanent'}

# Add event leaderboards
for ev in events:
    lb_cfg = ev.get('leaderboardConfig', {})
    lb_name = lb_cfg.get('leaderboardName', ev.get('leaderboardName', ev.get('eventLeaderboardName', '')))
    mode = ev.get('targetGameMode', lb_cfg.get('gameMode', ''))
    if lb_name and lb_name not in lb_meta:
        all_lb_names.append(lb_name)
        lb_meta[lb_name] = {'gameMode': mode, 'scoreStrategy': lb_cfg.get('scoreStrategy', 'event'), 'type': 'event',
                            'eventName': ev.get('eventName', ''),
                            'eventType': ev.get('eventType', ''),
                            'startTime': ev.get('startTime', 0),
                            'endTime': ev.get('endTime', 0)}

# Also scan created_lbs for any event leaderboards not in the above
if isinstance(created_lbs, list):
    for clb in created_lbs:
        name = clb.get('leaderboardName', '') if isinstance(clb, dict) else str(clb)
        if name and name not in lb_meta:
            all_lb_names.append(name)
            lb_meta[name] = {'gameMode': clb.get('gameMode', '') if isinstance(clb, dict) else '', 'type': 'event-created'}

print(f"   Total leaderboards to query: {len(all_lb_names)}")

lb_scores = {}  # lb_name -> {scores: [...], totalPlayers: N}
headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}

for lb_name in all_lb_names:
    mode = lb_meta[lb_name].get('gameMode', '')
    try:
        body = {"leaderboardScoresRequest": {
            "gameID": game_id, "gameMode": mode,
            "leaderboardName": lb_name, "queryType": "top", "limit": 25
        }}
        r = requests.post(f"{api_base_url}/leaderboards/scores", json=body, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json().get('leaderboardScoresResponse', {})
            scores = data.get('scores', [])
            total = data.get('metadata', {}).get('totalPlayers', len(scores))
            lb_scores[lb_name] = {'scores': scores, 'totalPlayers': total}
            print(f"   ✓ {lb_name}: {total} players, top {len(scores)} retrieved")
        else:
            print(f"   ✗ {lb_name}: HTTP {r.status_code}")
            lb_scores[lb_name] = {'scores': [], 'totalPlayers': 0}
    except Exception as e:
        print(f"   ✗ {lb_name}: {e}")
        lb_scores[lb_name] = {'scores': [], 'totalPlayers': 0}


# ============================================================
# 5. Generate HTML report
# ============================================================
print("\n5. Generating HTML report...")

# Download Chart.js for offline embedding
print("   Fetching Chart.js for inline embedding...")
chartjs_inline = ""
try:
    r = requests.get("https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js", timeout=15)
    if r.status_code == 200:
        chartjs_inline = r.text
        print(f"   ✓ Chart.js fetched ({len(chartjs_inline)//1024} KB)")
    else:
        print(f"   ⚠ Chart.js fetch failed (HTTP {r.status_code}), charts will not render offline")
except Exception as e:
    print(f"   ⚠ Chart.js fetch failed ({e}), charts will not render offline")

dur_mins = (END_TIME - START_TIME) / 60
dur_hrs = dur_mins / 60
gen_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
test_start_str = datetime.fromtimestamp(START_TIME, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
test_end_str = datetime.fromtimestamp(END_TIME, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

# Total requests across ALL instances
grand_total_reqs = sum_total_reqs + PRIMARY_METRICS['total_requests']

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Load Test Report — {TEST_CONFIG_ID}</title>
<script>{chartjs_inline if chartjs_inline else '/* Chart.js not available - fetch failed */'}</script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }}
.header h1 {{ margin: 0 0 10px 0; }} .header p {{ margin: 5px 0; opacity: 0.9; }}
.section {{ background: white; padding: 25px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.section h2 {{ margin-top: 0; color: #667eea; border-bottom: 2px solid #667eea; padding-bottom: 10px; }}
.section h3 {{ color: #444; margin-top: 25px; }}
.mg {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin: 15px 0; }}
.mc {{ background: #f8f9fa; padding: 18px; border-radius: 8px; border-left: 4px solid #667eea; }}
.mc h4 {{ margin: 0 0 8px 0; font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
.mc .v {{ font-size: 28px; font-weight: bold; color: #333; }} .mc .u {{ font-size: 14px; color: #999; }}
.red {{ border-left-color: #dc3545; }} .green {{ border-left-color: #28a745; }} .orange {{ border-left-color: #fd7e14; }}
table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 14px; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #f8f9fa; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.3px; }}
.lb-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; margin: 20px 0; }}
.lb-card {{ background: #f8f9fa; border-radius: 8px; padding: 15px; border-left: 4px solid #667eea; }}
.lb-card.event {{ border-left-color: #fd7e14; }}
.lb-card h4 {{ margin: 0 0 5px 0; font-size: 14px; color: #333; }}
.lb-card .lb-meta {{ font-size: 12px; color: #888; margin-bottom: 10px; }}
.lb-card table {{ font-size: 13px; margin: 5px 0 0 0; }}
.lb-card th, .lb-card td {{ padding: 4px 8px; }}
.chart-box {{ position: relative; height: 250px; margin: 10px 0; }}
.charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 15px 0; }}
.footer {{ text-align: center; color: #999; margin-top: 40px; padding: 20px; }}
</style>
</head>
<body>

<div class="header">
    <h1>Load Test Report</h1>
    <p><strong>Test Name:</strong> {TEST_NAME}</p>
    <p><strong>Test Config ID:</strong> {TEST_CONFIG_ID}</p>
    <p><strong>Genre:</strong> {GENRE}</p>
    <p><strong>Period:</strong> {test_start_str} → {test_end_str} ({dur_hrs:.1f} hours)</p>
    <p><strong>Generated:</strong> {gen_time} UTC</p>
</div>

<div class="section">
    <h2>Test Configuration</h2>
    <div class="mg">
        <div class="mc"><h4>Duration</h4><div class="v">{dur_hrs:.1f}</div><div class="u">hours ({dur_mins:.0f} min)</div></div>
        <div class="mc"><h4>Players per Instance</h4><div class="v">{MAX_PLAYERS_PER_INSTANCE}</div><div class="u">concurrent</div></div>
        <div class="mc"><h4>Total Instances</h4><div class="v">{total_instances}</div><div class="u">1 PRIMARY + {total_instances-1} WORKER</div></div>
        <div class="mc"><h4>Region</h4><div class="v" style="font-size:22px">{REGION}</div></div>
    </div>
</div>

<div class="section">
    <h2>Performance Summary</h2>
    <h3>Peak Values</h3>
    <div class="mg">
        <div class="mc red"><h4>Peak RPS</h4><div class="v">{final_peak_rps:,.1f}</div><div class="u">req/sec ({peak_rps_src})</div></div>
        <div class="mc red"><h4>Peak Concurrent Players</h4><div class="v">{sum_peak_players:,}</div><div class="u">across all instances</div></div>
        <div class="mc red"><h4>Peak Player Workers</h4><div class="v">{sum_peak_pw:,}</div><div class="u">threads</div></div>
        <div class="mc red"><h4>Peak Batch Workers</h4><div class="v">{sum_peak_bw}</div><div class="u">threads</div></div>
    </div>
    <h3>Aggregate Performance</h3>
    <div class="mg">
        <div class="mc"><h4>Total Requests (all instances)</h4><div class="v">{grand_total_reqs:,}</div><div class="u">{total_instances} instances combined</div></div>
        <div class="mc"><h4>API Gateway Requests</h4><div class="v">{apigw.get('total_requests',0):,.0f}</div><div class="u">CloudWatch Count metric</div></div>
        <div class="mc"><h4>Average RPS (aggregate)</h4><div class="v">{avg_agg_rps:,.1f}</div><div class="u">req/sec across all instances</div></div>
        <div class="mc"><h4>API Gateway Avg Latency</h4><div class="v">{apigw.get('avg_latency_ms',0):,.1f}</div><div class="u">ms (CloudWatch)</div></div>
    </div>
</div>
"""

# Performance charts section
html += """<div class="section"><h2>Performance Over Time</h2>
<div class="charts-grid">
    <div><h3 style="font-size:14px;color:#666">API Gateway RPS</h3><div class="chart-box"><canvas id="rpsChart"></canvas></div></div>
    <div><h3 style="font-size:14px;color:#666">API Gateway Avg Latency (ms)</h3><div class="chart-box"><canvas id="latChart"></canvas></div></div>
    <div><h3 style="font-size:14px;color:#666">Lambda Concurrent Executions</h3><div class="chart-box"><canvas id="concChart"></canvas></div></div>
    <div><h3 style="font-size:14px;color:#666">DynamoDB Stats Table (RCU/WCU per sec)</h3><div class="chart-box"><canvas id="ddbChart"></canvas></div></div>
</div>
</div>
"""

# CloudWatch section
html += """<div class="section"><h2>AWS CloudWatch Insights</h2>
<h3>Lambda Functions</h3>
<table><thead><tr><th>Function</th><th>Invocations</th><th>Errors</th><th>Throttles</th><th>Avg Duration</th><th>Max Duration</th><th>Avg Concurrency</th><th>Max Concurrency</th></tr></thead><tbody>
"""
for fn, fm in lambda_metrics.items():
    dn = fn.replace('game-statsleaderboards-dev-','')
    html += f"<tr><td>{dn}</td><td>{fm.get('invocations',0):,.0f}</td><td>{fm.get('errors',0):,.0f}</td><td>{fm.get('throttles',0):,.0f}</td><td>{fm.get('avg_duration_ms',0):.1f}ms</td><td>{fm.get('max_duration_ms',0):.1f}ms</td><td>{fm.get('avg_concurrency',0):.1f}</td><td>{fm.get('max_concurrency',0):.0f}</td></tr>\n"

html += f"""</tbody></table>
<h3>API Gateway</h3>
<div class="mg">
    <div class="mc"><h4>Total Requests</h4><div class="v">{apigw.get('total_requests',0):,.0f}</div></div>
    <div class="mc red"><h4>Peak RPS</h4><div class="v">{apigw.get('peak_rps',0):,.1f}</div><div class="u">req/sec</div></div>
    <div class="mc"><h4>Avg Latency</h4><div class="v">{apigw.get('avg_latency_ms',0):.1f}</div><div class="u">ms</div></div>
    <div class="mc"><h4>P99 Latency (avg)</h4><div class="v">{apigw.get('p99_avg_ms',0):,.1f}</div><div class="u">ms (typical)</div></div>
    <div class="mc"><h4>P99 Latency (worst window)</h4><div class="v">{apigw.get('p99_worst_ms',0):,.1f}</div><div class="u">ms (single 5-min peak)</div></div>
    <div class="mc"><h4>Max Latency</h4><div class="v">{apigw.get('max_latency_ms',0):,.1f}</div><div class="u">ms (single request)</div></div>
</div>
<h3>DynamoDB Tables</h3>
<p style="color:#666;font-size:13px">Consumed capacity units during the test period.</p>
<table><thead><tr><th>Table</th><th>Total RCU</th><th>Avg RCU/sec</th><th>Total WCU</th><th>Avg WCU/sec</th><th>Throttled</th></tr></thead><tbody>
"""
for tn, tm in ddb_cw.items():
    dn = tn.replace('game-statsleaderboards-dev-','')
    html += f"<tr><td>{dn}</td><td>{tm.get('total_read_capacity',0):,.1f}</td><td>{tm.get('avg_read_capacity',0):.2f}</td><td>{tm.get('total_write_capacity',0):,.1f}</td><td>{tm.get('avg_write_capacity',0):.2f}</td><td>{tm.get('throttled_requests',0):,.0f}</td></tr>\n"

html += "</tbody></table></div>\n"


# Instance tracking section (compact — no per-instance worker breakdown)
html += f"""<div class="section"><h2>Test Instance Tracking</h2>
<div class="mg">
    <div class="mc"><h4>Total Instances</h4><div class="v">{total_instances}</div></div>
    <div class="mc"><h4>Total Player Workers</h4><div class="v">{sum_peak_pw + PRIMARY_MAX_PW}</div><div class="u">threads (sum of peaks)</div></div>
    <div class="mc"><h4>Total Batch Workers</h4><div class="v">{sum_peak_bw + PRIMARY_MAX_BW}</div><div class="u">threads (sum of peaks)</div></div>
    <div class="mc"><h4>Total Requests (all)</h4><div class="v">{grand_total_reqs:,}</div></div>
</div>
<h3>Instance Details</h3>
<table><thead><tr><th>Instance</th><th>Max Players</th><th>Workers</th><th>Total Requests</th><th>Success Rate</th><th>Metric Records</th></tr></thead><tbody>
<tr style="font-weight:600;background:#f0f0ff"><td>instance-1 (PRIMARY)</td><td>{PRIMARY_PEAK_PLAYERS}</td><td>{PRIMARY_MAX_PW}</td><td>{PRIMARY_METRICS['total_requests']:,}</td><td>100.00%</td><td>local</td></tr>
"""
for inst in instances:
    sr = (inst['success']/inst['total_reqs']*100) if inst['total_reqs'] > 0 else 0
    html += f"<tr><td>{inst['id']}</td><td>{inst['max_players']}</td><td>{inst['workers']}</td><td>{inst['total_reqs']:,}</td><td>{sr:.2f}%</td><td>{inst['records']}</td></tr>\n"

html += "</tbody></table></div>\n"

# Leaderboard config section
html += """<div class="section"><h2>Leaderboard Configuration</h2>
<h3>Permanent Leaderboards</h3>
<table><thead><tr><th>Leaderboard Name</th><th>Game Mode</th><th>Score Strategy</th><th>Score Type</th><th>Type</th><th>Stat Attribute</th></tr></thead><tbody>
"""
for lb in leaderboards:
    html += f"<tr><td>{lb.get('leaderboardName','')}</td><td>{lb.get('gameMode','')}</td><td>{lb.get('scoreStrategy','')}</td><td>{lb.get('scoreType','')}</td><td>{lb.get('leaderboardType','')}</td><td>{lb.get('statAttributeForLeaderboard','')}</td></tr>\n"
html += "</tbody></table>\n"

# Event leaderboards
if events:
    html += "<h3>Event Leaderboards</h3>\n<table><thead><tr><th>Event Name</th><th>Type</th><th>Leaderboard</th><th>Game Mode</th><th>Start (UTC)</th><th>End (UTC)</th><th>Duration</th><th>Status</th></tr></thead><tbody>\n"
    for ev in events:
        st = ev.get('startTime', 0)
        et = ev.get('endTime', 0)
        st_str = datetime.fromtimestamp(float(st), tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if st else 'N/A'
        et_str = datetime.fromtimestamp(float(et), tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if et else 'N/A'
        dur_m = float(ev.get('durationSeconds', 0) or 0) / 60
        status = 'expired' if float(et or 0) < END_TIME else 'active'
        ev_name = ev.get('eventName', 'N/A')
        ev_type = ev.get('eventType', 'N/A')
        lb_cfg = ev.get('leaderboardConfig', {})
        lb_name = lb_cfg.get('leaderboardName', 'N/A')
        mode = ev.get('targetGameMode', lb_cfg.get('gameMode', 'N/A'))
        color = '#6c757d' if status == 'expired' else '#28a745'
        html += f"<tr><td>{ev_name}</td><td>{ev_type}</td><td>{lb_name}</td><td>{mode}</td><td>{st_str}</td><td>{et_str}</td><td>{dur_m:.0f} min</td><td style='color:{color};font-weight:bold'>{status.upper()}</td></tr>\n"
    html += "</tbody></table>\n"

html += "</div>\n"


# Leaderboard standings section — the showcase
html += """<div class="section"><h2>Leaderboard Standings (Post-Test)</h2>
<p style="color:#666;font-size:14px">Live query of top players on each leaderboard after test completion.</p>
<div class="lb-grid">
"""

for lb_name in all_lb_names:
    meta = lb_meta.get(lb_name, {})
    data = lb_scores.get(lb_name, {})
    scores = data.get('scores', [])
    total_p = data.get('totalPlayers', 0)
    is_event = meta.get('type', '') != 'permanent'
    card_class = 'lb-card event' if is_event else 'lb-card'
    badge = ' <span style="color:#fd7e14;font-size:11px">[EVENT]</span>' if is_event else ''
    mode = meta.get('gameMode', '')
    strategy = meta.get('scoreStrategy', '')

    html += f'<div class="{card_class}"><h4>{lb_name}{badge}</h4>\n'
    html += f'<div class="lb-meta">{mode} · {strategy} · {total_p} players</div>\n'

    if scores:
        html += '<table><thead><tr><th>#</th><th>Player</th><th>Score</th></tr></thead><tbody>\n'
        for s in scores[:20]:
            score_val = s.get('score', 0)
            if isinstance(score_val, float) and score_val > 1000:
                score_str = f"{score_val:,.1f}"
            elif isinstance(score_val, float):
                score_str = f"{score_val:.2f}"
            else:
                score_str = f"{score_val:,}"
            html += f"<tr><td>{s.get('rank','')}</td><td>{s.get('playerID','')}</td><td>{score_str}</td></tr>\n"
        html += '</tbody></table>\n'
    elif is_event:
        html += '<p style="color:#999;font-size:12px">Event leaderboard data expired (Valkey TTL). Scores are evicted from the in-memory store after the event ends.</p>\n'
    else:
        html += '<p style="color:#999;font-size:13px">No scores available</p>\n'

    html += '</div>\n'

html += "</div></div>\n"

# ============================================================
# Infrastructure Details section
# ============================================================
html += '<div class="section"><h2>Infrastructure Configuration</h2>\n'
html += '<p style="color:#666;font-size:14px">AWS resource configuration during the test. Useful for understanding capacity and scaling behavior.</p>\n'

# Collect infrastructure details live from AWS
print("\n6. Collecting infrastructure details...")

try:
    lambda_client = session.client('lambda', region_name=REGION)
    apigw_client = session.client('apigateway', region_name=REGION)
    ddb_client = session.client('dynamodb', region_name=REGION)

    # Lambda function configs
    html += '<h3>Lambda Functions</h3>\n'
    html += '<table><thead><tr><th>Function</th><th>Runtime</th><th>Memory</th><th>Timeout</th><th>Reserved Concurrency</th></tr></thead><tbody>\n'
    for fn in lambda_functions:
        try:
            cfg = lambda_client.get_function_configuration(FunctionName=fn)
            display = fn.replace('game-statsleaderboards-dev-', '')
            memory = cfg.get('MemorySize', 0)
            timeout = cfg.get('Timeout', 0)
            runtime = cfg.get('Runtime', 'N/A')
            # Check reserved concurrency
            try:
                conc = lambda_client.get_function_concurrency(FunctionName=fn)
                reserved = conc.get('ReservedConcurrentExecutions', 'Unreserved')
            except Exception:
                reserved = 'Unreserved'
            html += f'<tr><td>{display}</td><td>{runtime}</td><td>{memory} MB</td><td>{timeout}s</td><td>{reserved}</td></tr>\n'
        except Exception as e:
            print(f"   ⚠ Lambda {fn}: {e}")
    html += '</tbody></table>\n'
    print("   ✓ Lambda configs")

    # API Gateway throttle settings
    html += '<h3>API Gateway</h3>\n'
    try:
        # Get account-level throttle
        acct = apigw_client.get_account()
        acct_throttle = acct.get('throttleSettings', {})

        # Get stage-level throttle
        stage = apigw_client.get_stage(restApiId='uukvtdz1ee', stageName='dev')
        stage_settings = stage.get('methodSettings', {}).get('*/*', {})

        html += '<div class="mg">\n'
        acct_rate = acct_throttle.get("rateLimit", 0)
        acct_burst = acct_throttle.get("burstLimit", 0)
        stage_rate = stage_settings.get("throttlingRateLimit", 0)
        stage_burst = stage_settings.get("throttlingBurstLimit", 0)
        eff_rate = min(acct_rate, stage_rate) if stage_rate else acct_rate
        eff_burst = min(acct_burst, stage_burst) if stage_burst else acct_burst
        html += f'<div class="mc"><h4>Account Rate Limit</h4><div class="v">{acct_rate:,.0f}</div><div class="u">req/sec</div></div>\n'
        html += f'<div class="mc"><h4>Account Burst Limit</h4><div class="v">{acct_burst:,}</div><div class="u">requests</div></div>\n'
        html += f'<div class="mc"><h4>Stage Rate Limit</h4><div class="v">{stage_rate:,.0f}</div><div class="u">req/sec</div></div>\n'
        html += f'<div class="mc"><h4>Stage Burst Limit</h4><div class="v">{stage_burst:,}</div><div class="u">requests</div></div>\n'
        html += f'<div class="mc green"><h4>Effective Rate Limit</h4><div class="v">{eff_rate:,.0f}</div><div class="u">req/sec (min of account &amp; stage)</div></div>\n'
        html += f'<div class="mc green"><h4>Effective Burst Limit</h4><div class="v">{eff_burst:,}</div><div class="u">requests (min of account &amp; stage)</div></div>\n'
        html += '</div>\n'
        print("   ✓ API Gateway throttle settings")
    except Exception as e:
        html += f'<p>Could not retrieve API Gateway settings: {e}</p>\n'
        print(f"   ⚠ APIGW settings: {e}")

    # DynamoDB table configs
    html += '<h3>DynamoDB Tables</h3>\n'
    html += '<table><thead><tr><th>Table</th><th>Billing Mode</th><th>Item Count</th><th>Size</th><th>GSIs</th></tr></thead><tbody>\n'
    all_ddb_tables = [
        'game-statsleaderboards-dev-config',
        'game-statsleaderboards-dev-stats',
        TEST_DEFINITIONS_TABLE,
        TEST_METRICS_TABLE,
        PLAYER_STATE_TABLE
    ]
    for tn in all_ddb_tables:
        try:
            desc = ddb_client.describe_table(TableName=tn)['Table']
            billing = desc.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED')
            items = desc.get('ItemCount', 0)
            size_mb = desc.get('TableSizeBytes', 0) / (1024 * 1024)
            gsi_count = len(desc.get('GlobalSecondaryIndexes', []))
            display = tn.replace('game-statsleaderboards-dev-', '').replace('game-StatsLeaderboards-', '')
            html += f'<tr><td>{display}</td><td>{billing}</td><td>{items:,}</td><td>{size_mb:,.1f} MB</td><td>{gsi_count}</td></tr>\n'
        except Exception as e:
            print(f"   ⚠ DDB {tn}: {e}")
    html += '</tbody></table>\n'
    print("   ✓ DynamoDB table configs")

    # MemoryDB / ElastiCache for Valkey
    html += '<h3>MemoryDB for Valkey</h3>\n'
    try:
        memdb = session.client('memorydb', region_name=REGION)
        clusters = memdb.describe_clusters(ShowShardDetails=True)
        valkey_clusters = clusters.get('Clusters', [])
        if valkey_clusters:
            html += '<table><thead><tr><th>Cluster</th><th>Status</th><th>Node Type</th><th>Shards</th><th>Replicas/Shard</th><th>Engine</th></tr></thead><tbody>\n'
            for cl in valkey_clusters:
                name = cl.get('Name', 'N/A')
                status = cl.get('Status', 'N/A')
                node_type = cl.get('NodeType', 'N/A')
                shards = cl.get('NumberOfShards', 0)
                replicas = cl.get('Shards', [{}])[0].get('NumberOfNodes', 1) - 1 if cl.get('Shards') else 0
                engine = cl.get('EnginePatchVersion', cl.get('EngineVersion', 'N/A'))
                html += f'<tr><td>{name}</td><td>{status}</td><td>{node_type}</td><td>{shards}</td><td>{replicas}</td><td>{engine}</td></tr>\n'
            html += '</tbody></table>\n'
        else:
            # Try ElastiCache Serverless
            ec = session.client('elasticache', region_name=REGION)
            serverless = ec.describe_serverless_caches()
            caches = serverless.get('ServerlessCaches', [])
            if caches:
                html += '<div class="mg">\n'
                for c in caches:
                    html += f'<div class="mc"><h4>{c.get("ServerlessCacheName","N/A")}</h4><div class="v">{c.get("Engine","valkey")}</div><div class="u">Status: {c.get("Status","N/A")}</div></div>\n'
                html += '</div>\n'
            else:
                html += '<p>No MemoryDB or ElastiCache Serverless clusters found.</p>\n'
        print("   ✓ MemoryDB/Valkey config")
    except Exception as e:
        html += f'<p>Could not retrieve MemoryDB details: {e}</p>\n'
        print(f"   ⚠ MemoryDB: {e}")

except Exception as e:
    html += f'<p>Error collecting infrastructure details: {e}</p>\n'
    print(f"   ⚠ Infrastructure collection error: {e}")

html += '</div>\n'

# ============================================================
# Cost Estimation section
# ============================================================
print("\n7. Computing cost estimates...")

# us-west-2 pricing — query from AWS Pricing API, fallback to known defaults
# Sources: aws.amazon.com/lambda/pricing, aws.amazon.com/api-gateway/pricing,
# aws.amazon.com/dynamodb/pricing/on-demand, aws.amazon.com/memorydb/pricing
# Content was rephrased for compliance with licensing restrictions.
pricing_source = 'defaults (us-west-2)'
LAMBDA_PRICE_PER_REQUEST = 0.20 / 1_000_000
LAMBDA_PRICE_PER_GB_SEC = 0.0000166667
APIGW_PRICE_PER_REQUEST = 3.50 / 1_000_000
DDB_PRICE_PER_WRU = 1.25 / 1_000_000
DDB_PRICE_PER_RRU = 0.25 / 1_000_000
MEMORYDB_VALKEY_R6G_LARGE_PER_HR = 0.259

try:
    pricing_client = session.client('pricing', region_name='us-east-1')
    # Lambda request price
    resp = pricing_client.get_products(ServiceCode='AWSLambda', Filters=[
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': REGION},
        {'Type': 'TERM_MATCH', 'Field': 'group', 'Value': 'AWS-Lambda-Requests'}
    ], MaxResults=1)
    if resp.get('PriceList'):
        pd = json.loads(resp['PriceList'][0]) if isinstance(resp['PriceList'][0], str) else resp['PriceList'][0]
        for t in pd.get('terms', {}).get('OnDemand', {}).values():
            for d in t.get('priceDimensions', {}).values():
                LAMBDA_PRICE_PER_REQUEST = float(d['pricePerUnit']['USD'])
    # Lambda duration (Tier 1)
    resp = pricing_client.get_products(ServiceCode='AWSLambda', Filters=[
        {'Type': 'TERM_MATCH', 'Field': 'regionCode', 'Value': REGION},
        {'Type': 'TERM_MATCH', 'Field': 'group', 'Value': 'AWS-Lambda-Duration'}
    ], MaxResults=5)
    if resp.get('PriceList'):
        for item in resp['PriceList']:
            pd = json.loads(item) if isinstance(item, str) else item
            for t in pd.get('terms', {}).get('OnDemand', {}).values():
                for d in t.get('priceDimensions', {}).values():
                    if 'Tier-1' in d.get('description', '') or 'Total Compute' in d.get('description', ''):
                        LAMBDA_PRICE_PER_GB_SEC = float(d['pricePerUnit']['USD'])
                        break
    pricing_source = f'AWS Pricing API ({REGION})'
    print(f"   Pricing fetched from AWS Pricing API")
except Exception as e:
    print(f"   ⚠ Pricing API unavailable, using defaults: {e}")

test_hours = (END_TIME - START_TIME) / 3600

# Lambda cost from actual CloudWatch data
total_lambda_invocations = sum(m.get('invocations', 0) for m in lambda_metrics.values())
# Estimate average duration from CloudWatch (weighted by invocations)
total_weighted_duration = 0
for fn, fm in lambda_metrics.items():
    inv = fm.get('invocations', 0)
    avg_dur = fm.get('avg_duration_ms', 0)
    # Get memory from live config if available
    try:
        cfg = lambda_client.get_function_configuration(FunctionName=fn)
        mem_mb = cfg.get('MemorySize', 512)
    except Exception:
        mem_mb = 512
    # GB-seconds = invocations * (duration_ms / 1000) * (memory_mb / 1024)
    gb_sec = inv * (avg_dur / 1000.0) * (mem_mb / 1024.0)
    total_weighted_duration += gb_sec

lambda_request_cost = total_lambda_invocations * LAMBDA_PRICE_PER_REQUEST
lambda_compute_cost = total_weighted_duration * LAMBDA_PRICE_PER_GB_SEC
lambda_total = lambda_request_cost + lambda_compute_cost

# API Gateway cost
apigw_total_reqs = apigw.get('total_requests', 0)
apigw_cost = apigw_total_reqs * APIGW_PRICE_PER_REQUEST

# DynamoDB cost from CloudWatch consumed capacity
ddb_total_wcu = sum(tm.get('total_write_capacity', 0) for tm in ddb_cw.values())
ddb_total_rcu = sum(tm.get('total_read_capacity', 0) for tm in ddb_cw.values())
ddb_write_cost = ddb_total_wcu * DDB_PRICE_PER_WRU
ddb_read_cost = ddb_total_rcu * DDB_PRICE_PER_RRU
ddb_total = ddb_write_cost + ddb_read_cost

# MemoryDB cost (fixed infrastructure — runs 24/7 regardless of test)
# Query actual node count
memdb_nodes = 0
memdb_node_type = 'db.r6g.large'
try:
    memdb_client = session.client('memorydb', region_name=REGION)
    clusters = memdb_client.describe_clusters(ShowShardDetails=True)
    for cl in clusters.get('Clusters', []):
        memdb_node_type = cl.get('NodeType', 'db.r6g.large')
        for shard in cl.get('Shards', []):
            memdb_nodes += shard.get('NumberOfNodes', 0)
except Exception:
    memdb_nodes = 2  # Default: 1 shard, 1 primary + 1 replica

memdb_cost_test = memdb_nodes * MEMORYDB_VALKEY_R6G_LARGE_PER_HR * test_hours

# Total test cost
total_test_cost = lambda_total + apigw_cost + ddb_total + memdb_cost_test

# MemoryDB monthly (always-on infrastructure)
memdb_monthly = memdb_nodes * MEMORYDB_VALKEY_R6G_LARGE_PER_HR * 24 * 30

# Realistic production estimates
# Our load test uses an aggressive pattern: each player thread calls APIs in a tight loop
# (~0.76 calls/player/second). Real games call APIs far less frequently.
# Typical patterns:
#   - Stats submission: end of match (every 5-15 min)
#   - Leaderboard query: when player opens leaderboard UI (a few times per session)
#   - Player standing: on demand (once per session or less)
# Conservative estimate: 1 API call per player per 30 seconds (still aggressive for most games)
# Casual estimate: 1 API call per player per 2 minutes

test_rps = apigw_total_reqs / (test_hours * 3600) if test_hours > 0 else 0
test_calls_per_player_per_sec = test_rps / max(sum_peak_players, 1) if sum_peak_players > 0 else 0

# Scenario A: Competitive game (1 call / player / 30s)
scenario_a_rps = sum_peak_players / 30.0
scenario_a_ratio = scenario_a_rps / max(test_rps, 1)
scenario_a_monthly_api = apigw_cost * scenario_a_ratio * (24 * 30 / max(test_hours, 1))
scenario_a_monthly_lambda = lambda_total * scenario_a_ratio * (24 * 30 / max(test_hours, 1))
scenario_a_monthly_ddb = ddb_total * scenario_a_ratio * (24 * 30 / max(test_hours, 1))
scenario_a_monthly = scenario_a_monthly_api + scenario_a_monthly_lambda + scenario_a_monthly_ddb + memdb_monthly

# Scenario B: Casual game (1 call / player / 2 min)
scenario_b_rps = sum_peak_players / 120.0
scenario_b_ratio = scenario_b_rps / max(test_rps, 1)
scenario_b_monthly_api = apigw_cost * scenario_b_ratio * (24 * 30 / max(test_hours, 1))
scenario_b_monthly_lambda = lambda_total * scenario_b_ratio * (24 * 30 / max(test_hours, 1))
scenario_b_monthly_ddb = ddb_total * scenario_b_ratio * (24 * 30 / max(test_hours, 1))
scenario_b_monthly = scenario_b_monthly_api + scenario_b_monthly_lambda + scenario_b_monthly_ddb + memdb_monthly

print(f"   Lambda: ${lambda_total:.2f} ({total_lambda_invocations:,.0f} invocations, {total_weighted_duration:,.0f} GB-s)")
print(f"   API Gateway: ${apigw_cost:.2f} ({apigw_total_reqs:,.0f} requests)")
print(f"   DynamoDB: ${ddb_total:.2f} ({ddb_total_wcu:,.0f} WCU, {ddb_total_rcu:,.0f} RCU)")
print(f"   MemoryDB: ${memdb_cost_test:.2f} ({memdb_nodes} nodes × {test_hours:.1f}h)")
print(f"   TOTAL (test): ${total_test_cost:.2f}")
print(f"   Test avg RPS: {test_rps:.0f}, calls/player/sec: {test_calls_per_player_per_sec:.2f}")
print(f"   Scenario A (competitive, {sum_peak_players} CCU): ${scenario_a_monthly:,.0f}/month")
print(f"   Scenario B (casual, {sum_peak_players} CCU): ${scenario_b_monthly:,.0f}/month")

html += '<div class="section"><h2>Cost Estimation</h2>\n'
html += f'<p style="color:#666;font-size:14px">Estimated costs based on {pricing_source} on-demand pricing and actual CloudWatch consumption data. '
html += 'Sources: <a href="https://aws.amazon.com/lambda/pricing/">Lambda</a>, '
html += '<a href="https://aws.amazon.com/api-gateway/pricing/">API Gateway</a>, '
html += '<a href="https://aws.amazon.com/dynamodb/pricing/on-demand/">DynamoDB</a>, '
html += '<a href="https://aws.amazon.com/memorydb/pricing/">MemoryDB</a>. '
html += 'This cost estimate does not include load generation infrastructure costs (e.g., EC2 instances used to run the test).</p>\n'

html += '<h3>Load Test Cost</h3>\n'
html += f'<p style="color:#666;font-size:13px">This is the cost of running the stress test itself. The test uses an aggressive request pattern (~{test_calls_per_player_per_sec:.1f} API calls/player/second) to push the system to its limits. Real game traffic is typically 20-60x lower.</p>\n'
html += '<table><thead><tr><th>Service</th><th>Usage</th><th>Cost</th></tr></thead><tbody>\n'
html += f'<tr><td>Lambda (requests)</td><td>{total_lambda_invocations:,.0f} invocations</td><td>${lambda_request_cost:.2f}</td></tr>\n'
html += f'<tr><td>Lambda (compute)</td><td>{total_weighted_duration:,.0f} GB-seconds</td><td>${lambda_compute_cost:.2f}</td></tr>\n'
html += f'<tr><td>API Gateway</td><td>{apigw_total_reqs:,.0f} requests</td><td>${apigw_cost:.2f}</td></tr>\n'
html += f'<tr><td>DynamoDB (writes)</td><td>{ddb_total_wcu:,.0f} WCU consumed</td><td>${ddb_write_cost:.2f}</td></tr>\n'
html += f'<tr><td>DynamoDB (reads)</td><td>{ddb_total_rcu:,.0f} RCU consumed</td><td>${ddb_read_cost:.2f}</td></tr>\n'
html += f'<tr><td>MemoryDB for Valkey</td><td>{memdb_nodes} × {memdb_node_type} × {test_hours:.1f}h</td><td>${memdb_cost_test:.2f}</td></tr>\n'
html += f'<tr style="font-weight:bold;background:#f0f0ff"><td>Total (stress test)</td><td>{test_hours:.1f} hours</td><td>${total_test_cost:.2f}</td></tr>\n'
html += '</tbody></table>\n'

html += f'<h3>Realistic Production Cost Estimates ({sum_peak_players:,} concurrent players)</h3>\n'
html += '<p style="color:#666;font-size:13px">In production, players interact with leaderboards far less frequently than our stress test. '
html += 'Below are monthly cost estimates for two typical game patterns, assuming the same player count as the test.</p>\n'

html += '<table style="margin:15px 0"><thead><tr><th></th><th>Competitive Game</th><th>Casual Game</th></tr></thead><tbody>\n'
html += '<tr><td style="font-weight:600">Player behavior</td><td>1 API call per player every 30 seconds</td><td>1 API call per player every 2 minutes</td></tr>\n'
html += '<tr><td style="font-weight:600">Example</td><td>PvP arena with live leaderboards, frequent score submissions after short matches</td><td>Story-driven RPG, leaderboard checked between sessions, stats submitted at end of longer play sessions</td></tr>\n'
html += f'<tr><td style="font-weight:600">Effective RPS</td><td>{scenario_a_rps:,.0f} req/sec</td><td>{scenario_b_rps:,.0f} req/sec</td></tr>\n'
html += f'<tr><td style="font-weight:600">vs. stress test</td><td>{scenario_a_ratio*100:.1f}% of test load</td><td>{scenario_b_ratio*100:.1f}% of test load</td></tr>\n'
html += f'<tr style="font-weight:bold;background:#f0f0ff"><td>Estimated monthly cost</td><td>${scenario_a_monthly:,.0f}/month</td><td>${scenario_b_monthly:,.0f}/month</td></tr>\n'
html += '</tbody></table>\n'

html += '<p style="color:#666;font-size:13px">Breakdown of the monthly estimate includes:</p>\n'
html += '<ul style="color:#666;font-size:13px;margin:5px 0">\n'
html += f'<li>Pay-per-use services (Lambda, API Gateway, DynamoDB) — scales linearly with traffic</li>\n'
html += f'<li>MemoryDB for Valkey — always-on infrastructure: ${memdb_monthly:,.0f}/month ({memdb_nodes} nodes × 24/7)</li>\n'
html += '</ul>\n'

html += '<p style="color:#666;font-size:12px;margin-top:15px">Note: Estimates exclude free tier credits, data transfer, CloudWatch, and WAF costs. '
html += 'Actual costs depend on your game\'s specific API call patterns, match duration, and player engagement. '
html += 'MemoryDB pricing is for Valkey engine (30% lower than Redis OSS). '
html += 'DynamoDB storage costs ($0.25/GB/month) are not included as they depend on data retention.</p>\n'
html += '</div>\n'
html += '<div class="footer"><p>Generated by Load Testing System v1.0.0</p></div>\n'

# Chart.js scripts
import json as json_mod
html += f"""<script>
const labels = {json_mod.dumps(chart_data['timestamps'])};
const chartOpts = {{responsive:true, maintainAspectRatio:false, plugins:{{legend:{{display:false}}}}, scales:{{x:{{ticks:{{maxTicksLimit:20,font:{{size:10}}}}}}}}}};
new Chart(document.getElementById('rpsChart'),{{type:'line',data:{{labels:labels,datasets:[{{label:'RPS',data:{json_mod.dumps(chart_data['apigw_rps'])},borderColor:'#667eea',backgroundColor:'rgba(102,126,234,0.1)',fill:true,tension:0.3,pointRadius:0}}]}},options:chartOpts}});
new Chart(document.getElementById('latChart'),{{type:'line',data:{{labels:labels,datasets:[{{label:'Latency',data:{json_mod.dumps(chart_data['apigw_latency'])},borderColor:'#f093fb',backgroundColor:'rgba(240,147,251,0.1)',fill:true,tension:0.3,pointRadius:0}}]}},options:chartOpts}});
new Chart(document.getElementById('concChart'),{{type:'line',data:{{labels:labels,datasets:[{{label:'Concurrency',data:{json_mod.dumps(chart_data['lambda_concurrency'])},borderColor:'#28a745',backgroundColor:'rgba(40,167,69,0.1)',fill:true,tension:0.3,pointRadius:0}}]}},options:chartOpts}});
new Chart(document.getElementById('ddbChart'),{{type:'line',data:{{labels:labels,datasets:[{{label:'Read (RCU/s)',data:{json_mod.dumps(chart_data['ddb_read'])},borderColor:'#17a2b8',tension:0.3,pointRadius:0}},{{label:'Write (WCU/s)',data:{json_mod.dumps(chart_data['ddb_write'])},borderColor:'#fd7e14',tension:0.3,pointRadius:0}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:true,position:'top'}}}},scales:{{x:{{ticks:{{maxTicksLimit:20,font:{{size:10}}}}}}}}}}}});
</script>
"""

html += '</body></html>'

# Write
out_path = f'reports/test_report_{TEST_CONFIG_ID}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
with open(out_path, 'w') as f:
    f.write(html)

print(f"\n{'='*60}")
print(f"Report: {out_path}")
print(f"{'='*60}")
