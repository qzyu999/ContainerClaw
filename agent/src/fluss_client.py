"""
Fluss connection client for ContainerClaw.

Encapsulates all Fluss infrastructure: connection management, table
initialization, and scanner creation. Every component that interacts
with Fluss should go through a FlussClient instance rather than
managing raw connections and admin objects directly.
"""

import asyncio
import fluss
import pyarrow as pa

from schemas import (
    CHATROOM_SCHEMA, SESSIONS_SCHEMA, BOARD_EVENTS_SCHEMA,
    DATABASE, CHATROOM_TABLE, SESSIONS_TABLE, BOARD_EVENTS_TABLE,
    DEFAULT_BUCKET_COUNT, BUCKET_KEY,
)


class FlussClient:
    """Centralized Fluss connection and table management.
    
    Usage:
        client = FlussClient(bootstrap_servers)
        await client.connect()       # Connect + create tables
        scanner = await client.create_scanner(client.chat_table)
        scanner = await client.create_scanner(client.chat_table, start_ts=ts)
    """

    def __init__(self, bootstrap_servers: str):
        self.bootstrap_servers = bootstrap_servers
        self.conn = None
        self.admin = None
        self.chat_table = None
        self.sessions_table = None
        self.board_table = None

    async def connect(self, max_attempts: int = 30, retry_delay: float = 3.0):
        """Connect to Fluss and initialize all tables.

        Retries up to max_attempts times with retry_delay between attempts.
        Raises Exception if all attempts fail.
        """
        print("🛰️ Initializing Fluss Infrastructure...")
        fluss_config = fluss.Config({"bootstrap.servers": self.bootstrap_servers})

        for attempt in range(max_attempts):
            try:
                self.conn = await fluss.FlussConnection.create(fluss_config)
                print("✅ Connected to Fluss.")
                self.admin = await self.conn.get_admin()

                # Create database
                await self.admin.create_database(DATABASE, ignore_if_exists=True)

                # Create tables
                self.chat_table = await self._ensure_table(
                    CHATROOM_TABLE, CHATROOM_SCHEMA
                )
                self.sessions_table = await self._ensure_table(
                    SESSIONS_TABLE, SESSIONS_SCHEMA
                )
                self.board_table = await self._ensure_table(
                    BOARD_EVENTS_TABLE, BOARD_EVENTS_SCHEMA
                )

                print("🚀 All Fluss tables connected and ready.")
                return

            except Exception as e:
                print(f"⏳ Fluss initialization failed (attempt {attempt + 1}/{max_attempts}): {e}")
                await asyncio.sleep(retry_delay)

        raise Exception(f"❌ Failed to initialize Fluss after {max_attempts} attempts.")

    async def _ensure_table(self, table_name: str, schema: pa.Schema):
        """Create a table if it doesn't exist, then return a table handle."""
        table_path = fluss.TablePath(DATABASE, table_name)
        descriptor = fluss.TableDescriptor(
            fluss.Schema(schema),
            bucket_keys=BUCKET_KEY,
            bucket_count=DEFAULT_BUCKET_COUNT,
        )
        await self.admin.create_table(table_path, descriptor, ignore_if_exists=True)
        return await self.conn.get_table(table_path)

    async def create_scanner(self, table, start_ts: int | None = None):
        """Create a batch log scanner with dynamic bucket discovery.
        
        Args:
            table: Fluss table handle (e.g., self.chat_table)
            start_ts: Optional millisecond timestamp to seek from.
                       If None, subscribes from the beginning (offset 0).
        
        Returns:
            A record-batch log scanner ready to poll.
        """
        scanner = await table.new_scan().create_record_batch_log_scanner()
        table_path = table.get_table_path()
        table_info = await self.admin.get_table_info(table_path)
        num_buckets = table_info.num_buckets

        if start_ts and start_ts > 0:
            # Seek to timestamp: get per-bucket offsets at or after start_ts
            offsets = await self.admin.list_offsets(
                table_path,
                list(range(num_buckets)),
                fluss.OffsetSpec.timestamp(start_ts),
            )
            scanner.subscribe_buckets(offsets)
        else:
            # Subscribe from the beginning
            scanner.subscribe_buckets(
                {b: fluss.EARLIEST_OFFSET for b in range(num_buckets)}
            )

        return scanner

    @staticmethod
    async def poll_async(scanner, timeout_ms: int = 500):
        """Perform a single async poll, returning a list of pyarrow RecordBatch.

        Uses the Rust-native _async_poll_batches() which integrates directly
        with Python's event loop via tokio — no asyncio.to_thread() needed.

        The Fluss SDK returns its own RecordBatch wrapper objects; we unwrap
        them via .batch to get standard pyarrow RecordBatch instances.

        Returns:
            list[pa.RecordBatch]: May be empty (timeout, not end-of-stream).
        """
        batches = await scanner._async_poll_batches(timeout_ms)
        if not batches:
            return []
        # Unwrap Fluss RecordBatch → pyarrow RecordBatch
        return [b.batch for b in batches]

