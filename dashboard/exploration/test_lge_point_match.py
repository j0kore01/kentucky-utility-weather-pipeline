"""
Exploratory script — LG&E Point-in-Polygon Test

Purpose:
Retrieve Louisville Gas and Electric Company service-area polygons from the
Kentucky ArcGIS REST service and test whether a known Louisville latitude/
longitude point falls inside one of those polygons.

This is exploratory only. It does not modify the Week 3 ETL pipeline,
Dash app, or Neon database.
"""

import requests
from shapely.geometry import Point, shape


ARCGIS_QUERY_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_Electric_Service_Areas_WGS84WM/"
    "MapServer/1/query"
)

# Test point: Louisville, KY
# Important: Shapely uses Point(longitude, latitude), not Point(latitude, longitude).
LOUISVILLE_POINT = Point(-85.7594, 38.2542)


def fetch_lge_geojson() -> dict:
    """
    Fetch LG&E electric service area polygons as GeoJSON.
    """
    params = {
        "where": "COMPANY_NA = 'Louisville Gas and Electric Company'",
        "outFields": "OBJECTID,UTIL_ID,COMPANY_NA,UTILITY_TY,ELEC_TYPE,CLASS",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": "4326",
    }

    response = requests.get(ARCGIS_QUERY_URL, params=params, timeout=60)
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise ValueError(f"ArcGIS API returned an error: {data['error']}")

    return data


def test_louisville_point_against_lge() -> None:
    """
    Test whether the Louisville point falls within an LG&E polygon.
    """
    geojson_data = fetch_lge_geojson()
    features = geojson_data.get("features", [])

    print("\nLG&E GeoJSON retrieved successfully.")
    print(f"LG&E polygon feature count: {len(features)}")

    if not features:
        print("No LG&E features returned. Check company name spelling or ArcGIS query.")
        return

    matches = []

    for feature in features:
        properties = feature.get("properties", {})
        geometry = feature.get("geometry")

        if not geometry:
            continue

        polygon = shape(geometry)

        if polygon.contains(LOUISVILLE_POINT) or polygon.touches(LOUISVILLE_POINT):
            matches.append(properties)

    if matches:
        print("\nMatch found. Louisville point falls inside/touches an LG&E service area.")

        for match in matches:
            print("\nMatched service area:")
            print(f"OBJECTID: {match.get('OBJECTID')}")
            print(f"UTIL_ID: {match.get('UTIL_ID')}")
            print(f"Company: {match.get('COMPANY_NA')}")
            print(f"Utility Type: {match.get('UTILITY_TY')}")
            print(f"Electric Type: {match.get('ELEC_TYPE')}")
            print(f"Service Class: {match.get('CLASS')}")
    else:
        print("\nNo match found for this Louisville point.")
        print("This may mean the point is outside the polygon boundary or near a service-area edge.")
        print("Try a different Louisville coordinate or test all utility polygons.")


if __name__ == "__main__":
    test_louisville_point_against_lge()