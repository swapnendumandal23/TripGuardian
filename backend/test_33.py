import os
import sys
import unittest
from unittest.mock import patch, MagicMock

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from places_service import discover_live_places, clear_places_cache

def run_test_33():
    print("\n" + "=" * 70)
    print("[TEST 32] BUG-33 Chroma/RAG vs Live POI Deterministic Precedence")
    print("=" * 70)

    clear_places_cache()

    # We mock requests.post to return a Live Overpass result for "Mandovi River Sunset Cruise"
    # In ChromaDB it is: 
    #   category: sightseeing
    #   type: outdoor
    #   timings: 17:00 - 19:00
    #
    # We mock it as conflicting:
    #   amenity: bar (makes type = dining)
    #   tourism: pub (category = pub)
    #   opening_hours: "00:00 - 24:00"
    
    mock_overpass_response = {
        "elements": [
            {
                "type": "node",
                "id": 999999,
                "lat": 15.5,
                "lon": 73.8,
                "tags": {
                    "name": "Mandovi River Sunset Cruise",
                    "amenity": "bar",
                    "tourism": "pub",
                    "opening_hours": "00:00 - 24:00"
                }
            }
        ]
    }

    class MockResponse:
        def __init__(self, json_data):
            self._json = json_data
        def json(self):
            return self._json
        def raise_for_status(self):
            pass

    with patch('requests.post', return_value=MockResponse(mock_overpass_response)):
        places = discover_live_places("Goa", "all")
        
        # We expect exactly one place returned from Overpass
        # And it should be merged with ChromaDB!
        cruise = next((p for p in places if "mandovi river" in p["name"].lower()), None)
        assert cruise is not None, "Failed to return the mocked place."
        
        # Verify Precedence Rule
        assert cruise["category"] == "sightseeing", f"Chroma category precedence failed. Got: {cruise['category']}"
        assert cruise["type"] == "outdoor", f"Chroma type precedence failed. Got: {cruise['type']}"
        
        # Verify Freshness Rule (Contradiction not silently merged)
        expected_hours = "17:00 - 19:00 [Live update: 00:00 - 24:00]"
        assert cruise["opening_hours"] == expected_hours, f"Timings freshness logic failed. Got: {cruise['opening_hours']}"
        
        # Verify custom flag
        assert cruise.get("is_verified") is True, "is_verified flag missing"

    print("  >>> TEST 32 PASSED: Precedence and freshness rules deterministic between RAG and Live POI.")

if __name__ == "__main__":
    run_test_33()
