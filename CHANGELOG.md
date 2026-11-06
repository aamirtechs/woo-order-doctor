# Changelog

## 1.0.0 - 2026-11-06

Initial release.

* Reads a WooCommerce orders CSV export (header row, common
  column-name variants accepted) with order id, order date,
  status, and total, plus optional billing email, customer,
  line items, line item total, and refund total.
* Eight checks: duplicate order ids, line-item sums that do not
  match the order total, paid orders with no billing email,
  refunded orders still marked completed, zero-value completed
  orders, negative totals, orders stuck pending or on-hold past
  N days, and unparseable line items cells.
* Decimal cents math for all amounts, so float errors never leak
  into a report.
* Text and `--json` output, `--check` CI gate (exit 1 on any
  error finding, `--strict` also fails on warnings),
  `--pending-days`, `--tolerance`, and `--status` filters, and
  file or stdin input.
* Clear exit 2 errors on stderr for missing files, missing
  required columns, and rows that fail validation.
* Zero dependencies, Python 3.10 or newer.
