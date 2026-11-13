"""Bounded persistent FIFO for Bender video requests.

One MongoDB document holds the short queue and a renewable consumer lease.
External side effects are preceded by a persisted phase: after a crash we can
check an existing worker UUID, but never blindly resubmit or resend a video.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import uuid

logger = logging.getLogger(__name__)
QUEUE_ID = 'bender-video'
QUEUE_LIMIT = 10  # Includes the item currently being processed.
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15
IDLE_SECONDS = 3
BUSY_SECONDS = 10


def now():
    return datetime.now(timezone.utc)


class LeaseLost(Exception):
    pass


class VideoQueue:
    def __init__(self, collection, *, owner=None):
        self.collection = collection
        self.owner = owner or str(uuid.uuid4())
        self.wake = asyncio.Event()

    async def ensure(self):
        await self.collection.update_one(
            {'_id': QUEUE_ID},
            {'$setOnInsert': {'items': [], 'lease_owner': None,
                              'lease_until': datetime(1970, 1, 1, tzinfo=timezone.utc)}},
            upsert=True,
        )

    async def enqueue(self, item):
        """Atomically admit an item only if there is capacity and no duplicate."""
        await self.ensure()
        result = await self.collection.update_one(
            {'_id': QUEUE_ID, 'items.id': {'$ne': item['id']},
             '$expr': {'$lt': [{'$size': '$items'}, QUEUE_LIMIT]}},
            {'$push': {'items': item}},
        )
        if result.modified_count:
            self.wake.set()
            return 'accepted'
        existing = await self.collection.find_one({'_id': QUEUE_ID, 'items.id': item['id']}, {'_id': 1})
        return 'duplicate' if existing else 'full'

    async def acquire(self):
        result = await self.collection.update_one(
            {'_id': QUEUE_ID, '$or': [{'lease_owner': self.owner}, {'lease_until': {'$lte': now()}}]},
            {'$set': {'lease_owner': self.owner, 'lease_until': now() + timedelta(seconds=LEASE_SECONDS)}},
        )
        return bool(result.matched_count)

    def owned(self, item_id=None):
        query = {'_id': QUEUE_ID, 'lease_owner': self.owner, 'lease_until': {'$gt': now()}}
        if item_id is not None:
            query['items.0.id'] = item_id
        return query

    async def head(self):
        doc = await self.collection.find_one(self.owned())
        if doc is None:
            raise LeaseLost()
        return doc['items'][0] if doc['items'] else None

    async def update(self, item_id, **fields):
        result = await self.collection.update_one(
            self.owned(item_id), {'$set': {f'items.0.{key}': value for key, value in fields.items()}},
        )
        if not result.matched_count:
            raise LeaseLost()

    async def remove(self, item_id):
        result = await self.collection.update_one(self.owned(item_id), {'$pop': {'items': -1}})
        if not result.matched_count:
            raise LeaseLost()

    async def release(self):
        await self.collection.update_one({'_id': QUEUE_ID, 'lease_owner': self.owner},
                                         {'$set': {'lease_owner': None, 'lease_until': now()}})

    async def heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            result = await self.collection.update_one(
                self.owned(), {'$set': {'lease_until': now() + timedelta(seconds=LEASE_SECONDS)}})
            if not result.matched_count:
                return

    async def run(self, process):
        """Process one head at a time; never hold a MongoDB lock over I/O."""
        while True:
            delay = IDLE_SECONDS
            heartbeat = None
            try:
                await self.ensure()
                if await self.acquire():
                    heartbeat = asyncio.create_task(self.heartbeat())
                    item = await self.head()
                    if item:
                        # process returns True only when a confirmed busy reply
                        # means the submission was not accepted.
                        busy = await process(self, item)
                        delay = BUSY_SECONDS if busy else 0
            except asyncio.CancelledError:
                raise
            except LeaseLost:
                logger.warning('Потеряна блокировка очереди кружков; задача будет восстановлена')
            except Exception as exc:
                logger.warning('Ошибка очереди кружков: %s', type(exc).__name__)
            finally:
                if heartbeat:
                    heartbeat.cancel()
                    result = (await asyncio.gather(heartbeat, return_exceptions=True))[0]
                    if isinstance(result, Exception):
                        logger.warning('Не удалось обновить блокировку очереди: %s', type(result).__name__)
                    try:
                        await self.release()
                    except Exception as exc:
                        logger.warning('Не удалось освободить очередь: %s', type(exc).__name__)
            if delay:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
