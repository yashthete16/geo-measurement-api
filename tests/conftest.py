import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite://", max_upload_bytes=200_000, max_uncompressed_bytes=500_000
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
