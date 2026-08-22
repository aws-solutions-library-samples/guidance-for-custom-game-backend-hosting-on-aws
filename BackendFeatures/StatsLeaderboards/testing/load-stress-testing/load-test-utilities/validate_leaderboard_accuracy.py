#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Definitive Leaderboard Accuracy Validation Script

1. Creates 6 test leaderboards (all strategy/type combos) via the API
2. Submits known scores (players × 6 LBs × scores each)
3. Verifies after every 10th submission via player standing API
4. Final 3-way comparison: in-memory expected vs DynamoDB vs MemoryDB (API)
5. Prints Valkey commands for manual bastion verification

Parallelized across players (each player's submissions are sequential).
Supports free-threaded Python 3.14t for true parallelism.

Usage:
    python3 testing/validate_leaderboard_accuracy.py --profile dev_aws_profile --region us-west-2
    python3.14t testing/validate_leaderboard_accuracy.py --players 10 --scores 200
"""

import boto3
import json
import time
import random
import threading
import os
import sys
import requests
import sys
import argparse
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict
from datetime import datetime, timezone

GAME_ID = "wars-of-valoria"
NUM_PLAYERS = 10
SCORES_PER_PLAYER_PER_LB = 100
PLAYER_PREFIX = "vld"

TEST_LEADERBOARDS = [
    {"leaderboardName": "validate-cumulative-desc-score", "gameMode": "story-quest",
     "scoreStrategy": "cumulative", "leaderboardType": "DESCENDING_LB",
     "scoreType": "score", "statAttributeForLeaderboard": "total_playtime",
     "score_range": (100, 5000)},
    {"leaderboardName": "validate-cumulative-asc-time", "gameMode": "dungeon-raid",
     "scoreStrategy": "cumulative", "leaderboardType": "ASCENDING_LB",
     "scoreType": "score", "statAttributeForLeaderboard": "total_playtime",
     "score_range": (10, 500)},
    {"leaderboardName": "validate-best-desc-score", "gameMode": "story-quest",
     "scoreStrategy": "best", "leaderboardType": "DESCENDING_LB",
     "scoreType": "score", "statAttributeForLeaderboard": "performance_index",
     "score_range": (1, 1000)},
    {"leaderboardName": "validate-best-asc-level", "gameMode": "dungeon-raid",
     "scoreStrategy": "best", "leaderboardType": "ASCENDING_LB",
     "scoreType": "level", "statAttributeForLeaderboard": "efficiency_rating",
     "score_range": (200, 400)},
    {"leaderboardName": "validate-replace-desc-score", "gameMode": "story-quest",
     "scoreStrategy": "replace", "leaderboardType": "DESCENDING_LB",
     "scoreType": "score", "statAttributeForLeaderboard": "performance_index",
     "score_range": (50, 950)},
    {"leaderboardName": "validate-replace-asc-level", "gameMode": "dungeon-raid",
     "scoreStrategy": "replace", "leaderboardType": "ASCENDING_LB",
     "scoreType": "level", "statAttributeForLeaderboard": "efficiency_rating",
     "score_range": (100, 300)},
]


class LeaderboardValidator:
    def __init__(self, region, profile):
        self.region = region
        self.session = boto3.Session(profile_name=profile, region_name=region)
        self.dynamodb = self.session.client('dynamodb', region_name=region)
        
        ssm = self.session.client('ssm', region_name=region)
        ep = ssm.get_parameter(Name='/game-statsleaderboards-dev/api/endpoint')
        self.api = ep['Parameter']['Value'].rstrip('/') + '/'
        
        keys = ssm.get_parameters_by_path(Path='/game-statsleaderboards-dev/api-keys/', WithDecryption=True, Recursive=False)
        kd = json.loads(keys['Parameters'][0]['Value'])
        self.api_key = kd['apiKey']
        self.game_id = GAME_ID
        
        self.headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_key}'}
        
        # In-memory tracking
        self.expected = {}       # {(lb,player): Decimal}
        self.sub_count = {}      # {(lb,player): int}
        self.all_scores = {}     # {(lb,player): [Decimal,...]}
        self.errors = []
        self.verify_fails = []
        
        print(f"✅ API: {self.api}")
        print(f"✅ Game: {self.game_id}")
    
    def _post(self, path, body, timeout=15):
        return requests.post(f"{self.api}{path}", json=body, headers=self.headers, timeout=timeout)
    
    # ================================================================
    # Phase 0: Create leaderboards
    # ================================================================
    def create_leaderboards(self):
        print(f"\n{'='*70}")
        print(f"PHASE 0: Creating {len(TEST_LEADERBOARDS)} test leaderboards")
        print(f"{'='*70}")
        
        for lb in TEST_LEADERBOARDS:
            name = lb['leaderboardName']
            body = {
                "gameLeaderboardConfigRequest": {
                    "gameID": self.game_id,
                    "leaderboardName": name,
                    "gameMode": lb['gameMode'],
                    "leaderboardType": lb['leaderboardType'],
                    "scoreType": lb['scoreType'],
                    "scoreStrategy": lb['scoreStrategy'],
                    "statAttributeForLeaderboard": lb['statAttributeForLeaderboard']
                }
            }
            
            try:
                resp = self._post("leaderboards/config", body)
                if resp.status_code in (200, 201, 409):
                    status = "created" if resp.status_code != 409 else "already exists"
                    print(f"  ✅ {name} ({lb['scoreStrategy']}, {lb['leaderboardType']}) — {status}")
                else:
                    print(f"  ❌ {name} — HTTP {resp.status_code}: {resp.text[:200]}")
                    self.errors.append(f"Create LB failed: {name} HTTP {resp.status_code}")
            except Exception as e:
                print(f"  ❌ {name} — {e}")
                self.errors.append(f"Create LB error: {name}: {e}")
            
            time.sleep(0.5)
        
        print(f"  Waiting 3s for leaderboard configs to propagate...")
        time.sleep(3)


    # ================================================================
    # Phase 1: Submit scores with inline verification (parallelized across players)
    # ================================================================
    def submit_all_scores(self):
        players = [f"{PLAYER_PREFIX}_{i:04d}" for i in range(1, NUM_PLAYERS + 1)]
        total = NUM_PLAYERS * len(TEST_LEADERBOARDS) * SCORES_PER_PLAYER_PER_LB
        
        # Detect free-threaded Python
        gil_disabled = hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()
        max_workers = min(NUM_PLAYERS, 5)
        
        print(f"\n{'='*70}")
        print(f"PHASE 1: Submitting {total:,} scores ({NUM_PLAYERS} players × {len(TEST_LEADERBOARDS)} LBs × {SCORES_PER_PLAYER_PER_LB} scores)")
        print(f"  Workers: {max_workers} {'(free-threaded)' if gil_disabled else '(GIL)'}")
        print(f"{'='*70}")
        
        # Thread-safe progress counters
        progress_lock = threading.Lock()
        progress = {'n': 0, 'inline_mismatches': 0, 'last_print': 0}
        t0 = time.time()
        
        def process_player(pid):
            """Process all leaderboards for a single player (sequential per player)."""
            local_errors = []
            local_verify_fails = []
            local_expected = {}
            local_sub_count = {}
            local_all_scores = {}
            
            for lb in TEST_LEADERBOARDS:
                lbn = lb['leaderboardName']
                strategy = lb['scoreStrategy']
                lbt = lb['leaderboardType']
                lo, hi = lb['score_range']
                
                key = (lbn, pid)
                local_sub_count[key] = 0
                local_all_scores[key] = []
                running = Decimal('0')
                best = None
                
                seed = hash(f"{pid}_{lbn}") & 0xFFFFFFFF
                rng = random.Random(seed)
                
                for i in range(SCORES_PER_PLAYER_PER_LB):
                    score = round(rng.uniform(lo, hi), 2)
                    score_dec = Decimal(str(score))
                    
                    # Update running expected
                    if strategy == 'cumulative':
                        running += score_dec
                    elif strategy == 'best':
                        if best is None:
                            best = score_dec
                        elif lbt == 'ASCENDING_LB':
                            best = min(best, score_dec)
                        else:
                            best = max(best, score_dec)
                        running = best
                    elif strategy == 'replace':
                        running = score_dec
                    
                    # Submit via API
                    body = {"gameReportBody": {
                        "playerID": pid, "gameID": self.game_id, "gameMode": lb['gameMode'],
                        "playerScore": score, "leaderboardName": lbn,
                        "fullRawGameReport": {"playerLevel": 1, "sessionID": f"v-{pid}-{i}",
                                              "gameMode": lb['gameMode'], lb['statAttributeForLeaderboard']: score}
                    }}
                    
                    try:
                        resp = self._post("leaderboards/stats", body)
                        if resp.status_code == 200:
                            local_sub_count[key] += 1
                            local_all_scores[key].append(score_dec)
                        else:
                            local_errors.append(f"Submit {lbn}/{pid}#{i}: HTTP {resp.status_code}")
                    except Exception as e:
                        local_errors.append(f"Submit {lbn}/{pid}#{i}: {e}")
                    
                    # Verify every 10th and last
                    if (i + 1) % 10 == 0 or i == SCORES_PER_PLAYER_PER_LB - 1:
                        time.sleep(0.15)
                        api_score = self._get_standing(lb, pid)
                        if api_score is not None:
                            diff = abs(float(api_score) - float(running))
                            if diff > 0.15:
                                local_verify_fails.append({
                                    'lb': lbn, 'player': pid, 'sub': i+1,
                                    'expected': float(running), 'got': float(api_score), 'diff': diff
                                })
                    
                    # Thread-safe progress update
                    with progress_lock:
                        progress['n'] += 1
                        progress['inline_mismatches'] += len(local_verify_fails) - progress.get(f'_prev_{pid}', 0)
                        progress[f'_prev_{pid}'] = len(local_verify_fails)
                        
                        if progress['n'] - progress['last_print'] >= 50:
                            progress['last_print'] = progress['n']
                            elapsed = time.time() - t0
                            rate = progress['n'] / elapsed if elapsed > 0 else 0
                            print(f"    [{progress['n']:,}/{total:,}] {rate:.0f}/s | mismatches: {progress['inline_mismatches']}", flush=True)
                
                local_expected[key] = running
            
            return local_expected, local_sub_count, local_all_scores, local_errors, local_verify_fails
        
        # Run players in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_player, pid): pid for pid in players}
            
            for future in as_completed(futures):
                pid = futures[future]
                try:
                    exp, sc, asc, errs, vf = future.result()
                    self.expected.update(exp)
                    self.sub_count.update(sc)
                    self.all_scores.update(asc)
                    self.errors.extend(errs)
                    self.verify_fails.extend(vf)
                except Exception as e:
                    print(f"  ❌ Player {pid} failed: {e}")
                    self.errors.append(f"Player {pid} thread error: {e}")
        
        elapsed = time.time() - t0
        n = progress['n']
        print(f"\n  ✅ Phase 1 complete: {n:,} submissions in {elapsed:.0f}s ({n/elapsed:.0f}/s)")
        print(f"     Submission errors: {len(self.errors)}")
        print(f"     Inline verification mismatches: {len(self.verify_fails)}")
        return players
    
    def _get_standing(self, lb, pid):
        """Query player standing. Returns score as Decimal or None."""
        body = {"playerLBStandingRequest": {
            "playerID": pid, "gameID": self.game_id, "gameMode": lb['gameMode'],
            "leaderboardName": lb['leaderboardName'],
            "includePercentile": False, "includeNeighbours": False
        }}
        try:
            resp = self._post("leaderboards/player/standing", body)
            if resp.status_code == 200:
                info = resp.json().get('playerLBStandingResponse', {}).get('playerLBStandingInfo', {})
                s = info.get('score')
                return Decimal(str(s)) if s is not None else None
        except Exception:
            pass
        return None


    # ================================================================
    # Phase 2: Final 3-way comparison
    # ================================================================
    def final_comparison(self, players):
        print(f"\n{'='*70}")
        print(f"PHASE 2: Final 3-way comparison (In-Memory vs DynamoDB vs API/MemoryDB)")
        print(f"{'='*70}")
        
        results = []  # List of dicts for summary table
        
        for lb in TEST_LEADERBOARDS:
            lbn = lb['leaderboardName']
            strategy = lb['scoreStrategy']
            lbt = lb['leaderboardType']
            
            print(f"\n  {'─'*66}")
            print(f"  📋 {lbn} ({strategy}, {lbt})")
            print(f"  {'─'*66}")
            print(f"  {'Player':<16} {'Subs':>5} {'DDB#':>5} {'Expected':>14} {'DDB Computed':>14} {'API Score':>14} {'DDB':>4} {'API':>4}")
            print(f"  {'─'*16} {'─'*5} {'─'*5} {'─'*14} {'─'*14} {'─'*14} {'─'*4} {'─'*4}")
            
            for pid in players:
                key = (lbn, pid)
                expected = self.expected.get(key, Decimal('0'))
                sub_count = self.sub_count.get(key, 0)
                scores = self.all_scores.get(key, [])
                
                # DynamoDB query
                ddb_scores = self._query_ddb(lbn, pid)
                ddb_count = len(ddb_scores)
                
                if strategy == 'cumulative':
                    ddb_computed = sum(ddb_scores) if ddb_scores else Decimal('0')
                elif strategy == 'best':
                    if lbt == 'ASCENDING_LB':
                        ddb_computed = min(ddb_scores) if ddb_scores else Decimal('0')
                    else:
                        ddb_computed = max(ddb_scores) if ddb_scores else Decimal('0')
                elif strategy == 'replace':
                    ddb_computed = ddb_scores[-1] if ddb_scores else Decimal('0')
                else:
                    ddb_computed = Decimal('0')
                
                # API query (MemoryDB)
                api_score = self._get_standing(lb, pid)
                api_dec = api_score if api_score is not None else Decimal('0')
                
                # Compare
                ddb_ok = abs(float(ddb_computed) - float(expected)) < 0.15
                api_ok = abs(float(api_dec) - float(expected)) < 0.15
                count_ok = ddb_count == sub_count
                
                ddb_sym = "✅" if ddb_ok else "❌"
                api_sym = "✅" if api_ok else "❌"
                count_note = "" if count_ok else f" ⚠({ddb_count})"
                
                print(f"  {pid:<16} {sub_count:>5} {ddb_count:>5}{count_note} {float(expected):>14.2f} {float(ddb_computed):>14.2f} {float(api_dec):>14.2f} {ddb_sym:>4} {api_sym:>4}")
                
                results.append({
                    'lb': lbn, 'strategy': strategy, 'type': lbt, 'player': pid,
                    'subs': sub_count, 'ddb_count': ddb_count,
                    'expected': float(expected), 'ddb': float(ddb_computed), 'api': float(api_dec),
                    'ddb_ok': ddb_ok, 'api_ok': api_ok, 'count_ok': count_ok
                })
        
        return results
    
    def _query_ddb(self, lb_name, player_id):
        """Query all DynamoDB records for a player/leaderboard, ordered by timestamp."""
        records = []
        kwargs = {
            'TableName': 'game-statsleaderboards-dev-stats',
            'IndexName': 'leaderboardName-timestamp-index',
            'KeyConditionExpression': 'leaderboardName = :lb',
            'FilterExpression': 'playerID = :pid',
            'ExpressionAttributeValues': {':lb': {'S': lb_name}, ':pid': {'S': player_id}},
            'ProjectionExpression': 'playerScore,#ts',
            'ExpressionAttributeNames': {'#ts': 'timestamp'}
        }
        while True:
            resp = self.dynamodb.query(**kwargs)
            for item in resp.get('Items', []):
                records.append((int(item.get('timestamp', {}).get('N', '0')), Decimal(item['playerScore']['N'])))
            if 'LastEvaluatedKey' not in resp:
                break
            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        
        # Sort by timestamp and return just scores
        records.sort(key=lambda x: x[0])
        return [s for _, s in records]


    # ================================================================
    # Phase 3: Summary + Valkey commands
    # ================================================================
    def print_summary(self, results, players):
        total = len(results)
        ddb_pass = sum(1 for r in results if r['ddb_ok'])
        api_pass = sum(1 for r in results if r['api_ok'])
        count_pass = sum(1 for r in results if r['count_ok'])
        
        print(f"\n{'='*70}")
        print(f"VALIDATION SUMMARY")
        print(f"{'='*70}")
        print(f"  Total checks:              {total}")
        print(f"  DDB record count match:    {count_pass}/{total} {'✅' if count_pass == total else '❌'}")
        print(f"  DDB score match:           {ddb_pass}/{total} {'✅' if ddb_pass == total else '❌'}")
        print(f"  API (MemoryDB) score match: {api_pass}/{total} {'✅' if api_pass == total else '❌'}")
        print(f"  Submission errors:         {len(self.errors)} {'✅' if not self.errors else '❌'}")
        print(f"  Inline verify failures:    {len(self.verify_fails)} {'✅' if not self.verify_fails else '❌'}")
        
        if self.verify_fails:
            print(f"\n  --- Inline Verification Failures (first 10) ---")
            for f in self.verify_fails[:10]:
                print(f"    {f['lb']} / {f['player']} @ #{f['sub']}: expected={f['expected']:.2f}, got={f['got']:.2f}, diff={f['diff']:.2f}")
        
        if self.errors:
            print(f"\n  --- Submission Errors (first 10) ---")
            for e in self.errors[:10]:
                print(f"    {e}")
        
        # Failures detail
        failures = [r for r in results if not r['ddb_ok'] or not r['api_ok'] or not r['count_ok']]
        if failures:
            print(f"\n  --- Failed Checks ---")
            for f in failures:
                issues = []
                if not f['count_ok']: issues.append(f"count({f['ddb_count']}≠{f['subs']})")
                if not f['ddb_ok']: issues.append(f"ddb({f['ddb']:.2f}≠{f['expected']:.2f})")
                if not f['api_ok']: issues.append(f"api({f['api']:.2f}≠{f['expected']:.2f})")
                print(f"    {f['lb']} / {f['player']}: {', '.join(issues)}")
        
        # ================================================================
        # Valkey commands for manual bastion verification
        # ================================================================
        print(f"\n{'='*70}")
        print(f"VALKEY COMMANDS (run from bastion for manual verification)")
        print(f"{'='*70}")
        
        for lb in TEST_LEADERBOARDS:
            lbn = lb['leaderboardName']
            gm = lb['gameMode']
            sorted_list = f"{self.game_id}:{gm}:{lbn}"
            
            print(f"\n  # {lbn} ({lb['scoreStrategy']}, {lb['leaderboardType']})")
            print(f"  ZCARD {sorted_list}")
            
            if lb['leaderboardType'] == 'ASCENDING_LB':
                print(f"  ZRANGE {sorted_list} 0 {NUM_PLAYERS - 1} WITHSCORES")
            else:
                print(f"  ZREVRANGE {sorted_list} 0 {NUM_PLAYERS - 1} WITHSCORES")
            
            for pid in players:
                key = (lbn, pid)
                exp = self.expected.get(key, Decimal('0'))
                print(f"  ZSCORE {sorted_list} {pid}  # expected: {float(exp):.2f}" + 
                      (f" (stored as {-float(exp):.2f})" if lb['leaderboardType'] == 'ASCENDING_LB' else ""))
        
        all_pass = (ddb_pass == total and api_pass == total and count_pass == total and 
                    len(self.errors) == 0)
        
        print(f"\n{'='*70}")
        if all_pass:
            print(f"🎉 ALL {total} CHECKS PASSED — Lambda functions are working correctly")
        else:
            print(f"⚠️  {total - min(ddb_pass, api_pass, count_pass)} ISSUE(S) FOUND — review above")
        print(f"{'='*70}")
        
        return all_pass
    
    # ================================================================
    # Main orchestrator
    # ================================================================
    def run(self):
        self.create_leaderboards()
        players = self.submit_all_scores()
        results = self.final_comparison(players)
        return self.print_summary(results, players)


def main():
    global NUM_PLAYERS, SCORES_PER_PLAYER_PER_LB
    
    parser = argparse.ArgumentParser(description='Validate leaderboard accuracy')
    parser.add_argument('--profile', default='default')
    parser.add_argument('--region', default='us-west-2')
    parser.add_argument('--players', type=int, default=NUM_PLAYERS)
    parser.add_argument('--scores', type=int, default=SCORES_PER_PLAYER_PER_LB)
    args = parser.parse_args()
    
    NUM_PLAYERS = args.players
    SCORES_PER_PLAYER_PER_LB = args.scores
    
    v = LeaderboardValidator(args.region, args.profile)
    ok = v.run()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
