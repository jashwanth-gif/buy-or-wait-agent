import calendar
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import re

from src.data_loader import DataLoader, FinancialEvent, FinancialProfile, Request, UserMessageInfo

def get_last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

class FinancialForecastEngine:
    def __init__(self, data_loader: DataLoader):
        self.dl = data_loader

    def extract_user_schedule(self, user_id: str, request_date: date):
        prof = self.dl.profiles[user_id]
        events = self.dl.events.get(user_id, [])
        msg_info = self.dl.user_message_info.get(user_id, UserMessageInfo())

        monthly_commitments = {}  # category -> dict
        variable_categories = {}  # category -> dict
        pending_debits = []
        scheduled_events = []

        # 1. Pending debits and scheduled events
        for ev in events:
            if ev.status == 'pending' and ev.direction == 'debit':
                s_date = ev.settlement_date or ev.event_date
                pending_debits.append((s_date, ev.amount))
            elif ev.status == 'scheduled':
                s_date = ev.settlement_date or ev.event_date
                scheduled_events.append((s_date, ev.amount, ev.direction))

        # 2. Historical settled events
        settled_events = [e for e in events if e.status == 'settled' and e.event_date <= request_date]

        # Salary detection
        salary_info = {'amount': 0.0, 'day': msg_info.salary_day, 'ended': msg_info.salary_ended}
        if msg_info.salary_amount is not None:
            salary_info['amount'] = msg_info.salary_amount
        else:
            # Look for scheduled salary first
            sched_sal = [s for s in scheduled_events if s[2] == 'credit']
            if sched_sal:
                salary_info['amount'] = sched_sal[0][1]
                salary_info['day'] = sched_sal[0][0].day
            else:
                sal_events = [e for e in settled_events if e.category == 'salary' and e.direction == 'credit']
                if sal_events:
                    # Filter out non-recurring ones (e.g. arrears, bonuses)
                    regular_sal = [e for e in sal_events if 'arrears' not in e.description.lower() and 'bonus' not in e.description.lower() and 'prorated' not in e.description.lower()]
                    target_sal = regular_sal if regular_sal else sal_events
                    salary_info['amount'] = target_sal[-1].amount
                    days = [e.event_date.day for e in target_sal]
                    salary_info['day'] = max(set(days), key=days.count)

        # Process debits by category
        cat_groups = {}
        for e in settled_events:
            if e.direction == 'debit':
                cat_groups.setdefault(e.category, []).append(e)

        for cat, cat_evs in cat_groups.items():
            if cat in ('salary', 'refund', 'investment'):
                continue

            # Sort by date
            cat_evs_sorted = sorted(cat_evs, key=lambda x: x.event_date)
            latest_ev = cat_evs_sorted[-1]

            if cat in ('groceries', 'transport', 'dining'):
                # Variable expense: calculate frequency
                dates = [e.event_date for e in cat_evs_sorted]
                if len(dates) >= 2:
                    diffs = [(dates[i] - dates[i-1]).days for i in range(1, len(dates))]
                    median_diff = float(np.median(diffs))
                else:
                    median_diff = 7.0 if cat == 'groceries' else (14.0 if cat == 'transport' else 21.0)

                # Check if it occurs on fixed days of month (e.g. 10, 20, 30)
                days_of_month = [d.day for d in dates]
                is_day_of_month = (set(days_of_month) <= {9, 10, 19, 20, 29, 30, 31}) and len(dates) >= 6

                variable_categories[cat] = {
                    'category': cat,
                    'interval_days': max(1, int(round(median_diff))),
                    'is_fixed_days': is_day_of_month,
                    'fixed_days': [10, 20, 30],
                    'amount': float(np.mean([e.amount for e in cat_evs_sorted])),
                    'last_date': latest_ev.event_date,
                    'last_ev_id': latest_ev.event_id,
                    'flexibility': latest_ev.flexibility,
                    'min_allowed': latest_ev.minimum_allowed_amount,
                    'description': latest_ev.description,
                }
            else:
                # Monthly recurring commitment
                days = [e.event_date.day for e in cat_evs_sorted]
                mode_day = max(set(days), key=days.count)
                amt = latest_ev.amount
                if cat == 'rent' and msg_info.rent_multiplier != 1.0:
                    amt *= msg_info.rent_multiplier

                monthly_commitments[cat] = {
                    'category': cat,
                    'day': mode_day,
                    'amount': amt,
                    'last_ev_id': latest_ev.event_id,
                    'flexibility': latest_ev.flexibility,
                    'min_allowed': latest_ev.minimum_allowed_amount,
                    'description': latest_ev.description,
                }

        return {
            'monthly_commitments': monthly_commitments,
            'variable_categories': variable_categories,
            'salary_info': salary_info,
            'pending_debits': pending_debits,
            'scheduled_events': scheduled_events,
            'confirmed_inflows': msg_info.confirmed_inflows,
        }

    def simulate(
        self,
        user_id: str,
        request_date: date,
        days: int = 90,
        extra_payments: Optional[List[Tuple[date, float]]] = None,
        spending_changes: Optional[List[str]] = None,
    ) -> List[Tuple[date, float]]:
        prof = self.dl.profiles[user_id]
        sched = self.extract_user_schedule(user_id, request_date)

        stopped_events = set()
        reduced_events = {}
        if spending_changes:
            for ch in spending_changes:
                ch = ch.strip()
                if not ch or ch.lower() == 'none':
                    continue
                parts = ch.split(':')
                action = parts[0].strip().lower()
                if action == 'stop' and len(parts) >= 2:
                    stopped_events.add(parts[1].strip())
                elif action in ('reduce', 'reduce_to') and len(parts) >= 3:
                    reduced_events[parts[1].strip()] = float(parts[2].strip())

        extra_pmts_map = {}
        if extra_payments:
            for d, a in extra_payments:
                extra_pmts_map[d] = extra_pmts_map.get(d, 0.0) + a

        curr_bal = prof.current_available_balance
        daily_balances = []

        # Setup next dates for variable categories
        var_next_dates = {}
        for cat, var in sched['variable_categories'].items():
            if not var['is_fixed_days']:
                nxt = var['last_date'] + timedelta(days=var['interval_days'])
                while nxt < request_date:
                    nxt += timedelta(days=var['interval_days'])
                var_next_dates[cat] = nxt

        for d_offset in range(days):
            curr_date = request_date + timedelta(days=d_offset)

            # 1. Pending debits
            for s_date, p_amt in sched['pending_debits']:
                if s_date == curr_date:
                    curr_bal -= p_amt

            # 2. Scheduled events
            for s_date, s_amt, s_dir in sched['scheduled_events']:
                if s_date == curr_date:
                    if s_dir == 'credit':
                        curr_bal += s_amt
                    else:
                        curr_bal -= s_amt

            # 3. Confirmed inflows from messages
            for s_date, inf_amt in sched['confirmed_inflows']:
                if s_date == curr_date:
                    curr_bal += inf_amt

            # 4. Salary
            sal = sched['salary_info']
            if not sal['ended'] and sal['amount'] > 0:
                last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                sal_day = min(sal['day'], last_day)
                if curr_date.day == sal_day:
                    curr_bal += sal['amount']

            # 5. Monthly recurring debits
            for cat, item in sched['monthly_commitments'].items():
                last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                target_day = min(item['day'], last_day)
                if curr_date.day == target_day:
                    ev_id = item['last_ev_id']
                    if ev_id in stopped_events:
                        amt = 0.0
                    elif ev_id in reduced_events:
                        amt = reduced_events[ev_id]
                    else:
                        amt = item['amount']
                    curr_bal -= amt

            # 6. Variable debits
            for cat, var in sched['variable_categories'].items():
                is_due = False
                if var['is_fixed_days']:
                    last_day = get_last_day_of_month(curr_date.year, curr_date.month)
                    if curr_date.day in (10, 20) or curr_date.day == last_day:
                        is_due = True
                else:
                    if var_next_dates[cat] == curr_date:
                        is_due = True
                        var_next_dates[cat] += timedelta(days=var['interval_days'])

                if is_due:
                    ev_id = var['last_ev_id']
                    if ev_id in stopped_events:
                        amt = 0.0
                    elif ev_id in reduced_events:
                        amt = reduced_events[ev_id]
                    else:
                        amt = var['amount']
                    curr_bal -= amt

            # 7. Extra payments
            if curr_date in extra_pmts_map:
                curr_bal -= extra_pmts_map[curr_date]

            daily_balances.append((curr_date, curr_bal))

        return daily_balances

    def compute_amount_safe_to_pay(self, user_id: str, request_date: date, requested_amount: float) -> float:
        prof = self.dl.profiles[user_id]
        bals = self.simulate(user_id, request_date, days=90)
        min_headroom = min(bal - prof.minimum_balance_to_keep for _, bal in bals)
        return max(0.0, min(float(requested_amount), float(min_headroom)))

    def compute_earliest_full_payment_date(self, user_id: str, request_date: date, requested_amount: float) -> Optional[date]:
        prof = self.dl.profiles[user_id]
        bals = self.simulate(user_id, request_date, days=90)
        min_bal = prof.minimum_balance_to_keep

        bal_vals = [b for _, b in bals]
        n = len(bal_vals)
        s_min = [0.0] * n
        s_min[-1] = bal_vals[-1]
        for k in range(n - 2, -1, -1):
            s_min[k] = min(bal_vals[k], s_min[k + 1])

        for k in range(n):
            if s_min[k] - requested_amount >= min_bal:
                return bals[k][0]

        return None
