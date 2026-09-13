import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import re

df_prof = pd.read_csv('dataset/financial_profiles.csv')
df_ev = pd.read_csv('dataset/financial_events.csv')
df_rates = pd.read_csv('dataset/exchange_rates.csv')
df_opts = pd.read_csv('dataset/request_payment_options.csv')
df_msgs = pd.read_csv('dataset/messages.csv')
df_imgs = pd.read_csv('dataset/images.csv')
df_sample = pd.read_csv('dataset/sample_requests.csv')

IMAGE_AMOUNTS = {
    'image_01': 4365000.0,
    'image_02': 100000.0,
    'image_03': 41272.0,
    'image_04': 2854.0,
    'image_05': 704.05,
    'image_06': 1995.0,
    'image_07': 8528.0,
    'image_08': 15339.0,
    'image_09': 723.0,
    'image_10': 79679.26,
    'image_11': 3650.0,
    'image_12': 33.50,
    'image_13': 2298.0,
    'image_14': 4543.0,
    'image_15': 9968.0,
    'image_16': 393.22,
}
img_to_ev = dict(zip(df_imgs['image_id'], df_imgs['related_event_id']))
ev_to_img_amt = {img_to_ev[k]: v for k, v in IMAGE_AMOUNTS.items() if k in img_to_ev}

# Fill missing amounts
for idx, row in df_ev.iterrows():
    ev_id = row['event_id']
    if pd.isna(row['amount']) and ev_id in ev_to_img_amt:
        df_ev.at[idx, 'amount'] = ev_to_img_amt[ev_id]

# Build rate lookup: (rate_date, from_currency, to_currency) -> rate
rates_map = {}
for _, r in df_rates.iterrows():
    rates_map[(r['rate_date'], r['from_currency'], r['to_currency'])] = float(r['rate'])

def convert_amount(amt, from_curr, to_curr, date_str):
    if from_curr == to_curr or pd.isna(amt):
        return amt
    key = (date_str, from_curr, to_curr)
    if key in rates_map:
        return amt * rates_map[key]
    # Fallback to closest date or inverse
    for (rd, fc, tc), rate in rates_map.items():
        if fc == from_curr and tc == to_curr:
            return amt * rate
        if fc == to_curr and tc == from_curr:
            return amt / rate
    return amt

print("Converted setup complete.")
