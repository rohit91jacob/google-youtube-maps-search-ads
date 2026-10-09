"""Shared plumbing for the extractors (the Cloud Run job / Cloud Function bodies)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from geco.config import Settings
from geco.http import HttpClient
from geco.lake import Lake, Manifest, ManifestEntry

log = logging.getLogger(__name__)


@dataclass
class ExtractContext:
    settings: Settings
    lake: Lake
    manifest: Manifest
    http: HttpClient
    run_id: str
    now: datetime

    @property
    def now_iso(self) -> str:
        return self.now.isoformat().replace("+00:00", "Z")

    def store(
        self,
        *,
        source: str,
        dataset: str,
        key: str,
        url: str,
        data: bytes,
        ext: str,
        compress: bool = False,
        etag: str | None = None,
        last_modified: str | None = None,
        detail: dict | None = None,
    ) -> ManifestEntry:
        bronze_key, digest, created = self.lake.put_object(source, dataset, data, ext, compress=compress)
        entry = ManifestEntry(
            run_id=self.run_id,
            source=source,
            dataset=dataset,
            key=key,
            fetched_at=self.now_iso,
            status="new" if created else "unchanged",
            source_url=url,
            sha256=digest,
            bronze_key=bronze_key,
            bytes=len(data),
            etag=etag,
            last_modified=last_modified,
            detail=detail or {},
        )
        log.info("%s/%s[%s]: %s (%d bytes)", source, dataset, key, entry.status, len(data))
        return entry

    def failed(self, *, source: str, dataset: str, key: str, url: str, error: str) -> ManifestEntry:
        log.warning("%s/%s[%s]: failed: %s", source, dataset, key, error)
        return ManifestEntry(
            run_id=self.run_id,
            source=source,
            dataset=dataset,
            key=key,
            fetched_at=self.now_iso,
            status="failed",
            source_url=url,
            detail={"error": error[:500]},
        )

    def not_modified(self, previous: ManifestEntry, url: str) -> ManifestEntry:
        log.info("%s/%s[%s]: not modified (HTTP 304)", previous.source, previous.dataset, previous.key)
        return ManifestEntry(
            run_id=self.run_id,
            source=previous.source,
            dataset=previous.dataset,
            key=previous.key,
            fetched_at=self.now_iso,
            status="not_modified",
            source_url=url,
            sha256=previous.sha256,
            bronze_key=previous.bronze_key,
            bytes=previous.bytes,
            etag=previous.etag,
            last_modified=previous.last_modified,
            detail=previous.detail,
        )
