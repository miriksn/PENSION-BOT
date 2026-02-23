import streamlit as st
import fitz
import json
import os
import pandas as pd
from openai import OpenAI

# הגדרות תצוגה
st.set_page_config(page_title="מנתח פנסיה - גרסת אפס פשרות", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; }
    .status-msg { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #f0fdf4; border: 1px solid #16a34a; }
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

def display_pension_table(rows, title):
    if not rows: return
    df = pd.DataFrame(rows)
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

def process_no_compromise(client, text):
    prompt = f"""You are a top-tier forensic auditor. Extract EVERY table and EVERY row from this pension report. 
    
    MANDATORY - DO NOT SKIP:
    1. TABLE A (Estimates): Extract all rows (e.g., 5,223, 10,888, etc.).
    2. TABLE B (Movements): Extract all rows (Opening, Deposits, Profits, Fees, Insurance, etc.).
    3. TABLE C (Fees): Extract ALL personal fees including investment expenses (הוצאות ניהול השקעות).
    4. TABLE D (Tracks): Extract the FULL track name and its return.
    
    STRICT RULES FOR TABLE E (DEPOSITS):
    - DO NOT AGGREGATE. Every single row from the PDF must be a separate entry in the JSON. Even small amounts (39, 34, 478, etc.) must appear exactly as shown.
    - FILTERING: If a section is titled 'פירוט הפקדות בגין שנת XXXX שהופקדו לאחר תום השנה' or similar, DO NOT include rows from that section. Extract ONLY deposits belonging to the reporting period.
    - TOTAL ROW: The last row must be 'סה"כ'. Calculate the sum of the 'שכר' column for this row based ONLY on the non-filtered rows.
    - Columns: מועד | חודש | שכר | עובד | מעסיק | פיצויים | סה"כ.

    JSON STRUCTURE:
    {{
      "report_info": {{"קרן": "", "עמית": ""}},
      "table_a": {{"rows": [{{"תיאור": "", "סכום": ""}}]}},
      "table_b": {{"rows": [{{"תיאור": "", "סכום": ""}}]}},
      "table_c": {{"rows": [{{"תיאור": "", "אחוז": ""}}]}},
      "table_d": {{"rows": [{{"מסלול": "", "תשואה": ""}}]}},
      "table_e": {{"rows": [{{ "מועד": "", "חודש": "", "שכר": "", "עובד": "", "מעסיק": "", "פיצויים": "", "סה\"כ": "" }}]}}
    }}
    REPORT TEXT:
    {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a meticulous data extraction engine. You extract every row exactly as printed. No summaries."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)

# ממשק
st.title("📋 חילוץ נתונים פנסיוני (גרסת אפס פשרות)")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מבצע חילוץ מלא של כל השורות..."):
            full_text = get_full_pdf_text(file)
            data = process_no_compromise(client, full_text)
            
            if data:
                st.markdown('<div class="status-msg">✅ הנתונים חולצו במלואם.</div>', unsafe_allow_html=True)
                
                # תצוגת טבלאות
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים")
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן")
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות")
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה")
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות")
                
                # כפתור הורדה
                st.markdown("---")
                st.download_button(
                    label="📥 הורד נתונים כקובץ JSON",
                    data=json.dumps(data, indent=2, ensure_ascii=False),
                    file_name="pension_audit_data.json",
                    mime="application/json"
                )
