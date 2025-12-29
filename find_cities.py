#!/usr/bin/env python3
import argparse
import json
import csv
from math import radians, sin, cos, sqrt, atan2

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    r = 6371 # Radius of earth in kilometers.
    return c * r

def load_cities_csv(file_path):
    cities = []
    try:
        with open(file_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    cities.append({
                        'city': row.get('city', '').strip(),
                        'lat': float(row['lat']),
                        'lng': float(row['lng']),
                        'country': row.get('country', ''),
                        'state': row.get('state', ''),
                        'population': int(row.get('population', 0)) if row.get('population') else 0
                    })
                except (ValueError, KeyError):
                    continue
    except Exception:
        pass # Handle exceptions quietly
    return cities

def find_cities_in_radius(lat, lon, cities, radius_km):
    """
    Finds all cities within a given radius with a population > 50,000.
    """
    cities_within_radius = [
        city for city in cities
        if haversine(lat, lon, city['lat'], city['lng']) <= radius_km and city['population'] >= 50000
    ]
    return cities_within_radius

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find cities within a radius from a given geolocation.")
    parser.add_argument("--lat", type=float, required=True, help="Latitude")
    parser.add_argument("--lon", type=float, required=True, help="Longitude")
    parser.add_argument("--radius", type=float, required=True, help="Search radius in kilometers")
    parser.add_argument("--cities-csv", default="cities.csv", help="Path to the cities CSV file")
    args = parser.parse_args()
    
    cities = load_cities_csv(args.cities_csv)
    found_cities = find_cities_in_radius(args.lat, args.lon, cities, args.radius)
    
    response = {"nearest_city": None, "other_cities": []}

    if found_cities:
        # Find the nearest city among the found cities
        nearest_city = min(found_cities, key=lambda city: haversine(args.lat, args.lon, city['lat'], city['lng']))
        
        response["nearest_city"] = {
            "name": nearest_city['city'],
            "country": nearest_city['country'],
            "state": nearest_city['state'],
            "wikipedia_url": f"https://en.wikipedia.org/wiki/{nearest_city['city'].replace(' ', '_')}"
        }

        # Add other cities to the list
        for city in found_cities:
            if city['city'] != nearest_city['city']:
                response["other_cities"].append({
                    "name": city['city'],
                    "country": city['country'],
                    "state": city['state'],
                    "wikipedia_url": f"https://en.wikipedia.org/wiki/{city['city'].replace(' ', '_')}"
                })

    print(json.dumps(response, indent=2))
