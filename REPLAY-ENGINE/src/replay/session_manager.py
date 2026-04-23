import json
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from ..common.logging_config import ReplayLogger

@dataclass
class ReplaySession:
    replay_id: str
    status: str = "idle"
    start_time: datetime = None
    progress: float = 0.0
    events_processed: int = 0
    divergences_detected: int = 0  # CHANGED: "bugs" → "divergences"
    total_events: int = 0
    last_updated: datetime = None
    raw_event_json: str = None
    current_event_id: str = None
    message: str = None
    current_event_details: Dict[str, Any] = field(default_factory=lambda: {
        'method': 'GET', 'path': 'Unknown', 'activity': 'N/A', 'status': 'N/A'
    })

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.start_time:
            data['start_time'] = self.start_time.isoformat()
        if self.last_updated:
            data['last_updated'] = self.last_updated.isoformat()
        return data

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, ReplaySession] = {}
        self.logger = ReplayLogger(__name__)

    def create_session(self, replay_id: str, replay_config: Dict[str, Any]) -> ReplaySession:
        """Create a new replay session"""
        mode = replay_config.get('mode', 'dry-run')
        session = ReplaySession(
            replay_id=replay_id,
            status="running",
            start_time=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
            progress=0.0,
            events_processed=0,
            divergences_detected=0,
            total_events=0
        )
        self.sessions[replay_id] = session
        self.logger.info(f"Created session {replay_id} in {mode} mode")
        return session

    async def update_progress(self, replay_id: str, progress: float, events_processed: int, 
                            divergences_detected: int = 0, **kwargs):
        """Update session progress (GENERIC - No app-specific logic)"""
        session = await self.get_session(replay_id)
        if session:
            session.progress = progress
            session.events_processed = events_processed
            session.divergences_detected = divergences_detected
            session.last_updated = datetime.now(timezone.utc)
            
            # Store raw event JSON
            if 'raw_event_json' in kwargs:
                session.raw_event_json = kwargs['raw_event_json']
                
                # FIXED: Generic activity inference (no Juice Shop hardcoding)
                try:
                    event_json = json.loads(kwargs['raw_event_json']) if isinstance(kwargs['raw_event_json'], str) else kwargs['raw_event_json']
                    
                    # Generic activity inference based on HTTP method + path patterns
                    method = event_json.get('method', 'GET')
                    path = event_json.get('path', '/').lower()
                    
                    # Generic activity categories (works for ANY web app)
                    if 'login' in path or 'auth' in path or 'signin' in path:
                        activity = 'Authentication'
                    elif 'logout' in path or 'signout' in path:
                        activity = 'Logout'
                    elif 'user' in path or 'profile' in path or 'account' in path:
                        activity = 'User Management'
                    elif 'product' in path or 'item' in path or 'catalog' in path:
                        activity = 'Browse Products'
                    elif 'cart' in path or 'basket' in path or 'order' in path:
                        activity = 'Shopping Cart'
                    elif 'checkout' in path or 'payment' in path or 'pay' in path:
                        activity = 'Checkout/Payment'
                    elif 'search' in path or 'query' in path:
                        activity = 'Search'
                    elif 'api/' in path or '/api/' in path:
                        activity = 'API Request'
                    elif 'admin' in path or 'dashboard' in path:
                        activity = 'Admin Panel'
                    elif method == 'POST':
                        activity = 'Data Submission'
                    elif method == 'PUT' or method == 'PATCH':
                        activity = 'Data Update'
                    elif method == 'DELETE':
                        activity = 'Data Deletion'
                    elif method == 'GET':
                        activity = 'Data Retrieval'
                    else:
                        activity = 'API Request'
                    
                    session.current_event_details = {
                        'method': method,
                        'path': event_json.get('path', 'Unknown'),
                        'activity': activity,
                        'status': event_json.get('status', 'N/A')
                    }
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    self.logger.warning(f"Failed to parse event JSON in update_progress: {e}")
                    session.current_event_details = {
                        'method': 'GET', 'path': 'Unknown', 'activity': 'Parse Error', 'status': 'N/A'
                    }
            
            if 'status' in kwargs:
                session.status = kwargs['status']
            if 'current_event_id' in kwargs:
                session.current_event_id = kwargs['current_event_id']
            if 'message' in kwargs:
                session.message = kwargs['message']
            
            self.logger.debug(f"Updated {replay_id}: {progress*100:.1f}% ({events_processed} events, {divergences_detected} divergences)")
        else:
            self.logger.warning(f"Cannot update progress: session {replay_id} not found")

    async def update_session_progress(self, replay_id: str, total_events: Optional[int] = None,
                                     events_processed: Optional[int] = None, progress: Optional[float] = None) -> bool:
        """Update session progress metrics"""
        session = await self.get_session(replay_id)
        if not session:
            self.logger.warning(f"Cannot update progress: session {replay_id} not found")
            return False

        if total_events is not None:
            session.total_events = total_events
        
        if events_processed is not None:
            session.events_processed = events_processed
        
        if progress is not None:
            session.progress = progress
        elif session.total_events and session.total_events > 0 and session.events_processed is not None:
            session.progress = session.events_processed / session.total_events
        else:
            session.progress = 0.0

        session.last_updated = datetime.now(timezone.utc)
        
        if session.total_events:
            self.logger.debug(f"Progress update: {replay_id} - {session.events_processed}/{session.total_events} ({session.progress*100:.1f}%)")
        else:
            self.logger.debug(f"Progress update: {replay_id} - {session.events_processed} events ({session.progress*100:.1f}%)")
        
        return True

    async def get_session(self, replay_id: str) -> Optional[ReplaySession]:
        """Retrieve a session by replay ID"""
        session = self.sessions.get(replay_id)
        
        if not session:
            self.logger.warning(f"Session not found for replay {replay_id}")
            return None
        
        # Enrich with current event details
        raw_event = session.raw_event_json
        if raw_event:
            try:
                event_json = json.loads(raw_event) if isinstance(raw_event, str) else raw_event
                
                # Use same generic logic as in update_progress
                method = event_json.get('method', 'GET')
                path = event_json.get('path', '/').lower()
                
                # Generic activity inference
                if 'login' in path or 'auth' in path:
                    activity = 'Authentication'
                elif 'logout' in path:
                    activity = 'Logout'
                elif 'user' in path or 'profile' in path:
                    activity = 'User Management'
                elif 'product' in path or 'item' in path:
                    activity = 'Browse Products'
                elif 'cart' in path or 'basket' in path or 'order' in path:
                    activity = 'Shopping Cart'
                elif 'checkout' in path or 'payment' in path:
                    activity = 'Checkout/Payment'
                elif 'search' in path:
                    activity = 'Search'
                elif 'api/' in path:
                    activity = 'API Request'
                elif 'admin' in path:
                    activity = 'Admin Panel'
                elif method == 'POST':
                    activity = 'Data Submission'
                elif method == 'PUT' or method == 'PATCH':
                    activity = 'Data Update'
                elif method == 'DELETE':
                    activity = 'Data Deletion'
                else:
                    activity = 'Data Retrieval'
                
                session.current_event_details = {
                    'method': method,
                    'path': event_json.get('path', 'Unknown'),
                    'activity': activity,
                    'status': event_json.get('status', 'N/A')
                }
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                self.logger.warning(f"Failed to parse event JSON for {replay_id}: {e}")
                session.current_event_details = {
                    'method': 'GET', 'path': 'Unknown', 'activity': 'Parse Error', 'status': 'N/A'
                }
        else:
            session.current_event_details = {
                'method': session.current_event_id.split()[0] if session.current_event_id else 'GET',
                'path': 'Unknown',
                'activity': 'N/A',
                'status': 'N/A'
            }
        
        return session

    async def list_sessions(self, status: Optional[str] = None, replay_id: Optional[str] = None) -> List[ReplaySession]:
        """List sessions with optional filters"""
        sessions = list(self.sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        if replay_id:
            sessions = [s for s in sessions if s.replay_id == replay_id]
        self.logger.debug(f"Listed {len(sessions)} sessions")
        return sessions

    def complete_session(self, replay_id: str):
        """Mark session as completed"""
        session = self.sessions.get(replay_id)
        if session:
            session.status = "completed"
            session.progress = 1.0
            session.last_updated = datetime.now(timezone.utc)
            self.logger.info(f"Completed session {replay_id}")
        else:
            self.logger.warning(f"Cannot complete: session {replay_id} not found")

    def delete_session(self, replay_id: str):
        """Delete a session"""
        if replay_id in self.sessions:
            del self.sessions[replay_id]
            self.logger.info(f"Deleted session {replay_id}")
        else:
            self.logger.warning(f"Cannot delete: session {replay_id} not found")

    def _get_session_sync(self, replay_id: str) -> Optional[ReplaySession]:
        """Synchronous version of get_session for error handlers"""
        return self.sessions.get(replay_id)

    async def update_session_status(self, replay_id: str, status: str) -> bool:
        """Update session status"""
        session = await self.get_session(replay_id)
        if session:
            session.status = status
            session.last_updated = datetime.now(timezone.utc)
            self.logger.info(f"Updated session {replay_id} status to {status}")
            return True
        else:
            self.logger.warning(f"Cannot update status: session {replay_id} not found")
            return False