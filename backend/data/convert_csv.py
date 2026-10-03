import csv
import json
import os

in_path = r'c:\Users\swapn\Downloads\Projects\trip planner\ai-trip-concierge\backend\data\goa.csv'
out_path = r'c:\Users\swapn\Downloads\Projects\trip planner\ai-trip-concierge\backend\data\verified_locations.json'

locations = []
with open(in_path, 'r', encoding='utf-8', errors='ignore') as f:
    reader = csv.DictReader(f)
    for row in reader:
        title = row.get('title', '').strip()
        if not title:
            continue
            
        location = row.get('Place Location: ', '').strip()
        tips = row.get('Travel Tips: ', '').strip()
        duration = row.get('Trip Duration (Including Travel): ', '').strip()
        
        # Heuristics
        title_lower = title.lower()
        if 'beach' in title_lower or 'fort' in title_lower or 'waterfall' in title_lower:
            loc_type = 'outdoor'
            category = 'beach' if 'beach' in title_lower else 'sightseeing'
        elif 'temple' in title_lower or 'church' in title_lower or 'cathedral' in title_lower or 'basilica' in title_lower or 'museum' in title_lower:
            loc_type = 'indoor'
            category = 'heritage'
        elif 'cruise' in title_lower:
            loc_type = 'outdoor'
            category = 'activity'
        else:
            loc_type = 'outdoor'
            category = 'sightseeing'
            
        loc_id = "".join([c if c.isalnum() else "_" for c in title_lower])
        
        weather_constraints = {"max_rain_mm": 2, "safe_in_storm": False} if loc_type == 'outdoor' else None
        
        locations.append({
            "id": loc_id,
            "name": title,
            "category": category,
            "type": loc_type,
            "location": location,
            "timings": duration,
            "weather_constraints": weather_constraints,
            "description": tips
        })

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(locations, f, indent=2)

print(f"Successfully converted {len(locations)} locations.")
