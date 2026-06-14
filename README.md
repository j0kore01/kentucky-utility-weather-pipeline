# Kentucky Utility Weather & Demand Monitoring Pipeline

## Project Overview

This project builds an end-to-end data engineering and analytics workflow for monitoring Kentucky weather conditions and utility demand indicators.

The pipeline extracts weather forecast data from the Open-Meteo API, validates and transforms the data, loads an analytics-ready table into PostgreSQL/Neon, and supports a Dash dashboard for interactive analysis.

This project was completed for **MSBA 692: Pipelines to Insights** and demonstrates API ingestion, ETL pipeline design, validation, logging, relational database modeling, dashboard development, and executive communication.

 

## Business Problem

Weather conditions directly affect utility demand, infrastructure monitoring, and field operations. Temperature, precipitation, wind speed, and weather events can influence electricity usage patterns and operational risk.

This project addresses the following business question:

**How can Kentucky utility planners monitor forecasted weather conditions and translate them into actionable demand and operational risk indicators?**

The final dashboard helps users compare weather-driven demand and risk across selected Kentucky locations.

 

## Business Value

This project provides business value by:

- Converting raw weather forecast data into utility-relevant planning metrics
- Supporting location-based monitoring across Kentucky cities
- Calculating Heating Degree Days and Cooling Degree Days as demand indicators
- Categorizing wind and precipitation conditions into operational risk groups
- Creating a repeatable data pipeline instead of relying on manual reporting
- Providing an interactive dashboard for non-technical stakeholders

 

## Data Sources

### Primary Data Source

The primary data source is the **Open-Meteo Forecast API**.

The API provides daily forecast data by latitude and longitude. The project uses selected Kentucky locations as monitoring points for utility-focused weather analysis.

Forecast data includes:

- Forecast date
- Maximum temperature
- Minimum temperature
- Average temperature
- Precipitation
- Wind speed
- Wind gusts
- Weather code
- Sunrise and sunset times

API configuration includes:

- Temperature unit: Fahrenheit
- Wind speed unit: miles per hour
- Precipitation unit: inches
- Timezone: America/New_York

### Reference Data

The project also uses reference data for:

- Kentucky monitoring locations
- Latitude and longitude coordinates
- Open-Meteo weather code descriptions
- Exploratory Kentucky electric utility service area data

The GIS utility service area work is included as an exploratory future enhancement and is not required for the core dashboard MVP.

 

## Pipeline Architecture

The project follows the pattern:

```text
Extract → Validate → Transform → Load → Visualize
```

High-level architecture:

```text
Open-Meteo API
        ↓
Python ETL Pipeline
        ↓
Raw CSV Storage
        ↓
Validation Checks
        ↓
Transformation and Derived Metrics
        ↓
Processed CSV Storage
        ↓
Neon/PostgreSQL Database
        ↓
Dash Dashboard
```

The Week 3 ETL pipeline creates a curated analytics table named:

```text
fact_weather_daily
```

The Dash dashboard reads from this curated table to display KPI cards and visualizations.

 

## Repository Structure

```text
kentucky-utility-weather-pipeline/
│
├── README.md
├── requirements.txt
├── week3_etl_pipeline.py
│
├── dashboard/
│   ├── app.py
│   └── exploration/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── reference/
│
├── docs/
│   ├── proposal/
│   ├── data_source_plan/
│   ├── schema/
│   ├── screenshots/
│   ├── architecture/
│   └── presentation/
│
├── logs/
│
└── .gitignore
```

 

## Main Files and Folders

| File or Folder | Purpose |
| | |
| `week3_etl_pipeline.py` | Main ETL pipeline for extracting, validating, transforming, and loading weather data |
| `dashboard/app.py` | Main Dash application connected to PostgreSQL |
| `requirements.txt` | Python package dependencies |
| `README.md` | Project documentation and reproducibility instructions |
| `docs/proposal/` | Final project proposal |
| `docs/data_source_plan/` | Data source planning documentation |
| `docs/schema/` | ER diagram and schema documentation |
| `docs/screenshots/` | Dashboard screenshots |
| `logs/` | ETL pipeline log output |
| `data/` | Raw, processed, and reference data files |
| `dashboard/exploration/` | Exploratory GIS and utility service area enhancement scripts |

 

## Database Design

The initial relational schema included normalized supporting tables:

```text
locations
weather_codes
weather_forecast
utility_metrics
```

These tables separate location reference data, weather code reference data, forecast records, and derived utility metrics.

As the project evolved, the Week 3 ETL pipeline created a curated dashboard-ready fact table:

```text
fact_weather_daily
```

This table combines location attributes, weather forecast fields, weather descriptions, validation flags, and derived utility metrics.

The Dash dashboard queries `fact_weather_daily` directly to simplify the MVP application and avoid repeated joins in the visualization layer.

This approach separates the normalized database design from the curated analytics layer used for dashboard reporting.

 

## ETL Pipeline

The ETL pipeline is located in:

```text
week3_etl_pipeline.py
```

The pipeline performs the following steps.

### 1. Extract

The pipeline:

- Connects to the Open-Meteo Forecast API
- Requests daily weather forecast data for selected Kentucky locations
- Saves raw API output for traceability

### 2. Validate

The pipeline includes validation checks for:

- Required columns
- Row counts
- Duplicate natural keys
- Critical null values
- Temperature range checks
- Missing weather codes
- Missing temperature values

### 3. Transform

The transformation process creates utility-focused metrics including:

- Average daily temperature
- Heating Degree Days
- Cooling Degree Days
- Wind risk category
- Precipitation risk category
- Demand category
- Missing value flags
- Weather code descriptions
- Natural key for each location/date record

### 4. Load

The final curated dataset is loaded into Neon/PostgreSQL as:

```text
fact_weather_daily
```

The table is rebuilt during pipeline execution to avoid duplicate forecast-window records.

### 5. Log

The pipeline writes execution details to the `logs/` folder, including extraction counts, validation results, transformation notes, and load confirmation.

 

## Validation and Data Quality

The project includes a validation framework focused on reproducibility and data trust.

Validation checks include:

- Required schema fields
- Duplicate record detection
- Critical null checks
- Weather code handling
- Temperature range validation
- Missing value flags
- Row count checks
- Pipeline logging

Missing values are not silently ignored. The ETL pipeline flags missing source values and applies controlled handling where appropriate.

 

## Dashboard Application

The Dash application is located at:

```text
dashboard/app.py
```

The dashboard connects to Neon/PostgreSQL using the `DATABASE_URL` environment variable and reads from the `fact_weather_daily` table.

### Dashboard Features

The Dash MVP includes:

- PostgreSQL/Neon database connectivity
- Location dropdown filter
- KPI summary cards
- Average temperature forecast trend
- Heating and Cooling Degree Days visualization
- Operational risk category summary
- Professional dashboard layout with readable labels and titles

### Dashboard KPIs

The dashboard includes KPI cards for:

- Forecast Records
- Average Forecast Temperature
- High Demand Days
- Missing Source Flags

### Dashboard Visualizations

The dashboard includes:

- Average Temperature Forecast Trend
- Heating and Cooling Degree Days chart
- Operational Risk Category Counts

 

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/j0kore01/kentucky-utility-weather-pipeline.git
cd kentucky-utility-weather-pipeline
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Set Database Connection

The database credential is intentionally not included in the repository.

Set the `DATABASE_URL` environment variable before running the ETL pipeline or dashboard:

```bash
export DATABASE_URL="postgresql://USERNAME:PASSWORD@HOST/DATABASE?sslmode=require"
```

### 4. Run the ETL Pipeline

```bash
python3 week3_etl_pipeline.py
```

### 5. Run the Dash App

```bash
python3 dashboard/app.py
```

Then open the local Dash URL shown in the terminal, usually:

```text
http://127.0.0.1:8051/
```

 

## Security Note

The Neon/PostgreSQL database credential is not included in this repository.

The project uses an environment variable named:

```text
DATABASE_URL
```

This prevents database passwords from being committed to GitHub.

 

## Screenshots and Demo Materials

Dashboard screenshots are stored in:

```text
docs/screenshots/
```

The screenshot demonstrates the running Dash application with location filtering, KPI cards, forecast trend charts, Heating/Cooling Degree Days, and operational risk summaries.

 

## Project Documentation

Final project documentation is organized in the `docs/` folder.

| Folder | Contents |
| | |
| `docs/proposal/` | Project proposal |
| `docs/data_source_plan/` | Data source plan |
| `docs/schema/` | ER diagram and schema documentation |
| `docs/screenshots/` | Dashboard screenshots |
| `docs/architecture/` | Architecture diagram, if available |
| `docs/presentation/` | Final Week 5 executive presentation |

 

## Exploratory GIS Enhancement

The repository includes exploratory GIS scripts related to Kentucky electric utility service areas.

These scripts are located in:

```text
dashboard/exploration/
```

The GIS work explores how utility service territory data could be used to match Kentucky locations to electric providers. This is included as a future enhancement and is separate from the core Week 4 Dash MVP.

Potential future GIS improvements include:

- Mapping utility service territories
- Matching clicked map points to utility providers
- Adding PostGIS support
- Adding utility provider filters
- Joining weather risk metrics to service territories



## Challenges and Lessons Learned

Key challenges included:

- Moving from notebook-style development to reproducible Python scripts
- Managing PostgreSQL connection strings securely
- Handling missing API values
- Designing validation checks before loading data
- Organizing GitHub deliverables for a production-style repository
- Connecting Dash callbacks to database-backed data

Key lessons learned:

- Build a stable MVP before adding advanced features
- Keep credentials out of code and documentation
- Validate data before loading it into the database
- Use curated analytics tables to simplify dashboard applications
- Clear documentation is part of the final data product



## Future Enhancements

Potential future improvements include:

- Automating scheduled ETL refreshes
- Adding more Kentucky locations
- Expanding utility service territory mapping
- Adding PostGIS support for spatial analysis
- Creating a utility provider filter
- Adding weather alert data
- Deploying the Dash application to a production hosting environment
- Adding automated tests for validation functions



## Final Project Summary

This project demonstrates a complete end-to-end data engineering workflow:

```text
REST API ingestion
→ data validation
→ transformation
→ PostgreSQL loading
→ dashboard visualization
→ business communication
```

The final result is a reproducible Kentucky utility weather monitoring pipeline that turns raw forecast data into actionable demand and operational risk insights.