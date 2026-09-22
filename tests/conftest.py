import pytest

from system_one_chess import retry


@pytest.fixture(autouse=True)
def no_retry_pauses(monkeypatch):
    monkeypatch.setattr(retry, "PAUSES", (0.0, 0.0, 0.0))
