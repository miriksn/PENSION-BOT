import streamlit as st
import google.generativeai as genai
import pypdf

# הגדרת המפתח (וודא שהעתקת אותו במדויק)
API_KEY = "AIzaSyBrvKibfRFWjnmSm4LTFHtaqLEoZZVcrgU"
genai.configure(api_key=API_KEY)

st.set_page_config(page_title="בודק הפנסיה - pensya.info", layout="centered")
st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה דוח פנסיוני בפורמט PDF")

file = st.file_uploader("בחר קובץ PDF", type=['pdf'])

if file:
    st.info("מנתח נתונים, אנא המתן...")
    try:
        # 1. חילוץ טקסט מה-PDF (כדי לא לשלוח קובץ לגוגל ולמנוע שגיאות 404)
        reader = pypdf.PdfReader(file)
        full_text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t: full_text += t
        
        if len(full_text) < 50:
            st.error("הקובץ נראה סרוק כתמונה או ריק. נסה להעלות דוח דיגיטלי.")
        else:
            # 2. שימוש במודל היציב ביותר
            model = genai.GenerativeModel(model_name='gemini-1.5-flash')
            
            prompt = f"""
            נתח את דמי הניהול בטקסט הבא:
            - דמי ניהול מהפקדה (תקרה: 1%)
            - דמי ניהול מצבירה (תקרה: 0.145%)
            
            החזר תשובה בעברית: האם דמי הניהול גבוהים, סבירים או מעולים, ומהם האחוזים שמצאת?
            
            הטקסט:
            {full_text[:10000]}  # שולחים רק את ההתחלה כדי לא להעמיס
            """
            
            response = model.generate_content(prompt)
            st.success("תוצאת הבדיקה:")
            st.write(response.text)
            
    except Exception as e:
        st.error(f"שגיאה: {e}")
