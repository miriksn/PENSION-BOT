import streamlit as st
import fitz
import json
import os
import pandas as pd
from openai import OpenAI

# הגדרות עמוד ועיצוב
st.set_page_config(page_title="חילוץ פנסיה - גרסת שורה 7", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; }
    .status-box { padding: 12px; border-radius: 8px; margin-bottom: 15px; font-weight: bold; border: 1px solid #e2e8f0; }
</style>
""", unsafe_allow_html=True)

def init_openai():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        st.error("❌ מפתח API חסר ב-Secrets.")
        return None
    return OpenAI(api_key=api_key)

def get_pdf_text(file):
    file.seek(0)
    doc = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join([page.get_text() for page in doc])

def display_pension_table(rows, title):
    """מציג טבלה עם מספור המתחיל מ-1 (כותרת=0)"""
    if not rows:
        st.warning(f"לא נמצאו נתונים עבור {title}")
        return
    
    df = pd.DataFrame(rows)
    if not df.empty:
        # הגדרת האינדקס שיתחיל ב-1
        df.index = range(1, len(df) + 1)
        st.subheader(title)
        st.table(df)

def validate_math_logic(data):
    """אימות ששורה 7 בטבלה ה' אכן שווה לסכום השורות מעליה"""
    logs = []
    
    # אימות טבלה ה'
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        # לוקחים את כל השורות פרט לאחרונה (סה"כ)
        data_rows = rows_e[:-1]
        total_row = rows_e[-1]
        
        try:
            calc_sum = sum(float(str(r.get("סה\"כ", 0)).replace(",", "")) for r in data_rows)
            rep_sum = float(str(total_row.get("סה\"כ", 0)).replace(",", ""))
            
            if abs(calc_sum - rep_sum) < 2:
                logs.append("✅ טבלה ה': שורה 7 (סה\"כ) תואמת במדויק לסיכום ההפקדות.")
            else:
                logs.append(f"⚠️ טבלה ה': סטייה בשורת הסיכום (צפוי: {rep_sum}, חושב: {calc_sum:.0f})")
        except:
            logs.append("⚠️ לא ניתן היה לבצע אימות מתמטי בגלל פורמט מספרים.")
            
    return logs

def process_with_ai(client, text):
    # הנחיה קשיחה לכלול את הסה"כ כשורה אחרונה ומפתחות בעברית
    prompt = f"""Extract ALL pension tables. 
    IMPORTANT RULES:
    1. TABLE E: Extract all 7 columns. The LAST ROW must be the total (סה"כ) row.
    2. USE HEBREW KEYS ONLY for all rows.
    3. TABLE C: Extract personal rates (1.49% and 0.10%) only.
    
    JSON STRUCTURE:
    {{
      "report_info": {{"קרן": "", "תקופה": ""}},
      "table_a": {{"rows": [{{"תיאור": "", "סכום": ""}}]}},
      "table_b": {{"rows": [{{"תיאור": "", "סכום": ""}}]}},
      "table_c": {{"rows": [{{"תיאור": "", "אחוז": ""}}]}},
      "table_d": {{"rows": [{{"מסלול": "", "תשואה": ""}}]}},
      "table_e": {{
          "rows": [
              {{ "מועד": "", "חודש": "", "שכר": "", "עובד": "", "מעסיק": "", "פיצויים": "", "סה\"כ": "" }}
          ]
      }}
    }}
    TEXT CONTENT: {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a precise financial extractor. Return JSON with Hebrew keys."},
                  {"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    return json.loads(res.choices[0].message.content)

# הממשק
st.title("📋 חילוץ דוח פנסיה - גרסה 7.0")
client = init_openai()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מבצע חילוץ ואימות..."):
            raw_text = get_pdf_text(file)
            data = process_with_ai(client, raw_text)
            
            # הצגת הודעות אימות
            for note in validate_math_logic(data):
                st.markdown(f'<div class="status-box">{note}</div>', unsafe_allow_html=True)
            
            # הצגת הטבלאות עם מספור (שורה 0 היא הכותרת)
            display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים")
            display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן")
            display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול (אישי)")
            display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה")
            display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות")
            
            st.download_button("הורד נתונים (JSON)", json.dumps(data, indent=2, ensure_ascii=False), "pension_data.json")
