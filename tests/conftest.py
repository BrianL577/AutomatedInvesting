import pytest


@pytest.fixture(autouse=True)
def _no_real_supabase(monkeypatch):
    """Tests must never read/write a developer's real Supabase project.
    The live machine has SUPABASE_* in its .env, and load_config() re-reads
    that .env (load_dotenv) on every call -- so clearing env vars alone isn't
    enough; also neutralize load_dotenv. Tests that need Supabase set fake
    values themselves."""
    import jj_bot.config as config_module

    monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
