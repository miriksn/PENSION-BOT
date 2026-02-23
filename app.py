import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from openai import OpenAI

# הגדרות תצוגה RTL קשיחות
st.set_page_config(page_title="מנתח פנסיה - גרסה 20.0", layout="wide")

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
        # ניקוי יסודי של תווים שאינם מספרים, נקודה או מינוס
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except: return 0.0

def get_processed_text(file):
    """חילוץ טקסט עם חיתוך זהיר ב-'סה\"כ' הראשון של הפקדות"""
    file.seek(0)
    doc = fitz.open(stream=file.read(), filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    
    # חיפוש נקודת העצירה בטבלה ה'
    target_header = "ה. פירוט הפקדות"
    if target_header in full_text:
        parts = full_text.split(target_header)
        pre_content = parts[0]
        post_content = parts[1]
        
        # מחפשים את 'סה"כ' רק בתוך תוכן ההפקדות
        if 'סה"כ' in post_content:
            # חותכים מיד אחרי ה-'סה"כ' הראשון שמופיע שם
            match = re.search(r'סה"כ', post_content)
            cutoff = match.end() + 100 # לוקחים עוד קצת לביטחון
            return pre_content + target_header + post_content[:cutoff]
            
    return full_text

def perform_cross_validation(data):
    """אימות הצלבה מדויק בין טבלה ב' לטבלה ה'"""
    dep_b = 0.0
    # חיפוש סכום הפקדות בטבלה ב'
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "הפקדות"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 100]
            if nums: dep_b = nums[0]
            break
            
    # חיפוש סה"כ בטבלה ה'
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = 0.0
    if rows_e:
        # מחפשים את השורה שמכילה 'סה"כ' או פשוט את האחרונה
        last_row = rows_e[-1]
        dep_e = clean_num(last_row.get("סה\"כ", 0))
    
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.0f} ₪) תואם בין הטבלאות.</div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="val-error">⚠️ אימות נכשל: טבלה ב\' ({dep_b:,.0f} ₪) לעומת טבלה ה\' ({dep_e:,.0f} ₪).</div>', unsafe_allow_html=True)

def display_pension_table(rows, title, first_col_name):
    """הצגת טבלה עם יישור עמודות: תיאור בימין, ערך בשמאל"""
    if not rows: return
    df = pd.DataFrame(rows)
    
    # סידור עמודות: שם העמודה שצוין יהיה הימני ביותר
    if first_col_name in df.columns:
        cols = [first_col_name] + [c for c in df.columns if c != first_col_name]
        df = df[cols]
    
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_audit_v20(client, text):
    prompt = f"""Extract ALL tables into JSON.
    RULES:
    1. Table E: Extract EVERY row found. The last row must be 'סה"כ'.
    2. Table C: Include management fees and 'הוצאות ניהול השקעות'.
    3. Table D: Verbatim track name.
    
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
        messages=[{"role": "system", "content": "You are a precise financial auditor. Use Hebrew keys only."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # חישוב שכר ב-Python למניעת טעויות AI
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        # סכימת כל השורות פרט לאחרונה (שורת הסה"כ)
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        # עדכון שורת הסה"כ
        rows_e[-1]["שכר"] = f"{salary_sum:,.0f}"
    
    return data

# ממשק משתמש
st.title("📋 חילוץ נתונים פנסיוני - גרסה 20.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מחלץ ומאמת נתונים..."):
            clean_text = get_processed_text(file)
            data = process_audit_v20(client, clean_text)
            
            if data:
                perform_cross_validation(data)
                
                # תצוגה: תיאור בימין (עמודה ראשונה), מספרים משמאל
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", "תיאור")
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", "תיאור")
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", "תיאור")
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", "מסלול")
                
                # טבלה ה' - סדר עמודות מובנה
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", "מועד")
                
                st.download_button("📥 הורד JSON", json.dumps(data, indent=2, ensure_ascii=False), "pension_audit.json")
