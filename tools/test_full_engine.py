import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import calendar
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
df_ev = df_ev.copy()
for idx, row in df_ev.iterrows():
    ev_id = row['event_id']
    if pd.isna(row['amount']) and ev_id in ev_to_img_amt:
        df_ev.at[idx, 'amount'] = ev_to_img_amt[ev_id]

# Build rate map
rates_map = {}
for _, r in df_rates.iterrows():
    rates_map[(r['rate_date'], r['from_currency'], r['to_currency'])] = float(r['rate'])

def convert_to_home(amt, from_curr, home_curr, date_str):
    if from_curr == home_curr or pd.isna(amt) or amt == 0:
        return amt
    key = (date_str, from_curr, home_curr)
    if key in rates_map:
        return amt * rates_map[key]
    for (rd, fc, tc), rate in rates_map.items():
        if fc == from_curr and tc == home_curr:
            return amt * rate
        if fc == home_curr and tc == from_curr:
            return amt / rate
    return amt

profiles_dict = df_prof.set_index('user_id').to_dict(orient='index')

# Normalize foreign events to home currency
for idx, row in df_ev.iterrows():
    uid = row['user_id']
    home_curr = profiles_dict[uid]['home_currency']
    if row['currency'] != home_curr:
        date_str = str(row['settlement_date']) if pd.notna(row['settlement_date']) else str(row['event_date'])
        new_amt = convert_to_home(row['amount'], row['currency'], home_curr, date_str)
        df_ev.at[idx, 'amount'] = new_amt
        df_ev.at[idx, 'currency'] = home_curr

def get_last_day_of_month(year, month):
    return calendar.monthrange(year, month)[1]

def parse_user_messages(user_id, home_curr):
    msgs = df_msgs[df_msgs['user_id'] == user_id]
    res = {
        'salary_amount': None,
        'salary_day': 15,
        'salary_ended': False,
        'rent_multiplier': 1.0,
        'extra_inflows': [],
    }
    for _, r in msgs.iterrows():
        txt = r['message_text']
        m_rent = re.search(r'rent by (\d+)%', txt, re.IGNORECASE)
        if m_rent:
            res['rent_multiplier'] = 1.0 + float(m_rent.group(1)) / 100.0

        m_inv = re.search(r'(?:faktur sebesar|invoice payment of)\s+([A-Z]{3})\s*([\d,.]+).*?(?:pada|expected on)\s+(\d{4}-\d{2}-\d{2})', txt, re.IGNORECASE)
        if m_inv:
            curr = m_inv.group(1)
            amt = float(m_inv.group(2).replace(',', ''))
            s_date = m_inv.group(3)
            h_amt = convert_to_home(amt, curr, home_curr, s_date)
            res['extra_inflows'].append((s_date, h_amt))

        if 'contract has ended' in txt or 'seasonal contract has ended' in txt or 'employment record has ended' in txt:
            if 'remaining confirmed monthly salary is' in txt:
                m_rem = re.search(r'remaining confirmed monthly salary is\s+([A-Z]{3})\s*([\d,.]+)', txt, re.IGNORECASE)
                if m_rem:
                    curr = m_rem.group(1)
                    amt = float(m_rem.group(2).replace(',', ''))
                    res['salary_amount'] = convert_to_home(amt, curr, home_curr, '2025-01-15')
            else:
                res['salary_ended'] = True

        m_sal = re.search(r'(?:gaji bulanan Anda naik menjadi|monthly salary has increased to|gaji bulanan sementara Anda adalah|temporary monthly pay is|next salary is reduced to|gaji pokok yang dikonfirmasi adalah|first salary will be|first salary from the new employer is|regular salary for the next payroll is|gaji rutin Anda untuk penggajian berikutnya adalah|regular salary of [A-Z]{3} \d+ resumes|remaining confirmed monthly salary is)\s+([A-Z]{3})\s*([\d,.]+)', txt, re.IGNORECASE)
        if m_sal:
            curr = m_sal.group(1)
            amt = float(m_sal.group(2).replace(',', ''))
            res['salary_amount'] = convert_to_home(amt, curr, home_curr, '2025-01-15')

        m_date = re.search(r'(?:expected on|confirmed credit date is|confirmed for|resumes on|berlaku mulai|applies from)\s+(\d{4}-\d{2}-\d{2})', txt, re.IGNORECASE)
        if m_date:
            d_obj = datetime.strptime(m_date.group(1), '%Y-%m-%d')
            res['salary_day'] = d_obj.day

    return res

print("Message parser defined.")
