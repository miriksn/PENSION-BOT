import streamlit as st
import google.generativeai as genai
import pypdf

# הגדרות עמוד
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
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.stop()

st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה דוח פנסיוני בפורמט PDF לניתוח מהיר")

with st.expander("ℹ️ מה הסטנדרטים?"):
    st.write("""
    **דמי ניהול תקינים:**
    - 🏦 מהפקדה: עד 1.0%
    - 💰 על צבירה: עד 0.145% בשנה
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

def get_available_model():
    """בוחר מודל זמין - מנסה כמה אופציות"""
    model_options = [
        'gemini-1.5-flash',
        'models/gemini-1.5-flash',
        'gemini-pro',
        'models/gemini-pro'
    ]
    
    for model_name in model_options:
        try:
            model = genai.GenerativeModel(model_name)
            st.success(f"✅ משתמש במודל: {model_name}")
            return model
        except Exception as e:
            continue
    
    # אם אף מודל לא עובד - נראה מה זמין
    st.error("❌ לא נמצא מודל זמין")
    try:
        available = [m.name for m in genai.list_models()]
        st.write("מודלים זמינים:", available)
    except:
        pass
    return None

def analyze_with_gemini(text):
    """ניתוח עם Gemini"""
    model = get_available_model()
    
    if not model:
        raise Exception("לא נמצא מודל Gemini זמין")
    
    prompt = f"""אתה מומחה לניתוח דוחות פנסיה ישראליים.

נתח את הדוח הבא וחלץ:
1. **דמי ניהול מהפקדה** (באחוזים)
2. **דמי ניהול על צבירה** (באחוזים שנתיים)

**סטנדרטים:**
- מהפקדה: מעל 1.0% = גבוה
- על צבירה: מעל 0.145% = גבוה

**פורמט תשובה:**

### 📊 התוצאות:
- דמי ניהול מהפקדה: X%
- דמי ניהול על צבירה: Y%

### ⚖️ הערכה:
[גבוה/סביר/נמוך]

### 💡 המלצה:
[1-2 משפטים]

---
**טקסט:**
{text[:15000]}"""
    
    response = model.generate_content(prompt)
    return response.text

if file:
    try:
        with st.spinner("🔄 מנתח דוח..."):
            # חילוץ טקסט
            full_text = extract_pdf_text(file)
            
            if not full_text or len(full_text.strip()) < 50:
                st.error("❌ לא הצלחתי לקרוא טקסט מהקובץ")
                st.warning("ייתכן שהקובץ מוצפן, סרוק או פגום")
                st.stop()
            
            # ניתוח
            analysis = analyze_with_gemini(full_text)
            
            st.success("✅ הניתוח הושלם!")
            st.markdown(analysis)
            
            st.download_button(
                label="📥 הורד תוצאות",
                data=analysis,
                file_name="pension_analysis.txt",
                mime="text/plain"
            )
            
    except Exception as e:
        error_msg = str(e)
        
        if "404" in error_msg:
            st.error("❌ שגיאת 404: המודל לא נמצא")
            st.info("""
            **פתרונות אפשריים:**
            1. עדכן את google-generativeai לגרסה 0.8.3 ומעלה
            2. בדוק שמפתח ה-API תקף
            3. נסה מודל אחר (gemini-pro במקום gemini-1.5-flash)
            """)
        elif "quota" in error_msg.lower() or "resource" in error_msg.lower():
            st.error("❌ חריגה מהמכסה היומית")
            st.info("נסה שוב מאוחר יותר או שדרג את חשבון ה-API")
        elif "api" in error_msg.lower():
            st.error(f"❌ שגיאת API: {error_msg}")
        else:
            st.error(f"❌ שגיאה: {error_msg}")
        
        with st.expander("🔧 פרטים טכניים"):
            st.code(error_msg)

st.markdown("---")
st.caption("🏦 pensya.info | כלי עזר בלבד, לא ייעוץ פנסיוני")
