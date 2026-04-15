import asyncio
import docker
import uuid
import os
import subprocess
from typing import AsyncGenerator, Optional, Callable, Awaitable

import config

class SandboxManager:
    """Manages execution environments (Local, Remote Sidecar, Ephemeral Sandboxes).
    
    Implements the "Awareness Continuum":
    - Native: Local execution.
    - Implicit Proxy: Proxies to a static sidecar (SWE-bench).
    - Explicit Orchestrator: Dynamic ephemeral containers.
    """

    def __init__(self):
        """Initialize SandboxManager based on central config.yaml."""
        self.mode = config.CONFIG.execution_mode
        self.sidecar_config = config.CONFIG.sidecar_config
        self._client = None
        self.default_target = self.sidecar_config.default_target_id
        self.network = self.sidecar_config.network

    @property
    def client(self):
        """Lazy-load the Docker client only when needed."""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # Rapid connectivity test
                self._client.ping()
            except Exception as e:
                # We don't crash here, as native mode might still work.
                # Errors are raised only if a tool actually needs the client.
                print(f"⚠️  [SandboxManager] Docker daemon not accessible: {e}")
                self._client = "ERROR" # Sentinel to avoid repeated ping attempts
        
        if self._client == "ERROR":
            raise RuntimeError("Docker daemon is not accessible. Check socket mounts and permissions.")
        
        return self._client

    async def execute(
        self, 
        command: str, 
        agent_id: str,
        publish_fn: Callable[[bytes], Awaitable[None]],
        image: Optional[str] = None
    ) -> tuple[int, str]:
        """Routes execution to the correct environment based on mode."""
        if self.mode == "native":
            return await self.execute_local(command, publish_fn)
        elif self.mode == "implicit_proxy":
            return await self.execute_remote(self.default_target, command, publish_fn)
        elif self.mode == "explicit_orchestrator":
            if not image:
                return await self.execute_local(command, publish_fn) # Fallback or error?
            return await self.execute_ephemeral(image, command, publish_fn)
        else:
            raise ValueError(f"Unknown execution mode: {self.mode}")

    async def execute_local(
        self, 
        command: str, 
        publish_fn: Callable[[bytes], Awaitable[None]]
    ) -> tuple[int, str]:
        """Executes a command locally and streams output via chunks."""
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=config.WORKSPACE_ROOT
        )

        output_accumulator = []
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            await publish_fn(chunk)
            output_accumulator.append(chunk.decode(errors="replace"))

        returncode = await process.wait()
        return returncode, "".join(output_accumulator)

    async def execute_remote(
        self, 
        container_id: str, 
        command: str, 
        publish_fn: Callable[[bytes], Awaitable[None]]
    ) -> tuple[int, str]:
        """Executes a command in a remote container and streams output."""
        if not container_id:
            raise RuntimeError("No target container ID provided for remote execution.")

        try:
            exec_log = self.client.api.exec_create(
                container=container_id,
                cmd=["/bin/sh", "-c", command],
                workdir=config.WORKSPACE_ROOT,
                tty=False
            )
            
            stream = self.client.api.exec_start(exec_id=exec_log['Id'], stream=True)
            
            output_accumulator = []
            queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def run_stream():
                try:
                    for chunk in stream:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except Exception as e:
                    loop.call_soon_threadsafe(queue.put_nowait, e)
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            # Start streaming in a background thread
            asyncio.create_task(asyncio.to_thread(run_stream))

            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk
                
                await publish_fn(chunk)
                output_accumulator.append(chunk.decode(errors="replace"))

            # Check exit code
            inspect = self.client.api.exec_inspect(exec_id=exec_log['Id'])
            exit_code = inspect.get('ExitCode', -1)
            
            return exit_code, "".join(output_accumulator)
        except Exception as e:
            await publish_fn(f"Error executing remote command: {e}".encode())
            return -1, str(e)

    async def execute_ephemeral(
        self, 
        image: str, 
        command: str, 
        publish_fn: Callable[[bytes], Awaitable[None]]
    ) -> tuple[int, str]:
        """Provisions an ephemeral container, runs command, and cleans up."""
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:8]}"
        
        container = await asyncio.to_thread(
            self.client.containers.run,
            image=image,
            name=sandbox_id,
            detach=True,
            network_mode=self.network,
            mem_limit="512m",
            command="sleep infinity" # Keep it alive for exec
        )
        
        try:
            return await self.execute_remote(container.id, command, publish_fn)
        finally:
            await asyncio.to_thread(container.remove, force=True)
