#!/usr/bin/env python3
import pandas as pd
import os

def clean_city_data(input_file='cities.csv', output_file='cities.csv'):
    """
    Cleans the city data by consolidating Dubai suburbs, filtering small cities,
    and removing duplicates.
    """
    try:
        # Create a backup of the original file
        backup_file = 'cities.csv.bak'
        if os.path.exists(input_file):
            os.rename(input_file, backup_file)
            print(f"Original file '{input_file}' renamed to '{backup_file}'")
            input_file = backup_file

        df = pd.read_csv(input_file)
        
        # Coerce population to numeric, fill NaNs with 0
        df['population'] = pd.to_numeric(df['population'], errors='coerce').fillna(0).astype(int)

        # Filter out cities with population less than 100,000
        df = df[df['population'] >= 100000].copy()

        # Consolidate Dubai suburbs
        dubai_suburbs = [
            "jebel ali", "al barsha", "deira", "bur dubai", "dubai marina",
            "downtown dubai", "jumeirah", "mirdif", "satwa", "al quoz"
        ]
        
        # Find the main Dubai entry to consolidate into
        dubai_main_entry = df[df['city'].str.lower() == 'dubai']
        if not dubai_main_entry.empty:
            dubai_index = dubai_main_entry.index[0]
            
            # Identify suburb entries
            is_suburb = df['city'].str.lower().isin(dubai_suburbs)
            is_uae = df['country'].str.lower() == 'united arab emirates'
            suburb_df = df[is_suburb & is_uae]
            
            # Add suburbs' population to Dubai's
            df.loc[dubai_index, 'population'] += suburb_df['population'].sum()
            
            # Remove suburb entries
            df.drop(suburb_df.index, inplace=True)

        # Remove duplicates, keeping the one with the highest population
        df.sort_values('population', ascending=False, inplace=True)
        df.drop_duplicates(subset='city', keep='first', inplace=True)

        # Save the cleaned data
        df.to_csv(output_file, index=False)
        
        print(f"Cleaned data saved to '{output_file}'")
        print(f"Total cities after cleaning: {len(df)}")

    except FileNotFoundError:
        print(f"Error: The file {input_file} was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    clean_city_data()

