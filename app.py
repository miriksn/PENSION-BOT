import streamlit as st
import pypdf
import io
import gc
import re
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
    .report-header p { margin: 2px 0; font-size: 0.9rem; opacity: 0.9; }
    .error-box { background: #fff5f5; border-right: 4px solid #e53e3e; padding: 12px; border-radius: 4px; }
    .stFileUploader { direction: rtl; }
</style>
""", unsafe_allow_html=True)

# ─── קבועי אבטחה ───────────────────────────────────────────
MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_CHARS = 15_000
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW_SEC = 3600

# ─── אבטחה: משיכת המפתח ────────────────────────────────────
try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(
        api_key=API_KEY,
        default_headers={"OpenAI-No-Store": "true"},
    )
except Exception:
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.stop()


# ─── Rate limiting ──────────────────────────────────────────
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


# ─── ולידציית קובץ ─────────────────────────────────────────
def validate_file(uploaded_file) -> tuple[bool, str]:
    content = uploaded_file.read()
    uploaded_file.seek(0)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return False, f"❌ הקובץ גדול מדי. מקסימום: {MAX_FILE_SIZE_MB} MB"
    if not content.startswith(b"%PDF"):
        return False, "❌ הקובץ אינו PDF תקני"
    return True, ""


# ─── אנונימיזציה ──────────────────────────────────────────
def anonymize_pii(text: str) -> str:
    # ת"ז ישראלית: 7-9 ספרות (לא כחלק מסכומים)
    text = re.sub(r"(?<!\d)\d{7,9}(?!\d)", "[ID]", text)
    # מספר פוליסה: 10-12 ספרות
    text = re.sub(r"(?<!\d)\d{10,12}(?!\d)", "[POLICY_NUMBER]", text)
    # תאריכים
    text = re.sub(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b", "[DATE]", text)
    # אימייל
    text = re.sub(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL]", text)
    # טלפון
    text = re.sub(r"\b0\d{1,2}[-\s]?\d{7}\b", "[PHONE]", text)
    # שם מלא: מוחק רק שם+שם_משפחה שמופיעים אחרי "שם העמית:" או "שם העמית/ה:"
    text = re.sub(
        r"(שם העמית(?:/ה)?[:\s]+)([\u05d0-\u05ea\s]{2,30})",
        r"\1[FULL_NAME]",
        text
    )
    return text


# ─── תיקון טקסט הפוך (RTL שנחלץ בסדר שגוי) ────────────────
def fix_reversed_hebrew(text: str) -> str:
    """
    pypdf לפעמים מחלץ שורות עבריות הפוכות.
    בודק כל שורה — אם היא נראית הפוכה (מתחילה בתווים לטיניים/מספרים
    ומסתיימת בעברית) — הופך אותה.
    """
    fixed_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            fixed_lines.append(line)
            continue
        # בדיקה אם השורה מכילה עברית
        has_hebrew = bool(re.search(r'[\u05d0-\u05ea]', stripped))
        if has_hebrew:
            # אם השורה מתחילה בתו לטיני/מספר ומסתיימת בעברית — כנראה הפוכה
            starts_non_hebrew = bool(re.match(r'^[a-zA-Z0-9\s,.\-]', stripped))
            ends_hebrew = bool(re.search(r'[\u05d0-\u05ea]$', stripped))
            if starts_non_hebrew and ends_hebrew:
                fixed_lines.append(stripped[::-1])
                continue
        fixed_lines.append(line)
    return "\n".join(fixed_lines)


# ─── חילוץ טקסט מ-PDF ──────────────────────────────────────
def extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    full_text = ""
    for page in reader.pages:
        t = page.extract_text()
        if t:
            full_text += t + "\n"
    return full_text


# ─── Prompt לחילוץ טבלאות ──────────────────────────────────
def build_extraction_prompt(text: str) -> list[dict]:
    system_prompt = """אתה מחלץ נתונים מדוחות פנסיה ישראליים.
תפקידך אחד בלבד: לחלץ את הטבלאות מהטקסט ולהחזיר JSON מובנה.
אל תנתח, אל תמליץ, אל תוסיף מידע שאינו בטקסט.
אם ערך לא קיים, החזר null.

החזר JSON בלבד בפורמט הבא:

{
  "report_info": {
    "fund_name": "שם הקרן/חברה",
    "report_type": "רבעוני/שנתי",
    "report_period": "תקופת הדוח",
    "report_date": "תאריך הדוח"
  },
  "table_a": {
    "title": "א. תשלומים צפויים מקרן הפנסיה",
    "rows": [
      {"description": "תיאור", "value": "ערך בש\"ח"}
    ]
  },
  "table_b": {
    "title": "ב. תנועות בקרן הפנסיה בתקופת הדוח",
    "rows": [
      {"description": "תיאור", "value": "ערך בש\"ח"}
    ]
  },
  "table_c": {
    "title": "ג. אחוז דמי ניהול והוצאות",
    "rows": [
      {"description": "תיאור", "value": "ערך באחוזים"}
    ]
  },
  "table_d": {
    "title": "ד. מסלולי השקעה ותשואות",
    "rows": [
      {"description": "שם המסלול", "value": "תשואה"}
    ]
  },
  "table_e": {
    "title": "ה. פירוט הפקדות לקרן הפנסיה",
    "columns": ["מועד הפקדה", "עבור חודש משכורת", "משכורת", "תגמולי עובד", "תגמולי מעסיק", "פיצויים", "סה\"כ הפקדות"],
    "rows": [
      {"deposit_date": "", "salary_month": "", "salary": "", "employee": "", "employer": "", "severance": "", "total": ""}
    ],
    "totals": {"employee": "", "employer": "", "severance": "", "total": ""}
  }
}"""

    user_prompt = (
        "חלץ את 5 הטבלאות מהדוח הפנסיוני הבא.\n\n"
        "<PENSION_REPORT>\n"
        f"{text}\n"
        "</PENSION_REPORT>\n\n"
        "החזר JSON בלבד."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ─── שליחה ל-OpenAI ────────────────────────────────────────
def extract_tables_with_ai(text: str) -> dict | None:
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=build_extraction_prompt(text),
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
        st.error(f"❌ אירעה שגיאה: {str(e)[:100]}")
        return None


# ─── הצגת הטבלאות ──────────────────────────────────────────
def display_tables(data: dict):
    info = data.get("report_info", {})

    # כותרת הדוח
    st.markdown(f"""
    <div class="report-header">
        <h3>📋 {info.get('fund_name', 'דוח פנסיוני')}</h3>
        <p>סוג דוח: {info.get('report_type', '—')} &nbsp;|&nbsp; תקופה: {info.get('report_period', '—')} &nbsp;|&nbsp; תאריך: {info.get('report_date', '—')}</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    # ── טבלא א ──────────────────────────────────────────────
    with col1:
        tbl = data.get("table_a", {})
        st.markdown(f'<div class="table-title">א. {tbl.get("title", "תשלומים צפויים")}</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>פריט</th><th>סכום (ש\"ח)</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    # ── טבלא ב ──────────────────────────────────────────────
    with col2:
        tbl = data.get("table_b", {})
        st.markdown(f'<div class="table-title">ב. {tbl.get("title", "תנועות בקרן")}</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>פריט</th><th>סכום (ש\"ח)</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    col3, col4 = st.columns(2)

    # ── טבלא ג ──────────────────────────────────────────────
    with col3:
        tbl = data.get("table_c", {})
        st.markdown(f'<div class="table-title">ג. {tbl.get("title", "דמי ניהול")}</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>פריט</th><th>אחוז</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    # ── טבלא ד ──────────────────────────────────────────────
    with col4:
        tbl = data.get("table_d", {})
        st.markdown(f'<div class="table-title">ד. {tbl.get("title", "מסלולי השקעה")}</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>מסלול</th><th>תשואה</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            html += "</tbody></table>"
            st.markdown(html, unsafe_allow_html=True)

    # ── טבלא ה ──────────────────────────────────────────────
    st.markdown("---")
    tbl = data.get("table_e", {})
    st.markdown(f'<div class="table-title">ה. {tbl.get("title", "פירוט הפקדות")}</div>', unsafe_allow_html=True)
    rows = tbl.get("rows", [])
    totals = tbl.get("totals", {})
    if rows:
        html = """<table>
        <thead>
          <tr>
            <th>מועד הפקדה</th>
            <th>עבור חודש</th>
            <th>משכורת</th>
            <th>תגמולי עובד</th>
            <th>תגמולי מעסיק</th>
            <th>פיצויים</th>
            <th>סה"כ</th>
          </tr>
        </thead>
        <tbody>"""
        for r in rows:
            html += f"""<tr>
                <td>{r.get('deposit_date','')}</td>
                <td>{r.get('salary_month','')}</td>
                <td>{r.get('salary','')}</td>
                <td>{r.get('employee','')}</td>
                <td>{r.get('employer','')}</td>
                <td>{r.get('severance','')}</td>
                <td><strong>{r.get('total','')}</strong></td>
            </tr>"""
        if totals:
            html += f"""<tr style="background:#e8f4fd; font-weight:bold;">
                <td colspan="3">סה"כ</td>
                <td>{totals.get('employee','')}</td>
                <td>{totals.get('employer','')}</td>
                <td>{totals.get('severance','')}</td>
                <td>{totals.get('total','')}</td>
            </tr>"""
        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)


# ─── ממשק משתמש ────────────────────────────────────────────
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
        with st.spinner("🔄 מחלץ טבלאות... אנא המתן"):
            pdf_bytes = file.read()
            full_text = extract_pdf_text(pdf_bytes)
            del pdf_bytes
            gc.collect()

            if not full_text or len(full_text.strip()) < 50:
                st.error("❌ לא הצלחתי לקרוא טקסט מהקובץ. ייתכן שהוא מוצפן או סרוק כתמונה.")
                st.stop()

            # תיקון טקסט הפוך לפני אנונימיזציה
            fixed_text = fix_reversed_hebrew(full_text)
            del full_text
            gc.collect()

            anon_text = anonymize_pii(fixed_text)
            del fixed_text
            gc.collect()

            trimmed_text = anon_text[:MAX_TEXT_CHARS]
            del anon_text
            gc.collect()

            result = extract_tables_with_ai(trimmed_text)
            del trimmed_text
            gc.collect()

            if result:
                st.success("✅ הטבלאות חולצו בהצלחה!")
                display_tables(result)

                # אפשרות להורדת JSON
                with st.expander("📥 הורד נתונים גולמיים (JSON)"):
                    st.download_button(
                        label="הורד JSON",
                        data=json.dumps(result, ensure_ascii=False, indent=2),
                        file_name="pension_data.json",
                        mime="application/json",
                    )

    except pypdf.errors.PdfReadError:
        st.error("❌ הקובץ פגום או מוצפן ולא ניתן לקריאה.")
    except Exception:
        st.error("❌ אירעה שגיאה בעיבוד הקובץ. נסה שוב מאוחר יותר.")

st.markdown("---")
st.caption("כלי עזר בלבד | אינו מהווה ייעוץ פנסיוני מקצועי")
