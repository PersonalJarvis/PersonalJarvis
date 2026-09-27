"""Listener drivers using public protocol clients, with acknowledgement after admission."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import ssl
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from jarvis.core.http_pool import HttpClientPool

from .hook_inbox import MAX_PAYLOAD_BYTES, encode_payload
from .source_schema import SourceSettings


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class SourcePacket:
    delivery_id: str
    body_json: str
    acknowledge: Callable[[], Awaitable[None]] = _nothing
    checkpoint: dict[str, Any] | None = None
    ready: bool = False


def decode(value: bytes | str) -> Any:
    if len(value) > MAX_PAYLOAD_BYTES:
        raise ValueError("Source payload exceeds 32 KiB")
    try:
        text = value.decode("utf-8") if isinstance(value, bytes) else value
    except UnicodeDecodeError:
        assert isinstance(value, bytes)
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    try:
        return json.loads(text)
    except ValueError:
        return text


async def listen_sse(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    headers = {"Accept": "text/event-stream", "Accept-Encoding": "identity"}
    if cursor.get("last_event_id"):
        headers["Last-Event-ID"] = str(cursor["last_event_id"])
    if credentials.get("token"):
        headers["Authorization"] = "Bearer " + credentials["token"]
    pool = HttpClientPool(timeout_s=120)
    try:
        async with pool.client().stream("GET", settings.endpoint, headers=headers) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("Expected an SSE event stream")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("SSE sources must support identity encoding")
            yield SourcePacket("ready", "{}", ready=True)
            data: list[str] = []
            event_id: str | None = None
            event_name, size = "message", 0
            # Bound frames while reading raw chunks, before accumulating a line.
            buffer = b""
            async for chunk in response.aiter_bytes():
                for offset in range(0, len(chunk), 1024):
                    buffer += chunk[offset : offset + 1024]
                    if len(buffer) > MAX_PAYLOAD_BYTES:
                        raise ValueError("SSE line exceeds the payload limit")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.rstrip(b"\r").decode("utf-8")
                        if not line:
                            if data:
                                payload = {"event": event_name, "data": decode("\n".join(data))}
                                yield SourcePacket(
                                    event_id or str(uuid4()),
                                    encode_payload(payload),
                                    checkpoint={"last_event_id": event_id} if event_id else None,
                                )
                            data, event_id, event_name, size = [], None, "message", 0
                            continue
                        field, _, value = line.partition(":")
                        value = value.removeprefix(" ")
                        if field == "data":
                            size += len(value.encode("utf-8")) + 1
                            if size > MAX_PAYLOAD_BYTES:
                                raise ValueError("SSE event exceeds the payload limit")
                            data.append(value)
                        elif field == "id" and "\x00" not in value:
                            event_id = value
                        elif field == "event":
                            event_name = value
    finally:
        await pool.aclose()


def _snapshot(settings: SourceSettings) -> dict[str, list[int]]:
    import fnmatch

    root = Path(settings.path).expanduser().resolve()
    if not root.exists():
        return {}
    paths = (
        [root] if root.is_file() else (root.rglob("*") if settings.recursive else root.glob("*"))
    )
    rows = {}
    for index, path in enumerate(paths):
        if index >= 5000:
            raise ValueError("Choose a smaller watched folder (maximum 5000 entries)")
        if (
            path.is_symlink()
            or not path.is_file()
            or not fnmatch.fnmatch(path.name, settings.pattern)
        ):
            continue
        resolved = path.resolve()
        if root.is_dir() and not resolved.is_relative_to(root):
            continue
        try:
            stat = resolved.stat()
        except FileNotFoundError:
            continue  # A file removed during the snapshot is a normal race.
        rows[str(resolved)] = [stat.st_mtime_ns, stat.st_size]
    return rows


async def listen_file(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    old = cursor.get("files")
    while True:
        current = await asyncio.to_thread(_snapshot, settings)
        if old is None:
            old = current
            yield SourcePacket(
                "baseline:" + str(uuid4()), "{}", checkpoint={"files": current, "baseline": True}
            )
        else:
            for path in sorted(set(old) | set(current)):
                change = (
                    "created"
                    if path not in old
                    else "deleted"
                    if path not in current
                    else "modified"
                )
                if old.get(path) == current.get(path) or change not in settings.file_events:
                    continue
                version = current.get(path) or old[path]
                key = hashlib.sha256(
                    json.dumps([path, change, version, old.get(path)]).encode()
                ).hexdigest()
                after = dict(old)
                if path in current:
                    after[path] = current[path]
                else:
                    after.pop(path, None)
                yield SourcePacket(
                    key,
                    encode_payload(
                        {"path": path, "change": change, "mtime_ns": version[0], "size": version[1]}
                    ),
                    checkpoint={"files": after},
                )
                old = after
            old = current
        await asyncio.sleep(settings.poll_seconds)


async def listen_kafka(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    from importlib import import_module

    module = import_module("aiokafka")
    AIOKafkaConsumer, TopicPartition = module.AIOKafkaConsumer, module.TopicPartition

    address = urlsplit(settings.endpoint)
    options: dict[str, Any] = {
        "bootstrap_servers": address.netloc,
        "group_id": settings.group or identity,
        "enable_auto_commit": False,
        "auto_offset_reset": settings.start_position,
        "retry_backoff_ms": random.SystemRandom().randint(30000, 60000),
    }
    secure = address.scheme == "kafkas"
    options["security_protocol"] = (
        ("SASL_SSL" if secure else "SASL_PLAINTEXT")
        if credentials.get("username")
        else ("SSL" if secure else "PLAINTEXT")
    )
    if secure:
        options["ssl_context"] = ssl.create_default_context()
    if credentials.get("username"):
        options.update(
            sasl_mechanism="PLAIN",
            sasl_plain_username=credentials["username"],
            sasl_plain_password=credentials.get("password", ""),
        )
    client = AIOKafkaConsumer(settings.topic, **options)
    try:
        await client.start()
        async with asyncio.timeout(30):
            while not client.assignment():  # noqa: ASYNC110 - bounded public client readiness probe
                await asyncio.sleep(0.05)
            for partition in client.assignment():
                await client.position(partition)
        yield SourcePacket("ready", "{}", ready=True)
        async for record in client:

            async def acknowledge(
                topic=record.topic, partition=record.partition, offset=record.offset
            ):
                await client.commit({TopicPartition(topic, partition): offset + 1})

            yield SourcePacket(
                f"{record.topic}:{record.partition}:{record.offset}",
                encode_payload(
                    {
                        "topic": record.topic,
                        "partition": record.partition,
                        "offset": record.offset,
                        "data": decode(record.value or b""),
                    }
                ),
                acknowledge,
            )
    finally:
        await client.stop()


async def listen_rabbitmq(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    from importlib import import_module

    aio_pika = import_module("aio_pika")

    address = urlsplit(settings.endpoint)
    client = await aio_pika.connect(
        host=address.hostname,
        port=address.port or (5671 if address.scheme == "amqps" else 5672),
        login=credentials.get("username", ""),
        password=credentials.get("password", ""),
        virtualhost=address.path.removeprefix("/") or "/",
        ssl=address.scheme == "amqps",
        ssl_context=ssl.create_default_context() if address.scheme == "amqps" else None,
        timeout=30,
    )
    try:
        channel = await client.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.get_queue(settings.topic, ensure=True)
        async with queue.iterator(no_ack=False) as messages:
            yield SourcePacket("ready", "{}", ready=True)
            async for message in messages:
                yield SourcePacket(
                    message.message_id or str(uuid4()),
                    encode_payload(
                        {
                            "queue": settings.topic,
                            "routing_key": message.routing_key,
                            "data": decode(message.body),
                        }
                    ),
                    message.ack,
                )
    finally:
        await client.close()


async def listen_redis(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    from redis.asyncio import Redis
    from redis.exceptions import ResponseError

    client = Redis.from_url(
        settings.endpoint,
        username=credentials.get("username") or None,
        password=credentials.get("password") or None,
        socket_connect_timeout=30,
        socket_timeout=30,
    )
    group = settings.group or identity
    try:
        try:
            await client.xgroup_create(
                settings.topic,
                group,
                id="$" if settings.start_position == "latest" else "0",
                mkstream=True,
            )
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise
        yield SourcePacket("ready", "{}", ready=True)
        pending = True
        while True:
            rows = await client.xreadgroup(
                group, identity, {settings.topic: "0" if pending else ">"}, count=1, block=1000
            )
            if not rows or not rows[0][1]:
                pending = False
                continue
            for _, records in rows:
                for entry_id, values in records:
                    if not values:
                        logging.getLogger(__name__).warning(
                            "Redis source skipped a pending entry already removed by the producer"
                        )
                        await client.xack(settings.topic, group, entry_id)
                        continue
                    key = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
                    payload = {
                        (k.decode() if isinstance(k, bytes) else k): decode(v)
                        for k, v in values.items()
                    }

                    async def acknowledge(entry=entry_id):
                        await client.xack(settings.topic, group, entry)

                    yield SourcePacket(
                        key,
                        encode_payload({"stream": settings.topic, "data": payload}),
                        acknowledge,
                    )
    finally:
        await client.aclose()


async def listen_mqtt(
    settings: SourceSettings, credentials: dict, identity: str, cursor: dict
) -> AsyncGenerator[SourcePacket, None]:
    from importlib import import_module

    mqtt = import_module("paho.mqtt.client")

    loop = asyncio.get_running_loop()
    messages: asyncio.Queue = asyncio.Queue(maxsize=100)
    disconnected = asyncio.Event()
    connected = loop.create_future()
    subscribed = loop.create_future()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.group or identity,
        clean_session=False,
        manual_ack=True,
        reconnect_on_failure=False,
    )
    address = urlsplit(settings.endpoint)

    def enqueue(value):
        if messages.full():
            disconnected.set()
            client.disconnect()  # Unacknowledged QoS messages remain at the broker.
        else:
            messages.put_nowait(value)

    def connected_callback(_client, _userdata, _flags, reason, _properties):
        def finish():
            if not connected.done():
                connected.set_result(not reason.is_failure)

        loop.call_soon_threadsafe(finish)

    def subscribed_callback(_client, _userdata, _mid, reasons, _properties):
        def finish():
            if not subscribed.done():
                subscribed.set_result(
                    bool(reasons) and not any(reason.is_failure for reason in reasons)
                )

        loop.call_soon_threadsafe(finish)

    client.on_subscribe = subscribed_callback
    client.on_connect = connected_callback
    client.on_message = lambda _c, _u, msg: loop.call_soon_threadsafe(enqueue, msg)
    client.on_disconnect = lambda *_args: loop.call_soon_threadsafe(disconnected.set)
    if credentials.get("username"):
        client.username_pw_set(credentials["username"], credentials.get("password"))
    if address.scheme == "mqtts":
        client.tls_set_context(ssl.create_default_context())
    try:
        await asyncio.to_thread(
            client.connect,
            address.hostname,
            address.port or (8883 if address.scheme == "mqtts" else 1883),
            60,
        )
        client.loop_start()
        if not await asyncio.wait_for(connected, 30):
            raise ConnectionError("MQTT authentication failed")
        client.subscribe(settings.topic, qos=1)
        if not await asyncio.wait_for(subscribed, 30):
            raise ConnectionError("MQTT subscription was refused")
        yield SourcePacket("ready", "{}", ready=True)
        while True:
            if disconnected.is_set():
                raise ConnectionError("MQTT disconnected")
            try:
                message = await asyncio.wait_for(messages.get(), timeout=1)
            except TimeoutError:
                continue
            if message is None:
                raise ConnectionError("MQTT disconnected")
            data = decode(message.payload)
            event_id = (
                (str(data["event_id"]) if data.get("event_id") is not None else str(uuid4()))
                if isinstance(data, dict)
                else str(uuid4())
            )

            async def acknowledge(mid=message.mid, qos=message.qos):
                if qos:
                    result = await asyncio.to_thread(client.ack, mid, qos)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        raise ConnectionError("MQTT acknowledgement failed")

            yield SourcePacket(
                event_id, encode_payload({"topic": message.topic, "data": data}), acknowledge
            )
    finally:
        client.disconnect()
        await asyncio.to_thread(client.loop_stop)


DRIVERS = {
    "sse": listen_sse,
    "file": listen_file,
    "kafka": listen_kafka,
    "rabbitmq": listen_rabbitmq,
    "mqtt": listen_mqtt,
    "redis": listen_redis,
}
