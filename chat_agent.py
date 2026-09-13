# chat_agent.py
import sys
from typing import Optional, List

from src.data_loader import Request, load_requests
from src.simulation import simulate_user_profile
from src.chat_bot import FinancialChatBot
from main import process_single_request

BOT = None


def get_bot():
    global BOT
    if BOT is None:
        BOT = FinancialChatBot("records.csv", "evaluation/output.csv")
    return BOT


def load_records_csv(path: str = "records.csv") -> List[Request]:
    """Load requests from records.csv."""
    return load_requests(path)


def find_request_by_id(records: List[Request], request_id: str) -> Optional[Request]:
    """Find a request by its ID (case-insensitive, supporting '26' or 'request_26')."""
    clean_id = request_id.strip().lower()
    if clean_id.isdigit():
        clean_id = f"request_{clean_id}"
    for r in records:
        if r.request_id.lower() == clean_id:
            return r
    return None


def explain_plan_to_user(request: Request, plan: dict, profile=None) -> str:
    """Format evaluation plan and explanation for user display."""
    status = plan.get("affordability_status", "")
    method = plan.get("recommended_payment_method", "")
    payment_plan = plan.get("payment_plan", "")
    earliest = plan.get("earliest_date_for_full_payment", "")
    changes = plan.get("spending_changes_needed", "")
    explanation = plan.get("decision_explanation", "")
    safe_to_pay = plan.get("amount_safe_to_pay", 0.0)
    curr = request.detected_currency or "USD"

    profile_lines = []
    if profile:
        profile_lines = [
            f"Available Balance: {profile.available_balance:,.2f} {curr}",
            f"Minimum Reserve: {profile.minimum_balance_to_keep:,.2f} {curr}",
        ]

    lines = [
        f"============================================================",
        f"FINANCIAL AGENT ADVISORY: {request.request_id.upper()}",
        f"============================================================",
        f"User ID: {request.user_id}",
        f"Request Type: {request.request_type}",
        f"Request Date: {request.request_date}",
        f"Requested Amount: {request.requested_amount:,.2f} {curr}",
        f"Desired Completion: {request.desired_completion_date}",
        f"Allows Partial Payment: {request.allows_partial_payment}",
        f"Request Note: \"{request.request_text}\"",
    ]
    if profile_lines:
        lines.append("")
        lines.append("--- Simulated Financial Profile ---")
        lines.extend(profile_lines)

    lines.extend([
        "",
        "--- Financial Assessment & Recommendation ---",
        f"Amount Safe to Pay Today: {safe_to_pay:,.2f} {curr}",
        f"Affordability Status: {status}",
        f"Recommended Payment Method: {method}",
        f"Payment Plan: {payment_plan}",
        f"Earliest Full-Payment Date: {earliest if earliest else 'N/A'}",
        f"Spending Changes Needed: {changes}",
        "",
        "--- Decision Explanation ---",
        explanation or "(No detailed explanation generated.)",
        f"============================================================",
    ])
    return "\n".join(lines)


def process_and_display_request(request_obj: Request):
    """Run deterministic engine on request and display results."""
    profile = simulate_user_profile(
        user_id=request_obj.user_id,
        requested_amount=request_obj.requested_amount,
        home_currency=request_obj.detected_currency,
    )
    plan_dict = process_single_request(request_obj)
    answer = explain_plan_to_user(request_obj, plan_dict, profile)
    print(answer)
    return plan_dict


def chat_loop():
    """Run interactive or CLI chat agent with natural language support."""
    records = load_records_csv("records.csv")
    bot = get_bot()

    # If query is provided as command line arguments (e.g. python3 chat_agent.py request_26)
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
        request_obj = find_request_by_id(records, query)
        if request_obj is not None:
            process_and_display_request(request_obj)
        else:
            res = bot.handle_message(query)
            print("\n" + res["reply"] + "\n")
        return

    # Interactive mode
    print("============================================================")
    print("      BUY OR WAIT AGENT — AI FINANCIAL ASSISTANT           ")
    print("============================================================")
    print("Loaded records.csv (250 requests indexed).")
    print("Ask about any request (e.g. 'request_26', '26', 'what about request 26?'),")
    print("ask general questions ('how is safe to pay calculated?'),")
    print("or test what-ifs ('what if request 26 was 5M?').")
    print("Type 'quit' or 'q' to exit.\n")

    while True:
        try:
            query = input("Ask AI Agent > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting chat agent.")
            break

        if not query:
            continue

        if query.lower() in ("quit", "exit", "q"):
            print("Exiting chat agent.")
            break

        # Check direct request ID first
        request_obj = find_request_by_id(records, query)
        if request_obj is not None:
            process_and_display_request(request_obj)
            print()
            continue

        # Otherwise, process through conversational AI chatbot
        res = bot.handle_message(query)
        print("\n" + res["reply"])
        if res.get("quick_suggestions"):
            print("\n💡 Suggested follow-ups: " + " | ".join(f"[{s}]" for s in res["quick_suggestions"]))
        print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    chat_loop()