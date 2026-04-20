"""
src/state/mysql_adapter.py

MySQL / MariaDB state adapter — mysqldump / mysql restore via docker exec.

dltrf.yaml config section used:
  state_management:
    type: mysql
    mysql:
      container: my-mysql
      database:  myapp
      user:      root
      password:  ""        # empty = use MYSQL_PWD env var on the container
"""

import logging
from pathlib import Path

from .base_adapter import StateAdapter, StateAdapterError

logger = logging.getLogger(__name__)


class MySQLAdapter(StateAdapter):
    """
    Checkpoint adapter for MySQL / MariaDB databases.

    snapshot(): mysqldump → host file
    restore():  DROP DATABASE → CREATE DATABASE → mysql restore
    """

    def __init__(self, config: dict):
        super().__init__(config)
        my = config.get("mysql", {})
        if not my:
            raise StateAdapterError(
                "MySQL adapter requires state_management.mysql section in dltrf.yaml."
            )
        self.my_container = my.get("container") or config.get("container", "")
        self.database      = my.get("database", "")
        self.user          = my.get("user", "root")
        self.password      = my.get("password", "")  # empty = use MYSQL_PWD on container

        for field, val in [("container", self.my_container), ("database", self.database)]:
            if not val:
                raise StateAdapterError(
                    f"MySQL adapter: state_management.mysql.{field} is required in dltrf.yaml."
                )

    def snapshot(self, checkpoint_path: Path) -> None:
        """Run mysqldump inside the container, write SQL to checkpoint_path."""
        self._assert_container_running(self.my_container)

        logger.info(f"MySQL snapshot: {self.my_container}/{self.database} → {checkpoint_path}")

        result = self._run(
            self._mysql_env_cmd() + [
                "mysqldump",
                f"--user={self.user}",
                "--single-transaction",   # consistent snapshot without locking
                "--routines",             # include stored procedures
                "--triggers",             # include triggers
                "--add-drop-database",    # include DROP DATABASE for clean restore
                "--databases", self.database,
            ],
            error_prefix=f"mysqldump failed for database '{self.database}'",
            timeout=300,
        )

        checkpoint_path.write_bytes(result.stdout)
        size = len(result.stdout)
        logger.info(f"MySQL snapshot saved ({size / 1024:.1f} KB)")

    def restore(self, checkpoint_path: Path) -> None:
        """Restore from SQL dump — drop and recreate the database first."""
        if not checkpoint_path.exists():
            raise FileNotFoundError(
                f"Checkpoint not found: {checkpoint_path}. Run checkpoint save first."
            )

        self._assert_container_running(self.my_container)
        sql_data = checkpoint_path.read_bytes()
        logger.info(f"MySQL restore: {checkpoint_path} → {self.my_container}/{self.database}")

        # Drop and recreate the database before restoring
        drop_create_sql = (
            f"DROP DATABASE IF EXISTS `{self.database}`; "
            f"CREATE DATABASE `{self.database}`;"
        ).encode()

        self._run(
            self._mysql_env_cmd() + [
                "mysql",
                f"--user={self.user}",
                "--execute",
                f"DROP DATABASE IF EXISTS `{self.database}`; "
                f"CREATE DATABASE `{self.database}`;",
            ],
            error_prefix=f"DROP/CREATE database '{self.database}' failed",
            timeout=30,
        )

        # Restore dump (dump includes USE <database> due to --databases flag)
        self._run(
            self._mysql_env_cmd() + [
                "mysql",
                f"--user={self.user}",
            ],
            input_data=sql_data,
            error_prefix=f"mysql restore to '{self.database}' failed",
            timeout=300,
        )
        logger.info("MySQL restore complete")

    def health_check(self) -> bool:
        if not self._container_running(self.my_container):
            return False
        try:
            self._run(
                self._mysql_env_cmd() + [
                    "mysqladmin",
                    f"--user={self.user}",
                    "ping",
                ],
                timeout=5,
                error_prefix="health check",
            )
            return True
        except StateAdapterError:
            return False

    def _mysql_env_cmd(self) -> list:
        """
        Build docker exec prefix with MYSQL_PWD set if a password is configured.

        Using MYSQL_PWD env var avoids the password appearing in the process list
        (unlike --password=xxx on the command line).
        """
        if self.password:
            return [
                "docker", "exec",
                "-e", f"MYSQL_PWD={self.password}",
                self.my_container,
            ]
        return ["docker", "exec", self.my_container]