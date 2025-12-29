#!/usr/bin/env python3
import csv
import requests
import re

def get_wikidata_cities():
    """Queries Wikidata for cities with a population > 100,000."""
    url = "https://query.wikidata.org/sparql"
    query = """
    SELECT ?cityLabel ?countryLabel ?adminDivLabel ?population ?coordinates WHERE {
      ?city wdt:P31/wdt:P279* wd:Q486972;
            wdt:P1082 ?population.
      FILTER(?population > 100000)
      
      OPTIONAL { ?city wdt:P17 ?country. }
      OPTIONAL { ?city wdt:P131 ?adminDiv. }
      OPTIONAL { ?city wdt:P625 ?coordinates. }
      
      SERVICE wikibase:label { 
        bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en".
        ?city rdfs:label ?cityLabel.
        ?country rdfs:label ?countryLabel.
        ?adminDiv rdfs:label ?adminDivLabel.
      }
    }
    """
    try:
        response = requests.get(url, params={'query': query, 'format': 'json'})
        response.raise_for_status()
        data = response.json()
        
        cities = []
        for item in data['results']['bindings']:
            coords = item.get('coordinates', {}).get('value')
            if not coords:
                continue
            
            # Extract lat and lng from "Point(lng lat)"
            match = re.match(r'Point\(([-]?\d+\.?\d*)\s([-]?\d+\.?\d*)\)', coords)
            if not match:
                continue
                
            lng, lat = match.groups()

            cities.append({
                'city': item.get('cityLabel', {}).get('value'),
                'lat': lat,
                'lng': lng,
                'country': item.get('countryLabel', {}).get('value'),
                'state': item.get('adminDivLabel', {}).get('value'),
                'population': item.get('population', {}).get('value')
            })
        return cities
    except requests.exceptions.RequestException as e:
        print(f"Error querying Wikidata: {e}")
        return []

if __name__ == "__main__":
    print("Querying Wikidata for city data (this may take a moment)...")
    cities_data = get_wikidata_cities()

    if cities_data:
        print(f"Found {len(cities_data)} cities. Writing to cities.csv...")
        with open('cities.csv', 'w', newline='', encoding='utf-8') as outfile:
            fieldnames = ['city', 'lat', 'lng', 'country', 'state', 'population']
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(cities_data)
        print("Done.")
    else:
        print("Could not retrieve city data from Wikidata.")
