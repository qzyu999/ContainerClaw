import sys
import os
import asyncio
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "agent" / "src"))
sys.path.append(str(Path(__file__).parent.parent))

from fluss_client import FlussClient
import config

async def main():
    bootstrap = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "localhost:9123")
    client = FlussClient(bootstrap)
    try:
        await client.connect()
        session_id = "test-session-anchor-" + os.urandom(4).hex()
        anchor_text = "Keep it short and sweet."
        
        print(f"Setting anchor for {session_id}...")
        success = await client.set_anchor(session_id, anchor_text)
        if not success:
            print("Failed to set anchor.")
            return

        print("Fetching anchor...")
        fetched = await client.fetch_latest_anchor(session_id)
        print(f"Fetched anchor: '{fetched}'")

        if fetched == anchor_text:
            print("✅ SUCCESS: Anchor match.")
        else:
            print(f"❌ FAILURE: Expected '{anchor_text}', got '{fetched}'")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
