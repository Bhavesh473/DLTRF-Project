"""
src/state/sqlite_adapter.py

SQLite state adapter — copies the .sqlite/.db file in/out of the container.

dltrf.yaml config section used:
  state_management:
    type: sqlite
    container: juice-shop
    sqlite_path: /juice-shop/data/juiceshop.sqlite
    checkpoint_name: baseline
"""

import logging
import time
from pathlib import Path

from .base_adapter import StateAdapter, StateAdapterError

logger = logging.getLogger(__name__)


class SQLiteAdapter(StateAdapter):
    """
    Checkpoint adapter for SQLite databases.

    snapshot(): docker cp container:/path/to/db -> local file
    restore():  docker cp local file -> container:/path/to/db
                then restarts the container so it picks up the new file
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.sqlite_path = config.get("sqlite_path", "")
        if not self.sqlite_path:
            raise StateAdapterError(
                "SQLite adapter requires state_management.sqlite_path in dltrf.yaml. "
                "Example: sqlite_path: /juice-shop/data/juiceshop.sqlite"
            )

    def snapshot(self, checkpoint_path: Path) -> None:
        """Copy SQLite file from container to host checkpoint_path."""
        self._assert_container_running(self.container)

        logger.info(
            f"SQLite snapshot: {self.container}:{self.sqlite_path} "
            f"→ {checkpoint_path}"
        )
        self._run(
            ["docker", "cp", f"{self.container}:{self.sqlite_path}", str(checkpoint_path)],
            error_prefix=f"SQLite snapshot failed. "
                         f"Check that sqlite_path '{self.sqlite_path}' exists in container "
                         f"'{self.container}'",
        )
        size = checkpoint_path.stat().st_size
        logger.info(f"SQLite snapshot saved ({size / 1024:.1f} KB)")

    def restore(self, checkpoint_path: Path) -> None:
        """Copy checkpoint_path back into the container, then restart it."""
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint file not found: {checkpoint_path}\n"
                f"Run checkpoint save first."
            )

        self._assert_container_running(self.container)

        logger.info(
            f"SQLite restore: {checkpoint_path} "
            f"→ {self.container}:{self.sqlite_path}"
        )
        self._run(
            ["docker", "cp", str(checkpoint_path), f"{self.container}:{self.sqlite_path}"],
            error_prefix="SQLite restore (copy) failed",
        )

        # Restart the container so it re-reads the replaced file.
        # SQLite holds WAL/journal files; a clean restart ensures consistency.
        logger.info(f"Restarting {self.container} to reload database...")
        self._run(
            ["docker", "restart", self.container],
            error_prefix=f"Failed to restart container '{self.container}'",
            timeout=60,
        )

        # Wait for the app to be ready before returning
        self._wait_for_ready()
        logger.info("SQLite restore complete")

    def health_check(self) -> bool:
        return self._container_running(self.container)

    def _wait_for_ready(self, timeout_seconds: int = 60) -> None:
        """
        Poll until the app container is responsive.
        Uses `docker exec` to run a lightweight check rather than making
        an HTTP request (avoids network dependency from inside the container).
        """
        import time

        deadline = time.time() + timeout_seconds
        attempts = 0

        logger.info(f"Waiting for {self.container} to be ready...")
        while time.time() < deadline:
            try:
                result = self._run(
                    ["docker", "inspect", "--format", "{{.State.Running}}", self.container],
                    timeout=5,
                    error_prefix="health poll",
                )
                if result.stdout.strip() == b"true":
                    # Container is up — give it a moment to fully initialise
                    time.sleep(2)
                    logger.info(f"{self.container} is ready (attempt {attempts + 1})")
                    return
            except StateAdapterError:
                pass

            attempts += 1
            time.sleep(2)

        logger.warning(
            f"Container '{self.container}' did not become ready within "
            f"{timeout_seconds}s — continuing anyway"
        )