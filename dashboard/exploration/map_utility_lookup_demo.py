"""
Point-First Utility Territory Weather Lookup Demo

Purpose:
This exploratory Dash app makes the map click the primary interaction.

Workflow:
1. User clicks a point on the Kentucky map
2. App captures latitude/longitude
3. App checks all Kentucky electric utility service-area polygons
4. App identifies the utility provider that contains the clicked point
5. The matched utility dropdown updates automatically
6. The matched utility territory is highlighted
7. App retrieves current weather from Open-Meteo for the clicked coordinate
8. App displays utility context, nearest monitored city, weather, HDD/CDD,
   demand category, and risk categories

This is exploratory only. It does not modify the Week 3 ETL pipeline,
Neon database, or main Week 4 dashboard app.
"""

import math
import requests
from shapely.geometry import Point, shape

from dash import Dash, dcc, html, Input, Output, State
import dash_leaflet as dl


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

ARCGIS_QUERY_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_Electric_Service_Areas_WGS84WM/"
    "MapServer/1/query"
)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

KENTUCKY_CENTER = [37.8393, -84.2700]
KENTUCKY_ZOOM = 7

# Monitored locations are used only for nearest-city context.
MONITORED_LOCATIONS = [
    {
        "location_name": "Louisville",
        "state": "KY",
        "latitude": 38.2542,
        "longitude": -85.7594,
    },
    {
        "location_name": "Lexington",
        "state": "KY",
        "latitude": 38.0406,
        "longitude": -84.5037,
    },
    {
        "location_name": "Bowling Green",
        "state": "KY",
        "latitude": 36.9685,
        "longitude": -86.4808,
    },
    {
        "location_name": "Paducah",
        "state": "KY",
        "latitude": 37.0834,
        "longitude": -88.6000,
    },
    {
        "location_name": "Covington",
        "state": "KY",
        "latitude": 39.0837,
        "longitude": -84.5086,
    },
    {
        "location_name": "Owensboro",
        "state": "KY",
        "latitude": 37.7719,
        "longitude": -87.1112,
    },
    {
        "location_name": "Frankfort",
        "state": "KY",
        "latitude": 38.2009,
        "longitude": -84.8733,
    },
    {
        "location_name": "Elizabethtown",
        "state": "KY",
        "latitude": 37.7031,
        "longitude": -85.8649,
    },
    {
        "location_name": "Pikeville",
        "state": "KY",
        "latitude": 37.4793,
        "longitude": -82.5188,
    },
    {
        "location_name": "Somerset",
        "state": "KY",
        "latitude": 37.0920,
        "longitude": -84.6041,
    },
]

WEATHER_CODE_LOOKUP = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


# ---------------------------------------------------------
# ArcGIS / Weather helpers
# ---------------------------------------------------------

def fetch_all_utility_geojson() -> dict:
    """
    Retrieve Kentucky electric utility service-area polygons as GeoJSON.

    This function uses pagination because ArcGIS services often limit each
    response to 1000 records. It combines all returned features into one
    GeoJSON FeatureCollection.
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
            raise ValueError(f"ArcGIS returned an error: {data['error']}")

        features = data.get("features", [])

        if not features:
            break

        all_features.extend(features)
        print(f"Retrieved {len(features)} utility features at offset {result_offset}.")

        if len(features) < batch_size:
            break

        result_offset += batch_size

    return {
        "type": "FeatureCollection",
        "features": all_features,
    }


def filter_geojson_by_company(all_geojson: dict, company_name: str) -> dict:
    """
    Build a GeoJSON FeatureCollection containing only one company.
    """
    if not company_name:
        return {
            "type": "FeatureCollection",
            "features": [],
        }

    filtered_features = [
        feature
        for feature in all_geojson.get("features", [])
        if feature.get("properties", {}).get("COMPANY_NA") == company_name
    ]

    return {
        "type": "FeatureCollection",
        "features": filtered_features,
    }


def find_utility_for_point(latitude: float, longitude: float, all_geojson: dict):
    """
    Find the utility polygon that contains the clicked point.

    Shapely expects Point(longitude, latitude).
    """
    point = Point(longitude, latitude)

    for feature in all_geojson.get("features", []):
        geometry = feature.get("geometry")
        properties = feature.get("properties", {})

        if not geometry:
            continue

        try:
            polygon = shape(geometry)
        except Exception:
            continue

        if polygon.contains(point) or polygon.touches(point):
            return properties

    return None


def fetch_current_weather(latitude: float, longitude: float) -> dict:
    """
    Retrieve current weather conditions from Open-Meteo.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }

    response = requests.get(OPEN_METEO_URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    if "current" not in data:
        raise ValueError("Open-Meteo response did not include current weather data.")

    return data["current"]


# ---------------------------------------------------------
# Business logic helpers
# ---------------------------------------------------------

def classify_wind_risk(wind_speed: float) -> str:
    """
    Classify wind risk using the same style of business logic as the ETL pipeline.
    """
    if wind_speed is None:
        return "Unknown"
    if wind_speed >= 40:
        return "High"
    if wind_speed >= 25:
        return "Moderate"
    return "Low"


def classify_precipitation_risk(precipitation: float) -> str:
    """
    Classify precipitation risk using simple operational thresholds.
    """
    if precipitation is None:
        return "Unknown"
    if precipitation >= 1.0:
        return "High"
    if precipitation >= 0.25:
        return "Moderate"
    return "Low"


def classify_demand_category(temperature_f: float) -> str:
    """
    Classify demand category from current temperature.
    """
    if temperature_f is None:
        return "Unknown"
    if temperature_f < 32:
        return "High Heating Demand"
    if temperature_f < 50:
        return "Moderate Heating Demand"
    if temperature_f <= 70:
        return "Mild Demand"
    if temperature_f <= 85:
        return "Cooling Demand"
    return "High Cooling Demand"


def calculate_hdd_cdd(temperature_f: float) -> tuple:
    """
    Estimate HDD/CDD from current temperature using 65°F base.
    """
    if temperature_f is None:
        return None, None

    heating_degree_days = max(65 - temperature_f, 0)
    cooling_degree_days = max(temperature_f - 65, 0)

    return heating_degree_days, cooling_degree_days


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """
    Calculate distance in miles between two latitude/longitude points.
    """
    radius_miles = 3958.8

    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return radius_miles * c


def find_nearest_monitored_city(latitude: float, longitude: float) -> dict:
    """
    Find nearest monitored city from the configured city list.
    """
    nearest = None

    for location in MONITORED_LOCATIONS:
        distance = haversine_miles(
            latitude,
            longitude,
            location["latitude"],
            location["longitude"],
        )

        candidate = {
            **location,
            "distance_miles": distance,
        }

        if nearest is None or distance < nearest["distance_miles"]:
            nearest = candidate

    return nearest


def create_metric_row(label: str, value: str):
    """
    Small helper for consistent sidebar text rows.
    """
    return html.Div(
        style={"marginBottom": "10px"},
        children=[
            html.Div(
                label,
                style={
                    "fontWeight": "bold",
                    "fontSize": "13px",
                    "color": "#555",
                },
            ),
            html.Div(
                value,
                style={
                    "fontSize": "15px",
                    "color": "#222",
                },
            ),
        ],
    )


# ---------------------------------------------------------
# Initial data load
# ---------------------------------------------------------

print("Loading Kentucky electric utility service-area polygons...")
ALL_UTILITY_GEOJSON = fetch_all_utility_geojson()
ALL_FEATURES = ALL_UTILITY_GEOJSON.get("features", [])
print(f"Loaded {len(ALL_FEATURES)} utility polygon features.")

UTILITY_COMPANIES = sorted(
    {
        feature.get("properties", {}).get("COMPANY_NA")
        for feature in ALL_FEATURES
        if feature.get("properties", {}).get("COMPANY_NA")
    }
)


# ---------------------------------------------------------
# Dash app
# ---------------------------------------------------------

app = Dash(__name__)
app.title = "Kentucky Utility Territory Weather Lookup"


app.layout = html.Div(
    style={
        "fontFamily": "Arial, sans-serif",
        "backgroundColor": "#f5f7fb",
        "padding": "24px",
        "minHeight": "100vh",
    },
    children=[
        html.Div(
            style={
                "backgroundColor": "white",
                "padding": "24px",
                "borderRadius": "16px",
                "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                "marginBottom": "18px",
            },
            children=[
                html.H1(
                    "Kentucky Utility Territory Weather Lookup",
                    style={"margin": "0 0 8px 0", "fontSize": "34px"},
                ),
                html.P(
                    "Click a point on the Kentucky map to identify the electric utility "
                    "service territory and retrieve current weather risk indicators for "
                    "that coordinate. The matched utility is automatically populated below.",
                    style={"color": "#555", "fontSize": "17px", "margin": "0"},
                ),
            ],
        ),

        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "380px 1fr",
                "gap": "18px",
            },
            children=[
                html.Div(
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "16px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                        "maxHeight": "800px",
                        "overflowY": "auto",
                    },
                    children=[
                        html.Label(
                            "Matched Utility Provider",
                            style={"fontWeight": "bold", "fontSize": "16px"},
                        ),
                        dcc.Dropdown(
                            id="matched-utility-dropdown",
                            options=[
                                {"label": company, "value": company}
                                for company in UTILITY_COMPANIES
                            ],
                            value=None,
                            placeholder="Click the map to identify utility provider",
                            clearable=True,
                            style={"marginTop": "8px", "marginBottom": "18px"},
                        ),

                        html.Div(
                            style={
                                "backgroundColor": "#f2f5fa",
                                "padding": "12px",
                                "borderRadius": "10px",
                                "marginBottom": "18px",
                                "fontSize": "14px",
                                "color": "#444",
                            },
                            children=[
                                html.Div(
                                    "Map Legend",
                                    style={"fontWeight": "bold", "marginBottom": "8px"},
                                ),
                                html.Div("Gray areas = Kentucky electric service territories"),
                                html.Div("Blue area = matched utility territory"),
                                html.Div("Red marker = clicked coordinate"),
                            ],
                        ),

                        html.H3("Clicked Location"),
                        html.Div(
                            id="click-output",
                            children="Click a point on the map.",
                            style={"fontSize": "14px", "color": "#444"},
                        ),

                        html.Hr(),

                        html.H3("Nearest Monitored City"),
                        html.Div(
                            id="nearest-city-output",
                            children="Nearest city will appear after clicking the map.",
                            style={"fontSize": "14px", "color": "#444"},
                        ),

                        html.Hr(),

                        html.H3("Weather & Utility Risk"),
                        html.Div(
                            id="weather-output",
                            children="Weather will appear after clicking the map.",
                            style={"fontSize": "14px", "color": "#444"},
                        ),
                    ],
                ),

                html.Div(
                    style={
                        "backgroundColor": "white",
                        "padding": "14px",
                        "borderRadius": "16px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        dl.Map(
                            id="utility-map",
                            center=KENTUCKY_CENTER,
                            zoom=KENTUCKY_ZOOM,
                            style={
                                "height": "800px",
                                "width": "100%",
                                "borderRadius": "12px",
                            },
                            children=[
                                dl.LayersControl(
                                    position="topright",
                                    children=[
                                        dl.BaseLayer(
                                            dl.TileLayer(
                                                url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                                                attribution="© OpenStreetMap contributors",
                                            ),
                                            name="OpenStreetMap",
                                            checked=True,
                                        ),
                                        dl.BaseLayer(
                                            dl.TileLayer(
                                                url=(
                                                    "https://{s}.basemaps.cartocdn.com/light_all/"
                                                    "{z}/{x}/{y}{r}.png"
                                                ),
                                                attribution="© OpenStreetMap contributors © CARTO",
                                            ),
                                            name="CARTO Light",
                                            checked=False,
                                        ),
                                        dl.BaseLayer(
                                            dl.TileLayer(
                                                url=(
                                                    "https://{s}.basemaps.cartocdn.com/dark_all/"
                                                    "{z}/{x}/{y}{r}.png"
                                                ),
                                                attribution="© OpenStreetMap contributors © CARTO",
                                            ),
                                            name="CARTO Dark",
                                            checked=False,
                                        ),
                                        dl.BaseLayer(
                                            dl.TileLayer(
                                                url=(
                                                    "https://server.arcgisonline.com/ArcGIS/rest/services/"
                                                    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
                                                ),
                                                attribution="Tiles © Esri",
                                            ),
                                            name="Esri World Imagery",
                                            checked=False,
                                        ),
                                    ],
                                ),

                                dl.GeoJSON(
                                    id="all-utilities-geojson",
                                    data=ALL_UTILITY_GEOJSON,
                                    style={
                                        "color": "#777777",
                                        "weight": 1,
                                        "fillOpacity": 0.07,
                                    },
                                ),

                                dl.GeoJSON(
                                    id="matched-utility-geojson",
                                    data={
                                        "type": "FeatureCollection",
                                        "features": [],
                                    },
                                    style={
                                        "color": "#0057b8",
                                        "weight": 4,
                                        "fillOpacity": 0.35,
                                    },
                                ),

                                dl.LayerGroup(id="marker-layer"),
                            ],
                        ),
                    ],
                ),
            ],
        ),

        dcc.Store(id="all-utilities-store", data=ALL_UTILITY_GEOJSON),
    ],
)


# ---------------------------------------------------------
# Callbacks
# ---------------------------------------------------------

@app.callback(
    Output("matched-utility-dropdown", "value"),
    Output("matched-utility-geojson", "data"),
    Output("click-output", "children"),
    Output("nearest-city-output", "children"),
    Output("weather-output", "children"),
    Output("marker-layer", "children"),
    Input("utility-map", "clickData"),
    State("all-utilities-store", "data"),
)
def handle_map_click(click_data, all_geojson):
    """
    Main point-first interaction.

    The clicked latitude/longitude determines:
    - matched utility provider
    - highlighted utility territory
    - nearest monitored city
    - current weather and risk metrics
    """
    if not click_data:
        empty_geojson = {
            "type": "FeatureCollection",
            "features": [],
        }

        return (
            None,
            empty_geojson,
            "Click a point on the map.",
            "Nearest city will appear after clicking the map.",
            "Weather will appear after clicking the map.",
            [],
        )

    latitude = click_data["latlng"]["lat"]
    longitude = click_data["latlng"]["lng"]

    marker = dl.Marker(
        position=[latitude, longitude],
        children=[
            dl.Tooltip("Selected coordinate"),
            dl.Popup(f"Lat: {latitude:.5f}, Lon: {longitude:.5f}"),
        ],
    )

    matched_properties = find_utility_for_point(latitude, longitude, all_geojson)

    if matched_properties:
        matched_company = matched_properties.get("COMPANY_NA")
        utility_id = str(matched_properties.get("UTIL_ID", "Unknown"))
        utility_type = str(matched_properties.get("UTILITY_TY", "Unknown"))
        electric_type = matched_properties.get("ELEC_TYPE", "Unknown")
        service_class = matched_properties.get("CLASS", "Unknown")
        point_status = "Inside matched utility territory"

        matched_geojson = filter_geojson_by_company(all_geojson, matched_company)

    else:
        matched_company = None
        utility_id = "Not matched"
        utility_type = "Not matched"
        electric_type = "Not matched"
        service_class = "Not matched"
        point_status = "No Kentucky utility territory matched this point"

        matched_geojson = {
            "type": "FeatureCollection",
            "features": [],
        }

    click_message = html.Div(
        children=[
            create_metric_row("Latitude", f"{latitude:.5f}"),
            create_metric_row("Longitude", f"{longitude:.5f}"),
            create_metric_row("Point Status", point_status),
            create_metric_row(
                "Matched Utility",
                matched_company if matched_company else "No match found",
            ),
            create_metric_row("Utility ID", utility_id),
            create_metric_row("Utility Type", utility_type),
            create_metric_row("Electric Type", electric_type),
            create_metric_row("Service Class", service_class),
        ]
    )

    nearest_city = find_nearest_monitored_city(latitude, longitude)

    nearest_city_message = html.Div(
        children=[
            create_metric_row("Nearest Monitored City", nearest_city["location_name"]),
            create_metric_row("State", nearest_city["state"]),
            create_metric_row("Distance", f"{nearest_city['distance_miles']:.2f} miles"),
        ]
    )

    try:
        weather = fetch_current_weather(latitude, longitude)

        temperature = weather.get("temperature_2m")
        wind_speed = weather.get("wind_speed_10m")
        precipitation = weather.get("precipitation")
        weather_code = weather.get("weather_code")

        weather_description = WEATHER_CODE_LOOKUP.get(weather_code, "Unknown")
        hdd, cdd = calculate_hdd_cdd(temperature)

        wind_risk = classify_wind_risk(wind_speed)
        precipitation_risk = classify_precipitation_risk(precipitation)
        demand_category = classify_demand_category(temperature)

        weather_message = html.Div(
            children=[
                create_metric_row("Temperature", f"{temperature}°F"),
                create_metric_row("Weather", weather_description),
                create_metric_row("Wind Speed", f"{wind_speed} mph"),
                create_metric_row("Precipitation", f"{precipitation} in"),
                create_metric_row(
                    "Estimated Heating Degree Days",
                    "N/A" if hdd is None else f"{hdd:.1f}",
                ),
                create_metric_row(
                    "Estimated Cooling Degree Days",
                    "N/A" if cdd is None else f"{cdd:.1f}",
                ),
                create_metric_row("Demand Category", demand_category),
                create_metric_row("Wind Risk", wind_risk),
                create_metric_row("Precipitation Risk", precipitation_risk),
            ]
        )

    except Exception as error:
        weather_message = f"Weather lookup failed: {error}"

    return (
        matched_company,
        matched_geojson,
        click_message,
        nearest_city_message,
        weather_message,
        [marker],
    )


# ---------------------------------------------------------
# Run app
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=8053)