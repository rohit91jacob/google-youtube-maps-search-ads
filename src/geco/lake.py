"""Object-store abstraction that stands in for Google Cloud Storage.

Locally the lake is a directory (``file://``); the same code runs against ``gs://`` through gcsfs
or against fake-gcs-server in CI. Layout::

    bronze/<source>/<dataset>/<sha256>.<ext>      immutable, content-addressed source payloads
    raw/<table>.parquet                           typed, normalised tables (BigQuery external tables)
    ops/manifest/<run_id>.jsonl                   one line per fetch/poll (append-only, one object per run)
    ops/dq/<run_id>.json                          data-quality results
    ops/events/<topic>/...                        local Pub/Sub emulator state
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import fsspec
import polars as pl


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Lake:
    def __init__(self, uri: str, **storage_options: Any) -> None:
        self.uri = uri.rstrip("/")
        self.fs, self.root = fsspec.core.url_to_fs(self.uri, **storage_options)
        self.root = self.root.rstrip("/")

    # ------------------------------------------------------------------ primitives
    def path(self, key: str) -> str:
        return f"{self.root}/{key.lstrip('/')}"

    def url(self, key: str) -> str:
        return f"{self.uri}/{key.lstrip('/')}"

    def exists(self, key: str) -> bool:
        return self.fs.exists(self.path(key))

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self.path(key)
        parent = path.rsplit("/", 1)[0]
        self.fs.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp"
        with self.fs.open(tmp, "wb") as handle:
            handle.write(data)
        self.fs.mv(tmp, path)

    def read_bytes(self, key: str) -> bytes:
        with self.fs.open(self.path(key), "rb") as handle:
            return handle.read()

    def list(self, prefix: str, suffix: str = "") -> list[str]:
        base = self.path(prefix)
        if not self.fs.exists(base):
            return []
        found = self.fs.find(base)
        keys = [p[len(self.root) + 1 :] if p.startswith(self.root) else p for p in found]
        return sorted(k for k in keys if k.endswith(suffix) and not k.endswith(".tmp"))

    def delete(self, key: str) -> None:
        if self.exists(key):
            self.fs.rm(self.path(key))

    # ------------------------------------------------------------------ helpers
    def put_object(
        self, source: str, dataset: str, data: bytes, ext: str, *, compress: bool = False
    ) -> tuple[str, str, bool]:
        """Store a payload content-addressed. Returns (key, sha256, created)."""
        digest = sha256_bytes(data)
        key = f"bronze/{source}/{dataset}/{digest}.{ext}" + (".gz" if compress else "")
        if self.exists(key):
            return key, digest, False
        payload = gzip.compress(data, compresslevel=6, mtime=0) if compress else data
        self.write_bytes(key, payload)
        return key, digest, True

    def get_object(self, key: str) -> bytes:
        data = self.read_bytes(key)
        return gzip.decompress(data) if key.endswith(".gz") else data

    def write_parquet(self, key: str, frame: pl.DataFrame) -> None:
        buffer = io.BytesIO()
        frame.write_parquet(buffer, compression="zstd", statistics=True)
        self.write_bytes(key, buffer.getvalue())

    def read_parquet(self, key: str) -> pl.DataFrame:
        return pl.read_parquet(io.BytesIO(self.read_bytes(key)))

    def write_json(self, key: str, obj: Any) -> None:
        self.write_bytes(key, json.dumps(obj, indent=2, sort_keys=True, default=str).encode())

    def local_path(self, key: str) -> str | None:
        """Filesystem path when the lake is local (DuckDB reads parquet from it directly)."""
        protocol = self.fs.protocol if isinstance(self.fs.protocol, str) else self.fs.protocol[0]
        return self.path(key) if protocol in {"file", "local"} else None


@dataclass
class ManifestEntry:
    """One fetch or poll of one source object."""

    run_id: str
    source: str
    dataset: str
    key: str  # geo, channel id, city or file name
    fetched_at: str  # ISO 8601 UTC
    status: str  # new | unchanged | not_modified | failed
    source_url: str
    sha256: str | None = None
    bronze_key: str | None = None
    bytes: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class Manifest:
    """Append-only manifest: one JSONL object per run, so it also works on object stores."""

    PREFIX = "ops/manifest"

    def __init__(self, lake: Lake) -> None:
        self.lake = lake
        self._exists_cache: dict[str, bool] = {}

    def write_run(self, run_id: str, entries: Iterable[ManifestEntry]) -> str | None:
        lines = [json.dumps(asdict(e), sort_keys=True) for e in entries]
        if not lines:
            return None
        key = f"{self.PREFIX}/{run_id}.jsonl"
        existing = self.lake.read_bytes(key).decode().splitlines() if self.lake.exists(key) else []
        self.lake.write_bytes(key, ("\n".join([*existing, *lines]) + "\n").encode())
        return key

    def entries(self, source: str | None = None, dataset: str | None = None) -> Iterator[ManifestEntry]:
        for key in self.lake.list(self.PREFIX, ".jsonl"):
            for line in self.lake.read_bytes(key).decode().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if source and row["source"] != source:
                    continue
                if dataset and row["dataset"] != dataset:
                    continue
                yield ManifestEntry(**row)

    def ok_entries(self, source: str, dataset: str | None = None) -> list[ManifestEntry]:
        """Successful entries, oldest first. A re-run of the same poll (same source, dataset, key and
        fetched_at, e.g. a retried job with a frozen clock) counts once."""
        rows: dict[tuple[str, str, str], ManifestEntry] = {}
        for e in self.entries(source, dataset):
            if e.bronze_key and e.status != "failed":
                rows[(e.dataset, e.key, e.fetched_at)] = e
        # Entries whose object was pruned stay in the manifest (history) but are not loadable.
        rows = {k: e for k, e in rows.items() if self._present(e.bronze_key)}
        return sorted(rows.values(), key=lambda e: (e.fetched_at, e.dataset, e.key))

    def _present(self, key: str | None) -> bool:
        if key is None:
            return False
        if key not in self._exists_cache:
            self._exists_cache[key] = self.lake.exists(key)
        return self._exists_cache[key]

    def latest(self, source: str, dataset: str, key: str) -> ManifestEntry | None:
        rows = [e for e in self.ok_entries(source, dataset) if e.key == key]
        return rows[-1] if rows else None
