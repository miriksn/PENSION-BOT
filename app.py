import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
import io
from openai import OpenAI

# הגדרות RTL ועיצוב קשיח למניעת "יוזמות" AI
st.set_page_config(page_title="מנתח פנסיה - גרסה 38.0", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
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

def perform_cross_validation(data):
    """אימות הצלבה בין טבלה ב' ל-ה'"""
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        row_str = " ".join(str(v) for v in r.values())
        if any(kw in row_str for kw in ["הופקדו", "כספים שהופקדו"]):
            nums = [clean_num(v) for v in r.values() if clean_num(v) > 10]
            if nums: dep_b = nums[0]; break
            
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = clean_num(rows_e[-1].get("סה\"כ", 0)) if rows_e else 0.0
    
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.2f} ₪) תואם במדויק.</div>', unsafe_allow_html=True)

def get_styled_df(rows, col_order):
    if not rows: return None
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    return df[existing]

def process_audit_v38(client, text):
    # פרומפט "מעתיק מכני" (גירסה 29) למניעת עיגול ופרשנות
    prompt = f"""You are a MECHANICAL SCRIBE. Your ONLY job is to transcribe text to JSON with ZERO intelligence applied.
    
    CRITICAL RULES:
    1. ZERO ROUNDING: Copy decimals exactly (e.g., 0.17% stays 0.17%). Do NOT flip digits (67 stays 67).
    2. TABLE D (CLAL SPECIAL): Join multiline track names. Find the EXACT '%' value nearby.
    3. TABLE E SUMMARY: Stop at the first 'סה"כ' row. Map sums digit-by-digit. Clear 'מועד' and 'חודש'.
    
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
        messages=[{"role": "system", "content": "OCR mode. Copy verbatim. No logic. No rounding."},
                  {"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    data = json.loads(res.choices[0].message.content)
    
    # תיקון שורת סיכום ב-Python למניעת הסטת עמודות
    rows_e = data.get("table_e", {}).get("rows", [])
    if len(rows_e) > 1:
        last_row = rows_e[-1]
        salary_sum = sum(clean_num(r.get("שכר", 0)) for r in rows_e[:-1])
        vals = [last_row.get(k) for k in ["עובד", "מעסיק", "פיצויים", "סה\"כ"]]
        c_vals = [clean_num(v) for v in vals]
        max_v = max(c_vals)
        
        if max_v > 0 and clean_num(last_row.get("סה\"כ")) != max_v:
            non_zero = [v for v in vals if clean_num(v) > 0]
            if len(non_zero) >= 3:
                last_row["סה\"כ"] = non_zero[-1]
                last_row["מעסיק"] = non_zero[-2]
                last_row["עובד"] = non_zero[-3]
        
        last_row["שכר"] = f"{salary_sum:,.0f}"
        last_row["מועד"], last_row["חודש"], last_row["שם המעסיק"] = "", "", "סה\"כ"
    return data

# ממשק
st.title("📋 חילוץ נתונים פנסיוני - גרסה 38.0")
client = init_client()

if client:
    file = st.file_uploader("העלה דוח PDF", type="pdf")
    if file:
        with st.spinner("מעתיק נתונים במדויק (ללא עיגול)..."):
            raw_text = "\n".join([page.get_text() for page in fitz.open(stream=file.read(), filetype="pdf")])
            data = process_audit_v38(client, raw_text)
            
            if data:
                perform_cross_validation(data)
                
                # הכנת DataFrames
                dfs = {
                    "A": get_styled_df(data.get("table_a", {}).get("rows"), ["תיאור", "סכום בש\"ח"]),
                    "B": get_styled_df(data.get("table_b", {}).get("rows"), ["תיאור", "סכום בש\"ח"]),
                    "C": get_styled_df(data.get("table_c", {}).get("rows"), ["תיאור", "אחוז"]),
                    "D": get_styled_df(data.get("table_d", {}).get("rows"), ["מסלול", "תשואה"]),
                    "E": get_styled_df(data.get("table_e", {}).get("rows"), ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
                }
                
                for k, title in zip(["A", "B", "C", "D", "E"], ["א. תשלומים צפויים", "ב. תנועות בקרן", "ג. דמי ניהול והוצאות", "ד. מסלולי השקעה", "ה. פירוט הפקדות"]):
                    st.subheader(title)
                    st.table(dfs[k])
                
                # יצירת קובץ אקסל מאוחד - תיקון מלא של כל השגיאות
                output = io.BytesIO()
                try:
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        sheet_name = 'ריכוז נתונים'
                        # כתיבת טבלאות לפי עמודות B, E, H, K, N
                        if dfs["A"] is not None: dfs["A"].to_excel(writer, sheet_name=sheet_name, startcol=1, startrow=1, index=False)
                        if dfs["B"] is not None: dfs["B"].to_excel(writer, sheet_name=sheet_name, startcol=4, startrow=1, index=False)
                        if dfs["C"] is not None: dfs["C"].to_excel(writer, sheet_name=sheet_name, startcol=7, startrow=1, index=False)
                        if dfs["D"] is not None: dfs["D"].to_excel(writer, sheet_name=sheet_name, startcol=10, startrow=1, index=False)
                        if dfs["E"] is not None: dfs["E"].to_excel(writer, sheet_name=sheet_name, startcol=13, startrow=1, index=False)
                        
                        workbook = writer.book
                        worksheet = writer.sheets[sheet_name]
                        fmt = workbook.add_format({'bold': True, 'align': 'right'})
                        
                        # הוספת כותרות ידניות
                        worksheet.write(0, 1, "טבלה א - תשלומים צפויים", fmt)
                        worksheet.write(0, 4, "טבלה ב - תנועות בקרן", fmt)
                        worksheet.write(0, 7, "טבלה ג - דמי ניהול", fmt)
                        worksheet.write(0, 10, "טבלה ד - מסלולי השקעה", fmt)
                        worksheet.write(0, 13, "טבלה ה - פירוט הפקדות", fmt)
                        
                        worksheet.set_right_to_left()

                    st.markdown("---")
                    st.download_button(
                        label="📥 הורד קובץ Excel מאוחד",
                        data=output.getvalue(),
                        file_name="pension_report_unified.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                except Exception as e:
                    st.error(f"שגיאה ביצירת קובץ Excel: {e}")
