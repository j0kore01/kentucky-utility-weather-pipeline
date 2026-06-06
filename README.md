# Kentucky Utility Weather & Demand Monitoring Pipeline

## Project Overview

This project builds an end-to-end data engineering and analytics workflow for monitoring Kentucky weather conditions and utility demand indicators.

The pipeline extracts weather forecast data from the Open-Meteo API, transforms and validates the data, loads an analytics-ready table into PostgreSQL/Neon, and supports a Dash dashboard for interactive analysis.

## Week 4 Dash MVP

The Week 4 deliverable is a functional Dash analytics application connected to PostgreSQL/Neon.

The dashboard uses the `fact_weather_daily` table created by the Week 3 ETL pipeline.

## Dashboard Features

The Dash MVP includes:

- PostgreSQL/Neon database connectivity
- Live/refreshed data pull from `fact_weather_daily`
- Location dropdown filter
- KPI summary cards
- Average temperature forecast trend
- Heating and Cooling Degree Days visualization
- Operational risk category summary
- Professional dashboard layout with readable labels and titles

## Business Purpose

The dashboard helps Kentucky utility planners monitor forecasted weather conditions that may affect:

- Electricity demand
- Heating and cooling load
- Field operations
- Wind-related operational risk
- Precipitation-related operational risk

Heating Degree Days and Cooling Degree Days are used as simplified demand indicators. Wind and precipitation categories provide operational awareness for utility planning.

## Repository Structure

```text
kentucky-utility-weather-pipeline/
├── data/
│   ├── raw/
│   ├── processed/
│   └── reference/
├── dashboard/
│   ├── app.py
│   ├── enhanced_utility_weather_app.py
│   ├── map_utility_lookup_demo.py
│   └── exploration/
├── docs/
│   └── screenshots/
├── logs/
├── week3_etl_pipeline.py
├── requirements.txt
└── README.md