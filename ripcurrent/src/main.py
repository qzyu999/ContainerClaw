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

# Load keys from Docker Secrets
def get_secret(name):
    try:
        with open(f"/run/secrets/{name}", "r") as f:
            return f.read().strip()
    except Exception:
        return None

# Load credentials
DISCORD_BOT_TOKEN = get_secret("discord_bot_token") or os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WEBHOOK_URL = get_secret("discord_webhook_url") or os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_CHANNEL_ID = get_secret("discord_channel_id") or os.getenv("DISCORD_CHANNEL_ID")
FLUSS_BOOTSTRAP_SERVERS = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "coordinator-server:9123")
SESSION_ID = os.getenv("CLAW_SESSION_ID", "user-session")

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True

class DiscordConnector:
    def __init__(self):
        self.fluss_conn = None
        self.chat_table = None
        self.sessions_table = None
        self.session_id = SESSION_ID # Initial default
        self.session = None # aiohttp session for webhooks

    async def init_fluss(self):
        print("🛰️ Connecting to Fluss...")
        fluss_config = fluss.Config({"bootstrap.servers": FLUSS_BOOTSTRAP_SERVERS})
        for attempt in range(30):
            try:
                self.fluss_conn = await fluss.FlussConnection.create(fluss_config)
                
                # Chat table
                table_path = fluss.TablePath("containerclaw", "chatroom")
                self.chat_table = await self.fluss_conn.get_table(table_path)
                
                # Sessions table for discovery
                sessions_path = fluss.TablePath("containerclaw", "sessions")
                self.sessions_table = await self.fluss_conn.get_table(sessions_path)
                
                print("✅ Connected to Fluss.")
                return
            except Exception as e:
                print(f"⏳ Fluss connection failed (attempt {attempt+1}/30): {e}")
                await asyncio.sleep(3)
        raise Exception("❌ Failed to connect to Fluss")

    async def session_discovery_worker(self):
        """Periodically polls the sessions table to find the latest active session ID."""
        print("🔍 Starting Session Discovery Worker...")
        scanner = await self.sessions_table.new_scan().create_record_batch_log_scanner()
        for b in range(16):
            scanner.subscribe(bucket_id=b, start_offset=0)
            
        while True:
            try:
                poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
                if poll.num_rows > 0:
                    # Find the session with the largest created_at
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
        admin = await self.fluss_conn.get_admin()
        offsets = None
        for attempt in range(10):
            try:
                offsets = await admin.list_offsets(
                    self.chat_table.get_table_path(),
                    list(range(16)),
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
                poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
                if poll.num_rows == 0:
                    await asyncio.sleep(0.1)
                    continue

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
        ts = int(time.time() * 1000)
        # We prefix the actor_id with "Discord/" to identify the source across the bridge
        # and use type="user" so the UI renders it immediately in the main chat tab
        discord_actor = f"Discord/{actor_id}"
        batch = pa.RecordBatch.from_arrays([
            pa.array([self.session_id], type=pa.string()),
            pa.array([ts], pa.int64()),
            pa.array([discord_actor], pa.string()),
            pa.array([content], pa.string()),
            pa.array(["user"], pa.string()), # Use 'user' type for UI visibility
            pa.array([""], pa.string()),
            pa.array([False], pa.bool_()),
            pa.array([""], pa.string()),
        ], schema=pa.schema([
            pa.field("session_id", pa.string()),
            pa.field("ts", pa.int64()),
            pa.field("actor_id", pa.string()),
            pa.field("content", pa.string()),
            pa.field("type", pa.string()),
            pa.field("tool_name", pa.string()),
            pa.field("tool_success", pa.bool_()),
            pa.field("parent_actor", pa.string()),
        ]))
        
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
