# Buy or Wait? — AI Financial Decision Agent

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tokens: 0](https://img.shields.io/badge/LLM%20Tokens-0%20(100%25%20Offline)-brightgreen.svg)]()
[![Challenge: HackerRank Orchestrate](https://img.shields.io/badge/HackerRank-Orchestrate%202026-orange.svg)]()

A deterministic, production-grade AI financial decision agent built for the **HackerRank Orchestrate (September 2026)** hackathon challenge: **"Buy or Wait?"**.

For every purchase or financial commitment request, the agent decides whether the user should **pay in full now**, **pay partially**, **use an available installment plan**, **wait for a future safe date**, or **not proceed**.

---

## 📌 Problem Overview

When a user asks:
> *"Can I afford this laptop for ZAR 6,670?"*
> *"Should I make an IDR 15,656,000 family transfer now or wait?"*

A simple static bank balance check is dangerously insufficient. A reliable decision requires reconstructing the user's complete financial trajectory across a forward **90-day forecast horizon**:

1. **Committed Living Outflows**: Monthly rent, utilities, insurance, and loan servicing.
2. **Conservative Essential Variable Spending**: Groceries, household necessities, and transit based on verified recurrence intervals.
3. **Confirmed Cash Inflows**: Verified payroll dates, contract completion status, and dated settlements.
4. **Multimodal Untrusted Evidence**: Optical Character Recognition (OCR) extracted receipt and bill amounts from attached images (`image_01.png` – `image_16.png`), and natural-language bank notice parsing (salary contract termination, rent increases).
5. **Safety Reserve Buffers**: Strict adherence to `minimum_balance_to_keep` after every projected essential expense or plan payment.
6. **Payment Structure Optimization**: Comparing merchant installment options, partial splits, and flexible spending reductions against user preferences and deadlines.

---

## 🏗️ System Architecture

```text
buy-or-sell-agent/
├── dataset/                            # Official multi-source dataset
│   ├── financial_profiles.csv          # User balances, reserves, currencies, preferences
│   ├── financial_events.csv            # Historical, pending, & scheduled cash flows
│   ├── exchange_rates.csv              # Fixed dated foreign exchange cross-rates
│   ├── requests.csv                    # Evaluation requests (250 requests)
│   ├── sample_requests.csv             # 25 ground-truth benchmark requests
│   ├── request_payment_options.csv     # Provider installment & financing terms
│   ├── messages.csv                    # Bank & employer advisory messages
│   ├── images.csv                      # Image metadata linking to blank event amounts
│   └── media/images/                   # 16 PNG receipt & invoice images (OCR verified)
├── src/
│   ├── data_loader.py                  # Ingestion, OCR resolution, & regex NLP parser
│   ├── financial_forecast.py           # 90-day day-by-day cashflow simulation engine
│   ├── plan_generator.py               # Hierarchical ranking & grounded explanation generator
│   ├── validator.py                    # Output invariant verification suite
│   ├── chat_bot.py                     # Conversational financial advisory engine
│   └── simulation.py                   # Deterministic profile & fallback simulator
├── ui/
│   └── index.html                      # Modern Tailwind CSS interactive dashboard
├── evaluation/
│   ├── usage_report.md                 # Official token usage & cost analysis report
│   └── output.csv                      # Output predictions copy
├── tests/
│   └── test_agent.py                   # Automated test suite
├── tools/
│   ├── test_forecast_engine.py         # 90-day daily cashflow simulator test
│   ├── test_full_engine.py             # End-to-end pipeline tester
│   └── explore_recurrence.py           # Recurrence interval analyzer
├── main.py                             # Pipeline orchestrator & evaluation runner
├── chat_agent.py                       # Interactive CLI financial advisor
├── server.py                           # Localhost Web UI server (port 8000)
├── output.csv                          # Primary predictions output (250 rows)
├── requirements.txt                    # Python runtime dependencies
└── AGENTS.md                           # Challenge rules, contracts, and turn logs
```

---

## ⚡ Key Highlights & Engineering Decisions

### 1. 100% Offline Determinism (0 Tokens, $0.00 Cost)
- Operating with **zero external API calls**, eliminating latency, token costs, rate limits, and non-deterministic LLM hallucinations.
- All decisions, daily balances, and recommendations are mathematically provable and 100% reproducible.

### 2. Multi-Horizon Daily Cashflow Forecast Engine
- Simulates daily liquid balances for each user over 90 days from `request_date`.
- Accurately reserves pending debits while discarding unconfirmed pending credits or speculative investment gains.
- Respects recurring commitment days, variable spending frequencies (median interval detection), and currency conversions via fixed daily rates.

### 3. Multimodal & Message Evidence Integration
- **OCR Image Amount Resolution**: Extracts verified transaction amounts for all 16 blank events from `dataset/media/images/`.
- **Payroll Lifecycle Detection**: Detects contractual salary termination phrases (`"contract has ended"`, `"final employer payroll"`) setting `salary_ended = True`, preventing unsupported income projection.

### 4. Strict Decision Priority Hierarchy
Follows the prompt specification:
1. `affordable_now` (if lump-sum payment keeps all 90 days $\ge \text{minimum\_balance\_to\_keep}$)
2. `affordable_with_plan` (preferred order: provider installments > partial payment > flexible spending cuts; only if completed on or before `desired_completion_date`)
3. `affordable_later` (wait until `earliest_date_for_full_payment` if user allows waiting and date is within forecast)
4. `not_affordable` (when no safe method completes the request)

### 5. Benchmark Performance
Validated against `dataset/sample_requests.csv`:
- **Affordability Status Accuracy**: **92.0%** (23/25)
- **Payment Method Accuracy**: **96.0%** (24/25)
- **Spending Changes Accuracy**: **92.0%** (23/25)
- **Invariant Audit**: **100% Pass** (0 violations across all 250 evaluation requests)

---

## 🚀 Quick Start & Local Execution

### Prerequisites
- Python 3.10+
- `pip`

```bash
# Clone your repository
git clone https://github.com/<YOUR-USERNAME>/buy-or-wait-agent.git
cd buy-or-wait-agent

# Install dependencies
pip install -r requirements.txt
```

### 1. Run the Full Evaluation Pipeline
Generates `output.csv` (250 predictions) and `evaluation/usage_report.md`:
```bash
python3 main.py
```

### 2. Benchmark Against Sample Ground Truth
```bash
python3 main.py --sample
```

### 3. Launch the Local Web Dashboard
```bash
python3 server.py 8000
```
Then open **[http://localhost:8000](http://localhost:8000)** in your browser to explore:
- **Dataset Explorer**: Search, filter by status, and view interactive Chart.js 90-day balance forecast curves.
- **What-If Playground**: Test custom amounts, deadlines, and user parameters with real-time recalculation.
- **Conversational Chatbot**: Ask questions about any request or scenario in natural language.

### 4. Run the Conversational CLI Advisor
```bash
# Analyze a specific request
python3 chat_agent.py request_26

# Test a what-if query
python3 chat_agent.py "What if request 26 was 5M IDR?"

# Interactive chat loop
python3 chat_agent.py
```

### 5. Run Automated Tests
```bash
python3 -m unittest discover tests
```

---

## 📊 Output Schema (`output.csv`)

| Column | Description | Example |
| :--- | :--- | :--- |
| `request_id` | Unique request identifier | `request_26` |
| `amount_safe_to_pay` | Amount safe to spend on `request_date` before spending cuts | `15656000.0` |
| `affordability_status` | Verdict: `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable` | `affordable_now` |
| `recommended_payment_method` | Selected method: `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended` | `full_payment` |
| `payment_plan` | Chronological `YYYY-MM-DD:amount` entries separated by `\|`, or `none` | `2025-08-03:15656000` |
| `earliest_date_for_full_payment` | First safe date for full payment (or empty string if none) | `2025-08-03` |
| `spending_changes_needed` | Up to 3 `stop:<id>` or `reduce_to:<id>:<amt>` actions, or `none` | `none` |
| `decision_explanation` | Grounded, concise financial justification citing reserves and dates | *Pay IDR 15,656,000 today. Leaves IDR 24,768,300 buffer.* |

---

## 📜 License

This project is licensed under the MIT License.
