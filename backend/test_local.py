import sys
import os
import json

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure backend directory is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

print("=" * 60)
print("1. TESTING RAG PIPELINE")
print("=" * 60)

try:
    from RAG_Pipeline import rag_pipeline
    
    print("\n[RAG] Testing general search for 'beach sunset':")
    results = rag_pipeline.search("beach sunset", n_results=3)
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['metadata']['name']} (Type: {r['metadata']['type']}, Category: {r['metadata']['category']})")
        print(f"     Timings: {r['metadata']['timings']}")
        print(f"     Distance: {r['distance']:.4f}")
    
    print("\n[RAG] Testing filtered search for 'rainy indoor activity' (filter: type='indoor'):")
    indoor_results = rag_pipeline.search("rainy indoor activity museum church", n_results=3, filter_conditions={"type": "indoor"})
    for i, r in enumerate(indoor_results, 1):
        print(f"  {i}. {r['metadata']['name']} (Type: {r['metadata']['type']}, Category: {r['metadata']['category']})")
    
    print("\n>>> RAG Pipeline Test PASSED!")
except Exception as e:
    print(f"\n>>> RAG Pipeline Test FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("2. TESTING FASTAPI BACKEND ENDPOINTS (TestClient)")
print("=" * 60)

try:
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # 1. Health check
    print("\n[API] Testing GET /api/health:")
    health_resp = client.get("/api/health")
    print(f"  Status code: {health_resp.status_code}")
    print(f"  Response: {health_resp.json()}")
    assert health_resp.status_code == 200

    # 2. Jobs endpoint
    print("\n[API] Testing GET /api/jobs:")
    jobs_resp = client.get("/api/jobs")
    print(f"  Status code: {jobs_resp.status_code}")
    print(f"  Response: {jobs_resp.json()}")
    assert jobs_resp.status_code == 200

    # 3. Trigger alert endpoint
    print("\n[API] Testing POST /api/trigger-alert (scenario: rain_approaching):")
    alert_resp = client.post("/api/trigger-alert", json={"scenario": "rain_approaching", "chat_id": 999999})
    print(f"  Status code: {alert_resp.status_code}")
    print(f"  Response: {alert_resp.json()}")
    assert alert_resp.status_code == 200

    # 4. Chat endpoint
    print("\n[API] Testing POST /api/chat (query: 'best beach to visit in North Goa'):")
    chat_resp = client.post("/api/chat", json={"query": "best beach to visit in North Goa"})
    print(f"  Status code: {chat_resp.status_code}")
    chat_data = chat_resp.json()
    print(f"  Matched locations count: {len(chat_data.get('matched_locations', []))}")
    for loc in chat_data.get("matched_locations", []):
        print(f"    - {loc['name']} ({loc['category']}) [Score: {loc['score']:.4f}]")
    print(f"  Latency: {chat_data.get('latency_ms')} ms")
    print(f"  LLM Response snippet: {chat_data.get('llm_response')[:120]}...")
    assert chat_resp.status_code == 200

    print("\n>>> Backend Endpoints Test PASSED!")
except Exception as e:
    print(f"\n>>> Backend Test FAILED: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("ALL LOCAL TESTS COMPLETE")
print("=" * 60)
