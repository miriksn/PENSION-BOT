import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# הגדרות תצוגה RTL קשיחות
st.set_page_config(page_title="מנתח פנסיה - גרסה 21.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th { text-align: right !important; background-color: #f1f5f9; }
    td { text-align: right !important; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
</style>
""", unsafe_allow_html=True)

def init_client():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    return OpenAI(api_key=api_key) if api_key else None

def clean_num(val):
    if val is None or val == "": return 0.0
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except: return 0.0

def perform_cross_validation(data):
    """אימות הצלבה: בודק שסכום ההפקדות בטבלה ב' תואם לשורת הסה\"כ בטבלה ה'"""
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "הפקדות"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 100]
            if nums: dep_b = nums[0]
            break
            
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = 0.0
    if rows_e:
        last_row = rows_e[-1]
        dep_e = clean_num(last_row.get("סה\"כ", 0))
    
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.0f} ₪) תואם בין הטבלאות.</div>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div class="val-error">⚠️ אימות נכשל: טבלה ב\' (₪{dep_b:,.0f}) לעומת טבלה ה\' (₪{dep_e:,.0f}).</div>', unsafe_allow_html=True)

def display_pension_table(rows, title, col_order):
    """הצגת טבלה עם סדר עמודות נכון (מימין לשמאל)"""
    if not rows: return
    df = pd.DataFrame(rows)
    # סידור עמודות: העמודה הראשונה ברשימה תהיה הימנית ביותר ב-RTL
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_audit_v21(client, text):
    prompt = f"""Extract ALL tables into JSON.
    
    IMPORTANT RULES:
    1. TABLE E: Extract every row. STOP extracting immediately when you reach the summary row that starts with 'סה"כ'.
    2. IGNORE any rows appearing after the first summary 'סה"כ' row (like future year deposits).
    3. TABLE C: Include all personal management fees and 'הוצאות ניהול השקעות'.
    4. TABLE D: Extract the FULL track name (e.g., 'מסלול כספי שקלי').

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
        messages=[{"role": "system", "content": "You are a precise financial auditor. Use Hebrew keys only. Do not summarize Table E."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # חישוב שכר ב-Python (דיוק 100%)
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        rows_e[-1]["שכר"] = f"{salary_sum:,.0f}"
    
    return data

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 21.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מחלץ ומאמת נתונים..."):
            # שולחים את כל הטקסט ללא חיתוך ידני שעלול להרוס, אך עם הנחיה ברורה ל-AI לעצור ב'סה"כ'
            file.seek(0)
            doc = fitz.open(stream=file.read(), filetype="pdf")
            full_text = "\n".join([page.get_text() for page in doc])
            
            data = process_audit_v21(client, full_text)
            
            if data:
                perform_cross_validation(data)
                
                # תצוגה: העמודה הראשונה ברשימה תהיה הימנית ביותר בטבלה
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", ["מסלול", "תשואה"])
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
                
                st.download_button("📥 הורד JSON", json.dumps(data, indent=2, ensure_ascii=False), "pension_audit.json")
