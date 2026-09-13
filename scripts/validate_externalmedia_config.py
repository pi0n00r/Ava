#!/usr/bin/env python3
"""
Simple validation script for ExternalMedia configuration.
This script validates the YAML configuration without importing the full modules.
"""

import yaml
import sys
from pathlib import Path

def validate_yaml_config():
    """Validate the YAML configuration file."""
    print("🔧 Validating ExternalMedia configuration...")
    
    config_path = Path(__file__).parent.parent / "config" / "ai-agent.yaml"
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Check audio_transport
        audio_transport = config.get('audio_transport')
        if audio_transport != 'externalmedia':
            print(f"❌ audio_transport is '{audio_transport}', expected 'externalmedia'")
            return False
        print(f"✅ audio_transport: {audio_transport}")
        
        # Check external_media section
        external_media = config.get('external_media')
        if not external_media:
            print("❌ external_media section not found")
            return False
        
        required_fields = ['rtp_host', 'rtp_port', 'codec', 'direction', 'jitter_buffer_ms']
        for field in required_fields:
            if field not in external_media:
                print(f"❌ Missing field: external_media.{field}")
                return False
            print(f"✅ external_media.{field}: {external_media[field]}")

        if 'port_range' in external_media:
            print(f"✅ external_media.port_range: {external_media['port_range']}")
        else:
            print("ℹ️ external_media.port_range not set; single-port mode will be used")
        
        print("✅ ExternalMedia configuration is valid")
        return True
        
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")
        return False

def validate_python_syntax():
    """Validate Python syntax of modified files."""
    print("\n🔧 Validating Python syntax...")
    
    files_to_check = [
        "src/config.py",
        "src/ari_client.py", 
        "src/rtp_server.py",
        "src/engine.py"
    ]
    
    for file_path in files_to_check:
        full_path = Path(__file__).parent.parent / file_path
        try:
            with open(full_path, 'r') as f:
                compile(f.read(), str(full_path), 'exec')
            print(f"✅ {file_path}: Syntax OK")
        except SyntaxError as e:
            print(f"❌ {file_path}: Syntax Error - {e}")
            return False
        except Exception as e:
            print(f"❌ {file_path}: Error - {e}")
            return False
    
    print("✅ All Python files have valid syntax")
    return True

def main():
    """Run all validations."""
    print("🚀 Starting ExternalMedia + RTP validation...")
    print("="*50)
    
    results = []
    
    # Test 1: YAML Configuration
    results.append(("YAML Configuration", validate_yaml_config()))
    
    # Test 2: Python Syntax
    results.append(("Python Syntax", validate_python_syntax()))
    
    # Summary
    print("\n" + "="*50)
    print("VALIDATION SUMMARY")
    print("="*50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nOverall: {passed}/{total} validations passed")
    
    if passed == total:
        print("🎉 All validations passed! Ready for deployment.")
        return True
    else:
        print("❌ Some validations failed. Please fix issues before deployment.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
