"""
src/state/postgres_adapter.py

PostgreSQL state adapter — pg_dump / psql restore via docker exec.

dltrf.yaml config section used:
  state_management:
    type: postgres
    postgres:
      container: my-postgres
      database:  myapp
      user:      postgres
      password:  ""        # empty = use PGPASSWORD env var on the container
"""

import logging
import os
from pathlib import Path

from .base_adapter import StateAdapter, StateAdapterError

logger = logging.getLogger(__name__)


class PostgresAdapter(StateAdapter):
    """
    Checkpoint adapter for PostgreSQL databases.

    snapshot(): pg_dump → host file
    restore():  DROP DATABASE → CREATE DATABASE → psql restore
    """

    def __init__(self, config: dict):
        super().__init__(config)
        pg = config.get("postgres", {})
        if not pg:
            raise StateAdapterError(
                "Postgres adapter requires state_management.postgres section in dltrf.yaml."
            )
        # Allow container name to fall back to top-level container field
        self.pg_container = pg.get("container") or config.get("container", "")
        self.database     = pg.get("database", "")
        self.user         = pg.get("user", "postgres")
        self.password     = pg.get("password", "")  # empty = use PGPASSWORD on container

        for field, val in [("container", self.pg_container), ("database", self.database)]:
            if not val:
                raise StateAdapterError(
                    f"Postgres adapter: state_management.postgres.{field} is required in dltrf.yaml."
                )

    def snapshot(self, checkpoint_path: Path) -> None:
        """Run pg_dump inside the container, write SQL to checkpoint_path."""
        self._assert_container_running(self.pg_container)

        logger.info(f"Postgres snapshot: {self.pg_container}/{self.database} → {checkpoint_path}")

        cmd = self._pg_env_cmd() + [
            "pg_dump",
            "-U", self.user,
            "-d", self.database,
            "--no-password",
            "--clean",            # include DROP statements for clean restore
            "--if-exists",        # avoid errors on DROP if objects don't exist
            "--format=plain",
        ]

        result = self._run(
            cmd,
            error_prefix=f"pg_dump failed for database '{self.database}'",
            timeout=300,
        )

        checkpoint_path.write_bytes(result.stdout)
        size = len(result.stdout)
        logger.info(f"Postgres snapshot saved ({size / 1024:.1f} KB)")

    def restore(self, checkpoint_path: Path) -> None:
        """
        Restore from SQL dump.

        Uses pg_terminate_backend to forcibly disconnect all clients before
        dropping the database — without this, DROP DATABASE fails if any
        connection is open.
        """
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint not found: {checkpoint_path}. Run checkpoint save first."
            )

        self._assert_container_running(self.pg_container)
        sql_data = checkpoint_path.read_bytes()
        logger.info(f"Postgres restore: {checkpoint_path} → {self.pg_container}/{self.database}")

        # Step 1: Terminate all active connections to the target database.
        # Without this, DROP DATABASE raises "database is being accessed by other users".
        terminate_sql = (
            f"SELECT pg_terminate_backend(pid) "
            f"FROM pg_stat_activity "
            f"WHERE datname = '{self.database}' AND pid <> pg_backend_pid();"
        )
        try:
            self._run(
                self._pg_env_cmd() + [
                    "psql", "-U", self.user, "--no-password", "-d", "postgres",
                    "-c", terminate_sql,
                ],
                error_prefix="pg_terminate_backend failed",
                timeout=30,
            )
        except StateAdapterError as e:
            # Non-fatal: log and continue — the DROP will surface the real error
            logger.warning(f"Could not terminate connections (non-fatal): {e}")

        # Step 2: Drop and recreate the database
        self._run(
            self._pg_env_cmd() + [
                "psql", "-U", self.user, "--no-password", "-d", "postgres",
                "-c", f"DROP DATABASE IF EXISTS \"{self.database}\";",
            ],
            error_prefix=f"DROP DATABASE '{self.database}' failed",
            timeout=30,
        )
        self._run(
            self._pg_env_cmd() + [
                "psql", "-U", self.user, "--no-password", "-d", "postgres",
                "-c", f"CREATE DATABASE \"{self.database}\";",
            ],
            error_prefix=f"CREATE DATABASE '{self.database}' failed",
            timeout=30,
        )

        # Step 3: Restore from dump
        self._run(
            self._pg_env_cmd() + [
                "psql", "-U", self.user, "--no-password",
                "-d", self.database,
                "--set=ON_ERROR_STOP=1",   # abort on first error
            ],
            input_data=sql_data,
            error_prefix=f"psql restore to '{self.database}' failed",
            timeout=300,
        )
        logger.info("Postgres restore complete")

    def health_check(self) -> bool:
        if not self._container_running(self.pg_container):
            return False
        try:
            self._run(
                self._pg_env_cmd() + [
                    "pg_isready", "-U", self.user, "-d", self.database
                ],
                timeout=5,
                error_prefix="health check",
            )
            return True
        except StateAdapterError:
            return False

    def _pg_env_cmd(self) -> list:
        """
        Build docker exec prefix with PGPASSWORD set if a password is configured.

        Passing password via PGPASSWORD env var (not via -W / --password flag)
        avoids the password appearing in the process list.
        """
        if self.password:
            return [
                "docker", "exec",
                "-e", f"PGPASSWORD={self.password}",
                self.pg_container,
            ]
        return ["docker", "exec", self.pg_container]