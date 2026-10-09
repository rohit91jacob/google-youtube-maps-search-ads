"""Google Trends "trending now" RSS (Search). One poll per geo; poll-to-poll diffs become events."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import polars as pl
from defusedxml import ElementTree

from geco.http import FetchError
from geco.lake import Lake, Manifest, ManifestEntry
from geco.pubsub import PubSub
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCE = "trends"
DATASET = "trending_rss"
EVENTS_DATASET = "trends_events"
HT = "{https://trends.google.com/trending/rss}"

_TRAFFIC = re.compile(r"^\s*([\d.,]+)\s*([KkMm]?)\s*\+?\s*$")


def parse_traffic(value: str | None) -> int | None:
    """'200+' -> 200, '2K+' -> 2000, '1M+' -> 1_000_000. Unknown formats -> None."""
    if not value:
        return None
    match = _TRAFFIC.match(value.replace(" ", " "))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]
    return int(number * multiplier)


def term_key(term: str) -> str:
    return " ".join(term.casefold().split())


def parse_rss(data: bytes) -> list[dict]:
    root = ElementTree.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("not a Trends RSS document: no <channel>")
    items = []
    for rank, item in enumerate(channel.findall("item"), start=1):
        term = (item.findtext("title") or "").strip()
        pub = item.findtext("pubDate")
        started_at = parsedate_to_datetime(pub).astimezone(UTC) if pub else None
        news = item.findall(f"{HT}news_item")
        first = news[0] if news else None
        traffic = item.findtext(f"{HT}approx_traffic")
        items.append(
            {
                "rank": rank,
                "term": term,
                "term_key": term_key(term),
                "approx_traffic": traffic,
                "approx_traffic_min": parse_traffic(traffic),
                "started_at": started_at,
                "news_item_count": len(news),
                "top_news_title": first.findtext(f"{HT}news_item_title") if first is not None else None,
                "top_news_source": first.findtext(f"{HT}news_item_source") if first is not None else None,
                "top_news_url": first.findtext(f"{HT}news_item_url") if first is not None else None,
            }
        )
    return items


def event_id(geo: str, key: str, event_type: str, polled_at: str) -> str:
    return hashlib.sha256(f"{geo}|{key}|{event_type}|{polled_at}".encode()).hexdigest()[:24]


def diff_events(
    geo: str,
    previous: list[dict] | None,
    previous_polled_at: str | None,
    current: list[dict],
    polled_at: str,
) -> list[dict]:
    """'entered' for terms new since the previous poll, 'exited' for terms that dropped out.

    The first poll of a geo emits 'entered' for everything (previous_polled_at is null).
    """
    prev_by_key = {i["term_key"]: i for i in previous or []}
    cur_by_key = {i["term_key"]: i for i in current}
    events = []
    for key, item in cur_by_key.items():
        if key not in prev_by_key:
            events.append(_event(geo, item, "entered", polled_at, previous_polled_at))
    for key, item in prev_by_key.items():
        if key not in cur_by_key:
            events.append(_event(geo, item, "exited", polled_at, previous_polled_at))
    return sorted(events, key=lambda e: (e["event_type"], e["term_key"]))


def _event(geo: str, item: dict, kind: str, polled_at: str, previous_polled_at: str | None) -> dict:
    return {
        "event_id": event_id(geo, item["term_key"], kind, polled_at),
        "event_type": kind,
        "geo": geo,
        "term": item["term"],
        "term_key": item["term_key"],
        "rank": item["rank"],
        "approx_traffic_min": item["approx_traffic_min"],
        "polled_at": polled_at,
        "previous_polled_at": previous_polled_at,
    }


def extract(ctx: ExtractContext, pubsub: PubSub | None = None) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    if pubsub is not None:
        pubsub.ensure()
    for geo in ctx.settings.trends_geos:
        url = ctx.settings.trends_url_template.format(geo=geo)
        try:
            result = ctx.http.get(url)
            data = result.read()
            items = parse_rss(data)
        except (FetchError, ValueError, ElementTree.ParseError) as exc:
            entries.append(ctx.failed(source=SOURCE, dataset=DATASET, key=geo, url=url, error=str(exc)))
            continue
        previous = _previous_poll(ctx.lake, ctx.manifest, geo, before=ctx.now_iso)
        entry = ctx.store(
            source=SOURCE,
            dataset=DATASET,
            key=geo,
            url=url,
            data=data,
            ext="xml",
            detail={"items": len(items)},
        )
        entries.append(entry)
        if pubsub is not None:
            prev_items, prev_at = previous if previous else (None, None)
            events = diff_events(geo, prev_items, prev_at, items, entry.fetched_at)
            if events:
                pubsub.publish(events, ordering_key=geo, attributes={"geo": geo, "schema": "trends-event/v1"})
            log.info("trends[%s]: published %d events", geo, len(events))
    return entries


def _previous_poll(lake: Lake, manifest: Manifest, geo: str, before: str) -> tuple[list[dict], str] | None:
    prior = [e for e in manifest.ok_entries(SOURCE, DATASET) if e.key == geo and e.fetched_at < before]
    if not prior:
        return None
    last = prior[-1]
    return parse_rss(lake.get_object(last.bronze_key)), last.fetched_at


def consume_events(ctx: ExtractContext, pubsub: PubSub, max_batches: int = 50) -> ManifestEntry | None:
    """Drain the subscription into one bronze JSONL object, then ack (at-least-once)."""
    records: dict[str, dict] = {}
    pulled = []
    seen: set[str] = set()
    for _ in range(max_batches):
        # Unacked messages are redelivered (as Pub/Sub does after the ack deadline); stop once a
        # pull brings nothing that has not been seen in this drain.
        batch = [m for m in pubsub.pull(max_messages=1000) if m.message_id not in seen]
        if not batch:
            break
        pulled.extend(batch)
        seen.update(m.message_id for m in batch)
        for message in batch:
            payload = message.json()
            records[payload["event_id"]] = {**payload, "message_id": message.message_id}
    if not records:
        log.info("trends events: subscription empty")
        return None
    body = "\n".join(json.dumps(r, sort_keys=True) for r in sorted(records.values(), key=_event_sort)) + "\n"
    entry = ctx.store(
        source=SOURCE,
        dataset=EVENTS_DATASET,
        key=ctx.run_id,
        url=f"pubsub://{ctx.settings.trends_topic}/{ctx.settings.trends_subscription}",
        data=body.encode(),
        ext="jsonl",
        detail={"messages": len(pulled), "distinct_events": len(records)},
    )
    pubsub.ack(pulled)  # ack only after the batch is durable in bronze
    return entry


def _event_sort(r: dict) -> tuple:
    return (r["geo"], r["polled_at"], r["event_type"], r["term_key"])


# ---------------------------------------------------------------------- raw tables
POLL_SCHEMA = {
    "geo": pl.String,
    "polled_at": pl.Datetime("us", "UTC"),
    "item_count": pl.Int64,
    "sha256": pl.String,
}
ITEM_SCHEMA = {
    "geo": pl.String,
    "polled_at": pl.Datetime("us", "UTC"),
    "rank": pl.Int64,
    "term": pl.String,
    "term_key": pl.String,
    "approx_traffic": pl.String,
    "approx_traffic_min": pl.Int64,
    "started_at": pl.Datetime("us", "UTC"),
    "news_item_count": pl.Int64,
    "top_news_title": pl.String,
    "top_news_source": pl.String,
    "top_news_url": pl.String,
}
EVENT_SCHEMA = {
    "event_id": pl.String,
    "event_type": pl.String,
    "geo": pl.String,
    "term": pl.String,
    "term_key": pl.String,
    "rank": pl.Int64,
    "approx_traffic_min": pl.Int64,
    "polled_at": pl.Datetime("us", "UTC"),
    "previous_polled_at": pl.Datetime("us", "UTC"),
    "message_id": pl.String,
}


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def build_raw(lake: Lake, manifest: Manifest) -> dict[str, pl.DataFrame]:
    polls: dict[tuple[str, str], ManifestEntry] = {}
    for entry in manifest.ok_entries(SOURCE, DATASET):
        polls[(entry.key, entry.fetched_at)] = entry  # a re-run of the same poll overwrites itself
    poll_rows, item_rows = [], []
    parsed_cache: dict[str, list[dict]] = {}
    for (geo, fetched_at), entry in sorted(polls.items()):
        if entry.sha256 not in parsed_cache:
            parsed_cache[entry.sha256] = parse_rss(lake.get_object(entry.bronze_key))
        items = parsed_cache[entry.sha256]
        polled_at = _ts(fetched_at)
        poll_rows.append(
            {"geo": geo, "polled_at": polled_at, "item_count": len(items), "sha256": entry.sha256}
        )
        item_rows.extend({"geo": geo, "polled_at": polled_at, **item} for item in items)

    events: dict[str, dict] = {}
    for entry in manifest.ok_entries(SOURCE, EVENTS_DATASET):
        for line in lake.get_object(entry.bronze_key).decode().splitlines():
            if line.strip():
                record = json.loads(line)
                events.setdefault(record["event_id"], record)  # duplicates from redelivery collapse
    event_rows = [
        {**r, "polled_at": _ts(r["polled_at"]), "previous_polled_at": _ts(r.get("previous_polled_at"))}
        for r in events.values()
    ]
    return {
        "trends_polls": pl.DataFrame(poll_rows, schema=POLL_SCHEMA).sort("geo", "polled_at"),
        "trends_items": pl.DataFrame(item_rows, schema=ITEM_SCHEMA).sort("geo", "polled_at", "rank"),
        "trends_events": pl.DataFrame(
            [{k: r.get(k) for k in EVENT_SCHEMA} for r in event_rows], schema=EVENT_SCHEMA
        ).sort("geo", "polled_at", "event_type", "term_key"),
    }
