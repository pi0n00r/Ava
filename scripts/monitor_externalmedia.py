#!/usr/bin/env python3
"""
ExternalMedia + RTP monitoring script.
This script provides real-time monitoring of the ExternalMedia implementation.
"""

import asyncio
import aiohttp
import json
import time
import sys
from datetime import datetime

class ExternalMediaMonitor:
    def __init__(self, health_url="http://localhost:15000/health"):
        self.health_url = health_url
        self.session = None
    
    async def start(self):
        """Start monitoring session."""
        self.session = aiohttp.ClientSession()
        print("🔍 ExternalMedia + RTP Monitor Started")
        print("=" * 60)
        print(f"Health Endpoint: {self.health_url}")
        print("=" * 60)
    
    async def stop(self):
        """Stop monitoring session."""
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
    
    def format_status(self, status):
        """Format status for display."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n[{timestamp}] Health Status")
        print("-" * 40)
        
        # Overall status
        overall_status = status.get("status", "unknown")
        status_icon = "✅" if overall_status == "healthy" else "❌"
        print(f"Overall Status: {status_icon} {overall_status}")
        
        # ARI Connection
        ari_connected = status.get("ari_connected", False)
        ari_icon = "✅" if ari_connected else "❌"
        print(f"ARI Connected: {ari_icon} {ari_connected}")
        
        # Audio Transport
        audio_transport = status.get("audio_transport", "unknown")
        print(f"Audio Transport: {audio_transport}")
        
        # AudioSocket Status
        audiosocket_listening = status.get("audiosocket_listening", False)
        audiosocket_icon = "✅" if audiosocket_listening else "❌"
        print(f"AudioSocket Listening: {audiosocket_icon} {audiosocket_listening}")
        
        # RTP Server Status
        rtp_running = status.get("rtp_server_running", False)
        rtp_icon = "✅" if rtp_running else "❌"
        print(f"RTP Server Running: {rtp_icon} {rtp_running}")
        
        # Active Calls
        active_calls = status.get("active_calls", 0)
        print(f"Active Calls: {active_calls}")
        
        # Providers
        providers = status.get("providers", {})
        print("Providers:")
        for name, info in providers.items():
            ready = info.get("ready", False)
            provider_icon = "✅" if ready else "❌"
            print(f"  {name}: {provider_icon} {'ready' if ready else 'not ready'}")
        
        # RTP Server Stats
        rtp_server = status.get("rtp_server", {})
        if rtp_server and not rtp_server.get("error"):
            print("RTP Server Stats:")
            print(f"  Host: {rtp_server.get('host', 'unknown')}")
            print(f"  Port: {rtp_server.get('port', 'unknown')}")
            print(f"  Codec: {rtp_server.get('codec', 'unknown')}")
            print(f"  Total Sessions: {rtp_server.get('total_sessions', 0)}")
            print(f"  Active Sessions: {rtp_server.get('active_sessions', 0)}")
            print(f"  Frames Received: {rtp_server.get('total_frames_received', 0)}")
            print(f"  Frames Processed: {rtp_server.get('total_frames_processed', 0)}")
            print(f"  Packet Loss: {rtp_server.get('total_packet_loss', 0)}")
            print(f"  SSRC Mappings: {rtp_server.get('ssrc_mappings', 0)}")
        elif rtp_server.get("error"):
            print(f"RTP Server Error: {rtp_server['error']}")
    
    async def monitor_loop(self, interval=5):
        """Main monitoring loop."""
        try:
            while True:
                status = await self.get_health_status()
                self.format_status(status)
                
                # Check for critical issues
                if status.get("status") != "healthy":
                    print("\n⚠️  WARNING: System not healthy!")
                
                if not status.get("ari_connected", False):
                    print("\n⚠️  WARNING: ARI not connected!")
                
                if status.get("audio_transport") == "externalmedia" and not status.get("rtp_server_running", False):
                    print("\n⚠️  WARNING: RTP server not running for ExternalMedia transport!")
                
                await asyncio.sleep(interval)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Monitoring stopped by user")
        except Exception as e:
            print(f"\n❌ Monitoring error: {e}")

async def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Monitor ExternalMedia + RTP implementation")
    parser.add_argument("--url", default="http://localhost:15000/health", 
                       help="Health endpoint URL")
    parser.add_argument("--interval", type=int, default=5, 
                       help="Monitoring interval in seconds")
    parser.add_argument("--once", action="store_true", 
                       help="Run once and exit")
    
    args = parser.parse_args()
    
    monitor = ExternalMediaMonitor(args.url)
    
    try:
        await monitor.start()
        
        if args.once:
            status = await monitor.get_health_status()
            monitor.format_status(status)
        else:
            await monitor.monitor_loop(args.interval)
            
    finally:
        await monitor.stop()

if __name__ == "__main__":
    asyncio.run(main())
