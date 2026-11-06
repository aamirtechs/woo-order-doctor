"""woo-order-doctor: audit a WooCommerce orders CSV export.

Reads a WooCommerce orders CSV export and checks every order for
common data problems, offline:

* duplicate order IDs
* line-item sums that do not match the order total
* paid orders with no billing email
* refunded orders still marked completed
* zero-value completed orders
* negative order totals
* orders stuck pending past N days (default 7)

Money uses Decimal and is rounded to cents, so float errors never
leak into a report. Line-item sums use Decimal cents math too.

Input format: a CSV file with a header row. Column names are matched
case-insensitively, and common WooCommerce export variants are
accepted. Required columns:

    order_id: "Order ID", "Order Number", or "ID"
    order_date: "Order Date", "Date Created", or "Date"
    status: "Order Status" or "Status"
    total: "Order Total", "Total", or "Order Grand Total"

Optional columns:

    billing_email: "Billing Email", "Email", or "Billing Email Address"
    customer: "Customer", "Billing Name", or "Customer Name"
    line_items: "Line Items" or "Items", pipe separated, each item
        written as "Name x 2 @ 15.00" or "Name @ 15.00"
    line_item_total: "Line Item Total" or "Items Total", a single
        amount used when line_items is absent
    refund_total: "Refund Total", "Refund Amount", or "Total Refunded"

Dates are ISO "YYYY-MM-DD", optionally with a time part
("YYYY-MM-DD HH:MM:SS"). Amounts may carry a currency symbol and
thousands commas, for example "$1,234.56".

Rules:

* A paid order is one with status "completed" or "processing".
  A paid order with a positive total and no billing email is an
  error.
* An order with status "completed" and a refund_total above zero
  is an error: it was refunded but is still marked completed.
* An order with status "completed" and a total of exactly zero is
  a warning.
* A negative total is an error, whatever the status.
* An order with status "pending" or "on-hold" whose order date is
  more than --pending-days before the reference date is a warning.
* A duplicate order id is an error on the second and later rows.
* A line-item sum that differs from the order total by more than
  --tolerance (default 0.01) is an error. A line_items cell that
  cannot be parsed is a warning, and the sum check is skipped for
  that order.
* Statuses are normalized to lower case and a leading "wc-" prefix
  is stripped, so "wc-completed" counts as "completed".
* With --check, exit 1 when any error finding exists. With
  --strict, warnings fail the check too. Otherwise exit 0.
* Exit 2 means bad input: missing file, missing required columns,
  a row with a missing order id, a bad date, or a bad amount. The
  error goes to stderr.

Usage:

    python3 woo_order_doctor.py examples/sample.csv
    python3 woo_order_doctor.py examples/sample.csv --as-of 2026-11-06
    python3 woo_order_doctor.py examples/sample.csv --json
    python3 woo_order_doctor.py examples/sample.csv --check
    python3 woo_order_doctor.py examples/sample.csv --pending-days 14
    python3 woo_order_doctor.py examples/sample.csv --status completed
    cat examples/sample.csv | python3 woo_order_doctor.py -
"""

import argparse
import csv
import io
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__version__ = "1.0.0"

CENT = Decimal("0.01")
DEFAULT_PENDING_DAYS = 7
DEFAULT_TOLERANCE = Decimal("0.01")
PAID_STATUSES = {"completed", "processing"}
STUCK_STATUSES = {"pending", "on-hold"}
CURRENCY_CHARS = "$£€"

COLUMN_ALIASES = {
    "order_id": {"order id", "order number", "order no", "order_id", "id", "order"},
    "order_date": {"order date", "date created", "date", "created", "order_date", "date_created"},
    "status": {"order status", "status"},
    "billing_email": {"billing email", "billing email address", "email", "customer email", "billing_email"},
    "customer": {"customer", "billing name", "customer name"},
    "total": {"order total", "total", "order grand total", "grand total", "order_total"},
    "line_items": {"line items", "line item", "items", "line_items"},
    "line_item_total": {"line item total", "line items total", "items total", "line_item_total"},
    "refund_total": {"refund total", "refund amount", "total refunded", "refunded total", "refund_total"},
}

CHECKS = [
    "duplicate order id",
    "line item total mismatch",
    "missing billing email",
    "refunded but completed",
    "negative total",
    "zero value completed",
    "stuck pending",
    "unparseable line items",
]

ITEM_QTY_RE = re.compile(r"^(.*?)\s*x\s*(\d+)\s*@\s*(.+)$")
ITEM_SINGLE_RE = re.compile(r"^(.*?)\s*@\s*(.+)$")


class InputError(ValueError):
    """Raised for any bad input that should exit 2."""


def parse_iso_date(value, field, label):
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{label}: {field} must be an ISO date string, got {value!r}")
    text = value.strip()
    try:
        if " " in text or "T" in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        else:
            parsed = date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{label}: {field} is not a valid ISO date YYYY-MM-DD: {value!r}")
    if parsed.isoformat() not in (text, text.split(" ")[0], text.split("T")[0]):
        raise InputError(f"{label}: {field} is not a valid ISO date YYYY-MM-DD: {value!r}")
    return parsed


def parse_money(value, field, label):
    if isinstance(value, bool):
        raise InputError(f"{label}: {field} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise InputError(f"{label}: {field} is not a valid amount: {value!r}")
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        for symbol in CURRENCY_CHARS:
            text = text.replace(symbol, "")
        text = text.strip()
        if not text:
            raise InputError(f"{label}: {field} must not be empty")
        try:
            return Decimal(text).quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise InputError(f"{label}: {field} is not a valid amount: {value!r}")
    raise InputError(f"{label}: {field} must be a number, got {value!r}")


def parse_optional_money(value, field, label):
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return parse_money(value, field, label)


def normalize_status(raw):
    text = raw.strip().lower()
    if text.startswith("wc-"):
        text = text[3:]
    return text


def parse_line_items(cell, label):
    """Return the Decimal sum of a line_items cell, or None if unparseable."""
    if cell is None:
        return None
    text = cell.strip()
    if not text:
        return None
    separator = "|" if "|" in text else ";"
    total = Decimal("0.00")
    for segment in text.split(separator):
        segment = segment.strip()
        if not segment:
            continue
        match = ITEM_QTY_RE.match(segment)
        if match:
            quantity = int(match.group(2))
            price = parse_money(match.group(3), "line_items", label)
            total += price * quantity
            continue
        match = ITEM_SINGLE_RE.match(segment)
        if match:
            price = parse_money(match.group(2), "line_items", label)
            total += price
            continue
        return None
    return total.quantize(CENT, rounding=ROUND_HALF_UP)


def resolve_columns(fieldnames):
    if not fieldnames:
        raise InputError("CSV has no header row")
    lookup = {}
    for raw_name in fieldnames:
        if raw_name is None:
            continue
        lookup[raw_name.strip().lower()] = raw_name
    resolved = {}
    for logical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                resolved[logical] = lookup[alias]
                break
    missing = [name for name in ("order_id", "order_date", "status", "total")
               if name not in resolved]
    if missing:
        raise InputError(f"missing required column(s): {', '.join(missing)}")
    return resolved


def normalize_order(row, columns, index):
    label = f"row {index}"
    order_id = (row.get(columns["order_id"]) or "").strip()
    if not order_id:
        raise InputError(f"{label}: missing required field 'order_id'")
    label = f"order {order_id}"
    order_date = parse_iso_date(row.get(columns["order_date"]), "order_date", label)
    status_raw = row.get(columns["status"])
    if status_raw is None or not str(status_raw).strip():
        raise InputError(f"{label}: missing required field 'status'")
    status = normalize_status(str(status_raw))
    total = parse_money(row.get(columns["total"]), "total", label)
    email = ""
    if "billing_email" in columns:
        email = (row.get(columns["billing_email"]) or "").strip()
    customer = ""
    if "customer" in columns:
        customer = (row.get(columns["customer"]) or "").strip()
    refund_total = None
    if "refund_total" in columns:
        refund_total = parse_optional_money(row.get(columns["refund_total"]), "refund_total", label)
    line_items_sum = None
    line_items_unparseable = False
    if "line_items" in columns:
        cell = row.get(columns["line_items"])
        if cell is not None and cell.strip():
            try:
                line_items_sum = parse_line_items(cell, label)
            except InputError:
                line_items_unparseable = True
            if line_items_sum is None:
                line_items_unparseable = True
    if line_items_sum is None and "line_item_total" in columns:
        line_items_sum = parse_optional_money(
            row.get(columns["line_item_total"]), "line_item_total", label)
    return {
        "id": order_id,
        "order_date": order_date,
        "status": status,
        "total": total,
        "billing_email": email,
        "customer": customer,
        "refund_total": refund_total if refund_total is not None else Decimal("0.00"),
        "line_items_sum": line_items_sum,
        "line_items_unparseable": line_items_unparseable,
    }


def load_orders(text):
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        columns = resolve_columns(reader.fieldnames)
        orders = []
        for index, row in enumerate(reader, start=2):
            if row is None:
                continue
            if all(value is None or not str(value).strip()
                   for value in row.values()):
                continue
            orders.append(normalize_order(row, columns, index))
        return orders
    except csv.Error as exc:
        raise InputError(f"invalid CSV: {exc}")


def audit_orders(orders, reference, pending_days, tolerance):
    findings = []
    seen_ids = set()
    status_counts = {}
    for order in orders:
        status_counts[order["status"]] = status_counts.get(order["status"], 0) + 1
        order_id = order["id"]
        if order_id in seen_ids:
            findings.append({
                "order_id": order_id,
                "check": "duplicate order id",
                "severity": "error",
                "message": f"order id {order_id} appears more than once in the export",
            })
        else:
            seen_ids.add(order_id)
        if order["total"] < 0:
            findings.append({
                "order_id": order_id,
                "check": "negative total",
                "severity": "error",
                "message": f"order total is negative: {money_str(order['total'])}",
            })
        if order["status"] in PAID_STATUSES and order["total"] > 0 and not order["billing_email"]:
            findings.append({
                "order_id": order_id,
                "check": "missing billing email",
                "severity": "error",
                "message": "paid order has no billing email",
            })
        if order["status"] == "completed" and order["refund_total"] > 0:
            findings.append({
                "order_id": order_id,
                "check": "refunded but completed",
                "severity": "error",
                "message": f"order was refunded {money_str(order['refund_total'])} "
                           "but is still marked completed",
            })
        if order["status"] == "completed" and order["total"] == 0:
            findings.append({
                "order_id": order_id,
                "check": "zero value completed",
                "severity": "warning",
                "message": "completed order has a zero total",
            })
        if order["status"] in STUCK_STATUSES:
            age_days = (reference - order["order_date"]).days
            if age_days > pending_days:
                findings.append({
                    "order_id": order_id,
                    "check": "stuck pending",
                    "severity": "warning",
                    "message": f"order has been {order['status']} for {age_days} days, "
                               f"over the {pending_days} day threshold",
                })
        if order["line_items_unparseable"]:
            findings.append({
                "order_id": order_id,
                "check": "unparseable line items",
                "severity": "warning",
                "message": "line items cell could not be parsed, sum check skipped",
            })
        elif order["line_items_sum"] is not None and order["total"] >= 0:
            difference = order["line_items_sum"] - order["total"]
            if abs(difference) > tolerance:
                findings.append({
                    "order_id": order_id,
                    "check": "line item total mismatch",
                    "severity": "error",
                    "message": f"line items sum {money_str(order['line_items_sum'])} "
                               f"but order total is {money_str(order['total'])} "
                               f"(difference {money_str(difference)})",
                })
    errors = sum(1 for item in findings if item["severity"] == "error")
    warnings = sum(1 for item in findings if item["severity"] == "warning")
    gross_total = sum((order["total"] for order in orders), Decimal("0.00"))
    return {
        "reference_date": reference.isoformat(),
        "pending_days": pending_days,
        "total_orders": len(orders),
        "status_counts": status_counts,
        "gross_total": gross_total,
        "findings": findings,
        "error_count": errors,
        "warning_count": warnings,
    }


def money_str(amount):
    return f"{amount:.2f}"


def format_text(report, source):
    lines = []
    lines.append(f"WooCommerce orders audit: {source}")
    lines.append(f"Reference date: {report['reference_date']} "
                 f"(pending threshold: {report['pending_days']} days)")
    lines.append("")
    lines.append(f"Orders: {report['total_orders']} total, "
                 f"gross total {money_str(report['gross_total'])}")
    if report["status_counts"]:
        breakdown = ", ".join(
            f"{status}: {count}"
            for status, count in sorted(report["status_counts"].items()))
        lines.append(f"Statuses: {breakdown}")
    lines.append("")
    lines.append(f"Findings: {len(report['findings'])} "
                 f"({report['error_count']} errors, {report['warning_count']} warnings)")
    if report["findings"]:
        lines.append("")
        lines.append(f"{'Check':<26} {'Errors':>6} {'Warnings':>8}")
        for check in CHECKS:
            errors = sum(1 for item in report["findings"]
                         if item["check"] == check and item["severity"] == "error")
            warnings = sum(1 for item in report["findings"]
                           if item["check"] == check and item["severity"] == "warning")
            if errors or warnings:
                lines.append(f"{check:<26} {errors:>6} {warnings:>8}")
        lines.append("")
        lines.append("Findings:")
        for item in report["findings"]:
            lines.append(f"[{item['severity']}] order {item['order_id']}: {item['message']}")
    return "\n".join(lines) + "\n"


def report_to_json(report):
    return {
        "reference_date": report["reference_date"],
        "pending_days": report["pending_days"],
        "total_orders": report["total_orders"],
        "status_counts": report["status_counts"],
        "gross_total": money_str(report["gross_total"]),
        "error_count": report["error_count"],
        "warning_count": report["warning_count"],
        "findings": report["findings"],
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="woo-order-doctor",
        description="Audit a WooCommerce orders CSV export for common data problems.",
    )
    parser.add_argument("input", nargs="?", default=None,
                        help="CSV file path, or '-' / omitted for stdin")
    parser.add_argument("--as-of", default=None,
                        help="Reference date YYYY-MM-DD (default: today)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if any error finding exists")
    parser.add_argument("--strict", action="store_true",
                        help="With --check, also exit 1 on warnings")
    parser.add_argument("--pending-days", type=int, default=DEFAULT_PENDING_DAYS,
                        help=f"Days before a pending or on-hold order counts as stuck (default: {DEFAULT_PENDING_DAYS})")
    parser.add_argument("--tolerance", default=None,
                        help="Allowed difference between line-item sum and order total, for example 0.05 (default: 0.01)")
    parser.add_argument("--status", default=None,
                        help="Only audit orders with this status, for example completed")
    args = parser.parse_args(argv)
    if args.as_of is not None:
        try:
            args.reference = parse_iso_date(args.as_of, "--as-of", "reference date")
        except InputError as exc:
            parser.error(str(exc))
    else:
        args.reference = date.today()
    if args.pending_days < 0:
        parser.error("--pending-days must be zero or more")
    if args.tolerance is not None:
        try:
            args.tolerance_value = parse_money(args.tolerance, "--tolerance", "tolerance")
        except InputError as exc:
            parser.error(str(exc))
    else:
        args.tolerance_value = DEFAULT_TOLERANCE
    if args.status is not None:
        args.status = normalize_status(args.status)
    return args


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.input is None or args.input == "-":
            text = sys.stdin.read()
            source = "stdin"
        else:
            try:
                with open(args.input, "r", encoding="utf-8-sig", newline="") as handle:
                    text = handle.read()
            except OSError as exc:
                print(f"error: cannot read {args.input}: {exc.strerror or exc}", file=sys.stderr)
                return 2
            source = args.input
        orders = load_orders(text)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.status is not None:
        orders = [order for order in orders if order["status"] == args.status]
    report = audit_orders(orders, args.reference, args.pending_days, args.tolerance_value)
    if args.json:
        print(json.dumps(report_to_json(report), indent=2))
    else:
        print(format_text(report, source), end="")
    if args.check:
        failed = report["error_count"] > 0 or (args.strict and report["warning_count"] > 0)
        if failed:
            if not args.json:
                print(f"CHECK FAILED: {report['error_count']} error(s), "
                      f"{report['warning_count']} warning(s)")
            return 1
        if not args.json:
            print("CHECK PASSED: no error findings"
                  + (" and no warnings" if args.strict else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
