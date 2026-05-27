"""Shared pytest fixtures for market_simulator tests."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
