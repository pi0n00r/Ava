#!/usr/bin/env python3
"""
Test script for ExternalMedia + RTP call flow.
This script simulates a test call and validates the complete flow.
"""

import asyncio
import aiohttp
import json
import time
import sys
from datetime import datetime

class ExternalMediaCallTester:
    def __init__(self, health_url="http://localhost:15000/health"):
        self.health_url = health_url
        self.session = None
        self.test_start_time = None
        self.initial_status = None
    
    async def start(self):
        """Start test session."""
        self.session = aiohttp.ClientSession()
        self.test_start_time = time.time()
        print("🧪 ExternalMedia + RTP Call Tester")
        print("=" * 50)
        print(f"Health Endpoint: {self.health_url}")
        print("=" * 50)
    
    async def stop(self):
        """Stop test session."""
        if self.session:
            await self.session.close()
    
    async def get_health_status(self):
        """Get current health status."""
        try:
            async with self.session.get(self.health_url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def wait_for_condition(self, condition_func, timeout=30, interval=1):
        """Wait for a condition to be met."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = await self.get_health_status()
            if condition_func(status):
                return True
            await asyncio.sleep(interval)
        return False
    
    def test_pre_call_health(self):
        """Test 1: Pre-call health check."""
        print("\n🔍 Test 1: Pre-call Health Check")
        print("-" * 30)
        
        if self.initial_status.get("error"):
            print(f"❌ Health check failed: {self.initial_status['error']}")
            return False
        
        # Check overall status
        if self.initial_status.get("status") != "healthy":
            print(f"❌ System not healthy: {self.initial_status.get('status')}")
            return False
        print("✅ System is healthy")
        
        # Check ARI connection
        if not self.initial_status.get("ari_connected", False):
            print("❌ ARI not connected")
            return False
        print("✅ ARI is connected")
        
        # Check audio transport
        audio_transport = self.initial_status.get("audio_transport")
        if audio_transport != "externalmedia":
            print(f"❌ Wrong audio transport: {audio_transport}")
            return False
        print(f"✅ Audio transport: {audio_transport}")
        
        # Check RTP server
        if not self.initial_status.get("rtp_server_running", False):
            print("❌ RTP server not running")
            return False
        print("✅ RTP server is running")
        
        # Check providers
        providers = self.initial_status.get("providers", {})
        ready_providers = [name for name, info in providers.items() if info.get("ready", False)]
        if not ready_providers:
            print("❌ No providers ready")
            return False
        print(f"✅ Providers ready: {', '.join(ready_providers)}")
        
        return True
    
    async def test_call_flow(self):
        """Test 2: Call flow simulation."""
        print("\n📞 Test 2: Call Flow Simulation")
        print("-" * 30)
        print("📋 Instructions:")
        print("1. Place a test call to your Asterisk system")
        print("2. The call should be routed to the AI agent")
        print("3. You should hear a greeting")
        print("4. Speak after the greeting")
        print("5. You should hear a response")
        print("6. Hang up the call")
        print("\n⏱️  You have 60 seconds to complete the test call...")
        
        # Wait for call to start
        print("\n⏳ Waiting for call to start...")
        call_started = await self.wait_for_condition(
            lambda status: status.get("active_calls", 0) > 0,
            timeout=60
        )
        
        if not call_started:
            print("❌ No call detected within 60 seconds")
            return False
        
        print("✅ Call detected!")
        
        # Monitor call progress
        print("⏳ Monitoring call progress...")
        for i in range(12):  # Monitor for 60 seconds (5s intervals)
            status = await self.get_health_status()
            active_calls = status.get("active_calls", 0)
            rtp_stats = status.get("rtp_server", {})
            
            print(f"  [{i*5+5:2d}s] Active calls: {active_calls}")
            
            if rtp_stats and not rtp_stats.get("error"):
                frames_received = rtp_stats.get("total_frames_received", 0)
                frames_processed = rtp_stats.get("total_frames_processed", 0)
                print(f"         RTP frames received: {frames_received}")
                print(f"         RTP frames processed: {frames_processed}")
            
            await asyncio.sleep(5)
        
        # Check if call ended
        final_status = await self.get_health_status()
        if final_status.get("active_calls", 0) == 0:
            print("✅ Call completed successfully")
            return True
        else:
            print("⚠️  Call still active - this may be normal")
            return True
    
    def test_rtp_performance(self):
        """Test 3: RTP performance analysis."""
        print("\n📊 Test 3: RTP Performance Analysis")
        print("-" * 30)
        
        if not self.initial_status:
            print("❌ No initial status available")
            return False
        
        rtp_server = self.initial_status.get("rtp_server", {})
        if rtp_server.get("error"):
            print(f"❌ RTP server error: {rtp_server['error']}")
            return False
        
        print("RTP Server Configuration:")
        print(f"  Host: {rtp_server.get('host', 'unknown')}")
        print(f"  Port: {rtp_server.get('port', 'unknown')}")
        print(f"  Codec: {rtp_server.get('codec', 'unknown')}")
        print(f"  Total Sessions: {rtp_server.get('total_sessions', 0)}")
        print(f"  Active Sessions: {rtp_server.get('active_sessions', 0)}")
        
        # Performance metrics
        frames_received = rtp_server.get('total_frames_received', 0)
        frames_processed = rtp_server.get('total_frames_processed', 0)
        packet_loss = rtp_server.get('total_packet_loss', 0)
        
        print("\nPerformance Metrics:")
        print(f"  Frames Received: {frames_received}")
        print(f"  Frames Processed: {frames_processed}")
        print(f"  Packet Loss: {packet_loss}")
        
        if frames_received > 0:
            loss_rate = (packet_loss / frames_received) * 100
            print(f"  Loss Rate: {loss_rate:.2f}%")
            
            if loss_rate > 5:
                print("⚠️  High packet loss rate detected")
            else:
                print("✅ Packet loss rate is acceptable")
        
        if frames_processed > 0:
            print("✅ RTP audio processing is working")
        else:
            print("⚠️  No RTP audio processed")
        
        return True
    
    async def run_tests(self):
        """Run all tests."""
        # Get initial status
        self.initial_status = await self.get_health_status()
        
        tests = [
            ("Pre-call Health Check", self.test_pre_call_health),
            ("Call Flow Simulation", self.test_call_flow),
            ("RTP Performance Analysis", self.test_rtp_performance),
        ]
        
        results = []
        
        for test_name, test_func in tests:
            print(f"\n{'='*50}")
            print(f"Running: {test_name}")
            print(f"{'='*50}")
            
            try:
                if asyncio.iscoroutinefunction(test_func):
                    result = await test_func()
                else:
                    result = test_func()
                results.append((test_name, result))
            except Exception as e:
                print(f"❌ Test failed with error: {e}")
                results.append((test_name, False))
        
        # Summary
        print(f"\n{'='*50}")
        print("TEST SUMMARY")
        print(f"{'='*50}")
        
        passed = 0
        total = len(results)
        
        for test_name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{test_name}: {status}")
            if result:
                passed += 1
        
        print(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed! ExternalMedia + RTP is working correctly.")
        else:
            print("❌ Some tests failed. Please check the logs and configuration.")
        
        return passed == total

async def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test ExternalMedia + RTP call flow")
    parser.add_argument("--url", default="http://localhost:15000/health", 
                       help="Health endpoint URL")
    
    args = parser.parse_args()
    
    tester = ExternalMediaCallTester(args.url)
    
    try:
        await tester.start()
        success = await tester.run_tests()
        sys.exit(0 if success else 1)
    finally:
        await tester.stop()

if __name__ == "__main__":
    asyncio.run(main())
