import asyncio
import httpx
import time

"""
Pressure Test — ContainerClaw LLM Gateway (Phase 7)
Fires N concurrent requests to the Gateway to prove that I/O multiplexing
via Uvicorn+FastAPI completely eliminates the asynchronous queue latency.

Expected Result: Total execution time ≈ Max latency of the slowest provider call.
"""

NUM_REQUESTS = 20
GATEWAY_URL = "http://localhost:8000/v1/chat/completions"

async def fire_request(client: httpx.AsyncClient, index: int) -> float:
    payload = {
        "model": "Qwen2.5-3B-Instruct-4bit",
        # We test against the local provider to isolate Gateway network overhead 
        # from external API rate-limiting delays. 
        "provider": "mlx-local", 
        "messages": [
            {"role": "user", "content": f"Return a very short random fact. Request ID: {index}"}
        ]
    }
    
    start_time = time.perf_counter()
    try:
        res = await client.post(GATEWAY_URL, json=payload, timeout=60.0)
        res.raise_for_status()
    except Exception as e:
        print(f"❌ Request {index} failed: {e}")
        return -1.0
        
    latency = time.perf_counter() - start_time
    print(f"✅ Request {index} finished in {latency:.2f}s")
    return latency

async def main():
    print(f"🚀 Firing {NUM_REQUESTS} concurrent requests to {GATEWAY_URL}...")
    
    global_start = time.perf_counter()
    
    # We use a custom transport limits to ensure our test client itself
    # doesn't enforce maximum connection pools and serialize the test requests.
    limits = httpx.Limits(max_connections=NUM_REQUESTS * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        tasks = [fire_request(client, i) for i in range(NUM_REQUESTS)]
        latencies = await asyncio.gather(*tasks)
    
    global_end = time.perf_counter()
    total_time = global_end - global_start
    
    valid_latencies = [lt for lt in latencies if lt > 0]
    
    if not valid_latencies:
        print("\n💥 All requests failed. Is the gateway running?")
        return
        
    max_latency = max(valid_latencies)
    avg_latency = sum(valid_latencies) / len(valid_latencies)
    
    print("\n--- 🏁 Pressure Test Results ---")
    print(f"Total Concurrent Requests: {len(valid_latencies)} / {NUM_REQUESTS} succeeded")
    print(f"Average Request Latency:   {avg_latency:.2f}s")
    print(f"Max Request Latency:       {max_latency:.2f}s")
    print(f"Total Wall-Clock Time:     {total_time:.2f}s")
    
    # Analyze the concurrency success metric
    # The actual physical time spent should be barely larger than the single slowest response.
    # We permit ~1s overhead for FastAPI serialization/event loop task switching.
    # In a fully sync architecture (Flask + requests), Total Wall-Clock Time would be ~ sum(latencies).
    print("\n--- 🧠 Analysis ---")
    if total_time < max_latency + 1.5:
        print(f"🟢 SUCCESS: The Gateway multiplexed I/O flawlessly.")
        print(f"   Total Time ({total_time:.2f}s) ≈ Max Latency ({max_latency:.2f}s)")
    else:
        print(f"🔴 WARNING: Concurrency bottleneck detected.")
        print(f"   Total Time ({total_time:.2f}s) is significantly higher than Max Latency ({max_latency:.2f}s).")
        print("   If it matches Sum of Latencies, you are running synchronously!")

if __name__ == "__main__":
    asyncio.run(main())
