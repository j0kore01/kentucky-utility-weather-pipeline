"""
Week 4 Dash MVP — Kentucky Utility Weather & Demand Analytics Dashboard

Project:
Kentucky Utility Weather & Demand Analytics Pipeline

Purpose:
This Dash application connects to the Neon/PostgreSQL database and reads from
fact_weather_daily, the curated analytics table produced by the Week 3 ETL
pipeline.

The dashboard provides utility-focused weather and demand insights using:
- Location filtering
- KPI summary cards
- Temperature trend visualization
- Heating/Cooling Degree Days visualization
- Weather and operational risk summaries

Database Design Note:
The database includes supporting tables such as locations, weather_codes,
weather_forecast, and utility_metrics. The Week 3 ETL pipeline materializes
the dashboard-ready output into fact_weather_daily so this MVP can query one
curated analytics table efficiently.

Security Note:
Do not hard-code the Neon/PostgreSQL connection string in this file.
Set DATABASE_URL in the terminal before running the app.

Example:
    export DATABASE_URL="postgresql://USERNAME:PASSWORD@HOST/DATABASE?sslmode=require"
    python3 dashboard/app.py
"""

import os

import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine

from dash import Dash, dcc, html, Input, Output


# ---------------------------------------------------------
# Database connection
# ---------------------------------------------------------

def get_database_url() -> str:
    """
    Retrieve the Neon/PostgreSQL connection string from an environment variable.

    Returns:
        Database connection string.

    Raises:
        ValueError if DATABASE_URL is not set.
    """
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is not set. "
            "Set DATABASE_URL before running the Dash app."
        )

    return database_url


def load_weather_data() -> pd.DataFrame:
    """
    Load dashboard-ready data from the fact_weather_daily table.

    fact_weather_daily is the curated analytics table created by the Week 3
    ETL pipeline. It already includes location attributes, weather forecast
    fields, weather descriptions, and derived utility metrics.
    """
    database_url = get_database_url()
    engine = create_engine(database_url)

    query = """
        SELECT
            weather_natural_key,
            location_id,
            location_name,
            state,
            latitude,
            longitude,
            forecast_date,
            weather_code,
            weather_description,
            weather_code_missing_flag,
            temperature_max_f,
            temperature_min_f,
            temperature_avg_f,
            temperature_missing_flag,
            precipitation_sum_in,
            wind_speed_max_mph,
            wind_gust_max_mph,
            sunrise_time,
            sunset_time,
            heating_degree_days,
            cooling_degree_days,
            wind_risk_category,
            precipitation_risk_category,
            demand_category,
            extracted_at,
            pipeline_run_timestamp
        FROM fact_weather_daily
        ORDER BY forecast_date, location_name;
    """

    df = pd.read_sql(query, engine)

    # Ensure date fields are chart-ready.
    df["forecast_date"] = pd.to_datetime(df["forecast_date"])

    # Ensure numeric fields are chart-ready.
    numeric_columns = [
        "temperature_max_f",
        "temperature_min_f",
        "temperature_avg_f",
        "precipitation_sum_in",
        "wind_speed_max_mph",
        "wind_gust_max_mph",
        "heating_degree_days",
        "cooling_degree_days",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    return df


# Load the database data when the app starts.
# For this MVP, this is acceptable because the dataset is small.
weather_df = load_weather_data()


# ---------------------------------------------------------
# Dash app setup
# ---------------------------------------------------------

app = Dash(__name__)
app.title = "Kentucky Utility Weather Dashboard"


location_options = [{"label": "All Locations", "value": "All"}] + [
    {"label": location, "value": location}
    for location in sorted(weather_df["location_name"].dropna().unique())
]


# ---------------------------------------------------------
# Layout
# ---------------------------------------------------------

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
                "borderRadius": "14px",
                "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                "marginBottom": "20px",
            },
            children=[
                html.H1(
                    "Kentucky Utility Weather & Demand Monitoring Dashboard",
                    style={
                        "margin": "0 0 8px 0",
                        "fontSize": "30px",
                    },
                ),
                html.P(
                    "MVP Dash analytics application connected to Neon/PostgreSQL. "
                    "The dashboard uses the curated fact_weather_daily table from "
                    "the Week 3 ETL pipeline to support utility weather planning.",
                    style={
                        "color": "#555",
                        "fontSize": "16px",
                        "margin": "0",
                    },
                ),
            ],
        ),

        html.Div(
            style={
                "backgroundColor": "white",
                "padding": "20px",
                "borderRadius": "14px",
                "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                "marginBottom": "20px",
            },
            children=[
                html.Label(
                    "Filter by Kentucky Location",
                    style={
                        "fontWeight": "bold",
                        "display": "block",
                        "marginBottom": "8px",
                    },
                ),
                dcc.Dropdown(
                    id="location-filter",
                    options=location_options,
                    value="All",
                    clearable=False,
                ),
            ],
        ),

        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(4, 1fr)",
                "gap": "16px",
                "marginBottom": "20px",
            },
            children=[
                html.Div(
                    className="metric-card",
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H4("Forecast Records", style={"margin": "0", "color": "#555"}),
                        html.H2(id="record-count-card", style={"margin": "8px 0 0 0"}),
                    ],
                ),
                html.Div(
                    className="metric-card",
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H4("Avg Forecast Temp", style={"margin": "0", "color": "#555"}),
                        html.H2(id="avg-temp-card", style={"margin": "8px 0 0 0"}),
                    ],
                ),
                html.Div(
                    className="metric-card",
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H4("High Demand Days", style={"margin": "0", "color": "#555"}),
                        html.H2(id="high-demand-card", style={"margin": "8px 0 0 0"}),
                    ],
                ),
                html.Div(
                    className="metric-card",
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H4("Missing Source Flags", style={"margin": "0", "color": "#555"}),
                        html.H2(id="missing-flag-card", style={"margin": "8px 0 0 0"}),
                    ],
                ),
            ],
        ),

        html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr",
                "gap": "20px",
            },
            children=[
                html.Div(
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H3("Average Temperature Forecast Trend"),
                        html.P(
                            "Shows expected average daily temperature by forecast date. "
                            "Useful for anticipating utility demand changes.",
                            style={"color": "#666"},
                        ),
                        dcc.Graph(id="temperature-trend-chart"),
                    ],
                ),

                html.Div(
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H3("Heating and Cooling Degree Days"),
                        html.P(
                            "Compares heating and cooling demand indicators across the forecast window.",
                            style={"color": "#666"},
                        ),
                        dcc.Graph(id="degree-days-chart"),
                    ],
                ),

                html.Div(
                    style={
                        "backgroundColor": "white",
                        "padding": "20px",
                        "borderRadius": "14px",
                        "boxShadow": "0 2px 8px rgba(0,0,0,0.08)",
                    },
                    children=[
                        html.H3("Operational Risk Category Counts"),
                        html.P(
                            "Summarizes wind, precipitation, and demand risk categories for the selected location.",
                            style={"color": "#666"},
                        ),
                        dcc.Graph(id="risk-category-chart"),
                    ],
                ),
            ],
        ),
    ],
)


# ---------------------------------------------------------
# Callback / interactivity
# ---------------------------------------------------------

@app.callback(
    Output("record-count-card", "children"),
    Output("avg-temp-card", "children"),
    Output("high-demand-card", "children"),
    Output("missing-flag-card", "children"),
    Output("temperature-trend-chart", "figure"),
    Output("degree-days-chart", "figure"),
    Output("risk-category-chart", "figure"),
    Input("location-filter", "value"),
)
def update_dashboard(selected_location):
    """
    Update KPI cards and visualizations based on the selected location.
    """
    if selected_location == "All":
        filtered_df = weather_df.copy()
    else:
        filtered_df = weather_df[weather_df["location_name"] == selected_location].copy()

    record_count = len(filtered_df)

    avg_temp = filtered_df["temperature_avg_f"].mean()
    avg_temp_display = "N/A" if pd.isna(avg_temp) else f"{avg_temp:.1f}°F"

    high_demand_days = filtered_df[
        filtered_df["demand_category"].str.contains("High", na=False)
    ]["weather_natural_key"].nunique()

    missing_source_flags = int(
        filtered_df["temperature_missing_flag"].sum()
        + filtered_df["weather_code_missing_flag"].sum()
    )

    # Visualization 1: Temperature trend
    temperature_fig = px.line(
        filtered_df,
        x="forecast_date",
        y="temperature_avg_f",
        color="location_name",
        markers=True,
        title="Average Daily Forecast Temperature",
        labels={
            "forecast_date": "Forecast Date",
            "temperature_avg_f": "Average Temperature (°F)",
            "location_name": "Location",
        },
    )

    temperature_fig.update_layout(
        margin={"l": 40, "r": 20, "t": 60, "b": 40},
        hovermode="x unified",
    )

    # Visualization 2: Heating and cooling degree days
    degree_days_df = filtered_df.melt(
        id_vars=["forecast_date", "location_name"],
        value_vars=["heating_degree_days", "cooling_degree_days"],
        var_name="degree_day_type",
        value_name="degree_days",
    )

    degree_days_fig = px.bar(
        degree_days_df,
        x="forecast_date",
        y="degree_days",
        color="degree_day_type",
        barmode="group",
        title="Heating vs Cooling Degree Days",
        labels={
            "forecast_date": "Forecast Date",
            "degree_days": "Degree Days",
            "degree_day_type": "Metric",
        },
    )

    degree_days_fig.update_layout(
        margin={"l": 40, "r": 20, "t": 60, "b": 40},
        hovermode="x unified",
    )

    # Visualization 3: Operational risk summary
    risk_summary = pd.concat(
        [
            filtered_df["wind_risk_category"].rename("category"),
            filtered_df["precipitation_risk_category"].rename("category"),
            filtered_df["demand_category"].rename("category"),
        ],
        ignore_index=True,
    ).value_counts().reset_index()

    risk_summary.columns = ["category", "count"]

    risk_fig = px.bar(
        risk_summary,
        x="category",
        y="count",
        title="Operational Risk and Demand Category Counts",
        labels={
            "category": "Category",
            "count": "Forecast Day Count",
        },
    )

    risk_fig.update_layout(
        margin={"l": 40, "r": 20, "t": 60, "b": 80},
        xaxis_tickangle=-30,
    )

    return (
        f"{record_count}",
        avg_temp_display,
        f"{high_demand_days}",
        f"{missing_source_flags}",
        temperature_fig,
        degree_days_fig,
        risk_fig,
    )


# ---------------------------------------------------------
# Run app
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=8051)