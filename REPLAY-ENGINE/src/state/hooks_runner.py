"""
src/state/hooks_runner.py

Runs lifecycle hooks defined in dltrf.yaml hooks section.

Hooks are shell commands that run INSIDE the replay-engine container.
They are useful for seeding test data, resetting queues, or notifying
external systems — anything that can be done from inside the container.

IMPORTANT: Hooks cannot directly call `docker exec` or other host commands
unless /var/run/docker.sock is mounted in the replay-engine container.
For DB operations (snapshot/restore) use the StateAdapter / checkpoint.sh.

dltrf.yaml hooks section:
  hooks:
    before_record: ""     # runs before recording starts
    after_record:  ""     # runs after recording ends
    before_replay: ""     # runs after checkpoint restore, before replay fires
    after_replay:  ""     # runs after replay + report generation
"""

import logging
import subprocess
import shlex
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum time (seconds) a hook is allowed to run before being killed
HOOK_TIMEOUT = int(60)


class HooksRunner:
    """
    Executes lifecycle hook commands from dltrf.yaml.

    Each hook is a shell command string. Empty strings and None are silently
    skipped. Failures raise HookError with the full stdout/stderr output.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The full dltrf.yaml dict. Reads config['hooks'] section.
        """
        self.hooks = config.get("hooks", {}) or {}

    def run(self, hook_name: str) -> None:
        """
        Run the named hook if it is defined and non-empty.

        Args:
            hook_name: One of: before_record, after_record,
                                before_replay, after_replay

        Raises:
            HookError: If the command exits with a non-zero status.
        """
        cmd = self.hooks.get(hook_name, "") or ""
        cmd = cmd.strip()

        if not cmd:
            logger.debug(f"Hook '{hook_name}' is not configured — skipping")
            return

        logger.info(f"Running hook '{hook_name}': {cmd}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,          # hooks are free-form shell commands
                capture_output=True,
                timeout=HOOK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise HookError(
                f"Hook '{hook_name}' timed out after {HOOK_TIMEOUT}s.\n"
                f"Command: {cmd}"
            )

        stdout = result.stdout.decode(errors="replace").strip()
        stderr = result.stderr.decode(errors="replace").strip()

        if stdout:
            logger.info(f"Hook '{hook_name}' stdout:\n{stdout}")
        if stderr:
            logger.warning(f"Hook '{hook_name}' stderr:\n{stderr}")

        if result.returncode != 0:
            raise HookError(
                f"Hook '{hook_name}' failed (exit {result.returncode}).\n"
                f"Command: {cmd}\n"
                f"stdout: {stdout or '(empty)'}\n"
                f"stderr: {stderr or '(empty)'}"
            )

        logger.info(f"Hook '{hook_name}' completed successfully")

    def before_record(self) -> None:
        """Run the before_record hook."""
        self.run("before_record")

    def after_record(self) -> None:
        """Run the after_record hook."""
        self.run("after_record")

    def before_replay(self) -> None:
        """Run the before_replay hook (fires after checkpoint restore)."""
        self.run("before_replay")

    def after_replay(self) -> None:
        """Run the after_replay hook (fires after report is saved)."""
        self.run("after_replay")


class HookError(Exception):
    """Raised when a lifecycle hook command fails."""