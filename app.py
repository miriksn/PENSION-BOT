import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
import io
from openai import OpenAI

# הגדרות RTL ועיצוב
st.set_page_config(page_title="מנתח פנסיה - גרסה 44.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
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

def process_audit_v44(client, text):
    prompt = f"""You are a FORENSIC MECHANICAL SCRIBE. 
    STRICT RULES:
    1. NO DIGIT FLIPPING: If text says '50', it is '50', never '05'. 
    2. TABLE D RETURN: Look for the '%' sign specifically next to the track name. In 'Clal', if it says 11.25%, capture 11.25%.
    3. TABLE E: Extract EVERY row. Capture 'מועד' and 'חודש' for each. 
    4. ALL TABLES: You MUST return data for tables A, B, C, D, and E.
    
    TEXT: {text}"""
    
    res = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "You are a data entry clerk. Copy numbers exactly. Do not flip digits in RTL text. Calculate nothing, just copy."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # חישוב שורת סיכום מתמטית מדויקת ב-Python
    rows_e = data.get("table_e", {}).get("rows", [])
    if rows_e:
        sum_row = {
            "שם המעסיק": "סה\"כ (מחושב)",
            "שכר": sum(clean_num(r.get("שכר")) for r in rows_e),
            "עובד": sum(clean_num(r.get("עובד")) for r in rows_e),
            "מעסיק": sum(clean_num(r.get("מעסיק")) for r in rows_e),
            "פיצויים": sum(clean_num(r.get("פיצויים")) for r in rows_e),
            "סה\"כ": sum(clean_num(r.get("סה\"כ")) for r in rows_e)
        }
        rows_e.append(sum_row)
    return data

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 44.0 (תיקון תשואות וטבלאות)")
client = init_client()

if client and (file := st.file_uploader("העלה דוח PDF", type="pdf")):
    with st.spinner("מחלץ נתונים..."):
        raw_text = "\n".join([page.get_text() for page in fitz.open(stream=file.read(), filetype="pdf")])
        data = process_audit_v44(client, raw_text)
        
        if data:
            dfs_for_excel = {}
            # הגדרות עמודות לכל טבלה
            config = {
                "A": ["תיאור", "סכום בש\"ח"],
                "B": ["תיאור", "סכום בש\"ח"],
                "C": ["תיאור", "אחוז"],
                "D": ["מסלול", "תשואה"],
                "E": ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"]
            }
            
            for k, cols in config.items():
                rows = data.get(f"table_{k.lower()}", {}).get("rows", [])
                if rows:
                    df = pd.DataFrame(rows)
                    # וודא שכל העמודות קיימות
                    for c in cols: 
                        if c not in df.columns: df[c] = ""
                    df = df[cols]
                    st.subheader(f"טבלה {k}")
                    st.table(df)
                    
                    # המרה למספרים לאקסל (רק עמודות כספיות)
                    excel_df = df.copy()
                    num_cols = ["סכום בש\"ח", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"]
                    for c in num_cols:
                        if c in excel_df.columns:
                            excel_df[c] = excel_df[c].apply(clean_num)
                    dfs_for_excel[k] = excel_df

            # יצירת אקסל
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                sn = 'ריכוז נתונים'
                col_map = {"A": 1, "B": 4, "C": 7, "D": 10, "E": 13}
                for k, start_col in col_map.items():
                    if k in dfs_for_excel:
                        dfs_for_excel[k].to_excel(writer, sheet_name=sn, startcol=start_col, startrow=1, index=False)
                
                workbook, worksheet = writer.book, writer.sheets[sn]
                header_fmt = workbook.add_format({'bold': True, 'align': 'right', 'bg_color': '#D3D3D3'})
                for (k, start_col), title in zip(col_map.items(), ["תשלומים", "תנועות", "דמי ניהול", "מסלולים", "הפקדות"]):
                    worksheet.write(0, start_col, title, header_fmt)
                worksheet.right_to_left()

            st.download_button("📥 הורד Excel מתוקן (מספרים)", output.getvalue(), "pension_v44.xlsx")
