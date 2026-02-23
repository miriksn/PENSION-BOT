import streamlit as st
import fitz  # PyMuPDF
import io
import gc
import base64
import json
import hashlib
import time
from openai import OpenAI

st.set_page_config(
    page_title="חילוץ טבלאות פנסיה",
    layout="wide",
    page_icon="📋"
)

# ─── CSS לתמיכה בעברית ─────────────────────────────────────
st.markdown("""
<style>
    * { direction: rtl; text-align: right; }
    .stApp { font-family: 'Assistant', 'Segoe UI', sans-serif; }
    table { width: 100%; border-collapse: collapse; direction: rtl; margin-bottom: 1.5rem; }
    th { background-color: #1a3a5c; color: white; padding: 10px 14px; font-size: 0.95rem; }
    td { padding: 8px 14px; border-bottom: 1px solid #e2e8f0; font-size: 0.9rem; }
    tr:nth-child(even) { background-color: #f7fafc; }
    tr:hover { background-color: #ebf4ff; }
    .table-title {
        background: #1a3a5c;
        color: white;
        padding: 10px 16px;
        border-radius: 6px 6px 0 0;
        font-size: 1.05rem;
        font-weight: bold;
        margin-top: 1.5rem;
    }
    .report-header {
        background: linear-gradient(135deg, #1a3a5c, #2d6a9f);
        color: white;
        padding: 16px 20px;
        border-radius: 10px;
        margin-bottom: 1.5rem;
    }
    .report-header h3 { color: white; margin: 0 0 6px 0; }
    .report-header p { margin: 4px 0; font-size: 0.9rem; opacity: 0.9; }
    .stFileUploader { direction: rtl; }
    .negative { color: #c53030; }
    .positive { color: #276749; }
</style>
""", unsafe_allow_html=True)

# ─── קבועי אבטחה ───────────────────────────────────────────
MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_PAGES = 4
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW_SEC = 3600

# ─── משיכת המפתח ────────────────────────────────────────────
try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(
        api_key=API_KEY,
        default_headers={"OpenAI-No-Store": "true"},
    )
except Exception:
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.stop()


# ─── Rate limiting ───────────────────────────────────────────
def _get_client_id() -> str:
    headers = st.context.headers if hasattr(st, "context") else {}
    raw_ip = (
        headers.get("X-Forwarded-For", "")
        or headers.get("X-Real-Ip", "")
        or "unknown"
    )
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
        mins = remaining // 60
        return False, f"❌ הגעת למגבלת {RATE_LIMIT_MAX} עיבודים לשעה. נסה שוב בעוד {mins} דקות."
    st.session_state[key].append(now)
    return True, ""


# ─── ולידציית קובץ ──────────────────────────────────────────
def validate_file(uploaded_file) -> tuple[bool, str]:
    content = uploaded_file.read()
    uploaded_file.seek(0)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return False, f"❌ הקובץ גדול מדי. מקסימום: {MAX_FILE_SIZE_MB} MB"
    if not content.startswith(b"%PDF"):
        return False, "❌ הקובץ אינו PDF תקני"
    return True, ""


# ─── המרת PDF לתמונות (base64) ──────────────────────────────
def pdf_to_images_b64(pdf_bytes: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    """
    ממיר עמודי PDF לתמונות PNG מקודדות ב-base64.
    משתמש ב-PyMuPDF (fitz) — קורא את הדף כמו שהוא נראה,
    ללא בעיות של חילוץ טקסט הפוך או שורות חסרות.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images_b64 = []
    pages_to_process = min(len(doc), max_pages)

    for page_num in range(pages_to_process):
        page = doc[page_num]
        # 200 DPI — חד מספיק לקריאת טקסט עברי קטן
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        images_b64.append(b64)
        del pix, img_bytes

    doc.close()
    return images_b64


# ─── Prompt ל-GPT-4o Vision ──────────────────────────────────
def build_vision_messages(images_b64: list[str]) -> list[dict]:
    system_prompt = """אתה מחלץ נתונים מדוחות פנסיה ישראליים.
תפקידך: לקרוא את התמונות של הדוח ולחלץ את הטבלאות בדיוק מלא.

חוקים קריטיים:
1. העתק את הטקסט העברי בדיוק כפי שמופיע בדוח
2. שמור על סימני מינוס (-) בסכומים שליליים — חשוב מאוד
3. שם הקרן — קח מכותרת הדוח הראשית (לדוגמה: "אלטשולר שחם פנסיה מקיפה")
4. אם שדה לא קיים, החזר null

התעלם לחלוטין מהאלמנטים הבאים — הם אינם חלק מהטבלאות:
- תיבות צדדיות עם טקסט כגון "לידיעתך ממוצע דמי ניהול בקרן" — זהו מידע השוואתי בלבד
- הערות שוליים עם כוכבית (*)
- טקסט הסבר מחוץ לגבולות הטבלה
- הוראות כלליות ("בדוק אם סכומי הביטוח", "מומלץ לבדוק", "שים לב לגובה דמי הניהול")

הוראות ספציפיות לטבלא ב (תנועות בקרן):
- חלץ כל שורה בנפרד — גם אם יש שתי שורות ביטוח נפרדות (נכות ומוות) חלץ כל אחת בשורה משלה
- שורת "הפסדים בניכוי הוצאות ניהול השקעות" היא שורה קריטית — אל תדלג עליה
- בדיקת חובה: חשב את הסכום של כל השורות מלבד האחרונה. התוצאה חייבת להיות שווה לשורה האחרונה (יתרה בסוף התקופה). אם לא — יש שורות חסרות, חזור ותחפש.
- שורות שליליות (-) חייבות להופיע עם מינוס

הוראות ספציפיות לטבלא ג (דמי ניהול):
- הטבלה מכילה רק את דמי הניהול הנגבים מהעמית הספציפי הזה
- בדוח רבעוני: 2 שורות (מהפקדה, מחיסכון)
- בדוח שנתי: 3 שורות (מהפקדה, מחיסכון, הוצאות ניהול השקעות)
- אל תכלול את "ממוצע דמי ניהול בקרן" — זה טקסט צדדי השוואתי, לא חלק מהטבלה

הוראות ספציפיות לטבלא ה (פירוט הפקדות):
- הטבלה כתובה מימין לשמאל
- סדר העמודות מימין לשמאל הוא בדיוק: [1]מועד הפקדה | [2]עבור חודש | [3]משכורת | [4]תגמולי עובד | [5]תגמולי מעסיק | [6]פיצויים | [7]סה"כ
- עמודה [1] "מועד הפקדה" — תאריך מלא עם יום, חודש ושנה: DD/MM/YYYY (לדוגמה: 03/02/2025). תמיד יש בה 3 מקטעים מופרדים בלוכסן
- עמודה [2] "עבור חודש" — חודש ושנה בלבד: MM/YYYY (לדוגמה: 01/2025). תמיד יש בה 2 מקטעים בלבד
- ההבדל הקריטי: מועד ההפקדה מתחיל תמיד ביום (01-31), ועבור חודש מתחיל בחודש (01-12) — אבל שתיהן עשויות להתחיל באותם מספרים, לכן קרא את מספר המקטעים: 3 = תאריך מלא, 2 = חודש/שנה
- חלץ כל שורה בנפרד — כולל שורות עם סכומים קטנים כמו 38 ₪ או 88 ₪
- בדיקת חובה: סכום עמודת סה"כ של כל השורות חייב להיות שווה לסה"כ בשורת הסיכום. אם לא — יש שורות חסרות

כלל גורף לכל הטבלאות:
- אסור לדלג על אף שורה — גם אם הערך בה הוא 0, גם אם היא נראית לא חשובה
- כל שורה שמופיעה בדוח חייבת להופיע בJSON

החזר JSON בלבד בפורמט:
{
  "report_info": {
    "fund_name": "שם הקרן/קופה מהכותרת",
    "report_type": "רבעוני או שנתי",
    "report_period": "תקופת הדוח כמו שמופיעה בדוח",
    "report_date": "תאריך הדוח"
  },
  "table_a": {
    "title": "א. תשלומים צפויים מקרן הפנסיה",
    "rows": [{"description": "טקסט מדויק", "value": "סכום"}]
  },
  "table_b": {
    "title": "ב. תנועות בקרן הפנסיה בתקופת הדוח",
    "rows": [{"description": "טקסט מדויק", "value": "סכום (שמור - אם שלילי)"}]
  },
  "table_c": {
    "title": "ג. אחוז דמי ניהול והוצאות",
    "rows": [{"description": "טקסט מדויק", "value": "אחוז"}]
  },
  "table_d": {
    "title": "ד. מסלולי השקעה ותשואות",
    "rows": [{"description": "שם המסלול", "value": "תשואה (שמור - אם שלילי)"}]
  },
  "table_e": {
    "title": "ה. פירוט הפקדות לקרן הפנסיה",
    "rows": [
      {
        "employer_name": "שם מעסיק אם קיים",
        "deposit_date": "מועד הפקדה",
        "salary_month": "עבור חודש משכורת",
        "salary": "משכורת",
        "employee": "תגמולי עובד",
        "employer": "תגמולי מעסיק",
        "severance": "פיצויים",
        "total": "סה\"כ הפקדות"
      }
    ],
    "totals": {
      "employee": "סה\"כ תגמולי עובד",
      "employer": "סה\"כ תגמולי מעסיק",
      "severance": "סה\"כ פיצויים",
      "total": "סה\"כ הפקדות"
    }
  }
}"""

    content = [{"type": "text", "text": "חלץ את הנתונים מהדוח הפנסיוני. החזר JSON בלבד."}]
    for b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64}",
                "detail": "high"
            }
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


# ─── שליחה ל-GPT-4o Vision ───────────────────────────────────
def extract_tables_with_vision(images_b64: list[str]) -> dict | None:
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=build_vision_messages(images_b64),
            temperature=0.0,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        return json.loads(raw)
    except json.JSONDecodeError:
        st.error("❌ תגובת ה-AI לא הייתה בפורמט תקין. נסה שוב.")
        return None
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "quota" in err.lower():
            st.error("❌ חריגה מהמכסה ב-OpenAI.")
        else:
            st.error(f"❌ שגיאה: {err[:120]}")
        return None


# ─── הצגת הטבלאות ────────────────────────────────────────────
def display_tables(data: dict):
    info = data.get("report_info", {})

    st.markdown(f"""
    <div class="report-header">
        <h3>📋 {info.get('fund_name', 'דוח פנסיוני')}</h3>
        <p>סוג דוח: <strong>{info.get('report_type', '—')}</strong></p>
        <p>תקופה: <strong>{info.get('report_period', '—')}</strong></p>
        <p>תאריך הדוח: <strong>{info.get('report_date', '—')}</strong></p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        tbl = data.get("table_a", {})
        st.markdown('<div class="table-title">א. תשלומים צפויים מקרן הפנסיה</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = '<table><thead><tr><th>פריט</th><th>סכום (ש"ח)</th></tr></thead><tbody>'
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    with col2:
        tbl = data.get("table_b", {})
        st.markdown('<div class="table-title">ב. תנועות בקרן הפנסיה בתקופת הדוח</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = '<table><thead><tr><th>פריט</th><th>סכום (ש"ח)</th></tr></thead><tbody>'
            for r in rows:
                val = str(r.get('value', ''))
                css = ' class="negative"' if val.lstrip().startswith('-') else ''
                html += f"<tr><td>{r.get('description','')}</td><td{css}>{val}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    col3, col4 = st.columns(2)

    with col3:
        tbl = data.get("table_c", {})
        st.markdown('<div class="table-title">ג. אחוז דמי ניהול והוצאות</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>פריט</th><th>אחוז</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    with col4:
        tbl = data.get("table_d", {})
        st.markdown('<div class="table-title">ד. מסלולי השקעה ותשואות</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>מסלול</th><th>תשואה</th></tr></thead><tbody>"
            for r in rows:
                val = str(r.get('value', ''))
                css = ' class="negative"' if val.lstrip().startswith('-') else ' class="positive"'
                html += f"<tr><td>{r.get('description','')}</td><td{css}>{val}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    st.markdown("---")
    tbl = data.get("table_e", {})
    st.markdown('<div class="table-title">ה. פירוט הפקדות לקרן הפנסיה</div>', unsafe_allow_html=True)
    rows = tbl.get("rows", [])
    totals = tbl.get("totals", {})
    if rows:
        has_employer_col = any(r.get("employer_name") for r in rows)
        headers = ""
        if has_employer_col:
            headers += "<th>שם המעסיק</th>"
        headers += "<th>מועד הפקדה</th><th>עבור חודש</th><th>משכורת</th><th>תגמולי עובד</th><th>תגמולי מעסיק</th><th>פיצויים</th><th>סה\"כ</th>"
        html = f"<table><thead><tr>{headers}</tr></thead><tbody>"
        for r in rows:
            row_html = ""
            if has_employer_col:
                row_html += f"<td>{r.get('employer_name','')}</td>"
            row_html += f"<td>{r.get('deposit_date','')}</td><td>{r.get('salary_month','')}</td><td>{r.get('salary','')}</td><td>{r.get('employee','')}</td><td>{r.get('employer','')}</td><td>{r.get('severance','')}</td><td><strong>{r.get('total','')}</strong></td>"
            html += f"<tr>{row_html}</tr>"
        if totals:
            colspan = 4 if has_employer_col else 3
            html += f'<tr style="background:#dbeafe; font-weight:bold;"><td colspan="{colspan}">סה"כ</td><td>{totals.get("employee","")}</td><td>{totals.get("employer","")}</td><td>{totals.get("severance","")}</td><td>{totals.get("total","")}</td></tr>'
        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)


# ─── ממשק משתמש ─────────────────────────────────────────────
st.markdown("<h1 style='text-align:right'>📋 חילוץ טבלאות מדוח פנסיוני</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:right; color:#555'>העלה דוח פנסיה בפורמט PDF — הנתונים יוצגו כטבלאות מסודרות</p>", unsafe_allow_html=True)

file = st.file_uploader("בחר קובץ PDF", type=["pdf"])

if file:
    allowed, rate_error = _check_rate_limit()
    if not allowed:
        st.error(rate_error)
        st.stop()

    is_valid, error_message = validate_file(file)
    if not is_valid:
        st.error(error_message)
        st.stop()

    try:
        with st.spinner("🔄 ממיר דוח לתמונות ומחלץ טבלאות... אנא המתן"):
            pdf_bytes = file.read()

            images_b64 = pdf_to_images_b64(pdf_bytes)
            del pdf_bytes
            gc.collect()

            if not images_b64:
                st.error("❌ לא הצלחתי לפתוח את הקובץ.")
                st.stop()

            st.info(f"📄 עמודים לעיבוד: {len(images_b64)}")

            result = extract_tables_with_vision(images_b64)
            del images_b64
            gc.collect()

            if result:
                st.success("✅ הטבלאות חולצו בהצלחה!")
                display_tables(result)

                with st.expander("📥 הורד נתונים גולמיים (JSON)"):
                    st.download_button(
                        label="הורד JSON",
                        data=json.dumps(result, ensure_ascii=False, indent=2),
                        file_name="pension_data.json",
                        mime="application/json",
                    )

    except Exception as e:
        st.error(f"❌ אירעה שגיאה: {str(e)[:150]}")

st.markdown("---")
st.caption("כלי עזר בלבד | אינו מהווה ייעוץ פנסיוני מקצועי")
