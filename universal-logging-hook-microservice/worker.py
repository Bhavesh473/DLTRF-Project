#!/usr/bin/env python3
"""
Background Endpoint Discovery Worker
Incrementally discovers endpoints as traffic flows
"""

import redis
import time
import os

def endpoint_discovery_worker():
    """
    Background worker that incrementally discovers endpoints
    Dashboard reads pre-computed results for instant loading
    """
    redis_url = os.getenv('REDIS_URL', 'redis://redis:6379')
    stream_key = os.getenv('STREAM_KEY', 'logs:stream')
    
    redis_client = redis.from_url(redis_url, decode_responses=True)
    last_id = '0'
    
    print("🔍 Endpoint Discovery Worker Started")
    print(f"   Monitoring stream: {stream_key}")
    
    while True:
        try:
            # Read only new events (efficient)
            events = redis_client.xread({stream_key: last_id}, count=100, block=5000)
            
            if events:
                for stream, event_list in events:
                    for event_id, event_data in event_list:
                        method = event_data.get('method', 'GET')
                        path = event_data.get('path', '/')
                        
                        # Update discovered endpoints set
                        endpoint_key = f"{method}|{path}"
                        redis_client.sadd('discovered_endpoints', endpoint_key)
                        redis_client.hincrby('endpoint_counts', endpoint_key, 1)
                        
                        last_id = event_id
                
                print(f"✅ Processed {len(event_list)} new events (Total endpoints: {redis_client.scard('discovered_endpoints')})")
        
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    endpoint_discovery_worker()