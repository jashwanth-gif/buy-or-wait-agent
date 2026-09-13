"""
Interactive Web UI server for the Buy or Wait Agent.
Provides a modern dashboard, 90-day balance forecast visualization,
clickable filters, and a real-time 'What-If' scenario evaluator.
"""
from datetime import date, datetime
import json
import logging
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Dict, Optional

import pandas as pd

from src.data_loader import Request, extract_currency, load_requests, parse_bool, parse_date
from src.simulation import (
    ExchangeRates,
    FinancialEvent,
    PaymentOption,
    UserProfile,
    simulate_events,
    simulate_payment_options,
    simulate_user_profile,
)
from src.financial_forecast import (
    compute_amount_safe_to_pay,
    compute_earliest_full_payment_date,
    forecast_balance,
)
from src.plan_generator import (
    PlanResult,
    format_amount,
    generate_explanation,
    generate_payment_plans,
    rank_plans,
)
from src.chat_bot import FinancialChatBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agent_server")

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

# Preload records and baseline profiles
ALL_REQUESTS: Dict[str, Request] = {}
BASELINE_PROFILES: Dict[str, UserProfile] = {}
USER_TO_REQUEST: Dict[str, Request] = {}
CHAT_BOT: Optional[FinancialChatBot] = None


def initialize_cache():
    global ALL_REQUESTS, BASELINE_PROFILES, USER_TO_REQUEST, CHAT_BOT
    reqs = load_requests("records.csv")
    ALL_REQUESTS = {r.request_id: r for r in reqs}
    USER_TO_REQUEST = {r.user_id: r for r in reqs}
    for r in reqs:
        p = simulate_user_profile(
            user_id=r.user_id,
            requested_amount=r.requested_amount,
            home_currency=r.detected_currency,
        )
        BASELINE_PROFILES[r.user_id] = p
    CHAT_BOT = FinancialChatBot("records.csv", "evaluation/output.csv")
    logger.info("Initialized cache with %d requests and baseline user profiles.", len(reqs))


initialize_cache()


class AgentHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=UI_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.path = "/index.html"
            return super().do_GET()

        elif path == "/api/summary":
            self.handle_api_summary()

        elif path == "/api/requests":
            self.handle_api_requests(parsed.query)

        elif path == "/api/preset_users":
            self.handle_api_preset_users()

        elif path.startswith("/api/request/"):
            req_id = path.split("/")[-1]
            self.handle_api_request_detail(req_id)

        else:
            return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/evaluate":
            self.handle_api_evaluate()
        elif parsed.path == "/api/chat":
            self.handle_api_chat()
        else:
            self.send_error(404, "Endpoint not found")

    def handle_api_chat(self):
        """Handle conversational chatbot interactions with the financial planning engine."""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body)
            msg = data.get("message", "").strip()
            if not msg:
                self._send_json({"reply": "Please provide a question or request ID to analyze."}, status=400)
                return

            if CHAT_BOT is None:
                self._send_json({"reply": "Chatbot engine is initializing. Please retry in a moment."}, status=503)
                return

            response = CHAT_BOT.handle_message(msg)
            self._send_json(response)
        except Exception as e:
            logger.error("Error in /api/chat: %s", e)
            self._send_json({"error": str(e), "reply": "An error occurred while analyzing your request."}, status=500)

    def _send_json(self, data: object, status: int = 200):
        # Guarantee no NaN or Infinity is ever serialized as bare tokens
        def sanitize(val):
            if isinstance(val, float):
                import math
                if math.isnan(val) or math.isinf(val):
                    return ""
            elif isinstance(val, dict):
                return {k: sanitize(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [sanitize(v) for v in val]
            return val

        cleaned = sanitize(data)
        body = json.dumps(cleaned, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def handle_api_summary(self):
        try:
            out_path = "evaluation/output.csv"
            rec_path = "records.csv" if os.path.exists("records.csv") else "requests.csv"
            out_df = pd.read_csv(out_path, keep_default_na=False).fillna("")
            rec_df = pd.read_csv(rec_path, keep_default_na=False).fillna("")

            status_counts = out_df["affordability_status"].value_counts().to_dict()
            method_counts = out_df["recommended_payment_method"].value_counts().to_dict()

            summary = {
                "total_requests": len(out_df),
                "affordability_status_counts": {
                    "affordablenow": int(status_counts.get("affordablenow", 0)),
                    "affordablewithplan": int(status_counts.get("affordablewithplan", 0)),
                    "affordablelater": int(status_counts.get("affordablelater", 0)),
                    "notaffordable": int(status_counts.get("notaffordable", 0)),
                },
                "recommended_payment_method_counts": {k: int(v) for k, v in method_counts.items()},
                "request_types": {k: int(v) for k, v in rec_df["request_type"].value_counts().items()},
            }
            self._send_json(summary)
        except Exception as e:
            logger.error("Error in /api/summary: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def handle_api_preset_users(self):
        """Return preset sample users representing diverse statuses."""
        presets = [
            {"user_id": "user_27", "label": "user_27 (ZAR - R3.5k Balance, Budget Conscious)", "currency": "ZAR", "balance": 3576.26, "min_keep": 406.05},
            {"user_28": "user_28", "label": "user_28 (EUR - €700 Balance, Modest Income)", "currency": "EUR", "balance": 703.20, "min_keep": 80.59},
            {"user_30": "user_30", "label": "user_30 (USD - $1,192 Balance, Average Income)", "currency": "USD", "balance": 1192.36, "min_keep": 142.42},
            {"user_26": "user_26", "label": "user_26 (IDR - Rp 18.7M Balance, Waiting for Payday)", "currency": "IDR", "balance": 18787200.0, "min_keep": 2763292.06},
            {"user_33": "user_33", "label": "user_33 (INR - ₹280k Balance, Healthy Surplus)", "currency": "INR", "balance": 280200.0, "min_keep": 32000.0},
        ]
        self._send_json({"presets": presets})

    def handle_api_requests(self, query_str: str):
        try:
            params = parse_qs(query_str)
            status_filter = params.get("status", [None])[0]
            search_query = params.get("search", [None])[0]

            out_path = "evaluation/output.csv"
            rec_path = "records.csv" if os.path.exists("records.csv") else "requests.csv"
            out_df = pd.read_csv(out_path, keep_default_na=False).fillna("")
            rec_df = pd.read_csv(rec_path, keep_default_na=False).fillna("")

            merged = pd.merge(rec_df, out_df, on="request_id").fillna("")

            if status_filter and status_filter.lower() != "all":
                merged = merged[merged["affordability_status"].str.lower() == status_filter.lower()]

            if search_query:
                q = search_query.strip().lower()
                merged = merged[
                    merged["request_id"].str.lower().str.contains(q, na=False)
                    | merged["user_id"].str.lower().str.contains(q, na=False)
                    | merged["request_type"].str.lower().str.contains(q, na=False)
                    | merged["request_text"].str.lower().str.contains(q, na=False)
                ]

            records = merged.to_dict(orient="records")
            self._send_json({"count": len(records), "requests": records})
        except Exception as e:
            logger.error("Error in /api/requests: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def handle_api_request_detail(self, req_id: str):
        try:
            target_req = ALL_REQUESTS.get(req_id)
            if not target_req:
                self._send_json({"error": f"Request '{req_id}' not found"}, status=404)
                return

            profile = BASELINE_PROFILES.get(target_req.user_id)
            if not profile:
                profile = simulate_user_profile(
                    user_id=target_req.user_id,
                    requested_amount=target_req.requested_amount,
                    home_currency=target_req.detected_currency,
                )

            events = simulate_events(profile=profile, request_date=target_req.request_date)
            options = simulate_payment_options(
                request_id=target_req.request_id,
                requested_amount=target_req.requested_amount,
                request_date=target_req.request_date,
            )

            safe_to_pay = compute_amount_safe_to_pay(
                events=events,
                initial_balance=profile.available_balance,
                min_balance=profile.minimum_balance_to_keep,
                requested_amount=target_req.requested_amount,
                request_date=target_req.request_date,
                days=90,
            )

            earliest_full_date = compute_earliest_full_payment_date(
                events=events,
                initial_balance=profile.available_balance,
                min_balance=profile.minimum_balance_to_keep,
                requested_amount=target_req.requested_amount,
                request_date=target_req.request_date,
                days=90,
            )

            candidate_plans = generate_payment_plans(
                request=target_req,
                profile=profile,
                events=events,
                payment_options=options,
                amount_safe_to_pay=safe_to_pay,
                earliest_date_for_full_payment=earliest_full_date,
            )

            chosen_plan = rank_plans(candidate_plans, request_date=target_req.request_date)

            earliest_date_str = ""
            if chosen_plan.affordability_status == "affordablenow":
                earliest_date_str = str(target_req.request_date)
            elif chosen_plan.affordability_status in ("affordablewithplan", "affordablelater"):
                if earliest_full_date is not None:
                    earliest_date_str = str(earliest_full_date)
            elif chosen_plan.affordability_status == "notaffordable":
                earliest_date_str = ""

            explanation = generate_explanation(
                plan=chosen_plan,
                request=target_req,
                profile=profile,
                amount_safe_to_pay=safe_to_pay,
                earliest_date=earliest_full_date,
            )

            # Baseline forecast
            baseline_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=target_req.request_date,
                days=90,
            )

            # Plan forecast schedule
            plan_schedule = []
            if chosen_plan.recommended_payment_method == "fullpayment":
                plan_schedule = [(target_req.request_date, target_req.requested_amount)]
            elif chosen_plan.recommended_payment_method == "partialpayment":
                rem = round(target_req.requested_amount - safe_to_pay, 2)
                plan_schedule = [(target_req.request_date, safe_to_pay)]
                if earliest_full_date:
                    plan_schedule.append((earliest_full_date, rem))
            elif chosen_plan.recommended_payment_method == "installments" and chosen_plan.payment_plan != "none":
                for part in chosen_plan.payment_plan.split(";"):
                    if ":" in part:
                        d_s, a_s = part.split(":", 1)
                        plan_schedule.append((parse_date(d_s), float(a_s)))

            spending_changes = None
            if chosen_plan.spending_changes_needed != "none":
                spending_changes = chosen_plan.spending_changes_needed.split(";")

            plan_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=target_req.request_date,
                days=90,
                extra_payments=plan_schedule,
                spending_changes=spending_changes,
            )

            # Hypothetical full payment today (to visualize why 'notaffordable' fails)
            hypothetical_full_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=target_req.request_date,
                days=90,
                extra_payments=[(target_req.request_date, target_req.requested_amount)],
                spending_changes=None,
            )

            chart_dates = [str(d) for d, _ in baseline_forecast]
            chart_baseline = [bal for _, bal in baseline_forecast]
            chart_plan = [bal for _, bal in plan_forecast]
            chart_hypo = [bal for _, bal in hypothetical_full_forecast]

            detail = {
                "request": {
                    "request_id": target_req.request_id,
                    "user_id": target_req.user_id,
                    "request_date": str(target_req.request_date),
                    "request_type": target_req.request_type,
                    "requested_amount": target_req.requested_amount,
                    "desired_completion_date": str(target_req.desired_completion_date),
                    "allows_partial_payment": target_req.allows_partial_payment,
                    "request_text": target_req.request_text,
                    "detected_currency": target_req.detected_currency or profile.home_currency,
                },
                "profile": {
                    "user_id": profile.user_id,
                    "home_currency": profile.home_currency,
                    "available_balance": profile.available_balance,
                    "minimum_balance_to_keep": profile.minimum_balance_to_keep,
                    "financial_priorities": profile.financial_priorities,
                    "spending_preferences": profile.spending_preferences,
                    "payment_methods_user_will_consider": profile.payment_methods_user_will_consider,
                },
                "events": [
                    {
                        "event_id": ev.event_id,
                        "name": ev.name,
                        "amount": ev.amount,
                        "is_income": ev.is_income,
                        "frequency": ev.frequency,
                        "day_of_month": ev.day_of_month,
                        "event_date": str(ev.event_date) if ev.event_date else None,
                        "is_flexible": ev.is_flexible,
                        "original_currency": ev.original_currency,
                    }
                    for ev in events
                ],
                "payment_options": [
                    {
                        "payment_option_id": opt.payment_option_id,
                        "start_date": str(opt.start_date),
                        "num_payments": opt.num_payments,
                        "payment_interval_days": opt.payment_interval_days,
                        "financing_fee": opt.financing_fee,
                        "total_payable": opt.total_payable,
                        "schedule": [(str(d), a) for d, a in opt.schedule],
                    }
                    for opt in options
                ],
                "decision": {
                    "amount_safe_to_pay": safe_to_pay,
                    "affordability_status": chosen_plan.affordability_status,
                    "recommended_payment_method": chosen_plan.recommended_payment_method,
                    "payment_plan": chosen_plan.payment_plan,
                    "earliest_date_for_full_payment": earliest_date_str,
                    "spending_changes_needed": chosen_plan.spending_changes_needed,
                    "decision_explanation": explanation,
                },
                "forecast_chart": {
                    "dates": chart_dates,
                    "baseline": chart_baseline,
                    "plan": chart_plan,
                    "hypothetical_full": chart_hypo,
                    "minimum_balance": profile.minimum_balance_to_keep,
                },
            }
            self._send_json(detail)
        except Exception as e:
            logger.error("Error in /api/request/%s: %s", req_id, e)
            self._send_json({"error": str(e)}, status=500)

    def handle_api_evaluate(self):
        """
        Evaluate a custom request in the What-If playground.
        Uses realistic fixed financial statistics so increasing requested_amount
        correctly transitions from affordablenow -> affordablewithplan -> affordablelater -> notaffordable.
        """
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body)

            user_id = data.get("user_id", "custom_user_1").strip()
            req_date = parse_date(data["request_date"])
            comp_date = parse_date(data["desired_completion_date"])
            req_type = data.get("request_type", "purchase")
            req_amt = float(data["requested_amount"])
            partial_ok = parse_bool(data.get("allows_partial_payment", False))
            req_text = data.get("request_text", "")
            detected_curr = extract_currency(req_text) or data.get("home_currency") or "USD"

            # 1. Determine User Profile
            # If user explicitly supplied custom financial statistics, use them!
            if "available_balance" in data and float(data["available_balance"]) > 0:
                avail_bal = float(data["available_balance"])
                home_curr = data.get("home_currency") or detected_curr
                min_keep = float(data.get("minimum_balance_to_keep", round(avail_bal * 0.15, 2)))
                methods = data.get("payment_methods", ["fullpayment", "partialpayment", "installments", "wait"])
                profile = UserProfile(
                    user_id=user_id,
                    home_currency=home_curr,
                    available_balance=avail_bal,
                    minimum_balance_to_keep=min_keep,
                    financial_priorities=["essentials", "savings", "flexible"],
                    spending_preferences={"subscriptions": True, "dining": True, "entertainment": True},
                    payment_methods_user_will_consider=methods,
                )
            # Or if this is an existing user from records.csv, use their fixed established profile!
            elif user_id in BASELINE_PROFILES:
                base_prof = BASELINE_PROFILES[user_id]
                profile = UserProfile(
                    user_id=base_prof.user_id,
                    home_currency=base_prof.home_currency,
                    available_balance=base_prof.available_balance,
                    minimum_balance_to_keep=base_prof.minimum_balance_to_keep,
                    financial_priorities=base_prof.financial_priorities,
                    spending_preferences=base_prof.spending_preferences,
                    payment_methods_user_will_consider=base_prof.payment_methods_user_will_consider,
                )
            # Otherwise, use a fixed, realistic baseline benchmark by currency (DO NOT scale with req_amt!)
            else:
                benchmark_balances = {
                    "USD": 2500.0,
                    "EUR": 2200.0,
                    "INR": 150000.0,
                    "ZAR": 25000.0,
                    "IDR": 20000000.0,
                }
                home_curr = detected_curr
                avail_bal = benchmark_balances.get(home_curr, 2500.0)
                min_keep = round(avail_bal * 0.15, 2)
                profile = UserProfile(
                    user_id=user_id,
                    home_currency=home_curr,
                    available_balance=avail_bal,
                    minimum_balance_to_keep=min_keep,
                    financial_priorities=["essentials", "savings", "flexible"],
                    spending_preferences={"subscriptions": True, "dining": True, "entertainment": True},
                    payment_methods_user_will_consider=["fullpayment", "partialpayment", "installments", "wait"],
                )

            req = Request(
                request_id=data.get("request_id", "playground_req"),
                user_id=user_id,
                request_date=req_date,
                request_type=req_type,
                requested_amount=req_amt,
                desired_completion_date=comp_date,
                allows_partial_payment=partial_ok,
                request_text=req_text,
                detected_currency=profile.home_currency,
            )

            events = simulate_events(profile=profile, request_date=req.request_date)

            # If user explicitly supplied custom monthly income, update salary event
            if "monthly_salary" in data and float(data["monthly_salary"]) > 0:
                salary_val = float(data["monthly_salary"])
                for ev in events:
                    if ev.is_income:
                        ev.amount = salary_val

            options = simulate_payment_options(
                request_id=req.request_id,
                requested_amount=req.requested_amount,
                request_date=req.request_date,
            )

            safe_to_pay = compute_amount_safe_to_pay(
                events=events,
                initial_balance=profile.available_balance,
                min_balance=profile.minimum_balance_to_keep,
                requested_amount=req.requested_amount,
                request_date=req.request_date,
                days=90,
            )

            earliest_full_date = compute_earliest_full_payment_date(
                events=events,
                initial_balance=profile.available_balance,
                min_balance=profile.minimum_balance_to_keep,
                requested_amount=req.requested_amount,
                request_date=req.request_date,
                days=90,
            )

            candidate_plans = generate_payment_plans(
                request=req,
                profile=profile,
                events=events,
                payment_options=options,
                amount_safe_to_pay=safe_to_pay,
                earliest_date_for_full_payment=earliest_full_date,
            )
            chosen_plan = rank_plans(candidate_plans, request_date=req.request_date)

            earliest_date_str = ""
            if chosen_plan.affordability_status == "affordablenow":
                earliest_date_str = str(req.request_date)
            elif earliest_full_date is not None:
                earliest_date_str = str(earliest_full_date)

            explanation = generate_explanation(
                plan=chosen_plan,
                request=req,
                profile=profile,
                amount_safe_to_pay=safe_to_pay,
                earliest_date=earliest_full_date,
            )

            # Chart data
            baseline_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=req.request_date,
                days=90,
            )

            plan_schedule = []
            if chosen_plan.recommended_payment_method == "fullpayment":
                plan_schedule = [(req.request_date, req.requested_amount)]
            elif chosen_plan.recommended_payment_method == "partialpayment":
                rem = round(req.requested_amount - safe_to_pay, 2)
                plan_schedule = [(req.request_date, safe_to_pay)]
                if earliest_full_date:
                    plan_schedule.append((earliest_full_date, rem))
            elif chosen_plan.recommended_payment_method == "installments" and chosen_plan.payment_plan != "none":
                for part in chosen_plan.payment_plan.split(";"):
                    if ":" in part:
                        d_s, a_s = part.split(":", 1)
                        plan_schedule.append((parse_date(d_s), float(a_s)))

            spending_changes = None
            if chosen_plan.spending_changes_needed != "none":
                spending_changes = chosen_plan.spending_changes_needed.split(";")

            plan_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=req.request_date,
                days=90,
                extra_payments=plan_schedule,
                spending_changes=spending_changes,
            )

            hypothetical_full_forecast = forecast_balance(
                events=events,
                initial_balance=profile.available_balance,
                start_date=req.request_date,
                days=90,
                extra_payments=[(req.request_date, req.requested_amount)],
                spending_changes=None,
            )

            self._send_json({
                "decision": {
                    "request_id": req.request_id,
                    "amount_safe_to_pay": safe_to_pay,
                    "affordability_status": chosen_plan.affordability_status,
                    "recommended_payment_method": chosen_plan.recommended_payment_method,
                    "payment_plan": chosen_plan.payment_plan,
                    "earliest_date_for_full_payment": earliest_date_str,
                    "spending_changes_needed": chosen_plan.spending_changes_needed,
                    "decision_explanation": explanation,
                },
                "profile": {
                    "user_id": profile.user_id,
                    "home_currency": profile.home_currency,
                    "available_balance": profile.available_balance,
                    "minimum_balance_to_keep": profile.minimum_balance_to_keep,
                    "payment_methods_user_will_consider": profile.payment_methods_user_will_consider,
                },
                "forecast_chart": {
                    "dates": [str(d) for d, _ in baseline_forecast],
                    "baseline": [bal for _, bal in baseline_forecast],
                    "plan": [bal for _, bal in plan_forecast],
                    "hypothetical_full": [bal for _, bal in hypothetical_full_forecast],
                    "minimum_balance": profile.minimum_balance_to_keep,
                },
            })
        except Exception as e:
            logger.error("Error in /api/evaluate: %s", e)
            self._send_json({"error": str(e)}, status=500)


def start_server(port: int = 8000):
    server_address = ("0.0.0.0", port)
    httpd = HTTPServer(server_address, AgentHandler)
    logger.info("=================================================================")
    logger.info("Buy or Wait Agent UI Server running at: http://localhost:%d", port)
    logger.info("Open http://localhost:%d in your web browser to check the UI.", port)
    logger.info("=================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down UI server...")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    start_server(port)
