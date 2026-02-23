import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# הגדרות RTL ועיצוב קשיח למניעת "יוזמות" של ה-AI
st.set_page_config(page_title="מנתח פנסיה - גרסה 29.0 (דיוק מכני)", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
</style>
""", unsafe_allow_html=True)

def init_client():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key) if api_key else None

def clean_num(val):
    if val is None or val == "" or str(val).strip() in ["-", "nan", ".", "0"]: return 0.0
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except: return 0.0

def perform_cross_validation(data):
    """אימות הצלבה קשיח: טבלה ב' מול טבלה ה'"""
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "כספים שהופקדו"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 10]
            if nums: dep_b = nums[0]
            break
            
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = clean_num(rows_e[-1].get("סה\"כ", 0)) if rows_e else 0.0
    
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.2f} ₪) תואם במדויק.</div>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div class="val-error">⚠️ שגיאת אימות: טבלה ב\' ({dep_b:,.2f} ₪) לעומת טבלה ה\' ({dep_e:,.2f} ₪).</div>', unsafe_allow_html=True)

def display_pension_table(rows, title, col_order):
    if not rows: return
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_audit_v29(client, text):
    prompt = f"""You are a MECHANICAL SCRIBE. Your ONLY job is to transcribe text to JSON with ZERO intelligence applied.
    
    STRICT RULES FOR EXTRACTION:
    1. DIGIT-BY-DIGIT COPYING: If a number is '67', do not write '76'. If it is '0.17', do not write '1.0'.
    2. TABLE D (CLAL SPECIAL): Track names in 'Clal' often span multiple lines. Join them into one name. Find the number with the '%' sign nearby and copy it EXACTLY as the 'תשואה'. 
    3. NO ROUNDING: Do not round any percentages. If it has two decimal places, copy both.
    4. TABLE E SUMMARY:
       - The last row is 'סה"כ'. 
       - The largest number in that row (Total of totals) MUST go into 'סה"כ'.
       - Map Employee/Employer/Severance sums digit-by-digit.
       - Clear 'מועד' and 'חודש' fields for this row.
    
    JSON STRUCTURE:
    {{
      "table_a": {{"rows": [{{"תיאור": "", "סכום בש\"ח": ""}}]}},
      "table_b": {{"rows": [{{"תיאור": "", "סכום בש\"ח": ""}}]}},
      "table_c": {{"rows": [{{"תיאור": "", "אחוז": ""}}]}},
      "table_d": {{"rows": [{{"מסלול": "", "תשואה": ""}}]}},
      "table_e": {{"rows": [{{ "שם המעסיק": "", "מועד": "", "חודש": "", "שכר": "", "עובד": "", "מעסיק": "", "פיצויים": "", "סה\"כ": "" }}]}}
    }}
    TEXT: {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a mechanical character-copying tool. You have no brain. You only copy characters exactly. No rounding. No logic."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # תיקון הסטות וחישוב שכר ב-Python (כדי שלא יטעה במתמטיקה)
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        last_row = rows_e[-1]
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        
        # תיקון הסטה אם הסה"כ "ברח" לעמודת הפיצויים
        vals = [last_row.get("עובד"), last_row.get("מעסיק"), last_row.get("פיצויים"), last_row.get("סה\"כ")]
        cleaned_vals = [clean_num(v) for v in vals]
        max_val = max(cleaned_vals)
        
        if max_val > 0 and clean_num(last_row.get("סה\"כ")) != max_val:
            non_zero_vals = [v for v in vals if clean_num(v) > 0]
            if len(non_zero_vals) == 4:
                last_row["סה\"כ"], last_row["פיצויים"], last_row["מעסיק"], last_row["עובד"] = non_zero_vals[3], non_zero_vals[2], non_zero_vals[1], non_zero_vals[0]
            elif len(non_zero_vals) == 3:
                last_row["סה\"כ"], last_row["מעסיק"], last_row["עובד"] = non_zero_vals[2], non_zero_vals[1], non_zero_vals[0]
                last_row["פיצויים"] = "0"
            
        last_row["שכר"] = f"{salary_sum:,.0f}"
        last_row["מועד"] = ""
        last_row["חודש"] = ""
        last_row["שם המעסיק"] = "סה\"כ"
    
    return data

# ממשק
st.title("📋 מנתח פנסיה - גרסה 29.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF (מגדל, כלל, אלטשולר וכו')", type="pdf")
    if file:
        with st.spinner("מעתיק נתונים במדויק..."):
            raw_text = "\n".join([page.get_text() for page in fitz.open(stream=file.read(), filetype="pdf")])
            data = process_audit_v29(client, raw_text)
            
            if data:
                perform_cross_validation(data)
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", ["מסלול", "תשואה"])
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
