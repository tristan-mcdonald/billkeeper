{#-
  invoice.md — the Markdown billkeeper hands to Pandoc.

  This file is a copy, not a link: editing it changes this data repo's invoices
  and nothing else, and billkeeper will never write over it.

  Almost all of it is a YAML metadata block, because the layout lives next door
  in `invoice.typ` and everything that template prints has to reach it as
  Pandoc metadata. Below the closing `---` is the document body, which arrives
  in the Typst template as $body$ — by default the notes, so that a list or a
  bit of emphasis written in them comes out as a list or as emphasis.

  Every value is written as `|-`, a YAML block scalar. That way a name with a
  colon in it, an amount with a currency symbol, or a description with a quote
  mark needs no escaping and cannot break the file. Values are still read as
  Markdown, so `*rush job*` in a description comes out italic.

  Amounts arrive already written out — rounded to the currency's minor units
  and formatted for this repo's locale — so there is no arithmetic to do here:
  item.unit_price is a string like "€80.00", not a number.

  The whitespace control is load-bearing. Every optional block below opens with
  `{%- if ... %}` and starts its own first line with a newline, so a block that
  is skipped leaves no blank line and a block that is kept lands at the column
  YAML needs it at. Copy that shape if you add a field.
-#}
---
kind-label: |-
  {{ kind_label | indent(2) }}
number: |-
  {{ (number or "DRAFT") | indent(2) }}
draft: {{ "true" if draft else "false" }}
status: |-
  {{ status | indent(2) }}
currency: |-
  {{ currency | indent(2) }}
created: |-
  {{ created }}
{%- if issue_date %}
issue-date: |-
  {{ issue_date }}
{%- endif %}
{%- if due_date %}
due-date: |-
  {{ due_date }}
{%- endif %}
payment-terms-days: {{ payment_terms_days }}
issuer:
  name: |-
    {{ issuer.name | indent(4) }}
{%- if issuer.address %}
  address:
  {%- for line in issuer.address.splitlines() %}
    - |-
      {{ line | indent(6) }}
  {%- endfor %}
{%- endif %}
{%- if issuer.email %}
  email: |-
    {{ issuer.email | indent(4) }}
{%- endif %}
client:
  name: |-
    {{ client.name | indent(4) }}
{%- if client.address %}
  address:
  {%- for line in client.address.splitlines() %}
    - |-
      {{ line | indent(6) }}
  {%- endfor %}
{%- endif %}
{%- if client.contact %}
  contact: |-
    {{ client.contact | indent(4) }}
{%- endif %}
{%- if client.email %}
  email: |-
    {{ client.email | indent(4) }}
{%- endif %}
{%- if items %}
items:
  {%- for item in items %}
  - description: |-
      {{ item.description | indent(6) }}
    quantity: |-
      {{ item.quantity | indent(6) }}
    {%- if item.unit %}
    unit: |-
      {{ item.unit | indent(6) }}
    {%- endif %}
    unit-price: |-
      {{ item.unit_price | indent(6) }}
    amount: |-
      {{ item.line_total | indent(6) }}
  {%- endfor %}
{%- endif %}
subtotal: |-
  {{ subtotal | indent(2) }}
total: |-
  {{ total | indent(2) }}
{%- if issuer.payment_details %}
payment-details:
  {%- for line in issuer.payment_details.splitlines() %}
  - |-
    {{ line | indent(4) }}
  {%- endfor %}
{%- endif %}
{%- if references %}
references: |-
  {{ references | indent(2) }}
{%- endif %}
---
{%- if notes %}

## Notes

{{ notes }}
{%- endif %}
