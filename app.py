import streamlit as st
import pypdf
from openai import OpenAI

st.set_page_config(
    page_title="בודק הפנסיה - pensya.info", 
    layout="centered",
    page_icon="🔍"
)

# אבטחה: משיכת המפתח
try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=API_KEY)
except Exception as e:
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.info("הוסף את OPENAI_API_KEY ב-Streamlit Secrets")
    st.stop()

st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה דוח פנסיוני בפורמט PDF לניתוח מהיר")

with st.expander("ℹ️ מה הסטנדרטים?"):
    st.write("""
    **דמי ניהול תקינים:**
    - 🏦 מהפקדה: עד 1.0%
    - 💰 על צבירה: עד 0.145% בשנה
    
    דמי ניהול גבוהים יכולים לשחוק עשרות אלפי שקלים מהפנסיה לאורך שנים!
    """)

file = st.file_uploader("📄 בחר קובץ PDF", type=['pdf'])

@st.cache_data
def extract_pdf_text(pdf_file):
    """חילוץ טקסט מ-PDF"""
    reader = pypdf.PdfReader(pdf_file)
    full_text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t: 
            full_text += t + "\n"
    return full_text

def analyze_with_openai(text):
    """ניתוח עם OpenAI GPT-4"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # זול ומהיר - 0.15$ לכל מליון tokens
            messages=[
                {
                    "role": "system",
                    "content": """אתה מומחה לניתוח דוחות פנסיה ישראליים.
תפקידך לחלץ דמי ניהול ולהעריך אם הם גבוהים.

סטנדרטים:
- דמי ניהול מהפקדה: מעל 1.0% = גבוה
- דמי ניהול על צבירה: מעל 0.145% = גבוה"""
                },
                {
                    "role": "user",
                    "content": f"""נתח את הדוח הבא וחלץ:

1. **דמי ניהול מהפקדה** (באחוזים)
2. **דמי ניהול על צבירה** (באחוזים שנתיים)

פורמט התשובה:

### 📊 מה מצאתי:
- דמי ניהול מהפקדה: X%
- דמי ניהול על צבירה: Y%

### ⚖️ הערכה:
[האם הם גבוהים/סבירים/נמוכים ביחס לסטנדרט]

### 💡 המלצה קצרה:
[1-2 משפטים]

---

**טקסט הדוח:**
{text[:15000]}"""
                }
            ],
            temperature=0.3,  # יותר דטרמיניסטי
            max_tokens=1000
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        error_msg = str(e)
        if "insufficient_quota" in error_msg or "quota" in error_msg.lower():
            st.error("❌ חריגה מהמכסה או שהחשבון לא מופעל")
            st.info("""
            **פתרונות:**
            1. ודא שהוספת כרטיס אשראי: https://platform.openai.com/settings/organization/billing/overview
            2. בדוק שיש לך קרדיט: https://platform.openai.com/usage
            3. המתן מספר דקות ונסה שוב
            """)
        elif "invalid" in error_msg.lower():
            st.error("❌ מפתח API לא תקין")
            st.info("ודא שהעתקת את המפתח המלא מ-OpenAI")
        else:
            st.error(f"❌ שגיאה: {error_msg}")
        return None

if file:
    try:
        with st.spinner("🔄 מנתח דוח... אנא המתן"):
            # חילוץ טקסט
            full_text = extract_pdf_text(file)
            
            if not full_text or len(full_text.strip()) < 50:
                st.error("❌ לא הצלחתי לקרוא טקסט מהקובץ")
                st.warning("""
                **סיבות אפשריות:**
                - הקובץ מוצפן או מוגן
                - הקובץ הוא תמונה סרוקה (לא PDF טקסטואלי)
                - הקובץ פגום
                
                💡 נסה להמיר את הקובץ או להוריד מחדש
                """)
                st.stop()
            
            # הצגת מידע על אורך הטקסט
            st.info(f"📄 חולץ טקסט: {len(full_text)} תווים")
            
            # ניתוח עם OpenAI
            analysis = analyze_with_openai(full_text)
            
            if analysis:
                st.success("✅ הניתוח הושלם!")
                st.markdown(analysis)
                
                # כפתור להורדה
                st.download_button(
                    label="📥 הורד תוצאות",
                    data=analysis,
                    file_name="pension_analysis.txt",
                    mime="text/plain"
                )
                
                # הצגת עלות משוערת (אופציונלי)
                estimated_cost = (len(full_text) / 1000) * 0.00015  # GPT-4o-mini pricing
                st.caption(f"💰 עלות משוערת: ${estimated_cost:.4f}")
            
    except Exception as e:
        st.error(f"❌ אירעה שגיאה: {e}")
        
        with st.expander("🔧 פרטים טכניים"):
            st.code(str(e))

# כותרת תחתונה
st.markdown("---")
st.caption("🏦 פותח על ידי pensya.info | מופעל על ידי OpenAI GPT-4")
st.caption("זהו כלי עזר בלבד ואינו מהווה ייעוץ פנסיוני מקצועי")
