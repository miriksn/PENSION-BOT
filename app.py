import streamlit as st
import pypdf
import io
import gc
import re
import json
import hashlib
import time
import math
from openai import OpenAI

# הגדרות עמוד ועיצוב RTL
st.set_page_config(page_title="בודק הפנסיה - pensya.info", layout="centered", page_icon="🔍")

st.markdown("""
<style>
    body, .stApp { direction: rtl; }
    .stRadio > div { direction: rtl; }
    .stRadio label { direction: rtl; text-align: right; }
    .stRadio > div > div { flex-direction: row-reverse; justify-content: flex-start; }
    .stMarkdown, .stText, p, h1, h2, h3, h4, div { text-align: right; }
    .stAlert { direction: rtl; text-align: right; }
</style>
""", unsafe_allow_html=True)

# ─── קבועים ───────────────────────────────────────────
PENSION_INTEREST = 0.0386  # 3.86%
MAX_TEXT_CHARS = 15_000

# ─── חיבור ל-API ─────────────────────────────────────
try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=API_KEY, default_headers={"OpenAI-No-Store": "true"})
except Exception:
    st.error("⚠️ מפתח ה-API לא נמצא בכספת (Secrets).")
    st.stop()

# ─── פונקציות תשתית ──────────────────────────────────

def is_vector_pdf(pdf_bytes):
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for i in range(min(len(reader.pages), 2)):
            text += reader.pages[i].extract_text() or ""
        return len(text.strip()) > 100
    except:
        return False

def validate_pension_type(text):
    """בדיקת סוג דוח לפי כותרת ומילות מפתח"""
    # ניקוי רווחים כפולים ובדיקת טקסט רגיל והפוך (RTL)
    search_text = text[:2000] + "\n" + "\n".join(line[::-1] for line in text[:2000].split("\n"))
    
    if 'כללית' in search_text:
        return False, "הרובוט מחווה דעה רק על דוחות מקוצרים של קרן פנסיה מקיפה (ולא פנסיה כללית)."
    if 'מפורט' in search_text:
        return False, "הרובוט מחווה דעה רק על דוחות מקוצרים (ולא מפורטים)."
    if 'בקרן הפנסיה החדשה' not in search_text and 'קרן הפנסיה' not in search_text:
        return False, "הרובוט מחווה דעה רק על דוחות מקוצרים של קרן פנסיה מקיפה."
    
    return True, ""

def anonymize_pii(text: str) -> str:
    text = re.sub(r"\b\d{7,9}\b", "[ID]", text)
    text = re.sub(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b", "[DATE]", text)
    return text

# ─── לוגיקת AI ───────────────────────────────────────

def build_prompt_messages(text):
    system_prompt = """אתה מחלץ נתונים מדוח פנסיה. החזר JSON בלבד עם השדות הבאים (מספרים בלבד):
    accumulation (יתרת הכספים בסוף התקופה - טבלה ב),
    expected_pension (קצבה חודשית צפויה בפרישה גיל 67),
    disability_release (שחרור מתשלום הפקדות - שורה תחתונה טבלה א),
    total_deposits (סה"כ הפקדות בגין התקופה - טבלה ה),
    total_salaries (סה"כ משכורות בגין התקופה - טבלה ה),
    disability_cost (עלות ביטוח נכות - טבלה ב, כמספר חיובי),
    survivor_cost (עלות ביטוח שארים - טבלה ב, כמספר חיובי),
    widow_pension (קצבה חודשית לאלמן/ה),
    disability_pension (קצבה חודשית בנכות מלאה),
    report_quarter (1, 2, 3 או 4 אם שנתי)."""
    
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"נתח את הטקסט הבא:\n\n{text[:MAX_TEXT_CHARS]}"}
    ]

# ─── חישובים וניתוח לוגי ──────────────────────────────

def perform_analysis(data, gender, family_status):
    # 1. אומדן גיל (NPER)
    try:
        pv = float(data.get('accumulation', 0))
        fv = float(data.get('expected_pension', 0)) * 190
        # nper = log(fv/pv) / log(1+r)
        years_to_retirement = math.log(fv / pv) / math.log(1 + PENSION_INTEREST)
        estimated_age = 67 - years_to_retirement
    except:
        return "⚠️ לא ניתן היה לחשב אומדן גיל באופן אמין מהנתונים בדוח."

    if estimated_age > 52:
        return "הרובוט עוד צעיר ועדיין לא למד לחוות דעה על דוחות של אנשים שיכולים לפרוש בתוך פחות מ-10 שנים. בעתיד הרובוט רוצה ללמוד לעזור גם להם."

    # 2. הכנסה מבוטחת
    try:
        disability_release = float(data.get('disability_release', 0))
        rep_deposit = disability_release / 0.94
        total_dep = float(data.get('total_deposits', 1))
        total_sal = float(data.get('total_salaries', 1))
        deposit_rate = total_dep / total_sal
        insured_salary = rep_deposit / deposit_rate
    except:
        insured_salary = 0

    lines = [f"### 📋 נתונים שחושבו:"]
    lines.append(f"- גיל משוער: **{estimated_age:.1f}**")
    lines.append(f"- שכר מבוטח מוערך: **₪{insured_salary:,.0f}**")
    lines.append("---")

    # 3. בדיקת פעילות
    disability_cost = abs(float(data.get('disability_cost', 0)))
    if disability_cost <= 0:
        return "🔴 **קרן הפנסיה איננה פעילה ואין לך דרכה כיסויים ביטוחיים!** ממליץ לשקול לנייד את הכספים לקרן הפנסיה הפעילה שלך."

    survivor_cost = abs(float(data.get('survivor_cost', 0)))
    quarter = data.get('report_quarter', 4)
    multiplier = {1: 4, 2: 2, 3: 1.333, 4: 1}.get(quarter, 1)
    annual_survivor_cost = survivor_cost * multiplier

    # לוגיקה לפי מצב משפחתי
    if family_status == "רווק":
        if survivor_cost == 0:
            lines.append("💡 מומלץ לפנות לקרן הפנסיה בכדי לקנות **'ברות ביטוח'** מה שיחסוך לך את הצורך עבור חיתום ותקופת אכשרה אם תרצה לרכוש ביטוח שארים בעתיד. העלות זניחה.")
        elif annual_survivor_cost > 13:
            savings = annual_survivor_cost * (1.0386 ** (67 - estimated_age))
            lines.append(f"1. כרווק סביר מאוד שהביטוח הזה מיותר עבורך (₪{annual_survivor_cost:,.0f} לשנה). ממליץ לשקול לבטל את ביטוח השארים.")
            lines.append(f"2. ביטול הביטוח למשך שנתיים צפוי לשפר את הצבירה שלך בערך ב-**₪{savings:,.0f}**.")
            lines.append("3. ביטול הביטוח תקף לשנתיים ויש לפנות לקרן על מנת לחדשו במידה והמצב המשפחתי לא השתנה.")
        else:
            lines.append("✅ מעולה, אתה לא מבזבז כסף על רכישת ביטוח שארים. זכור לחדש את הויתור אחת לשנתיים.")

    elif family_status in ["נשוי", "לא נשוי אך יש ילדים מתחת לגיל 21"]:
        if annual_survivor_cost < 13:
            lines.append("⚠️ **ייתכן שאתה בתקופת ויתור שארים.** עלות הביטוח נמוכה מאוד. מומלץ לעדכן בהקדם את הקרן שאינך רווק כדי שירכשו לך ביטוח שארים.")

    # 4. בדיקת כיסוי מקסימלי
    widow_p = float(data.get('widow_pension', 0))
    disability_p = float(data.get('disability_pension', 0))
    
    is_low = (widow_p < 0.59 * insured_salary) or (disability_p < 0.74 * insured_salary)
    if is_low:
        lines.append("\n<span style='color:red; font-weight:bold;'>🔴 הכיסוי הביטוחי בקרן הפנסיה איננו מקסימלי</span>")
        
        is_young_man = (gender == "גבר" and (67 - estimated_age) > 27)
        if gender == "אשה" or is_young_man:
            lines.append("💡 **מומלץ לשקול לשנות את מסלול הביטוח** כך שיקנה לך ולמשפחתך הגנה ביטוחית מקסימלית.")

    return "\n".join(lines)

# ─── ממשק משתמש ───────────────────────────────────────

st.title("🔍 בודק הפנסיה האוטומטי")

# השאלות (בשימוש בגרש בודד למניעת שגיאת Syntax)
q_gender = st.radio('1. מגדר:', ['גבר', 'אשה'], index=None, horizontal=True)
q_emp = st.radio('2. האם ההפקדות בדו"ח הן:', ['שכיר בלבד', 'עצמאי בלבד', 'שכיר + עצמאי'], index=None, horizontal=True)
q_status = st.radio('3. מצב משפחתי:', ['נשוי', 'רווק', 'לא נשוי אך יש ילדים מתחת לגיל 21'], index=None, horizontal=True)

if q_emp and q_emp != 'שכיר בלבד':
    st.warning("בשלב זה הבוט לא למד לחוות דעה על דוחות של מי שאינם רק שכירים.")
    st.stop()

if all([q_gender, q_emp, q_status]):
    st.markdown("---")
    file = st.file_uploader("📄 העלה דוח מקוצר (PDF מקורי בלבד)", type=["pdf"])
    
    if file:
        pdf_bytes = file.read()
        
        # שלב 1: בדיקת וקטוריות
        if not is_vector_pdf(pdf_bytes):
            st.error("הבוט לא יודע לקרוא קבצים שאינם הקבצים המקוריים מאתר קרן הפנסיה (PDF סרוק/צילום לא נתמך).")
            st.stop()
            
        # שלב 2: חילוץ ובדיקת סוג דוח
        full_text = pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
        is_pension, error_msg = validate_pension_type(full_text)
        
        if not is_pension:
            st.error(error_msg)
            st.stop()
            
        # שלב 3: ניתוח
        with st.spinner("🔄 מנתח נתונים..."):
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=build_prompt_messages(anonymize_pii(full_text)),
                    response_format={"type": "json_object"}
                )
                extracted = json.loads(response.choices[0].message.content)
                analysis_res = perform_analysis(extracted, q_gender, q_status)
                
                st.success("✅ הניתוח הושלם")
                st.markdown(analysis_res, unsafe_allow_html=True)
            except Exception as e:
                st.error("אירעה שגיאה בעיבוד ה-AI. נסה שוב מאוחר יותר.")
