import streamlit as st
import fitz  # PyMuPDF
import json
import os
from openai import OpenAI

# הגדרות תצוגה
st.set_page_config(page_title="חילוץ נתוני פנסיה", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Assistant', sans-serif;
        direction: rtl;
        text-align: right;
    }
    .stTable { direction: rtl !important; }
    .report-card { background-color: #f8fafc; border-right: 5px solid #1e40af; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
</style>
""", unsafe_allow_html=True)

def init_openai():
    api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        st.error("❌ מפתח API חסר.")
        return None
    return OpenAI(api_key=api_key)

def get_pdf_text(uploaded_file):
    """קריאה בטוחה של ה-PDF עם איפוס המצביע"""
    try:
        uploaded_file.seek(0) # חובה כדי למנוע קריאת קובץ ריק
        file_bytes = uploaded_file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text("text") + "\n"
        doc.close()
        return text
    except Exception as e:
        st.error(f"שגיאה בחילוץ טקסט: {e}")
        return None

def process_pension_ai(client, raw_text):
    """עיבוד עם סכמת JSON כפויה"""
    # הגדרת המבנה המדויק שהקוד מצפה לו
    schema = {
        "report_info": {"fund_name": "", "report_period": "", "report_date": ""},
        "table_a": {"rows": [{"description": "", "value": ""}]},
        "table_b": {"rows": [{"description": "", "value": ""}]},
        "table_e": {"rows": [{"deposit_date": "", "salary_month": "", "total": ""}]}
    }
    
    prompt = f"""Extract pension data into THIS EXACT JSON STRUCTURE: {json.dumps(schema)}
    
    Rules:
    1. Table B must include losses (הפסדים) with a minus sign.
    2. Table E must include all deposit rows.
    3. If a value is missing, use an empty string.
    
    TEXT:
    {raw_text}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a financial data extractor. Return ONLY valid JSON."},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        st.error(f"שגיאה בעיבוד AI: {e}")
        return None

# ממשק
st.title("📋 חילוץ נתונים מדוח פנסיה")
client = init_openai()

if client:
    file = st.file_uploader("העלה דוח PDF", type=["pdf"])
    if file:
        with st.spinner("מנתח..."):
            raw_text = get_pdf_text(file)
            if raw_text and len(raw_text.strip()) > 10:
                data = process_pension_ai(client, raw_text)
                
                if data:
                    info = data.get("report_info", {})
                    st.markdown(f"""<div class="report-card">
                        <h3>{info.get('fund_name', 'דוח פנסיה')}</h3>
                        <p>תקופה: {info.get('report_period', '—')} | תאריך: {info.get('report_date', '—')}</p>
                    </div>""", unsafe_allow_html=True)
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        st.subheader("תשלומים צפויים (א')")
                        rows_a = data.get("table_a", {}).get("rows", [])
                        if rows_a: st.table(rows_a)
                        else: st.info("לא נמצאו נתונים לטבלה א'")
                        
                    with c2:
                        st.subheader("תנועות בקרן (ב')")
                        rows_b = data.get("table_b", {}).get("rows", [])
                        if rows_b: st.table(rows_b)
                        else: st.info("לא נמצאו נתונים לטבלה ב'")
                        
                    st.subheader("פירוט הפקדות (ה')")
                    rows_e = data.get("table_e", {}).get("rows", [])
                    if rows_e: st.table(rows_e)
                    else: st.info("לא נמצאו נתונים לטבלה ה'")
                else:
                    st.error("ה-AI לא הצליח לייצר מבנה נתונים תקין.")
            else:
                st.error("לא חולץ טקסט מהקובץ. ייתכן שמדובר בסריקה (Image-based PDF) ולא בדוח דיגיטלי.")
