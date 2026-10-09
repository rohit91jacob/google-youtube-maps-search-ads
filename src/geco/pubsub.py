"""Pub/Sub for the trends event stream.

``LocalPubSub`` is a small file-backed emulator on top of the lake (at-least-once delivery, explicit
acks, ordering key per message). ``GcpPubSub`` uses google-cloud-pubsub and works against real
Pub/Sub or the official emulator (``PUBSUB_EMULATOR_HOST``); CI runs it against the emulator.
Consumers must be idempotent: every message carries a deterministic ``event_id``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Protocol

from geco.config import Settings
from geco.lake import Lake, sha256_bytes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    message_id: str
    data: bytes
    attributes: dict[str, str]
    ordering_key: str
    ack_id: str

    def json(self) -> dict:
        return json.loads(self.data)


class PubSub(Protocol):
    def ensure(self) -> None: ...
    def publish(self, payloads: list[dict], ordering_key: str, attributes: dict[str, str]) -> list[str]: ...
    def pull(self, max_messages: int = 1000) -> list[Message]: ...
    def ack(self, messages: list[Message]) -> None: ...


class LocalPubSub:
    """Topic = one object per message; subscription = one object per acked message."""

    def __init__(self, lake: Lake, topic: str, subscription: str) -> None:
        self.lake = lake
        self.topic_prefix = f"ops/pubsub/topics/{topic}"
        self.sub_prefix = f"ops/pubsub/subscriptions/{subscription}/acked"

    def ensure(self) -> None:
        """Nothing to create: prefixes appear on first write."""

    def publish(self, payloads: list[dict], ordering_key: str, attributes: dict[str, str]) -> list[str]:
        ids = []
        for payload in payloads:
            data = json.dumps(payload, sort_keys=True).encode()
            message_id = sha256_bytes(data)[:20]
            record = {
                "message_id": message_id,
                "ordering_key": ordering_key,
                "attributes": attributes,
                "data": payload,
            }
            self.lake.write_bytes(f"{self.topic_prefix}/{message_id}.json", json.dumps(record).encode())
            ids.append(message_id)
        return ids

    def pull(self, max_messages: int = 1000) -> list[Message]:
        acked = {k.rsplit("/", 1)[-1].removesuffix(".ack") for k in self.lake.list(self.sub_prefix, ".ack")}
        messages = []
        for key in self.lake.list(self.topic_prefix, ".json"):
            message_id = key.rsplit("/", 1)[-1].removesuffix(".json")
            if message_id in acked:
                continue
            record = json.loads(self.lake.read_bytes(key))
            messages.append(
                Message(
                    message_id=message_id,
                    data=json.dumps(record["data"], sort_keys=True).encode(),
                    attributes=record.get("attributes", {}),
                    ordering_key=record.get("ordering_key", ""),
                    ack_id=message_id,
                )
            )
            if len(messages) >= max_messages:
                break
        return messages

    def ack(self, messages: list[Message]) -> None:
        for message in messages:
            self.lake.write_bytes(f"{self.sub_prefix}/{message.ack_id}.ack", b"")


class GcpPubSub:  # pragma: no cover - exercised by the emulator integration test in CI
    def __init__(self, project: str, topic: str, subscription: str) -> None:
        from google.cloud import pubsub_v1

        self._pubsub = pubsub_v1
        self.publisher = pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(enable_message_ordering=True)
        )
        self.subscriber = pubsub_v1.SubscriberClient()
        self.topic_path = self.publisher.topic_path(project, topic)
        self.sub_path = self.subscriber.subscription_path(project, subscription)

    def ensure(self) -> None:
        from google.api_core.exceptions import AlreadyExists

        with contextlib.suppress(AlreadyExists):
            self.publisher.create_topic(request={"name": self.topic_path})
        with contextlib.suppress(AlreadyExists):
            self.subscriber.create_subscription(
                request={
                    "name": self.sub_path,
                    "topic": self.topic_path,
                    "enable_message_ordering": True,
                    "ack_deadline_seconds": 60,
                }
            )

    def publish(self, payloads: list[dict], ordering_key: str, attributes: dict[str, str]) -> list[str]:
        futures = [
            self.publisher.publish(
                self.topic_path,
                json.dumps(p, sort_keys=True).encode(),
                ordering_key=ordering_key,
                **attributes,
            )
            for p in payloads
        ]
        return [f.result(timeout=60) for f in futures]

    def pull(self, max_messages: int = 1000) -> list[Message]:
        response = self.subscriber.pull(
            request={"subscription": self.sub_path, "max_messages": max_messages}, timeout=30
        )
        return [
            Message(
                message_id=r.message.message_id,
                data=r.message.data,
                attributes=dict(r.message.attributes),
                ordering_key=r.message.ordering_key,
                ack_id=r.ack_id,
            )
            for r in response.received_messages
        ]

    def ack(self, messages: list[Message]) -> None:
        if messages:
            self.subscriber.acknowledge(
                request={"subscription": self.sub_path, "ack_ids": [m.ack_id for m in messages]}
            )


def get_pubsub(settings: Settings, lake: Lake) -> PubSub:
    if settings.pubsub_backend == "gcp":
        return GcpPubSub(settings.gcp_project, settings.trends_topic, settings.trends_subscription)
    return LocalPubSub(lake, settings.trends_topic, settings.trends_subscription)
