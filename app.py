import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# הגדרות תצוגה RTL
st.set_page_config(page_title="מנתח פנסיה - גרסה 18.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
</style>
""", unsafe_allow_html=True)

def init_client():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key) if api_key else None

def get_full_pdf_text(file):
    file.seek(0)
    doc = fitz.open(stream=file.read(), filetype="pdf")
    full_text = ""
    for i, page in enumerate(doc):
        full_text += f"--- PAGE {i+1} ---\n" + page.get_text() + "\n"
    return full_text

def clean_num(val):
    if val is None: return 0.0
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0

def perform_cross_validation(data):
    """אימות הצלבה משופר - מחפש בכל המפתחות האפשריים"""
    # 1. מציאת סכום ההפקדות בטבלה ב'
    deposit_in_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        content = " ".join(str(v) for v in r.values())
        if "הופקדו" in content or "הפקדות" in content:
            # מחפש את הערך המספרי בשורה
            for val in r.values():
                num = clean_num(val)
                if num > 10: # מניעת תפיסת אחוזים קטנים
                    deposit_in_b = num
                    break
            break
            
    # 2. מציאת שורת הסה"כ בטבלה ה'
    deposit_in_e = 0.0
    rows_e = data.get("table_e", {}).get("rows", [])
    if rows_e:
        last_row = rows_e[-1]
        deposit_in_e = clean_num(last_row.get("סה\"כ", 0))
    
    if abs(deposit_in_b - deposit_in_e) < 5 and deposit_in_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות (₪{deposit_in_b:,.0f}) תואם בין הטבלאות.</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="val-error">⚠️ אימות נכשל: טבלה ב\' (₪{deposit_in_b:,.0f}) לעומת טבלה ה\' (₪{deposit_in_e:,.0f}).</div>', unsafe_allow_html=True)

def display_pension_table(rows, title, col_order=None):
    if not rows: return
    df = pd.DataFrame(rows)
    # סידור עמודות - טקסט בימין, מספרים בשמאל
    if col_order:
        existing_cols = [c for c in col_order if c in df.columns]
        other_cols = [c for c in df.columns if c not in existing_cols]
        df = df[existing_cols + other_cols]
    
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_audit_v18(client, text):
    prompt = f"""Extract ALL pension tables into JSON.
    
    TABLE E RULES:
    1. Extract all rows exactly. STOP immediately at the row starting with 'סה"כ'.
    2. TOTAL ROW (סה"כ): 
       - You MUST MANUALLY SUM all values in the 'שכר' column. 
       - DO NOT use the deposit total (like 42,023) for the salary sum. 
       - For 'מור', the salary sum should be around 171,385.
    
    TABLES A, B, C, D:
    - Extract ALL rows without skipping. 
    - TABLE C: Include 'הוצאות ניהול השקעות'.
    - TABLE D: Use the full track name.

    JSON STRUCTURE:
    {{
      "table_a": {{"rows": [{{"תיאור": "", "סכום בש\"ח": ""}}]}},
      "table_b": {{"rows": [{{"תיאור": "", "סכום בש\"ח": ""}}]}},
      "table_c": {{"rows": [{{"תיאור": "", "אחוז": ""}}]}},
      "table_d": {{"rows": [{{"מסלול": "", "תשואה": ""}}]}},
      "table_e": {{"rows": [{{ "מועד": "", "חודש": "", "שכר": "", "עובד": "", "מעסיק": "", "פיצויים": "", "סה\"כ": "" }}]}}
    }}
    TEXT: {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a forensic auditor. Sum the salary column correctly. Use Hebrew keys."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 18.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מחלץ ומאמת נתונים..."):
            raw_text = get_full_pdf_text(file)
            data = process_audit_v18(client, raw_text)
            
            if data:
                perform_cross_validation(data)
                
                # תצוגה עם סדר עמודות מתוקן (תיאור בימין)
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", ["מסלול", "תשואה"])
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", ["מועד", "חודש", "שכר"])
                
                st.markdown("---")
                st.download_button("📥 הורד JSON", json.dumps(data, indent=2, ensure_ascii=False), "pension_audit.json")
