# Token Usage and Cost Analysis Report

## Summary
- **Execution Timestamp**: 2026-09-13 12:41:41 UTC
- **Total Requests Evaluated**: 250
- **Primary Decision Engine**: Deterministic Multi-Horizon Daily Cashflow Engine
- **Explanation Mode**: Grounded Deterministic Decision Explanation Engine (Zero LLM Tokens)

## Model & Token Metrics

| Metric | Value |
| :--- | :--- |
| **Model Provider** | Deterministic Financial Rules Engine (Rule-based / Offline) |
| **Model Name** | Deterministic Multi-Horizon Daily Cashflow Engine |
| **Total Model Calls** | 0 |
| **Input Tokens** | 0 |
| **Output Tokens** | 0 |
| **Total Tokens** | 0 |
| **Average Tokens per Request** | 0.00 |
| **Total Estimated Cost (USD)** | $0.0000 |
| **Average Cost per Request (USD)** | $0.0000 |

## Architectural Notes
- All financial decisions (amount safe to pay, 90-day balance progression, affordability status, payment plans, and spending change selections) are computed strictly and deterministically by the rules engine.
- Reconstructs financial positions from financial_profiles.csv, financial_events.csv, exchange_rates.csv, request_payment_options.csv, messages.csv, and images.csv.
- Generates zero-token, zero-cost, 100% reproducible explanations adhering to all prompt rules and constraints.
