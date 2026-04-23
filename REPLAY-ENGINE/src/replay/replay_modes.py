"""
Replay Modes - REPLAY, PASSTHROUGH, RECORD, HYBRID
"""

from enum import Enum


class ReplayMode(Enum):
    """Replay behavior modes"""
    REPLAY = "replay"           # Use stored HAR responses only
    PASSTHROUGH = "passthrough" # Make live HTTP requests
    RECORD = "record"           # Capture new responses to HAR
    HYBRID = "hybrid"           # Replay if found, else passthrough


class ReplayModeHandler:
    """Manages current replay mode and decisions"""
    
    def __init__(self, mode: ReplayMode = ReplayMode.REPLAY):
        self.mode = mode
    
    def set_mode(self, mode: ReplayMode):
        """Change replay mode"""
        self.mode = mode
        print(f"✓ Replay mode set to: {mode.value}")
    
    def should_use_stored(self) -> bool:
        """Should we use stored HAR response?"""
        return self.mode in [ReplayMode.REPLAY, ReplayMode.HYBRID]
    
    def should_make_live_request(self) -> bool:
        """Should we make actual HTTP request?"""
        return self.mode in [ReplayMode.PASSTHROUGH, ReplayMode.RECORD]
    
    def should_record(self) -> bool:
        """Should we save response to HAR?"""
        return self.mode == ReplayMode.RECORD


if __name__ == "__main__":
    handler = ReplayModeHandler()
    print(f"Default mode: {handler.mode.value}")
    print(f"Use stored? {handler.should_use_stored()}")
    print(f"Make live? {handler.should_make_live_request()}")
    
    handler.set_mode(ReplayMode.PASSTHROUGH)
    print(f"Use stored? {handler.should_use_stored()}")
    print(f"Make live? {handler.should_make_live_request()}")
    
    print("\n✓ Replay Modes ready!")