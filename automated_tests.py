"""
Run all 13 investigation test cases sequentially against the AegisMesh Gateway.
Outputs results to the console.
"""
import asyncio
import httpx
import time

TEST_CASES = [
    # Stage 1 — Light Sanity Tests
    {
        "id": "Test 1 (RAM pressure)",
        "query": "My computer has been extremely slow today and I suspect my RAM might be overloaded. Can you check if memory usage is causing the slowdown?"
    },
    {
        "id": "Test 2 (Disk pressure)",
        "query": "My PC feels sluggish and I think my disk might be almost full. Can you check my disk space and see if that's the cause?"
    },
    {
        "id": "Test 3 (Network connectivity)",
        "query": "My applications are having trouble connecting to the internet. Can you check the network ports and adapters on my system to see if something is wrong?"
    },
    # Stage 2 — Medium Complexity
    {
        "id": "Test 4 (Ambiguous system issue)",
        "query": "Something feels wrong with my computer lately. It just doesn't feel as responsive as usual. Can you investigate what might be going on?"
    },
    {
        "id": "Test 5 (Contradictory observation)",
        "query": "My computer feels slow, but when I check Task Manager the CPU usage seems normal. Can you analyze what might be causing the slowdown?"
    },
    # Stage 3 — Realistic Diagnostic Scenarios
    {
        "id": "Test 6 (CPU spike investigation)",
        "query": "My computer suddenly became very slow a few minutes ago. It feels like something might be using too much CPU. Can you investigate?"
    },
    {
        "id": "Test 7 (Network socket investigation)",
        "query": "Some of my applications are behaving strangely and I suspect there might be too many open network connections on my system. Can you check the socket usage and listening ports?"
    },
    {
        "id": "Test 8 (Application crash analysis)",
        "query": "One of my programs crashed earlier today and I'm not sure why. Can you check the Windows event logs to see if there are any relevant errors or crash reports?"
    },
    # Stage 4 — Stress Tests
    {
        "id": "Test 9 (Multiple system checks)",
        "query": "My system has been unstable today. Please run a full diagnostic check on CPU usage, RAM usage, disk space, and network connections to see if anything looks abnormal."
    },
    {
        "id": "Test 10 (Dependency failure simulation)",
        "query": "I think there might be issues affecting multiple parts of my system at once. Can you analyze the health of my system resources, event logs, and network stack to identify any failures?"
    },
    # Stage 5 — Adversarial Tests
    {
        "id": "Test 11 (Misleading assumption)",
        "query": "My computer is running slow and I think it's because the CPU might be overheating or overloaded. Can you confirm if the CPU is actually the cause?"
    },
    {
        "id": "Test 12 (Nonsense / edge case query)",
        "query": "My computer feels strange today and I can't really explain why. Can you investigate and tell me if anything unusual is happening with my system?"
    },
    # Stage 6 — Full System Integration Test
    {
        "id": "Test 13 (Real-world investigation)",
        "query": "My PC has been slow for about the past hour and my browser keeps freezing occasionally. Can you investigate the system and identify the likely root cause?"
    }
]

async def run_test(client, test_case):
    print(f"\n{'='*70}")
    print(f"▶ RUNNING: {test_case['id']}")
    print(f"Query: {test_case['query']}")
    print("-" * 70)
    
    start_time = time.time()
    try:
        resp = await client.post(
            "http://127.0.0.1:9000/investigate",
            json={"query": test_case['query']}
        )
        resp.raise_for_status()
        data = resp.json()
        
        duration = time.time() - start_time
        llm_used = duration > 5.0  # Safe heuristic: if it took > 5s, the LLM was almost certainly used
        
        print(f"✓ Status     : {resp.status_code}")
        print(f"✓ Trace ID   : {data.get('trace_id')}")
        print(f"✓ Duration   : {data.get('duration_seconds')}s (LLM used: {'YES' if llm_used else 'NO'})")
        print(f"✓ Confidence : {data.get('confidence_score', 0):.0%}")
        print(f"✓ Agents     : {data.get('agents_dispatched')}")
        print(f"✓ Facts      : {data.get('facts_collected')}")
        print(f"\n-- DIAGNOSTIC REPORT --\n{data.get('report')[:300]}...\n")
        
    except Exception as e:
        print(f"✗ ERROR: {e}")

async def main():
    print("Checking Gateway health...")
    async with httpx.AsyncClient(timeout=5.0) as check:
        try:
            r = await check.get("http://127.0.0.1:9000/health")
            print(f"Gateway is UP. Starting test suite.\n")
        except Exception:
            print("Gateway is DOWN. Start the bootstrapper first.")
            return

    # 300s timeout per request for safety with LLM calls
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        for t in TEST_CASES:
            await run_test(client, t)
            # Brief pause between investigations to let sockets clear
            await asyncio.sleep(2.0)
            
    print(f"\n{'='*70}\nALL 13 TESTS COMPLETE.\n{'='*70}")

if __name__ == "__main__":
    asyncio.run(main())
