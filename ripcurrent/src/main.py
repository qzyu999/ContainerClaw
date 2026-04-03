import asyncio
import os
import json
import time
import aiohttp
import discord
from discord.ext import commands
import fluss
import pyarrow as pa
from datetime import datetime, timezone
from fluss_helpers import CHATROOM_SCHEMA, poll_batches

import sys
from pathlib import Path

# Add parent of shared/ to the Python path so it can be imported as a package
shared_path = os.getenv("SHARED_MODULE_PATH", "/app/shared")
sys.path.insert(0, os.path.dirname(shared_path))

from shared.config_loader import load_config

cfg = load_config()

# Load credentials
DISCORD_BOT_TOKEN = cfg.discord_bot_token
DISCORD_WEBHOOK_URL = cfg.discord_webhook_url
DISCORD_CHANNEL_ID = cfg.discord_channel_id
FLUSS_BOOTSTRAP_SERVERS = cfg.fluss_bootstrap_servers
SESSION_ID = cfg.session_id

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True

class DiscordConnector:
    def __init__(self):
        self.fluss_conn = None
        self.admin = None
        self.chat_table = None
        self.sessions_table = None
        self.session_id = SESSION_ID # Initial default
        self.session = None # aiohttp session for webhooks

    async def init_fluss(self):
        """Connect to Fluss with retry. Tables may not exist yet if the agent
        hasn't started — retry get_table separately from connection."""
        print("🛰️ Connecting to Fluss...")
        fluss_config = fluss.Config({"bootstrap.servers": FLUSS_BOOTSTRAP_SERVERS})

        # Phase 1: Establish connection
        for attempt in range(30):
            try:
                self.fluss_conn = await fluss.FlussConnection.create(fluss_config)
                self.admin = await self.fluss_conn.get_admin()
                print("✅ Connected to Fluss.")
                break
            except Exception as e:
                print(f"⏳ Fluss connection failed (attempt {attempt+1}/30): {e}")
                await asyncio.sleep(3)
        else:
            raise Exception("❌ Failed to connect to Fluss")

        # Phase 2: Wait for tables (agent creates them)
        chat_path = fluss.TablePath("containerclaw", "chatroom")
        sessions_path = fluss.TablePath("containerclaw", "sessions")
        for attempt in range(30):
            try:
                self.chat_table = await self.fluss_conn.get_table(chat_path)
                self.sessions_table = await self.fluss_conn.get_table(sessions_path)
                print("✅ Fluss tables ready.")
                return
            except Exception as e:
                print(f"⏳ Waiting for tables (attempt {attempt+1}/30): {e}")
                await asyncio.sleep(3)
        raise Exception("❌ Tables not created after 90s — is claw-agent running?")

    async def _get_num_buckets(self, table):
        """Dynamic bucket discovery via admin API."""
        table_info = await self.admin.get_table_info(table.get_table_path())
        return table_info.num_buckets

    async def session_discovery_worker(self):
        """Periodically polls the sessions table to find the latest active session ID."""
        print("🔍 Starting Session Discovery Worker...")
        num_buckets = await self._get_num_buckets(self.sessions_table)
        scanner = await self.sessions_table.new_scan().create_record_batch_log_scanner()
        scanner.subscribe_buckets({b: 0 for b in range(num_buckets)})
            
        while True:
            try:
                batches = await poll_batches(scanner, timeout_ms=500)
                if batches:
                    for poll in batches:
                        if poll.num_rows == 0: continue
                        id_arr = poll["session_id"]
                        created_arr = poll["created_at"]
                        
                        latest_ts = 0
                        latest_id = self.session_id
                        
                        for i in range(poll.num_rows):
                            ts = int(created_arr[i].as_py())
                            if ts > latest_ts:
                                latest_ts = ts
                                latest_id = str(id_arr[i].as_py())
                        
                        if latest_id != self.session_id:
                            print(f"🎯 Discord Bot switched to latest session: {latest_id}")
                            self.session_id = latest_id
                
            except Exception as e:
                print(f"⚠️ Session discovery error: {e}")
            
            await asyncio.sleep(10)

    async def start_egress_worker(self):
        """Tails Fluss and pushes messages to Discord Webhook."""
        print("🌊 Starting Egress Worker (Fluss -> Discord)...")
        scanner = await self.chat_table.new_scan().create_record_batch_log_scanner()
        
        # Tail from the current end of the log
        num_buckets = await self._get_num_buckets(self.chat_table)
        offsets = None
        for attempt in range(10):
            try:
                offsets = await self.admin.list_offsets(
                    self.chat_table.get_table_path(),
                    list(range(num_buckets)),
                    fluss.OffsetSpec.latest()
                )
                break
            except Exception as e:
                print(f"⏳ Fluss metadata delay (attempt {attempt+1}/10): {e}")
                await asyncio.sleep(3)
        
        if not offsets:
            raise Exception("❌ Failed to reach the end of the Fluss log")

        scanner.subscribe_buckets(offsets)

        async with aiohttp.ClientSession() as session:
            self.session = session
            while True:
                batches = await poll_batches(scanner, timeout_ms=500)
                if not batches:
                    await asyncio.sleep(0.1)
                    continue

                for poll in batches:
                    for i in range(poll.num_rows):
                        row = {col: poll[col][i].as_py() for col in poll.schema.names}
                        
                        # Filter: Only messages for our session
                        if row.get("session_id") != self.session_id:
                            continue
                        
                        # Filter: Prevent loops - ignore messages that originated from Discord
                        if str(row.get("actor_id")).startswith("Discord/"):
                            continue
                        
                        # Filter: Ignore internal system messages without content
                        if not row.get("content"):
                            continue

                        # Execute Webhook
                        await self.send_to_discord(row)

    async def send_to_discord(self, row):
        """Sends a message to Discord via Webhook with actor impersonation."""
        actor_id = row.get("actor_id")
        content = row.get("content")
        msg_type = row.get("type", "output")
        
        # Format the username to show if it's from the Web UI
        username = actor_id
        if actor_id == "Human":
            username = f"{actor_id} [Web]"
        
        payload = {
            "content": content,
            "username": username,
        }

        # Add visual distinction for different types of messages
        if msg_type == "thought":
            payload["content"] = f"*Thought: {content}*"
        elif msg_type == "action":
            # Tool actions can be a bit noisy, maybe format as code block or embed
            if content.startswith("$"):
                payload["content"] = f"```bash\n{content}\n```"
            else:
                # Truncate large tool outputs
                if len(content) > 1500:
                    content = content[:1500] + "\n... (truncated)"
                payload["content"] = f"```\n{content}\n```"

        try:
            async with self.session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
                if resp.status != 204:
                    print(f"⚠️ Webhook error {resp.status}: {await resp.text()}")
        except Exception as e:
            print(f"❌ Failed to send to Discord: {e}")

    async def push_to_fluss(self, actor_id, content):
        """Writes a message from Discord into the Fluss chatroom."""
        import uuid
        event_id = str(uuid.uuid4())
        ts = int(time.time() * 1000)
        # We prefix the actor_id with "Discord/" to identify the source across the bridge
        # and use type="user" so the UI renders it immediately in the main chat tab
        discord_actor = f"Discord/{actor_id}"
        batch = pa.RecordBatch.from_arrays([
            pa.array([event_id], type=pa.string()),
            pa.array([self.session_id], type=pa.string()),
            pa.array([ts], pa.int64()),
            pa.array([discord_actor], pa.string()),
            pa.array([content], pa.string()),
            pa.array(["user"], pa.string()),
            pa.array([""], pa.string()),
            pa.array([False], pa.bool_()),
            pa.array([""], pa.string()),
            pa.array([""], pa.string()),
            pa.array(["ROOT"], pa.string()),
        ], schema=CHATROOM_SCHEMA)
        
        writer = self.chat_table.new_append().create_writer()
        writer.write_arrow_batch(batch)
        if hasattr(writer, "flush"):
            await writer.flush()

connector = DiscordConnector()

class DiscordBot(commands.Bot):
    async def setup_hook(self):
        """Called once when the bot is ready to connect."""
        await connector.init_fluss()
        self.loop.create_task(connector.session_discovery_worker())
        self.loop.create_task(connector.start_egress_worker())

bot = DiscordBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🤖 Discord Bot Logged in as {bot.user}")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself or webhooks (to prevent loops)
    if message.author == bot.user or message.webhook_id is not None:
        return
    
    # Only listen to the configured channel
    if str(message.channel.id) != DISCORD_CHANNEL_ID:
        return

    print(f"📥 Discord -> Fluss: {message.author.name}: {message.content}")
    await connector.push_to_fluss(message.author.name, message.content)

def main():
    if not DISCORD_BOT_TOKEN:
        print("❌ CRITICAL: DISCORD_BOT_TOKEN is missing!")
        return

    print("🚀 Starting Discord Bot service...")
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
