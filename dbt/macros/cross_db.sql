{#- Small portability layer: every model compiles for DuckDB (local) and BigQuery (production). -#}

{% macro hours_between(start_ts, end_ts) -%}
    ({{ dbt.datediff(start_ts, end_ts, 'second') }} / 3600.0)
{%- endmacro %}

{% macro days_between(start_date, end_date) -%}
    {{ dbt.datediff(start_date, end_date, 'day') }}
{%- endmacro %}

{% macro add_days(date_expr, days) -%}
    cast({{ dbt.dateadd('day', days, date_expr) }} as date)
{%- endmacro %}

{% macro safe_div(numerator, denominator) -%}
    case when {{ denominator }} is null or {{ denominator }} = 0 then null
         else {{ numerator }} / {{ denominator }} end
{%- endmacro %}

{% macro median(expr) -%}
    {{ return(adapter.dispatch('median', 'geco')(expr)) }}
{%- endmacro %}

{% macro default__median(expr) -%}
    median({{ expr }})
{%- endmacro %}

{% macro bigquery__median(expr) -%}
    approx_quantiles({{ expr }}, 100)[offset(50)]
{%- endmacro %}

{#- "Now" for relative windows. GECO_AS_OF freezes it so fixture runs are deterministic. -#}
{% macro as_of_ts() -%}
    {%- set as_of = env_var('GECO_AS_OF', '') -%}
    {%- if as_of -%}
        {{ return(adapter.dispatch('timestamp_literal', 'geco')(as_of)) }}
    {%- else -%}
        current_timestamp
    {%- endif -%}
{%- endmacro %}

{% macro default__timestamp_literal(value) -%}
    cast('{{ value }}' as timestamptz)
{%- endmacro %}

{% macro bigquery__timestamp_literal(value) -%}
    timestamp('{{ value }}')
{%- endmacro %}
