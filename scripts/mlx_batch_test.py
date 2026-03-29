import asyncio
import time
from openai import AsyncOpenAI

client = AsyncOpenAI(base_url="http://127.0.0.1:8080/v1", api_key="mlx-is-cool")

# Prompts of roughly similar length to keep generation times relatively even
prompts = [
    "Write a 100-word story about a space cat.",
    "Write a 100-word story about a space dog.",
    "Write a 100-word story about a space bird."
]

async def get_response(prompt: str) -> float:
    start = time.time()
    await client.chat.completions.create(
        model="Qwen2.5-3B-Instruct-4bit",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150 # Cap tokens so the test runs quickly
    )
    return time.time() - start

async def run_test():
    print("--- 1. Testing Sequential Execution ---")
    seq_start = time.time()
    for p in prompts:
        dur = await get_response(p)
        print(f"Single request time: {dur:.2f}s")
    seq_total = time.time() - seq_start
    print(f"Total Sequential Time: {seq_total:.2f}s\n")

    print("--- 2. Testing Concurrent Execution ---")
    conc_start = time.time()
    tasks = [get_response(p) for p in prompts]
    results = await asyncio.gather(*tasks)
    
    for i, dur in enumerate(results):
         print(f"Concurrent request {i+1} time: {dur:.2f}s")
         
    conc_total = time.time() - conc_start
    print(f"Total Concurrent Time: {conc_total:.2f}s\n")

    print("--- VERDICT ---")
    # If concurrent time is significantly faster than sequential time
    if conc_total < (seq_total * 0.75):
        print("✅ Batching is WORKING! The server processed the requests together.")
    else:
        print("❌ The server processed them SEQUENTIALLY. Requests were just queued.")

if __name__ == "__main__":
    asyncio.run(run_test())