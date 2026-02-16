import streamlit as st
import google.generativeai as genai
import pypdf

# --- הגדרת המפתח שלך ---
API_KEY = "AIzaSyBrvKibfRFWjnmSm4LTFHtaqLEoZZVcrgU"
genai.configure(api_key=API_KEY)

st.set_page_config(page_title="בודק הפנסיה - pensya.info", layout="centered")
st.title("🔍 בודק דמי ניהול אוטומטי")
st.write("העלה דוח שנתי או רבעוני (PDF)")

file = st.file_uploader("בחר קובץ PDF", type=['pdf'])

if file:
    st.info("מנתח נתונים, אנא המתן...")
    try:
        # 1. חילוץ טקסט מה-PDF אצלנו בשרת (לא אצל גוגל)
        reader = pypdf.PdfReader(file)
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text()
        
        # 2. בניית דגם הבינה המלאכותית
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # 3. שליחת הטקסט בלבד כהודעה רגילה
        prompt = f"""
        להלן טקסט מתוך דוח פנסיוני. 
        משימה: מצא את דמי הניהול מהפקדה (דמי ניהול מהתשלומים) ודמי הניהול מצבירה (דמי ניהול מהחיסכון).
        
        תנאי סף:
        - מעל 1% מהפקדה זה גבוה.
        - מעל 0.145% מצבירה זה גבוה.
        
        החזר תשובה בעברית ברורה: האם דמי הניהול גבוהים, סבירים או מעולים, ופרט את האחוזים שמצאת בטקסט.
        
        הטקסט לניתוח:
        {full_text}
        """
        
        # שים לב: כאן אנחנו שולחים רק טקסט (String), לא קבצים!
        response = model.generate_content(prompt)
        
        st.success("תוצאת הבדיקה:")
        st.write(response.text)
        
    except Exception as e:
        st.error(f"שגיאה בניתוח: {e}")
