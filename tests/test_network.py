"""Tests for network connectivity checks with subprocess mocking."""

from __future__ import annotations

import subprocess

import pytest
from pytest_mock import MockerFixture

from src.network import NetworkManager


@pytest.fixture
def network_mgr() -> NetworkManager:
    return NetworkManager(
        nmcli_path="nmcli",
        command_timeout_seconds=5.0,
        internet_check_hosts=("1.1.1.1",),
        internet_check_port=53,
        internet_check_timeout_seconds=1.0,
    )


class TestCheckConnectivity:
    def test_connected_via_nmcli(
        self, network_mgr: NetworkManager, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="full\n", stderr=""
            ),
        )
        status = network_mgr.check_connectivity()
        assert status.is_connected is True
        assert "full" in status.connectivity

    def test_nmcli_not_found_falls_back_to_socket(
        self, network_mgr: NetworkManager, mocker: MockerFixture
    ) -> None:
        mocker.patch("subprocess.run", side_effect=FileNotFoundError("nmcli"))
        mock_socket = mocker.patch("socket.create_connection")
        mock_socket.return_value.__enter__ = lambda s: s
        mock_socket.return_value.__exit__ = lambda s, *a: None

        status = network_mgr.check_connectivity()
        assert status.is_connected is True

    def test_no_connectivity(
        self, network_mgr: NetworkManager, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="none\n", stderr=""
            ),
        )
        mocker.patch("socket.create_connection", side_effect=OSError("no route"))

        status = network_mgr.check_connectivity()
        assert status.is_connected is False
