import pytest


@pytest.fixture(autouse=True)
def _no_real_supabase(monkeypatch):
    """Tests must never read/write a developer's real Supabase project (the
    live machine has these set); tests that need them set fake values."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
