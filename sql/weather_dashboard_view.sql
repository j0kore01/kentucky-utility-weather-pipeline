-- Kentucky Utility Weather & Demand Monitoring Pipeline
-- SQL reference view for Week 5 final repository
--
-- Purpose:
-- This view demonstrates how the original normalized Week 2 tables can be joined
-- into a dashboard-ready analytics dataset.
--
-- Note:
-- The final Week 4 Dash MVP reads from fact_weather_daily, which is produced by
-- the Week 3 Python ETL pipeline. This SQL view is included as schema/reference
-- documentation to show how the normalized relational design supports analytics.

CREATE OR REPLACE VIEW vw_weather_dashboard_summary AS
SELECT
    wf.forecast_id,
    l.location_name,
    l.region,
    wf.forecast_date,
    wc.description AS weather_description,
    wf.temperature_max_f,
    wf.temperature_min_f,
    (wf.temperature_max_f + wf.temperature_min_f) / 2.0 AS temperature_avg_f,
    wf.precipitation_inches,
    wf.wind_speed_mph,
    wf.wind_gusts_mph,
    um.heating_degree_days,
    um.cooling_degree_days,
    um.wind_risk_category,
    um.precipitation_risk_category,
    um.demand_category
FROM weather_forecast wf
JOIN locations l
    ON wf.location_id = l.location_id
LEFT JOIN weather_codes wc
    ON wf.weather_code = wc.weather_code
LEFT JOIN utility_metrics um
    ON wf.forecast_id = um.forecast_id;