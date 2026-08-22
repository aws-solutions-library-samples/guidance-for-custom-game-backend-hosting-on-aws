#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Comprehensive Game Stats & Leaderboards Analysis Tool

Compares MemoryDB/Valkey vs DynamoDB results to validate data consistency.
Supports free-threaded Python 3.14t for faster DynamoDB scanning.

Usage:
    python3 analyze_leaderboards.py                    # Standard mode
    python3.14t analyze_leaderboards.py                # Free-threaded (faster)
    python3.14t analyze_leaderboards.py --full         # Full dataset comparison
"""

import json
import boto3
import requests
import time
import os
from decimal import Decimal
from typing import Dict, List, Any, Tuple, Optional
from botocore.config import Config
import argparse
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

# Detect free-threaded Python
GIL_DISABLED = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
CPU_COUNT = os.cpu_count() or 4

if GIL_DISABLED:
    SCAN_WORKERS = CPU_COUNT * 5
    print(f"🚀 Free-threaded Python (GIL disabled) — {CPU_COUNT} CPUs, {SCAN_WORKERS} scan workers")
else:
    SCAN_WORKERS = min(CPU_COUNT * 2, 20)
    print(f"🐍 Standard Python — {CPU_COUNT} CPUs, {SCAN_WORKERS} scan workers")

class LeaderboardAnalyzer:
    def __init__(self, region='us-west-2', profile='default'):
        self.region = region
        self.profile = profile
        self.session = boto3.Session(profile_name=profile)
        self.ssm = self.session.client('ssm', region_name=region)
        
        # Configure connection pool for parallel scanning
        ddb_config = Config(
            max_pool_connections=SCAN_WORKERS + 10,
            retries={'max_attempts': 3, 'mode': 'standard'}
        )
        self.dynamodb = self.session.client('dynamodb', region_name=region, config=ddb_config)
        
        # Load configuration
        self._load_config()
        
    def _load_config(self):
        """Load API configuration from SSM Parameter Store"""
        try:
            # Get API endpoint
            endpoint_param = self.ssm.get_parameter(
                Name='/game-statsleaderboards-dev/api/endpoint'
            )
            self.api_endpoint = endpoint_param['Parameter']['Value']
            # Ensure endpoint ends with /
            if not self.api_endpoint.endswith('/'):
                self.api_endpoint += '/'
            
            # Find the API key — list all keys under the api-keys path
            api_keys_path = '/game-statsleaderboards-dev/api-keys/'
            try:
                keys_resp = self.ssm.get_parameters_by_path(
                    Path=api_keys_path, WithDecryption=True, Recursive=False
                )
                if keys_resp.get('Parameters'):
                    # Use the first available key
                    param = keys_resp['Parameters'][0]
                    api_key_json = json.loads(param['Value'])
                    self.api_key = api_key_json['apiKey']
                    self.studio_id = api_key_json.get('studioId', '')
                    self.game_id = api_key_json.get('gameId', '')
                    print(f"✅ API key loaded from: {param['Name']}")
                else:
                    raise ValueError("No API keys found in SSM")
            except Exception as e:
                print(f"❌ Failed to load API key from SSM: {e}")
                sys.exit(1)
            
            print(f"✅ Configuration loaded:")
            print(f"   Endpoint: {self.api_endpoint}")
            print(f"   Game ID: {self.game_id}")
            print(f"   Studio ID: {self.studio_id}")
            
        except Exception as e:
            print(f"❌ Failed to load configuration: {e}")
            sys.exit(1)
    
    def get_leaderboard_configs(self) -> Dict[str, Dict]:
        """Get all leaderboard configurations from DynamoDB"""
        try:
            response = self.dynamodb.scan(
                TableName='game-statsleaderboards-dev-config'
            )
            
            configs = {}
            for item in response['Items']:
                lb_name = item['leaderboardName']['S']
                configs[lb_name] = {
                    'leaderboardName': lb_name,
                    'gameID': item['gameID']['S'],
                    'gameMode': item['gameMode']['S'],
                    'leaderboardType': item['leaderboardType']['S'],
                    'scoreType': item.get('scoreType', {}).get('S', 'score'),
                    'scoreStrategy': item.get('scoreStrategy', {}).get('S', 'best'),
                    'timeFormat': item.get('timeFormat', {}).get('S', 'seconds'),
                    'timePrecision': int(item.get('timePrecision', {}).get('N', '3')),
                    'sortedListName': item['sortedListName']['S']
                }
            
            print(f"📋 Found {len(configs)} leaderboard configurations")
            return configs
            
        except Exception as e:
            print(f"❌ Failed to get leaderboard configs: {e}")
            return {}
    
    def get_memorydb_scores(self, leaderboard_name: str, game_id: str, game_mode: str, limit: int = 1000) -> Tuple[List[Dict], int]:
        """Get scores from MemoryDB via Lambda API with pagination.
        Returns (scores_list, totalPlayers)."""
        try:
            url = f"{self.api_endpoint}leaderboards/scores"
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}'
            }

            all_scores = []
            total_players = 0
            next_token = None
            page = 0

            while True:
                payload = {
                    "leaderboardScoresRequest": {
                        "leaderboardName": leaderboard_name,
                        "queryType": "top",
                        "pageSize": min(limit, 500)  # API max is 500
                    }
                }
                if next_token:
                    payload["leaderboardScoresRequest"]["nextToken"] = next_token

                response = requests.post(url, json=payload, headers=headers, timeout=30)

                if response.status_code == 200:
                    data = response.json()
                    scores_resp = data.get('leaderboardScoresResponse', data)
                    scores = scores_resp.get('scores', [])
                    meta = scores_resp.get('metadata', {})

                    all_scores.extend(scores)
                    if page == 0:
                        total_players = meta.get('totalPlayers', len(scores))

                    next_token = meta.get('nextToken')
                    page += 1

                    # Stop if no more pages or we've fetched enough
                    if not next_token or len(all_scores) >= limit:
                        break
                else:
                    print(f"   ❌ API call failed: HTTP {response.status_code}")
                    break

            return all_scores, total_players

        except Exception as e:
            print(f"   ❌ MemoryDB query failed: {e}")
            return [], 0
    
    def get_dynamodb_scores(self, leaderboard_name: str) -> List[Dict]:
        """Get ALL scores from DynamoDB for a leaderboard using GSI with full pagination."""
        try:
            records = []
            query_kwargs = {
                'TableName': 'game-statsleaderboards-dev-stats',
                'IndexName': 'leaderboardName-timestamp-index',
                'KeyConditionExpression': 'leaderboardName = :lb_name',
                'ExpressionAttributeValues': {
                    ':lb_name': {'S': leaderboard_name}
                }
            }

            page_count = 0
            t0 = time.time()
            while True:
                response = self.dynamodb.query(**query_kwargs)

                for item in response.get('Items', []):
                    try:
                        # Use sortKey for chronological ordering (ms-precision ISO timestamp)
                        # Falls back to integer timestamp field if sortKey not available
                        sort_key = item.get('sortKey', {}).get('S', '')
                        timestamp = item.get('timestamp', {}).get('N', item.get('timestamp', {}).get('S', ''))
                        records.append({
                            'playerID': item['playerID']['S'],
                            'playerScore': Decimal(item['playerScore']['N']),
                            'timestamp': sort_key if sort_key else timestamp
                        })
                    except KeyError:
                        continue

                page_count += 1
                if page_count % 100 == 0:
                    elapsed = time.time() - t0
                    rate = len(records) / elapsed if elapsed > 0 else 0
                    print(f"      ... {len(records):,} records ({page_count} pages, {rate:,.0f} rec/s)", flush=True)

                if 'LastEvaluatedKey' not in response:
                    break
                query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

            return records

        except Exception as e:
            print(f"   ❌ DynamoDB query failed: {e}")
            return []
    
    def calculate_final_scores(self, records: List[Dict], strategy: str, score_type: str, leaderboard_type: str) -> List[Tuple[str, Decimal]]:
        """
        Calculate final scores exactly as MemoryDB would store them.
        
        Mirrors the Lambda update_leaderboard_async logic:
        - best + DESCENDING_LB: keep the highest score per player
        - best + ASCENDING_LB: keep the lowest score per player
        - cumulative + DESCENDING_LB: sum all scores (ZINCRBY)
        - cumulative + ASCENDING_LB: sum all scores (additive)
        - replace: use the most recent score (last by timestamp)
        """
        player_records = defaultdict(list)

        # Group records by player, keeping timestamp for chronological sorting
        for record in records:
            player_records[record['playerID']].append({
                'score': record['playerScore'],
                'timestamp': record.get('timestamp', '0')
            })

        final_scores = []

        for player_id, recs in player_records.items():
            # Sort by timestamp (ascending) so most recent is last
            recs.sort(key=lambda r: str(r['timestamp']))
            scores = [r['score'] for r in recs]

            if strategy == 'best':
                if leaderboard_type == 'ASCENDING_LB':
                    final_score = min(scores)
                else:
                    final_score = max(scores)
            elif strategy == 'cumulative':
                final_score = sum(scores)
            elif strategy == 'replace':
                # Most recent score (chronologically last after sorting)
                final_score = scores[-1]
            else:
                final_score = max(scores)

            final_scores.append((player_id, final_score))
        
        return final_scores
    
    def sort_scores(self, scores: List[Tuple[str, Decimal]], score_type: str, leaderboard_type: str) -> List[Tuple[str, Decimal]]:
        """Sort scores according to leaderboard type - matching MemoryDB native ordering"""
        if leaderboard_type == "ASCENDING_LB":
            # For ASCENDING_LB, lower scores are better (ascending sort)
            # Secondary sort by playerID for consistent ordering when scores are equal
            return sorted(scores, key=lambda x: (x[1], x[0]))
        else:
            # For DESCENDING_LB, higher scores are better (descending sort)
            # Secondary sort by playerID for consistent ordering when scores are equal
            return sorted(scores, key=lambda x: (-x[1], x[0]))
    
    def compare_leaderboard(self, leaderboard_name: str, config: Dict, show_full: bool = False) -> Dict:
        """Compare MemoryDB vs DynamoDB for a single leaderboard"""
        print(f"\n🔍 Analyzing: {leaderboard_name}")
        print(f"   Game: {config['gameID']}, Mode: {config['gameMode']}")
        print(f"   Type: {config['scoreType']}, Strategy: {config['scoreStrategy']}, LB Type: {config['leaderboardType']}")
        
        # Get MemoryDB scores (with pagination for full dataset)
        print("   📡 Querying MemoryDB/Valkey via Lambda API...")
        memorydb_scores, memorydb_total = self.get_memorydb_scores(
            leaderboard_name,
            config['gameID'],
            config['gameMode'],
            limit=10000 if show_full else 500
        )
        print(f"   ✅ MemoryDB: {len(memorydb_scores)} entries retrieved (totalPlayers={memorydb_total})")

        # If MemoryDB is empty (e.g., expired event leaderboard), skip DynamoDB comparison
        if len(memorydb_scores) == 0:
            print("   ⏭️  Skipping DynamoDB comparison (MemoryDB empty — likely expired event)")
            return {
                'leaderboard': config['leaderboardName'],
                'memorydb_count': 0,
                'dynamodb_count': 0,
                'matches': 0,
                'mismatches': 0,
                'total_compared': 0,
                'skipped': True
            }
        
        # Get DynamoDB scores (full pagination)
        print("   🗄️  Querying DynamoDB...")
        dynamodb_records = self.get_dynamodb_scores(leaderboard_name)
        print(f"   ✅ DynamoDB: {len(dynamodb_records)} raw records found")
        
        # Calculate final DynamoDB scores
        dynamodb_final = self.calculate_final_scores(
            dynamodb_records,
            config['scoreStrategy'],
            config['scoreType'],
            config['leaderboardType']
        )
        
        # Sort DynamoDB scores
        dynamodb_sorted = self.sort_scores(
            dynamodb_final,
            config['scoreType'],
            config['leaderboardType']
        )
        
        print(f"   ✅ DynamoDB: {len(dynamodb_sorted)} final entries calculated")

        # Cross-check total player counts
        if memorydb_total != len(dynamodb_sorted):
            print(f"   ⚠️  Player count mismatch: API totalPlayers={memorydb_total}, DynamoDB unique players={len(dynamodb_sorted)}")
        else:
            print(f"   ✅ Player counts match: {memorydb_total}")

        # Compare results
        comparison_result = self._compare_results(memorydb_scores, dynamodb_sorted, config, show_full)
        
        return comparison_result
    
    def _compare_results(self, memorydb_scores: List[Dict], dynamodb_scores: List[Tuple[str, Decimal]], config: Dict, show_full: bool) -> Dict:
        """Compare and display results - position by position comparison"""
        max_entries = max(len(memorydb_scores), len(dynamodb_scores))
        if not show_full:
            max_entries = min(max_entries, 50)
        
        matches = 0
        mismatches = 0
        
        # Show full datasets if requested
        if show_full:
            print(f"\n   📋 FULL MEMORYDB/VALKEY DATASET:")
            print("   ┌─────┬─────────────────────────┬─────────────┬─────────────────────────┐")
            print("   │Rank │ Player ID               │    Score    │ Last Updated            │")
            print("   ├─────┼─────────────────────────┼─────────────┼─────────────────────────┤")
            
            for i, score_data in enumerate(memorydb_scores):
                player = score_data.get('playerID', '')
                score = str(score_data.get('score', ''))
                rank = score_data.get('rank', i+1)
                
                print(f"   │ {rank:3d} │ {player:23s} │ {score:11s} │ {'via API':23s} │")
            
            print("   └─────┴─────────────────────────┴─────────────┴─────────────────────────┘")
            
            print(f"\n   📋 FULL DYNAMODB DATASET:")
            print("   ┌─────┬─────────────────────────┬─────────────┐")
            print("   │Rank │ Player ID               │    Score    │")
            print("   ├─────┼─────────────────────────┼─────────────┤")
            
            for i, (player, score) in enumerate(dynamodb_scores):
                print(f"   │ {i+1:3d} │ {player:23s} │ {float(score):11.1f} │")
            
            print("   └─────┴─────────────────────────┴─────────────┘")
            print()
        
        print(f"\n   📊 COMPARISON RESULTS (Top {max_entries}):")
        print("   ┌─────┬─────────────────────────┬─────────────────────────┬─────────────────────────┬─────────────────────────┬────────┐")
        print("   │Rank │ MemoryDB Player         │    MemoryDB Score       │ DynamoDB Player         │    DynamoDB Score       │ Status │")
        print("   ├─────┼─────────────────────────┼─────────────────────────┼─────────────────────────┼─────────────────────────┼────────┤")
        
        for i in range(max_entries):
            # Get MemoryDB entry
            memorydb_player = ""
            memorydb_score = ""
            if i < len(memorydb_scores):
                memorydb_player = memorydb_scores[i].get('playerID', '')
                raw_score = str(memorydb_scores[i].get('score', ''))
                # API score may be in display format (e.g., ms); pass config to normalize
                memorydb_score = self._format_time_display(raw_score, config['scoreType'], config)

            # Get DynamoDB entry
            dynamodb_player = ""
            dynamodb_score = ""
            if i < len(dynamodb_scores):
                dynamodb_player = dynamodb_scores[i][0]
                raw_score = str(float(dynamodb_scores[i][1]))
                # DynamoDB stores raw seconds; pass config with timeFormat=seconds for display
                ddb_config = dict(config, timeFormat='seconds') if config else None
                dynamodb_score = self._format_time_display(raw_score, config['scoreType'], ddb_config)
            
            # Position-by-position comparison (no forcing matches)
            status = self._get_match_status(memorydb_player, str(memorydb_scores[i].get('score', '')) if i < len(memorydb_scores) else '', dynamodb_player, str(float(dynamodb_scores[i][1])) if i < len(dynamodb_scores) else '', config)
            if status in ["✅", "🔀"]:
                matches += 1  # 🔀 = tie-order difference (same score, harmless)
            elif status in ["❌", "🔴"]:
                mismatches += 1
            
            print(f"   │ {i+1:3d} │ {memorydb_player:23s} │ {memorydb_score:23s} │ {dynamodb_player:23s} │ {dynamodb_score:23s} │ {status:6s} │")
        
        print("   └─────┴─────────────────────────┴─────────────────────────┴─────────────────────────┴─────────────────────────┴────────┘")
        
        return {
            'leaderboard': config['leaderboardName'],
            'memorydb_count': len(memorydb_scores),
            'dynamodb_count': len(dynamodb_scores),
            'matches': matches,
            'mismatches': mismatches,
            'total_compared': max_entries
        }
    
    def _get_match_status(self, memorydb_player: str, memorydb_score: str, dynamodb_player: str, dynamodb_score: str, config: Dict = None) -> str:
        """Determine match status between MemoryDB and DynamoDB entries.

        The API returns scores in display format (e.g., milliseconds * 1000, or MM:SS strings).
        DynamoDB stores raw seconds. We must normalize both to seconds before comparing.
        """
        if not memorydb_player and not dynamodb_player:
            return "✅"
        elif not memorydb_player or not dynamodb_player:
            return "⚠️"

        try:
            mem_val = self._api_score_to_seconds(memorydb_score, config)
            dyn_val = float(dynamodb_score) if dynamodb_score else 0
            scores_match = abs(mem_val - dyn_val) < 0.01
        except (ValueError, TypeError):
            scores_match = False

        if memorydb_player == dynamodb_player:
            if scores_match:
                return "✅"
            else:
                return "🔴"  # Same player, different score — real mismatch
        else:
            if scores_match:
                return "🔀"  # Different players but same score — tie-order difference (harmless)
            else:
                return "❌"  # Different players AND different scores

    def _api_score_to_seconds(self, score_str: str, config: Dict = None) -> float:
        """Convert an API-returned display score back to raw seconds.

        The API formats scores based on timeFormat:
        - seconds: returned as-is (e.g., 83.456)
        - milliseconds: returned as ms (e.g., 2000.0) → divide by 1000 to get seconds
        - minutes_seconds: returned as "M:SS.mmm" → parse to seconds
        - hours_minutes_seconds: returned as "H:MM:SS.mmm" → parse to seconds
        Non-time scores are returned as-is.
        """
        if not score_str:
            return 0.0

        time_format = (config or {}).get('timeFormat', 'seconds')
        score_type = (config or {}).get('scoreType', 'score')

        # For non-time scores, just parse as float
        if score_type != 'time':
            return float(score_str)

        # If it contains colons, it's a formatted time string → parse to seconds
        if ':' in str(score_str):
            return self._parse_score_to_seconds(str(score_str))

        # Numeric value — reverse the display format conversion
        val = float(score_str)
        if time_format == 'milliseconds':
            return val / 1000.0  # API multiplied by 1000, reverse it
        else:
            return val  # seconds, or other formats that return numeric
    
    def _format_time_display(self, score_str: str, score_type: str, config: Dict = None) -> str:
        """Format time scores to show both HH:MM:SS.sss and (decimal seconds) formats.

        For API scores: converts display value back to seconds for the parenthetical.
        For DynamoDB scores: raw value IS seconds, formats to time string.
        """
        if score_type != 'time':
            return score_str

        try:
            # Get the actual seconds value (reversing any display format)
            seconds = self._api_score_to_seconds(score_str, config)

            # Format as time string with seconds in brackets
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = seconds % 60

            if hours > 0:
                time_str = f"{hours}:{minutes:02d}:{secs:06.3f}"
            else:
                time_str = f"{minutes}:{secs:06.3f}"

            return f"{time_str} ({seconds:.3f}s)"
        except:
            return score_str

    def _parse_score_to_seconds(self, score_str: str) -> float:
        """Parse score string to seconds, handling time formats"""
        if not score_str:
            return 0.0
        
        # If it's already a number, return it
        try:
            return float(score_str)
        except ValueError:
            pass
        
        # Handle time formats: HH:MM:SS.mmm, MM:SS.mmm, SS.mmm
        if ':' in score_str:
            parts = score_str.split(':')
            if len(parts) == 3:  # HH:MM:SS.mmm
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:  # MM:SS.mmm
                minutes = float(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
        
        # If all else fails, try to parse as float
        return float(score_str)
    
    def get_all_stats_data(self) -> List[Dict]:
        """Get stats data from DynamoDB for discovery analysis (limited sample)"""
        try:
            response = self.dynamodb.scan(
                TableName='game-statsleaderboards-dev-stats',
                Limit=1000
            )
            
            records = []
            for item in response.get('Items', []):
                try:
                    records.append({
                        'gameID': item['gameID']['S'],
                        'gameMode': item['gameMode']['S'],
                        'leaderboardName': item['leaderboardName']['S'],
                        'playerID': item['playerID']['S'],
                        'playerScore': Decimal(item['playerScore']['N']),
                        'timestamp': item.get('timestamp', {}).get('S', '')
                    })
                except KeyError:
                    continue
            
            return records
            
        except Exception as e:
            print(f"❌ Failed to get stats data: {e}")
            return []
    
    def discovery_analysis(self):
        """Perform discovery analysis like the shell script"""
        print("🔍 DISCOVERY ANALYSIS")
        print("=" * 25)
        
        # Get all stats data
        print("📊 Fetching data from DynamoDB (limited scan)...")
        stats_data = self.get_all_stats_data()
        print(f"📈 Total Stats Records: {len(stats_data)}")
        print()
        
        # Discover games
        games = {}
        for record in stats_data:
            game_id = record['gameID']
            if game_id not in games:
                games[game_id] = 0
            games[game_id] += 1
        
        print("🎮 Games Found:")
        for game_id, count in sorted(games.items()):
            print(f"  • {game_id} ({count} records)")
        print()
        
        # Discover game modes per game
        print("🎯 Game Modes by Game:")
        for game_id in sorted(games.keys()):
            print(f"  📦 {game_id}:")
            game_modes = {}
            for record in stats_data:
                if record['gameID'] == game_id:
                    mode = record['gameMode']
                    if mode not in game_modes:
                        game_modes[mode] = 0
                    game_modes[mode] += 1
            
            # Sort by count descending
            for mode, count in sorted(game_modes.items(), key=lambda x: x[1], reverse=True):
                print(f"    • {mode:<20}: {count} records")
        print()
        
        return stats_data

    def analyze_all(self, show_full: bool = False):
        """Analyze all leaderboards with temporary DynamoDB throughput boost"""
        
        stats_table = 'game-statsleaderboards-dev-stats'
        original_billing = None
        
        try:
            # Save current billing mode and boost throughput
            original_billing = self._boost_table_throughput(stats_table, target_rcu=30000, target_wcu=30000)
            
            self._run_analysis(show_full)
            
        finally:
            # ALWAYS restore original billing mode, even if analysis fails
            if original_billing:
                self._restore_table_throughput(stats_table, original_billing)
    
    def _boost_table_throughput(self, table_name: str, target_rcu: int = 30000, target_wcu: int = 30000) -> Dict:
        """
        Save current billing mode and switch to provisioned with high throughput.
        Also provisions GSIs proportionately. Returns the original state for restoration.
        """
        print(f"\n⚡ Boosting DynamoDB throughput for {table_name}...")
        import time
        
        try:
            # Check account limits
            limits = self.dynamodb.describe_limits()
            table_max_rcu = limits.get('TableMaxReadCapacityUnits', 40000)
            table_max_wcu = limits.get('TableMaxWriteCapacityUnits', 40000)
            acct_max_rcu = limits.get('AccountMaxReadCapacityUnits', 80000)
            acct_max_wcu = limits.get('AccountMaxWriteCapacityUnits', 80000)
            
            # Count GSIs to calculate per-unit allocation
            desc = self.dynamodb.describe_table(TableName=table_name)['Table']
            num_gsis = len(desc.get('GlobalSecondaryIndexes', []))
            total_units = 1 + num_gsis  # table + GSIs
            
            # For analysis, we only READ — maximize RCU, minimize WCU
            # GSI RCU matters here because we query via leaderboardName-timestamp-index
            gsi_wcu = 1  # Not writing anything
            table_wcu = 1
            headroom = 5000
            
            # Split RCU across table + GSIs (we query the GSI, so it needs RCU too)
            per_unit_rcu = min((acct_max_rcu - headroom) // total_units, table_max_rcu)
            
            total_rcu = per_unit_rcu * total_units
            total_wcu = table_wcu + (gsi_wcu * num_gsis)
            
            print(f"   Account limits: RCU={acct_max_rcu:,}, WCU={acct_max_wcu:,}")
            print(f"   Read-only mode: Table RCU={per_unit_rcu:,}, GSI RCU={per_unit_rcu:,} each, WCU={gsi_wcu} (minimal)")
            print(f"   Total: RCU={total_rcu:,}, WCU={total_wcu:,}")
            original = {
                'billing_mode': desc.get('BillingModeSummary', {}).get('BillingMode', 'PAY_PER_REQUEST'),
                'read_capacity': desc.get('ProvisionedThroughput', {}).get('ReadCapacityUnits', 0),
                'write_capacity': desc.get('ProvisionedThroughput', {}).get('WriteCapacityUnits', 0),
                'gsis': []
            }
            
            # Save GSI original state
            for gsi in desc.get('GlobalSecondaryIndexes', []):
                original['gsis'].append({
                    'name': gsi['IndexName'],
                    'read_capacity': gsi.get('ProvisionedThroughput', {}).get('ReadCapacityUnits', 0),
                    'write_capacity': gsi.get('ProvisionedThroughput', {}).get('WriteCapacityUnits', 0),
                })
            
            print(f"   Current: {original['billing_mode']} (RCU: {original['read_capacity']}, WCU: {original['write_capacity']})")
            for gsi in original['gsis']:
                print(f"   GSI {gsi['name']}: RCU={gsi['read_capacity']}, WCU={gsi['write_capacity']}")
            
            # Build GSI updates — high RCU for queries, minimal WCU
            gsi_updates = []
            for gsi in desc.get('GlobalSecondaryIndexes', []):
                gsi_updates.append({
                    'Update': {
                        'IndexName': gsi['IndexName'],
                        'ProvisionedThroughput': {
                            'ReadCapacityUnits': per_unit_rcu,
                            'WriteCapacityUnits': gsi_wcu
                        }
                    }
                })
            
            # Switch to provisioned (read-optimized)
            print(f"   Switching to PROVISIONED (read-optimized)...")
            update_kwargs = {
                'TableName': table_name,
                'BillingMode': 'PROVISIONED',
                'ProvisionedThroughput': {
                    'ReadCapacityUnits': per_unit_rcu,
                    'WriteCapacityUnits': table_wcu
                }
            }
            if gsi_updates:
                update_kwargs['GlobalSecondaryIndexUpdates'] = gsi_updates
            
            self.dynamodb.update_table(**update_kwargs)
            
            # Wait for table AND all GSIs to become active
            print("   Waiting for table + GSIs to become active...", flush=True)
            for attempt in range(90):  # Up to 3 minutes
                time.sleep(2)
                status_desc = self.dynamodb.describe_table(TableName=table_name)['Table']
                table_status = status_desc['TableStatus']
                gsi_statuses = [g['IndexStatus'] for g in status_desc.get('GlobalSecondaryIndexes', [])]
                all_active = table_status == 'ACTIVE' and all(s == 'ACTIVE' for s in gsi_statuses)
                
                if attempt % 5 == 0:
                    print(f"   [{attempt*2}s] Table: {table_status}, GSIs: {gsi_statuses}", flush=True)
                
                if all_active:
                    print(f"   ✅ All active after {attempt*2}s")
                    break
            else:
                print(f"   ⚠️  Timed out waiting — proceeding anyway")
            
            return original
            
        except Exception as e:
            print(f"   ❌ Failed to boost throughput: {e}")
            print(f"   Continuing with current throughput (may be slow)...")
            return None
    
    def _restore_table_throughput(self, table_name: str, original: Dict):
        """Restore table and GSIs to original billing mode and throughput."""
        print(f"\n🔄 Restoring DynamoDB throughput for {table_name}...")
        import time
        
        try:
            if original['billing_mode'] == 'PAY_PER_REQUEST':
                print(f"   Switching back to PAY_PER_REQUEST (on-demand)...")
                self.dynamodb.update_table(
                    TableName=table_name,
                    BillingMode='PAY_PER_REQUEST'
                )
            else:
                # Restore provisioned with original values + GSIs
                gsi_updates = []
                for gsi in original.get('gsis', []):
                    gsi_updates.append({
                        'Update': {
                            'IndexName': gsi['name'],
                            'ProvisionedThroughput': {
                                'ReadCapacityUnits': max(gsi['read_capacity'], 1),
                                'WriteCapacityUnits': max(gsi['write_capacity'], 1)
                            }
                        }
                    })
                
                update_kwargs = {
                    'TableName': table_name,
                    'BillingMode': 'PROVISIONED',
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': max(original['read_capacity'], 1),
                        'WriteCapacityUnits': max(original['write_capacity'], 1)
                    }
                }
                if gsi_updates:
                    update_kwargs['GlobalSecondaryIndexUpdates'] = gsi_updates
                
                print(f"   Restoring PROVISIONED (RCU: {original['read_capacity']}, WCU: {original['write_capacity']})...")
                self.dynamodb.update_table(**update_kwargs)
            
            # Wait for full restoration — verify everything is back
            print("   Waiting for table + GSIs to become active...", flush=True)
            for attempt in range(90):
                time.sleep(2)
                status_desc = self.dynamodb.describe_table(TableName=table_name)['Table']
                table_status = status_desc['TableStatus']
                gsi_statuses = [g['IndexStatus'] for g in status_desc.get('GlobalSecondaryIndexes', [])]
                all_active = table_status == 'ACTIVE' and all(s == 'ACTIVE' for s in gsi_statuses)
                
                if attempt % 5 == 0:
                    print(f"   [{attempt*2}s] Table: {table_status}, GSIs: {gsi_statuses}", flush=True)
                
                if all_active:
                    break
            
            # Verify final state
            final_desc = self.dynamodb.describe_table(TableName=table_name)['Table']
            final_billing = final_desc.get('BillingModeSummary', {}).get('BillingMode', 'UNKNOWN')
            final_rcu = final_desc.get('ProvisionedThroughput', {}).get('ReadCapacityUnits', 0)
            final_wcu = final_desc.get('ProvisionedThroughput', {}).get('WriteCapacityUnits', 0)
            
            print(f"   ✅ Restored: {final_billing} (RCU: {final_rcu}, WCU: {final_wcu})")
            
            if final_billing != original['billing_mode']:
                print(f"   ⚠️  WARNING: Billing mode is {final_billing}, expected {original['billing_mode']}")
            
        except Exception as e:
            print(f"\n   ❌ CRITICAL: Failed to restore table: {e}")
            print(f"   ⚠️  MANUAL ACTION REQUIRED:")
            if original['billing_mode'] == 'PAY_PER_REQUEST':
                print(f"   Run: aws dynamodb update-table --table-name {table_name} --billing-mode PAY_PER_REQUEST --profile {self.profile} --region {self.region}")
            else:
                print(f"   Run: aws dynamodb update-table --table-name {table_name} --billing-mode PROVISIONED --provisioned-throughput ReadCapacityUnits={original['read_capacity']},WriteCapacityUnits={original['write_capacity']} --profile {self.profile} --region {self.region}")
    
    def _run_analysis(self, show_full: bool = False):
        """Core analysis logic (separated for throughput boost wrapper)"""
        print("🎮 COMPREHENSIVE GAME STATS & LEADERBOARDS ANALYSIS")
        print("=" * 60)
        print("📊 Comparing MemoryDB/Valkey vs DynamoDB Results\n")
        
        # Discovery analysis
        stats_data = self.discovery_analysis()
        
        configs = self.get_leaderboard_configs()
        if not configs:
            print("❌ No leaderboard configurations found")
            return
        
        # Show leaderboard configurations
        print("🏆 Leaderboards Found:")
        for lb_name, config in sorted(configs.items()):
            print(f"  🏅 {lb_name} ({config['scoreType']}, {config['scoreStrategy']} strategy, {config['leaderboardType']}, mode: {config['gameMode']})")
        print()
        
        print("⚖️  MEMORYDB/VALKEY vs DYNAMODB COMPARISON")
        print("=" * 42)
        
        total_results = []
        
        for leaderboard_name, config in configs.items():
            try:
                result = self.compare_leaderboard(leaderboard_name, config, show_full)
                total_results.append(result)
            except Exception as e:
                print(f"   ❌ Analysis failed: {e}")
        
        # Summary
        print("\n" + "=" * 60)
        print("📋 ANALYSIS SUMMARY")
        print("=" * 60)
        
        total_matches = sum(r['matches'] for r in total_results)
        total_mismatches = sum(r['mismatches'] for r in total_results)
        total_compared = sum(r['total_compared'] for r in total_results)
        
        print(f"✅ Total matches: {total_matches}")
        print(f"❌ Total mismatches: {total_mismatches}")
        print(f"📊 Total entries compared: {total_compared}")
        
        if total_compared > 0:
            accuracy = (total_matches / total_compared) * 100
            print(f"🎯 Accuracy: {accuracy:.1f}%")
        
        print(f"✅ Compared MemoryDB/Valkey (via Lambda API) vs DynamoDB (direct query)")
        print(f"🔧 API Key source: SSM Parameter Store (source of truth)")
        print(f"📊 Legend: ✅ = Match, ❌ = Mismatch, ⚠️ = Missing data, 🔴 = Score mismatch")
        print(f"🔍 Mode: {'Full dataset' if show_full else 'Summary (top 50 per leaderboard)'}")
        print("🎯 Analysis complete!")

def main():
    parser = argparse.ArgumentParser(description='Analyze leaderboard data consistency')
    parser.add_argument('--full', '-f', action='store_true', help='Show full datasets')
    parser.add_argument('--region', default='us-west-2', help='AWS region')
    parser.add_argument('--profile', default='default', help='AWS profile')
    
    args = parser.parse_args()
    
    analyzer = LeaderboardAnalyzer(region=args.region, profile=args.profile)
    analyzer.analyze_all(show_full=args.full)

if __name__ == '__main__':
    main()
