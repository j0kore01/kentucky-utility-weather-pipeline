"""
Exploratory script — Kentucky Electric Service Areas

Purpose:
Connect to the Kentucky GIS ArcGIS REST service for electric service areas,
retrieve utility service area attributes, and save a local CSV sample.

This is exploratory only. It does not modify the Week 3 ETL pipeline,
Dash app, or Neon database.
"""

import os
import requests
import pandas as pd


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "reference")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "kentucky_electric_service_areas.csv")

ARCGIS_QUERY_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_Electric_Service_Areas_WGS84WM/"
    "MapServer/1/query"
)


def fetch_utility_service_areas() -> pd.DataFrame:
    """
    Fetch Kentucky electric service area attributes from the ArcGIS REST API.

    This first version retrieves attributes only, not full polygon geometry.
    That keeps the file small and easy to inspect.
    """
    params = {
        "where": "1=1",
        "outFields": "OBJECTID,UTIL_ID,COMPANY_NA,UTILITY_TY,ELEC_TYPE,CLASS,Shape.area,Shape.len",
        "returnGeometry": "false",
        "f": "json",
    }

    response = requests.get(ARCGIS_QUERY_URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise ValueError(f"ArcGIS API returned an error: {data['error']}")

    features = data.get("features", [])

    if not features:
        raise ValueError("No features returned from ArcGIS service.")

    records = [feature["attributes"] for feature in features]
    df = pd.DataFrame(records)

    df = df.rename(columns={
        "OBJECTID": "object_id",
        "UTIL_ID": "utility_id",
        "COMPANY_NA": "company_name",
        "UTILITY_TY": "utility_type",
        "ELEC_TYPE": "electric_type",
        "CLASS": "service_class",
        "Shape.area": "shape_area",
        "Shape.len": "shape_length",
    })

    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = fetch_utility_service_areas()

    print("\nKentucky electric service areas retrieved successfully.")
    print(f"Row count: {len(df)}")
    print("\nColumns:")
    print(df.columns.tolist())

    print("\nSample records:")
    print(df.head(10))

    print("\nUtility company count:")
    print(df["company_name"].nunique())

    print("\nService class counts:")
    print(df["service_class"].value_counts(dropna=False).head(20))

    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved output to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()