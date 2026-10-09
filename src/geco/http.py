"""HTTP access with retries, conditional requests and file:// support (used for fixtures)."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from geco.config import Settings

log = logging.getLogger(__name__)

RETRY_STATUSES = (429, 500, 502, 503, 504)


class FetchError(RuntimeError):
    """A source could not be fetched after all retries."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int  # 200, or 304 when the conditional request says "not modified"
    content: bytes | None = None
    path: Path | None = None  # set instead of content for large streamed downloads
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def read(self) -> bytes:
        if self.content is not None:
            return self.content
        if self.path is not None:
            return self.path.read_bytes()
        raise FetchError(f"{self.url}: no body (status {self.status})")


def file_url_to_path(url: str) -> Path:
    parsed = urlparse(url)
    return Path(url2pathname(unquote(parsed.netloc + parsed.path) if parsed.netloc else parsed.path))


class HttpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        retry = Retry(
            total=settings.http_retries,
            backoff_factor=settings.http_backoff_seconds,
            status_forcelist=RETRY_STATUSES,
            allowed_methods=frozenset({"GET", "HEAD", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers["User-Agent"] = settings.user_agent

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        stream_to_disk: bool = False,
    ) -> FetchResult:
        if url.startswith("file://"):
            return self._read_file(url)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=self.settings.http_timeout_seconds,
                stream=stream_to_disk,
            )
        except requests.RequestException as exc:
            raise FetchError(f"GET {url}: {exc}") from exc
        return self._result(url, response, stream_to_disk)

    def post_form(self, url: str, data: dict[str, str]) -> FetchResult:
        if url.startswith("file://"):
            return self._read_file(url)
        try:
            response = self.session.post(url, data=data, timeout=self.settings.http_timeout_seconds)
        except requests.RequestException as exc:
            raise FetchError(f"POST {url}: {exc}") from exc
        return self._result(url, response, stream_to_disk=False)

    def _result(self, url: str, response: requests.Response, stream_to_disk: bool) -> FetchResult:
        meta = {
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
        }
        if response.status_code == 304:
            return FetchResult(url=url, status=304, **meta)
        if response.status_code != 200:
            snippet = response.text[:300] if not stream_to_disk else ""
            raise FetchError(f"{url}: HTTP {response.status_code} {snippet!r}")
        if not stream_to_disk:
            return FetchResult(url=url, status=200, content=response.content, **meta)
        with tempfile.NamedTemporaryFile(prefix="geco-", suffix=".download", delete=False) as handle:
            started = time.monotonic()
            shutil.copyfileobj(response.raw, handle, length=1 << 20)
            log.info("downloaded %s in %.1fs", url, time.monotonic() - started)
            return FetchResult(url=url, status=200, path=Path(handle.name), **meta)

    @staticmethod
    def _read_file(url: str) -> FetchResult:
        path = file_url_to_path(url)
        if not path.exists():
            raise FetchError(f"{url}: no such file {path}")
        return FetchResult(url=url, status=200, content=path.read_bytes())
