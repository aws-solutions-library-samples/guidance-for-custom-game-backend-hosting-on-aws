#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
API Latency Diagnostic Tool

Tests API endpoints with and without connection pooling to identify latency sources.
Captures detailed timing metrics, request/response data, and network statistics.

Usage:
    python3 diagnose_api_latency.py [--iterations N] [--api-base-url URL] [--api-key KEY]
"""

import json
import time
import argparse
import sys
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
import statistics

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("ERROR: requests library not found. Install with: pip3 install requests")
    sys.exit(1)


class LatencyDiagnostic:
    """Comprehensive API latency diagnostic tool"""
    
    def __init__(self, api_base_url: str, api_key: str):
        self.api_base_url = api_base_url.rstrip('/')
        self.api_key = api_key
        self.results = []
        
    def create_session_with_pooling(self) -> requests.Session:
        """Create a requests session with connection pooling enabled"""
        session = requests.Session()
        
        # Configure connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,  # Number of connection pools to cache
            pool_maxsize=20,      # Maximum number of connections to save in the pool
            max_retries=Retry(
                total=0,  # No retries for diagnostic purposes
                backoff_factor=0
            )
        )
        
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        return session
    
    def test_store_player_stats(self, use_pooling: bool = False) -> Dict[str, Any]:
        """Test storePlayerStatsAndScores endpoint"""
        endpoint = f"{self.api_base_url}/leaderboards/stats"
        
        request_body = {
            "gameReportBody": {
                "playerID": "diagnostic_player_001",
                "gameID": "wars-of-valoria",
                "gameMode": "sprint-race",
                "playerScore": 75000,
                "leaderboardName": "racing-circuit-average-session-duration-sprint-race-best",
                "fullRawGameReport": {
                    "playerLevel": 10,
                    "sessionID": f"diagnostic_session_{int(time.time())}",
                    "gameMode": "sprint-race",
                    "sessionDuration": 300,
                    "playerID": "diagnostic_player_001",
                    "timestamp": int(time.time()),
                    "average_session_duration": 75000
                }
            }
        }
        
        return self._execute_test(
            endpoint=endpoint,
            method="POST",
            request_body=request_body,
            api_name="storePlayerStatsAndScores",
            use_pooling=use_pooling
        )
    
    def test_get_leaderboard_scores(self, use_pooling: bool = False) -> Dict[str, Any]:
        """Test getLeaderboardScores endpoint"""
        endpoint = f"{self.api_base_url}/leaderboards/scores"
        
        request_body = {
            "leaderboardScoresRequest": {
                "gameID": "wars-of-valoria",
                "gameMode": "sprint-race",
                "leaderboardName": "racing-circuit-average-session-duration-sprint-race-best",
                "queryType": "top",
                "count": 10
            }
        }
        
        return self._execute_test(
            endpoint=endpoint,
            method="POST",
            request_body=request_body,
            api_name="getLeaderboardScores",
            use_pooling=use_pooling
        )
    
    def test_get_player_stats(self, use_pooling: bool = False) -> Dict[str, Any]:
        """Test getPlayerStatsAndScores endpoint"""
        endpoint = f"{self.api_base_url}/leaderboards/player/stats"
        
        request_body = {
            "playerStatsAndScoresRequest": {
                "playerID": "diagnostic_player_001",
                "gameID": "wars-of-valoria",
                "gameMode": "sprint-race",
                "leaderboardName": "racing-circuit-average-session-duration-sprint-race-best",
                "limit": 10
            }
        }
        
        return self._execute_test(
            endpoint=endpoint,
            method="POST",
            request_body=request_body,
            api_name="getPlayerStatsAndScores",
            use_pooling=use_pooling
        )
    
    def test_get_player_standing(self, use_pooling: bool = False) -> Dict[str, Any]:
        """Test getPlayerLBStanding endpoint"""
        endpoint = f"{self.api_base_url}/leaderboards/player/standing"
        
        request_body = {
            "playerLBStandingRequest": {
                "playerID": "diagnostic_player_001",
                "gameID": "wars-of-valoria",
                "gameMode": "sprint-race",
                "leaderboardName": "racing-circuit-average-session-duration-sprint-race-best",
                "includePercentile": True,
                "includeNeighbours": True,
                "neighbourCount": 5
            }
        }
        
        return self._execute_test(
            endpoint=endpoint,
            method="POST",
            request_body=request_body,
            api_name="getPlayerLBStanding",
            use_pooling=use_pooling
        )
    
    def test_leaderboards_config_get(self, use_pooling: bool = False) -> Dict[str, Any]:
        """Test leaderboardsConfig GET endpoint"""
        endpoint = f"{self.api_base_url}/leaderboards/config/get"
        
        request_body = {
            "gameLeaderboardConfigRequest": {
                "gameID": "wars-of-valoria",
                "gameMode": "sprint-race",
                "leaderboardName": "racing-circuit-average-session-duration-sprint-race-best"
            }
        }
        
        return self._execute_test(
            endpoint=endpoint,
            method="POST",
            request_body=request_body,
            api_name="leaderboardsConfig-GET",
            use_pooling=use_pooling
        )
    
    def _execute_test(
        self,
        endpoint: str,
        method: str,
        request_body: Dict[str, Any],
        api_name: str,
        use_pooling: bool
    ) -> Dict[str, Any]:
        """Execute a single API test with detailed timing measurements"""
        
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        # Timing measurements
        timings = {}
        
        # DNS resolution time (approximate - first request will include DNS)
        dns_start = time.perf_counter()
        
        try:
            if use_pooling:
                session = self.create_session_with_pooling()
                requester = session
            else:
                requester = requests
            
            # Connection establishment + request + response
            request_start = time.perf_counter()
            request_start_wall = datetime.now(timezone.utc)
            
            response = requester.post(
                endpoint,
                json=request_body,
                headers=headers,
                timeout=30
            )
            
            request_end = time.perf_counter()
            request_end_wall = datetime.now(timezone.utc)
            
            # Parse response
            try:
                response_data = response.json()
            except:
                response_data = {"error": "Failed to parse JSON", "text": response.text[:500]}
            
            # Calculate timings
            total_latency_ms = (request_end - request_start) * 1000
            
            # Extract Lambda processing time from response
            lambda_processing_ms = None
            if isinstance(response_data, dict):
                # Try different response structures
                lambda_processing_ms = (
                    response_data.get('processingTimeMs') or
                    response_data.get('performance', {}).get('processingTimeMs') or
                    (response_data.get('gameReportResponse', {}) or {}).get('performance', {}).get('processingTimeMs') or
                    (response_data.get('leaderboardScoresResponse', {}) or {}).get('processingTimeMs') or
                    (response_data.get('playerStatsAndScoresResponse', {}) or {}).get('processingTimeMs') or
                    (response_data.get('playerLBStandingResponse', {}) or {}).get('processingTimeMs') or
                    (response_data.get('gameLeaderboardConfigResponse', {}) or {}).get('processingTimeMs')
                )
            
            # Calculate network overhead
            network_overhead_ms = None
            if lambda_processing_ms is not None:
                network_overhead_ms = total_latency_ms - lambda_processing_ms
            
            result = {
                "api_name": api_name,
                "endpoint": endpoint,
                "method": method,
                "use_pooling": use_pooling,
                "status_code": response.status_code,
                "success": response.status_code == 200,
                "timestamps": {
                    "request_start": request_start_wall.isoformat(),
                    "request_end": request_end_wall.isoformat(),
                },
                "timings": {
                    "total_latency_ms": round(total_latency_ms, 2),
                    "lambda_processing_ms": lambda_processing_ms,
                    "network_overhead_ms": round(network_overhead_ms, 2) if network_overhead_ms else None,
                    "network_overhead_percent": round((network_overhead_ms / total_latency_ms * 100), 1) if network_overhead_ms else None
                },
                "request": {
                    "headers": headers,
                    "body": request_body
                },
                "response": {
                    "headers": dict(response.headers),
                    "body": response_data
                }
            }
            
            if use_pooling:
                session.close()
            
            return result
            
        except Exception as e:
            return {
                "api_name": api_name,
                "endpoint": endpoint,
                "method": method,
                "use_pooling": use_pooling,
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    def run_diagnostic(self, iterations: int = 5) -> Dict[str, Any]:
        """Run comprehensive diagnostic tests"""
        
        print("=" * 80)
        print("API LATENCY DIAGNOSTIC TOOL")
        print("=" * 80)
        print(f"API Base URL: {self.api_base_url}")
        print(f"Iterations per test: {iterations}")
        print(f"Testing with and without connection pooling")
        print("=" * 80)
        print()
        
        test_functions = [
            ("storePlayerStatsAndScores", self.test_store_player_stats),
            ("getLeaderboardScores", self.test_get_leaderboard_scores),
            ("getPlayerStatsAndScores", self.test_get_player_stats),
            ("getPlayerLBStanding", self.test_get_player_standing),
            ("leaderboardsConfig-GET", self.test_leaderboards_config_get),
        ]
        
        all_results = []
        
        for api_name, test_func in test_functions:
            print(f"\n{'=' * 80}")
            print(f"Testing: {api_name}")
            print(f"{'=' * 80}\n")
            
            # Test WITHOUT connection pooling
            print(f"  WITHOUT Connection Pooling:")
            no_pool_results = []
            for i in range(iterations):
                print(f"    Iteration {i+1}/{iterations}...", end=" ", flush=True)
                result = test_func(use_pooling=False)
                no_pool_results.append(result)
                all_results.append(result)
                
                if result.get('success'):
                    latency = result['timings']['total_latency_ms']
                    lambda_time = result['timings'].get('lambda_processing_ms', 'N/A')
                    overhead = result['timings'].get('network_overhead_ms', 'N/A')
                    print(f"✓ Total: {latency}ms, Lambda: {lambda_time}ms, Overhead: {overhead}ms")
                else:
                    print(f"✗ Error: {result.get('error', 'Unknown')}")
                
                time.sleep(0.5)  # Small delay between requests
            
            # Test WITH connection pooling
            print(f"\n  WITH Connection Pooling:")
            pool_results = []
            for i in range(iterations):
                print(f"    Iteration {i+1}/{iterations}...", end=" ", flush=True)
                result = test_func(use_pooling=True)
                pool_results.append(result)
                all_results.append(result)
                
                if result.get('success'):
                    latency = result['timings']['total_latency_ms']
                    lambda_time = result['timings'].get('lambda_processing_ms', 'N/A')
                    overhead = result['timings'].get('network_overhead_ms', 'N/A')
                    print(f"✓ Total: {latency}ms, Lambda: {lambda_time}ms, Overhead: {overhead}ms")
                else:
                    print(f"✗ Error: {result.get('error', 'Unknown')}")
                
                time.sleep(0.5)  # Small delay between requests
            
            # Calculate statistics
            self._print_statistics(api_name, no_pool_results, pool_results)
        
        # Save detailed results to file
        output_file = f"api_latency_diagnostic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump({
                "metadata": {
                    "api_base_url": self.api_base_url,
                    "iterations": iterations,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                },
                "results": all_results
            }, f, indent=2)
        
        print(f"\n{'=' * 80}")
        print(f"Detailed results saved to: {output_file}")
        print(f"{'=' * 80}\n")
        
        return all_results
    
    def _print_statistics(self, api_name: str, no_pool_results: List[Dict], pool_results: List[Dict]):
        """Print statistical analysis of results"""
        
        print(f"\n  Statistics for {api_name}:")
        print(f"  {'-' * 76}")
        
        # Extract successful results
        no_pool_success = [r for r in no_pool_results if r.get('success')]
        pool_success = [r for r in pool_results if r.get('success')]
        
        if not no_pool_success or not pool_success:
            print(f"    ⚠ Insufficient successful results for statistics")
            return
        
        # Calculate statistics for no pooling
        no_pool_latencies = [r['timings']['total_latency_ms'] for r in no_pool_success]
        no_pool_lambda = [r['timings']['lambda_processing_ms'] for r in no_pool_success if r['timings'].get('lambda_processing_ms')]
        no_pool_overhead = [r['timings']['network_overhead_ms'] for r in no_pool_success if r['timings'].get('network_overhead_ms')]
        
        # Calculate statistics for pooling
        pool_latencies = [r['timings']['total_latency_ms'] for r in pool_success]
        pool_lambda = [r['timings']['lambda_processing_ms'] for r in pool_success if r['timings'].get('lambda_processing_ms')]
        pool_overhead = [r['timings']['network_overhead_ms'] for r in pool_success if r['timings'].get('network_overhead_ms')]
        
        print(f"    WITHOUT Pooling:")
        print(f"      Total Latency:    min={min(no_pool_latencies):.1f}ms, max={max(no_pool_latencies):.1f}ms, avg={statistics.mean(no_pool_latencies):.1f}ms, median={statistics.median(no_pool_latencies):.1f}ms")
        if no_pool_lambda:
            print(f"      Lambda Time:      min={min(no_pool_lambda):.1f}ms, max={max(no_pool_lambda):.1f}ms, avg={statistics.mean(no_pool_lambda):.1f}ms")
        if no_pool_overhead:
            print(f"      Network Overhead: min={min(no_pool_overhead):.1f}ms, max={max(no_pool_overhead):.1f}ms, avg={statistics.mean(no_pool_overhead):.1f}ms")
        
        print(f"\n    WITH Pooling:")
        print(f"      Total Latency:    min={min(pool_latencies):.1f}ms, max={max(pool_latencies):.1f}ms, avg={statistics.mean(pool_latencies):.1f}ms, median={statistics.median(pool_latencies):.1f}ms")
        if pool_lambda:
            print(f"      Lambda Time:      min={min(pool_lambda):.1f}ms, max={max(pool_lambda):.1f}ms, avg={statistics.mean(pool_lambda):.1f}ms")
        if pool_overhead:
            print(f"      Network Overhead: min={min(pool_overhead):.1f}ms, max={max(pool_overhead):.1f}ms, avg={statistics.mean(pool_overhead):.1f}ms")
        
        # Calculate improvement
        avg_improvement = statistics.mean(no_pool_latencies) - statistics.mean(pool_latencies)
        improvement_percent = (avg_improvement / statistics.mean(no_pool_latencies)) * 100
        
        print(f"\n    Improvement with Pooling:")
        print(f"      Average latency reduction: {avg_improvement:.1f}ms ({improvement_percent:.1f}%)")
        
        if avg_improvement > 0:
            print(f"      ✓ Connection pooling IMPROVES performance")
        elif avg_improvement < -10:
            print(f"      ✗ Connection pooling DEGRADES performance")
        else:
            print(f"      ≈ Connection pooling has MINIMAL impact")


def main():
    # Hard-coded configuration for gtisengard AWS profile, us-west-2 region
    API_BASE_URL = 'https://uukvtdz1ee.execute-api.us-west-2.amazonaws.com/dev'
    API_KEY = 'cosmicwo_warsofva_dev_lFDckbbQLMOWM8lABZOy8A'
    AWS_PROFILE = 'gtisengard'
    AWS_REGION = 'us-west-2'
    
    parser = argparse.ArgumentParser(
        description='Diagnose API latency with detailed timing measurements'
    )
    parser.add_argument(
        '--iterations',
        type=int,
        default=5,
        help='Number of iterations per test (default: 5)'
    )
    
    args = parser.parse_args()
    
    print(f"AWS Profile: {AWS_PROFILE}")
    print(f"AWS Region: {AWS_REGION}")
    print()
    
    diagnostic = LatencyDiagnostic(
        api_base_url=API_BASE_URL,
        api_key=API_KEY
    )
    
    diagnostic.run_diagnostic(iterations=args.iterations)


if __name__ == '__main__':
    main()
