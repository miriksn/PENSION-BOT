import streamlit as st
import google.generativeai as genai
import PIL.Image

# --- כאן שמים את המפתח שלך ---
API_KEY = "AIzaSyBrvKibfRFWjnmSm4LTFHtaqLEoZZVcrgU"

genai.configure(api_key=API_KEY)

st.set_page_config(page_title="בודק הפנסיה", layout="centered")
st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה צילום של טבלת דמי הניהול מהדוח")

file = st.file_uploader("בחר קובץ (PDF או תמונה)", type=['png', 'jpg', 'jpeg', 'pdf'])

if file:
    st.info("מנתח נתונים, אנא המתן...")
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        img = PIL.Image.open(file)
        
        prompt = """
        נתח את דמי הניהול בטבלה:
        1. הפקדה (מעל 1% זה גבוה)
        2. צבירה (מעל 0.145% זה גבוה)
        
        החזר תשובה בעברית:
        - אם שניהם גבוהים -> 'דמי הניהול גבוהים'
        - אם רק אחד גבוה -> 'דמי הניהול סבירים'
        - אם שניהם נמוכים/שווים -> 'דמי הניהול מעולים'
        ציין את המספרים שמצאת.
        """
        
        response = model.generate_content([prompt, img])
        st.success("הנה הניתוח המהיר:")
        st.write(response.text)
    except Exception as e:
        st.error(f"שגיאה: {e}")
