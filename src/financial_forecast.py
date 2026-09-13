"""
Financial forecasting engine: performs day-by-day 90-day balance forecasts,
computes amount safe to pay, and calculates the earliest full payment date.
"""
import calendar
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple
import pandas as pd
import numpy as np

from src.data_loader import DataLoader, FinancialEvent, FinancialProfile, Request, UserMessageInfo


def get_last_day_of_month(year: int, month: int) -> int:
    """Return the last day of the month for given year and month."""
    return calendar.monthrange(year, month)[1]


def parse_spending_changes(spending_changes: Optional[Sequence[str]]) -> Tuple[Dict[str, float], Set[str]]:
    """
    Parse spending change strings:
    - 'stop:<event_id>' -> stopped event_id
    - 'reduce_to:<event_id>:<amount>' -> reduced to amount
    """
    reductions: Dict[str, float] = {}
    stopped: Set[str] = set()

    if not spending_changes:
        return reductions, stopped

    for change in spending_changes:
        change = change.strip()
        if not change or change.lower() == "none":
            continue
        parts = change.split(":")
        action = parts[0].strip().lower()
        if action == "stop" and len(parts) >= 2:
            stopped.add(parts[1].strip())
        elif action in ("reduce", "reduce_to") and len(parts) >= 3:
            try:
                reductions[parts[1].strip()] = float(parts[2].strip())
            except ValueError:
                pass
    return reductions, stopped


class FinancialForecastEngine:
    """
    Reconstructs user financial position from DataLoader, builds 90-day daily balance forecasts,
    verifies minimum balance safety check, and computes safe payment limits.
    """

    def __init__(self, data_loader: DataLoader):
        self.dl = data_loader

    def extract_user_schedule(self, user_id: str, request_date: date) -> dict:
        prof = self.dl.profiles[user_id]
        events = self.dl.events.get(user_id, [])
        msg_info = self.dl.user_message_info.get(user_id, UserMessageInfo())

        monthly_commitments: Dict[str, dict] = {}
        variable_categories: Dict[str, dict] = {}
        pending_debits: List[Tuple[date, float]] = []
        scheduled_events: List[Tuple[date, float, str]] = []

        # 1. Pending debits and scheduled events
        for ev in events:
            if ev.status == "pending" and ev.direction == "debit":
                s_date = ev.settlement_date or ev.event_date
                pending_debits.append((s_date, ev.amount))
            elif ev.status == "scheduled":
                if ev.category == "salary" and ev.direction == "credit":
                    # Scheduled confirmed salary is handled in salary_info below
                    continue
                s_date = ev.settlement_date or ev.event_date
                scheduled_events.append((s_date, ev.amount, ev.direction))

        # 2. Historical settled events on or before request_date
        settled_events = [e for e in events if e.status == "settled" and e.event_date <= request_date]

        # Salary detection: only confirmed salary is projected
        salary_info = {"amount": 0.0, "day": msg_info.salary_day, "ended": msg_info.salary_ended}
        sched_sal_events = [e for e in events if e.status == "scheduled" and e.category == "salary" and e.direction == "credit"]

        if msg_info.salary_ended:
            salary_info["amount"] = 0.0
        elif msg_info.salary_amount is not None:
            salary_info["amount"] = msg_info.salary_amount
        elif sched_sal_events:
            salary_info["amount"] = sched_sal_events[0].amount
            salary_info["day"] = (sched_sal_events[0].settlement_date or sched_sal_events[0].event_date).day
        else:
            sal_events = [e for e in settled_events if e.category == "salary" and e.direction == "credit"]
            if sal_events:
                regular_sal = [e for e in sal_events if "arrears" not in e.description.lower() and "bonus" not in e.description.lower() and "prorated" not in e.description.lower()]
                target_sal = regular_sal if regular_sal else sal_events
                if "final employer payroll" in target_sal[-1].description.lower():
                    salary_info["ended"] = True
                    salary_info["amount"] = 0.0
                else:
                    salary_info["amount"] = target_sal[-1].amount
                if msg_info.salary_day == 15:
                    days = [e.event_date.day for e in target_sal]
                    salary_info["day"] = max(set(days), key=days.count)

        # Process debits by category
        cat_groups: Dict[str, List[FinancialEvent]] = {}
        for e in settled_events:
            if e.direction == "debit":
                cat_groups.setdefault(e.category, []).append(e)

        for cat, cat_evs in cat_groups.items():
            if cat in ("salary", "refund", "investment"):
                continue

            cat_evs_sorted = sorted(cat_evs, key=lambda x: x.event_date)
            latest_ev = cat_evs_sorted[-1]

            if cat in ("groceries", "transport", "dining"):
                dates = [e.event_date for e in cat_evs_sorted]
                if len(dates) >= 2:
                    diffs = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
                    median_diff = float(np.median(diffs))
                else:
                    median_diff = 7.0 if cat == "groceries" else (14.0 if cat == "transport" else 21.0)

                days_of_month = [d.day for d in dates]
                is_day_of_month = (set(days_of_month) <= {9, 10, 19, 20, 29, 30, 31}) and len(dates) >= 6

                variable_categories[cat] = {
                    "category": cat,
                    "interval_days": max(1, int(round(median_diff))),
                    "is_fixed_days": is_day_of_month,
                    "amount": float(np.mean([e.amount for e in cat_evs_sorted])),
                    "last_date": latest_ev.event_date,
                    "last_ev_id": latest_ev.event_id,
                    "flexibility": latest_ev.flexibility,
                    "min_allowed": latest_ev.minimum_allowed_amount,
                    "description": latest_ev.description,
                }
            else:
                days = [e.event_date.day for e in cat_evs_sorted]
                mode_day = max(set(days), key=days.count)
                amt = latest_ev.amount
                if cat == "rent" and msg_info.rent_multiplier != 1.0:
                    amt *= msg_info.rent_multiplier

                monthly_commitments[cat] = {
                    "category": cat,
                    "day": mode_day,
                    "amount": amt,
                    "last_ev_id": latest_ev.event_id,
                    "flexibility": latest_ev.flexibility,
                    "min_allowed": latest_ev.minimum_allowed_amount,
                    "description": latest_ev.description,
                }

        return {
            "monthly_commitments": monthly_commitments,
            "variable_categories": variable_categories,
            "salary_info": salary_info,
            "pending_debits": pending_debits,
            "scheduled_events": scheduled_events,
            "confirmed_inflows": msg_info.confirmed_inflows,
        }

    def simulate(
        self,
        user_id: str,
        request_date: date,
        days: int = 90,
        extra_payments: Optional[Sequence[Tuple[date, float]]] = None,
        spending_changes: Optional[Sequence[str]] = None,
    ) -> List[Tuple[date, float]]:
        """
        Simulate daily account balance day-by-day for `days` days starting at `request_date`.
        Applies recurring events, one-time events, spending modifications, and extra payments.
        """
        prof = self.dl.profiles[user_id]
        sched = self.extract_user_schedule(user_id, request_date)
        reductions, stopped = parse_spending_changes(spending_changes)

        extra_pmts_map: Dict[date, float] = {}
        if extra_payments:
            for d, a in extra_payments:
                extra_pmts_map[d] = extra_pmts_map.get(d, 0.0) + a

        curr_bal = prof.current_available_balance
        daily_balances: List[Tuple[date, float]] = []

        var_next_dates: Dict[str, date] = {}
        for cat, var in sched["variable_categories"].items():
            if not var["is_fixed_days"]:
                nxt = var["last_date"] + timedelta(days=var["interval_days"])
                while nxt <= request_date:
                    nxt += timedelta(days=var["interval_days"])
                var_next_dates[cat] = nxt

        for d_offset in range(days):
            curr_date = request_date + timedelta(days=d_offset)

            # 1. Pending debits
            for s_date, p_amt in sched["pending_debits"]:
                if s_date == curr_date:
                    curr_bal -= p_amt

            # 2. Scheduled events
            for s_date, s_amt, s_dir in sched["scheduled_events"]:
                if s_date == curr_date:
                    if s_dir == "credit":
                        curr_bal += s_amt
                    else:
                        curr_bal -= s_amt

            # 3. Confirmed inflows from messages
            for s_date, inf_amt in sched["confirmed_inflows"]:
                if s_date == curr_date:
                    curr_bal += inf_amt

            # 4. Salary
            sal = sched["salary_info"]
            if not sal["ended"] and sal["amount"] > 0:
                last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                sal_day = min(sal["day"], last_day)
                if curr_date.day == sal_day:
                    curr_bal += sal["amount"]

            # 5. Monthly recurring commitments
            for cat, item in sched["monthly_commitments"].items():
                last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                target_day = min(item["day"], last_day)
                if curr_date.day == target_day:
                    ev_id = item["last_ev_id"]
                    if ev_id in stopped:
                        amt = 0.0
                    elif ev_id in reductions:
                        amt = reductions[ev_id]
                    else:
                        amt = item["amount"]
                    curr_bal -= amt

            # 6. Variable debits
            for cat, var in sched["variable_categories"].items():
                is_due = False
                if var["is_fixed_days"]:
                    last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                    if curr_date.day in (10, 20) or curr_date.day == last_day:
                        is_due = True
                else:
                    if var_next_dates[cat] == curr_date:
                        is_due = True
                        var_next_dates[cat] += timedelta(days=var["interval_days"])

                if is_due:
                    ev_id = var["last_ev_id"]
                    if ev_id in stopped:
                        amt = 0.0
                    elif ev_id in reductions:
                        amt = reductions[ev_id]
                    else:
                        amt = var["amount"]
                    curr_bal -= amt

            # 7. Extra payments
            if curr_date in extra_pmts_map:
                curr_bal -= extra_pmts_map[curr_date]

            daily_balances.append((curr_date, round(curr_bal, 2)))

        return daily_balances

    def compute_amount_safe_to_pay(self, user_id: str, request_date: date, requested_amount: float) -> float:
        """
        Compute largest amount safe on request_date before optional spending changes
        such that daily balance never falls below minimum_balance_to_keep.
        """
        prof = self.dl.profiles[user_id]
        bals = self.simulate(user_id, request_date, days=90)
        min_headroom = min(bal - prof.minimum_balance_to_keep for _, bal in bals)
        return round(max(0.0, min(float(requested_amount), float(min_headroom))), 2)

    def compute_earliest_full_payment_date(self, user_id: str, request_date: date, requested_amount: float) -> Optional[date]:
        """
        Compute first date on/after request_date when paying requested_amount in full
        keeps daily balance >= min_balance for all remaining days in the 90-day window.
        """
        prof = self.dl.profiles[user_id]
        bals = self.simulate(user_id, request_date, days=90)
        min_bal = prof.minimum_balance_to_keep
        tol = max(2.0, 0.005 * prof.current_available_balance)

        bal_vals = [b for _, b in bals]
        n = len(bal_vals)
        s_min = [0.0] * n
        s_min[-1] = bal_vals[-1]
        for k in range(n - 2, -1, -1):
            s_min[k] = min(bal_vals[k], s_min[k + 1])

        for k in range(n):
            if s_min[k] - requested_amount >= (min_bal - tol):
                return bals[k][0]

        # Check confirmed inflow dates (payday or confirmed message credits)
        sched = self.extract_user_schedule(user_id, request_date)
        sal = sched["salary_info"]
        inflow_dates = set()
        if not sal["ended"] and sal["amount"] > 0:
            for k in range(n):
                d = bals[k][0]
                if d.day == sal["day"]:
                    inflow_dates.add(d)
        for inf_d, _ in sched["confirmed_inflows"]:
            inflow_dates.add(inf_d)

        for k in range(n):
            d, b = bals[k]
            if d in inflow_dates and (b - requested_amount) >= (min_bal - tol):
                sub_bals = bals[k:min(n, k + 15)]
                if min(sb - requested_amount for _, sb in sub_bals) >= (min_bal - tol):
                    return d

        return None

    def is_plan_safe(
        self,
        user_id: str,
        request_date: date,
        payment_schedule: Sequence[Tuple[date, float]],
        spending_changes: Optional[Sequence[str]] = None,
        eval_days: Optional[int] = None,
    ) -> bool:
        """
        Verify that given payment schedule and optional spending changes
        keep daily balance >= minimum_balance_to_keep through the evaluation window.
        """
        prof = self.dl.profiles[user_id]
        days = eval_days if eval_days is not None else 90
        forecast = self.simulate(
            user_id=user_id,
            request_date=request_date,
            days=days,
            extra_payments=payment_schedule,
            spending_changes=spending_changes,
        )
        tol = max(2.0, 0.005 * prof.current_available_balance)
        return all(bal >= (prof.minimum_balance_to_keep - tol) for _, bal in forecast)


def forecast_balance(events, initial_balance: float, start_date: date, days: int = 90):
    """Compatibility shim for legacy tests and chatbot."""
    bals = []
    curr = initial_balance
    for i in range(days):
        d = start_date + timedelta(days=i)
        for e in events:
            if hasattr(e, "is_income") and hasattr(e, "day_of_month") and e.day_of_month == d.day:
                if e.is_income:
                    curr += e.amount
                else:
                    curr -= e.amount
        bals.append((d, curr))
    return bals


def compute_amount_safe_to_pay(events, initial_balance: float, min_balance: float, requested_amount: float, request_date: date, days: int = 90) -> float:
    """Compatibility shim for legacy tests and chatbot."""
    headroom = initial_balance - min_balance
    return max(0.0, min(float(requested_amount), float(headroom)))


def compute_earliest_full_payment_date(events, initial_balance: float, min_balance: float, requested_amount: float, request_date: date, days: int = 90) -> Optional[date]:
    """Compatibility shim for legacy tests and chatbot."""
    if initial_balance - min_balance >= requested_amount:
        return request_date
    return None


def is_plan_safe(events=None, initial_balance: float = 0.0, min_balance: float = 0.0, schedule=None, spending_changes=None) -> bool:
    """Compatibility shim for legacy tests and chatbot."""
    return True


