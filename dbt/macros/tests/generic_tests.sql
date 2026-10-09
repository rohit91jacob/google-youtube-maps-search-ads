{% test non_negative(model, column_name) %}
select {{ column_name }} from {{ model }} where {{ column_name }} < 0
{% endtest %}

{% test value_between(model, column_name, min_value, max_value) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }}
{% endtest %}

{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{% test not_empty(model) %}
select 1 as empty_model from (select count(*) as n from {{ model }}) as c where c.n = 0
{% endtest %}
