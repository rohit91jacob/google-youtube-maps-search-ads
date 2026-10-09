"""YouTube (video): public channel RSS feeds (no key) + optional YouTube Data API v3 (YOUTUBE_API_KEY).

The RSS feed lists a channel's 15 most recent uploads with their current view counts. The Data API
adds channel totals (subscribers, views, uploads) and up to 50 recent uploads; it only runs when a
key is configured, and its tests use synthetic, recorded-shape responses.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import polars as pl
from defusedxml import ElementTree

from geco.http import FetchError
from geco.lake import Lake, Manifest, ManifestEntry
from geco.sources.base import ExtractContext

log = logging.getLogger(__name__)

SOURCE = "youtube"
RSS_DATASET = "channel_rss"
API_DATASET = "data_api"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC) if value else None


def parse_feed(data: bytes) -> tuple[dict, list[dict]]:
    root = ElementTree.fromstring(data)
    channel_id = root.findtext("yt:channelId", namespaces=NS) or ""
    if channel_id and not channel_id.startswith("UC"):
        channel_id = "UC" + channel_id  # the feed-level element drops the "UC" prefix
    channel = {
        "channel_id": channel_id,
        "channel_title": root.findtext("atom:title", namespaces=NS),
        "channel_created_at": _dt(root.findtext("atom:published", namespaces=NS)),
    }
    videos = []
    for entry in root.findall("atom:entry", NS):
        group = entry.find("media:group", NS)
        stats = group.find("media:community/media:statistics", NS) if group is not None else None
        rating = group.find("media:community/media:starRating", NS) if group is not None else None
        videos.append(
            {
                "video_id": entry.findtext("yt:videoId", namespaces=NS),
                "channel_id": entry.findtext("yt:channelId", namespaces=NS) or channel_id,
                "title": entry.findtext("atom:title", namespaces=NS),
                "published_at": _dt(entry.findtext("atom:published", namespaces=NS)),
                "updated_at": _dt(entry.findtext("atom:updated", namespaces=NS)),
                "views": int(stats.get("views")) if stats is not None and stats.get("views") else None,
                "star_rating_count": int(rating.get("count"))
                if rating is not None and rating.get("count")
                else None,
            }
        )
    return channel, videos


def extract(ctx: ExtractContext) -> list[ManifestEntry]:
    entries = []
    for channel_id in ctx.settings.youtube_channels:
        url = ctx.settings.youtube_rss_url_template.format(channel_id=channel_id)
        try:
            data = ctx.http.get(url).read()
            parse_feed(data)
        except (FetchError, ElementTree.ParseError) as exc:
            entries.append(
                ctx.failed(source=SOURCE, dataset=RSS_DATASET, key=channel_id, url=url, error=str(exc))
            )
            continue
        entries.append(
            ctx.store(source=SOURCE, dataset=RSS_DATASET, key=channel_id, url=url, data=data, ext="xml")
        )
    if ctx.settings.youtube_api_key is not None:
        entries.extend(extract_api(ctx))
    else:
        log.info("youtube data api: YOUTUBE_API_KEY / GECO_YOUTUBE_API_KEY not set, skipped")
    return entries


def extract_api(ctx: ExtractContext) -> list[ManifestEntry]:
    """channels.list (statistics + uploads playlist), then playlistItems.list for each channel."""
    key = ctx.settings.youtube_api_key.get_secret_value()  # type: ignore[union-attr]
    base = ctx.settings.youtube_api_base_url.rstrip("/")
    ids = ",".join(ctx.settings.youtube_channels)
    url = f"{base}/channels"
    try:
        channels = json.loads(
            ctx.http.get(
                url, params={"part": "snippet,statistics,contentDetails", "id": ids, "key": key}
            ).read()
        )
        playlists = {}
        for item in channels.get("items", []):
            uploads = item["contentDetails"]["relatedPlaylists"]["uploads"]
            playlists[item["id"]] = json.loads(
                ctx.http.get(
                    f"{base}/playlistItems",
                    params={
                        "part": "contentDetails,snippet",
                        "playlistId": uploads,
                        "maxResults": "50",
                        "key": key,
                    },
                ).read()
            )
    except (FetchError, KeyError, ValueError) as exc:
        # Never log the key: requests puts it in the URL, so only the endpoint is reported.
        message = str(exc).replace(key, "***")
        return [ctx.failed(source=SOURCE, dataset=API_DATASET, key="channels", url=url, error=message)]
    body = json.dumps({"channels": channels, "playlist_items": playlists}, sort_keys=True).encode()
    return [ctx.store(source=SOURCE, dataset=API_DATASET, key="channels", url=url, data=body, ext="json")]


VIDEO_SCHEMA = {
    "video_id": pl.String,
    "channel_id": pl.String,
    "title": pl.String,
    "published_at": pl.Datetime("us", "UTC"),
    "updated_at": pl.Datetime("us", "UTC"),
    "views": pl.Int64,
    "star_rating_count": pl.Int64,
    "polled_at": pl.Datetime("us", "UTC"),
    "source": pl.String,
}
CHANNEL_SCHEMA = {
    "channel_id": pl.String,
    "channel_title": pl.String,
    "channel_created_at": pl.Datetime("us", "UTC"),
    "polled_at": pl.Datetime("us", "UTC"),
    "subscriber_count": pl.Int64,
    "view_count": pl.Int64,
    "video_count": pl.Int64,
    "source": pl.String,
}


def _int(value: object) -> int | None:
    return int(value) if value not in (None, "") else None


def build_raw(lake: Lake, manifest: Manifest) -> dict[str, pl.DataFrame]:
    videos, channels = [], []
    for entry in manifest.ok_entries(SOURCE, RSS_DATASET):
        polled_at = _dt(entry.fetched_at)
        channel, items = parse_feed(lake.get_object(entry.bronze_key))
        channels.append(
            {
                **channel,
                "polled_at": polled_at,
                "subscriber_count": None,
                "view_count": None,
                "video_count": None,
                "source": "rss",
            }
        )
        videos.extend({**v, "polled_at": polled_at, "source": "rss"} for v in items)
    for entry in manifest.ok_entries(SOURCE, API_DATASET):
        polled_at = _dt(entry.fetched_at)
        payload = json.loads(lake.get_object(entry.bronze_key))
        for item in payload["channels"].get("items", []):
            stats = item.get("statistics", {})
            channels.append(
                {
                    "channel_id": item["id"],
                    "channel_title": item.get("snippet", {}).get("title"),
                    "channel_created_at": _dt(item.get("snippet", {}).get("publishedAt")),
                    "polled_at": polled_at,
                    "subscriber_count": None
                    if stats.get("hiddenSubscriberCount")
                    else _int(stats.get("subscriberCount")),
                    "view_count": _int(stats.get("viewCount")),
                    "video_count": _int(stats.get("videoCount")),
                    "source": "data_api",
                }
            )
        for channel_id, page in payload["playlist_items"].items():
            for item in page.get("items", []):
                details = item.get("contentDetails", {})
                videos.append(
                    {
                        "video_id": details.get("videoId"),
                        "channel_id": channel_id,
                        "title": item.get("snippet", {}).get("title"),
                        "published_at": _dt(details.get("videoPublishedAt")),
                        "updated_at": None,
                        "views": None,
                        "star_rating_count": None,
                        "polled_at": polled_at,
                        "source": "data_api",
                    }
                )
    # Several polls of the same feed in one instant (re-runs) collapse to one row.
    video_frame = pl.DataFrame(videos, schema=VIDEO_SCHEMA).unique(
        subset=["video_id", "polled_at", "source"], keep="first", maintain_order=True
    )
    channel_frame = pl.DataFrame(channels, schema=CHANNEL_SCHEMA).unique(
        subset=["channel_id", "polled_at", "source"], keep="first", maintain_order=True
    )
    return {
        "youtube_videos": video_frame.sort("channel_id", "video_id", "polled_at", "source"),
        "youtube_channels": channel_frame.sort("channel_id", "polled_at", "source"),
    }
