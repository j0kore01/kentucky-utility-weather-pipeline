"""
Week 3 Assignment — ETL Pipeline & Data Quality Engineering
Project: Kentucky Utility Weather & Demand Analytics Pipeline

Purpose:
This script extracts weather forecast data from the Open-Meteo API,
transforms it into an analytics-ready dataset, validates data quality,
and saves the final dataset for downstream use in Power BI or Plotly Dash.

Dashboard code is intentionally excluded because Week 3 focuses on the
ETL pipeline, data quality, and storage preparation.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List

import pandas as pd
import requests


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RAW_DATA_DIR = os.path.join(BASE_DIR, "data", "raw")
PROCESSED_DATA_DIR = os.path.join(BASE_DIR, "data", "processed")
LOG_DIR = os.path.join(BASE_DIR, "logs")

RAW_OUTPUT_FILE = os.path.join(RAW_DATA_DIR, "open_meteo_raw.csv")
PROCESSED_OUTPUT_FILE = os.path.join(PROCESSED_DATA_DIR, "weather_daily_processed.csv")
LOG_FILE = os.path.join(LOG_DIR, "week3_etl_pipeline.log")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Kentucky utility-focused locations.
# You can add/remove locations later.
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
# Setup functions
# ---------------------------------------------------------

def create_project_folders() -> None:
    """Create required project folders if they do not already exist."""
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging() -> None:
    """Configure logging to write messages to both a log file and the console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )


# ---------------------------------------------------------
# Extract
# ---------------------------------------------------------

def extract_weather_for_location(location: Dict) -> pd.DataFrame:
    """
    Extract daily weather forecast data for one Kentucky location
    from the Open-Meteo API.
    """
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "daily": [
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "wind_speed_10m_max",
            "wind_gusts_10m_max",
            "sunrise",
            "sunset"
        ],
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
        "forecast_days": 16,
    }

    try:
        logging.info(f"Requesting weather data for {location['location_name']}.")
        response = requests.get(OPEN_METEO_URL, params=params, timeout=30)

        if response.status_code != 200:
            raise ValueError(
                f"API request failed for {location['location_name']} "
                f"with status code {response.status_code}: {response.text}"
            )

        data = response.json()

        if "daily" not in data:
            raise ValueError(f"API response for {location['location_name']} does not contain 'daily' data.")

        daily = data["daily"]
        df = pd.DataFrame(daily)

        df["location_id"] = location["location_id"]
        df["location_name"] = location["location_name"]
        df["state"] = location["state"]
        df["latitude"] = location["latitude"]
        df["longitude"] = location["longitude"]
        df["extracted_at"] = datetime.now().isoformat(timespec="seconds")

        logging.info(f"Extracted {len(df)} rows for {location['location_name']}.")
        return df

    except requests.exceptions.RequestException as error:
        logging.exception(f"Network/API error while extracting {location['location_name']}: {error}")
        raise


def extract_all_weather_data() -> pd.DataFrame:
    """Extract weather data for all configured Kentucky locations."""
    all_location_frames: List[pd.DataFrame] = []

    for location in LOCATIONS:
        location_df = extract_weather_for_location(location)
        all_location_frames.append(location_df)

    raw_df = pd.concat(all_location_frames, ignore_index=True)
    raw_df.to_csv(RAW_OUTPUT_FILE, index=False)

    logging.info(f"Saved raw extracted data to {RAW_OUTPUT_FILE}.")
    logging.info(f"Total raw rows extracted: {len(raw_df)}.")

    return raw_df


# ---------------------------------------------------------
# Transform
# ---------------------------------------------------------

def classify_wind_risk(wind_speed: float) -> str:
    """Classify wind risk for utility operations."""
    if pd.isna(wind_speed):
        return "Unknown"
    if wind_speed >= 40:
        return "High"
    if wind_speed >= 25:
        return "Moderate"
    return "Low"


def classify_precipitation_risk(precipitation: float) -> str:
    """Classify precipitation risk for utility operations."""
    if pd.isna(precipitation):
        return "Unknown"
    if precipitation >= 1.0:
        return "High"
    if precipitation >= 0.25:
        return "Moderate"
    return "Low"


def classify_demand_category(avg_temp: float) -> str:
    """Classify likely utility demand conditions based on average temperature."""
    if pd.isna(avg_temp):
        return "Unknown"
    if avg_temp < 32:
        return "High Heating Demand"
    if avg_temp < 50:
        return "Moderate Heating Demand"
    if avg_temp <= 70:
        return "Mild Demand"
    if avg_temp <= 85:
        return "Cooling Demand"
    return "High Cooling Demand"


def transform_weather_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and transform raw Open-Meteo daily forecast data into an
    analytics-ready weather dataset.
    """
    logging.info("Starting transformation process.")

    df = raw_df.copy()

    # Standardize column names from API names to business-friendly names.
    df = df.rename(columns={
        "time": "forecast_date",
        "weather_code": "weather_code",
        "temperature_2m_max": "temperature_max_f",
        "temperature_2m_min": "temperature_min_f",
        "precipitation_sum": "precipitation_sum_in",
        "wind_speed_10m_max": "wind_speed_max_mph",
        "wind_gusts_10m_max": "wind_gust_max_mph",
    })

    # Clean text fields.
    df["location_name"] = df["location_name"].astype(str).str.strip()
    df["state"] = df["state"].astype(str).str.strip().str.upper()

    # Convert forecast date into date format.
    df["forecast_date"] = pd.to_datetime(df["forecast_date"], errors="coerce").dt.date

    # Convert sunrise/sunset timestamps into 24-hour time format.
    if "sunrise" in df.columns:
        df["sunrise_time"] = pd.to_datetime(df["sunrise"], errors="coerce").dt.strftime("%H:%M")
    if "sunset" in df.columns:
        df["sunset_time"] = pd.to_datetime(df["sunset"], errors="coerce").dt.strftime("%H:%M")

    # Enforce numeric data types.
    numeric_columns = [
        "weather_code",
        "temperature_max_f",
        "temperature_min_f",
        "precipitation_sum_in",
        "wind_speed_max_mph",
        "wind_gust_max_mph",
        "latitude",
        "longitude",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # Derived metrics for utility analytics.
    df["temperature_avg_f"] = (df["temperature_max_f"] + df["temperature_min_f"]) / 2

    # Heating Degree Days and Cooling Degree Days use 65°F as the base temperature.
    df["heating_degree_days"] = (65 - df["temperature_avg_f"]).clip(lower=0)
    df["cooling_degree_days"] = (df["temperature_avg_f"] - 65).clip(lower=0)

    df["wind_risk_category"] = df["wind_speed_max_mph"].apply(classify_wind_risk)
    df["precipitation_risk_category"] = df["precipitation_sum_in"].apply(classify_precipitation_risk)
    df["demand_category"] = df["temperature_avg_f"].apply(classify_demand_category)

    # Create a natural key to help prevent duplicate loads.
    df["weather_natural_key"] = (
        df["location_id"].astype(str)
        + "_"
        + df["forecast_date"].astype(str)
    )

    # Add pipeline metadata.
    df["pipeline_run_timestamp"] = datetime.now().isoformat(timespec="seconds")

    # Select final analytics-ready columns.
    final_columns = [
        "weather_natural_key",
        "location_id",
        "location_name",
        "state",
        "latitude",
        "longitude",
        "forecast_date",
        "weather_code",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
        "precipitation_sum_in",
        "wind_speed_max_mph",
        "wind_gust_max_mph",
        "sunrise_time",
        "sunset_time",
        "heating_degree_days",
        "cooling_degree_days",
        "wind_risk_category",
        "precipitation_risk_category",
        "demand_category",
        "extracted_at",
        "pipeline_run_timestamp",
    ]

    final_columns = [column for column in final_columns if column in df.columns]
    transformed_df = df[final_columns]

    logging.info(f"Transformation complete. Transformed row count: {len(transformed_df)}.")

    return transformed_df


# ---------------------------------------------------------
# Validate
# ---------------------------------------------------------

def validate_weather_data(df: pd.DataFrame, raw_row_count: int) -> bool:
    """
    Run data quality checks on the transformed dataset.
    Raises an error if a critical validation check fails.
    """
    logging.info("Starting data validation checks.")

    required_columns = [
        "weather_natural_key",
        "location_id",
        "location_name",
        "forecast_date",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
        "precipitation_sum_in",
        "wind_speed_max_mph",
        "heating_degree_days",
        "cooling_degree_days",
        "wind_risk_category",
        "precipitation_risk_category",
        "demand_category",
    ]

    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Validation failed. Missing required columns: {missing_columns}")

    if df.empty:
        raise ValueError("Validation failed. Transformed dataset is empty.")

    if len(df) != raw_row_count:
        raise ValueError(
            f"Validation failed. Row count mismatch. Raw rows: {raw_row_count}, "
            f"transformed rows: {len(df)}."
        )

    duplicate_count = df.duplicated(subset=["weather_natural_key"]).sum()
    if duplicate_count > 0:
        raise ValueError(f"Validation failed. Duplicate weather natural keys found: {duplicate_count}")

    critical_null_columns = [
        "location_id",
        "location_name",
        "forecast_date",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
    ]

    null_counts = df[critical_null_columns].isnull().sum()
    failed_null_checks = null_counts[null_counts > 0]

    if not failed_null_checks.empty:
        raise ValueError(f"Validation failed. Critical null values found: {failed_null_checks.to_dict()}")

    if (df["precipitation_sum_in"] < 0).any():
        raise ValueError("Validation failed. Precipitation cannot be negative.")

    if (df["wind_speed_max_mph"] < 0).any():
        raise ValueError("Validation failed. Wind speed cannot be negative.")

    if (df["heating_degree_days"] < 0).any():
        raise ValueError("Validation failed. Heating Degree Days cannot be negative.")

    if (df["cooling_degree_days"] < 0).any():
        raise ValueError("Validation failed. Cooling Degree Days cannot be negative.")

    if not df["weather_code"].between(0, 100, inclusive="both").all():
        raise ValueError("Validation failed. Weather code outside expected range 0–100.")

    logging.info("Validation passed: required columns are present.")
    logging.info("Validation passed: row counts match.")
    logging.info("Validation passed: no duplicate natural keys.")
    logging.info("Validation passed: no critical null values.")
    logging.info("Validation passed: range checks completed successfully.")

    return True


# ---------------------------------------------------------
# Load / Store
# ---------------------------------------------------------

def save_processed_data(df: pd.DataFrame) -> None:
    """
    Save the transformed and validated dataset to CSV.

    Incremental loading strategy:
    For this Week 3 version, the project uses a kill-and-fill style approach
    for the current forecast window. Weather forecasts change frequently and
    the forecast window is small, so the script overwrites the processed CSV
    with the newest validated forecast dataset each time it runs.

    If loaded into PostgreSQL later, the same concept can be implemented by
    deleting existing records for the same location/date keys and inserting
    the refreshed records. This prevents duplicate loads while keeping the
    latest forecast data available for analytics.
    """
    df.to_csv(PROCESSED_OUTPUT_FILE, index=False)
    logging.info(f"Processed analytics-ready data saved to {PROCESSED_OUTPUT_FILE}.")


# ---------------------------------------------------------
# Main execution
# ---------------------------------------------------------

def main() -> None:
    """Run the full ETL pipeline from extraction through storage."""
    create_project_folders()
    setup_logging()

    try:
        logging.info("Starting Week 3 ETL pipeline.")

        raw_df = extract_all_weather_data()
        transformed_df = transform_weather_data(raw_df)
        validate_weather_data(transformed_df, raw_row_count=len(raw_df))
        save_processed_data(transformed_df)

        logging.info("Week 3 ETL pipeline completed successfully.")

    except Exception as error:
        logging.exception(f"Week 3 ETL pipeline failed: {error}")
        raise


if __name__ == "__main__":
    main()