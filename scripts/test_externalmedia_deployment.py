#!/usr/bin/env python3
"""
Test script for ExternalMedia + RTP deployment validation.
This script validates the configuration and basic functionality.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Ensure project root is on sys.path so we can import 'src.<module>' as a package
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import with package path to preserve package-relative imports inside modules
from src.config import load_config
from src.rtp_server import RTPServer
from src.ari_client import ARIClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_configuration():
    """Test configuration loading and validation."""
    logger.info("🔧 Testing configuration loading...")
    
    try:
        config = load_config()
        
        # Validate ExternalMedia config
        if not config.external_media:
            logger.error("❌ ExternalMedia configuration not found")
            return False
        
        logger.info("✅ ExternalMedia configuration loaded successfully")
        logger.info(f"   RTP Host: {config.external_media.rtp_host}")
        logger.info(f"   RTP Port: {config.external_media.rtp_port}")
        logger.info(f"   Codec: {config.external_media.codec}")
        logger.info(f"   Direction: {config.external_media.direction}")
        # jitter_buffer_ms was removed from ExternalMediaConfig (RTP buffering is not configurable here)
        
        # Validate audio transport
        if config.audio_transport != "externalmedia":
            logger.warning(f"⚠️  Audio transport is '{config.audio_transport}', expected 'externalmedia'")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Configuration test failed: {e}")
        return False

async def test_rtp_server():
    """Test RTP server initialization."""
    logger.info("🔧 Testing RTP server initialization...")
    
    try:
        config = load_config()
        
        # Create RTP server
        rtp_server = RTPServer(
            host=config.external_media.rtp_host,
            port=config.external_media.rtp_port,
            engine_callback=lambda call_id, data: logger.info(f"RTP audio received: {call_id}, {len(data)} bytes"),
            codec=config.external_media.codec
        )
        
        # Test start/stop
        await rtp_server.start()
        logger.info("✅ RTP server started successfully")
        
        # Test stats
        stats = rtp_server.get_stats()
        logger.info(f"   RTP Server Stats: {stats}")
        
        await rtp_server.stop()
        logger.info("✅ RTP server stopped successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ RTP server test failed: {e}")
        return False

async def test_ari_client():
    """Test ARI client ExternalMedia methods."""
    logger.info("🔧 Testing ARI client ExternalMedia methods...")
    
    try:
        config = load_config()
        
        # Create ARI client
        ari_client = ARIClient(
            username=config.asterisk.username,
            password=config.asterisk.password,
            base_url=f"http://{config.asterisk.host}:{config.asterisk.port}/ari",
            app_name=config.asterisk.app_name
        )
        
        # Test method exists
        if not hasattr(ari_client, 'create_external_media'):
            logger.error("❌ create_external_media method not found")
            return False
        
        if not hasattr(ari_client, 'create_external_media_channel'):
            logger.error("❌ create_external_media_channel method not found")
            return False
        
        logger.info("✅ ARI client ExternalMedia methods found")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ ARI client test failed: {e}")
        return False

async def main():
    """Run all tests."""
    logger.info("🚀 Starting ExternalMedia + RTP deployment validation...")
    
    tests = [
        ("Configuration", test_configuration),
        ("RTP Server", test_rtp_server),
        ("ARI Client", test_ari_client),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*50}")
        logger.info(f"Running {test_name} Test")
        logger.info(f"{'='*50}")
        
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ {test_name} test crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    logger.info(f"\n{'='*50}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*50}")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 All tests passed! Ready for deployment.")
        return True
    else:
        logger.error("❌ Some tests failed. Please fix issues before deployment.")
        return False

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
