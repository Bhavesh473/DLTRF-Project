"""
Request Matcher - Pollyjs-inspired request matching
Matches replay requests to stored HAR responses
"""

from typing import Dict, Any, Optional, List
from urllib.parse import urlparse, parse_qs
import logging
import re

logger = logging.getLogger(__name__)


class RequestMatcher:
    """
    Matches HTTP requests to stored HAR responses
    
    Matching Strategy (in order):
    1. Exact match (method + full URL)
    2. Fuzzy match (method + URL without query params)
    3. Pattern match (regex on URL)
    4. No match (return None → triggers passthrough)
    """
    
    def __init__(self):
        """Initialize request matcher with empty storage"""
        self.har_entries = []  # List of HAR entries
        self.exact_match_index = {}  # Dict for fast exact lookups
        self.fuzzy_match_index = {}  # Dict for path-only lookups
        logger.info("RequestMatcher initialized")
    
    def load_har_entries(self, har_entries: List[Dict[str, Any]]):
        """
        Load HAR entries into matcher
        
        Args:
            har_entries: List of HAR entry objects
        """
        self.har_entries = har_entries
        self._build_indexes()
        logger.info(f"Loaded {len(har_entries)} HAR entries into matcher")
    
    def _build_indexes(self):
        """Build fast lookup indexes for matching"""
        self.exact_match_index = {}
        self.fuzzy_match_index = {}
        
        for entry in self.har_entries:
            method = entry['request']['method']
            url = entry['request']['url']
            
            # Exact match key: "GET:http://localhost:3000/products?page=1"
            exact_key = f"{method}:{url}"
            self.exact_match_index[exact_key] = entry
            
            # Fuzzy match key: "GET:http://localhost:3000/products"
            parsed = urlparse(url)
            fuzzy_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            fuzzy_key = f"{method}:{fuzzy_url}"
            
            if fuzzy_key not in self.fuzzy_match_index:
                self.fuzzy_match_index[fuzzy_key] = []
            self.fuzzy_match_index[fuzzy_key].append(entry)
        
        logger.debug(f"Built indexes: {len(self.exact_match_index)} exact, "
                    f"{len(self.fuzzy_match_index)} fuzzy")
    
    def find_match(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Find matching HAR entry for given request
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL
            headers: Optional request headers
        
        Returns:
            Matching HAR entry or None
        
        Example:
            >>> matcher = RequestMatcher()
            >>> matcher.load_har_entries([...])
            >>> match = matcher.find_match("GET", "http://localhost:3000/products")
            >>> if match:
            ...     print(match['response']['status'])
        """
        # Strategy 1: Exact match
        exact_match = self._exact_match(method, url)
        if exact_match:
            logger.debug(f"Exact match found for {method} {url}")
            return exact_match
        
        # Strategy 2: Fuzzy match (ignore query params)
        fuzzy_match = self._fuzzy_match(method, url)
        if fuzzy_match:
            logger.debug(f"Fuzzy match found for {method} {url}")
            return fuzzy_match
        
        # Strategy 3: Pattern match
        pattern_match = self._pattern_match(method, url)
        if pattern_match:
            logger.debug(f"Pattern match found for {method} {url}")
            return pattern_match
        
        # No match
        logger.warning(f"No match found for {method} {url}")
        return None
    
    def _exact_match(self, method: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Find exact match: method + full URL
        
        Args:
            method: HTTP method
            url: Full URL with query params
        
        Returns:
            HAR entry or None
        """
        key = f"{method}:{url}"
        return self.exact_match_index.get(key)
    
    def _fuzzy_match(self, method: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Find fuzzy match: method + URL path (no query params)
        
        Args:
            method: HTTP method
            url: Full URL
        
        Returns:
            HAR entry or None (first match if multiple)
        """
        parsed = urlparse(url)
        fuzzy_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        key = f"{method}:{fuzzy_url}"
        
        matches = self.fuzzy_match_index.get(key, [])
        if matches:
            # Return first match
            # TODO: Could rank by query param similarity
            return matches[0]
        
        return None
    
    def _pattern_match(self, method: str, url: str) -> Optional[Dict[str, Any]]:
        """
        Find pattern match using regex
        
        Args:
            method: HTTP method
            url: Full URL
        
        Returns:
            HAR entry or None
        """
        # Define common patterns
        patterns = [
            (r'/rest/products/\d+', '/rest/products/:id'),  # Product by ID
            (r'/api/v\d+/', '/api/v*/'),                    # API version
            (r'/users/[a-f0-9-]+', '/users/:uuid'),         # UUID paths
        ]
        
        for pattern, description in patterns:
            if re.search(pattern, url):
                # Find any HAR entry matching this pattern
                for entry in self.har_entries:
                    if (entry['request']['method'] == method and 
                        re.search(pattern, entry['request']['url'])):
                        logger.debug(f"Pattern match: {description}")
                        return entry
        
        return None
    
    def get_response(self, har_entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract response from HAR entry
        
        Args:
            har_entry: HAR entry object
        
        Returns:
            Response dict with status, headers, body
        """
        response = har_entry['response']
        
        return {
            'status': response['status'],
            'status_text': response['statusText'],
            'headers': {h['name']: h['value'] for h in response['headers']},
            'body': response['content'].get('text', ''),
            'content_type': response['content'].get('mimeType', 'text/plain')
        }
    
    def add_har_entry(self, har_entry: Dict[str, Any]):
        """
        Add single HAR entry to matcher (for recording mode)
        
        Args:
            har_entry: HAR entry to add
        """
        self.har_entries.append(har_entry)
        
        # Update indexes
        method = har_entry['request']['method']
        url = har_entry['request']['url']
        
        exact_key = f"{method}:{url}"
        self.exact_match_index[exact_key] = har_entry
        
        parsed = urlparse(url)
        fuzzy_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        fuzzy_key = f"{method}:{fuzzy_url}"
        
        if fuzzy_key not in self.fuzzy_match_index:
            self.fuzzy_match_index[fuzzy_key] = []
        self.fuzzy_match_index[fuzzy_key].append(har_entry)
        
        logger.debug(f"Added HAR entry: {method} {url}")
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get matcher statistics
        
        Returns:
            Stats dict with entry counts
        """
        return {
            'total_entries': len(self.har_entries),
            'exact_matches_available': len(self.exact_match_index),
            'fuzzy_paths_available': len(self.fuzzy_match_index)
        }


# Testing
if __name__ == "__main__":
    # Create sample HAR entries
    sample_entries = [
        {
            "request": {
                "method": "GET",
                "url": "http://localhost:3000/products?page=1"
            },
            "response": {
                "status": 200,
                "statusText": "OK",
                "headers": [],
                "content": {"text": '{"products": []}', "mimeType": "application/json"}
            }
        },
        {
            "request": {
                "method": "GET",
                "url": "http://localhost:3000/products?page=2"
            },
            "response": {
                "status": 200,
                "statusText": "OK",
                "headers": [],
                "content": {"text": '{"products": []}', "mimeType": "application/json"}
            }
        },
        {
            "request": {
                "method": "POST",
                "url": "http://localhost:3000/api/login"
            },
            "response": {
                "status": 401,
                "statusText": "Unauthorized",
                "headers": [],
                "content": {"text": '{"error": "Invalid credentials"}', "mimeType": "application/json"}
            }
        }
    ]
    
    # Initialize matcher
    matcher = RequestMatcher()
    matcher.load_har_entries(sample_entries)
    
    print("="*60)
    print("Request Matcher Test")
    print("="*60)
    
    # Test 1: Exact match
    print("\n1. Exact Match Test:")
    match = matcher.find_match("GET", "http://localhost:3000/products?page=1")
    if match:
        response = matcher.get_response(match)
        print(f"   ✓ Found: {response['status']} - {response['body'][:50]}")
    else:
        print("   ✗ No match")
    
    # Test 2: Fuzzy match (different query param)
    print("\n2. Fuzzy Match Test:")
    match = matcher.find_match("GET", "http://localhost:3000/products?page=999")
    if match:
        response = matcher.get_response(match)
        print(f"   ✓ Found (fuzzy): {response['status']}")
    else:
        print("   ✗ No match")
    
    # Test 3: No match
    print("\n3. No Match Test:")
    match = matcher.find_match("GET", "http://localhost:3000/nonexistent")
    if match:
        print(f"   Unexpected match")
    else:
        print("   ✓ Correctly returned None (would trigger passthrough)")
    
    # Test 4: Stats
    print("\n4. Matcher Stats:")
    stats = matcher.get_stats()
    for key, value in stats.items():
        print(f"   - {key}: {value}")
    
    print("\n" + "="*60)
    print("✓ Request Matcher implementation complete!")
    print("="*60)