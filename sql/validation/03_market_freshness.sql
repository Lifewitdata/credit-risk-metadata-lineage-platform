-- ============================================================================
-- 03_market_freshness.sql
--
-- Hand-run diagnostic for the daily closing-price dataset: is the feed late,
-- or is it just a weekend?
--
-- Run it in any MySQL client against the catalog database:
--     mysql> SOURCE sql/validation/03_market_freshness.sql
-- etl/load_market_prices.py executes this same file after a fetch and prints
-- the row, so the query you read here is the query that ran.
--
-- This is deliberately NOT the catalog's general check. metadata/data_quality.py
-- covers every registered dataset and cadence from the registry; this file
-- answers one support question about one table, at a depth a generic check does
-- not bother with: which weekdays inside the recent window have no row at all.
--
-- Requires MySQL 8.0 (recursive CTE). Trading days are approximated by
-- Monday-Friday plus the holiday rows the source itself carries. There is no
-- exchange calendar in this project, so docs/support_runbook.md is what turns a
-- surprising number here into a decision.
-- ============================================================================

WITH RECURSIVE bounds AS (
    SELECT
        MAX(o.trading_day)                                   AS last_day,
        COUNT(o.trading_day)                                 AS observations,
        GREATEST(
            CAST(COALESCE(MAX(o.trading_day), CURDATE()) AS DATE) - INTERVAL 42 DAY,
            CAST(COALESCE(MIN(o.trading_day), CURDATE()) AS DATE),
            CURDATE() - INTERVAL 120 DAY
        )                                                    AS window_start
    FROM raw_market_close_prices_daily o
),
-- One row per date from the window to today, so the date arithmetic below can
-- see gaps instead of only endpoints. Bounded at about 120 dates by the
-- window_start above, so it stays well under cte_max_recursion_depth even when
-- the feed has been down for years.
spine AS (
    SELECT b.window_start AS calendar_day FROM bounds b
    UNION ALL
    SELECT CAST(sp.calendar_day + INTERVAL 1 DAY AS DATE)
    FROM spine sp
    WHERE sp.calendar_day < CURDATE()
),
-- trading_day is unique in the loaded table (verified against the source), so
-- this join is one spine row to at most one observation.
windowed AS (
    SELECT
        b.last_day,
        b.observations,
        MAX(CASE WHEN o.close_value IS NOT NULL THEN o.trading_day END) AS last_priced_day,
        SUM(CASE WHEN o.trading_day IS NOT NULL THEN 1 ELSE 0 END)      AS rows_in_window,
        SUM(CASE WHEN o.trading_day IS NOT NULL AND o.close_value IS NULL
                 THEN 1 ELSE 0 END)                           AS holiday_rows_in_window,
        SUM(CASE WHEN b.last_day IS NOT NULL
                  AND s.calendar_day <= b.last_day
                  AND WEEKDAY(s.calendar_day) < 5
                  AND o.trading_day IS NULL
                 THEN 1 ELSE 0 END)                           AS weekdays_without_row
    FROM spine s
    CROSS JOIN bounds b
    LEFT JOIN raw_market_close_prices_daily o ON o.trading_day = s.calendar_day
    GROUP BY b.last_day, b.observations
),
aged AS (
    SELECT w.last_day, w.observations, w.last_priced_day, w.rows_in_window,
           w.holiday_rows_in_window, w.weekdays_without_row,
           SUM(CASE WHEN s.calendar_day > w.last_priced_day
                     AND WEEKDAY(s.calendar_day) < 5
                    THEN 1 ELSE 0 END)                        AS trading_days_since_last_price
    FROM windowed w
    CROSS JOIN spine s
    GROUP BY w.last_day, w.observations, w.last_priced_day, w.rows_in_window,
             w.holiday_rows_in_window, w.weekdays_without_row
)
SELECT
    d.dataset_name,
    d.refresh_frequency,
    a.last_day                                               AS last_row,
    a.last_priced_day,
    DATEDIFF(CURDATE(), a.last_day)                          AS calendar_days_since_last_row,
    a.trading_days_since_last_price,
    2                                                        AS tolerance_trading_days,
    CASE
        WHEN a.last_priced_day IS NULL THEN 'no_data'
        WHEN a.trading_days_since_last_price > 2 THEN 'stale'
        ELSE 'current'
    END                                                      AS verdict,
    a.observations                                           AS total_observations,
    a.rows_in_window,
    a.holiday_rows_in_window,
    a.weekdays_without_row                                   AS weekdays_without_row_in_window,
    'The source keeps a row for every day it covers and leaves close_value blank on '
    'an exchange holiday, so last_row can be a holiday while last_priced_day is the '
    'Friday before it. The verdict uses last_priced_day, counted Monday to Friday: a '
    'Friday close read on the Tuesday is 2, not 4. holiday_rows_in_window is expected '
    'and is not a missing price; weekdays_without_row_in_window is the one to chase.' AS how_to_read
FROM aged a
JOIN datasets d ON d.physical_table_name = 'raw_market_close_prices_daily';
