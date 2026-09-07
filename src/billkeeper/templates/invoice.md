---
title: "Invoice {{ invoice.number or invoice.draft_id }}"
date: "{{ invoice.issue_date or invoice.created }}"
---

# Invoice {{ invoice.number or invoice.draft_id }}

**From:** {{ issuer.name }}

**To:** {{ invoice.client.name }}

| Description | Quantity | Unit price |
|-------------|---------:|-----------:|
{% for item in invoice.items -%}
| {{ item.description }} | {{ item.quantity }} | {{ item.unit_price }} |
{% endfor %}
{% if issuer.payment_details %}
## How to pay

{{ issuer.payment_details }}
{% endif %}
