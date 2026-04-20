#!/usr/bin/env python3
"""
Startup Configuration Validator
Validates environment and connectivity before starting framework
"""

import os
import sys
import socket
import time

def validate_env_vars():
    """Check required environment variables"""
    errors = []
    required = ['APP_HOST', 'APP_PORT']
    
    for var in required:
        value = os.getenv(var)
        if not value:
            errors.append(f"❌ Missing required env var: {var}")
        elif var == 'APP_PORT':
            try:
                port = int(value)
                if port < 1 or port > 65535:
                    errors.append(f"❌ Invalid port: {port}. Must be 1-65535")
            except ValueError:
                errors.append(f"❌ Invalid port: {value}. Must be a number")
    
    if errors:
        for err in errors:
            print(err)
        print("\n💡 Set APP_HOST and APP_PORT in .env file\n")
        return False
    
    print(f"✅ Environment variables OK (APP_HOST={os.getenv('APP_HOST')}, APP_PORT={os.getenv('APP_PORT')})")
    return True

def validate_redis():
    """Check if Redis is reachable"""
    try:
        import redis
        r = redis.Redis(host='redis', port=6379, socket_timeout=5)
        r.ping()
        print("✅ Redis connection successful")
        return True
    except ImportError:
        print("❌ Redis Python package not installed")
        return False
    except Exception as e:
        print(f"⚠️  Redis not reachable yet: {e}")
        print("   (Will retry when services start)")
        return True  # Don't fail - Redis might be starting

def validate_app_connectivity():
    """Check if target app is reachable"""
    host = os.getenv('APP_HOST', 'localhost')
    port = int(os.getenv('APP_PORT', '3000'))
    
    # Try socket connection
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result != 0:
            print(f"⚠️  Cannot connect to {host}:{port} yet")
            print("   (Target app might be starting)")
            return True  # Don't fail - app might be starting
        
        print(f"✅ Target app reachable at {host}:{port}")
        return True
    except socket.gaierror:
        print(f"❌ Cannot resolve hostname: {host}")
        print(f"   Check APP_HOST in .env file")
        return False

def main():
    print("=" * 60)
    print("🔍 DLTRF Startup Validation")
    print("=" * 60)
    
    checks = [
        ("Environment Variables", validate_env_vars),
        ("Redis Connectivity", validate_redis),
        ("Target App Connectivity", validate_app_connectivity),
    ]
    
    all_passed = True
    for name, check_fn in checks:
        print(f"\n[{name}]")
        if not check_fn():
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All validations passed! Starting framework...")
        print("=" * 60)
        return 0
    else:
        print("❌ Some validations failed. Fix errors and try again.")
        print("=" * 60)
        return 1

if __name__ == '__main__':
    sys.exit(main())