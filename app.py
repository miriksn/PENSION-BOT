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

st.set_page_config(
    page_title="בודק הפנסיה - pensya.info",
    layout="centered",
    page_icon="🔍"
)

# עיצוב RTL
st.markdown("""
<style>
    body, .stApp { direction: rtl; }
    .stRadio > div { direction: rtl; }
    .stRadio label { direction: rtl; text-align: right; }
    .stRadio > div > div { flex-direction: row-reverse; justify-content: flex-start; }
    .stMarkdown, .stText, p, h1, h2, h3, h4, div { text-align: right; }
    .stAlert { direction: rtl; text-align: right; }
    .stFileUploader { direction: rtl; }
    .stDownloadButton { direction: rtl; }
    .stExpander { direction: rtl; }
    .stInfo, .stWarning, .stError, .stSuccess { direction: rtl; text-align: right; }
</style>
""", unsafe_allow_html=True)

MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_CHARS = 15_000
MAX_PAGES = 3
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW_SEC = 3600
PENSION_FACTOR = 190
RETURN_RATE = 0.0386
DISABILITY_RELEASE_FACTOR = 0.94

try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=API_KEY, default_headers={"OpenAI-No-Store": "true"})
except Exception:
    st.error("שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.stop()

def _get_client_id() -> str:
    headers = st.context.headers if hasattr(st, "context") else {}
    raw_ip = headers.get("X-Forwarded-For", "") or headers.get("X-Real-Ip", "") or "unknown"
    ip = raw_ip.split(",")[0].strip()
    return hashlib.sha256(ip.encode()).hexdigest()[:16]

def _check_rate_limit() -> tuple[bool, str]:
    cid = _get_client_id()
    now = time.time()
    key = f"rl_{cid}"
    if key not in st.session_state:
        st.session_state[key] = []
    st.session_state[key] = [t for t in st.session_state[key] if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(st.session_state[key]) >= RATE_LIMIT_MAX:
        remaining = int(RATE_LIMIT_WINDOW_SEC - (now - st.session_state[key][0]))
        return False, f"הגעת למגבלת הניתוחים. נסה שוב בעוד {remaining // 60} דקות."
    st.session_state[key].append(now)
    return True, ""

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """חילוץ טקסט תוך שמירה על מבנה (Layout)."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            try:
                t = page.extract_text(extraction_mode="layout")
            except:
                t = page.extract_text()
            if t:
                full_text += t + "\n"
        return full_text
    except:
        return ""

def is_comprehensive_pension(text: str) -> bool:
    """זיהוי קרן פנסיה מקיפה על בסיס טקסט שחולץ."""
    if not text: return False
    
    # בדיקת טקסט רגיל וטקסט הפוך (עבור PDF שה-RTL בו משובש)
    per_line_rev = "\n".join(line[::-1] for line in text.split("\n"))
    search_text = text + "\n" + per_line_rev

    # מילות מפתח לזיהוי
    positive_markers = ["בקרן הפנסיה החדשה", "פנסיה מקיפה", "קרן פנסיה מקיפה", "כלל פנסיה", "מקפת"]
    negative_markers = ["קופת גמל", "קרן השתלמות", "ביטוח מנהלים", "קופת הגמל אלפא"]

    found_positive = any(m in search_text for m in positive_markers)
    found_negative = any(m in search_text for m in negative_markers)

    # אם זה מקפת, זו כמעט תמיד פנסיה מקיפה (אלא אם רשום במפורש קופת גמל)
    if "מקפת" in search_text and not found_negative:
        return True

    return found_positive and not (found_negative and "פנסיה" not in search_text)

def validate_file(uploaded_file):
    content = uploaded_file.read()
    uploaded_file.seek(0)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return False, "הקובץ גדול מדי."
    if not content.startswith(b"%PDF"):
        return False, "הקובץ אינו PDF תקני."
    return True, content

def anonymize_pii(text: str) -> str:
    text = re.sub(r"\b\d{7,9}\b", "[ID]", text)
    text = re.sub(r"\b\d{10,12}\b", "[POLICY_NUMBER]", text)
    text = re.sub(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b", "[DATE]", text)
    return text

# ... (פונקציות החישוב ו-build_prompt_messages נשארות כפי שהיו)
# [הכנס כאן את format_full_analysis, estimate_years_to_retirement וכו' מהקוד המקורי שלך]

def analyze_with_openai(text: str, gender: str, employment: str, family_status: str):
    try:
        # כאן קריאת ה-API
        # (השתמש בפונקציית build_prompt_messages המקורית שלך)
        messages = build_prompt_messages(text, gender, employment, family_status)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
        return format_full_analysis(parsed, gender, family_status)
    except Exception as e:
        st.error(f"שגיאה בניתוח ה-AI: {e}")
        return None

# --- ממשק משתמש ---
st.title("🔍 בודק דמי ניהול וכיסוי ביטוחי")
st.write("הרובוט בוחן דוחות מקוצרים בלבד של קרן פנסיה מקיפה.")

gender = st.radio("מה המגדר שלך?", options=["גבר", "אישה"], index=None, horizontal=True)
employment = st.radio("מעמד תעסוקתי?", options=["שכיר", "עצמאי", "שכיר + עצמאי"], index=None, horizontal=True)
family_status = st.radio("מצב משפחתי?", options=["רווק/ה", "נשוי/אה", "לא נשוי/אה אך יש ילדים"], index=None, horizontal=True)

if not all([gender, employment, family_status]):
    st.stop()

file = st.file_uploader("העלה דוח שנתי/רבעוני (PDF)", type=["pdf"])

if file:
    allowed, rate_err = _check_rate_limit()
    if not allowed: st.error(rate_err); st.stop()

    is_valid, result = validate_file(file)
    if not is_valid: st.error(result); st.stop()

    with st.spinner("מעבד נתונים..."):
        # חילוץ טקסט פעם אחת
        full_text = extract_pdf_text(result)
        
        # בדיקת דוח מקוצר (לפי עמודים)
        reader = pypdf.PdfReader(io.BytesIO(result))
        if len(reader.pages) > MAX_PAGES:
            st.warning(f"הדוח ארוך מדי ({len(reader.pages)} עמודים). העלה דוח מקוצר.")
            st.stop()

        # בדיקת סוג הקרן (על בסיס הטקסט שכבר חולץ)
        if not is_comprehensive_pension(full_text):
            st.error("⚠️ לא זוהתה קרן פנסיה מקיפה. הבוט מיועד לניתוח קרנות פנסיה מקיפות בלבד.")
            st.stop()

        # המשך לניתוח
        anon_text = anonymize_pii(full_text)
        analysis = analyze_with_openai(anon_text[:MAX_TEXT_CHARS], gender, employment, family_status)
        
        if analysis:
            st.success("הניתוח הושלם!")
            st.markdown(analysis)
