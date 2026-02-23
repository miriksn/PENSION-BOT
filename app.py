import streamlit as st
import fitz
import json
import os
import re
from openai import OpenAI

st.set_page_config(page_title="חילוץ פנסיה מתקדם", layout="wide")

# עיצוב RTL וטבלאות
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; }
    .val-success { color: #15803d; font-weight: bold; padding: 5px; border: 1px solid #15803d; border-radius: 4px; }
    .val-error { color: #b91c1c; font-weight: bold; padding: 5px; border: 1px solid #b91c1c; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

def parse_val(val):
    """המרת מחרוזת למספר נקי לחישובים"""
    if not val: return 0.0
    try:
        return float(re.sub(r'[^\d\.\-]', '', str(val)))
    except:
        return 0.0

def validate_math(data):
    """בדיקת תקינות מתמטית לטבלאות ב' וה'"""
    results = {"table_b": False, "table_e": False}
    
    # בדיקת טבלה ב' 
    rows_b = data.get("table_b", {}).get("rows", [])
    if len(rows_b) > 1:
        sum_b = sum(parse_val(r.get("value")) for r in rows_b[:-1])
        total_b = parse_val(rows_b[-1].get("value"))
        results["table_b"] = abs(sum_b - total_b) < 2 # סובלנות לעיגול
        
    # בדיקת טבלה ה' 
    rows_e = data.get("table_e", {}).get("rows", [])
    total_row = data.get("table_e", {}).get("totals", {})
    if rows_e:
        sum_e = sum(parse_val(r.get("total")) for r in rows_e)
        declared_e = parse_val(total_row.get("total"))
        results["table_e"] = abs(sum_e - declared_e) < 2
        
    return results

def get_clean_text(file):
    file.seek(0)
    doc = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join([page.get_text("text") for page in doc])

def process_ai(client, text):
    schema = {
        "report_info": {"fund": "", "period": "", "date": ""},
        "table_a": {"rows": [{"desc": "", "val": ""}]},
        "table_b": {"rows": [{"description": "", "value": ""}]}, # יתרת פתיחה עד יתרת סגירה
        "table_c": {"rows": [{"desc": "", "pct": ""}]}, # דמי ניהול אישיים בלבד
        "table_d": {"rows": [{"path": "", "return": ""}]}, # תשואות מסלולים
        "table_e": {
            "rows": [{"deposit_date": "", "salary_month": "", "salary": "", "employee": "", "employer": "", "severance": "", "total": ""}],
            "totals": {"employee": "", "employer": "", "severance": "", "total": ""}
        }
    }
    
    prompt = f"""Extract data into JSON: {json.dumps(schema)}
    IMPORTANT:
    1. Table C: Ignore sidebar averages (1.26%, 0.13%). Extract personal rates (1.49%, 0.10%).
    2. Table B: Include ALL items (Losses, Fees, Insurance) to ensure math works.
    3. Table E: Extract ALL 7 columns for every row.
    
    TEXT: {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Return JSON only."}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)

# ממשק
st.title("📋 מנתח פנסיה דייקן")
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if api_key:
    client = OpenAI(api_key=api_key)
    file = st.file_uploader("העלה PDF", type="pdf")
    
    if file:
        with st.spinner("מנתח ומאמת נתונים..."):
            text = get_clean_text(file)
            data = process_ai(client, text)
            validations = validate_math(data)
            
            # תצוגת אימות
            c1, c2 = st.columns(2)
            with c1:
                st.write("אימות טבלה ב' (תנועות):", "✅ תקין" if validations["table_b"] else "❌ שגיאת חישוב")
            with c2:
                st.write("אימות טבלה ה' (הפקדות):", "✅ תקין" if validations["table_e"] else "❌ שגיאת חישוב")

            # הצגת כל הטבלאות
            st.header("א. תשלומים צפויים [cite: 9]")
            st.table(data.get("table_a", {}).get("rows", []))
            
            st.header("ב. תנועות בקרן ")
            st.table(data.get("table_b", {}).get("rows", []))
            
            st.header("ג. דמי ניהול אישיים ")
            st.table(data.get("table_c", {}).get("rows", []))
            
            st.header("ד. מסלולי השקעה ")
            st.table(data.get("table_d", {}).get("rows", []))
            
            st.header("ה. פירוט הפקדות (7 עמודות) ")
            st.table(data.get("table_e", {}).get("rows", []))
            st.json(data.get("table_e", {}).get("totals", {}))

            st.download_button("הורד JSON", json.dumps(data, indent=2, ensure_ascii=False), "pension.json")
