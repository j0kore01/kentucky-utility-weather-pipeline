"""
Kentucky Utility Territory Weather Lookup - Enhanced Dash App

Purpose
-------
Point-first geospatial dashboard for identifying the Kentucky electric utility
service territory at a clicked coordinate and retrieving weather-driven utility
risk indicators from Open-Meteo.

Major improvements over the exploratory version
-----------------------------------------------
1. Faster spatial lookup:
   - Converts GeoJSON geometries to Shapely objects once at startup.
   - Uses STRtree spatial indexing when available.
   - Falls back to prepared geometries if STRtree behavior differs by Shapely version.

2. More reliable API calls:
   - Reuses one requests.Session.
   - Adds retry/backoff for transient API failures.
   - Uses bounded timeouts.
   - Caches weather lookups by rounded coordinate.

3. Better feature set:
   - Supports either map click or manual latitude/longitude lookup.
   - Adds current conditions plus 7-day forecast.
   - Adds operational risk classification.
   - Adds forecast chart using Plotly.
   - Adds selected-point history table and CSV export.
   - Adds utility territory filtering.
   - Adds explicit data-quality checks for coordinates and API responses.

4. Cleaner architecture:
   - Separates extract, validate, transform, and visualize helper functions.
   - Avoids storing the full utility GeoJSON in dcc.Store, which can make the
     browser payload unnecessarily large.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import plotly.graph_objects as go
import requests
from dash import Dash, Input, Output, State, callback_context, dcc, html, dash_table
import dash_leaflet as dl
from requests.adapters import HTTPAdapter
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep
from shapely.strtree import STRtree
from urllib3.util.retry import Retry


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

ARCGIS_QUERY_URL = (
    "https://kygisserver.ky.gov/arcgis/rest/services/"
    "WGS84WM_Services/Ky_Electric_Service_Areas_WGS84WM/"
    "MapServer/1/query"
)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

KENTUCKY_CENTER = [37.8393, -84.2700]
KENTUCKY_ZOOM = 7

# Loose Kentucky bounding box used as a quick data-quality guardrail.
# This is intentionally generous so border clicks are not rejected too aggressively.
KY_LAT_MIN = 36.30
KY_LAT_MAX = 39.40
KY_LON_MIN = -89.70
KY_LON_MAX = -81.80

ARCGIS_BATCH_SIZE = 1000
ARCGIS_TIMEOUT_SECONDS = 60
WEATHER_TIMEOUT_SECONDS = 20
WEATHER_CACHE_SECONDS = 15 * 60

MONITORED_LOCATIONS = [
    {"location_name": "Louisville", "state": "KY", "latitude": 38.2542, "longitude": -85.7594},
    {"location_name": "Lexington", "state": "KY", "latitude": 38.0406, "longitude": -84.5037},
    {"location_name": "Bowling Green", "state": "KY", "latitude": 36.9685, "longitude": -86.4808},
    {"location_name": "Paducah", "state": "KY", "latitude": 37.0834, "longitude": -88.6000},
    {"location_name": "Covington", "state": "KY", "latitude": 39.0837, "longitude": -84.5086},
    {"location_name": "Owensboro", "state": "KY", "latitude": 37.7719, "longitude": -87.1112},
    {"location_name": "Frankfort", "state": "KY", "latitude": 38.2009, "longitude": -84.8733},
    {"location_name": "Elizabethtown", "state": "KY", "latitude": 37.7031, "longitude": -85.8649},
    {"location_name": "Pikeville", "state": "KY", "latitude": 37.4793, "longitude": -82.5188},
    {"location_name": "Somerset", "state": "KY", "latitude": 37.0920, "longitude": -84.6041},
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


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class UtilityFeature:
    """Server-side representation of one utility service-area feature."""

    feature_index: int
    geometry: BaseGeometry
    prepared_geometry: Any
    properties: Dict[str, Any]


@dataclass
class TimedCacheValue:
    """Simple in-memory TTL cache value."""

    created_at: float
    value: Any


# -----------------------------------------------------------------------------
# HTTP helpers
# -----------------------------------------------------------------------------

def build_http_session() -> requests.Session:
    """Create a requests session with retry/backoff for transient failures."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "MSBA692-KY-Utility-Weather-Dash/1.0",
            "Accept": "application/json",
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


HTTP_SESSION = build_http_session()


def get_json(url: str, params: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Fetch JSON with consistent error handling."""
    response = HTTP_SESSION.get(url, params=params, timeout=timeout)
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Expected JSON object but received a different payload type.")

    if "error" in data:
        raise ValueError(f"Remote service returned an error: {data['error']}")

    return data


# -----------------------------------------------------------------------------
# Extract
# -----------------------------------------------------------------------------

def fetch_all_utility_geojson() -> Dict[str, Any]:
    """
    Retrieve Kentucky electric utility service-area polygons as GeoJSON.

    ArcGIS services commonly page responses. This function requests all pages and
    combines them into one FeatureCollection.
    """
    all_features: List[Dict[str, Any]] = []
    result_offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": "OBJECTID,UTIL_ID,COMPANY_NA,UTILITY_TY,ELEC_TYPE,CLASS",
            "returnGeometry": "true",
            "f": "geojson",
            "outSR": "4326",
            "resultOffset": result_offset,
            "resultRecordCount": ARCGIS_BATCH_SIZE,
        }

        data = get_json(ARCGIS_QUERY_URL, params=params, timeout=ARCGIS_TIMEOUT_SECONDS)
        features = data.get("features", [])

        if not features:
            break

        all_features.extend(features)
        logger.info("Retrieved %s utility features at offset %s.", len(features), result_offset)

        if len(features) < ARCGIS_BATCH_SIZE:
            break

        result_offset += ARCGIS_BATCH_SIZE

    if not all_features:
        raise ValueError("No utility service-area features were returned from ArcGIS.")

    return {"type": "FeatureCollection", "features": all_features}


def fetch_weather_forecast(latitude: float, longitude: float) -> Dict[str, Any]:
    """Retrieve current weather and a 7-day daily forecast from Open-Meteo."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,weather_code"
        ),
        "forecast_days": 7,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    }

    data = get_json(OPEN_METEO_URL, params=params, timeout=WEATHER_TIMEOUT_SECONDS)
    validate_weather_response(data)
    return data


_WEATHER_CACHE: Dict[Tuple[float, float], TimedCacheValue] = {}


def get_cached_weather(latitude: float, longitude: float) -> Dict[str, Any]:
    """
    Cache weather calls by rounded coordinate.

    Rounding avoids treating tiny differences from repeated clicks as entirely new
    coordinates while still preserving neighborhood-level specificity.
    """
    key = (round(latitude, 3), round(longitude, 3))
    cached = _WEATHER_CACHE.get(key)

    if cached and time.time() - cached.created_at < WEATHER_CACHE_SECONDS:
        return cached.value

    data = fetch_weather_forecast(latitude, longitude)
    _WEATHER_CACHE[key] = TimedCacheValue(created_at=time.time(), value=data)
    return data


# -----------------------------------------------------------------------------
# Validate
# -----------------------------------------------------------------------------

def validate_coordinate(latitude: Any, longitude: Any) -> Tuple[Optional[float], Optional[float], List[str]]:
    """Validate and coerce a clicked or manually entered coordinate."""
    errors: List[str] = []

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None, None, ["Latitude and longitude must be numeric."]

    if not -90 <= lat <= 90:
        errors.append("Latitude must be between -90 and 90.")

    if not -180 <= lon <= 180:
        errors.append("Longitude must be between -180 and 180.")

    if not (KY_LAT_MIN <= lat <= KY_LAT_MAX and KY_LON_MIN <= lon <= KY_LON_MAX):
        errors.append(
            "Coordinate is outside the expected Kentucky bounding box. "
            "The app will still try to evaluate it, but a match is unlikely."
        )

    return lat, lon, errors


def validate_weather_response(data: Dict[str, Any]) -> None:
    """Validate required Open-Meteo response sections."""
    if "current" not in data:
        raise ValueError("Open-Meteo response did not include current weather data.")

    if "daily" not in data:
        raise ValueError("Open-Meteo response did not include daily forecast data.")

    required_current = {
        "temperature_2m",
        "precipitation",
        "weather_code",
        "wind_speed_10m",
    }

    missing_current = required_current.difference(data["current"].keys())

    if missing_current:
        raise ValueError(f"Open-Meteo current payload missing fields: {sorted(missing_current)}")


def validate_geojson_feature(feature: Dict[str, Any], feature_index: int) -> Optional[UtilityFeature]:
    """Convert a raw GeoJSON feature into a validated UtilityFeature."""
    geometry = feature.get("geometry")
    properties = feature.get("properties", {})

    if not geometry:
        logger.warning("Skipping feature %s because it has no geometry.", feature_index)
        return None

    try:
        geom = shape(geometry)
    except Exception as error:
        logger.warning("Skipping feature %s because geometry parsing failed: %s", feature_index, error)
        return None

    if geom.is_empty:
        logger.warning("Skipping feature %s because geometry is empty.", feature_index)
        return None

    if not geom.is_valid:
        # buffer(0) is a common pragmatic fix for minor polygon self-intersection.
        fixed_geom = geom.buffer(0)
        if fixed_geom.is_empty or not fixed_geom.is_valid:
            logger.warning("Skipping feature %s because geometry is invalid.", feature_index)
            return None
        geom = fixed_geom

    return UtilityFeature(
        feature_index=feature_index,
        geometry=geom,
        prepared_geometry=prep(geom),
        properties=properties,
    )


# -----------------------------------------------------------------------------
# Transform and spatial indexing
# -----------------------------------------------------------------------------

def build_utility_features(all_geojson: Dict[str, Any]) -> List[UtilityFeature]:
    """Build validated Shapely geometry objects once at startup."""
    utility_features: List[UtilityFeature] = []

    for index, raw_feature in enumerate(all_geojson.get("features", [])):
        feature = validate_geojson_feature(raw_feature, index)
        if feature:
            utility_features.append(feature)

    if not utility_features:
        raise ValueError("No valid utility geometries were available after validation.")

    return utility_features


def build_spatial_index(utility_features: Sequence[UtilityFeature]) -> Tuple[Optional[STRtree], Dict[int, int]]:
    """
    Build STRtree spatial index.

    Shapely 1 and 2 return different values from STRtree.query:
    - Shapely 1 may return geometry objects.
    - Shapely 2 returns integer indices.
    The lookup dictionary supports the Shapely 1 case.
    """
    geometries = [feature.geometry for feature in utility_features]

    try:
        tree = STRtree(geometries)
        id_to_feature_index = {id(geom): idx for idx, geom in enumerate(geometries)}
        logger.info("Built STRtree spatial index for %s utility geometries.", len(geometries))
        return tree, id_to_feature_index
    except Exception as error:
        logger.warning("STRtree spatial index could not be created; using fallback scan: %s", error)
        return None, {}


def candidate_features_for_point(point: Point) -> Iterable[UtilityFeature]:
    """Return spatially plausible features for a point."""
    if UTILITY_SPATIAL_INDEX is None:
        return UTILITY_FEATURES

    try:
        candidates = UTILITY_SPATIAL_INDEX.query(point)
    except Exception:
        return UTILITY_FEATURES

    resolved: List[UtilityFeature] = []

    for candidate in candidates:
        # Shapely 2 returns integer or numpy integer indices.
        if isinstance(candidate, (int,)):
            resolved.append(UTILITY_FEATURES[candidate])
            continue

        # Some environments return numpy integer scalars.
        if hasattr(candidate, "item"):
            try:
                possible_index = candidate.item()
                if isinstance(possible_index, int):
                    resolved.append(UTILITY_FEATURES[possible_index])
                    continue
            except Exception:
                pass

        # Shapely 1 returns geometry objects.
        feature_index = GEOMETRY_ID_TO_FEATURE_INDEX.get(id(candidate))
        if feature_index is not None:
            resolved.append(UTILITY_FEATURES[feature_index])

    return resolved if resolved else UTILITY_FEATURES


def find_utility_for_point(latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
    """
    Find the utility polygon containing or touching the selected point.

    Coordinates are stored in WGS84, so Shapely receives Point(longitude, latitude).
    """
    point = Point(longitude, latitude)

    for feature in candidate_features_for_point(point):
        prepared_geom = feature.prepared_geometry

        try:
            if prepared_geom.contains(point) or feature.geometry.touches(point):
                return feature.properties
        except Exception:
            continue

    return None


@lru_cache(maxsize=256)
def company_geojson(company_name: Optional[str]) -> Dict[str, Any]:
    """Build and cache a one-company GeoJSON FeatureCollection."""
    if not company_name:
        return {"type": "FeatureCollection", "features": []}

    features = [
        feature
        for feature in ALL_UTILITY_GEOJSON.get("features", [])
        if feature.get("properties", {}).get("COMPANY_NA") == company_name
    ]

    return {"type": "FeatureCollection", "features": features}


def all_companies() -> List[str]:
    """Return sorted company names."""
    return sorted(
        {
            feature.properties.get("COMPANY_NA")
            for feature in UTILITY_FEATURES
            if feature.properties.get("COMPANY_NA")
        }
    )


# -----------------------------------------------------------------------------
# Business logic
# -----------------------------------------------------------------------------

def classify_wind_risk(wind_speed: Optional[float]) -> str:
    if wind_speed is None:
        return "Unknown"
    if wind_speed >= 40:
        return "High"
    if wind_speed >= 25:
        return "Moderate"
    return "Low"


def classify_precipitation_risk(precipitation: Optional[float]) -> str:
    if precipitation is None:
        return "Unknown"
    if precipitation >= 1.0:
        return "High"
    if precipitation >= 0.25:
        return "Moderate"
    return "Low"


def classify_temperature_demand(temperature_f: Optional[float]) -> str:
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


def classify_overall_operational_risk(
    wind_risk: str,
    precipitation_risk: str,
    weather_code: Optional[int],
) -> str:
    """Combine risk signals into an executive-friendly operational risk category."""
    storm_codes = {95, 96, 99}
    heavy_precip_codes = {65, 67, 75, 82, 86}

    if wind_risk == "High" or precipitation_risk == "High" or weather_code in storm_codes:
        return "High"

    if wind_risk == "Moderate" or precipitation_risk == "Moderate" or weather_code in heavy_precip_codes:
        return "Moderate"

    return "Low"


def calculate_hdd_cdd(temperature_f: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if temperature_f is None:
        return None, None
    return max(65 - temperature_f, 0), max(temperature_f - 65, 0)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate geodetic distance in miles between two latitude/longitude points."""
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


def find_nearest_monitored_city(latitude: float, longitude: float) -> Dict[str, Any]:
    nearest: Optional[Dict[str, Any]] = None

    for location in MONITORED_LOCATIONS:
        distance = haversine_miles(
            latitude,
            longitude,
            location["latitude"],
            location["longitude"],
        )

        candidate = {**location, "distance_miles": distance}

        if nearest is None or distance < nearest["distance_miles"]:
            nearest = candidate

    if nearest is None:
        raise ValueError("No monitored locations are configured.")

    return nearest


def safe_value(value: Any, suffix: str = "", precision: int = 1) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}{suffix}"
    return f"{value}{suffix}"


def summarize_current_weather(data: Dict[str, Any]) -> Dict[str, Any]:
    current = data["current"]

    temperature = current.get("temperature_2m")
    wind_speed = current.get("wind_speed_10m")
    precipitation = current.get("precipitation")
    weather_code = current.get("weather_code")

    hdd, cdd = calculate_hdd_cdd(temperature)
    wind_risk = classify_wind_risk(wind_speed)
    precipitation_risk = classify_precipitation_risk(precipitation)
    demand_category = classify_temperature_demand(temperature)
    overall_risk = classify_overall_operational_risk(wind_risk, precipitation_risk, weather_code)

    return {
        "temperature": temperature,
        "wind_speed": wind_speed,
        "precipitation": precipitation,
        "weather_code": weather_code,
        "weather_description": WEATHER_CODE_LOOKUP.get(weather_code, "Unknown"),
        "hdd": hdd,
        "cdd": cdd,
        "wind_risk": wind_risk,
        "precipitation_risk": precipitation_risk,
        "demand_category": demand_category,
        "overall_risk": overall_risk,
    }


def daily_forecast_records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    daily = data["daily"]
    rows: List[Dict[str, Any]] = []

    for i, forecast_date in enumerate(daily.get("time", [])):
        temp_max = daily.get("temperature_2m_max", [None] * 7)[i]
        temp_min = daily.get("temperature_2m_min", [None] * 7)[i]
        precip = daily.get("precipitation_sum", [None] * 7)[i]
        wind_max = daily.get("wind_speed_10m_max", [None] * 7)[i]
        weather_code = daily.get("weather_code", [None] * 7)[i]

        average_temp = None
        if temp_max is not None and temp_min is not None:
            average_temp = (temp_max + temp_min) / 2

        hdd, cdd = calculate_hdd_cdd(average_temp)
        wind_risk = classify_wind_risk(wind_max)
        precip_risk = classify_precipitation_risk(precip)
        overall_risk = classify_overall_operational_risk(wind_risk, precip_risk, weather_code)

        rows.append(
            {
                "date": forecast_date,
                "temp_max_f": temp_max,
                "temp_min_f": temp_min,
                "precip_in": precip,
                "wind_max_mph": wind_max,
                "weather": WEATHER_CODE_LOOKUP.get(weather_code, "Unknown"),
                "hdd": hdd,
                "cdd": cdd,
                "risk": overall_risk,
            }
        )

    return rows


# -----------------------------------------------------------------------------
# Visualization helpers
# -----------------------------------------------------------------------------

def metric_card(label: str, value: str, tone: str = "neutral") -> html.Div:
    tone_styles = {
        "neutral": {"backgroundColor": "#f6f8fb", "borderColor": "#d9e0ea"},
        "low": {"backgroundColor": "#edf8ef", "borderColor": "#b8dfc0"},
        "moderate": {"backgroundColor": "#fff7e6", "borderColor": "#ffd488"},
        "high": {"backgroundColor": "#fff0f0", "borderColor": "#ffb3b3"},
    }
    style = tone_styles.get(tone, tone_styles["neutral"])

    return html.Div(
        style={
            **style,
            "border": f"1px solid {style['borderColor']}",
            "borderRadius": "12px",
            "padding": "12px",
            "marginBottom": "10px",
        },
        children=[
            html.Div(label, style={"fontWeight": "bold", "fontSize": "13px", "color": "#4a5568"}),
            html.Div(value, style={"fontSize": "17px", "color": "#1a202c", "marginTop": "4px"}),
        ],
    )


def risk_tone(risk: str) -> str:
    return {
        "Low": "low",
        "Moderate": "moderate",
        "High": "high",
    }.get(risk, "neutral")


def build_forecast_figure(records: List[Dict[str, Any]]) -> go.Figure:
    fig = go.Figure()

    dates = [row["date"] for row in records]

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=[row["temp_max_f"] for row in records],
            mode="lines+markers",
            name="Max Temp °F",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=[row["temp_min_f"] for row in records],
            mode="lines+markers",
            name="Min Temp °F",
        )
    )
    fig.add_trace(
        go.Bar(
            x=dates,
            y=[row["precip_in"] for row in records],
            name="Precipitation in",
            yaxis="y2",
            opacity=0.45,
        )
    )

    fig.update_layout(
        margin={"l": 40, "r": 40, "t": 30, "b": 40},
        height=320,
        legend={"orientation": "h", "y": 1.12},
        xaxis_title="Forecast Date",
        yaxis={"title": "Temperature °F"},
        yaxis2={
            "title": "Precipitation in",
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
    )

    return fig


def build_forecast_table(records: List[Dict[str, Any]]) -> dash_table.DataTable:
    table_rows = [
        {
            "Date": row["date"],
            "High": safe_value(row["temp_max_f"], "°F"),
            "Low": safe_value(row["temp_min_f"], "°F"),
            "Precip": safe_value(row["precip_in"], " in", precision=2),
            "Max Wind": safe_value(row["wind_max_mph"], " mph"),
            "Risk": row["risk"],
            "Weather": row["weather"],
        }
        for row in records
    ]

    return dash_table.DataTable(
        columns=[{"name": col, "id": col} for col in table_rows[0].keys()] if table_rows else [],
        data=table_rows,
        page_size=7,
        style_table={"overflowX": "auto"},
        style_cell={
            "fontFamily": "Arial, sans-serif",
            "fontSize": "13px",
            "padding": "8px",
            "textAlign": "left",
        },
        style_header={"fontWeight": "bold", "backgroundColor": "#f2f5fa"},
    )


def build_history_table(history: List[Dict[str, Any]]) -> dash_table.DataTable:
    return dash_table.DataTable(
        columns=[
            {"name": "Timestamp", "id": "timestamp"},
            {"name": "Latitude", "id": "latitude"},
            {"name": "Longitude", "id": "longitude"},
            {"name": "Utility", "id": "utility"},
            {"name": "Overall Risk", "id": "overall_risk"},
            {"name": "Temp °F", "id": "temperature"},
            {"name": "Wind mph", "id": "wind_speed"},
            {"name": "Precip in", "id": "precipitation"},
        ],
        data=history[-10:][::-1],
        page_size=10,
        style_table={"overflowX": "auto"},
        style_cell={
            "fontFamily": "Arial, sans-serif",
            "fontSize": "12px",
            "padding": "7px",
            "textAlign": "left",
            "maxWidth": "180px",
            "whiteSpace": "normal",
        },
        style_header={"fontWeight": "bold", "backgroundColor": "#f2f5fa"},
    )


def make_csv_download(history: List[Dict[str, Any]]) -> Dict[str, str]:
    output = io.StringIO()
    fieldnames = [
        "timestamp",
        "latitude",
        "longitude",
        "utility",
        "overall_risk",
        "temperature",
        "wind_speed",
        "precipitation",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(history)
    return {"content": output.getvalue(), "filename": "utility_weather_lookup_history.csv"}


# -----------------------------------------------------------------------------
# Initial data load
# -----------------------------------------------------------------------------

logger.info("Loading Kentucky electric utility service-area polygons...")
ALL_UTILITY_GEOJSON = fetch_all_utility_geojson()
UTILITY_FEATURES = build_utility_features(ALL_UTILITY_GEOJSON)
UTILITY_SPATIAL_INDEX, GEOMETRY_ID_TO_FEATURE_INDEX = build_spatial_index(UTILITY_FEATURES)
UTILITY_COMPANIES = all_companies()
logger.info("Loaded %s valid utility polygon features.", len(UTILITY_FEATURES))


# -----------------------------------------------------------------------------
# Dash app
# -----------------------------------------------------------------------------

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "Kentucky Utility Territory Weather Lookup"


CARD_STYLE = {
    "backgroundColor": "white",
    "padding": "20px",
    "borderRadius": "16px",
    "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
}

app.layout = html.Div(
    style={
        "fontFamily": "Arial, sans-serif",
        "backgroundColor": "#f5f7fb",
        "padding": "24px",
        "minHeight": "100vh",
    },
    children=[
        html.Div(
            style={**CARD_STYLE, "marginBottom": "18px"},
            children=[
                html.H1(
                    "Kentucky Utility Territory Weather Lookup",
                    style={"margin": "0 0 8px 0", "fontSize": "34px"},
                ),
                html.P(
                    "Click a point on the map or enter coordinates to identify the electric "
                    "utility service territory and retrieve current plus 7-day weather-driven "
                    "operational risk indicators.",
                    style={"color": "#555", "fontSize": "17px", "margin": "0"},
                ),
            ],
        ),
        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "410px 1fr",
                "gap": "18px",
            },
            children=[
                html.Div(
                    style={**CARD_STYLE, "maxHeight": "900px", "overflowY": "auto"},
                    children=[
                        html.Label("Utility Provider Filter", style={"fontWeight": "bold", "fontSize": "16px"}),
                        dcc.Dropdown(
                            id="utility-filter-dropdown",
                            options=[{"label": "All utilities", "value": "__ALL__"}]
                            + [{"label": company, "value": company} for company in UTILITY_COMPANIES],
                            value="__ALL__",
                            clearable=False,
                            style={"marginTop": "8px", "marginBottom": "18px"},
                        ),
                        html.Label("Matched Utility Provider", style={"fontWeight": "bold", "fontSize": "16px"}),
                        dcc.Dropdown(
                            id="matched-utility-dropdown",
                            options=[{"label": company, "value": company} for company in UTILITY_COMPANIES],
                            value=None,
                            placeholder="Click the map or run coordinate lookup",
                            clearable=True,
                            style={"marginTop": "8px", "marginBottom": "18px"},
                        ),
                        html.Div(
                            style={
                                "display": "grid",
                                "gridTemplateColumns": "1fr 1fr",
                                "gap": "10px",
                                "marginBottom": "10px",
                            },
                            children=[
                                dcc.Input(
                                    id="manual-latitude",
                                    type="number",
                                    placeholder="Latitude",
                                    debounce=True,
                                    style={"width": "100%", "padding": "9px"},
                                ),
                                dcc.Input(
                                    id="manual-longitude",
                                    type="number",
                                    placeholder="Longitude",
                                    debounce=True,
                                    style={"width": "100%", "padding": "9px"},
                                ),
                            ],
                        ),
                        html.Button(
                            "Lookup Coordinate",
                            id="manual-lookup-button",
                            n_clicks=0,
                            style={
                                "width": "100%",
                                "padding": "10px",
                                "borderRadius": "10px",
                                "border": "0",
                                "backgroundColor": "#0057b8",
                                "color": "white",
                                "fontWeight": "bold",
                                "cursor": "pointer",
                                "marginBottom": "8px",
                            },
                        ),
                        html.Button(
                            "Clear Results",
                            id="clear-button",
                            n_clicks=0,
                            style={
                                "width": "100%",
                                "padding": "10px",
                                "borderRadius": "10px",
                                "border": "1px solid #cbd5e0",
                                "backgroundColor": "white",
                                "fontWeight": "bold",
                                "cursor": "pointer",
                                "marginBottom": "18px",
                            },
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
                                html.Div("Map Legend", style={"fontWeight": "bold", "marginBottom": "8px"}),
                                html.Div("Gray = utility service territories"),
                                html.Div("Blue = matched or filtered utility territory"),
                                html.Div("Red marker = selected coordinate"),
                            ],
                        ),
                        dcc.Loading(
                            type="circle",
                            children=[
                                html.H3("Selected Location"),
                                html.Div(id="click-output", children="Click a point on the map."),
                                html.Hr(),
                                html.H3("Nearest Monitored City"),
                                html.Div(
                                    id="nearest-city-output",
                                    children="Nearest city will appear after lookup.",
                                ),
                                html.Hr(),
                                html.H3("Current Weather & Utility Risk"),
                                html.Div(id="weather-output", children="Weather will appear after lookup."),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    children=[
                        html.Div(
                            style={**CARD_STYLE, "padding": "14px", "marginBottom": "18px"},
                            children=[
                                dl.Map(
                                    id="utility-map",
                                    center=KENTUCKY_CENTER,
                                    zoom=KENTUCKY_ZOOM,
                                    style={
                                        "height": "650px",
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
                                            id="highlight-utility-geojson",
                                            data={"type": "FeatureCollection", "features": []},
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
                        html.Div(
                            style={**CARD_STYLE, "marginBottom": "18px"},
                            children=[
                                html.H3("7-Day Weather Outlook", style={"marginTop": 0}),
                                dcc.Loading(
                                    type="circle",
                                    children=[
                                        dcc.Graph(id="forecast-chart", figure=go.Figure()),
                                        html.Div(id="forecast-table-output"),
                                    ],
                                ),
                            ],
                        ),
                        html.Div(
                            style=CARD_STYLE,
                            children=[
                                html.Div(
                                    style={
                                        "display": "flex",
                                        "justifyContent": "space-between",
                                        "alignItems": "center",
                                        "gap": "12px",
                                    },
                                    children=[
                                        html.H3("Lookup History", style={"marginTop": 0}),
                                        html.Button(
                                            "Download CSV",
                                            id="download-history-button",
                                            n_clicks=0,
                                            style={
                                                "padding": "8px 12px",
                                                "borderRadius": "8px",
                                                "border": "1px solid #cbd5e0",
                                                "backgroundColor": "white",
                                                "cursor": "pointer",
                                            },
                                        ),
                                        dcc.Download(id="history-download"),
                                    ],
                                ),
                                html.Div(id="history-table-output"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        dcc.Store(id="lookup-history-store", data=[]),
        dcc.Store(id="last-selected-coordinate-store", data=None),
    ],
)


# -----------------------------------------------------------------------------
# Callbacks
# -----------------------------------------------------------------------------

@app.callback(
    Output("matched-utility-dropdown", "value"),
    Output("highlight-utility-geojson", "data"),
    Output("click-output", "children"),
    Output("nearest-city-output", "children"),
    Output("weather-output", "children"),
    Output("marker-layer", "children"),
    Output("forecast-chart", "figure"),
    Output("forecast-table-output", "children"),
    Output("lookup-history-store", "data"),
    Output("last-selected-coordinate-store", "data"),
    Input("utility-map", "clickData"),
    Input("manual-lookup-button", "n_clicks"),
    Input("clear-button", "n_clicks"),
    State("manual-latitude", "value"),
    State("manual-longitude", "value"),
    State("lookup-history-store", "data"),
    prevent_initial_call=False,
)
def handle_location_lookup(
    click_data: Optional[Dict[str, Any]],
    manual_clicks: int,
    clear_clicks: int,
    manual_latitude: Optional[float],
    manual_longitude: Optional[float],
    history: Optional[List[Dict[str, Any]]],
):
    """
    Main point-first interaction.

    The coordinate determines:
    - matched utility provider
    - highlighted utility territory
    - nearest monitored city
    - current weather and 7-day forecast
    - risk metrics
    - lookup history
    """
    history = history or []

    empty_geojson = {"type": "FeatureCollection", "features": []}
    empty_figure = go.Figure()

    trigger = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else ""

    if trigger == "clear-button":
        return (
            None,
            empty_geojson,
            "Click a point on the map.",
            "Nearest city will appear after lookup.",
            "Weather will appear after lookup.",
            [],
            empty_figure,
            "",
            [],
            None,
        )

    if trigger == "manual-lookup-button":
        latitude, longitude, validation_messages = validate_coordinate(manual_latitude, manual_longitude)
    elif click_data:
        latitude = click_data["latlng"]["lat"]
        longitude = click_data["latlng"]["lng"]
        latitude, longitude, validation_messages = validate_coordinate(latitude, longitude)
    else:
        return (
            None,
            empty_geojson,
            "Click a point on the map or enter coordinates.",
            "Nearest city will appear after lookup.",
            "Weather will appear after lookup.",
            [],
            empty_figure,
            "",
            history,
            None,
        )

    if latitude is None or longitude is None:
        error_panel = html.Div([metric_card("Coordinate Error", " ".join(validation_messages), "high")])
        return (
            None,
            empty_geojson,
            error_panel,
            "Nearest city will appear after a valid lookup.",
            "Weather will appear after a valid lookup.",
            [],
            empty_figure,
            "",
            history,
            None,
        )

    marker = dl.Marker(
        position=[latitude, longitude],
        children=[
            dl.Tooltip("Selected coordinate"),
            dl.Popup(f"Lat: {latitude:.5f}, Lon: {longitude:.5f}"),
        ],
    )

    matched_properties = find_utility_for_point(latitude, longitude)

    if matched_properties:
        matched_company = matched_properties.get("COMPANY_NA")
        utility_id = str(matched_properties.get("UTIL_ID", "Unknown"))
        utility_type = str(matched_properties.get("UTILITY_TY", "Unknown"))
        electric_type = matched_properties.get("ELEC_TYPE", "Unknown")
        service_class = matched_properties.get("CLASS", "Unknown")
        point_status = "Inside matched utility territory"
        highlight_geojson = company_geojson(matched_company)
    else:
        matched_company = None
        utility_id = "Not matched"
        utility_type = "Not matched"
        electric_type = "Not matched"
        service_class = "Not matched"
        point_status = "No Kentucky utility territory matched this point"
        highlight_geojson = empty_geojson

    validation_cards = [
        metric_card("Data Quality Warning", message, "moderate")
        for message in validation_messages
    ]

    click_message = html.Div(
        children=validation_cards
        + [
            metric_card("Latitude", f"{latitude:.5f}"),
            metric_card("Longitude", f"{longitude:.5f}"),
            metric_card("Point Status", point_status, "low" if matched_company else "moderate"),
            metric_card("Matched Utility", matched_company if matched_company else "No match found"),
            metric_card("Utility ID", utility_id),
            metric_card("Utility Type", utility_type),
            metric_card("Electric Type", electric_type),
            metric_card("Service Class", service_class),
        ]
    )

    nearest_city = find_nearest_monitored_city(latitude, longitude)

    nearest_city_message = html.Div(
        children=[
            metric_card("Nearest Monitored City", nearest_city["location_name"]),
            metric_card("State", nearest_city["state"]),
            metric_card("Distance", f"{nearest_city['distance_miles']:.2f} miles"),
        ]
    )

    forecast_figure = empty_figure
    forecast_table = ""
    history_record: Optional[Dict[str, Any]] = None

    try:
        weather_data = get_cached_weather(latitude, longitude)
        weather_summary = summarize_current_weather(weather_data)
        forecast_records = daily_forecast_records(weather_data)

        weather_message = html.Div(
            children=[
                metric_card(
                    "Overall Operational Risk",
                    weather_summary["overall_risk"],
                    risk_tone(weather_summary["overall_risk"]),
                ),
                metric_card("Temperature", safe_value(weather_summary["temperature"], "°F")),
                metric_card("Weather", weather_summary["weather_description"]),
                metric_card("Wind Speed", safe_value(weather_summary["wind_speed"], " mph")),
                metric_card("Precipitation", safe_value(weather_summary["precipitation"], " in", precision=2)),
                metric_card("Estimated Heating Degree Days", safe_value(weather_summary["hdd"])),
                metric_card("Estimated Cooling Degree Days", safe_value(weather_summary["cdd"])),
                metric_card("Demand Category", weather_summary["demand_category"]),
                metric_card(
                    "Wind Risk",
                    weather_summary["wind_risk"],
                    risk_tone(weather_summary["wind_risk"]),
                ),
                metric_card(
                    "Precipitation Risk",
                    weather_summary["precipitation_risk"],
                    risk_tone(weather_summary["precipitation_risk"]),
                ),
            ]
        )

        forecast_figure = build_forecast_figure(forecast_records)
        forecast_table = build_forecast_table(forecast_records)

        history_record = {
            "timestamp": weather_data.get("current", {}).get("time", ""),
            "latitude": f"{latitude:.5f}",
            "longitude": f"{longitude:.5f}",
            "utility": matched_company or "No match",
            "overall_risk": weather_summary["overall_risk"],
            "temperature": safe_value(weather_summary["temperature"]),
            "wind_speed": safe_value(weather_summary["wind_speed"]),
            "precipitation": safe_value(weather_summary["precipitation"], precision=2),
        }

    except Exception as error:
        logger.exception("Weather lookup failed.")
        weather_message = html.Div(
            [metric_card("Weather Lookup Failed", str(error), "high")]
        )

    if history_record:
        history = (history + [history_record])[-50:]

    return (
        matched_company,
        highlight_geojson,
        click_message,
        nearest_city_message,
        weather_message,
        [marker],
        forecast_figure,
        forecast_table,
        history,
        {"latitude": latitude, "longitude": longitude},
    )


@app.callback(
    Output("highlight-utility-geojson", "data", allow_duplicate=True),
    Input("utility-filter-dropdown", "value"),
    State("matched-utility-dropdown", "value"),
    prevent_initial_call=True,
)
def update_utility_filter(selected_company: str, matched_company: Optional[str]):
    """
    Allow users to browse one utility territory even before clicking the map.

    If the filter is reset to all utilities, keep the clicked match highlighted.
    """
    if selected_company and selected_company != "__ALL__":
        return company_geojson(selected_company)

    if matched_company:
        return company_geojson(matched_company)

    return {"type": "FeatureCollection", "features": []}


@app.callback(
    Output("history-table-output", "children"),
    Input("lookup-history-store", "data"),
)
def update_history_table(history: Optional[List[Dict[str, Any]]]):
    history = history or []
    if not history:
        return html.Div("No lookup history yet.", style={"color": "#555", "fontSize": "14px"})
    return build_history_table(history)


@app.callback(
    Output("history-download", "data"),
    Input("download-history-button", "n_clicks"),
    State("lookup-history-store", "data"),
    prevent_initial_call=True,
)
def download_history(n_clicks: int, history: Optional[List[Dict[str, Any]]]):
    history = history or []
    return make_csv_download(history)


# -----------------------------------------------------------------------------
# Run app
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=8053)
