"""The raw DQ gate stops the real pipeline path on bad source data, before dbt runs."""

from __future__ import annotations

import csv
import io
import os
import shutil
import zipfile

import duckdb
import pytest

from geco.cli import run_fixture_pipeline
from geco.pipeline import EXIT_DQ_FAILED, StageError
from tests.conftest import FIXTURES

pytestmark = pytest.mark.e2e

WEEKLY = "google-political-ads-advertiser-weekly-spend.csv"


def _tamper_weekly_spend(fixtures, value: str) -> None:
    bundle = fixtures / "synthetic" / "political_ads" / "google-political-ads-transparency-bundle.zip"
    with zipfile.ZipFile(bundle) as z:
        members = {name: z.read(name) for name in z.namelist()}
    rows = list(csv.reader(io.StringIO(members[WEEKLY].decode("utf-8"))))
    rows[1][rows[0].index("Spend_USD")] = value
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    members[WEEKLY] = out.getvalue().encode("utf-8")
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)


@pytest.mark.parametrize(
    ("value", "check"), [("-500", "ads_spend_non_negative"), ("n/a", "ads_spend_parsed")]
)
def test_bad_spend_fails_the_raw_gate(tmp_path, value, check):
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    _tamper_weekly_spend(fixtures, value)
    data_dir = tmp_path / "data"

    saved = dict(os.environ)  # run_fixture_pipeline sets GECO_* for each fixture poll
    try:
        with pytest.raises(StageError) as exc:
            run_fixture_pipeline(data_dir, fixtures)
    finally:
        os.environ.clear()
        os.environ.update(saved)

    assert exc.value.exit_code == EXIT_DQ_FAILED
    assert check in str(exc.value)
    # The gate stopped the run before dbt: the warehouse has raw views but no marts.
    with duckdb.connect(str(data_dir / "warehouse" / "geco.duckdb"), read_only=True) as con:
        schemas = {
            r[0]
            for r in con.execute("select distinct table_schema from information_schema.tables").fetchall()
        }
    assert "raw" in schemas and "marts" not in schemas
