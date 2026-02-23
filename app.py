import streamlit as st
import fitz
import json
import os
import pandas as pd
from openai import OpenAI

# הגדרות תצוגה
st.set_page_config(page_title="מנתח פנסיה - סינון הפקדות מאוחרות", layout="wide")

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

def process_with_filtering(client, text):
    prompt = f"""Extract ALL tables from the pension report.
    
    STRICT FILTERING RULE FOR TABLE E:
    - If the report contains a section titled "פירוט הפקדות בגין שנת XXXX שהופקדו לאחר תום השנה" (Deposits after year end) or similar headers for future-dated deposits, DO NOT extract or include these rows.
    - Ignore those rows completely as they are irrelevant to the user.
    - Extract ONLY the main deposit rows belonging to the actual reporting year.
    
    CRITICAL FOR TABLE E STRUCTURE:
    - The table may span multiple pages. Extract every relevant row.
    - The LAST row in your JSON must be the "סה"כ" (Total) row. 
    - You MUST calculate and include the sum of the 'שכר' (Salary) column for this last row.
    - Ensure 'עובד' (Employee) totals are in the 'עובד' column, not 'שכר'.

    JSON STRUCTURE:
    {{
      "report_info": {{"קרן": "", "עמית": ""}},
      "table_a": {{"rows": []}},
      "table_b": {{"rows": []}},
      "table_c": {{"rows": []}},
      "table_d": {{"rows": []}},
      "table_e": {{"rows": [
          {{ "מועד": "", "חודש": "", "שכר": "", "עובד": "", "מעסיק": "", "פיצויים": "", "סה\"כ": "" }}
      ]}}
    }}
    REPORT TEXT:
    {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a precise financial auditor. Ignore deposits made after the year end."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)

# ממשק
st.title("📋 חילוץ נתונים פנסיוני (עם סינון הפקדות מאוחרות)")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מנתח ומסנן נתונים..."):
            full_text = get_full_pdf_text(file)
            data = process_with_filtering(client, full_text)
            
            if data:
                st.markdown('<div class="status-msg">✅ הנתונים חולצו. הפקדות לאחר תום השנה סוננו החוצה.</div>', unsafe_allow_html=True)
                
                # תצוגת טבלאות
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים")
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן")
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות")
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה")
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות")
                
                # כפתור הורדה
                st.markdown("---")
                st.download_button(
                    label="📥 הורד נתונים מסוננים (JSON)",
                    data=json.dumps(data, indent=2, ensure_ascii=False),
                    file_name="pension_filtered_data.json",
                    mime="application/json"
                )
