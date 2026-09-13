"""
Deterministic simulation engine for user profiles, financial events, exchange rates, and payment options.
Generates reproducible data based on cryptographic hashes of user_id and request_id.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
import hashlib
import random
from typing import Dict, List, Optional, Tuple

SUPPORTED_CURRENCIES = ["INR", "ZAR", "IDR", "USD", "EUR"]

# Base exchange rates to USD (1 unit of currency in USD)
EXCHANGE_RATES_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,        # 1 EUR = 1.08 USD
    "INR": 0.012,       # 1 INR = 0.012 USD (1 USD ~ 83.33 INR)
    "ZAR": 0.055,       # 1 ZAR = 0.055 USD (1 USD ~ 18.18 ZAR)
    "IDR": 0.000064,    # 1 IDR = 0.000064 USD (1 USD ~ 15,625 IDR)
}


class ExchangeRates:
    """Fixed dated exchange rate lookup and currency conversion."""
    rates_to_usd: Dict[str, float] = EXCHANGE_RATES_TO_USD

    @classmethod
    def convert(cls, amount: float, from_curr: str, to_curr: str) -> float:
        """Convert an amount from from_curr to to_curr."""
        if from_curr == to_curr:
            return round(amount, 2)
        rate_from = cls.rates_to_usd.get(from_curr, 1.0)
        rate_to = cls.rates_to_usd.get(to_curr, 1.0)
        usd_value = amount * rate_from
        converted = usd_value / rate_to
        return round(converted, 2)


@dataclass
class UserProfile:
    user_id: str
    home_currency: str
    available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: List[str] = field(default_factory=lambda: ["essentials", "savings", "flexible"])
    spending_preferences: Dict[str, bool] = field(default_factory=dict)
    payment_methods_user_will_consider: List[str] = field(default_factory=list)


@dataclass
class FinancialEvent:
    event_id: str
    name: str
    amount: float          # in home_currency
    is_income: bool
    frequency: str         # 'monthly' or 'one_time'
    day_of_month: Optional[int] = None
    start_date: Optional[date] = None
    event_date: Optional[date] = None
    is_flexible: bool = False
    original_currency: Optional[str] = None
    original_amount: Optional[float] = None


@dataclass
class PaymentOption:
    payment_option_id: str
    start_date: date
    num_payments: int
    payment_interval_days: int
    financing_fee: float
    total_payable: float
    schedule: List[Tuple[date, float]] = field(default_factory=list)


def deterministic_rng(seed_key: str) -> random.Random:
    """Generate a reproducible Random instance from a string key."""
    h = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    seed_int = int(h[:16], 16)
    return random.Random(seed_int)


def simulate_user_profile(
    user_id: str,
    requested_amount: Optional[float] = None,
    home_currency: Optional[str] = None,
) -> UserProfile:
    """
    Deterministically simulate financial profile for user_id.
    - home_currency: chosen from ['INR', 'ZAR', 'IDR', 'USD', 'EUR'] via hash(user_id) if not supplied.
    - available_balance: 0.5x to 3x of requested_amount (or realistic base).
    - minimum_balance_to_keep: 10% to 20% of available_balance.
    - financial_priorities: ['essentials', 'savings', 'flexible'].
    - payment_methods_user_will_consider: subset of ['fullpayment', 'partialpayment', 'installments', 'wait'].
    """
    rng = deterministic_rng(f"profile_sim_{user_id}")

    # Determine home currency
    if not home_currency:
        curr_idx = int(hashlib.sha256(f"curr_{user_id}".encode("utf-8")).hexdigest(), 16) % len(SUPPORTED_CURRENCIES)
        home_currency = SUPPORTED_CURRENCIES[curr_idx]

    # Baseline requested amount if not provided
    if requested_amount is None or requested_amount <= 0:
        base_amounts = {
            "USD": 1500.0,
            "EUR": 1400.0,
            "INR": 120000.0,
            "ZAR": 25000.0,
            "IDR": 20000000.0,
        }
        requested_amount = base_amounts.get(home_currency, 1500.0)

    # Available balance: 0.5x to 3.0x of requested_amount
    balance_multiplier = rng.uniform(0.5, 3.0)
    available_balance = round(requested_amount * balance_multiplier, 2)

    # Minimum balance to keep: 10% to 20% of available_balance
    min_keep_ratio = rng.uniform(0.10, 0.20)
    minimum_balance_to_keep = round(available_balance * min_keep_ratio, 2)

    financial_priorities = ["essentials", "savings", "flexible"]
    spending_preferences = {
        "subscriptions": True,
        "dining": True,
        "entertainment": True,
        "rent": False,
        "utilities": False,
        "groceries": False,
    }

    # Deterministic subset of allowed payment methods
    subsets = [
        ["fullpayment", "partialpayment", "installments", "wait"],
        ["fullpayment", "partialpayment", "wait"],
        ["fullpayment", "installments", "wait"],
        ["partialpayment", "installments", "wait"],
        ["installments", "wait"],
        ["fullpayment", "wait"],
        ["fullpayment", "installments"],
    ]
    chosen_methods = subsets[rng.randint(0, len(subsets) - 1)]

    return UserProfile(
        user_id=user_id,
        home_currency=home_currency,
        available_balance=available_balance,
        minimum_balance_to_keep=minimum_balance_to_keep,
        financial_priorities=financial_priorities,
        spending_preferences=spending_preferences,
        payment_methods_user_will_consider=chosen_methods,
    )


def simulate_events(profile: UserProfile, request_date: date) -> List[FinancialEvent]:
    """
    Deterministically simulate recurring and one-time events for the user.
    - Monthly income in home_currency.
    - Monthly essential expenses (rent, utilities, groceries).
    - Optional flexible expenses (subscriptions, dining, entertainment).
    - Confirmed one-time events within the 90-day window.
    - All converted to user's home_currency.
    - Excludes failed/cancelled/pending items by design.
    """
    rng = deterministic_rng(f"events_sim_{profile.user_id}_{request_date}")
    events: List[FinancialEvent] = []

    # Monthly salary/income: ~40% - 70% of available_balance (healthy recurring cash flow)
    salary_amount = round(profile.available_balance * rng.uniform(0.40, 0.70), 2)
    salary_day = rng.choice([1, 25, 28])
    events.append(
        FinancialEvent(
            event_id=f"evt_income_salary_{profile.user_id}",
            name="Monthly Salary",
            amount=salary_amount,
            is_income=True,
            frequency="monthly",
            day_of_month=salary_day,
            start_date=request_date - timedelta(days=90),
            is_flexible=False,
            original_currency=profile.home_currency,
            original_amount=salary_amount,
        )
    )

    # Monthly essential expenses (cannot be altered)
    # Rent: 30% - 40% of salary
    rent_amount = round(salary_amount * rng.uniform(0.30, 0.40), 2)
    events.append(
        FinancialEvent(
            event_id=f"evt_ess_rent_{profile.user_id}",
            name="Apartment Rent",
            amount=rent_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=1,
            start_date=request_date - timedelta(days=90),
            is_flexible=False,
            original_currency=profile.home_currency,
            original_amount=rent_amount,
        )
    )

    # Utilities: 7% - 12% of salary
    utilities_amount = round(salary_amount * rng.uniform(0.07, 0.12), 2)
    events.append(
        FinancialEvent(
            event_id=f"evt_ess_util_{profile.user_id}",
            name="Electricity & Water Utilities",
            amount=utilities_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=15,
            start_date=request_date - timedelta(days=90),
            is_flexible=False,
            original_currency=profile.home_currency,
            original_amount=utilities_amount,
        )
    )

    # Groceries: 15% - 20% of salary (split on 5th and 20th)
    groceries_amount = round((salary_amount * rng.uniform(0.15, 0.20)) / 2.0, 2)
    events.append(
        FinancialEvent(
            event_id=f"evt_ess_groc1_{profile.user_id}",
            name="Essential Groceries Pt 1",
            amount=groceries_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=5,
            start_date=request_date - timedelta(days=90),
            is_flexible=False,
            original_currency=profile.home_currency,
            original_amount=groceries_amount,
        )
    )
    events.append(
        FinancialEvent(
            event_id=f"evt_ess_groc2_{profile.user_id}",
            name="Essential Groceries Pt 2",
            amount=groceries_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=20,
            start_date=request_date - timedelta(days=90),
            is_flexible=False,
            original_currency=profile.home_currency,
            original_amount=groceries_amount,
        )
    )

    # Optional flexible recurring expenses (can be stopped or reduced)
    # Flexible Subscription 1 (e.g. streaming / cloud / gym)
    # May originate in USD or EUR, converted to home_currency
    foreign_curr = rng.choice(["USD", "EUR"])
    raw_sub1 = round(rng.uniform(20.0, 60.0), 2)
    converted_sub1 = ExchangeRates.convert(raw_sub1, foreign_curr, profile.home_currency)
    events.append(
        FinancialEvent(
            event_id=f"evt_flex_sub_{profile.user_id}",
            name="Digital Subscriptions & Memberships",
            amount=converted_sub1,
            is_income=False,
            frequency="monthly",
            day_of_month=10,
            start_date=request_date - timedelta(days=60),
            is_flexible=True,
            original_currency=foreign_curr,
            original_amount=raw_sub1,
        )
    )

    # Flexible Dining & Social spending: 4% - 8% of salary
    dining_amount = round(salary_amount * rng.uniform(0.04, 0.08), 2)
    events.append(
        FinancialEvent(
            event_id=f"evt_flex_dining_{profile.user_id}",
            name="Dining Out & Social Entertainment",
            amount=dining_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=18,
            start_date=request_date - timedelta(days=60),
            is_flexible=True,
            original_currency=profile.home_currency,
            original_amount=dining_amount,
        )
    )

    # Flexible Hobbies / Shopping: 3% - 6% of salary
    shopping_amount = round(salary_amount * rng.uniform(0.03, 0.06), 2)
    events.append(
        FinancialEvent(
            event_id=f"evt_flex_shop_{profile.user_id}",
            name="Leisure Shopping & Hobbies",
            amount=shopping_amount,
            is_income=False,
            frequency="monthly",
            day_of_month=22,
            start_date=request_date - timedelta(days=60),
            is_flexible=True,
            original_currency=profile.home_currency,
            original_amount=shopping_amount,
        )
    )

    # Confirmed one-time events within the 90-day window
    # 50% chance of a confirmed one-time expense (e.g. annual maintenance/insurance)
    if rng.random() > 0.5:
        offset_days = rng.randint(15, 75)
        one_time_date = request_date + timedelta(days=offset_days)
        raw_onetime = round(salary_amount * rng.uniform(0.15, 0.35), 2)
        events.append(
            FinancialEvent(
                event_id=f"evt_onetime_{profile.user_id}_{offset_days}",
                name="Scheduled Maintenance & Insurance",
                amount=raw_onetime,
                is_income=False,
                frequency="one_time",
                event_date=one_time_date,
                is_flexible=False,
                original_currency=profile.home_currency,
                original_amount=raw_onetime,
            )
        )

    return events


def simulate_payment_options(
    request_id: str,
    requested_amount: float,
    request_date: date,
) -> List[PaymentOption]:
    """
    Deterministically simulate 0-2 installment payment options for a request.
    Option schedules are generated with exact integer/decimal distributions so
    the sum of installments matches total_payable exactly.
    """
    rng = deterministic_rng(f"options_sim_{request_id}")
    num_options = rng.choice([0, 1, 2, 2])
    options: List[PaymentOption] = []

    if num_options >= 1:
        # 3-payment monthly plan with 2% financing fee
        opt1_id = f"opt_{request_id}_3m"
        num_payments_1 = 3
        interval_1 = 30
        fee_1 = round(requested_amount * 0.02, 2)
        total_1 = round(requested_amount + fee_1, 2)

        schedule_1: List[Tuple[date, float]] = []
        base_inst_1 = round(total_1 / num_payments_1, 2)
        for i in range(num_payments_1):
            pay_date = request_date + timedelta(days=i * interval_1)
            if i == num_payments_1 - 1:
                # Last payment absorbs rounding difference
                inst_amt = round(total_1 - base_inst_1 * (num_payments_1 - 1), 2)
            else:
                inst_amt = base_inst_1
            schedule_1.append((pay_date, inst_amt))

        options.append(
            PaymentOption(
                payment_option_id=opt1_id,
                start_date=request_date,
                num_payments=num_payments_1,
                payment_interval_days=interval_1,
                financing_fee=fee_1,
                total_payable=total_1,
                schedule=schedule_1,
            )
        )

    if num_options >= 2:
        # 6-payment bi-weekly or monthly plan with 4% financing fee
        opt2_id = f"opt_{request_id}_6m"
        num_payments_2 = 6
        interval_2 = 15
        fee_2 = round(requested_amount * 0.04, 2)
        total_2 = round(requested_amount + fee_2, 2)

        schedule_2: List[Tuple[date, float]] = []
        base_inst_2 = round(total_2 / num_payments_2, 2)
        for i in range(num_payments_2):
            pay_date = request_date + timedelta(days=i * interval_2)
            if i == num_payments_2 - 1:
                inst_amt = round(total_2 - base_inst_2 * (num_payments_2 - 1), 2)
            else:
                inst_amt = base_inst_2
            schedule_2.append((pay_date, inst_amt))

        options.append(
            PaymentOption(
                payment_option_id=opt2_id,
                start_date=request_date,
                num_payments=num_payments_2,
                payment_interval_days=interval_2,
                financing_fee=fee_2,
                total_payable=total_2,
                schedule=schedule_2,
            )
        )

    return options
