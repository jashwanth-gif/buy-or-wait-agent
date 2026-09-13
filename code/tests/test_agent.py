"""
Unit and integration test suite for Buy or Wait Agent.
"""
from datetime import date, timedelta
import os
import unittest
import pandas as pd

from src.data_loader import Request, extract_currency, load_requests, parse_bool, parse_date
from src.simulation import (
    ExchangeRates,
    FinancialEvent,
    PaymentOption,
    deterministic_rng,
    simulate_events,
    simulate_payment_options,
    simulate_user_profile,
)
from src.financial_forecast import (
    compute_amount_safe_to_pay,
    compute_earliest_full_payment_date,
    forecast_balance,
    is_plan_safe,
)
from src.plan_generator import (
    generate_payment_plans,
    generate_spending_change_sets,
    rank_plans,
)
from src.validator import validate_output
from main import process_single_request


class TestDataLoader(unittest.TestCase):
    def test_parse_bool(self):
        self.assertTrue(parse_bool(True))
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("True"))
        self.assertTrue(parse_bool(1))
        self.assertFalse(parse_bool(False))
        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool(0))

    def test_parse_date(self):
        d = parse_date("2026-07-05")
        self.assertEqual(d, date(2026, 7, 5))

    def test_extract_currency(self):
        self.assertEqual(extract_currency("Price is USD 500."), "USD")
        self.assertEqual(extract_currency("IDR 15,000,000 to family"), "IDR")
        self.assertEqual(extract_currency("Costs EUR 250"), "EUR")
        self.assertEqual(extract_currency("No currency mentioned"), None)

    def test_load_requests(self):
        requests = load_requests("dataset/requests.csv")
        self.assertEqual(len(requests), 250)
        first = requests[0]
        self.assertEqual(first.request_id, "request_26")
        self.assertEqual(first.user_id, "user_26")
        self.assertEqual(first.requested_amount, 15656000.0)


class TestSimulation(unittest.TestCase):
    def test_deterministic_profile(self):
        p1 = simulate_user_profile("user_26", requested_amount=1000.0)
        p2 = simulate_user_profile("user_26", requested_amount=1000.0)
        self.assertEqual(p1.home_currency, p2.home_currency)
        self.assertEqual(p1.available_balance, p2.available_balance)
        self.assertEqual(p1.minimum_balance_to_keep, p2.minimum_balance_to_keep)
        self.assertEqual(p1.payment_methods_user_will_consider, p2.payment_methods_user_will_consider)

    def test_exchange_rates(self):
        # 100 USD to EUR
        eur = ExchangeRates.convert(100.0, "USD", "EUR")
        self.assertAlmostEqual(eur, 92.59, places=1)
        # Same currency
        self.assertEqual(ExchangeRates.convert(50.0, "USD", "USD"), 50.0)

    def test_simulate_events(self):
        profile = simulate_user_profile("user_27", requested_amount=6670.0, home_currency="ZAR")
        events = simulate_events(profile, date(2026, 7, 5))
        self.assertTrue(any(e.is_income for e in events))
        self.assertTrue(any(not e.is_income and not e.is_flexible for e in events))
        self.assertTrue(any(e.is_flexible for e in events))


class TestFinancialForecast(unittest.TestCase):
    def test_forecast_daily_progression(self):
        start = date(2026, 1, 1)
        events = [
            FinancialEvent(
                event_id="salary",
                name="Salary",
                amount=3000.0,
                is_income=True,
                frequency="monthly",
                day_of_month=25,
            ),
            FinancialEvent(
                event_id="rent",
                name="Rent",
                amount=1000.0,
                is_income=False,
                frequency="monthly",
                day_of_month=1,
            ),
        ]
        daily = forecast_balance(events, initial_balance=5000.0, start_date=start, days=30)
        self.assertEqual(len(daily), 30)
        # Day 1: Rent deducted (4000)
        self.assertEqual(daily[0][1], 4000.0)
        # Day 25: Salary added
        self.assertEqual(daily[24][1], 7000.0)

    def test_amount_safe_to_pay_bounds(self):
        start = date(2026, 1, 1)
        events = []
        safe = compute_amount_safe_to_pay(
            events=events,
            initial_balance=5000.0,
            min_balance=1000.0,
            requested_amount=3000.0,
            request_date=start,
            days=90,
        )
        self.assertEqual(safe, 3000.0)

        # When requested amount exceeds available buffer
        safe_capped = compute_amount_safe_to_pay(
            events=events,
            initial_balance=2000.0,
            min_balance=1000.0,
            requested_amount=3000.0,
            request_date=start,
            days=90,
        )
        self.assertEqual(safe_capped, 1000.0)


class TestPlanGeneratorAndRanking(unittest.TestCase):
    def test_affordablenow_invariant(self):
        req = Request(
            request_id="test_1",
            user_id="user_test1",
            request_date=date(2026, 5, 1),
            request_type="purchase",
            requested_amount=500.0,
            desired_completion_date=date(2026, 6, 1),
            allows_partial_payment=True,
            request_text="Can I buy this USD 500 item?",
            detected_currency="USD",
        )
        res = process_single_request(req)
        if res["affordability_status"] == "affordablenow":
            self.assertEqual(res["earliest_date_for_full_payment"], "2026-05-01")
            self.assertEqual(res["recommended_payment_method"], "fullpayment")
            self.assertEqual(res["spending_changes_needed"], "none")

    def test_spending_change_sets(self):
        profile = simulate_user_profile("user_test2", requested_amount=1000.0)
        events = simulate_events(profile, date(2026, 5, 1))
        change_sets = generate_spending_change_sets(events)
        self.assertTrue(len(change_sets) > 0)
        for c in change_sets:
            self.assertTrue(len(c) <= 3)

    def test_wait_requires_fullpayment_accepted(self):
        # A user who only accepts installments must never be recommended wait
        req = Request(
            request_id="test_wait",
            user_id="user_test_wait",
            request_date=date(2026, 5, 1),
            request_type="purchase",
            requested_amount=5000.0,
            desired_completion_date=date(2026, 8, 1),
            allows_partial_payment=False,
            request_text="Need USD 5000 purchase",
            detected_currency="USD",
        )
        profile = simulate_user_profile("user_test_wait", requested_amount=5000.0, home_currency="USD")
        profile.payment_methods_user_will_consider = ["installments"]
        events = simulate_events(profile, date(2026, 5, 1))
        options = simulate_payment_options(req.request_id, req.requested_amount, req.request_date)
        safe_amt = compute_amount_safe_to_pay(events, profile.available_balance, profile.minimum_balance_to_keep, req.requested_amount, req.request_date)
        earliest_full = compute_earliest_full_payment_date(events, profile.available_balance, profile.minimum_balance_to_keep, req.requested_amount, req.request_date)
        plans = generate_payment_plans(req, profile, events, options, safe_amt, earliest_full)
        self.assertFalse(any(p.recommended_payment_method == "wait" for p in plans))


class TestEndToEndValidation(unittest.TestCase):
    def test_full_dataset_validation(self):
        output_path = "output.csv" if os.path.exists("output.csv") else "evaluation/output.csv"
        req_path = "dataset/requests.csv" if os.path.exists("dataset/requests.csv") else "records.csv"
        df_out = pd.read_csv(output_path)
        df_rec = pd.read_csv(req_path)
        is_valid, errors = validate_output(df_out, df_rec)
        self.assertTrue(is_valid, f"Validation failed with errors: {errors}")


if __name__ == "__main__":
    unittest.main()
