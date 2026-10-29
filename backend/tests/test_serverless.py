from backend.app.repository import get_dataset


def test_serverless_rebuilds_database_from_bundled_snapshot_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("backend.app.repository.tempfile.gettempdir", lambda: str(tmp_path))
    get_dataset.cache_clear()
    try:
        first = get_dataset()
        assert len(first.actuals) == 1488
        assert len(first.forecasts) == 744
        assert len(list(tmp_path.glob('windlens-*.sqlite'))) == 1
        get_dataset.cache_clear()
        assert get_dataset().metadata["source_hash"] == first.metadata["source_hash"]
    finally:
        get_dataset.cache_clear()
