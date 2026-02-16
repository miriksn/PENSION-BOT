import streamlit as st
import google.generativeai as genai

# --- הגדרת המפתח שלך ---
# וודא שהמפתח שלך נשאר בתוך המרכאות
API_KEY = "AIzaSyBrvKibfRFWjnmSm4LTFHtaqLEoZZVcrgU"

genai.configure(api_key=API_KEY)

st.set_page_config(page_title="בודק הפנסיה - pensya.info", layout="centered")
st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה צילום מסך או קובץ PDF של טבלת דמי הניהול מהדוח השנתי")

file = st.file_uploader("בחר קובץ (PDF או תמונה)", type=['png', 'jpg', 'jpeg', 'pdf'])

if file:
    st.info("מנתח נתונים, אנא המתן...")
    try:
        # הגדרת המודל
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # קריאת תוכן הקובץ
        doc_data = file.read()
        
        # בניית הבקשה לבינה המלאכותית
        prompt = """
        נתח את דמי הניהול בטבלה שבמסמך המצורף:
        1. דמי ניהול מהפקדה (הרף הוא 1%).
        2. דמי ניהול מצבירה (הרף הוא 0.145%).
        
        החזר תשובה בעברית ברורה:
        - אם שניהם מעל הרף: 'דמי הניהול גבוהים'.
        - אם רק אחד מעל הרף: 'דמי הניהול סבירים'.
        - אם שניהם מתחת או שווים לרף: 'דמי הניהול מעולים'.
        
        ציין בקצרה את האחוזים שמצאת במסמך.
        """
        
        # שליחה ל-Gemini
        response = model.generate_content([
            prompt,
            {"mime_type": file.type, "data": doc_data}
        ])
        
        st.success("הנה הניתוח המהיר:")
        st.write(response.text)
        
    except Exception as e:
        st.error(f"אירעה שגיאה בניתוח הקובץ: {e}")
