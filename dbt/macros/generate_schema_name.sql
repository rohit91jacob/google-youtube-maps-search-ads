{#- Use the configured schema name verbatim (staging / intermediate / marts / seeds) so the DuckDB
    schemas and the BigQuery datasets created by Terraform have the same names. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}{%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}
