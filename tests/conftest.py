from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from geco.config import Settings
from geco.http import HttpClient
from geco.lake import Lake, Manifest
from geco.sources.base import ExtractContext

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"

_ENV_PREFIXES = ("GECO_", "YOUTUBE_API_KEY", "DBT_TARGET")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never inherit the developer's .env or shell settings."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(Path(__file__).parents[1])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        http_retries=0,
        http_backoff_seconds=0,
        as_of=datetime(2026, 10, 5, 6, 0, tzinfo=UTC),
    )


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    return Lake((tmp_path / "lake").as_uri())


@pytest.fixture
def ctx(settings: Settings, lake: Lake) -> ExtractContext:
    return ExtractContext(
        settings=settings,
        lake=lake,
        manifest=Manifest(lake),
        http=HttpClient(settings),
        run_id="20261005T060000Z",
        now=settings.now(),
    )


def make_ctx(base: ExtractContext, **overrides) -> ExtractContext:
    settings = base.settings.model_copy(update=overrides.pop("settings", {}))
    return ExtractContext(
        settings=settings,
        lake=base.lake,
        manifest=base.manifest,
        http=HttpClient(settings),
        run_id=overrides.get("run_id", base.run_id),
        now=overrides.get("now", base.now),
    )


@pytest.fixture(scope="session")
def fixture_run(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict]:
    """The full pipeline on fixtures, once per session (dbt build included)."""
    from geco.cli import run_fixture_pipeline

    saved = dict(os.environ)
    root = tmp_path_factory.mktemp("e2e")
    data_dir = root / "data"
    cwd = Path.cwd()
    os.chdir(Path(__file__).parents[1])
    try:
        pipe = run_fixture_pipeline(data_dir, FIXTURES, site=root / "site")
        yield {"pipe": pipe, "data_dir": data_dir, "site": root / "site", "root": root}
    finally:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(saved)
