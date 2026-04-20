"""
OpenTelemetry Redis Exporter
Exports traces to existing Redis Streams (lightweight!)
"""

from typing import Sequence
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
import redis
import json
from datetime import datetime

class RedisSpanExporter(SpanExporter):
    """
    Export OTel spans to Redis Streams
    Reuses existing Redis infrastructure (no extra services needed)
    """
    
    def __init__(self, redis_url: str, stream_key: str = "traces:stream"):
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.stream_key = stream_key
    
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans to Redis"""
        try:
            for span in spans:
                # Convert span to JSON-serializable format
                trace_data = {
                    "trace_id": format(span.context.trace_id, '032x'),
                    "span_id": format(span.context.span_id, '016x'),
                    "parent_span_id": format(span.parent.span_id, '016x') if span.parent else None,
                    "name": span.name,
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "duration_ns": (span.end_time - span.start_time) if span.end_time else 0,
                    "attributes": dict(span.attributes or {}),
                    "status": {
                        "status_code": span.status.status_code.name,
                        "description": span.status.description or ""
                    },
                    "events": [
                        {
                            "name": event.name,
                            "timestamp": event.timestamp,
                            "attributes": dict(event.attributes or {})
                        }
                        for event in (span.events or [])
                    ]
                }
                
                # Store in Redis Stream
                self.redis_client.xadd(
                    self.stream_key,
                    {"trace_data": json.dumps(trace_data)}
                )
            
            return SpanExportResult.SUCCESS
        
        except Exception as e:
            print(f"❌ Failed to export spans to Redis: {e}")
            return SpanExportResult.FAILURE
    
    def shutdown(self):
        """Cleanup"""
        self.redis_client.close()