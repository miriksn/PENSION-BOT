import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
import io
from openai import OpenAI

# הגדרות RTL ועיצוב
st.set_page_config(page_title="מנתח פנסיה - גרסה 41.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold; background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
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

def to_numeric_col(series):
    """המרת עמודה לפורמט מספרי עבור אקסל"""
    return series.apply(clean_num)

def perform_cross_validation(data):
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "כספים שהופקדו"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 10]
            if nums: dep_b = nums[0]; break
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = clean_num(rows_e[-1].get("סה\"כ", 0)) if rows_e else 0.0
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.2f} ₪) תואם.</div>', unsafe_allow_html=True)

def process_audit_v41(client, text):
    prompt = f"""You are a MECHANICAL TRANSCRIBER. 
    RULES:
    1. ZERO ROUNDING: Copy decimals exactly (e.g., 0.17% stays 0.17%).
    2. TABLE D: Join multiline names. Map '%' values verbatim.
    3. TABLE E: 
       - For REGULAR rows: You MUST extract 'מועד' and 'חודש' exactly as written. Do not leave them empty.
       - For the SUMMARY row (סה"כ): Stop at the first 'סה"כ'. Clear 'מועד' and 'חודש' for this row ONLY. 
       - Ensure numbers are mapped in order: Employee, Employer, Severance, Total.
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
        messages=[{"role": "system", "content": "Mechanical OCR mode. Capture all dates/months in Table E rows. No rounding."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # תיקון שורת סיכום והמרות סופיות ב-Python
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        last_row = rows_e[-1]
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        
        # לוגיקת סידור מספרים בסיכום (עובד, מעסיק, פיצויים, סה"כ)
        vals = [last_row.get(k) for k in ["עובד", "מעסיק", "פיצויים", "סה\"כ"]]
        clean_vals = [v for v in vals if clean_num(v) > 0]
        if len(clean_vals) >= 3:
            last_row["עובד"] = clean_vals[0]
            last_row["מעסיק"] = clean_vals[1]
            if len(clean_vals) == 4:
                last_row["פיצויים"] = clean_vals[2]
                last_row["סה\"כ"] = clean_vals[3]
            else:
                last_row["סה\"כ"] = clean_vals[2]
        
        last_row["שכר"] = f"{salary_sum:,.0f}"
        last_row["מועד"], last_row["חודש"], last_row["שם המעסיק"] = "", "", "סה\"כ"
    
    return data

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 41.0")
client = init_client()

if client and (file := st.file_uploader("העלה דוח PDF", type="pdf")):
    with st.spinner("מעתיק נתונים וממיר למספרים..."):
        raw_text = "\n".join([page.get_text() for page in fitz.open(stream=file.read(), filetype="pdf")])
        data = process_audit_v41(client, raw_text)
        
        if data:
            perform_cross_validation(data)
            
            # הכנת DataFrames עם המרה למספרים בעמודות הרלוונטיות
            dfs = {}
            for k, cols, num_cols in [
                ("A", ["תיאור", "סכום בש\"ח"], ["סכום בש\"ח"]),
                ("B", ["תיאור", "סכום בש\"ח"], ["סכום בש\"ח"]),
                ("C", ["תיאור", "אחוז"], []),
                ("D", ["מסלול", "תשואה"], []),
                ("E", ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"], ["שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
            ]:
                rows = data.get(f"table_{k.lower()}", {}).get("rows", [])
                if rows:
                    df = pd.DataFrame(rows)[cols]
                    # המרה למספרים רק עבור האקסל
                    dfs[k] = df.copy()
                    for c in num_cols:
                        dfs[k][c] = to_numeric_col(dfs[k][c])
                    st.subheader(f"{k}. טבלה")
                    st.table(df) # תצוגה במסך נשארת טקסטואלית יפה

            # יצירת אקסל
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                sn = 'ריכוז נתונים'
                col_map = {"A": 1, "B": 4, "C": 7, "D": 10, "E": 13}
                for k, start_col in col_map.items():
                    if k in dfs:
                        dfs[k].to_excel(writer, sheet_name=sn, startcol=start_col, startrow=1, index=False)
                
                workbook, worksheet = writer.book, writer.sheets[sn]
                header_fmt = workbook.add_format({'bold': True, 'align': 'right'})
                titles = ["תשלומים", "תנועות", "דמי ניהול", "מסלולים", "הפקדות"]
                for (k, start_col), title in zip(col_map.items(), titles):
                    worksheet.write(0, start_col, title, header_fmt)
                worksheet.right_to_left()

            st.download_button("📥 הורד Excel (ערכים מספריים)", output.getvalue(), "pension_v41.xlsx")
