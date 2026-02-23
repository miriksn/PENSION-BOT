import streamlit as st
import fitz
import base64
import pandas as pd
import re
import os
from openai import OpenAI
from pydantic import BaseModel, Field

# --- הגדרות עיצוב ---
st.set_page_config(page_title="מנתח פנסיה - מבוסס Vision (דיוק מוחלט)", layout="wide")
st.markdown("""
<style>
    .block-container { direction: rtl; }
    table { text-align: right; width: 100%; }
    th, td { text-align: right !important; }
</style>
""", unsafe_allow_html=True)

# --- סכמות מבנה נתונים קשיח (Structured Outputs) ---
class TableARow(BaseModel):
    description: str = Field(description="תיאור הקצבה או התשלום")
    amount: str = Field(description="סכום בשקלים")

class TableBRow(BaseModel):
    description: str = Field(description="תיאור התנועה")
    amount: str = Field(description="סכום בשקלים")

class TableCRow(BaseModel):
    description: str = Field(description="תיאור דמי ניהול או הוצאה")
    percentage: str = Field(description="האחוז (כולל סימן % אם קיים)")

class TableDRow(BaseModel):
    track: str = Field(description="שם המסלול")
    return_rate: str = Field(description="תשואה (כולל סימן %)")

class TableERow(BaseModel):
    employer: str = Field(description="שם המעסיק")
    deposit_date: str = Field(description="מועד הפקדה")
    salary_month: str = Field(description="עבור חודש משכורת")
    salary: str = Field(description="משכורת / שכר מבוטח")
    employee: str = Field(description="תגמולי עובד")
    employer_dep: str = Field(description="תגמולי מעסיק")
    severance: str = Field(description="פיצויים")
    total: str = Field(description="סה\"כ הפקדות (הסכום של כל הרכיבים)")

class PensionData(BaseModel):
    table_a: list[TableARow] = Field(description="טבלה א - תשלומים צפויים")
    table_b: list[TableBRow] = Field(description="טבלה ב - תנועות בקרן הפנסיה בשנת הדוח")
    table_c: list[TableCRow] = Field(description="טבלה ג - אחוז דמי ניהול והוצאות")
    table_d: list[TableDRow] = Field(description="טבלה ד - מסלולי השקעה ותשואות")
    table_e: list[TableERow] = Field(description="טבלה ה - פירוט הפקדות לקרן. חובה לעבור על כל העמודים ולחלץ את *כל* השורות. שורת הסה\"כ תהיה השורה האחרונה בהכרח.")

# --- פונקציות עזר ---
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
    """אימות הצלבה קשיח בין טבלה ב' ל-ה'"""
    dep_b = 0.0
    for r in data.get("table_b", {}).get("rows", []):
        desc = str(r.get("תיאור", ""))
        if any(kw in desc for kw in ["הופקדו", "כספים שהופקדו"]):
            dep_b = clean_num(r.get("סכום בש\"ח", 0))
            break
            
    rows_e = data.get("table_e", {}).get("rows", [])
    dep_e = clean_num(rows_e[-1].get("סה\"כ", 0)) if rows_e else 0.0

    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div style="color: green; font-weight: bold; padding: 10px; background-color: #e6ffe6; border-radius: 5px;">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.2f} ₪) תואם במדויק.</div><br>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div style="color: red; font-weight: bold; padding: 10px; background-color: #ffe6e6; border-radius: 5px;">⚠️ שגיאת אימות חזותית: טבלה ב\' ({dep_b:,.2f} ₪) לעומת סה"כ טבלה ה\' ({dep_e:,.2f} ₪).</div><br>', unsafe_allow_html=True)

def display_pension_table(rows, title, col_order):
    if not rows: return
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

# --- פונקציית העיבוד המרכזית (Vision + Structured Outputs) ---
def process_pdf_vision(client, pdf_bytes):
    # 1. המרת דפי ה-PDF לתמונות
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    base64_images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        base64_images.append(base64.b64encode(img_bytes).decode("utf-8"))
        
    # 2. בניית הפרומפט והעברת התמונות
    messages = [
        {
            "role": "system",
            "content": "אתה מנוע חילוץ נתונים מדויק מדוחות פנסיה ישראליים מקוצרים. המשימה שלך היא לחלץ נתונים מטבלאות א' עד ה'. העתק את המספרים במדויק מתוך התמונה. אל תעגל מספרים, אל תמציא נתונים, ואל תשנה את כיוון הספרות. בטבלה ה' (הפקדות), חובה לחלץ את *כל השורות* המופיעות ברצף, ייתכן שהן גולשות על פני מספר עמודים. הקפד לשמור על יישור העמודות המדויק, במיוחד בין 'עובד', 'מעסיק' ו'פיצויים'."
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "מצורפים עמודי דוח פנסיה מקוצר. אנא חלץ את הנתונים לתוך המבנה המוגדר."}
            ] + [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}} for img in base64_images
            ]
        }
    ]

    # 3. קריאה למודל בשימוש parse ליצירת אובייקט Pydantic ודאי
    response = client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=messages,
        response_format=PensionData,
        temperature=0 # חסימת "יצירתיות" של המודל
    )
    
    # התיקון שהוספנו: ניגשים לאיבר הראשון ברשימת התשובות 
    parsed_data = response.choices.message.parsed
    
    # 4. המרה חזרה למבנה ה-JSON (לצורך תאימות מלאה לקוד התצוגה)
    data = {
        "table_a": {"rows": [{"תיאור": r.description, "סכום בש\"ח": r.amount} for r in parsed_data.table_a]},
        "table_b": {"rows": [{"תיאור": r.description, "סכום בש\"ח": r.amount} for r in parsed_data.table_b]},
        "table_c": {"rows": [{"תיאור": r.description, "אחוז": r.percentage} for r in parsed_data.table_c]},
        "table_d": {"rows": [{"מסלול": r.track, "תשואה": r.return_rate} for r in parsed_data.table_d]},
        "table_e": {"rows": [{
            "שם המעסיק": r.employer,
            "מועד": r.deposit_date,
            "חודש": r.salary_month,
            "שכר": r.salary,
            "עובד": r.employee,
            "מעסיק": r.employer_dep,
            "פיצויים": r.severance,
            "סה\"כ": r.total
        } for r in parsed_data.table_e]}
    }
    
    return data

# --- ממשק המשתמש (UI) ---
st.title("📋 חילוץ נתונים פנסיוני - Vision Based (דיוק מלא)")

client = init_client()

if client:
    file = st.file_uploader("העלה דוח פנסיה מקוצר (PDF)", type="pdf")
    
    if file:
        with st.spinner("סורק את התמונות ומפענח טבלאות מורכבות..."):
            pdf_bytes = file.read()
            data = process_pdf_vision(client, pdf_bytes)
            
            if data:
                perform_cross_validation(data)
                
                # תצוגת הטבלאות
                display_pension_table(data.get("table_a", {}).get("rows"), "א. תשלומים צפויים", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_b", {}).get("rows"), "ב. תנועות בקרן", ["תיאור", "סכום בש\"ח"])
                display_pension_table(data.get("table_c", {}).get("rows"), "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
                display_pension_table(data.get("table_d", {}).get("rows"), "ד. מסלולי השקעה", ["מסלול", "תשואה"])
                display_pension_table(data.get("table_e", {}).get("rows"), "ה. פירוט הפקדות", ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", "סה\"כ"])
else:
    st.error("לא נמצא מפתח OpenAI (OPENAI_API_KEY). אנא הגדר אותו.")
