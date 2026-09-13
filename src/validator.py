"""
Output validation module: rigorously verifies schema, allowed strings,
invariants, and financial safety rules on output.csv according to HackerRank Orchestrate §6.2.
"""
from datetime import datetime
import logging
import re
from typing import List, Tuple
import pandas as pd

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

ALLOWED_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}

ALLOWED_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}


def validate_output(output_df: pd.DataFrame, requests_df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate output dataframe against hackathon problem requirements and invariants.
    Returns (is_valid, errors_list).
    """
    errors: List[str] = []

    # 1. Column presence and exact ordering
    if list(output_df.columns) != EXPECTED_COLUMNS:
        errors.append(
            f"Columns mismatch.\nExpected: {EXPECTED_COLUMNS}\nActual:   {list(output_df.columns)}"
        )

    # 2. Row count matching
    if len(output_df) != len(requests_df):
        errors.append(f"Row count mismatch: output has {len(output_df)} rows, requests has {len(requests_df)} rows.")

    # Match request_ids
    requests_indexed = requests_df.set_index("request_id")

    for idx, row in output_df.iterrows():
        req_id = row.get("request_id")
        if req_id not in requests_indexed.index:
            errors.append(f"Row {idx}: Unknown request_id '{req_id}' not found in requests.csv.")
            continue

        orig_row = requests_indexed.loc[req_id]
        if isinstance(orig_row, pd.DataFrame):
            orig_row = orig_row.iloc[0]

        req_amount = float(orig_row["requested_amount"])
        req_date_str = str(orig_row["request_date"]).strip()

        # 3. amount_safe_to_pay checks
        try:
            safe_amt = float(row["amount_safe_to_pay"])
        except (ValueError, TypeError):
            errors.append(f"Row {idx} ({req_id}): amount_safe_to_pay is not numeric: {row.get('amount_safe_to_pay')}")
            safe_amt = -1.0

        # Invariant: 0 <= amount_safe_to_pay <= requested_amount
        if safe_amt < -1e-4 or safe_amt > (req_amount + 1e-4):
            errors.append(
                f"Row {idx} ({req_id}): Invariant violated: amount_safe_to_pay ({safe_amt}) must be between 0 and requested_amount ({req_amount})."
            )

        # 4. Allowed strings (with underscores)
        status = str(row.get("affordability_status", "")).strip()
        if status not in ALLOWED_STATUSES:
            errors.append(f"Row {idx} ({req_id}): Invalid affordability_status '{status}'. Allowed: {ALLOWED_STATUSES}")

        method = str(row.get("recommended_payment_method", "")).strip()
        if method not in ALLOWED_METHODS:
            errors.append(f"Row {idx} ({req_id}): Invalid recommended_payment_method '{method}'. Allowed: {ALLOWED_METHODS}")

        earliest_date = str(row.get("earliest_date_for_full_payment", "")).strip()
        if pd.isna(row.get("earliest_date_for_full_payment")) or earliest_date == "nan":
            earliest_date = ""

        # Invariant: For affordable_now, earliest_date_for_full_payment == request_date
        if status == "affordable_now":
            if earliest_date != req_date_str:
                errors.append(
                    f"Row {idx} ({req_id}): Invariant violated: for affordable_now, "
                    f"earliest_date_for_full_payment ({earliest_date}) must equal request_date ({req_date_str})."
                )

        # Invariant: For not_affordable, earliest_date_for_full_payment must be empty
        if status == "not_affordable":
            if earliest_date != "":
                errors.append(
                    f"Row {idx} ({req_id}): Invariant violated: for not_affordable, "
                    f"earliest_date_for_full_payment must be blank/empty, got '{earliest_date}'."
                )

        # 5. payment_plan format checks
        plan = str(row.get("payment_plan", "")).strip()
        if not plan:
            errors.append(f"Row {idx} ({req_id}): payment_plan cannot be blank.")
        elif method == "not_recommended":
            if plan != "none":
                errors.append(f"Row {idx} ({req_id}): For not_recommended, payment_plan must be 'none', got '{plan}'.")
        else:
            # Must be YYYY-MM-DD:amount entries separated by |
            entries = plan.split("|")
            prev_date = None
            total_plan_paid = 0.0

            for entry in entries:
                entry = entry.strip()
                match = re.match(r"^(\d{4}-\d{2}-\d{2}):([\d.]+)$", entry)
                if not match:
                    errors.append(
                        f"Row {idx} ({req_id}): Invalid payment_plan entry '{entry}'. Expected 'YYYY-MM-DD:amount' separated by '|'."
                    )
                    continue

                p_date_str, p_amt_str = match.groups()
                try:
                    p_date = datetime.strptime(p_date_str, "%Y-%m-%d").date()
                    p_amt = float(p_amt_str)
                except ValueError as e:
                    errors.append(f"Row {idx} ({req_id}): Invalid date or amount in entry '{entry}': {e}")
                    continue

                # Chronological order
                if prev_date is not None and p_date < prev_date:
                    errors.append(
                        f"Row {idx} ({req_id}): payment_plan entries must be in chronological order: {p_date} < {prev_date}."
                    )
                prev_date = p_date
                total_plan_paid += p_amt

            # Partial payment invariant: exactly 2 payments adding up to requested_amount
            if method == "partial_payment":
                if len(entries) != 2:
                    errors.append(
                        f"Row {idx} ({req_id}): partial_payment plan must have exactly 2 payments, got {len(entries)}."
                    )
                if abs(total_plan_paid - req_amount) > 1.0:
                    errors.append(
                        f"Row {idx} ({req_id}): partial_payment sum ({total_plan_paid}) must equal requested_amount ({req_amount})."
                    )

        # 6. spending_changes_needed format checks
        changes_str = str(row.get("spending_changes_needed", "")).strip()
        if not changes_str:
            errors.append(f"Row {idx} ({req_id}): spending_changes_needed cannot be blank. Use 'none' if no changes.")
        elif changes_str != "none":
            # Up to three changes separated by |
            changes = changes_str.split("|")
            if len(changes) > 3:
                errors.append(
                    f"Row {idx} ({req_id}): spending_changes_needed exceeds maximum of 3 actions: got {len(changes)}."
                )
            for ch in changes:
                ch = ch.strip()
                stop_match = re.match(r"^stop:([a-zA-Z0-9_]+)$", ch)
                reduce_match = re.match(r"^reduce_to:([a-zA-Z0-9_]+):([\d.]+)$", ch)
                if not (stop_match or reduce_match):
                    errors.append(
                        f"Row {idx} ({req_id}): Invalid spending change format '{ch}'. Must be 'stop:<event_id>' or 'reduce_to:<event_id>:<amount>'."
                    )

        # 7. decision_explanation non-blank
        explanation = str(row.get("decision_explanation", "")).strip()
        if not explanation or explanation == "nan":
            errors.append(f"Row {idx} ({req_id}): decision_explanation is required and cannot be blank.")

    is_valid = len(errors) == 0
    return is_valid, errors
