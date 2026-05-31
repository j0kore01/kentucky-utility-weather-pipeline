"""
Week 3 Assignment — ETL Pipeline & Data Quality Engineering
Project: Kentucky Utility Weather & Demand Analytics Pipeline

Purpose:
This script extracts weather forecast data from the Open-Meteo API,
transforms it into an analytics-ready dataset, validates data quality,
saves the final dataset as a CSV, and loads the final dataset into
PostgreSQL/Neon when a DATABASE_URL environment variable is available.

Dashboard code is intentionally excluded because Week 3 focuses on the
ETL pipeline, data quality, and storage preparation. Power BI or Plotly Dash
will use this finalized dataset in a later project phase.

Pipeline Stages:
1. Extract weather forecast data from Open-Meteo
2. Store raw API results as CSV
3. Clean and normalize fields
4. Create derived utility metrics
5. Run validation and data quality checks
6. Store analytics-ready output as CSV
7. Load analytics-ready output to PostgreSQL/Neon
8. Log pipeline execution and errors

Database Note:
The Neon database contains supporting project tables:
- locations
- weather_codes
- weather_forecast
- utility_metrics
- fact_weather_daily

This Week 3 ETL script loads the final curated analytics dataset only into
fact_weather_daily. It does not overwrite the supporting reference/project
tables listed above.

Incremental Loading Strategy:
This Week 3 version uses a kill-and-fill strategy for fact_weather_daily.
Each successful run replaces the current fact_weather_daily table with the
newest validated forecast window. This prevents duplicate loads and keeps the
analytics table current for downstream dashboard use.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests
from sqlalchemy import create_engine


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
# These mirror the kind of location reference data stored in the Neon locations table.
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

# Weather code reference:
# A weather_codes lookup table also exists in Neon from the database schema work.
# For Week 3 reproducibility, this dictionary is included directly in the script
# so the submitted .py file can run end-to-end and create weather descriptions
# without depending on a separate preloaded reference table.
WEATHER_CODE_LOOKUP = {
    -1: "Unknown / Missing",
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
# Setup functions
# ---------------------------------------------------------

def create_project_folders() -> None:
    """
    Create required project folders if they do not already exist.

    This makes the script reproducible because it does not assume the
    data/raw, data/processed, or logs folders already exist.
    """
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging() -> None:
    """
    Configure logging to write messages to both a log file and the console.

    Logging is important for ETL pipelines because it creates an execution
    record that can be reviewed after the pipeline runs.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ],
        force=True
    )


def get_database_url() -> Optional[str]:
    """
    Retrieve the PostgreSQL database connection string from a local
    environment variable.

    In VS Code or Terminal, set this before running the script:

        export DATABASE_URL="postgresql://username:password@host/database?sslmode=require"

    The database URL is not stored in this script so that passwords are
    not exposed in the code or committed to GitHub.

    Returns:
        DATABASE_URL string if available, otherwise None.
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        logging.warning(
            "DATABASE_URL environment variable was not found. "
            "PostgreSQL load will be skipped, but CSV output will still be created."
        )
        return None

    return database_url


# ---------------------------------------------------------
# Extract
# ---------------------------------------------------------

def extract_weather_for_location(location: Dict) -> pd.DataFrame:
    """
    Extract daily weather forecast data for one Kentucky location
    from the Open-Meteo API.

    Args:
        location: Dictionary containing location_id, location_name, state,
                  latitude, and longitude.

    Returns:
        A pandas DataFrame containing raw daily forecast data for one location.
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

        # Timeout prevents the script from hanging forever if the API does not respond.
        response = requests.get(OPEN_METEO_URL, params=params, timeout=30)

        # API response validation: fail early if the API call was not successful.
        if response.status_code != 200:
            raise ValueError(
                f"API request failed for {location['location_name']} "
                f"with status code {response.status_code}: {response.text}"
            )

        data = response.json()

        # Schema validation on the raw API response.
        if "daily" not in data:
            raise ValueError(
                f"API response for {location['location_name']} does not contain 'daily' data."
            )

        daily = data["daily"]
        df = pd.DataFrame(daily)

        # Add location metadata so rows can be traced back to their geography.
        df["location_id"] = location["location_id"]
        df["location_name"] = location["location_name"]
        df["state"] = location["state"]
        df["latitude"] = location["latitude"]
        df["longitude"] = location["longitude"]

        # Add extraction timestamp for freshness/audit tracking.
        df["extracted_at"] = datetime.now().isoformat(timespec="seconds")

        logging.info(f"Extracted {len(df)} rows for {location['location_name']}.")
        return df

    except requests.exceptions.RequestException as error:
        logging.exception(
            f"Network/API error while extracting {location['location_name']}: {error}"
        )
        raise


def extract_all_weather_data() -> pd.DataFrame:
    """
    Extract weather data for all configured Kentucky locations.

    Returns:
        A combined raw DataFrame containing all configured locations.
    """
    all_location_frames: List[pd.DataFrame] = []

    for location in LOCATIONS:
        location_df = extract_weather_for_location(location)
        all_location_frames.append(location_df)

    # Append all location data into one raw dataset.
    raw_df = pd.concat(all_location_frames, ignore_index=True)

    # Store the raw extracted data before transformations.
    # This supports reproducibility and debugging.
    raw_df.to_csv(RAW_OUTPUT_FILE, index=False)

    logging.info(f"Saved raw extracted data to {RAW_OUTPUT_FILE}.")
    logging.info(f"Total raw rows extracted: {len(raw_df)}.")

    return raw_df


# ---------------------------------------------------------
# Transform
# ---------------------------------------------------------

def classify_wind_risk(wind_speed: float) -> str:
    """
    Classify wind risk for utility operations.

    These thresholds are simple business rules for operational awareness.
    """
    if pd.isna(wind_speed):
        return "Unknown"
    if wind_speed >= 40:
        return "High"
    if wind_speed >= 25:
        return "Moderate"
    return "Low"


def classify_precipitation_risk(precipitation: float) -> str:
    """
    Classify precipitation risk for utility operations.

    Higher precipitation may affect field work, flood awareness, or outage risk.
    """
    if pd.isna(precipitation):
        return "Unknown"
    if precipitation >= 1.0:
        return "High"
    if precipitation >= 0.25:
        return "Moderate"
    return "Low"


def classify_demand_category(avg_temp: float) -> str:
    """
    Classify likely utility demand conditions based on average temperature.

    This is a simplified operational category used for dashboard storytelling.
    """
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

    Transformation steps include:
    - Rename columns to business-friendly names
    - Trim whitespace in text fields
    - Convert dates and times
    - Convert numeric columns to numeric data types
    - Flag and fill missing temperature values
    - Flag and fill missing weather codes
    - Add weather code descriptions
    - Calculate average temperature, HDD, and CDD
    - Create risk and demand categories
    - Create a natural key for duplicate prevention
    """
    logging.info("Starting transformation process.")

    df = raw_df.copy()

    # Rename raw API fields into clearer analytics-friendly field names.
    df = df.rename(columns={
        "time": "forecast_date",
        "weather_code": "weather_code",
        "temperature_2m_max": "temperature_max_f",
        "temperature_2m_min": "temperature_min_f",
        "precipitation_sum": "precipitation_sum_in",
        "wind_speed_10m_max": "wind_speed_max_mph",
        "wind_gusts_10m_max": "wind_gust_max_mph",
    })

    # Clean and standardize text fields.
    # This prevents issues where "Louisville" and " Louisville " are treated differently.
    df["location_name"] = df["location_name"].astype(str).str.strip()
    df["state"] = df["state"].astype(str).str.strip().str.upper()

    # Convert forecast date into date format for grouping, joining, and dashboard filtering.
    df["forecast_date"] = pd.to_datetime(df["forecast_date"], errors="coerce").dt.date

    # Convert sunrise/sunset timestamps into 24-hour time format.
    # These are more readable and consistent for analytics displays.
    if "sunrise" in df.columns:
        df["sunrise_time"] = pd.to_datetime(df["sunrise"], errors="coerce").dt.strftime("%H:%M")
    else:
        df["sunrise_time"] = None

    if "sunset" in df.columns:
        df["sunset_time"] = pd.to_datetime(df["sunset"], errors="coerce").dt.strftime("%H:%M")
    else:
        df["sunset_time"] = None

    # Enforce numeric data types.
    # API values can sometimes arrive as strings, so this prevents calculation errors.
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

    # -----------------------------------------------------
    # Missing weather code handling
    # -----------------------------------------------------
    # Missing weather codes are flagged and filled with -1.
    # This preserves the row while making the quality issue visible.
    df["weather_code_missing_flag"] = df["weather_code"].isna()

    missing_weather_code_count = int(df["weather_code_missing_flag"].sum())

    if missing_weather_code_count > 0:
        logging.warning(
            f"Missing weather codes found in {missing_weather_code_count} records. "
            "Filling missing weather codes with -1 and labeling them as Unknown / Missing."
        )

    df["weather_code"] = df["weather_code"].fillna(-1).astype(int)

    # Map weather codes to descriptions for analytics use.
    df["weather_description"] = df["weather_code"].map(WEATHER_CODE_LOOKUP)

    # If a future API code appears that is not in the lookup, label it clearly.
    unknown_description_count = int(df["weather_description"].isna().sum())

    if unknown_description_count > 0:
        logging.warning(
            f"{unknown_description_count} records have weather codes not found in the lookup table. "
            "Labeling them as Unknown / Unmapped."
        )

        df["weather_description"] = df["weather_description"].fillna("Unknown / Unmapped")

    # -----------------------------------------------------
    # Missing temperature handling
    # -----------------------------------------------------
    # Missing temperature records are flagged before filling.
    # This preserves transparency: the dashboard/data model can still show that a value was imputed.
    df["temperature_missing_flag"] = (
        df["temperature_max_f"].isna() |
        df["temperature_min_f"].isna()
    )

    missing_temperature_count = int(df["temperature_missing_flag"].sum())

    if missing_temperature_count > 0:
        logging.warning(
            f"Missing temperature values found in {missing_temperature_count} records. "
            "Filling missing values using location-level average temperatures."
        )

        # Fill missing max/min temperatures using the average for the same location.
        # This is a practical Week 3 cleaning approach that avoids losing forecast rows.
        df["temperature_max_f"] = df.groupby("location_id")["temperature_max_f"].transform(
            lambda series: series.fillna(series.mean())
        )

        df["temperature_min_f"] = df.groupby("location_id")["temperature_min_f"].transform(
            lambda series: series.fillna(series.mean())
        )

        # Fallback: if an entire location were missing temperature values,
        # the location average would still be null. In that rare case, use the overall mean.
        df["temperature_max_f"] = df["temperature_max_f"].fillna(df["temperature_max_f"].mean())
        df["temperature_min_f"] = df["temperature_min_f"].fillna(df["temperature_min_f"].mean())

    # Derived metric: average daily temperature.
    df["temperature_avg_f"] = (df["temperature_max_f"] + df["temperature_min_f"]) / 2

    # Heating Degree Days and Cooling Degree Days use 65°F as the base temperature.
    # These are common utility demand indicators.
    df["heating_degree_days"] = (65 - df["temperature_avg_f"]).clip(lower=0)
    df["cooling_degree_days"] = (df["temperature_avg_f"] - 65).clip(lower=0)

    # Risk and demand categories convert numeric weather values into business-readable labels.
    df["wind_risk_category"] = df["wind_speed_max_mph"].apply(classify_wind_risk)
    df["precipitation_risk_category"] = df["precipitation_sum_in"].apply(classify_precipitation_risk)
    df["demand_category"] = df["temperature_avg_f"].apply(classify_demand_category)

    # Create a natural key to support duplicate prevention.
    # One forecast record should exist per location per forecast date in the current refresh.
    df["weather_natural_key"] = (
        df["location_id"].astype(str)
        + "_"
        + df["forecast_date"].astype(str)
    )

    # Add pipeline metadata for auditability and freshness checks.
    df["pipeline_run_timestamp"] = datetime.now().isoformat(timespec="seconds")

    # Select the final analytics-ready columns.
    # This keeps raw helper fields out of the curated output.
    final_columns = [
        "weather_natural_key",
        "location_id",
        "location_name",
        "state",
        "latitude",
        "longitude",
        "forecast_date",
        "weather_code",
        "weather_description",
        "weather_code_missing_flag",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
        "temperature_missing_flag",
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

    transformed_df = df[final_columns]

    logging.info(f"Transformation complete. Transformed row count: {len(transformed_df)}.")

    return transformed_df


# ---------------------------------------------------------
# Validate
# ---------------------------------------------------------

def validate_weather_data(df: pd.DataFrame, raw_row_count: int) -> bool:
    """
    Run data quality checks on the transformed dataset.

    Validation checks include:
    - Required columns are present
    - Dataset is not empty
    - Row count reconciles from raw to transformed data
    - Natural key is unique
    - Critical fields are not null
    - Numeric values are within expected ranges
    - Missing source values are flagged
    - Derived metrics are logically valid

    Raises:
        ValueError if a critical validation check fails.

    Returns:
        True if all validation checks pass.
    """
    logging.info("Starting data validation checks.")

    required_columns = [
        "weather_natural_key",
        "location_id",
        "location_name",
        "forecast_date",
        "weather_code",
        "weather_description",
        "weather_code_missing_flag",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
        "temperature_missing_flag",
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

    # Row count reconciliation confirms the transform did not accidentally lose rows.
    if len(df) != raw_row_count:
        raise ValueError(
            f"Validation failed. Row count mismatch. Raw rows: {raw_row_count}, "
            f"transformed rows: {len(df)}."
        )

    # Duplicate validation using the natural key.
    duplicate_count = df.duplicated(subset=["weather_natural_key"]).sum()
    if duplicate_count > 0:
        raise ValueError(
            f"Validation failed. Duplicate weather natural keys found: {duplicate_count}"
        )

    # Critical null validation after missing value handling.
    critical_null_columns = [
        "location_id",
        "location_name",
        "forecast_date",
        "weather_code",
        "weather_description",
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
    ]

    null_counts = df[critical_null_columns].isnull().sum()
    failed_null_checks = null_counts[null_counts > 0]

    if not failed_null_checks.empty:
        raise ValueError(
            f"Validation failed. Critical null values found: {failed_null_checks.to_dict()}"
        )

    # Range validation prevents invalid values from reaching dashboards.
    if (df["precipitation_sum_in"] < 0).any():
        raise ValueError("Validation failed. Precipitation cannot be negative.")

    if (df["wind_speed_max_mph"] < 0).any():
        raise ValueError("Validation failed. Wind speed cannot be negative.")

    if (df["heating_degree_days"] < 0).any():
        raise ValueError("Validation failed. Heating Degree Days cannot be negative.")

    if (df["cooling_degree_days"] < 0).any():
        raise ValueError("Validation failed. Cooling Degree Days cannot be negative.")

    # Weather code validation.
    # Missing weather codes are allowed only when they are clearly flagged and set to -1.
    if df["weather_code"].isnull().any():
        raise ValueError("Validation failed. Weather code still contains null values after cleaning.")

    invalid_negative_codes = df[
        (df["weather_code"] < 0) &
        (df["weather_code_missing_flag"] == False)
    ]

    if len(invalid_negative_codes) > 0:
        raise ValueError(
            "Validation failed. Negative weather codes found that were not marked as missing."
        )

    # Flag fields should be boolean values.
    if not df["weather_code_missing_flag"].isin([True, False]).all():
        raise ValueError("Validation failed. weather_code_missing_flag must contain boolean values.")

    if not df["temperature_missing_flag"].isin([True, False]).all():
        raise ValueError("Validation failed. temperature_missing_flag must contain boolean values.")

    # Informative success messages are useful for assignment review and debugging.
    logging.info("Validation passed: required columns are present.")
    logging.info("Validation passed: row counts match.")
    logging.info("Validation passed: no duplicate natural keys.")
    logging.info("Validation passed: no critical null values.")
    logging.info("Validation passed: range checks completed successfully.")
    logging.info("Validation passed: weather codes are present or clearly flagged as missing.")
    logging.info(
        f"Validation note: {int(df['temperature_missing_flag'].sum())} records had temperature values filled."
    )
    logging.info(
        f"Validation note: {int(df['weather_code_missing_flag'].sum())} records had weather codes filled as Unknown / Missing."
    )

    return True


# ---------------------------------------------------------
# Load / Store
# ---------------------------------------------------------

def save_processed_data(df: pd.DataFrame) -> None:
    """
    Save the transformed and validated dataset to CSV.

    This CSV serves as a sample output and as a backup analytics-ready file
    for downstream tools such as Power BI or Plotly Dash.
    """
    df.to_csv(PROCESSED_OUTPUT_FILE, index=False)
    logging.info(f"Processed analytics-ready data saved to {PROCESSED_OUTPUT_FILE}.")


def load_to_postgres(df: pd.DataFrame) -> None:
    """
    Load transformed weather data into PostgreSQL.

    Incremental loading strategy:
    This Week 3 pipeline uses a kill-and-fill strategy for the current
    forecast window. Because weather forecasts update frequently and the
    dataset is small, the PostgreSQL table is replaced with the newest
    validated data each time the script runs.

    This prevents duplicate loads and keeps the analytics table current
    for future Power BI or Plotly Dash use.

    Important:
    This function only writes to fact_weather_daily. It does not overwrite
    locations, weather_codes, weather_forecast, or utility_metrics.
    """
    database_url = get_database_url()

    if not database_url:
        logging.warning(
            "DATABASE_URL was not found. Skipping PostgreSQL load. "
            "CSV output was still created successfully."
        )
        return

    try:
        logging.info("Connecting to PostgreSQL database.")

        engine = create_engine(database_url)

        # Kill-and-fill load strategy:
        # Replace the current fact_weather_daily table each run so the database
        # contains only the latest validated forecast window and avoids duplicates.
        #
        # This affects only fact_weather_daily. Other Neon tables are not touched.
        df.to_sql(
            "fact_weather_daily",
            con=engine,
            if_exists="replace",
            index=False
        )

        logging.info(
            f"Loaded {len(df)} records to PostgreSQL table fact_weather_daily."
        )

    except Exception as error:
        logging.exception(f"PostgreSQL load failed: {error}")
        raise


# ---------------------------------------------------------
# Main execution
# ---------------------------------------------------------

def main() -> None:
    """
    Run the full ETL pipeline from extraction through storage.

    The order matters:
    1. Create folders
    2. Configure logging
    3. Extract raw data
    4. Transform and clean data
    5. Validate transformed data
    6. Save CSV output
    7. Load to PostgreSQL/Neon if configured
    """
    create_project_folders()
    setup_logging()

    try:
        logging.info("Starting Week 3 ETL pipeline.")

        raw_df = extract_all_weather_data()
        transformed_df = transform_weather_data(raw_df)

        validate_weather_data(transformed_df, raw_row_count=len(raw_df))

        save_processed_data(transformed_df)
        load_to_postgres(transformed_df)

        logging.info("Week 3 ETL pipeline completed successfully.")

    except Exception as error:
        logging.exception(f"Week 3 ETL pipeline failed: {error}")
        raise


if __name__ == "__main__":
    main()