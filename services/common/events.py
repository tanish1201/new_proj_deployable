import asyncio
import json
import logging
import os
from typing import Any

import aio_pika


LOGGER = logging.getLogger(__name__)
EXCHANGE_NAME = "learnhub.events"
DEAD_LETTER_EXCHANGE = "learnhub.events.dlx"


async def publish(routing_key: str, payload: dict[str, Any], event_id: str) -> None:
    connection = await aio_pika.connect_robust(
        os.environ["RABBITMQ_URL"],
        timeout=5,
    )
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        await exchange.publish(
            aio_pika.Message(
                json.dumps(payload).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=event_id,
            ),
            routing_key=routing_key,
            mandatory=True,
        )


async def declare_event_queue(channel: aio_pika.abc.AbstractChannel, queue_name: str):
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    dead_letter = await channel.declare_exchange(
        DEAD_LETTER_EXCHANGE,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    dead_queue = await channel.declare_queue(f"{queue_name}.dead", durable=True)
    await dead_queue.bind(dead_letter, routing_key="#")
    queue = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments={"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE},
    )
    return exchange, queue
