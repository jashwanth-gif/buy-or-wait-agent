"""
Interactive Web UI server for the Buy or Wait Agent.
Provides a modern dashboard, 90-day balance forecast visualization,
clickable filters, and a real-time 'What-If' scenario evaluator.
"""
from datetime import date, datetime, timedelta
import json
import logging
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Dict, List, Optional, Tuple, Any

import pandas as pd

from src.data_loader import (
    DataLoader,
    Request,
    FinancialProfile,
    FinancialEvent,
    PaymentOption,
    extract_currency,
    load_requests,
    parse_bool,
    parse_date,
)
from src.financial_forecast import FinancialForecastEngine
from src.plan_generator import PlanGenerator, Recommendation, format_amount
from src.chat_bot import FinancialChatBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("agent_server")

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

# Global cache
DATA_LOADER: Optional[DataLoader] = None
FORECAST_ENGINE: Optional[FinancialForecastEngine] = None
PLAN_GENERATOR: Optional[PlanGenerator] = None
ALL_REQUESTS: Dict[str, Request] = {}
OUTPUT_RECORDS: Dict[str, Dict[str, Any]] = {}
OUTPUT_DF: Optional[pd.DataFrame] = None
REQUESTS_DF: Optional[pd.DataFrame] = None
CHAT_BOT: Optional[FinancialChatBot] = None


def initialize_cache():
    global DATA_LOADER, FORECAST_ENGINE, PLAN_GENERATOR, ALL_REQUESTS, OUTPUT_RECORDS, OUTPUT_DF, REQUESTS_DF, CHAT_BOT
    
    # 1. Initialize official data loader
    dataset_dir = "dataset" if os.path.exists("dataset") else "."
    DATA_LOADER = DataLoader(dataset_dir)
    FORECAST_ENGINE = FinancialForecastEngine(DATA_LOADER)
    PLAN_GENERATOR = PlanGenerator(DATA_LOADER, FORECAST_ENGINE)

    # 2. Load requests
    req_file = "requests.csv" if os.path.exists(os.path.join(dataset_dir, "requests.csv")) else "records.csv"
    req_list = DATA_LOADER.load_requests(req_file)
    ALL_REQUESTS = {r.request_id: r for r in req_list}

    # 3. Load requests DataFrame
    raw_req_path = os.path.join(dataset_dir, "requests.csv") if os.path.exists(os.path.join(dataset_dir, "requests.csv")) else "records.csv"
    REQUESTS_DF = pd.read_csv(raw_req_path, keep_default_na=False).fillna("")

    # 4. Load output.csv
    out_path = "output.csv" if os.path.exists("output.csv") else ("evaluation/output.csv" if os.path.exists("evaluation/output.csv") else os.path.join(dataset_dir, "output.csv"))
    if os.path.exists(out_path):
        OUTPUT_DF = pd.read_csv(out_path, keep_default_na=False).fillna("")
        for _, row in OUTPUT_DF.iterrows():
            rid = str(row["request_id"]).strip()
            OUTPUT_RECORDS[rid] = row.to_dict()
    else:
        OUTPUT_DF = pd.DataFrame()

    # 5. Initialize ChatBot
    CHAT_BOT = FinancialChatBot(raw_req_path, out_path)
    logger.info("Initialized cache with %d requests and %d output predictions.", len(ALL_REQUESTS), len(OUTPUT_RECORDS))


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
            if OUTPUT_DF is not None and not OUTPUT_DF.empty:
                status_counts = OUTPUT_DF["affordability_status"].value_counts().to_dict()
                method_counts = OUTPUT_DF["recommended_payment_method"].value_counts().to_dict()
            else:
                status_counts = {}
                method_counts = {}

            # Normalize keys removing underscores for UI badge counters
            norm_status = {k.lower().replace("_", ""): v for k, v in status_counts.items()}

            req_types = {}
            if REQUESTS_DF is not None and "request_type" in REQUESTS_DF.columns:
                req_types = {k: int(v) for k, v in REQUESTS_DF["request_type"].value_counts().items()}

            summary = {
                "total_requests": len(OUTPUT_DF) if OUTPUT_DF is not None and not OUTPUT_DF.empty else len(ALL_REQUESTS),
                "affordability_status_counts": {
                    "affordablenow": int(norm_status.get("affordablenow", 0)),
                    "affordablewithplan": int(norm_status.get("affordablewithplan", 0)),
                    "affordablelater": int(norm_status.get("affordablelater", 0)),
                    "notaffordable": int(norm_status.get("notaffordable", 0)),
                },
                "recommended_payment_method_counts": {k: int(v) for k, v in method_counts.items()},
                "request_types": req_types,
            }
            self._send_json(summary)
        except Exception as e:
            logger.error("Error in /api/summary: %s", e)
            self._send_json({"error": str(e)}, status=500)

    def handle_api_preset_users(self):
        """Return preset sample users representing diverse statuses."""
        presets = [
            {"user_id": "user_27", "label": "user_27 (ZAR - R3.5k Balance, Budget Conscious)", "currency": "ZAR", "balance": 3576.26, "min_keep": 406.05},
            {"user_id": "user_28", "label": "user_28 (EUR - €700 Balance, Modest Income)", "currency": "EUR", "balance": 703.20, "min_keep": 80.59},
            {"user_id": "user_30", "label": "user_30 (USD - $1,192 Balance, Average Income)", "currency": "USD", "balance": 1192.36, "min_keep": 142.42},
            {"user_id": "user_26", "label": "user_26 (IDR - Rp 18.7M Balance, Waiting for Payday)", "currency": "IDR", "balance": 18787200.0, "min_keep": 2763292.06},
            {"user_id": "user_33", "label": "user_33 (INR - ₹280k Balance, Healthy Surplus)", "currency": "INR", "balance": 280200.0, "min_keep": 32000.0},
        ]
        self._send_json({"presets": presets})

    def handle_api_requests(self, query_str: str):
        try:
            params = parse_qs(query_str)
            status_filter = params.get("status", [None])[0]
            search_query = params.get("search", [None])[0]

            if REQUESTS_DF is not None and OUTPUT_DF is not None and not OUTPUT_DF.empty:
                merged = pd.merge(REQUESTS_DF, OUTPUT_DF, on="request_id").fillna("")
            elif REQUESTS_DF is not None:
                merged = REQUESTS_DF.copy()
            else:
                merged = pd.DataFrame()

            if status_filter and status_filter.lower() != "all" and "affordability_status" in merged.columns:
                target_status = status_filter.lower().replace("_", "")
                merged = merged[
                    merged["affordability_status"].astype(str).str.lower().str.replace("_", "") == target_status
                ]

            if search_query:
                q = search_query.strip().lower()
                merged = merged[
                    merged["request_id"].astype(str).str.lower().str.contains(q, na=False)
                    | merged["user_id"].astype(str).str.lower().str.contains(q, na=False)
                    | merged["request_type"].astype(str).str.lower().str.contains(q, na=False)
                    | merged["request_text"].astype(str).str.lower().str.contains(q, na=False)
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

            profile = DATA_LOADER.profiles.get(target_req.user_id) if DATA_LOADER else None
            events = DATA_LOADER.events.get(target_req.user_id, []) if DATA_LOADER else []
            options = DATA_LOADER.payment_options.get(target_req.request_id, []) if DATA_LOADER else []

            # Fetch recommendation from output cache or calculate dynamically
            if req_id in OUTPUT_RECORDS:
                rec_data = OUTPUT_RECORDS[req_id]
                safe_to_pay = float(rec_data["amount_safe_to_pay"])
                afford_status = str(rec_data["affordability_status"])
                rec_method = str(rec_data["recommended_payment_method"])
                pay_plan = str(rec_data["payment_plan"])
                earliest_full = str(rec_data["earliest_date_for_full_payment"])
                sp_changes = str(rec_data["spending_changes_needed"])
                expl = str(rec_data["decision_explanation"])
            elif PLAN_GENERATOR:
                rec = PLAN_GENERATOR.generate_recommendation(target_req)
                safe_to_pay = rec.amount_safe_to_pay
                afford_status = rec.affordability_status
                rec_method = rec.recommended_payment_method
                pay_plan = rec.payment_plan
                earliest_full = rec.earliest_date_for_full_payment
                sp_changes = rec.spending_changes_needed
                expl = rec.decision_explanation
            else:
                safe_to_pay = 0.0
                afford_status = "not_affordable"
                rec_method = "not_recommended"
                pay_plan = "none"
                earliest_full = ""
                sp_changes = "none"
                expl = "No planning engine available."

            # Baseline forecast (90 days)
            baseline_forecast: List[Tuple[date, float]] = []
            if FORECAST_ENGINE:
                baseline_forecast = FORECAST_ENGINE.simulate(
                    user_id=target_req.user_id,
                    request_date=target_req.request_date,
                    days=90,
                )

            # Plan forecast
            plan_schedule: List[Tuple[date, float]] = []
            if rec_method == "full_payment":
                plan_schedule = [(target_req.request_date, target_req.requested_amount)]
            elif rec_method == "partial_payment":
                plan_schedule = [(target_req.request_date, safe_to_pay)]
                if earliest_full and earliest_full.lower() not in ("none", ""):
                    rem = round(target_req.requested_amount - safe_to_pay, 2)
                    try:
                        plan_schedule.append((parse_date(earliest_full), rem))
                    except Exception:
                        pass
            elif rec_method == "installments" and pay_plan and pay_plan.lower() not in ("none", ""):
                parts = pay_plan.split("|") if "|" in pay_plan else pay_plan.split(";")
                for part in parts:
                    if ":" in part:
                        d_s, a_s = part.split(":", 1)
                        try:
                            plan_schedule.append((parse_date(d_s.strip()), float(a_s.strip())))
                        except Exception:
                            pass

            spending_changes_list = None
            if sp_changes and sp_changes.lower() not in ("none", ""):
                spending_changes_list = sp_changes.split("|") if "|" in sp_changes else sp_changes.split(";")

            plan_forecast: List[Tuple[date, float]] = []
            hypothetical_full_forecast: List[Tuple[date, float]] = []
            if FORECAST_ENGINE:
                plan_forecast = FORECAST_ENGINE.simulate(
                    user_id=target_req.user_id,
                    request_date=target_req.request_date,
                    days=90,
                    extra_payments=plan_schedule,
                    spending_changes=spending_changes_list,
                )
                hypothetical_full_forecast = FORECAST_ENGINE.simulate(
                    user_id=target_req.user_id,
                    request_date=target_req.request_date,
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
                    "detected_currency": profile.home_currency if profile else "USD",
                },
                "profile": {
                    "user_id": profile.user_id if profile else target_req.user_id,
                    "home_currency": profile.home_currency if profile else "USD",
                    "available_balance": profile.current_available_balance if profile else 0.0,
                    "minimum_balance_to_keep": profile.minimum_balance_to_keep if profile else 0.0,
                    "financial_priorities": profile.financial_priorities if profile else [],
                    "spending_preferences": profile.expense_categories_to_protect if profile else [],
                    "payment_methods_user_will_consider": profile.payment_methods_user_will_consider if profile else [],
                },
                "events": [
                    {
                        "event_id": ev.event_id,
                        "name": ev.description,
                        "amount": ev.amount,
                        "is_income": (ev.direction == "credit"),
                        "frequency": ev.event_type,
                        "day_of_month": ev.event_date.day if ev.event_date else 15,
                        "event_date": str(ev.event_date) if ev.event_date else None,
                        "is_flexible": (ev.flexibility != "fixed"),
                        "original_currency": ev.currency,
                    }
                    for ev in events
                ],
                "payment_options": [
                    {
                        "payment_option_id": opt.payment_option_id,
                        "start_date": str(opt.first_payment_date),
                        "num_payments": opt.number_of_payments,
                        "payment_interval_days": opt.payment_frequency_days or 30,
                        "financing_fee": opt.financing_fee,
                        "total_payable": opt.total_payable_amount,
                        "schedule": [(str(opt.first_payment_date), opt.payment_amount)],
                    }
                    for opt in options
                ],
                "decision": {
                    "amount_safe_to_pay": safe_to_pay,
                    "affordability_status": afford_status,
                    "recommended_payment_method": rec_method,
                    "payment_plan": pay_plan,
                    "earliest_date_for_full_payment": earliest_full,
                    "spending_changes_needed": sp_changes,
                    "decision_explanation": expl,
                },
                "forecast_chart": {
                    "dates": chart_dates,
                    "baseline": chart_baseline,
                    "plan": chart_plan,
                    "hypothetical_full": chart_hypo,
                    "minimum_balance": profile.minimum_balance_to_keep if profile else 0.0,
                },
            }
            self._send_json(detail)
        except Exception as e:
            logger.error("Error in /api/request/%s: %s", req_id, e)
            self._send_json({"error": str(e)}, status=500)

    def handle_api_evaluate(self):
        """
        Evaluate a custom request in the What-If playground.
        Dynamically executes the forecast and plan generator engine.
        """
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8")
            data = json.loads(body)

            user_id = data.get("user_id", "user_26").strip()
            req_date = parse_date(data["request_date"])
            comp_date = parse_date(data["desired_completion_date"])
            req_type = data.get("request_type", "purchase")
            req_amt = float(data["requested_amount"])
            partial_ok = parse_bool(data.get("allows_partial_payment", False))
            req_text = data.get("request_text", "")

            # Look up or build profile
            base_prof = DATA_LOADER.profiles.get(user_id) if DATA_LOADER else None
            if base_prof:
                avail_bal = float(data.get("available_balance", base_prof.current_available_balance))
                min_keep = float(data.get("minimum_balance_to_keep", base_prof.minimum_balance_to_keep))
                currency = base_prof.home_currency
            else:
                currency = data.get("home_currency") or extract_currency(req_text) or "USD"
                avail_bal = float(data.get("available_balance", 2500.0))
                min_keep = float(data.get("minimum_balance_to_keep", avail_bal * 0.15))

            # Build temporary Request
            temp_req = Request(
                request_id=data.get("request_id", "what_if_req"),
                user_id=user_id,
                request_date=req_date,
                request_type=req_type,
                requested_amount=req_amt,
                desired_completion_date=comp_date,
                allows_partial_payment=partial_ok,
                request_text=req_text,
                detected_currency=currency,
            )

            # Generate recommendation using deterministic engine
            if PLAN_GENERATOR:
                rec = PLAN_GENERATOR.generate_recommendation(temp_req)
            else:
                rec = Recommendation(
                    request_id=temp_req.request_id,
                    amount_safe_to_pay=0.0,
                    affordability_status="not_affordable",
                    recommended_payment_method="not_recommended",
                    payment_plan="none",
                    earliest_date_for_full_payment="",
                    spending_changes_needed="none",
                    decision_explanation="Planning engine not initialized.",
                )

            # Forecast curves
            baseline_forecast: List[Tuple[date, float]] = []
            plan_forecast: List[Tuple[date, float]] = []
            hypothetical_full_forecast: List[Tuple[date, float]] = []

            if FORECAST_ENGINE:
                baseline_forecast = FORECAST_ENGINE.simulate(
                    user_id=user_id,
                    request_date=req_date,
                    days=90,
                )

                plan_schedule: List[Tuple[date, float]] = []
                if rec.recommended_payment_method == "full_payment":
                    plan_schedule = [(req_date, req_amt)]
                elif rec.recommended_payment_method == "partial_payment":
                    plan_schedule = [(req_date, rec.amount_safe_to_pay)]
                    if rec.earliest_date_for_full_payment:
                        rem = round(req_amt - rec.amount_safe_to_pay, 2)
                        try:
                            plan_schedule.append((parse_date(rec.earliest_date_for_full_payment), rem))
                        except Exception:
                            pass
                elif rec.recommended_payment_method == "installments" and rec.payment_plan and rec.payment_plan.lower() not in ("none", ""):
                    parts = rec.payment_plan.split("|") if "|" in rec.payment_plan else rec.payment_plan.split(";")
                    for part in parts:
                        if ":" in part:
                            d_s, a_s = part.split(":", 1)
                            try:
                                plan_schedule.append((parse_date(d_s.strip()), float(a_s.strip())))
                            except Exception:
                                pass

                spending_changes_list = None
                if rec.spending_changes_needed and rec.spending_changes_needed.lower() not in ("none", ""):
                    spending_changes_list = rec.spending_changes_needed.split("|") if "|" in rec.spending_changes_needed else rec.spending_changes_needed.split(";")

                plan_forecast = FORECAST_ENGINE.simulate(
                    user_id=user_id,
                    request_date=req_date,
                    days=90,
                    extra_payments=plan_schedule,
                    spending_changes=spending_changes_list,
                )

                hypothetical_full_forecast = FORECAST_ENGINE.simulate(
                    user_id=user_id,
                    request_date=req_date,
                    days=90,
                    extra_payments=[(req_date, req_amt)],
                    spending_changes=None,
                )

            self._send_json({
                "decision": {
                    "request_id": temp_req.request_id,
                    "amount_safe_to_pay": rec.amount_safe_to_pay,
                    "affordability_status": rec.affordability_status,
                    "recommended_payment_method": rec.recommended_payment_method,
                    "payment_plan": rec.payment_plan,
                    "earliest_date_for_full_payment": rec.earliest_date_for_full_payment,
                    "spending_changes_needed": rec.spending_changes_needed,
                    "decision_explanation": rec.decision_explanation,
                },
                "profile": {
                    "user_id": user_id,
                    "home_currency": currency,
                    "available_balance": avail_bal,
                    "minimum_balance_to_keep": min_keep,
                    "payment_methods_user_will_consider": base_prof.payment_methods_user_will_consider if base_prof else [],
                },
                "forecast_chart": {
                    "dates": [str(d) for d, _ in baseline_forecast],
                    "baseline": [bal for _, bal in baseline_forecast],
                    "plan": [bal for _, bal in plan_forecast],
                    "hypothetical_full": [bal for _, bal in hypothetical_full_forecast],
                    "minimum_balance": min_keep,
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
        logger.info("Server terminated by user.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    port_num = 8000
    if len(sys.argv) > 1:
        try:
            port_num = int(sys.argv[1])
        except ValueError:
            pass
    start_server(port_num)
