import streamlit as st
import fitz
import json
import os
import io
import zipfile
import pandas as pd
import re
from openai import OpenAI

st.set_page_config(page_title="מנתח פנסיה - גירסה 30.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
                   background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error   { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
                   background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
    .val-warn    { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
                   background-color: #fffbeb; border: 1px solid #d97706; color: #d97706; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────
def init_client():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key) if api_key else None


def clean_num(val):
    if val is None or val == "" or str(val).strip() in ["-", "nan", ".", "0"]:
        return 0.0
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0


def fmt(n):
    """Format a number with commas and 2 decimals."""
    return f"{n:,.2f}"


# ─────────────────────────────────────────────
# Table D — Python extraction from raw text
# (bypasses AI digit-flip errors entirely)
# ─────────────────────────────────────────────
def extract_table_d_python(raw_text):
    """
    Extracts investment tracks and returns DIRECTLY from the raw PDF text.

    Assumption (confirmed by user): on every data row in table D,
    the track name (מסלול) appears to the RIGHT and the return (תשואה)
    appears to its LEFT — both on the SAME line.

    Strategy:
      1. Find the line that is the header of table D (contains מסלול/תשואה/מסלולי).
      2. Find the NEXT table header after it (contains keywords of tables A/B/C/E/F)
         — that marks the END of table D.
      3. Within that window only, extract lines that have Hebrew + percentage.
    """
    lines     = [l.strip() for l in raw_text.split('\n')]
    non_blank = [(i, l) for i, l in enumerate(lines) if l]

    # ── Keywords that signal a NEW table section (not table D) ────────────
    OTHER_TABLE_HEADERS = re.compile(
        r'פירוט.?הפקדות|תנועות.?בקרן|תשלומים.?צפויים|דמי.?ניהול|'
        r'הוצאות|הרכב.?נכסים|שינויים.?בחשבון|יתרות|טבלה.?[אבגהו]'
    )

    # ── Step 1: find table D header ───────────────────────────────────────
    header_idx = None
    for i, line in non_blank:
        if re.search(r'מסלולי.?השקעה|מסלול.{0,6}תשואה|תשואה.{0,6}מסלול', line):
            header_idx = i
            break

    if header_idx is None:
        return None

    # ── Step 2: find the next section header after table D ────────────────
    end_idx = header_idx + 60   # default: 60 lines max
    for i, line in non_blank:
        if i <= header_idx:
            continue
        if OTHER_TABLE_HEADERS.search(line):
            end_idx = i
            break

    # ── Step 3: scan only within the table D window ───────────────────────
    PCT    = re.compile(r'-?\d{1,3}(?:\.\d{1,4})?\s*%')
    HEBREW = re.compile(r'[\u0590-\u05FF]')
    window = [(i, l) for i, l in non_blank if header_idx < i < end_idx]

    rows = []
    for _, line in window:
        if PCT.search(line) and HEBREW.search(line):
            pct_match  = PCT.search(line)
            return_val = pct_match.group(0).strip()
            # Track name = everything before the percentage on the same line
            track = line[:pct_match.start()].strip() or line[pct_match.end():].strip()
            if track:
                rows.append({"מסלול": track, "תשואה": return_val})

    return rows if rows else None


# ─────────────────────────────────────────────
# Cross-validation helpers
# ─────────────────────────────────────────────
def perform_cross_validation(data):
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "כספים שהופקדו"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 10]
            if nums:
                dep_b = nums[0]
            break

    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = clean_num(rows_e[-1].get("סה\"כ", 0)) if rows_e else 0.0

    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(
            f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({fmt(dep_e)} ₪) תואם במדויק.</div>',
            unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(
            f'<div class="val-error">⚠️ שגיאת אימות: טבלה ב\' ({fmt(dep_b)} ₪) לעומת טבלה ה\' ({fmt(dep_e)} ₪).</div>',
            unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Table E — rebuild summary row with Python sums
# + validate each column against what AI extracted
# ─────────────────────────────────────────────
NUMERIC_COLS_E     = ["שכר", "עובד", "מעסיק", "פיצויים", 'סה"כ']
VALIDATED_COLS_E   = ["עובד", "מעסיק", "פיצויים", 'סה"כ']   # שכר is always computed — never validated
TOLERANCE_ILS      = 2.0   # ₪ — covers normal PDF rounding differences


def rebuild_table_e_summary(rows_e):
    """
    Replaces the last (summary) row with Python-calculated column sums.
    Validates each column against the AI-extracted value (except שכר).
    """
    if len(rows_e) < 2:
        return rows_e

    data_rows = rows_e[:-1]
    last_row  = rows_e[-1].copy()

    # ── Column sums ──────────────────────────────────────────────────────
    sums = {col: sum(clean_num(r.get(col, 0)) for r in data_rows) for col in NUMERIC_COLS_E}

    # ── Validate against AI-extracted summary (skip שכר) ─────────────────
    st.markdown("**אימות שורת סיכום – טבלה ה':**")
    for col in VALIDATED_COLS_E:
        ai_val = clean_num(last_row.get(col, 0))
        py_sum = sums[col]
        if py_sum == 0:
            continue
        diff = abs(py_sum - ai_val)
        if diff <= TOLERANCE_ILS:
            st.markdown(
                f'<div class="val-success">✅ עמודת "{col}": סכום Python ({fmt(py_sum)} ₪) = ערך ב-PDF ({fmt(ai_val)} ₪).</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="val-warn">⚠️ עמודת "{col}": Python ({fmt(py_sum)} ₪) ≠ PDF ({fmt(ai_val)} ₪) — '
                f'הפרש {fmt(diff)} ₪. הסה"כ תוקן לפי Python.</div>',
                unsafe_allow_html=True)

    # ── Write corrected summary row ────────────────────────────────────
    for col in NUMERIC_COLS_E:
        last_row[col] = f"{sums[col]:,.0f}" if col == "שכר" else fmt(sums[col])

    last_row["מועד"]       = ""
    last_row["חודש"]       = ""
    last_row['שם המעסיק'] = 'סה"כ'

    return data_rows + [last_row]


# ─────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────
def display_pension_table(rows, title, col_order):
    if not rows:
        return
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)


def build_zip_csv(all_tables):
    """
    Build a ZIP archive containing one UTF-8 CSV per table.
    Uses only Python stdlib — no openpyxl / xlsxwriter needed.
    Returns bytes ready for st.download_button.
    """
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for sheet_name, rows, col_order in all_tables:
            if not rows:
                continue
            df = pd.DataFrame(rows)
            existing = [c for c in col_order if c in df.columns]
            df = df[existing]
            # BOM so Excel opens Hebrew CSVs correctly
            csv_bytes = ("﻿" + df.to_csv(index=False)).encode("utf-8")
            zf.writestr(f"{sheet_name}.csv", csv_bytes)
    zip_buf.seek(0)
    return zip_buf.read()


# ─────────────────────────────────────────────
# AI extraction (tables A, B, C, E — NOT D)
# ─────────────────────────────────────────────
def process_audit_v30(client, text):
    prompt = f"""You are a RAW TEXT TRANSCRIBER. Your ONLY job is to copy characters from the text to JSON.
    
    CRITICAL INSTRUCTIONS:
    1. ZERO INTERPRETATION: Do not flip digits (e.g., 50 stays 50, not 05).
    2. ZERO ROUNDING: If a value is 0.17%, write 0.17%. Never round.

    TABLE D: Leave all rows EMPTY — return {{"rows": []}} for table_d.
             Table D will be extracted separately by a Python script.

    TABLE E RULES (פירוט הפקדות):
    - For every REGULAR (non-summary) row:
        * 'מועד' = full deposit date INCLUDING the day, e.g. "05/03/2024". Copy exactly.
        * 'חודש' = salary month WITHOUT a day, e.g. "03/2024". Copy exactly.
        * Do NOT leave these empty for regular rows.
    - For the SUMMARY row only (the last row, labeled סה"כ):
        * 'מועד' and 'חודש' must be empty strings.
        * 'שם המעסיק' must be 'סה"כ'.
    
    JSON STRUCTURE:
    {{
      "table_a": {{"rows": [{{"תיאור": "", "סכום בש\\"ח": ""}}]}},
      "table_b": {{"rows": [{{"תיאור": "", "סכום בש\\"ח": ""}}]}},
      "table_c": {{"rows": [{{"תיאור": "", "אחוז": ""}}]}},
      "table_d": {{"rows": []}},
      "table_e": {{"rows": [{{"שם המעסיק": "", "מועד": "", "חודש": "", "שכר": "",
                              "עובד": "", "מעסיק": "", "פיצויים": "", "סה\\"כ": ""}}]}}
    }}
    TEXT: {text}"""

    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",
             "content": "You are a mechanical OCR tool. Copy characters exactly. Never round, never flip digits."},
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)


# ─────────────────────────────────────────────
# Main UI
# ─────────────────────────────────────────────
st.title("📋 חילוץ נתונים פנסיוני – גירסה 30.0")
client = init_client()

if not client:
    st.error("מפתח OpenAI API לא נמצא. הגדר OPENAI_API_KEY ב-Secrets.")
else:
    uploaded_file = st.file_uploader("העלה דוח פנסיה (PDF)", type="pdf")

    if uploaded_file:
        with st.spinner("מחלץ נתונים..."):
            pdf_bytes = uploaded_file.read()
            raw_text = "\n".join(
                page.get_text() for page in fitz.open(stream=pdf_bytes, filetype="pdf")
            )

            # ── AI extraction (A, B, C, E) ────────────────────────────
            data = process_audit_v30(client, raw_text)

            # ── Python extraction for Table D ─────────────────────────
            d_rows_python = extract_table_d_python(raw_text)
            if d_rows_python:
                data["table_d"] = {"rows": d_rows_python}
                st.success(f"✅ טבלה ד' חולצה ישירות מהטקסט (Python) – {len(d_rows_python)} מסלולים.")
            else:
                st.warning("⚠️ לא נמצאה טבלת מסלולים בטקסט הגולמי. משתמש בתוצאת AI.")

            # ── Rebuild Table E summary row with Python sums ──────────
            rows_e = data.get("table_e", {}).get("rows", [])
            if rows_e:
                data["table_e"]["rows"] = rebuild_table_e_summary(rows_e)

            # ── Cross-validation ──────────────────────────────────────
            perform_cross_validation(data)

            # ── Display all tables ────────────────────────────────────
            col_a = ["תיאור", 'סכום בש"ח']
            col_b = ["תיאור", 'סכום בש"ח']
            col_c = ["תיאור", "אחוז"]
            col_d = ["מסלול", "תשואה"]
            col_e = ['שם המעסיק', "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", 'סה"כ']

            display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים",   col_a)
            display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן",      col_b)
            display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", col_c)
            display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה",      col_d)
            display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות",      col_e)

            # ── Download button ───────────────────────────────────────
            st.divider()
            all_tables = [
                ("א-תשלומים_צפויים",  data.get("table_a", {}).get("rows", []), col_a),
                ("ב-תנועות_בקרן",     data.get("table_b", {}).get("rows", []), col_b),
                ("ג-דמי_ניהול",       data.get("table_c", {}).get("rows", []), col_c),
                ("ד-מסלולי_השקעה",    data.get("table_d", {}).get("rows", []), col_d),
                ("ה-פירוט_הפקדות",    data.get("table_e", {}).get("rows", []), col_e),
            ]
            zip_bytes = build_zip_csv(all_tables)
            st.download_button(
                label="📥 הורד את כל הטבלאות (ZIP / CSV)",
                data=zip_bytes,
                file_name="pension_report.zip",
                mime="application/zip"
            )
