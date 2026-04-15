import asyncio
import os
import sys
from pathlib import Path

# Add core paths
sys.path.append(str(Path.cwd() / "agent/src"))
sys.path.append(str(Path.cwd()))

import config
from sandbox import SandboxManager

async def smoke_test():
    print("🔬 [SmokeTest] Initializing SandboxManager...")
    sm = SandboxManager()
    
    # Use a locally available image
    target_id = "smoke-test-sidecar"
    image = "nginx:alpine"
    
    print(f"🐳 [SmokeTest] Provisioning sandbox: {target_id} (Image: {image})...")
    try:
        import docker
        client = docker.from_env()
        # Clean up old one
        try:
            client.containers.get(target_id).remove(force=True)
        except:
            pass
            
        # skip pull to avoid network issues
        # client.images.pull(image)
        
        container = client.containers.run(
            image,
            name=target_id,
            command="sleep infinity",
            detach=True
        )
        print(f"✅ [SmokeTest] Sandbox online: {container.id[:12]}")
        
        # Test Streaming Execution
        print(f"📡 [SmokeTest] Testing streaming execution: 'echo Hello from Sidecar!'")
        
        async def publish_chunk(chunk: bytes):
            print(f"🟢 [Telemetry] {chunk.decode().strip()}")

        exit_code, output = await sm.execute_remote(
            container_id=target_id,
            command="echo Hello from Sidecar!",
            publish_fn=publish_chunk
        )
        
        print(f"🏁 [SmokeTest] Final Result: {'✅' if exit_code == 0 else '❌'}")
        print(f"   Output: {output}")
        
        # Cleanup
        container.remove(force=True)
        print("🧹 [SmokeTest] Cleanup complete.")
        
    except Exception as e:
        print(f"❌ [SmokeTest] Failed: {e}")

if __name__ == "__main__":
    # Ensure CLAW_CONFIG_PATH is set
    if "CLAW_CONFIG_PATH" not in os.environ:
        os.environ["CLAW_CONFIG_PATH"] = "config.yaml"
        
    asyncio.run(smoke_test())
