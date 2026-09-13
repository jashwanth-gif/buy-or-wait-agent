"""
Plan generator module: evaluates payment methods, constructs candidate payment plans,
searches for minimal flexible spending adjustments, ranks plans by challenge hierarchy,
and generates grounded decision explanations.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
from typing import Dict, List, Optional, Sequence, Tuple
import pandas as pd

from src.data_loader import DataLoader, PaymentOption, FinancialProfile, Request
from src.financial_forecast import FinancialForecastEngine

logger = logging.getLogger(__name__)


def format_amount(val: float) -> str:
    """Format amount: no decimals if whole number, else up to 2 decimal places."""
    if abs(val - round(val)) < 1e-4:
        return str(int(round(val)))
    return f"{val:.2f}"


def format_currency_amount(val: float) -> str:
    """Format amount for human-readable explanation: with commas, no decimals if whole number."""
    if abs(val - round(val)) < 1e-4:
        return f"{int(round(val)):,}"
    return f"{val:,.2f}"


def format_date_readable(d: date) -> str:
    """Format date for explanation text, e.g. '8 August 2025' or '15 November 2019'."""
    day = d.day
    month_name = d.strftime("%B")
    year = d.year
    return f"{day} {month_name} {year}"


@dataclass
class CandidatePlan:
    method: str  # full_payment | partial_payment | installments | wait | not_recommended
    status: str  # affordable_now | affordable_with_plan | affordable_later | not_affordable
    schedule: List[Tuple[date, float]]
    spending_changes: List[str] = field(default_factory=list)
    completes_by_deadline: bool = True
    total_payable_amount: float = 0.0
    start_date: date = field(default_factory=date.today)
    num_payments: int = 1
    payment_option_id: Optional[str] = None
    description_phrase: str = ""


@dataclass
class Recommendation:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


class PlanGenerator:
    """
    Generates, verifies, and selects optimal financial decision recommendations
    according to HackerRank Orchestrate challenge criteria.
    """

    def __init__(self, data_loader: DataLoader, forecast_engine: FinancialForecastEngine):
        self.dl = data_loader
        self.engine = forecast_engine

    def _get_eligible_spending_modifications(self, user_id: str, request_date: date) -> List[dict]:
        """
        Find candidate spending modifications for flexible recurring events
        that the user is willing to stop or reduce, excluding protected categories.
        """
        prof = self.dl.profiles[user_id]
        sched = self.engine.extract_user_schedule(user_id, request_date)
        mods = []

        # 1. Monthly commitments
        for cat, item in sched["monthly_commitments"].items():
            if cat in prof.expense_categories_to_protect:
                continue

            ev_id = item["last_ev_id"]
            flex = item["flexibility"]
            desc = item["description"]
            amt = item["amount"]
            min_amt = item["min_allowed"]

            # Check stoppable
            if flex in ("stoppable", "reducible_or_stoppable") and cat in prof.expense_categories_user_is_willing_to_stop:
                mods.append({
                    "action": f"stop:{ev_id}",
                    "type": "stop",
                    "event_id": ev_id,
                    "savings": amt,
                    "description": f"Stop the {desc.lower()}",
                })

            # Check reducible
            if flex in ("reducible", "reducible_or_stoppable") and cat in prof.expense_categories_user_is_willing_to_reduce:
                new_val = min_amt if min_amt is not None else amt * 0.5
                savings = amt - new_val
                if savings > 0:
                    mods.append({
                        "action": f"reduce_to:{ev_id}:{format_amount(new_val)}",
                        "type": "reduce",
                        "event_id": ev_id,
                        "savings": savings,
                        "description": f"Reduce the {desc.lower()} to {prof.home_currency} {format_currency_amount(new_val)}",
                    })

        # 2. Variable categories (e.g. dining, groceries)
        for cat, item in sched["variable_categories"].items():
            if cat in prof.expense_categories_to_protect:
                continue

            ev_id = item["last_ev_id"]
            flex = item["flexibility"]
            desc = item["description"]
            amt = item["amount"]
            min_amt = item["min_allowed"]

            if flex in ("stoppable", "reducible_or_stoppable") and cat in prof.expense_categories_user_is_willing_to_stop:
                mods.append({
                    "action": f"stop:{ev_id}",
                    "type": "stop",
                    "event_id": ev_id,
                    "savings": amt,
                    "description": f"Stop the {desc.lower()}",
                })

            if flex in ("reducible", "reducible_or_stoppable") and cat in prof.expense_categories_user_is_willing_to_reduce:
                new_val = min_amt if min_amt is not None else amt * 0.5
                savings = amt - new_val
                if savings > 0:
                    mods.append({
                        "action": f"reduce_to:{ev_id}:{format_amount(new_val)}",
                        "type": "reduce",
                        "event_id": ev_id,
                        "savings": savings,
                        "description": f"Reduce the {desc.lower()} to {prof.home_currency} {format_currency_amount(new_val)}",
                    })

        return mods

    def generate_recommendation(self, req: Request) -> Recommendation:
        uid = req.user_id
        requested_amt = req.requested_amount
        if uid not in self.dl.profiles:
            from src.simulation import simulate_user_profile
            sim_p = simulate_user_profile(uid, requested_amount=requested_amt, home_currency=req.detected_currency or "USD")
            self.dl.profiles[uid] = FinancialProfile(
                user_id=uid,
                home_currency=sim_p.home_currency,
                current_available_balance=sim_p.available_balance,
                minimum_balance_to_keep=sim_p.minimum_balance_to_keep,
                financial_priorities=sim_p.financial_priorities,
                expense_categories_to_protect=[],
                expense_categories_user_is_willing_to_reduce=[],
                expense_categories_user_is_willing_to_stop=[],
                payment_methods_user_will_consider=["full_payment", "partial_payment", "installments", "wait"],
                max_installment_months=12.0,
            )
        prof = self.dl.profiles[uid]
        r_date = req.request_date
        comp_date = req.desired_completion_date

        # 1. Base safety measurements
        safe_to_pay = self.engine.compute_amount_safe_to_pay(uid, r_date, requested_amt)
        earliest_full_date = self.engine.compute_earliest_full_payment_date(uid, r_date, requested_amt)

        considered_methods = set(prof.payment_methods_user_will_consider)
        candidate_plans: List[CandidatePlan] = []

        # 2. Candidate: Full Payment
        if "full_payment" in considered_methods:
            full_sched = [(r_date, requested_amt)]
            # Check without changes
            if safe_to_pay >= (requested_amt - 1e-4):
                candidate_plans.append(CandidatePlan(
                    method="full_payment",
                    status="affordable_now",
                    schedule=full_sched,
                    spending_changes=[],
                    completes_by_deadline=(r_date <= comp_date),
                    total_payable_amount=requested_amt,
                    start_date=r_date,
                    num_payments=1,
                    payment_option_id=None,
                ))
            else:
                # Check with spending changes
                mods = self._get_eligible_spending_modifications(uid, r_date)
                comp_span = max(14, (comp_date - r_date).days + 1)
                found_change = False
                for m in mods:
                    if self.engine.is_plan_safe(uid, r_date, full_sched, spending_changes=[m["action"]], eval_days=comp_span):
                        candidate_plans.append(CandidatePlan(
                            method="full_payment",
                            status="affordable_with_plan",
                            schedule=full_sched,
                            spending_changes=[m["action"]],
                            completes_by_deadline=(r_date <= comp_date),
                            total_payable_amount=requested_amt,
                            start_date=r_date,
                            num_payments=1,
                            payment_option_id=None,
                            description_phrase=m["description"],
                        ))
                        found_change = True
                        break

                # Try 2 modifications
                if not found_change and len(mods) >= 2:
                    for i in range(len(mods)):
                        for j in range(i + 1, len(mods)):
                            m1, m2 = mods[i], mods[j]
                            if m1["event_id"] == m2["event_id"]:
                                continue
                            actions = [m1["action"], m2["action"]]
                            if self.engine.is_plan_safe(uid, r_date, full_sched, spending_changes=actions, eval_days=comp_span):
                                phrase = f"{m1['description']} and {m2['description'][:1].lower() + m2['description'][1:]}"
                                candidate_plans.append(CandidatePlan(
                                    method="full_payment",
                                    status="affordable_with_plan",
                                    schedule=full_sched,
                                    spending_changes=actions,
                                    completes_by_deadline=(r_date <= comp_date),
                                    total_payable_amount=requested_amt,
                                    start_date=r_date,
                                    num_payments=1,
                                    payment_option_id=None,
                                    description_phrase=phrase,
                                ))
                                found_change = True
                                break
                        if found_change:
                            break

        # 3. Candidate: Installment Options
        if "installments" in considered_methods and prof.max_installment_months is not None:
            opts = self.dl.payment_options.get(req.request_id, [])
            for opt in opts:
                if opt.payment_method != "installments":
                    continue

                n_pmts = opt.number_of_payments
                freq = opt.payment_frequency_days or 30.0
                total_span_days = (n_pmts - 1) * freq
                duration_months = total_span_days / 30.0

                if duration_months > (prof.max_installment_months + 1e-4):
                    continue

                inst_sched = []
                for i in range(n_pmts):
                    p_date = opt.first_payment_date + timedelta(days=int(round(i * freq)))
                    inst_sched.append((p_date, opt.payment_amount))

                last_pmt_date = inst_sched[-1][0]
                completes_deadline = (last_pmt_date <= comp_date)
                inst_span = max(14, (last_pmt_date - r_date).days + 1, (comp_date - r_date).days + 1)

                if self.engine.is_plan_safe(uid, r_date, inst_sched, spending_changes=None, eval_days=inst_span):
                    candidate_plans.append(CandidatePlan(
                        method="installments",
                        status="affordable_with_plan",
                        schedule=inst_sched,
                        spending_changes=[],
                        completes_by_deadline=completes_deadline,
                        total_payable_amount=opt.total_payable_amount,
                        start_date=opt.first_payment_date,
                        num_payments=n_pmts,
                        payment_option_id=opt.payment_option_id,
                    ))

        # 4. Candidate: Partial Payment
        if (
            req.allows_partial_payment
            and "partial_payment" in considered_methods
            and 0 < safe_to_pay < requested_amt
            and earliest_full_date is not None
            and earliest_full_date <= comp_date
        ):
            part_sched = [
                (r_date, safe_to_pay),
                (earliest_full_date, round(requested_amt - safe_to_pay, 2)),
            ]
            part_span = max(14, (earliest_full_date - r_date).days + 1, (comp_date - r_date).days + 1)
            if self.engine.is_plan_safe(uid, r_date, part_sched, spending_changes=None, eval_days=part_span):
                candidate_plans.append(CandidatePlan(
                    method="partial_payment",
                    status="affordable_with_plan",
                    schedule=part_sched,
                    spending_changes=[],
                    completes_by_deadline=(earliest_full_date <= comp_date),
                    total_payable_amount=requested_amt,
                    start_date=r_date,
                    num_payments=2,
                    payment_option_id=None,
                ))

        # 5. Candidate: Wait
        if "full_payment" in considered_methods and earliest_full_date is not None and earliest_full_date > r_date:
            wait_sched = [(earliest_full_date, requested_amt)]
            candidate_plans.append(CandidatePlan(
                method="wait",
                status="affordable_later",
                schedule=wait_sched,
                spending_changes=[],
                completes_by_deadline=(earliest_full_date <= comp_date),
                total_payable_amount=requested_amt,
                start_date=earliest_full_date,
                num_payments=1,
                payment_option_id=None,
            ))

        # 6. Plan Ranking Hierarchy according to Prompt Specification:
        # 1. Prefer affordable_now if safe
        # 2. Next, prefer affordable_with_plan (installments > partial > spending changes) if deadline met
        # 3. Next, affordable_later (wait until earliest_date_for_full_payment) if within forecast and user willing
        # 4. Otherwise, not_affordable
        status_rank = {
            "affordable_now": 0,
            "affordable_with_plan": 1,
            "affordable_later": 2,
            "not_affordable": 3,
        }
        method_rank = {
            "full_payment": 0 if any(p.status == "affordable_now" for p in candidate_plans) else 3,
            "installments": 1,
            "partial_payment": 2,
            "wait": 4,
            "not_recommended": 5,
        }

        def plan_rank_key(p: CandidatePlan):
            # Plans that don't complete by deadline are penalized
            deadline_penalty = 0 if p.completes_by_deadline else 10
            return (
                deadline_penalty,
                status_rank.get(p.status, 9),
                0 if not p.spending_changes else 1,
                p.total_payable_amount,
                p.start_date,
                p.num_payments,
                p.payment_option_id or "zzzz",
            )

        sorted_plans = sorted(candidate_plans, key=plan_rank_key)

        # Filter only plans that complete by deadline if any exists
        viable_plans = [p for p in sorted_plans if p.completes_by_deadline]
        chosen_plan = viable_plans[0] if viable_plans else (sorted_plans[0] if sorted_plans else None)

        # If chosen plan does not complete by deadline and is wait or not safe, fallback to not_recommended
        if chosen_plan and not chosen_plan.completes_by_deadline and chosen_plan.method in ("wait", "installments"):
            chosen_plan = None

        curr = prof.home_currency
        min_bal_fmt = format_currency_amount(prof.minimum_balance_to_keep)
        req_amt_fmt = format_currency_amount(requested_amt)

        if chosen_plan is None:
            # Fallback: not_recommended
            plan_str = "none"
            spending_str = "none"
            earliest_str = str(earliest_full_date) if earliest_full_date else ""
            status = "not_affordable"
            method = "not_recommended"

            if safe_to_pay > 0:
                explanation = (
                    f"Do not proceed with the {curr} {req_amt_fmt} request. "
                    f"Although {curr} {format_currency_amount(safe_to_pay)} is available today, "
                    f"the full amount cannot be completed safely within 90 days."
                )
            else:
                explanation = (
                    f"Do not make this payment by {format_date_readable(comp_date)}. "
                    f"None of the available options keeps the {curr} {min_bal_fmt} minimum protected."
                )
            earliest_str = ""  # AGENTS.md: empty when no full payment safe or not_affordable
        else:
            method = chosen_plan.method
            status = chosen_plan.status
            spending_str = "|".join(chosen_plan.spending_changes) if chosen_plan.spending_changes else "none"
            earliest_str = str(earliest_full_date) if earliest_full_date else (str(r_date) if status == "affordable_now" else "")

            # Build payment_plan string
            if method == "full_payment":
                plan_str = f"{r_date}:{format_amount(requested_amt)}"
                if status == "affordable_now":
                    earliest_str = str(r_date)
                    explanation = (
                        f"Pay {curr} {req_amt_fmt} today. "
                        f"This leaves at least {curr} {min_bal_fmt} available over the next 90 days."
                    )
                else:
                    phrase = chosen_plan.description_phrase or "Adjust optional spending"
                    explanation = (
                        f"{phrase}, then pay {curr} {req_amt_fmt} today. "
                        f"This leaves at least {curr} {min_bal_fmt} available."
                    )

            elif method == "installments":
                plan_str = "|".join(f"{d}:{format_amount(a)}" for d, a in chosen_plan.schedule)
                pmt_amt_fmt = format_currency_amount(chosen_plan.schedule[0][1])
                start_readable = format_date_readable(chosen_plan.start_date)
                explanation = (
                    f"Use {chosen_plan.num_payments} installments of {curr} {pmt_amt_fmt}, "
                    f"starting {start_readable}. This leaves at least {curr} {min_bal_fmt} available."
                )

            elif method == "partial_payment":
                p1_date, p1_amt = chosen_plan.schedule[0]
                p2_date, p2_amt = chosen_plan.schedule[1]
                plan_str = f"{p1_date}:{format_amount(p1_amt)}|{p2_date}:{format_amount(p2_amt)}"
                explanation = (
                    f"Pay {curr} {format_currency_amount(p1_amt)} today and the remaining "
                    f"{curr} {format_currency_amount(p2_amt)} on {format_date_readable(p2_date)}. "
                    f"This completes the full request and keeps the {curr} {min_bal_fmt} minimum protected."
                )

            elif method == "wait":
                earliest_d = chosen_plan.schedule[0][0]
                earliest_str = str(earliest_d)
                plan_str = f"{earliest_d}:{format_amount(requested_amt)}"
                explanation = (
                    f"Pay {curr} {req_amt_fmt} in full on {format_date_readable(earliest_d)}. "
                    f"Paying earlier would take the balance below the {curr} {min_bal_fmt} minimum."
                )

        return Recommendation(
            request_id=req.request_id,
            amount_safe_to_pay=safe_to_pay,
            affordability_status=status,
            recommended_payment_method=method,
            payment_plan=plan_str,
            earliest_date_for_full_payment=earliest_str,
            spending_changes_needed=spending_str,
            decision_explanation=explanation,
        )


PlanResult = CandidatePlan


def generate_payment_plans(request, profile, events, payment_options, amount_safe_to_pay, earliest_date_for_full_payment):
    return []


def rank_plans(candidate_plans, request_date=None):
    if candidate_plans:
        return candidate_plans[0]
    return CandidatePlan(method="not_recommended", status="not_affordable", schedule=[])


def generate_explanation(plan, request, profile, amount_safe_to_pay, earliest_date):
    return ""


def generate_spending_change_sets(events=None):
    return [[]]


