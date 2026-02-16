import streamlit as st
import google.generativeai as genai
import pypdf

st.set_page_config(
    page_title="בודק הפנסיה - pensya.info", 
    layout="centered",
    page_icon="🔍"
)

# אבטחה: משיכת המפתח
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
except Exception as e:
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא.")
    st.stop()

st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה דוח פנסיוני בפורמט PDF")

with st.expander("ℹ️ מה הסטנדרטים?"):
    st.write("""
    **דמי ניהול תקינים:**
    - 🏦 מהפקדה: עד 1.0%
    - 💰 על צבירה: עד 0.145% בשנה
    """)

file = st.file_uploader("📄 בחר קובץ PDF", type=['pdf'])

@st.cache_data
def extract_pdf_text(pdf_file):
    """חילוץ טקסט"""
    reader = pypdf.PdfReader(pdf_file)
    full_text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t: 
            full_text += t + "\n"
    return full_text

def analyze_with_gemini(text):
    """ניתוח - כפה שימוש ב-gemini-pro בלבד"""
    try:
        # ניסיון 1: gemini-pro פשוט
        model = genai.GenerativeModel('gemini-pro')
        st.info("🔄 משתמש במודל: gemini-pro")
    except:
        try:
            # ניסיון 2: עם prefix
            model = genai.GenerativeModel('models/gemini-pro')
            st.info("🔄 משתמש במודל: models/gemini-pro")
        except:
            st.error("❌ לא הצלחתי ליצור חיבור למודל")
            st.warning("""
            **בדוק:**
            1. המפתח ב-Secrets תקין
            2. ה-API מופעל: https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com
            3. נסה ליצור מפתח חדש
            """)
            return None
    
    prompt = f"""אתה מומחה לניתוח דוחות פנסיה ישראליים.

חלץ מהטקסט:
1. **דמי ניהול מהפקדה** (%)
2. **דמי ניהול על צבירה** (%)

השווה לסטנדרט:
- מהפקדה: מעל 1.0% = גבוה
- צבירה: מעל 0.145% = גבוה

פורמט תשובה:

### 📊 מה מצאתי:
- דמי הפקדה: X%
- דמי צבירה: Y%

### ⚖️ הערכה:
[גבוה/סביר/נמוך]

### 💡 המלצה:
[משפט אחד]

טקסט:
{text[:12000]}"""
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        st.error(f"שגיאה ביצירת תוכן: {e}")
        return None

if file:
    try:
        with st.spinner("🔄 מנתח..."):
            full_text = extract_pdf_text(file)
            
            if not full_text or len(full_text.strip()) < 50:
                st.error("❌ לא הצלחתי לקרוא טקסט מהקובץ")
                st.stop()
            
            analysis = analyze_with_gemini(full_text)
            
            if analysis:
                st.success("✅ הניתוח הושלם!")
                st.markdown(analysis)
                
                st.download_button(
                    label="📥 הורד תוצאות",
                    data=analysis,
                    file_name="pension_analysis.txt",
                    mime="text/plain"
                )
            
    except Exception as e:
        st.error(f"❌ שגיאה כללית: {e}")
        
        with st.expander("🔧 פרטים טכניים"):
            st.code(str(e))

st.markdown("---")
st.caption("🏦 pensya.info")
