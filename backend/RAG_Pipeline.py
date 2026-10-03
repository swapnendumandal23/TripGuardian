import os
import json
import chromadb
from typing import List, Dict, Any

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "verified_locations.json")

class RAGPipeline:
    def __init__(self):
        # In-memory ChromaDB for simplicity/speed during hackathon
        self.chroma_client = chromadb.Client()
        self.collection = self.chroma_client.get_or_create_collection(name="locations")
        self._ingest_data()

    def _ingest_data(self):
        if not os.path.exists(DATA_PATH):
            print(f"Data file not found at {DATA_PATH}")
            return
            
        with open(DATA_PATH, 'r') as f:
            locations = json.load(f)

        docs = []
        metadatas = []
        ids = []

        for loc in locations:
            # Create a rich text representation for semantic search
            doc_text = f"{loc['name']} in {loc['location']}. Category: {loc['category']}. Type: {loc['type']}. {loc['description']}"
            docs.append(doc_text)
            metadatas.append({
                "id": loc['id'],
                "name": loc['name'],
                "category": loc['category'],
                "type": loc['type'],
                "timings": loc['timings']
            })
            ids.append(loc['id'])

        # Add to collection (upsert handles duplicates if run multiple times)
        self.collection.upsert(
            documents=docs,
            metadatas=metadatas,
            ids=ids
        )
        print(f"Ingested {len(locations)} verified locations into ChromaDB.")

    def search(self, query: str, n_results: int = 3, filter_conditions: dict = None, max_distance: float = 1.5) -> List[Dict[str, Any]]:
        # e.g., filter_conditions={"type": "indoor"}
        query_kwargs = {
            "query_texts": [query],
            "n_results": n_results
        }
        if filter_conditions:
            query_kwargs["where"] = filter_conditions

        results = self.collection.query(**query_kwargs)

        matched_locations = []
        if results['documents'] and len(results['documents'][0]) > 0:
            for i in range(len(results['documents'][0])):
                distance = results['distances'][0][i] if 'distances' in results and results['distances'] else 0.0
                if distance > max_distance:
                    continue
                    
                matched_locations.append({
                    "id": results['ids'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "document": results['documents'][0][i],
                    "distance": distance
                })
        return matched_locations

rag_pipeline = RAGPipeline()
