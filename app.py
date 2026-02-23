import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# הגדרות תצוגה RTL קשיחות
st.set_page_config(page_title="מנתח פנסיה - גרסה 22.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    /* יישור כותרות ותאים לימין */
    th, td { text-align: right !important; }
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
        # ניקוי פסיקים וסימני מינוס מיוחדים
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except: return 0.0

def perform_cross_validation(data):
    """אימות הצלבה חכם בין טבלה ב' לטבלה ה'"""
    # 1. מציאת סכום ההפקדות בטבלה ב'
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "כספים שהופקדו"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 100]
            if nums: dep_b = nums[0]
            break
            
    # 2. מציאת שורת הסה"כ בטבלה ה'
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = 0.0
    if rows_e:
        last_row = rows_e[-1]
        dep_e = clean_num(last_row.get("סה\"כ", 0))
    
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.0f} ₪) זהה בטבלאות ב\' ו-ה\'.</div>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div class="val-error">⚠️ אימות נכשל: טבלה ב\' ({dep_b:,.0f} ₪) שונה מטבלה ה\' ({dep_e:,.0f} ₪).</div>', unsafe_allow_html=True)

def display_pension_table(rows, title, col_order):
    """הצגת טבלה עם סדר עמודות נכון: תיאור בימין, ערך בשמאל"""
    if not rows: return
    df = pd.DataFrame(rows)
    
    # סינון עמודות קיימות בלבד וסידורן
    ordered_cols = [c for c in col_order if c in df.columns]
    other_cols = [c for c in df.columns if c not in ordered_cols]
    df = df[ordered_cols + other_cols]
    
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_audit_v22(client, text):
    prompt = f"""Extract ALL tables into JSON. 
    
    TABLE E STOP RULE:
    1. Extract every individual deposit row. 
    2. THE LAST ROW MUST BE THE SUMMARY ROW (סה"כ). 
    3. IN THE SUMMARY ROW: The fields 'מועד' and 'חודש' MUST BE EMPTY. Place the word 'סה"כ' in the 'שם המעסיק' field.
    4. STOP extracting immediately after this summary row. Ignore all future adjustments.

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
        messages=[{"role": "system", "content": "You are a forensic auditor. Table E must end with a Total row with NO dates."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # חישוב שכר ב-Python (דיוק 100%)
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        # סכימת כל השורות פרט לאחרונה (שורת הסה"כ המובטחת)
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        rows_e[-1]["שכר"] = f"{salary_sum:,.0f}"
    
    return data

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 22.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מחלץ נתונים ומבצע אימות..."):
            file.seek(0)
            doc = fitz.open(stream=file.read(), filetype="pdf")
            full_text = "\n".join([page.get_text() for page in doc])
            
            data = process_audit_v22(client, full_text)
            
            if data:
                perform_cross_validation(data)
                
                # תצוגה: העמודה הראשונה ברשימה היא הימנית ביותר ב-Streamlit RTL
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", ["מסלול", "תשואה"])
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
                
                st.download_button("📥 הורד JSON", json.dumps(data, indent=2, ensure_ascii=False), "pension_audit.json")
