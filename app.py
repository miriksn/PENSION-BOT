import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# -------------------------------
# UI SETTINGS
# -------------------------------
st.set_page_config(page_title="מנתח פנסיה - גרסה 29.2-GEO", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
</style>
""", unsafe_allow_html=True)


# -------------------------------
# CLIENT
# -------------------------------
def init_client():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key) if api_key else None


def clean_num(val):
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", ""))
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0


# -------------------------------
# GEO EXTRACTION FOR TABLE D
# -------------------------------
def extract_table_d_geo(doc):
    rows = []

    for page in doc:
        words = page.get_text("words")  # with coordinates

        # locate header of table D
        header_y = None
        for w in words:
            if "מסלולי" in w[4]:
                header_y = w[1]
                break

        if header_y is None:
            continue

        # words under header (reasonable vertical window)
        table_words = [w for w in words if header_y < w[1] < header_y + 300]

        # group by line (y)
        lines = {}
        for w in table_words:
            y_key = round(w[1], 1)
            lines.setdefault(y_key, []).append(w)

        for line_words in lines.values():
            line_words.sort(key=lambda x: x[0])  # sort by X
            full_line = " ".join(w[4] for w in line_words)

            match = re.search(r'-?\d+\.\d+%', full_line)
            if match:
                percent = match.group(0)
                name = full_line.replace(percent, "").strip()

                rows.append({
                    "מסלול": name,
                    "תשואה": percent
                })

    return rows


# -------------------------------
# AI EXTRACTION FOR OTHER TABLES
# -------------------------------
def process_other_tables(client, text):
    prompt = f"""
You copy characters exactly. No interpretation. No rounding.

Return JSON:
{{
  "table_a": {{"rows": []}},
  "table_b": {{"rows": []}},
  "table_c": {{"rows": []}},
  "table_e": {{"rows": []}}
}}

TEXT:
{text}
"""

    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You copy text exactly."},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )

    return json.loads(res.choices[0].message.content)


# -------------------------------
# DISPLAY
# -------------------------------
def display_pension_table(rows, title, col_order):
    if not rows:
        return
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)


# -------------------------------
# UI
# -------------------------------
st.title("📋 חילוץ נתונים פנסיוני - גרסה 29.2-GEO")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")

    if file:
        with st.spinner("חילוץ טקסט ונתונים..."):

            file_bytes = file.read()
            doc = fitz.open(stream=file_bytes, filetype="pdf")

            # GEO TABLE D (only)
            table_d_rows = extract_table_d_geo(doc)

            # AI for others
            raw_text = "\n".join(
                page.get_text("text", sort=True) for page in doc
            )

            other_tables = process_other_tables(client, raw_text)

            # display
            display_pension_table(other_tables.get("table_a", {}).get("rows"),
                                  "א. תשלומים צפויים",
                                  ["תיאור", "סכום בש\"ח"])

            display_pension_table(other_tables.get("table_b", {}).get("rows"),
                                  "ב. תנועות בקרן",
                                  ["תיאור", "סכום בש\"ח"])

            display_pension_table(other_tables.get("table_c", {}).get("rows"),
                                  "ג. דמי ניהול והוצאות",
                                  ["תיאור", "אחוז"])

            display_pension_table(table_d_rows,
                                  "ד. מסלולי השקעה (GEO)",
                                  ["מסלול", "תשואה"])

            display_pension_table(other_tables.get("table_e", {}).get("rows"),
                                  "ה. פירוט הפקדות",
                                  ["שם המעסיק", "מועד", "חודש", "שכר",
                                   "עובד", "מעסיק", "פיצויים", "סה\"כ"])
