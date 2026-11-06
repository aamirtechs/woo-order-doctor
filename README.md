# woo-order-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/woo-order-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/woo-order-doctor/issues)

A zero-dependency Python CLI that audits a WooCommerce orders CSV
export for common data problems, offline. It flags duplicate order
IDs, line-item sums that do not match the order total, paid orders
with no billing email, refunded orders still marked completed,
zero-value completed orders, negative totals, and orders stuck
pending past N days, and doubles as a CI gate with `--check`.

A WooCommerce export is usually eyeballed in a spreadsheet, and a
duplicated order id or a line-item total that is five dollars short
slips through unnoticed until a customer complains or a refund
goes to the wrong place. This tool reads the export, does the
money math with Decimal cents arithmetic, and prints every problem
in one list.

## Install

Requires Python 3.10 or newer. No dependencies beyond the standard library.

```bash
pip install woo-order-doctor
```

Or run it straight from the source checkout:

```bash
python3 woo_order_doctor.py examples/sample.csv
```

It also runs as a module:

```bash
python3 -m woo_order_doctor examples/sample.csv
```

## Input format

A CSV file with a header row. Column names are matched
case-insensitively, and common WooCommerce export variants are
accepted. Required columns:

| Field | Accepted headers |
| --- | --- |
| Order ID | `Order ID`, `Order Number`, `ID` |
| Order date | `Order Date`, `Date Created`, `Date` |
| Status | `Order Status`, `Status` |
| Total | `Order Total`, `Total`, `Order Grand Total` |

Optional columns:

| Field | Accepted headers |
| --- | --- |
| Billing email | `Billing Email`, `Email`, `Billing Email Address` |
| Customer | `Customer`, `Billing Name`, `Customer Name` |
| Line items | `Line Items`, `Items` |
| Line item total | `Line Item Total`, `Items Total` |
| Refund total | `Refund Total`, `Refund Amount`, `Total Refunded` |

Line items are written as a pipe-separated list, each item as
`Name x 2 @ 15.00` or `Name @ 15.00` (quantity 1). A semicolon
works as the separator too. When both `Line Items` and
`Line Item Total` are present, the item list is used; when only
`Line Item Total` is present, that amount is compared directly
against the order total.

Dates are ISO `YYYY-MM-DD`, optionally with a time part
(`YYYY-MM-DD HH:MM:SS`). Amounts may carry a currency symbol and
thousands commas, for example `$1,234.56`. Statuses are normalized
to lower case and a leading `wc-` prefix is stripped, so
`wc-completed` counts as `completed`.

## Checks

| Check | Severity | Rule |
| --- | --- | --- |
| duplicate order id | error | An order id appears more than once in the export |
| line item total mismatch | error | Line-item sum differs from the order total by more than `--tolerance` (default 0.01) |
| missing billing email | error | A paid order (`completed` or `processing`) with a positive total has no billing email |
| refunded but completed | error | Status `completed` with a refund total above zero |
| negative total | error | Order total below zero, whatever the status |
| zero value completed | warning | Status `completed` with a total of exactly zero |
| stuck pending | warning | Status `pending` or `on-hold` older than `--pending-days` (default 7) before the reference date |
| unparseable line items | warning | A line items cell cannot be parsed, so the sum check is skipped for that order |

Cancelled, refunded, and failed orders are not treated as paid, so
they never trigger the missing-email or zero-value checks. A
negative total is still flagged for any status.

## Usage

Audit an export (reference date defaults to today):

```bash
python3 woo_order_doctor.py examples/sample.csv
```

Pin the reference date so the report is reproducible:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06
```

Read from stdin (omit the argument, or pass `-`):

```bash
cat examples/sample.csv | python3 woo_order_doctor.py - --as-of 2026-11-06
```

Gate a job on it (exit 1 if any error finding exists, else exit 0):

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --check
```

Fail on warnings too:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --check --strict
```

Emit JSON instead of text:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --json
```

Only audit orders with one status:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --status completed
```

Change the stuck-pending threshold, or the mismatch tolerance:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --pending-days 14
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --tolerance 0.05
```

## Captured output: the sample audit

The sample file has 11 orders: one clean completed order, one
line-item mismatch, one paid order with no email, one refunded
order still marked completed, one zero-value completed order, one
negative total, one pending order 36 days old, one pending order
only 1 day old, a second row reusing order id 1002, one on-hold
order 66 days old, and one clean cancelled order.

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06
```

Captured output:

```
WooCommerce orders audit: examples/sample.csv
Reference date: 2026-11-06 (pending threshold: 7 days)

Orders: 11 total, gross total 364.46
Statuses: cancelled: 1, completed: 4, on-hold: 1, pending: 2, processing: 3

Findings: 8 (5 errors, 3 warnings)

Check                      Errors Warnings
duplicate order id              1        0
line item total mismatch        1        0
missing billing email           1        0
refunded but completed          1        0
negative total                  1        0
zero value completed            0        1
stuck pending                   0        2

Findings:
[error] order 1002: line items sum 40.00 but order total is 45.00 (difference -5.00)
[error] order 1003: paid order has no billing email
[error] order 1004: order was refunded 120.00 but is still marked completed
[warning] order 1005: completed order has a zero total
[error] order 1006: order total is negative: -15.00
[warning] order 1007: order has been pending for 36 days, over the 7 day threshold
[error] order 1002: order id 1002 appears more than once in the export
[warning] order 1009: order has been on-hold for 66 days, over the 7 day threshold
```

## Captured output: --check

The sample has 5 error findings, so the gate fails:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --check
```

Captured output (exit code 1, report above plus this last line):

```
CHECK FAILED: 5 error(s), 3 warning(s)
```

The clean sample has no findings, so the gate passes:

```bash
python3 woo_order_doctor.py examples/clean.csv --as-of 2026-11-06 --check
```

Captured output (exit code 0, report above plus this last line):

```
CHECK PASSED: no error findings
```

Note: `--check` fails on errors only. Add `--strict` to fail on
warnings too. A warnings-only export passes `--check` and fails
`--check --strict`.

## Captured output: --json

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --json
```

Captured output:

```
{
  "reference_date": "2026-11-06",
  "pending_days": 7,
  "total_orders": 11,
  "status_counts": {
    "completed": 4,
    "processing": 3,
    "pending": 2,
    "on-hold": 1,
    "cancelled": 1
  },
  "gross_total": "364.46",
  "error_count": 5,
  "warning_count": 3,
  "findings": [
    {
      "order_id": "1002",
      "check": "line item total mismatch",
      "severity": "error",
      "message": "line items sum 40.00 but order total is 45.00 (difference -5.00)"
    },
    {
      "order_id": "1003",
      "check": "missing billing email",
      "severity": "error",
      "message": "paid order has no billing email"
    },
    {
      "order_id": "1004",
      "check": "refunded but completed",
      "severity": "error",
      "message": "order was refunded 120.00 but is still marked completed"
    },
    {
      "order_id": "1005",
      "check": "zero value completed",
      "severity": "warning",
      "message": "completed order has a zero total"
    },
    {
      "order_id": "1006",
      "check": "negative total",
      "severity": "error",
      "message": "order total is negative: -15.00"
    },
    {
      "order_id": "1007",
      "check": "stuck pending",
      "severity": "warning",
      "message": "order has been pending for 36 days, over the 7 day threshold"
    },
    {
      "order_id": "1002",
      "check": "duplicate order id",
      "severity": "error",
      "message": "order id 1002 appears more than once in the export"
    },
    {
      "order_id": "1009",
      "check": "stuck pending",
      "severity": "warning",
      "message": "order has been on-hold for 66 days, over the 7 day threshold"
    }
  ]
}
```

## Captured output: --status

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --status completed
```

Captured output:

```
WooCommerce orders audit: examples/sample.csv
Reference date: 2026-11-06 (pending threshold: 7 days)

Orders: 4 total, gross total 204.98
Statuses: completed: 4

Findings: 3 (2 errors, 1 warnings)

Check                      Errors Warnings
missing billing email           1        0
refunded but completed          1        0
zero value completed            0        1

Findings:
[error] order 1003: paid order has no billing email
[error] order 1004: order was refunded 120.00 but is still marked completed
[warning] order 1005: completed order has a zero total
```

## Captured output: --pending-days

Raising the threshold to 40 days clears the 36-day pending order
but keeps the 66-day on-hold order:

```bash
python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06 --pending-days 40
```

Captured output (findings section only):

```
Findings: 7 (5 errors, 2 warnings)
...
[warning] order 1009: order has been on-hold for 66 days, over the 40 day threshold
```

## Captured output: bad input

`examples/invalid.csv` has an order date that is not an ISO date:

```bash
python3 woo_order_doctor.py examples/invalid.csv --as-of 2026-11-06
```

Captured output on stderr (exit code 2):

```
error: order 1001: order_date is not a valid ISO date YYYY-MM-DD: 'not-a-date'
```

## Options

| Flag | Meaning |
| --- | --- |
| `input` | CSV file path, or `-` / omitted for stdin |
| `--as-of` | Reference date YYYY-MM-DD (default: today) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error finding exists, else exit 0 |
| `--strict` | With `--check`, also exit 1 on warnings |
| `--pending-days` | Days before a pending or on-hold order counts as stuck (default: 7) |
| `--tolerance` | Allowed difference between line-item sum and order total (default: 0.01) |
| `--status` | Only audit orders with this status, for example completed |

Exit codes: 0 for a report (or a passed check), 1 for a failed
`--check`, 2 for bad input (missing file, missing required
columns, or a row that fails validation). Bad input errors go to
stderr.

## Edge cases

* Money is Decimal rounded to cents, so line items of 0.10 three
  times add up to exactly 0.30.
* A line-item sum within `--tolerance` of the order total passes,
  so a one-cent rounding difference in the export is not flagged.
* The stuck-pending boundary is strict: an order exactly
  `--pending-days` old is not stuck, one day older is.
* A duplicate order id is flagged on the second and later rows,
  not on the first occurrence.
* A missing billing email is only an error for paid orders with a
  positive total. Pending, on-hold, cancelled, refunded, and
  failed orders are not checked for an email.
* An unparseable line items cell is a warning and the sum check is
  skipped for that order, rather than failing the whole audit.
* Amounts with currency symbols and thousands commas
  (`$1,234.56`) are accepted. Order dates with a time part
  (`2026-11-02 10:30:00`) are accepted.
* A UTF-8 BOM at the start of the file is handled, and blank lines
  are skipped.

## Tests

```bash
python3 -m pytest tests/ -q
python3 -m unittest discover -s tests -v
```

45 tests, all passing: line-item parsing (quantities, single
items, semicolon separators, cents math, unparseable cells), money
parsing (currency symbols, commas, rounding), date parsing,
column aliases and missing-column errors, `wc-` status prefixes,
each of the eight checks with boundary cases, the sample totals
(11 orders, gross 364.46, 5 errors, 3 warnings), the clean sample,
plus CLI tests for text and JSON output, `--check` pass and fail,
`--strict`, `--status` filtering, `--pending-days`, stdin, module
run, and exit code 2 for bad input and bad option values.

## License

MIT. See [LICENSE](LICENSE).
