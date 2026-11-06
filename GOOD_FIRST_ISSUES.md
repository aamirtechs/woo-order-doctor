# Good first issues

Seeded issues for new contributors. File each one on GitHub with the
`good first issue` and `help wanted` labels.

## 1. Accept a JSON orders export in addition to CSV

Title: Accept a JSON orders export in addition to CSV

Description: Some stores export orders as JSON, not CSV. Accept a
JSON file (a list of order objects, or an object with an `orders`
list) with the same fields (order_id, order_date, status, total,
billing_email, customer, line_items, line_item_total,
refund_total) when the input file ends in `.json`, and produce
exactly the same audit as the CSV path. The CSV behavior must not
change.

Difficulty: easy

Acceptance criteria:

* A JSON sample with the same orders as `examples/sample.csv`
  produces the same finding count and the same findings.
* A JSON row with a bad date exits 2 with the order id in the
  error, just like the CSV path.
* An empty JSON list produces a report with zero totals.
* A test covers the JSON path and the bad date case.
* The README documents the JSON fields and the `.json` detection.

## 2. Add a --severity option to filter the findings list

Title: Add --severity to show only errors or only warnings

Description: Large exports can produce long findings lists, and
operators triaging errors first want to hide the warnings. Add
`--severity error` (errors only) and `--severity warning`
(warnings only). The summary counts must still describe the full
audit, and only the findings list and the per-check table change.

Difficulty: easy

Acceptance criteria:

* `--severity error` shows only error findings in text and JSON.
* `--severity warning` shows only warning findings.
* Without `--severity`, the findings list is unchanged.
* An invalid `--severity` value exits 2 with a clear message.
* Tests cover both filters and the unchanged default.
* The README documents the new flag.

## 3. Report the worst offenders per check in text output

Title: Add a per-check worst-offender line to text output

Description: The per-check table shows counts, but an operator
wants to see the single biggest problem per check at a glance.
Add one line per check that has findings, naming the order id
with the largest money difference (line-item mismatch, negative
total, refunded amount) or the longest stuck age (stuck pending).
Checks without a money or age value just name the first order id.

Difficulty: medium

Acceptance criteria:

* A sample with a mismatch and a stuck order shows both worst
  offenders with their values.
* A sample with no findings shows no worst-offender lines.
* The JSON output is unchanged by this issue.
* Tests cover the ordering and the no-findings case.
* The README documents the new section.

## 4. Support WooCommerce HPOS date and time columns

Title: Support separate date and time columns from HPOS exports

Description: Some WooCommerce HPOS exports split the order date
into separate `Date` and `Time` columns instead of one datetime
column. Accept an optional `Time` column and combine it with the
date column when both are present, so the stuck-pending age is
still computed from the order date. The single-column behavior
must not change.

Difficulty: medium

Acceptance criteria:

* An export with separate `Date` and `Time` columns loads with
  the same dates as the combined form.
* A `Time` column with a bad value exits 2 with the order id in
  the error.
* An export without a `Time` column behaves exactly as before.
* Tests cover the combined columns and the bad time case.
* The README documents the `Time` column.
