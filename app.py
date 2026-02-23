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

st.markdown("""
<style>
    * { direction: rtl; text-align: right; }
    .stApp { font-family: 'Assistant', 'Segoe UI', sans-serif; }
    table { width: 100%; border-collapse: collapse; direction: rtl; margin-bottom: 1.5rem; }
    th { background-color: #1a3a5c; color: white; padding: 10px 14px; font-size: 0.95rem; }
    td { padding: 8px 14px; border-bottom: 1px solid #e2e8f0; font-size: 0.9rem; }
    tr:nth-child(even) { background-color: #f7fafc; }
    tr:hover { background-color: #ebf4ff; }
    .table-title { background: #1a3a5c; color: white; padding: 10px 16px; border-radius: 6px 6px 0 0; font-size: 1.05rem; font-weight: bold; margin-top: 1.5rem; }
    .report-header { background: linear-gradient(135deg, #1a3a5c, #2d6a9f); color: white; padding: 16px 20px; border-radius: 10px; margin-bottom: 1.5rem; }
    .report-header h3 { color: white; margin: 0 0 6px 0; }
    .report-header p { margin: 4px 0; font-size: 0.9rem; opacity: 0.9; }
    .negative { color: #c53030; }
    .positive { color: #276749; }
    .warning-box { background: #fffbeb; border-right: 4px solid #d97706; padding: 10px 14px; border-radius: 4px; margin: 8px 0; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_PAGES = 4
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW_SEC = 3600

try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=API_KEY, default_headers={"OpenAI-No-Store": "true"})
except Exception:
    st.error("⚠️ שגיאה: מפתח ה-API לא נמצא בכספת.")
    st.stop()


def _get_client_id() -> str:
    headers = st.context.headers if hasattr(st, "context") else {}
    raw_ip = headers.get("X-Forwarded-For", "") or headers.get("X-Real-Ip", "") or "unknown"
    return hashlib.sha256(raw_ip.split(",")[0].strip().encode()).hexdigest()[:16]


def _check_rate_limit() -> tuple[bool, str]:
    cid = _get_client_id()
    now = time.time()
    key = f"rl_{cid}"
    if key not in st.session_state:
        st.session_state[key] = []
    st.session_state[key] = [t for t in st.session_state[key] if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(st.session_state[key]) >= RATE_LIMIT_MAX:
        mins = int((RATE_LIMIT_WINDOW_SEC - (now - st.session_state[key][0])) / 60)
        return False, f"❌ הגעת למגבלת {RATE_LIMIT_MAX} עיבודים לשעה. נסה שוב בעוד {mins} דקות."
    st.session_state[key].append(now)
    return True, ""


def validate_file(uploaded_file) -> tuple[bool, str]:
    content = uploaded_file.read()
    uploaded_file.seek(0)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return False, f"❌ הקובץ גדול מדי. מקסימום: {MAX_FILE_SIZE_MB} MB"
    if not content.startswith(b"%PDF"):
        return False, "❌ הקובץ אינו PDF תקני"
    return True, ""


def pdf_to_images_b64(pdf_bytes: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images_b64 = []
    for page_num in range(min(len(doc), max_pages)):
        page = doc[page_num]
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat)
        b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
        images_b64.append(b64)
        del pix
    doc.close()
    return images_b64


# ─── שלב 1: קריאת הדוח בשפה חופשית (Chain of Thought) ────────
def step1_read_report(images_b64: list[str]) -> str | None:
    """
    מבקש מה-AI לתאר כל טבלה שורה אחר שורה בטקסט חופשי.
    זה מונע טעויות של מיפוי עמודות שגוי ל-JSON ישירות.
    """
    prompt = """אתה קורא דוח פנסיה ישראלי.
תאר כל טבלה שורה אחר שורה — כולל כל המספרים המדויקים.

חוקים קריטיים:
1. התעלם לחלוטין מכל טקסט בתיבות צדדיות (סיידבר) — כגון "לידיעתך ממוצע דמי ניהול בקרן", "בדוק אם סכומי הביטוח", "שים לב לגובה דמי הניהול", "מומלץ לבדוק"
2. שמור על סימני מינוס (-) בסכומים שליליים
3. אסור לדלג על אף שורה — כולל שורות עם ערך 0

לגבי טבלא ב:
- פרט כל שורה בנפרד, כולל "עלות ביטוח לסיכוני נכות" ו"עלות ביטוח למקרה מוות" — הן שתי שורות נפרדות
- בסוף, חשב: האם סכום כל השורות (כולל מינוסים) שווה ליתרה הסופית? אם לא — ציין אילו שורות חסרות

לגבי טבלא ה:
- הטבלה כתובה מימין לשמאל
- לכל שורה, קרא את הערכים מימין לשמאל: [מועד הפקדה] [עבור חודש] [משכורת] [תגמולי עובד] [תגמולי מעסיק] [פיצויים] [סה"כ]
- מועד הפקדה הוא תאריך מלא עם יום: DD/MM/YYYY
- עבור חודש הוא MM/YYYY בלבד
- פרט כל שורה — כולל שורות עם סכומים קטנים (38, 88 וכדומה)
- בסוף, ודא שסכום עמודת סה"כ שווה לסה"כ בשורת הסיכום

פרמט:

=== פרטי הדוח ===
שם הקרן: ...
סוג דוח: ...
תקופה: ...
תאריך: ...

=== טבלא א ===
שורה 1: [תיאור] | [ערך]
...

=== טבלא ב ===
שורה 1: [תיאור] | [ערך]
...
בדיקת סכום: [חישוב]

=== טבלא ג ===
שורה 1: [תיאור] | [ערך]
...

=== טבלא ד ===
שורה 1: [תיאור] | [ערך]
...

=== טבלא ה ===
שורה 1: מועד=[DD/MM/YYYY] | חודש=[MM/YYYY] | משכורת=[X] | עובד=[X] | מעסיק=[X] | פיצויים=[X] | סה"כ=[X]
...
בדיקת סכום: [חישוב]"""

    content = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
            max_tokens=3000,
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"❌ שגיאה בשלב 1: {str(e)[:100]}")
        return None


# ─── שלב 2: המרת התיאור ל-JSON מובנה ────────────────────────
def step2_to_json(text_description: str) -> dict | None:
    """
    ממיר את התיאור הטקסטואלי מהשלב הראשון ל-JSON מובנה.
    """
    prompt = f"""המר את התיאור הבא של דוח פנסיה ל-JSON בדיוק כפי שהוא.
אל תשנה, אל תוסיף, אל תחסר — רק המר לפורמט.

{text_description}

החזר JSON בלבד בפורמט:
{{
  "report_info": {{
    "fund_name": "...",
    "report_type": "רבעוני או שנתי",
    "report_period": "...",
    "report_date": "..."
  }},
  "table_a": {{
    "rows": [{{"description": "...", "value": "..."}}]
  }},
  "table_b": {{
    "rows": [{{"description": "...", "value": "..."}}]
  }},
  "table_c": {{
    "rows": [{{"description": "...", "value": "..."}}]
  }},
  "table_d": {{
    "rows": [{{"description": "...", "value": "..."}}]
  }},
  "table_e": {{
    "rows": [
      {{
        "employer_name": null,
        "deposit_date": "DD/MM/YYYY",
        "salary_month": "MM/YYYY",
        "salary": "...",
        "employee": "...",
        "employer": "...",
        "severance": "...",
        "total": "..."
      }}
    ],
    "totals": {{
      "employee": "...",
      "employer": "...",
      "severance": "...",
      "total": "..."
    }}
  }}
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        st.error(f"❌ שגיאה בשלב 2: {str(e)[:100]}")
        return None


# ─── ולידציה בצד Python ──────────────────────────────────────
def parse_num(s) -> float | None:
    """ממיר מחרוזת מספר לfloat, מטפל במינוסים ובפסיקים."""
    if s is None:
        return None
    try:
        return float(str(s).replace(",", "").replace("−", "-").strip())
    except:
        return None


def validate_table_b(data: dict) -> list[str]:
    """בודק שסכום שורות טבלא ב שווה לשורה האחרונה."""
    warnings = []
    rows = data.get("table_b", {}).get("rows", [])
    if len(rows) < 2:
        return warnings
    total_row = rows[-1]
    other_rows = rows[:-1]
    total_val = parse_num(total_row.get("value"))
    calc_sum = sum(parse_num(r.get("value")) or 0 for r in other_rows)
    if total_val is not None and abs(calc_sum - total_val) > 1:
        warnings.append(f"⚠️ טבלא ב: סכום השורות ({calc_sum:,.0f}) ≠ יתרה סופית ({total_val:,.0f}). ייתכן שחסרות שורות.")
    return warnings


def validate_table_e(data: dict) -> list[str]:
    """בודק שסכום שורות טבלא ה שווה לסה"כ."""
    warnings = []
    tbl = data.get("table_e", {})
    rows = tbl.get("rows", [])
    totals = tbl.get("totals", {})
    if not rows or not totals:
        return warnings
    declared_total = parse_num(totals.get("total"))
    calc_sum = sum(parse_num(r.get("total")) or 0 for r in rows)
    if declared_total is not None and abs(calc_sum - declared_total) > 1:
        warnings.append(f"⚠️ טבלא ה: סכום השורות ({calc_sum:,.0f}) ≠ סה\"כ מוצהר ({declared_total:,.0f}). ייתכן שחסרות שורות.")
    return warnings


# ─── הצגת הטבלאות ────────────────────────────────────────────
def display_tables(data: dict, warnings: list[str]):
    info = data.get("report_info", {})

    st.markdown(f"""
    <div class="report-header">
        <h3>📋 {info.get('fund_name', 'דוח פנסיוני')}</h3>
        <p>סוג דוח: <strong>{info.get('report_type', '—')}</strong></p>
        <p>תקופה: <strong>{info.get('report_period', '—')}</strong></p>
        <p>תאריך הדוח: <strong>{info.get('report_date', '—')}</strong></p>
    </div>
    """, unsafe_allow_html=True)

    if warnings:
        for w in warnings:
            st.markdown(f'<div class="warning-box">{w}</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        tbl = data.get("table_a", {})
        st.markdown('<div class="table-title">א. תשלומים צפויים מקרן הפנסיה</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = '<table><thead><tr><th>פריט</th><th>סכום (ש"ח)</th></tr></thead><tbody>'
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)

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
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)

    col3, col4 = st.columns(2)

    with col3:
        tbl = data.get("table_c", {})
        st.markdown('<div class="table-title">ג. אחוז דמי ניהול והוצאות</div>', unsafe_allow_html=True)
        rows = tbl.get("rows", [])
        if rows:
            html = "<table><thead><tr><th>פריט</th><th>אחוז</th></tr></thead><tbody>"
            for r in rows:
                html += f"<tr><td>{r.get('description','')}</td><td>{r.get('value','')}</td></tr>"
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)

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
            st.markdown(html + "</tbody></table>", unsafe_allow_html=True)

    st.markdown("---")
    tbl = data.get("table_e", {})
    st.markdown('<div class="table-title">ה. פירוט הפקדות לקרן הפנסיה</div>', unsafe_allow_html=True)
    rows = tbl.get("rows", [])
    totals = tbl.get("totals", {})
    if rows:
        has_employer = any(r.get("employer_name") for r in rows)
        headers = ("<th>שם המעסיק</th>" if has_employer else "") + \
            "<th>מועד הפקדה</th><th>עבור חודש</th><th>משכורת</th><th>תגמולי עובד</th><th>תגמולי מעסיק</th><th>פיצויים</th><th>סה\"כ</th>"
        html = f"<table><thead><tr>{headers}</tr></thead><tbody>"
        for r in rows:
            row_html = (f"<td>{r.get('employer_name','')}</td>" if has_employer else "") + \
                f"<td>{r.get('deposit_date','')}</td><td>{r.get('salary_month','')}</td>" \
                f"<td>{r.get('salary','')}</td><td>{r.get('employee','')}</td>" \
                f"<td>{r.get('employer','')}</td><td>{r.get('severance','')}</td>" \
                f"<td><strong>{r.get('total','')}</strong></td>"
            html += f"<tr>{row_html}</tr>"
        if totals:
            colspan = 4 if has_employer else 3
            html += f'<tr style="background:#dbeafe;font-weight:bold;"><td colspan="{colspan}">סה"כ</td>' \
                    f'<td>{totals.get("employee","")}</td><td>{totals.get("employer","")}</td>' \
                    f'<td>{totals.get("severance","")}</td><td>{totals.get("total","")}</td></tr>'
        st.markdown(html + "</tbody></table>", unsafe_allow_html=True)


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
        pdf_bytes = file.read()
        images_b64 = pdf_to_images_b64(pdf_bytes)
        del pdf_bytes
        gc.collect()

        if not images_b64:
            st.error("❌ לא הצלחתי לפתוח את הקובץ.")
            st.stop()

        # שלב 1
        with st.spinner("🔍 שלב 1/2: קורא את הדוח..."):
            text_desc = step1_read_report(images_b64)

        if not text_desc:
            st.stop()

        with st.expander("📝 תיאור גולמי מהדוח (לצורך בדיקה)"):
            st.text(text_desc)

        # שלב 2
        with st.spinner("📊 שלב 2/2: ממיר לטבלאות..."):
            result = step2_to_json(text_desc)
            del images_b64
            gc.collect()

        if result:
            # ולידציה
            warnings = validate_table_b(result) + validate_table_e(result)
            st.success("✅ הטבלאות חולצו!")
            display_tables(result, warnings)

            with st.expander("📥 הורד נתונים גולמיים (JSON)"):
                st.download_button(
                    label="הורד JSON",
                    data=json.dumps(result, ensure_ascii=False, indent=2),
                    file_name="pension_data.json",
                    mime="application/json",
                )

    except Exception as e:
        st.error(f"❌ שגיאה: {str(e)[:150]}")

st.markdown("---")
st.caption("כלי עזר בלבד | אינו מהווה ייעוץ פנסיוני מקצועי")
