"""
ChatBot assistant engine for the Buy or Wait Agent.
Provides natural-language financial advisory, request lookup,
scenario what-if testing, and dataset statistics explanation.
"""
from datetime import datetime, date
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

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


class FinancialChatBot:
    def __init__(self, records_path: str = "records.csv", output_path: str = "evaluation/output.csv"):
        self.records_path = records_path
        self.output_path = output_path
        self.requests: Dict[str, Request] = {}
        self.user_to_requests: Dict[str, List[Request]] = {}
        self.profiles: Dict[str, UserProfile] = {}
        self.output_df: Optional[pd.DataFrame] = None
        self.load_data()

    def load_data(self):
        try:
            reqs = load_requests(self.records_path)
            self.requests = {r.request_id.lower(): r for r in reqs}
            self.user_to_requests = {}
            for r in reqs:
                uid = r.user_id.lower()
                self.user_to_requests.setdefault(uid, []).append(r)
                if uid not in self.profiles:
                    self.profiles[uid] = simulate_user_profile(
                        user_id=r.user_id,
                        requested_amount=r.requested_amount,
                        home_currency=r.detected_currency,
                    )
            if os.path.exists(self.output_path):
                self.output_df = pd.read_csv(self.output_path, keep_default_na=False).fillna("")
        except Exception as e:
            print(f"Warning in FinancialChatBot.load_data: {e}")

    def evaluate_request(self, request_obj: Request, profile: Optional[UserProfile] = None) -> Dict[str, Any]:
        """Run the deterministic planning engine on a request object."""
        if self.output_df is not None and not self.output_df.empty:
            matches = self.output_df[self.output_df["request_id"].str.lower() == request_obj.request_id.lower()]
            orig_req = self.requests.get(request_obj.request_id.lower())
            if not matches.empty and (orig_req is None or orig_req.requested_amount == request_obj.requested_amount):
                row = matches.iloc[0]
                prof = profile or self.profiles.get(request_obj.user_id.lower())
                curr = request_obj.detected_currency or (prof.home_currency if prof else "USD")
                avail = prof.available_balance if prof else 0.0
                min_k = prof.minimum_balance_to_keep if prof else 0.0
                return {
                    "request_id": str(row["request_id"]),
                    "user_id": request_obj.user_id,
                    "request_date": str(request_obj.request_date),
                    "request_type": request_obj.request_type,
                    "requested_amount": request_obj.requested_amount,
                    "desired_completion_date": str(request_obj.desired_completion_date),
                    "allows_partial_payment": request_obj.allows_partial_payment,
                    "currency": curr,
                    "available_balance": avail,
                    "minimum_balance_to_keep": min_k,
                    "amount_safe_to_pay": float(row["amount_safe_to_pay"]),
                    "affordability_status": str(row["affordability_status"]),
                    "recommended_payment_method": str(row["recommended_payment_method"]),
                    "payment_plan": str(row["payment_plan"]),
                    "earliest_date_for_full_payment": str(row["earliest_date_for_full_payment"]),
                    "spending_changes_needed": str(row["spending_changes_needed"]),
                    "decision_explanation": str(row["decision_explanation"]),
                }

        from main import process_single_request
        rec = process_single_request(request_obj)
        prof = profile or self.profiles.get(request_obj.user_id.lower())
        curr = request_obj.detected_currency or (prof.home_currency if prof else "USD")
        avail = prof.available_balance if prof else 0.0
        min_k = prof.minimum_balance_to_keep if prof else 0.0
        return {
            "request_id": request_obj.request_id,
            "user_id": request_obj.user_id,
            "request_date": str(request_obj.request_date),
            "request_type": request_obj.request_type,
            "requested_amount": request_obj.requested_amount,
            "desired_completion_date": str(request_obj.desired_completion_date),
            "allows_partial_payment": request_obj.allows_partial_payment,
            "currency": curr,
            "available_balance": avail,
            "minimum_balance_to_keep": min_k,
            "amount_safe_to_pay": float(rec["amount_safe_to_pay"]),
            "affordability_status": str(rec["affordability_status"]),
            "recommended_payment_method": str(rec["recommended_payment_method"]),
            "payment_plan": str(rec["payment_plan"]),
            "earliest_date_for_full_payment": str(rec["earliest_date_for_full_payment"]),
            "spending_changes_needed": str(rec["spending_changes_needed"]),
            "decision_explanation": str(rec["decision_explanation"]),
        }

    def handle_message(self, user_msg: str) -> Dict[str, Any]:
        """Process user message and return structured conversational response."""
        text = user_msg.strip()
        lower = text.lower()

        # 1. Greetings & Capabilities
        if any(w in lower for w in ["hi", "hello", "hey", "who are you", "what can you do", "help"]):
            return {
                "reply": (
                    "👋 **Hello! I'm your Buy or Wait AI Financial Advisory Assistant.**\n\n"
                    "I am powered by a **deterministic 90-day cashflow simulation engine** that analyzes user income, essential expenses, "
                    "safety reserve thresholds, and payment flexibility.\n\n"
                    "Here is what you can ask me:\n"
                    "- 🔍 **Inspect any request**: *\"Tell me about request_26\"* or *\"Can user_26 afford this?\"*\n"
                    "- 📊 **Dataset insights**: *\"Show me all not affordable requests\"* or *\"How many cases are affordable later?\"*\n"
                    "- 🔮 **What-If Scenarios**: *\"What if request 26 was 5,000,000 IDR?\"*\n"
                    "- 💡 **Methodology**: *\"How is Amount Safe to Pay calculated?\"* or *\"What are spending adjustments?\"*"
                ),
                "quick_suggestions": [
                    "Inspect Request 26",
                    "Why are some cases Not Affordable?",
                    "How is Safe to Pay calculated?",
                    "What if Request 26 is 5M IDR?",
                ],
            }

        # 2. Check for Specific Request reference (e.g. request_26, req 26, user_26, or standalone 26)
        req_match = re.search(r"\b(?:request[_\s]?|req[_\s]?|case[_\s]?)(\d+)\b", lower)
        user_match = re.search(r"\buser[_\s]?(\d+)\b", lower)
        target_req: Optional[Request] = None

        if req_match:
            req_id_key = f"request_{req_match.group(1)}"
            target_req = self.requests.get(req_id_key)
        elif user_match:
            uid_key = f"user_{user_match.group(1)}"
            user_reqs = self.user_to_requests.get(uid_key, [])
            if user_reqs:
                target_req = user_reqs[0]
        elif re.match(r"^\d+$", lower):
            req_id_key = f"request_{lower}"
            target_req = self.requests.get(req_id_key)

        # 3. If a request is found, check if user is asking a What-If scenario on it
        if target_req:
            # Check for what-if amount modification
            what_if_amt_match = re.search(r"(?:amount|was|is|reduce to|cut to)\s+(?:of\s+)?(?:[a-z]{3}\s*)?([0-9,]+(?:\.[0-9]+)?)\s*(?:[a-z]{3}|m|k)?", lower)
            has_what_if = "what if" in lower or "what-if" in lower or "suppose" in lower or "if it was" in lower

            if has_what_if and what_if_amt_match:
                raw_val = what_if_amt_match.group(1).replace(",", "")
                try:
                    mult = 1.0
                    if "m" in lower:
                        mult = 1000000.0
                    elif "k" in lower:
                        mult = 1000.0
                    new_amount = float(raw_val) * mult if mult > 1.0 and float(raw_val) < 10000 else float(raw_val)

                    # Baseline evaluation
                    base_eval = self.evaluate_request(target_req)

                    # Modified request evaluation
                    import copy
                    mod_req = copy.deepcopy(target_req)
                    mod_req.requested_amount = new_amount

                    mod_eval = self.evaluate_request(mod_req)

                    curr = target_req.detected_currency or "USD"
                    reply = (
                        f"🔮 **What-If Scenario Analysis for `{target_req.request_id.upper()}`**\n\n"
                        f"**Hypothetical Change**: Requested amount changed from `{target_req.requested_amount:,.2f} {curr}` ➡️ **`{new_amount:,.2f} {curr}`**\n\n"
                        f"### Comparison Results:\n"
                        f"| Metric | Baseline | What-If Scenario |\n"
                        f"| :--- | :--- | :--- |\n"
                        f"| **Requested Amount** | {target_req.requested_amount:,.2f} {curr} | **{new_amount:,.2f} {curr}** |\n"
                        f"| **Affordability Status** | `{base_eval['affordability_status']}` | **`{mod_eval['affordability_status']}`** |\n"
                        f"| **Payment Method** | `{base_eval['recommended_payment_method']}` | **`{mod_eval['recommended_payment_method']}`** |\n"
                        f"| **Earliest Pay Date** | {base_eval['earliest_date_for_full_payment']} | **{mod_eval['earliest_date_for_full_payment']}** |\n"
                        f"| **Amount Safe Today** | {base_eval['amount_safe_to_pay']:,.2f} {curr} | {mod_eval['amount_safe_to_pay']:,.2f} {curr} |\n\n"
                        f"💡 **Advisory Insight**: \n{mod_eval['decision_explanation']}"
                    )
                    return {
                        "reply": reply,
                        "request_id": target_req.request_id,
                        "details": mod_eval,
                        "quick_suggestions": [
                            f"Show baseline {target_req.request_id}",
                            "Try another What-If scenario",
                            "Open in What-If Playground",
                        ],
                    }
                except Exception as e:
                    pass

            # Standard Request Consultation
            eval_res = self.evaluate_request(target_req)
            curr = eval_res["currency"]
            status = eval_res["affordability_status"]

            status_emojis = {
                "affordablenow": "✅ **Affordable Now**",
                "affordablewithplan": "💳 **Affordable With Plan**",
                "affordablelater": "⏳ **Affordable Later**",
                "notaffordable": "🚫 **Not Affordable**",
            }
            status_badge = status_emojis.get(status, f"**{status}**")

            reply = (
                f"### 📋 Financial Advisory for `{target_req.request_id.upper()}`\n\n"
                f"**User**: `{target_req.user_id}` | **Category**: `{target_req.request_type}` | **Date**: `{target_req.request_date}`\n"
                f"**Requested Amount**: **{target_req.requested_amount:,.2f} {curr}** (Target: {target_req.desired_completion_date})\n"
                f"**User Request Note**: *\"{target_req.request_text}\"*\n\n"
                f"----\n"
                f"#### 💳 Financial Health & Evaluation\n"
                f"- **Available Balance**: `{eval_res['available_balance']:,.2f} {curr}`\n"
                f"- **Safety Reserve Needed**: `{eval_res['minimum_balance_to_keep']:,.2f} {curr}`\n"
                f"- **Amount Safe to Pay Today**: **`{eval_res['amount_safe_to_pay']:,.2f} {curr}`**\n"
                f"- **Affordability Verdict**: {status_badge}\n"
                f"- **Recommended Method**: `{eval_res['recommended_payment_method']}`\n"
                f"- **Earliest Date for Full Payment**: `{eval_res['earliest_date_for_full_payment'] or 'N/A'}`\n"
                f"- **Spending Changes Needed**: `{eval_res['spending_changes_needed']}`\n\n"
                f"#### 💡 Why This Decision?\n"
                f"{eval_res['decision_explanation']}\n\n"
                f"----\n"
                f"📌 **Actionable Advice**: "
            )

            if status == "affordablenow":
                reply += f"The user has sufficient surplus over the reserve threshold. Full payment of {target_req.requested_amount:,.2f} {curr} can proceed safely today."
            elif status == "affordablewithplan":
                reply += f"The user cannot pay the entire sum in one shot today, but an installment plan or partial payment keeps their cashflow positive throughout the 90-day period."
            elif status == "affordablelater":
                reply += f"Advise the user to wait until **{eval_res['earliest_date_for_full_payment']}** when confirmed recurring income replenishes their account balance."
            else:
                reply += f"Even with 90-day cashflows and potential spending cutbacks, this requested expenditure breaches the user's essential reserve. Suggest postponing or seeking outside financing."

            return {
                "reply": reply,
                "request_id": target_req.request_id,
                "details": eval_res,
                "quick_suggestions": [
                    f"What if {target_req.request_id} amount was half?",
                    f"Why can't {target_req.user_id} pay in full today?",
                    "Check another request",
                ],
            }

        # 4. Status / Categories Queries
        if any(w in lower for w in ["not affordable", "notaffordable", "unaffordable", "can't afford"]):
            count = 48
            if self.output_df is not None:
                count = len(self.output_df[self.output_df["affordability_status"] == "notaffordable"])
            pct = (count / 250) * 100
            return {
                "reply": (
                    f"🚫 **Not Affordable Cases Overview ({count} Requests / {pct:.1f}%)**\n\n"
                    f"In our 250 simulated user scenarios, **{count} requests** were classified as **`notaffordable`**.\n\n"
                    f"**Why are these classified as Not Affordable?**\n"
                    f"1. **High Requested Amount vs. Income**: The expenditure exceeds the user's total projected 90-day surplus.\n"
                    f"2. **Strict Reserve Invariant**: Making full, partial, or installment payments would cause the daily cash balance to drop below the user's **Minimum Reserve Threshold** ($B_t < B_{{min}}$).\n"
                    f"3. **Spending Cuts Insufficient**: Even eliminating flexible recurring spending (such as digital subscriptions and dining out), the account cannot recover before the desired date.\n\n"
                    f"**Representative Cases to Inspect**:\n"
                    f"- `request_27`: Laptop purchase with high commitment vs liquid balance.\n"
                    f"- `request_28`: Investment contribution breaching safety reserve threshold.\n"
                    f"- `request_51`: Requested amount greater than available liquid capital.\n\n"
                    f"💡 *Ask me to 'inspect request_27' to examine the exact numbers!*"
                ),
                "quick_suggestions": [
                    "Inspect Request 27",
                    "Inspect Request 28",
                    "How are reserves calculated?",
                    "Show Affordable Later Cases",
                ],
            }

        if any(w in lower for w in ["affordable later", "affordablelater", "wait", "when to pay"]):
            count = 42
            if self.output_df is not None:
                count = len(self.output_df[self.output_df["affordability_status"] == "affordablelater"])
            pct = (count / 250) * 100
            return {
                "reply": (
                    f"⏳ **Affordable Later Cases Overview ({count} Requests / {pct:.1f}%)**\n\n"
                    f"**{count} requests** are safe to execute, but **not today**. The engine recommends **`wait`**.\n\n"
                    f"**Key Characteristics**:\n"
                    f"- Full payment today would deplete reserves below safe minimums.\n"
                    f"- However, a confirmed recurring salary/income credit arrives before the desired completion date.\n"
                    f"- The engine computes the **Earliest Date for Full Payment** after that income event occurs.\n\n"
                    f"**Example Case**: `request_26` (IDR 15,656,000) — Paying today is unsafe, but waiting until **2025-10-25** allows full safe payment immediately after payday."
                ),
                "quick_suggestions": [
                    "Inspect Request 26",
                    "Inspect Request 15",
                    "How do installments compare?",
                ],
            }

        if any(w in lower for w in ["with plan", "affordablewithplan", "installments", "partial payment"]):
            count = 53
            if self.output_df is not None:
                count = len(self.output_df[self.output_df["affordability_status"] == "affordablewithplan"])
            return {
                "reply": (
                    f"💳 **Affordable With Plan Overview ({count} Requests / 21.2%)**\n\n"
                    f"**{count} requests** are safely payable by structuring the outflow:\n"
                    f"- **Installment Schedules**: 2 to 4 payments matching cashflow inflows without penalty.\n"
                    f"- **Partial Payments**: Paying the maximum safe amount today, and the balance when cashflow replenishes.\n"
                    f"- **Spending Adjustments**: Pausing non-essential subscriptions or dining spending to free up needed liquidity."
                ),
                "quick_suggestions": [
                    "Inspect Request 2",
                    "Inspect Request 5",
                    "What spending can be cut?",
                ],
            }

        # 5. Methodology & Rules
        if any(w in lower for w in ["safe to pay", "how is safe", "formula", "reserve", "calculation"]):
            return {
                "reply": (
                    "📐 **How Amount Safe to Pay Today is Calculated**\n\n"
                    "The engine performs a backward and forward constraint propagation over the 90-day balance forecast:\n\n"
                    "1. **90-Day Trajectory Simulation**:\n"
                    "   $$B_t = B_{t-1} + \\text{Income}_t - \\text{EssentialExpenses}_t$$\n"
                    "2. **Minimum Reserve Constraint**:\n"
                    "   $$B_t - X \\ge B_{min} \\quad \\forall t \\in [0, 90]$$\n"
                    "   Where $X$ is any payment made on `request_date`.\n"
                    "3. **Safe Amount Definition**:\n"
                    "   $$\\text{safe\\_to\\_pay} = \\min_{0 \\le t \\le 90} (B_t - B_{min})$$\n"
                    "   - If this amount is $\\le 0$, safe to pay today is $0.00$.\n"
                    "   - If this amount is $\\ge$ requested amount, the request is **Affordable Now**."
                ),
                "quick_suggestions": [
                    "Inspect Request 26",
                    "How do spending changes work?",
                    "What currencies are supported?",
                ],
            }

        if any(w in lower for w in ["currency", "currencies", "exchange rate"]):
            return {
                "reply": (
                    "💱 **Supported Currencies & Fixed Exchange Rates**\n\n"
                    "The system supports multi-currency financial evaluation across 5 world currencies:\n"
                    "- **USD**: 1.0 (Base reference)\n"
                    "- **EUR**: 1.08 USD\n"
                    "- **INR**: 0.012 USD (1 USD ≈ 83.33 INR)\n"
                    "- **ZAR**: 0.055 USD (1 ZAR ≈ 18.18 USD)\n"
                    "- **IDR**: 0.000064 USD (1 USD ≈ 15,625 IDR)\n\n"
                    "Foreign currency events (like international SaaS subscriptions) are automatically converted into the user's home currency."
                ),
                "quick_suggestions": [
                    "Inspect Request 26 (IDR)",
                    "Inspect Request 27 (ZAR)",
                    "How is Safe to Pay calculated?",
                ],
            }

        # 6. Fallback: Search keywords across request descriptions
        matches = []
        for r in self.requests.values():
            if any(term in r.request_text.lower() or term in r.request_type.lower() for term in lower.split()):
                matches.append(r)
                if len(matches) >= 3:
                    break

        if matches:
            match_lines = [f"- **`{m.request_id}`** ({m.request_type}): {m.requested_amount:,.2f} {m.detected_currency} — *\"{m.request_text[:80]}...\"*" for m in matches]
            return {
                "reply": (
                    f"🔍 I couldn't find an exact request ID match, but I found these relevant cases matching your terms:\n\n"
                    + "\n".join(match_lines) + "\n\n"
                    f"Would you like me to inspect one of these requests in detail?"
                ),
                "quick_suggestions": [f"Inspect {m.request_id}" for m in matches],
            }

        # Default help
        return {
            "reply": (
                "🤖 I'm here to help you navigate financial decisions, cashflow simulations, and affordability plans!\n\n"
                "Try asking:\n"
                "- *\"Tell me about request_26\"*\n"
                "- *\"What if request 26 was 5,000,000 IDR?\"*\n"
                "- *\"Show me not affordable cases\"*\n"
                "- *\"How is the safe amount calculated?\"*"
            ),
            "quick_suggestions": [
                "Inspect Request 26",
                "Show Not Affordable Cases",
                "How is Safe to Pay calculated?",
                "Inspect Request 1",
            ],
        }
