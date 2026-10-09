from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from geco.cli import app
from tests.conftest import SYNTHETIC

runner = CliRunner()


def test_help_lists_every_stage():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("extract", "load", "transform", "dq", "report", "run", "poll-trends", "fixtures-run"):
        assert command in result.output


def test_extract_and_status_on_fixtures(tmp_path, monkeypatch):
    monkeypatch.setenv("GECO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GECO_TRENDS_GEOS", "US")
    monkeypatch.setenv("GECO_TRENDS_URL_TEMPLATE", (SYNTHETIC / "trends" / "poll1").as_uri() + "/{geo}.xml")
    monkeypatch.setenv("GECO_AS_OF", "2026-10-05T06:00:00Z")
    result = runner.invoke(app, ["extract", "-s", "trends"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output[result.output.index("{") :]) == {"trends:new": 1}
    status = runner.invoke(app, ["status"])
    assert "trends/trending_rss/US" in status.output


def test_unknown_source_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("GECO_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["extract", "-s", "nope"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)


@pytest.mark.live
def test_live_trends_feed_parses():
    """Network: the real Trends RSS still has the shape the parser expects."""
    from geco.config import Settings
    from geco.http import HttpClient
    from geco.sources.trends import parse_rss

    settings = Settings(_env_file=None)
    items = parse_rss(HttpClient(settings).get(settings.trends_url_template.format(geo="US")).read())
    assert items and all(i["term"] for i in items)
