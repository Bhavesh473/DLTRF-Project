"""
src/state/base_adapter.py

Abstract base class for DLTRF state adapters.
Implements Memento's checkpoint concept at the DB layer.

Every adapter must implement:
  snapshot(checkpoint_path)  — save current DB state to a file
  restore(checkpoint_path)   — restore DB state from a file
  health_check()             — verify the DB is reachable

Adapters run subprocess calls to docker commands.
REQUIREMENT: /var/run/docker.sock must be mounted in the replay-engine
container, and docker CLI must be available.
  replay-engine docker-compose.yml:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import logging
import subprocess

logger = logging.getLogger(__name__)


class StateAdapterError(Exception):
    """Raised when a snapshot or restore operation fails."""


class StateAdapter(ABC):
    """
    Abstract base for database checkpoint adapters.

    Subclasses must implement snapshot(), restore(), and health_check().
    All methods raise StateAdapterError on failure — never swallow exceptions.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The state_management section of dltrf.yaml.
                    Subclasses extract their specific keys from this dict.
        """
        self.config = config
        self.container = config.get("container", "")
        self.checkpoint_name = config.get("checkpoint_name", "baseline")

    @abstractmethod
    def snapshot(self, checkpoint_path: Path) -> None:
        """
        Save the current database state to checkpoint_path.

        Args:
            checkpoint_path: Absolute path on the HOST where the snapshot file
                             should be written. The directory is guaranteed to
                             exist before this is called.

        Raises:
            StateAdapterError: If the snapshot fails for any reason.
        """

    @abstractmethod
    def restore(self, checkpoint_path: Path) -> None:
        """
        Restore the database to the state captured in checkpoint_path.

        Args:
            checkpoint_path: Absolute path on the HOST to the snapshot file
                             created by snapshot().

        Raises:
            StateAdapterError: If the restore fails for any reason.
            FileNotFoundError: If checkpoint_path does not exist.
        """

    @abstractmethod
    def health_check(self) -> bool:
        """
        Verify the database / container is reachable.

        Returns:
            True if healthy, False if not.
        """

    # ─────────────────────────────────────────────────────────────────────────
    # Shared helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _run(
        self,
        cmd: list,
        input_data: Optional[bytes] = None,
        timeout: int = 120,
        error_prefix: str = "Command failed",
    ) -> subprocess.CompletedProcess:
        """
        Run a subprocess command and raise StateAdapterError on failure.

        Args:
            cmd:          Command list to run.
            input_data:   Optional stdin bytes (for piped restore operations).
            timeout:      Seconds before killing the process.
            error_prefix: Prefix for the error message on failure.

        Returns:
            CompletedProcess with stdout/stderr captured.

        Raises:
            StateAdapterError: On non-zero return code or timeout.
        """
        logger.debug(f"Running: {' '.join(str(c) for c in cmd)}")
        try:
            result = subprocess.run(
                cmd,
                input=input_data,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise StateAdapterError(
                f"{error_prefix}: command timed out after {timeout}s\n"
                f"Command: {' '.join(str(c) for c in cmd)}"
            )
        except FileNotFoundError as e:
            raise StateAdapterError(
                f"{error_prefix}: executable not found — {e}\n"
                f"Ensure docker CLI is installed in the replay-engine container.\n"
                f"Command: {' '.join(str(c) for c in cmd)}"
            )

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            stdout = result.stdout.decode(errors="replace").strip()
            raise StateAdapterError(
                f"{error_prefix} (exit {result.returncode})\n"
                f"stderr: {stderr}\n"
                f"stdout: {stdout[:500] if stdout else '(empty)'}\n"
                f"Command: {' '.join(str(c) for c in cmd)}"
            )

        return result

    def _container_running(self, container_name: str) -> bool:
        """Return True if the named Docker container is currently running."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0 and result.stdout.strip() == b"true"
        except Exception:
            return False

    def _assert_container_running(self, container_name: str) -> None:
        """Raise StateAdapterError if the container is not running."""
        if not self._container_running(container_name):
            raise StateAdapterError(
                f"Container '{container_name}' is not running. "
                f"Start it before taking a checkpoint."
            )