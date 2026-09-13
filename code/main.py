"""
Buy or Wait Agent — Main Runner
Deterministic AI-powered financial decision agent for HackerRank Orchestrate.
Forecasts daily 90-day balance, evaluates safe payment methods and installment options,
ranks plans by challenge priority, validates against all invariants, and generates output.csv and usage reports.
"""
import argparse
from datetime import datetime, timezone
import logging
import os
import sys
from typing import Dict, List, Optional
import pandas as pd

from src.data_loader import DataLoader, Request
from src.financial_forecast import FinancialForecastEngine
from src.plan_generator import PlanGenerator, Recommendation
from src.validator import validate_output

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("buy_or_wait_agent")


def generate_usage_report(
    report_path: str,
    num_requests: int,
    model_provider: str = "Deterministic Financial Rules Engine (Rule-based / Offline)",
    model_name: str = "Deterministic Multi-Horizon Daily Cashflow Engine",
    num_calls: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost: float = 0.0,
) -> None:
    """
    Generate evaluation/usage_report.md summarizing model calls, token counts, and cost.
    Conforms to HackerRank Orchestrate §6.5 contract.
    """
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    avg_tokens = (input_tokens + output_tokens) / max(1, num_requests)
    cost_per_req = estimated_cost / max(1, num_requests)

    report_content = f"""# Token Usage and Cost Analysis Report

## Summary
- **Execution Timestamp**: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
- **Total Requests Evaluated**: {num_requests}
- **Primary Decision Engine**: Deterministic Multi-Horizon Daily Cashflow Engine
- **Explanation Mode**: Grounded Deterministic Decision Explanation Engine (Zero LLM Tokens)

## Model & Token Metrics

| Metric | Value |
| :--- | :--- |
| **Model Provider** | {model_provider} |
| **Model Name** | {model_name} |
| **Total Model Calls** | {num_calls} |
| **Input Tokens** | {input_tokens} |
| **Output Tokens** | {output_tokens} |
| **Total Tokens** | {input_tokens + output_tokens} |
| **Average Tokens per Request** | {avg_tokens:.2f} |
| **Total Estimated Cost (USD)** | ${estimated_cost:.4f} |
| **Average Cost per Request (USD)** | ${cost_per_req:.4f} |

## Architectural Notes
- All financial decisions (amount safe to pay, 90-day balance progression, affordability status, payment plans, and spending change selections) are computed strictly and deterministically by the rules engine.
- Reconstructs financial positions from financial_profiles.csv, financial_events.csv, exchange_rates.csv, request_payment_options.csv, messages.csv, and images.csv.
- Generates zero-token, zero-cost, 100% reproducible explanations adhering to all prompt rules and constraints.
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    logger.info("Usage report written to '%s'.", report_path)


def process_single_request(request: Request, dataset_dir: str = "dataset") -> Dict[str, object]:
    """Process an individual request end-to-end and return evaluation dictionary."""
    data_loader = DataLoader(dataset_dir)
    forecast_engine = FinancialForecastEngine(data_loader)
    plan_generator = PlanGenerator(data_loader, forecast_engine)
    rec = plan_generator.generate_recommendation(request)
    return {
        "request_id": rec.request_id,
        "amount_safe_to_pay": rec.amount_safe_to_pay,
        "affordability_status": rec.affordability_status,
        "recommended_payment_method": rec.recommended_payment_method,
        "payment_plan": rec.payment_plan,
        "earliest_date_for_full_payment": rec.earliest_date_for_full_payment,
        "spending_changes_needed": rec.spending_changes_needed,
        "decision_explanation": rec.decision_explanation,
    }


def run_pipeline(
    dataset_dir: str = "dataset",
    requests_file: str = "requests.csv",
    output_file: str = "output.csv",
    usage_report_file: str = "evaluation/usage_report.md",
    sample_mode: bool = False,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """
    Execute end-to-end evaluation pipeline.
    """
    logger.info("Initializing DataLoader for dataset at '%s'...", dataset_dir)
    data_loader = DataLoader(dataset_dir)
    forecast_engine = FinancialForecastEngine(data_loader)
    plan_generator = PlanGenerator(data_loader, forecast_engine)

    target_filename = "sample_requests.csv" if sample_mode else requests_file
    logger.info("Loading requests from '%s'...", target_filename)
    requests = data_loader.load_requests(target_filename)

    if limit is not None and limit > 0:
        logger.info("Limiting to first %d requests...", limit)
        requests = requests[:limit]

    results: List[Dict[str, object]] = []
    for idx, req in enumerate(requests, start=1):
        rec: Recommendation = plan_generator.generate_recommendation(req)
        results.append({
            "request_id": rec.request_id,
            "amount_safe_to_pay": rec.amount_safe_to_pay,
            "affordability_status": rec.affordability_status,
            "recommended_payment_method": rec.recommended_payment_method,
            "payment_plan": rec.payment_plan,
            "earliest_date_for_full_payment": rec.earliest_date_for_full_payment,
            "spending_changes_needed": rec.spending_changes_needed,
            "decision_explanation": rec.decision_explanation,
        })

    output_df = pd.DataFrame(results)

    # If in sample mode, evaluate against sample ground truth
    if sample_mode:
        sample_ground_truth_path = os.path.join(dataset_dir, "sample_requests.csv")
        if os.path.exists(sample_ground_truth_path):
            df_truth = pd.read_csv(sample_ground_truth_path)
            eval_cols = [
                "affordability_status",
                "recommended_payment_method",
                "payment_plan",
                "earliest_date_for_full_payment",
                "spending_changes_needed",
            ]
            matches = {c: 0 for c in eval_cols}
            total = len(output_df)
            for _, r_out in output_df.iterrows():
                rid = r_out["request_id"]
                r_exp = df_truth[df_truth["request_id"] == rid].iloc[0]
                for c in eval_cols:
                    act_v = str(r_out[c]).strip()
                    exp_v = "" if pd.isna(r_exp[c]) else str(r_exp[c]).strip()
                    if act_v == exp_v:
                        matches[c] += 1

            logger.info("=== SAMPLE ACCURACY EVALUATION ===")
            for c, cnt in matches.items():
                logger.info("%-32s: %d/%d (%.1f%%)", c, cnt, total, cnt / total * 100.0)

    # Validate output against raw requests
    raw_req_path = os.path.join(dataset_dir, target_filename)
    if os.path.exists(raw_req_path):
        df_raw = pd.read_csv(raw_req_path)
        if limit is not None and limit > 0:
            df_raw = df_raw.head(limit)
        logger.info("Validating output against official invariants...")
        is_valid, errors = validate_output(output_df, df_raw)
        if not is_valid:
            logger.error("Validation failed with %d errors:", len(errors))
            for err in errors[:10]:
                logger.error(" - %s", err)
            raise ValueError(f"Output validation failed with {len(errors)} errors.")
        logger.info("All output invariants passed successfully!")

    # Write output.csv
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    output_df.to_csv(output_file, index=False)
    logger.info("Wrote %d rows to '%s'.", len(output_df), output_file)

    # Write usage report
    generate_usage_report(
        report_path=usage_report_file,
        num_requests=len(requests),
        num_calls=0,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0.0,
    )

    return output_df


def main():
    parser = argparse.ArgumentParser(
        description="Buy or Wait Agent — AI Financial Decision Engine for HackerRank Orchestrate"
    )
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default="dataset",
        help="Path to dataset directory (default: dataset)",
    )
    parser.add_argument(
        "--requests",
        type=str,
        default="requests.csv",
        help="Requests filename inside dataset-dir or path (default: requests.csv)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output.csv",
        help="Path to output CSV file (default: output.csv)",
    )
    parser.add_argument(
        "--usage-report",
        type=str,
        default="evaluation/usage_report.md",
        help="Path to usage report markdown file (default: evaluation/usage_report.md)",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Run against sample_requests.csv and evaluate accuracy against ground truth",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of requests to process",
    )

    args = parser.parse_args()

    run_pipeline(
        dataset_dir=args.dataset_dir,
        requests_file=args.requests,
        output_file=args.output,
        usage_report_file=args.usage_report,
        sample_mode=args.sample,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
