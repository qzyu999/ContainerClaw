"""
Fluss connection client for ContainerClaw.

Encapsulates all Fluss infrastructure: connection management, table
initialization, and scanner creation. Every component that interacts
with Fluss should go through a FlussClient instance rather than
managing raw connections and admin objects directly.
"""

import asyncio
import time
import uuid
import fluss
import pyarrow as pa

from schemas import (
    CHATROOM_SCHEMA, SESSIONS_SCHEMA, BOARD_EVENTS_SCHEMA,
    AGENT_STATUS_SCHEMA, ANCHOR_MESSAGE_SCHEMA,
    DATABASE, CHATROOM_TABLE, SESSIONS_TABLE, BOARD_EVENTS_TABLE,
    AGENT_STATUS_TABLE, ANCHOR_MESSAGE_TABLE,
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
        self.status_table = None
        self.anchor_table = None

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
                self.status_table = await self._ensure_table(
                    AGENT_STATUS_TABLE, AGENT_STATUS_SCHEMA
                )
                self.anchor_table = await self._ensure_table(
                    ANCHOR_MESSAGE_TABLE, ANCHOR_MESSAGE_SCHEMA
                )

                print("🚀 All Fluss tables connected and ready.")
                return

            except Exception as e:
                print(f"⏳ Fluss initialization failed (attempt {attempt + 1}/{max_attempts}): {e}")
                await asyncio.sleep(retry_delay)

        raise Exception(f"❌ Failed to initialize Fluss after {max_attempts} attempts.")

    async def _ensure_table(self, table_name: str, schema: pa.Schema,
                             primary_keys: list[str] | None = None):
        """Create a table if it doesn't exist, then return a table handle.

        Args:
            table_name: Fluss table name.
            schema: PyArrow schema for the table.
            primary_keys: Optional list of column names for PK table semantics.
                          If None, creates a log (append-only) table.
        """
        table_path = fluss.TablePath(DATABASE, table_name)
        if primary_keys:
            fluss_schema = fluss.Schema(schema, primary_keys=primary_keys)
        else:
            fluss_schema = fluss.Schema(schema)
        descriptor = fluss.TableDescriptor(
            fluss_schema,
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
        try:
            scanner = await table.new_scan().create_record_batch_log_scanner()
            table_path = table.get_table_path()
            table_info = await self.admin.get_table_info(table_path)
            num_buckets = table_info.num_buckets

            if start_ts and start_ts > 0:
                offsets = await self.admin.list_offsets(
                    table_path,
                    list(range(num_buckets)),
                    fluss.OffsetSpec.timestamp(start_ts),
                )
                scanner.subscribe_buckets(offsets)
            else:
                scanner.subscribe_buckets(
                    {b: fluss.EARLIEST_OFFSET for b in range(num_buckets)}
                )

            return scanner
        except Exception as e:
            # SELF-HEALING: If the connection was poisoned by a previous cancellation,
            # trash the connection, reconnect, and try again seamlessly.
            if "poisoned" in str(e).lower() or "unexpectedeof" in str(e).lower():
                print("♻️ [FlussClient] Poisoned connection detected. Self-healing...")
                await self.connect()
                
                # Fetch a fresh table reference from the new connection
                fresh_table = await self.conn.get_table(table.get_table_path())
                return await self.create_scanner(fresh_table, start_ts)
            raise e

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
        try:
            # SHIELD THE RUST FUTURE: Because _async_poll_batches is a Rust function, 
            # it returns a Future, not a coroutine. We use ensure_future to safely wrap it.
            future = asyncio.ensure_future(scanner._async_poll_batches(timeout_ms))
            batches = await asyncio.shield(future)
            
            if not batches:
                return []
            return [b.batch for b in batches]
        except asyncio.CancelledError:
            # Python task aborts immediately, but Rust task finishes safely behind the scenes
            raise

    # ── Session CRUD ────────────────────────────────────────────────

    async def create_session(self, session_id: str, title: str) -> dict:
        """Create a new session record in the sessions log table.

        Returns:
            dict with session_id, title, created_at, last_active_at.
        """
        now = int(time.time() * 1000)
        batch = pa.RecordBatch.from_arrays([
            pa.array([session_id], type=pa.string()),
            pa.array([title], type=pa.string()),
            pa.array([now], type=pa.int64()),
            pa.array([now], type=pa.int64()),
        ], schema=SESSIONS_SCHEMA)

        writer = self.sessions_table.new_append().create_writer()
        writer.write_arrow_batch(batch)
        if hasattr(writer, "flush"):
            await writer.flush()
        print(f"✅ [FlussClient] Session {session_id} created.")

        return {
            "session_id": session_id,
            "title": title,
            "created_at": now,
            "last_active_at": now,
        }

    async def list_sessions(self) -> list[dict]:
        """Scan the sessions log and return deduplicated session list.

        Returns:
            list[dict] sorted by last_active_at descending. Each dict has
            session_id, title, created_at, last_active_at.
        """
        try:
            scanner = await self.create_scanner(self.sessions_table)
            sessions_dict = {}
            empty_polls = 0
            while empty_polls < 5:
                batches = await self.poll_async(scanner, timeout_ms=500)
                if not batches:
                    empty_polls += 1
                    continue
                empty_polls = 0
                for poll in batches:
                    id_arr = poll["session_id"]
                    title_arr = poll["title"]
                    created_arr = poll["created_at"]
                    active_arr = poll["last_active_at"]
                    for i in range(poll.num_rows):
                        sid = id_arr[i].as_py()
                        sessions_dict[sid] = {
                            "session_id": sid,
                            "title": title_arr[i].as_py(),
                            "created_at": int(created_arr[i].as_py()),
                            "last_active_at": int(active_arr[i].as_py()),
                        }
            return sorted(
                sessions_dict.values(),
                key=lambda s: s["last_active_at"],
                reverse=True,
            )
        except Exception as e:
            if "poisoned" in str(e).lower() or "unexpectedeof" in str(e).lower():
                print("♻️ [FlussClient] Poisoned connection in list_sessions. Self-healing...")
                await self.connect()
                return await self.list_sessions()
            raise e

    async def fetch_history(self, session_id: str) -> list[dict]:
        """Fetch full chat history for a session from the chatroom log.

        Performs an optimized seek if the session start time is found in
        the sessions table.

        Returns:
            list[dict] sorted by ts. Each dict has ts, actor_id, content, type.
        """
        # 1. Lookup session start time
        start_ts = 0
        try:
            sessions = await self.list_sessions()
            for s in sessions:
                if s["session_id"] == session_id:
                    start_ts = s["created_at"]
                    break
        except Exception as e:
            print(f"⚠️ [FlussClient] Session lookup failed: {e}")

        # 2. Scan chatroom with optional seek
        scanner = await self.create_scanner(
            self.chat_table, start_ts=start_ts if start_ts > 0 else None
        )

        events = []
        empty_polls = 0
        while empty_polls < 10:
            batches = await self.poll_async(scanner, timeout_ms=500)
            if not batches:
                empty_polls += 1
                continue
            empty_polls = 0
            for poll in batches:
                session_arr = poll.column("session_id")
                ts_arr = poll.column("ts")
                actor_arr = poll.column("actor_id")
                content_arr = poll.column("content")
                for i in range(poll.num_rows):
                    if session_arr[i].as_py() != session_id:
                        continue
                    ts_ms = ts_arr[i].as_py()
                    actor_id = actor_arr[i].as_py()
                    content = content_arr[i].as_py()
                    if isinstance(actor_id, bytes):
                        actor_id = actor_id.decode("utf-8")
                    if isinstance(content, bytes):
                        content = content.decode("utf-8")
                    try:
                        e_type = poll.column("type")[i].as_py()
                        if isinstance(e_type, bytes):
                            e_type = e_type.decode("utf-8")
                    except (KeyError, ValueError, IndexError):
                        e_type = "thought" if actor_id == "Moderator" else "output"
                    events.append({
                        "ts": ts_ms,
                        "actor_id": actor_id,
                        "content": content,
                        "type": e_type,
                    })

        events.sort(key=lambda x: x["ts"])
        return events

    async def fetch_latest_anchor(self, session_id: str) -> str:
        """Return the content of the most recent anchor_message for a session.
        
        Returns empty string if no anchor has been set.
        """
        scanner = await self.create_scanner(self.anchor_table)
        latest_ts = -1
        latest_content = ""
        empty_polls = 0
        while empty_polls < 5:
            batches = await self.poll_async(scanner, timeout_ms=500)
            if not batches:
                empty_polls += 1
                continue
            empty_polls = 0
            for batch in batches:
                sid_arr = batch["session_id"]
                ts_arr = batch["ts"]
                content_arr = batch["content"]
                for i in range(batch.num_rows):
                    if sid_arr[i].as_py() != session_id:
                        continue
                    ts = ts_arr[i].as_py()
                    if ts > latest_ts:
                        latest_ts = ts
                        content = content_arr[i].as_py()
                        latest_content = content.decode("utf-8") if isinstance(content, bytes) else str(content)
    async def set_anchor(self, session_id: str, content: str) -> bool:
        """Write a new steering anchor message to the anchor_table.
        
        Returns:
            True if successful.
        """
        now = int(time.time() * 1000)
        batch = pa.RecordBatch.from_arrays([
            pa.array([session_id], type=pa.string()),
            pa.array([now], type=pa.int64()),
            pa.array([content], type=pa.string()),
            pa.array(["System"], type=pa.string()),
        ], schema=ANCHOR_MESSAGE_SCHEMA)

        try:
            writer = self.anchor_table.new_append().create_writer()
            writer.write_arrow_batch(batch)
            if hasattr(writer, "flush"):
                await writer.flush()
            print(f"⚓ [FlussClient] Anchor set for session {session_id}.")
            return True
        except Exception as e:
            print(f"❌ [FlussClient] Failed to set anchor: {e}")
            return False
