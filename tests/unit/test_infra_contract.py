"""The Terraform and the Workflows definition agree with the code they deploy."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from geco.cli import app
from geco.raw import ALL_TABLES

ROOT = Path(__file__).parents[2]


def test_bigquery_external_tables_match_raw_tables():
    tf = (ROOT / "infra" / "modules" / "bigquery" / "main.tf").read_text(encoding="utf-8")
    block = tf[tf.index('variable "raw_tables"') :]
    block = block[block.index("default = [") : block.index("]")]
    assert sorted(re.findall(r'"([a-z_]+)"', block)) == sorted(ALL_TABLES)


def test_external_table_uris_match_the_lake_layout():
    tf = (ROOT / "infra" / "modules" / "bigquery" / "main.tf").read_text(encoding="utf-8")
    assert "/lake/raw/${each.value}/*.parquet" in tf  # geco.raw.raw_key -> raw/<table>/data.parquet


def test_workflow_steps_call_real_cli_commands():
    workflow = yaml.safe_load((ROOT / "infra" / "workflows" / "pipeline.yaml").read_text(encoding="utf-8"))
    text = yaml.safe_dump(workflow)
    used = set(re.findall(r"job_args:\n\s*- ([a-z-]+)", text))
    commands = {c.name or c.callback.__name__.replace("_", "-") for c in app.registered_commands}
    assert used == {"poll-trends", "extract", "consume-events", "load", "transform"}
    assert used <= commands


def test_cloud_run_jobs_cover_every_workflow_job():
    tf = (ROOT / "infra" / "modules" / "cloud_run_jobs" / "main.tf").read_text(encoding="utf-8")
    for stage in ("extract", "load", "transform"):
        assert re.search(rf"^\s+{stage} = \{{", tf, re.MULTILINE), stage
