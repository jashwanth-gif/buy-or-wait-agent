import json
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 1. Load data
df_prof = pd.read_csv('dataset/financial_profiles.csv')
df_ev = pd.read_csv('dataset/financial_events.csv')
df_rates = pd.read_csv('dataset/exchange_rates.csv')
df_opts = pd.read_csv('dataset/request_payment_options.csv')
df_msgs = pd.read_csv('dataset/messages.csv')
df_imgs = pd.read_csv('dataset/images.csv')
df_sample = pd.read_csv('dataset/sample_requests.csv')

# Image amount map
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

# Image to event map
img_to_ev = dict(zip(df_imgs['image_id'], df_imgs['related_event_id']))
ev_to_img_amt = {img_to_ev[k]: v for k, v in IMAGE_AMOUNTS.items() if k in img_to_ev}

print(f"Loaded {len(ev_to_img_amt)} image-resolved events.")
