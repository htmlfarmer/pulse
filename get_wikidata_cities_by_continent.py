#!/usr/bin/env python3
import csv
import requests
import re
import time

def get_cities_by_continent(continent_qid):
    """Queries Wikidata for cities on a specific continent with a population > 10,000."""
    url = "https://query.wikidata.org/sparql"
    query = f"""
    SELECT ?cityLabel ?countryLabel ?population ?coordinates WHERE {{
      ?city wdt:P31/wdt:P279* wd:Q486972;
            wdt:P1082 ?population.
      FILTER(?population > 30000)
      
      ?city wdt:P17 ?country.
      ?country wdt:P30 wd:{continent_qid}. # Country is on continent
      
      OPTIONAL {{ ?city wdt:P625 ?coordinates. }}
      
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
    }}
    """
    try:
        response = requests.get(url, params={'query': query, 'format': 'json'}, headers={'User-Agent': 'MyCoolTool/0.0 (https://example.org/cool-tool/; my-cool-tool@example.org) BasedOnSuperLib/1.4'})
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error querying Wikidata for {continent_qid}: {e}")
        return None

def process_city_data(data):
    """Processes the JSON data from Wikidata and returns a list of city dicts."""
    cities = []
    if not data:
        return cities
        
    for item in data['results']['bindings']:
        coords = item.get('coordinates', {}).get('value')
        if not coords:
            continue
        
        match = re.match(r'Point\(([-]?\d+\.?\d*)\s([-]?\d+\.?\d*)\)', coords)
        if not match:
            continue
            
        lng, lat = match.groups()

        cities.append({
            'city': item.get('cityLabel', {}).get('value'),
            'lat': lat,
            'lng': lng,
            'country': item.get('countryLabel', {}).get('value'),
            'population': item.get('population', {}).get('value')
        })
    return cities

if __name__ == "__main__":
    continents = {
        'Asia': 'Q48',
        'Africa': 'Q15',
        'Europe': 'Q46',
        'North America': 'Q49',
        'South America': 'Q18',
        'Oceania': 'Q538'
    }
    
    all_cities = []
    for name, qid in continents.items():
        print(f"Querying cities in {name}...")
        json_data = get_cities_by_continent(qid)
        if json_data:
            all_cities.extend(process_city_data(json_data))
        time.sleep(5) # Be respectful of the API

    if all_cities:
        print(f"Found {len(all_cities)} cities in total. Writing to cities.csv...")
        with open('cities.csv', 'w', newline='', encoding='utf-8') as outfile:
            fieldnames = ['city', 'lat', 'lng', 'country', 'population']
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_cities)
        print("Done.")
    else:
        print("Could not retrieve any city data from Wikidata.")
