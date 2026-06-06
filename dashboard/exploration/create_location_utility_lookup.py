"""
Exploratory script — Create Location to Utility Lookup

Purpose:
Retrieve Kentucky electric utility service-area polygons from the Kentucky
ArcGIS REST service, test selected Kentucky weather locations against those
polygons, and create a location_utility_lookup.csv file.

This is exploratory only. It does not modify the Week 3 ETL pipeline,
Dash app, or Neon database.

What this script does:
1. Pulls electric service-area polygons as GeoJSON
2. Tests each weather location latitude/longitude against the polygons
3. Deduplicates utility matches
4. Saves a CSV lookup table for future ETL/Dash enhancement
"""

import os
from typing import Dict, List

import pandas as pd
import requests
from shapely.geometry import Point, shape


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

# Because this file is inside dashboard/exploration, this walks up two folders:
# dashboard/exploration -> dashboard -> project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUTPUT_DIR = os.path.join(BASE_DIR, "data", "reference")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "location_utility_lookup.csv")

ARCGIS_QUERY_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_Electric_Service_Areas_WGS84WM/"
    "MapServer/1/query"
)

# Start with the same locations from your Week 3 ETL.
# Shapely uses Point(longitude, latitude), but we store lat/lon normally here.
LOCATIONS = [
    {
        "location_id": 1,
        "location_name": "Louisville",
        "state": "KY",
        "latitude": 38.2542,
        "longitude": -85.7594,
    },
    {
        "location_id": 2,
        "location_name": "Lexington",
        "state": "KY",
        "latitude": 38.0406,
        "longitude": -84.5037,
    },
    {
        "location_id": 3,
        "location_name": "Bowling Green",
        "state": "KY",
        "latitude": 36.9685,
        "longitude": -86.4808,
    },
]


# ---------------------------------------------------------
# Extract utility polygons
# ---------------------------------------------------------

def fetch_all_utility_polygons() -> List[Dict]:
    """
    Fetch all Kentucky electric service-area polygons from ArcGIS as GeoJSON.

    ArcGIS services often limit the number of records returned per request.
    This function uses resultOffset/resultRecordCount pagination so we are
    more likely to retrieve the full layer instead of only the first 1000 rows.

    Returns:
        List of GeoJSON features.
    """
    all_features = []
    result_offset = 0
    batch_size = 1000

    while True:
        params = {
            "where": "1=1",
            "outFields": "OBJECTID,UTIL_ID,COMPANY_NA,UTILITY_TY,ELEC_TYPE,CLASS",
            "returnGeometry": "true",
            "f": "geojson",
            "outSR": "4326",
            "resultOffset": result_offset,
            "resultRecordCount": batch_size,
        }

        response = requests.get(ARCGIS_QUERY_URL, params=params, timeout=120)
        response.raise_for_status()

        data = response.json()

        if "error" in data:
            raise ValueError(f"ArcGIS API returned an error: {data['error']}")

        features = data.get("features", [])

        if not features:
            break

        all_features.extend(features)

        print(f"Retrieved {len(features)} utility polygon features at offset {result_offset}.")

        if len(features) < batch_size:
            break

        result_offset += batch_size

    return all_features


# ---------------------------------------------------------
# Match locations to utility polygons
# ---------------------------------------------------------

def match_locations_to_utilities(locations: List[Dict], utility_features: List[Dict]) -> pd.DataFrame:
    """
    Match each location point to utility service-area polygons.

    Args:
        locations: List of location dictionaries with latitude/longitude.
        utility_features: GeoJSON features from the ArcGIS service.

    Returns:
        DataFrame containing one or more utility matches per location.
    """
    matches = []

    # Convert utility geometries once so they do not need to be rebuilt for every location.
    utility_polygons = []

    for feature in utility_features:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry")

        if not geometry:
            continue

        try:
            polygon = shape(geometry)
        except Exception as error:
            print(f"Skipping invalid geometry for OBJECTID {properties.get('OBJECTID')}: {error}")
            continue

        utility_polygons.append(
            {
                "properties": properties,
                "polygon": polygon,
            }
        )

    print(f"Prepared {len(utility_polygons)} polygon geometries for matching.")

    for location in locations:
        point = Point(location["longitude"], location["latitude"])

        print(f"\nMatching location: {location['location_name']}")

        location_match_count = 0

        for utility in utility_polygons:
            polygon = utility["polygon"]
            properties = utility["properties"]

            # contains = point is inside polygon
            # touches = point falls exactly on polygon boundary
            if polygon.contains(point) or polygon.touches(point):
                location_match_count += 1

                matches.append(
                    {
                        "location_id": location["location_id"],
                        "location_name": location["location_name"],
                        "state": location["state"],
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "object_id": properties.get("OBJECTID"),
                        "utility_id": properties.get("UTIL_ID"),
                        "company_name": properties.get("COMPANY_NA"),
                        "utility_type": properties.get("UTILITY_TY"),
                        "electric_type": properties.get("ELEC_TYPE"),
                        "service_class": properties.get("CLASS"),
                        "match_method": "point_in_polygon",
                    }
                )

        print(f"Raw matches found: {location_match_count}")

    if not matches:
        return pd.DataFrame(
            columns=[
                "location_id",
                "location_name",
                "state",
                "latitude",
                "longitude",
                "object_id",
                "utility_id",
                "company_name",
                "utility_type",
                "electric_type",
                "service_class",
                "match_method",
            ]
        )

    matches_df = pd.DataFrame(matches)

    # Deduplicate matches.
    # ArcGIS may return duplicate/overlapping polygon fragments for the same utility.
    deduped_df = matches_df.drop_duplicates(
        subset=[
            "location_id",
            "utility_id",
            "company_name",
            "electric_type",
            "service_class",
        ]
    ).reset_index(drop=True)

    return deduped_df


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main() -> None:
    """
    Run the location-to-utility matching process.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Fetching Kentucky electric utility service-area polygons...")
    utility_features = fetch_all_utility_polygons()

    print(f"\nTotal utility polygon features retrieved: {len(utility_features)}")

    lookup_df = match_locations_to_utilities(LOCATIONS, utility_features)

    print("\nLocation to utility lookup results:")
    if lookup_df.empty:
        print("No matches found.")
    else:
        print(lookup_df)

    lookup_df.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved location utility lookup to: {OUTPUT_FILE}")

    print("\nMatch count by location:")
    if not lookup_df.empty:
        print(lookup_df.groupby("location_name")["company_name"].count())
    else:
        print("No matches to summarize.")


if __name__ == "__main__":
    main()