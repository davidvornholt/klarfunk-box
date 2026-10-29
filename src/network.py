"""Network management helpers for Klarfunk Box (NetworkManager via nmcli)."""

from __future__ import annotations

import logging
import socket
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """Raw command result."""

    returncode: int
    stdout: str
    stderr: str
    error: str | None


@dataclass(frozen=True)
class ConnectivityStatus:
    """Connectivity status summary."""

    is_connected: bool
    connectivity: str
    reason: str


class NetworkManager:
    """NetworkManager wrapper using nmcli."""

    def __init__(
        self,
        nmcli_path: str,
        command_timeout_seconds: float,
        internet_check_hosts: tuple[str, ...],
        internet_check_port: int,
        internet_check_timeout_seconds: float,
    ) -> None:
        if not isinstance(nmcli_path, str) or not nmcli_path.strip():
            msg = "nmcli_path must be a non-empty string"
            raise ValueError(msg)
        if (
            not isinstance(command_timeout_seconds, (int, float))
            or command_timeout_seconds < 0
        ):
            msg = "command_timeout_seconds must be a non-negative number"
            raise ValueError(msg)
        if (
            not isinstance(internet_check_timeout_seconds, (int, float))
            or internet_check_timeout_seconds < 0
        ):
            msg = "internet_check_timeout_seconds must be a non-negative number"
            raise ValueError(msg)
        if not isinstance(internet_check_hosts, tuple) or not internet_check_hosts:
            msg = "internet_check_hosts must be a non-empty tuple of strings"
            raise ValueError(msg)
        if any(
            not isinstance(host, str) or not host.strip()
            for host in internet_check_hosts
        ):
            msg = "internet_check_hosts must be a non-empty tuple of strings"
            raise ValueError(msg)
        if (
            not isinstance(internet_check_port, int)
            or not 1 <= internet_check_port <= 65535
        ):
            msg = "internet_check_port must be between 1 and 65535"
            raise ValueError(msg)
        self._nmcli_path = nmcli_path
        self._command_timeout_seconds = command_timeout_seconds
        self._internet_check_hosts = internet_check_hosts
        self._internet_check_port = internet_check_port
        self._internet_check_timeout_seconds = internet_check_timeout_seconds

    def check_connectivity(self) -> ConnectivityStatus:
        """Return connectivity status using nmcli and socket probe fallback."""
        result = self._run_nmcli(
            ("-t", "-f", "CONNECTIVITY", "general"),
            timeout_seconds=self._command_timeout_seconds,
        )
        if result.error is None and result.returncode == 0:
            status = result.stdout.strip()
            is_connected = status == "full"
            reason = "nmcli reported full connectivity" if is_connected else status
            return ConnectivityStatus(
                is_connected=is_connected,
                connectivity=status,
                reason=reason,
            )

        socket_ok = self._check_hosts()
        reason = (
            "socket probe succeeded"
            if socket_ok
            else (result.error or "nmcli unavailable")
        )
        return ConnectivityStatus(
            is_connected=socket_ok,
            connectivity="unknown",
            reason=reason,
        )

    def _run_nmcli(
        self, args: tuple[str, ...], timeout_seconds: float
    ) -> CommandResult:
        cmd = (self._nmcli_path, *args)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError:
            msg = f"nmcli not found at '{self._nmcli_path}'"
            logger.warning(msg)
            return CommandResult(1, "", "", msg)
        except subprocess.TimeoutExpired:
            msg = "nmcli timed out"
            logger.warning(msg)
            return CommandResult(1, "", "", msg)

        return CommandResult(
            completed.returncode,
            completed.stdout.strip(),
            completed.stderr.strip(),
            None,
        )

    def _check_hosts(self) -> bool:
        if not self._internet_check_hosts:
            return False

        for host in self._internet_check_hosts:
            try:
                with socket.create_connection(
                    (host, self._internet_check_port),
                    timeout=self._internet_check_timeout_seconds,
                ):
                    return True
            except OSError:
                continue
        return False
