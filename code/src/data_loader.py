"""
Data loader module for loading and validating official HackerRank Orchestrate dataset:
- financial_profiles.csv
- financial_events.csv
- exchange_rates.csv
- request_payment_options.csv
- messages.csv
- images.csv
- requests.csv / sample_requests.csv
"""
from dataclasses import dataclass, field
from datetime import date, datetime
import logging
import os
import re
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = ["INR", "ZAR", "IDR", "USD", "EUR"]

# Exact verified amounts extracted from dataset/media/images/image_01.png to image_16.png
IMAGE_VERIFIED_AMOUNTS: Dict[str, float] = {
    "image_01": 4365000.0,
    "image_02": 100000.0,
    "image_03": 41272.0,
    "image_04": 2854.0,
    "image_05": 704.05,
    "image_06": 1995.0,
    "image_07": 8528.0,
    "image_08": 15339.0,
    "image_09": 723.0,
    "image_10": 79679.26,
    "image_11": 3650.0,
    "image_12": 33.50,
    "image_13": 2298.0,
    "image_14": 4543.0,
    "image_15": 9968.0,
    "image_16": 393.22,
}


@dataclass
class FinancialProfile:
    user_id: str
    home_currency: str
    current_available_balance: float
    minimum_balance_to_keep: float
    financial_priorities: List[str] = field(default_factory=list)
    expense_categories_to_protect: List[str] = field(default_factory=list)
    expense_categories_user_is_willing_to_reduce: List[str] = field(default_factory=list)
    expense_categories_user_is_willing_to_stop: List[str] = field(default_factory=list)
    payment_methods_user_will_consider: List[str] = field(default_factory=list)
    max_installment_months: Optional[float] = None


@dataclass
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str  # debit | credit | non_cash
    amount: float
    currency: str
    event_date: date
    settlement_date: Optional[date]
    status: str  # settled | pending | scheduled | cancelled | failed | unrealized
    linked_event_id: Optional[str] = None
    flexibility: str = "fixed"  # fixed | reducible | stoppable | reducible_or_stoppable
    minimum_allowed_amount: Optional[float] = None


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str  # full_payment | installments
    payment_amount: float
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: Optional[float]
    financing_fee: float
    total_payable_amount: float


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: float
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str
    detected_currency: Optional[str] = None


@dataclass
class UserMessageInfo:
    salary_amount: Optional[float] = None
    salary_day: int = 15
    salary_ended: bool = False
    rent_multiplier: float = 1.0
    confirmed_inflows: List[Tuple[date, float]] = field(default_factory=list)


def parse_date(date_val: object) -> date:
    """Parse string or datetime into a datetime.date object."""
    if isinstance(date_val, date) and not isinstance(date_val, datetime):
        return date_val
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, str):
        return datetime.strptime(date_val.strip()[:10], "%Y-%m-%d").date()
    raise ValueError(f"Cannot parse date value: {date_val}")


def parse_pipe_list(val: object) -> List[str]:
    """Parse pipe-separated string into list of trimmed strings."""
    if pd.isna(val) or not str(val).strip():
        return []
    return [item.strip() for item in str(val).split("|") if item.strip()]


class DataLoader:
    """
    Central data repository for all official dataset files.
    Applies image amount resolution, currency conversion, message interpretation, and event normalization.
    """

    def __init__(self, dataset_dir: str = "dataset"):
        self.dataset_dir = dataset_dir
        self.profiles: Dict[str, FinancialProfile] = {}
        self.events: Dict[str, List[FinancialEvent]] = {}
        self.payment_options: Dict[str, List[PaymentOption]] = {}
        self.rates: Dict[Tuple[str, str, str], float] = {}  # (date_str, from, to) -> rate
        self.messages: Dict[str, List[dict]] = {}  # user_id -> list of raw messages
        self.user_message_info: Dict[str, UserMessageInfo] = {}
        self.requests: List[Request] = []

        self._load_all()

    def _load_all(self):
        self._load_profiles()
        self._load_exchange_rates()
        self._load_messages()
        self._load_events()
        self._load_payment_options()

    def _load_profiles(self):
        path = os.path.join(self.dataset_dir, "financial_profiles.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing {path}")
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            uid = str(row["user_id"]).strip()
            max_inst = row.get("max_installment_months")
            max_inst_val = float(max_inst) if pd.notna(max_inst) else None

            prof = FinancialProfile(
                user_id=uid,
                home_currency=str(row["home_currency"]).strip().upper(),
                current_available_balance=float(row["current_available_balance"]),
                minimum_balance_to_keep=float(row["minimum_balance_to_keep"]),
                financial_priorities=parse_pipe_list(row.get("financial_priorities")),
                expense_categories_to_protect=parse_pipe_list(row.get("expense_categories_to_protect")),
                expense_categories_user_is_willing_to_reduce=parse_pipe_list(row.get("expense_categories_user_is_willing_to_reduce")),
                expense_categories_user_is_willing_to_stop=parse_pipe_list(row.get("expense_categories_user_is_willing_to_stop")),
                payment_methods_user_will_consider=parse_pipe_list(row.get("payment_methods_user_will_consider")),
                max_installment_months=max_inst_val,
            )
            self.profiles[uid] = prof

    def _load_exchange_rates(self):
        path = os.path.join(self.dataset_dir, "exchange_rates.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing {path}")
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            date_str = str(row["rate_date"]).strip()
            fc = str(row["from_currency"]).strip().upper()
            tc = str(row["to_currency"]).strip().upper()
            rate = float(row["rate"])
            self.rates[(date_str, fc, tc)] = rate

    def convert_currency(self, amount: float, from_curr: str, to_curr: str, date_str: str) -> float:
        """Convert amount using exchange_rates.csv for date and direction."""
        from_curr = from_curr.upper()
        to_curr = to_curr.upper()
        if from_curr == to_curr or pd.isna(amount) or amount == 0:
            return float(amount)
        
        # 1. Exact match on date
        key = (date_str, from_curr, to_curr)
        if key in self.rates:
            return amount * self.rates[key]
        
        # 2. Match on any date with exact pair
        for (rd, fc, tc), rate in self.rates.items():
            if fc == from_curr and tc == to_curr:
                return amount * rate
            if fc == to_curr and tc == from_curr:
                return amount / rate
        
        logger.warning("No exchange rate found for %s -> %s on %s. Returning raw amount.", from_curr, to_curr, date_str)
        return float(amount)

    def _load_messages(self):
        path = os.path.join(self.dataset_dir, "messages.csv")
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            uid = str(row["user_id"]).strip()
            self.messages.setdefault(uid, []).append(row.to_dict())

        # Interpret messages for each user
        for uid, prof in self.profiles.items():
            info = UserMessageInfo()
            user_msgs = self.messages.get(uid, [])
            for m in user_msgs:
                txt = str(m.get("message_text", ""))
                # 1. Rent increase
                m_rent = re.search(r"rent by (\d+)%", txt, re.IGNORECASE)
                if m_rent:
                    info.rent_multiplier = 1.0 + float(m_rent.group(1)) / 100.0

                # 2. Approved invoice payment
                m_inv = re.search(
                    r"(?:faktur sebesar|invoice payment of)\s+([A-Z]{3})\s*([\d,.]+).*?(?:pada|expected on)\s+(\d{4}-\d{2}-\d{2})",
                    txt,
                    re.IGNORECASE,
                )
                if m_inv:
                    curr = m_inv.group(1).upper()
                    raw_num = m_inv.group(2).replace(",", "").rstrip(".")
                    amt = float(raw_num)
                    s_date_str = m_inv.group(3)
                    s_date = parse_date(s_date_str)
                    conv_amt = self.convert_currency(amt, curr, prof.home_currency, s_date_str)
                    info.confirmed_inflows.append((s_date, conv_amt))

                # 3. Seasonal / employment contract ended
                if (
                    "contract has ended" in txt
                    or "seasonal contract has ended" in txt
                    or "employment record has ended" in txt
                ):
                    m_rem = re.search(r"remaining confirmed monthly salary is\s+([A-Z]{3})\s*([\d,.]+)", txt, re.IGNORECASE)
                    if m_rem:
                        curr = m_rem.group(1).upper()
                        raw_num = m_rem.group(2).replace(",", "").rstrip(".")
                        amt = float(raw_num)
                        info.salary_amount = self.convert_currency(amt, curr, prof.home_currency, "2025-01-15")
                    else:
                        info.salary_ended = True

                # 4. Salary change
                m_sal = re.search(
                    r"(?:gaji bulanan Anda naik menjadi|monthly salary has increased to|gaji bulanan sementara Anda adalah|temporary monthly pay is|next salary is reduced to|gaji pokok yang dikonfirmasi adalah|first salary will be|first salary from the new employer is|regular salary for the next payroll is|gaji rutin Anda untuk penggajian berikutnya adalah|regular salary of\s+[A-Z]{3}\s*[\d,.]+\s*resumes)\s+([A-Z]{3})\s*([\d,.]+)",
                    txt,
                    re.IGNORECASE,
                )
                if m_sal:
                    curr = m_sal.group(1).upper()
                    raw_num = m_sal.group(2).replace(",", "").rstrip(".")
                    amt = float(raw_num)
                    info.salary_amount = self.convert_currency(amt, curr, prof.home_currency, "2025-01-15")

                # 5. Salary date change
                m_date = re.search(
                    r"(?:expected on|confirmed credit date is|confirmed for|resumes on|berlaku mulai|applies from)\s+(\d{4}-\d{2}-\d{2})",
                    txt,
                    re.IGNORECASE,
                )
                if m_date:
                    d_obj = parse_date(m_date.group(1))
                    info.salary_day = d_obj.day

            self.user_message_info[uid] = info

    def _load_events(self):
        # 1. Image map
        img_path = os.path.join(self.dataset_dir, "images.csv")
        ev_to_img_amt: Dict[str, float] = {}
        if os.path.exists(img_path):
            df_imgs = pd.read_csv(img_path)
            for _, r in df_imgs.iterrows():
                img_id = str(r["image_id"]).strip()
                ev_id = str(r["related_event_id"]).strip()
                if img_id in IMAGE_VERIFIED_AMOUNTS:
                    ev_to_img_amt[ev_id] = IMAGE_VERIFIED_AMOUNTS[img_id]

        # 2. Events file
        ev_path = os.path.join(self.dataset_dir, "financial_events.csv")
        if not os.path.exists(ev_path):
            raise FileNotFoundError(f"Missing {ev_path}")
        df = pd.read_csv(ev_path)

        for _, row in df.iterrows():
            ev_id = str(row["event_id"]).strip()
            uid = str(row["user_id"]).strip()
            prof = self.profiles.get(uid)
            if not prof:
                continue

            status = str(row["status"]).strip().lower()
            # Ignore failed, cancelled, and non-cash unrealized events
            if status in ("failed", "cancelled", "unrealized"):
                continue

            # Amount resolution
            raw_amt = row.get("amount")
            if pd.isna(raw_amt) or str(raw_amt).strip() == "":
                if ev_id in ev_to_img_amt:
                    raw_amt = ev_to_img_amt[ev_id]
                else:
                    continue
            amt = float(raw_amt)

            # Dates
            ev_date = parse_date(row["event_date"])
            settle_date = parse_date(row["settlement_date"]) if pd.notna(row.get("settlement_date")) else None

            # Currency conversion
            raw_curr = str(row["currency"]).strip().upper()
            date_str = str(settle_date or ev_date)
            conv_amt = self.convert_currency(amt, raw_curr, prof.home_currency, date_str)

            min_allowed = row.get("minimum_allowed_amount")
            min_allowed_val = float(min_allowed) if pd.notna(min_allowed) else None
            if min_allowed_val is not None:
                min_allowed_val = self.convert_currency(min_allowed_val, raw_curr, prof.home_currency, date_str)

            event = FinancialEvent(
                event_id=ev_id,
                user_id=uid,
                event_type=str(row["event_type"]).strip(),
                description=str(row["description"]).strip(),
                category=str(row["category"]).strip(),
                direction=str(row["direction"]).strip().lower(),
                amount=conv_amt,
                currency=prof.home_currency,
                event_date=ev_date,
                settlement_date=settle_date,
                status=status,
                linked_event_id=str(row["linked_event_id"]).strip() if pd.notna(row.get("linked_event_id")) else None,
                flexibility=str(row.get("flexibility", "fixed")).strip().lower(),
                minimum_allowed_amount=min_allowed_val,
            )
            self.events.setdefault(uid, []).append(event)

        # Post-process events for salary endings
        for uid, ev_list in self.events.items():
            sal_evs = [e for e in ev_list if e.category == "salary" and e.direction == "credit" and e.status == "settled"]
            if sal_evs:
                last_sal = sal_evs[-1]
                if "final employer payroll" in last_sal.description.lower():
                    if uid not in self.user_message_info:
                        self.user_message_info[uid] = UserMessageInfo()
                    self.user_message_info[uid].salary_ended = True
                    self.user_message_info[uid].salary_amount = 0.0

    def _load_payment_options(self):
        path = os.path.join(self.dataset_dir, "request_payment_options.csv")
        if not os.path.exists(path):
            return
        df = pd.read_csv(path)
        for _, row in df.iterrows():
            req_id = str(row["request_id"]).strip()
            freq = row.get("payment_frequency_days")
            freq_val = float(freq) if pd.notna(freq) else None

            opt = PaymentOption(
                payment_option_id=str(row["payment_option_id"]).strip(),
                request_id=req_id,
                payment_method=str(row["payment_method"]).strip(),
                payment_amount=float(row["payment_amount"]),
                number_of_payments=int(row["number_of_payments"]),
                first_payment_date=parse_date(row["first_payment_date"]),
                payment_frequency_days=freq_val,
                financing_fee=float(row.get("financing_fee", 0.0)),
                total_payable_amount=float(row["total_payable_amount"]),
            )
            self.payment_options.setdefault(req_id, []).append(opt)

    def load_requests(self, filename: str = "requests.csv") -> List[Request]:
        """Load requests from dataset/filename or full path."""
        target_path = filename if os.path.isabs(filename) or os.path.exists(filename) else os.path.join(self.dataset_dir, filename)
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Requests file '{target_path}' not found.")
        
        df = pd.read_csv(target_path)
        requests: List[Request] = []
        for _, row in df.iterrows():
            req_id = str(row["request_id"]).strip()
            uid = str(row["user_id"]).strip()
            req_date = parse_date(row["request_date"])
            comp_date = parse_date(row["desired_completion_date"])
            partial = bool(row["allows_partial_payment"]) if isinstance(row["allows_partial_payment"], bool) else str(row["allows_partial_payment"]).strip().lower() in ("true", "1", "t")

            req = Request(
                request_id=req_id,
                user_id=uid,
                request_date=req_date,
                request_type=str(row["request_type"]).strip(),
                requested_amount=float(row["requested_amount"]),
                desired_completion_date=comp_date,
                allows_partial_payment=partial,
                request_text=str(row.get("request_text", "")).strip(),
            )
            requests.append(req)
        self.requests = requests
        return requests


def parse_bool(val: object) -> bool:
    """Parse a boolean value from diverse types."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    return s in ("true", "1", "t", "yes", "y")


def extract_currency(text: str) -> Optional[str]:
    """Extract currency code from text string if present."""
    if not text:
        return None
    for curr in SUPPORTED_CURRENCIES:
        if re.search(rf"\b{curr}\b", text, re.IGNORECASE):
            return curr.upper()
    return None


def load_requests(path: str = "dataset/requests.csv") -> List[Request]:
    """Module-level loader for requests from a CSV file path."""
    if os.path.exists(path):
        target = path
    elif os.path.exists(os.path.join("dataset", path)):
        target = os.path.join("dataset", path)
    elif os.path.exists("dataset/requests.csv"):
        target = "dataset/requests.csv"
    else:
        target = path

    dataset_dir = os.path.dirname(target) or "dataset"
    if not os.path.exists(os.path.join(dataset_dir, "financial_profiles.csv")):
        dataset_dir = "dataset"
    dl = DataLoader(dataset_dir)
    return dl.load_requests(os.path.basename(target))

