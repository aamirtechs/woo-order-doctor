"""Tests for woo-order-doctor."""

import io
import json
import subprocess
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from woo_order_doctor import (
    InputError,
    audit_orders,
    load_orders,
    main,
    parse_line_items,
    parse_money,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample.csv"
CLEAN = ROOT / "examples" / "clean.csv"
REF = date(2026, 11, 6)

HEADER = ("Order ID,Order Date,Order Status,Billing Email,Customer,"
          "Order Total,Line Items,Refund Total\n")


def csv_text(rows):
    return HEADER + "".join(rows)


def one_order(order_id="1001", order_date="2026-11-02", status="completed",
              email="a@example.com", customer="Alice Reed", total="10.00",
              items="", refund="0.00"):
    return (f'{order_id},{order_date},{status},{email},{customer},{total},'
            f'"{items}",{refund}\n')


def audit(text, reference=REF, pending_days=7, tolerance=Decimal("0.01")):
    return audit_orders(load_orders(text), reference, pending_days, tolerance)


def checks(report):
    return [(item["order_id"], item["check"], item["severity"])
            for item in report["findings"]]


class TestLineItems(unittest.TestCase):
    def test_quantity_and_price(self):
        self.assertEqual(parse_line_items("Widget A x 2 @ 15.00 | Widget B x 1 @ 29.98", "x"),
                         Decimal("59.98"))

    def test_single_item_without_quantity(self):
        self.assertEqual(parse_line_items("Widget A @ 12.50", "x"), Decimal("12.50"))

    def test_semicolon_separator(self):
        self.assertEqual(parse_line_items("A x 2 @ 5.00; B @ 3.00", "x"), Decimal("13.00"))

    def test_empty_cell_is_none(self):
        self.assertIsNone(parse_line_items("", "x"))
        self.assertIsNone(parse_line_items("   ", "x"))

    def test_unparseable_segment_is_none(self):
        self.assertIsNone(parse_line_items("Widget A, no price here", "x"))

    def test_cents_math_exact(self):
        self.assertEqual(parse_line_items("A x 3 @ 0.10", "x"), Decimal("0.30"))


class TestMoney(unittest.TestCase):
    def test_currency_symbol_and_commas(self):
        self.assertEqual(parse_money("$1,234.56", "total", "x"), Decimal("1234.56"))

    def test_rounds_to_cents(self):
        self.assertEqual(parse_money("10.005", "total", "x"), Decimal("10.01"))

    def test_bad_amount_raises(self):
        with self.assertRaises(InputError):
            parse_money("abc", "total", "x")

    def test_negative_allowed_at_parse(self):
        self.assertEqual(parse_money("-15.00", "total", "x"), Decimal("-15.00"))


class TestLoading(unittest.TestCase):
    def test_sample_loads_eleven_orders(self):
        self.assertEqual(len(load_orders(SAMPLE.read_text())), 11)

    def test_column_aliases(self):
        text = ("Order Number,Date Created,Status,Email,Total\n"
                "7,2026-11-01,completed,b@example.com,5.00\n")
        orders = load_orders(text)
        self.assertEqual(orders[0]["id"], "7")
        self.assertEqual(orders[0]["billing_email"], "b@example.com")

    def test_wc_status_prefix_stripped(self):
        orders = load_orders(csv_text([one_order(status="wc-completed")]))
        self.assertEqual(orders[0]["status"], "completed")

    def test_datetime_order_date(self):
        orders = load_orders(csv_text([one_order(order_date="2026-11-02 10:30:00")]))
        self.assertEqual(orders[0]["order_date"], date(2026, 11, 2))

    def test_missing_required_column(self):
        with self.assertRaises(InputError):
            load_orders("Order ID,Order Date,Order Status\n1,2026-11-01,completed\n")

    def test_bad_date_raises(self):
        with self.assertRaises(InputError):
            load_orders(csv_text([one_order(order_date="not-a-date")]))

    def test_missing_order_id_raises(self):
        with self.assertRaises(InputError):
            load_orders(csv_text([one_order(order_id="")]))

    def test_bad_total_raises(self):
        with self.assertRaises(InputError):
            load_orders(csv_text([one_order(total="free")]))

    def test_blank_lines_skipped(self):
        text = csv_text([one_order()]) + "\n"
        self.assertEqual(len(load_orders(text)), 1)

    def test_line_item_total_column_used_when_no_items(self):
        text = ("Order ID,Order Date,Order Status,Billing Email,Customer,"
                "Order Total,Line Item Total,Refund Total\n"
                "1,2026-11-01,completed,a@example.com,Alice,10.00,9.00,0.00\n")
        report = audit(text)
        self.assertIn(("1", "line item total mismatch", "error"), checks(report))


class TestChecks(unittest.TestCase):
    def test_sample_findings(self):
        report = audit(SAMPLE.read_text())
        self.assertEqual(report["total_orders"], 11)
        self.assertEqual(report["error_count"], 5)
        self.assertEqual(report["warning_count"], 3)
        self.assertEqual(report["gross_total"], Decimal("364.46"))
        self.assertEqual(report["status_counts"]["completed"], 4)

    def test_clean_sample_has_no_findings(self):
        report = audit(CLEAN.read_text())
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["gross_total"], Decimal("79.98"))

    def test_duplicate_order_id(self):
        text = csv_text([one_order(), one_order(order_date="2026-11-03")])
        self.assertIn(("1001", "duplicate order id", "error"), checks(audit(text)))

    def test_line_item_mismatch(self):
        text = csv_text([one_order(total="45.00", items="Gadget C x 1 @ 40.00")])
        report = audit(text)
        self.assertIn(("1001", "line item total mismatch", "error"), checks(report))
        self.assertIn("difference -5.00", report["findings"][0]["message"])

    def test_mismatch_within_tolerance_passes(self):
        text = csv_text([one_order(total="10.01", items="Widget A @ 10.00")])
        self.assertEqual(audit(text)["findings"], [])

    def test_missing_email_only_for_paid_orders(self):
        pending = csv_text([one_order(status="pending", email="")])
        self.assertEqual(audit(pending)["findings"], [])
        paid = csv_text([one_order(email="")])
        self.assertIn(("1001", "missing billing email", "error"), checks(audit(paid)))

    def test_refunded_but_completed(self):
        text = csv_text([one_order(refund="10.00")])
        self.assertIn(("1001", "refunded but completed", "error"), checks(audit(text)))

    def test_refunded_status_is_not_flagged(self):
        text = csv_text([one_order(status="refunded", refund="10.00")])
        self.assertEqual(audit(text)["findings"], [])

    def test_zero_value_completed(self):
        text = csv_text([one_order(total="0.00")])
        self.assertIn(("1001", "zero value completed", "warning"), checks(audit(text)))

    def test_negative_total(self):
        text = csv_text([one_order(total="-15.00")])
        self.assertIn(("1001", "negative total", "error"), checks(audit(text)))

    def test_stuck_pending_boundary(self):
        exactly = csv_text([one_order(status="pending", order_date="2026-10-30")])
        self.assertEqual(audit(exactly)["findings"], [])
        over = csv_text([one_order(status="pending", order_date="2026-10-29")])
        self.assertIn(("1001", "stuck pending", "warning"), checks(audit(over)))

    def test_stuck_on_hold_counts(self):
        text = csv_text([one_order(status="on-hold", order_date="2026-09-01")])
        self.assertIn(("1001", "stuck pending", "warning"), checks(audit(text)))

    def test_unparseable_line_items_warning(self):
        text = csv_text([one_order(items="Widget A, no price")])
        report = audit(text)
        self.assertIn(("1001", "unparseable line items", "warning"), checks(report))
        self.assertNotIn(("1001", "line item total mismatch", "error"), checks(report))

    def test_pending_days_option(self):
        text = csv_text([one_order(status="pending", order_date="2026-10-01")])
        self.assertEqual(audit(text, pending_days=40)["findings"], [])


class TestCli(unittest.TestCase):
    def run_main(self, argv, stdin_text=None):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text) if stdin_text is not None else io.StringIO("")
        try:
            return main(argv)
        finally:
            sys.stdin = old_stdin

    def test_text_report_exit_zero(self):
        self.assertEqual(self.run_main([str(SAMPLE), "--as-of", "2026-11-06"]), 0)

    def test_check_fails_on_sample(self):
        self.assertEqual(self.run_main([str(SAMPLE), "--as-of", "2026-11-06", "--check"]), 1)

    def test_check_passes_on_clean(self):
        self.assertEqual(self.run_main([str(CLEAN), "--as-of", "2026-11-06", "--check"]), 0)

    def test_strict_fails_on_warnings_only(self):
        text = csv_text([one_order(total="0.00")])
        self.assertEqual(self.run_main(["-", "--as-of", "2026-11-06", "--check"], stdin_text=text), 0)
        self.assertEqual(self.run_main(["-", "--as-of", "2026-11-06", "--check", "--strict"],
                                       stdin_text=text), 1)

    def test_json_output(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = self.run_main([str(SAMPLE), "--as-of", "2026-11-06", "--json"])
            data = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(data["total_orders"], 11)
        self.assertEqual(data["error_count"], 5)
        self.assertEqual(len(data["findings"]), 8)

    def test_status_filter(self):
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = self.run_main([str(SAMPLE), "--as-of", "2026-11-06", "--status", "completed", "--json"])
            data = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout
        self.assertEqual(code, 0)
        self.assertEqual(data["total_orders"], 4)
        self.assertEqual(data["error_count"], 2)

    def test_stdin(self):
        text = SAMPLE.read_text()
        self.assertEqual(self.run_main(["-", "--as-of", "2026-11-06"], stdin_text=text), 0)

    def test_bad_input_exit_two(self):
        self.assertEqual(self.run_main([str(ROOT / "examples" / "invalid.csv"),
                                        "--as-of", "2026-11-06"]), 2)

    def test_missing_file_exit_two(self):
        self.assertEqual(self.run_main([str(ROOT / "examples" / "nope.csv")]), 2)

    def test_bad_pending_days_exit_two(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "woo_order_doctor.py"), str(CLEAN),
             "--pending-days", "-1"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_module_run(self):
        result = subprocess.run(
            [sys.executable, "-m", "woo_order_doctor", str(CLEAN),
             "--as-of", "2026-11-06"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0)
        self.assertIn("Orders: 2 total", result.stdout)


if __name__ == "__main__":
    unittest.main()
