import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def require_test_database_is_separate() -> None:
    test_url = os.getenv("TEST_DATABASE_URL")
    runtime_url = os.getenv("DATABASE_URL")
    if test_url and runtime_url:
        assert test_url != runtime_url, "TEST_DATABASE_URL must not point at the runtime database"
