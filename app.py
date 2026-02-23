import streamlit as st
import pypdf
import pdfplumber
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
    [data-testid="stFileUploader"] { direction: rtl; }
    [data-testid="stMarkdownContainer"] { text-align: right; }
</style>
""", unsafe_allow_html=True)

MAX_FILE_SIZE_MB   = 5
MAX_FILE_SIZE_BYTES= MAX_FILE_SIZE_MB * 1024 * 1024
MAX_TEXT_CHARS     = 15_000
MAX_PAGES          = 3
RATE_LIMIT_MAX     = 5
RATE_LIMIT_WINDOW_SEC = 3600
PENSION_FACTOR     = 190
RETURN_RATE        = 0.0386
DISABILITY_RELEASE_FACTOR = 0.94

try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
    client = OpenAI(api_key=API_KEY, default_headers={"OpenAI-No-Store": "true"})
except Exception:
    st.error("שגיאה: מפתח ה-API לא נמצא בכספת (Secrets).")
    st.info("הוסף את OPENAI_API_KEY ב-Streamlit Secrets")
    st.stop()


# ─── Rate limiting ──────────────────────────────────────────
def _get_client_id():
    headers = st.context.headers if hasattr(st, "context") else {}
    raw_ip = headers.get("X-Forwarded-For","") or headers.get("X-Real-Ip","") or "unknown"
    return hashlib.sha256(raw_ip.split(",")[0].strip().encode()).hexdigest()[:16]

def _check_rate_limit():
    cid, now, key = _get_client_id(), time.time(), f"rl_{_get_client_id()}"
    if key not in st.session_state: st.session_state[key] = []
    st.session_state[key] = [t for t in st.session_state[key] if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(st.session_state[key]) >= RATE_LIMIT_MAX:
        mins = int(RATE_LIMIT_WINDOW_SEC - (now - st.session_state[key][0])) // 60
        return False, f"הגעת למגבלת {RATE_LIMIT_MAX} ניתוחים לשעה. נסה שוב בעוד {mins} דקות."
    st.session_state[key].append(now)
    return True, ""


# ─── PDF utilities ──────────────────────────────────────────
def extract_pdf_text_layout(pdf_bytes):
    """חילוץ layout mode — לשליחה ל-GPT."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = ""
    for page in reader.pages:
        try:
            t = page.extract_text(extraction_mode="layout")
        except Exception:
            t = page.extract_text()
        if t: text += t + "\n"
    return text

def is_vector_pdf(pdf_bytes):
    try: return len(extract_pdf_text_layout(pdf_bytes).strip()) >= 100
    except: return False

def get_page_count(pdf_bytes):
    try: return len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)
    except: return 0

def is_comprehensive_pension(pdf_bytes):
    """זיהוי קרן פנסיה מקיפה — כל הקרנות הגדולות."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            raw = "".join((p.extract_text() or "") + "\n" for p in pdf.pages)
    except Exception:
        return False
    search = raw + "\n" + "\n".join(l[::-1] for l in raw.split("\n"))
    for m in ["בקרן הפנסיה החדשה","פנסיה מקיפה","קרן פנסיה מקיפה"]:
        if m in search: return True
    if "מקפת" in search and not any(r in search for r in ["קופת הגמל אלפא","קופות הגמל"]):
        return True
    if "כלל פנסיה" in search: return True
    return False

def validate_file(uploaded_file):
    content = uploaded_file.read(); uploaded_file.seek(0)
    if len(content) > MAX_FILE_SIZE_BYTES:
        return False, f"הקובץ גדול מדי. מקסימום: {MAX_FILE_SIZE_MB} MB"
    if not content.startswith(b"%PDF"):
        return False, "הקובץ אינו PDF תקני"
    return True, content

def anonymize_pii(text):
    text = re.sub(r"\b\d{7,9}\b","[ID]",text)
    text = re.sub(r"\b\d{10,12}\b","[POLICY_NUMBER]",text)
    text = re.sub(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b","[DATE]",text)
    text = re.sub(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}","[EMAIL]",text)
    text = re.sub(r"\b0\d{1,2}[-\s]?\d{7}\b","[PHONE]",text)
    text = re.sub(r"[\u05d0-\u05ea]{2,}\s[\u05d0-\u05ea]{2,}\s[\u05d0-\u05ea]{2,}","[FULL_NAME]",text)
    return text


# ─── חילוץ נתונים מספריים ב-Python (לא GPT) ───────────────
def extract_numeric_data(pdf_bytes: bytes) -> dict:
    """
    חולץ את כל הנתונים המספריים ישירות מה-PDF.
    גמיש לפורמטים שונים (אלטשולר, מגדל, כלל, מנורה, מיטב, מור ועוד).
    """
    result = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            raw = "".join((p.extract_text() or "") + "\n" for p in pdf.pages)
            tables = pdf.pages[0].extract_tables() if pdf.pages else []
    except Exception:
        return result

    rev_lines = [l[::-1] for l in raw.split("\n")]
    rev_text  = "\n".join(rev_lines)

    def rev_num(s):
        """'345,2' → 2543 | '94.1' → 1.49"""
        try: return float(s[::-1].replace(",",""))
        except: return None

    def find_rev(pattern):
        m = re.search(pattern, rev_text)
        return rev_num(m.group(1)) if m else None

    def find_table_by_label(tbls, label_keywords):
        """מוצא טבלה לפי מילות מפתח בכל תא בטבלה"""
        for t in tbls:
            for row in t:
                if not row: continue
                for cell in row:
                    if cell and any(kw in str(cell) for kw in label_keywords):
                        return t
        return None

    # ── דמי ניהול ──
    # אלטשולר: % הפוך בטקסט | מגדל ואחרות: טבלה נפרדת עם % ישר
    m = re.search(r"דמי ניהול מהפקדה\s*%([\d.]+)", rev_text)
    if m:
        result["deposit_fee"] = float(m.group(1)[::-1])
    else:
        t = find_table_by_label(tables, ["הדקפהמ לוהינ ימד"])
        if t:
            for row in t:
                try:
                    v = str(row[0]).strip()
                    if "%" in v and "הדקפהמ" in str(row[1]):
                        result["deposit_fee"] = float(v.replace("%",""))
                except: pass

    m = re.search(r"דמי ניהול מחיסכון\s*%([\d.]+)", rev_text)
    if m:
        result["accumulation_fee"] = float(m.group(1)[::-1])
    else:
        t = find_table_by_label(tables, ["ןוכסיחמ לוהינ ימד"])
        if t:
            for row in t:
                try:
                    v = str(row[0]).strip()
                    if "%" in v and "ןוכסיחמ" in str(row[1]):
                        result["accumulation_fee"] = float(v.replace("%",""))
                except: pass

    # ── קצבאות מסעיף א' ──
    # חלק מהדוחות: "גיל 67 ** 853" — הקצבה אחרי **
    m = re.search(r"קצבה חודשית הצפויה לך בפרישה בגיל.*?\*\*\s*([\d,]+)", rev_text)
    result["monthly_pension"] = rev_num(m.group(1)) if m else \
        find_rev(r"קצבה חודשית הצפויה לך בפרישה בגיל.*?\s+([\d,]+)\s")
    result["widow_pension"]      = find_rev(r"קצבה חודשית לאלמן/ה במקרה מוות\s+([\d,]+)")
    result["disability_pension"] = find_rev(r"קצבה חודשית במקרה של נכות מלאה\s+([\d,]+)")
    result["disability_release"] = find_rev(r"שחרור מתשלום הפקדות לקרן במקרה של נכות\s+([\d,]+)")

    # ── תנועות בקרן — חיפוש גמיש לפי תוכן ──
    # ניסיון ראשון: מטבלה עם שתי עמודות (ערך + תיאור)
    t_mov = find_table_by_label(tables, ["הנשה תליחתב ןרקב םיפסכה תרתי"])
    if t_mov:
        for row in t_mov:
            try:
                val   = float(str(row[0]).replace(",","").strip())
                label = str(row[1])[::-1].strip() if row[1] else ""
                if "יתרת הכספים בקרן" in label and any(x in label for x in ["נכון","ב-","ב31","31/0"]):
                    result["accumulation"] = val
                elif "עלות ביטוח לסיכוני נכות" in label:
                    result["disability_insurance_cost"] = abs(val)
                elif "עלות ביטוח למקרה מוות" in label:
                    result["death_insurance_cost"] = abs(val)
            except: pass

    # ניסיון שני: דוח שנתי — טבלת תנועות ללא עמודת ערך → חלץ מהטקסט
    if not result.get("accumulation"):
        # יתרת סוף שנה
        m = re.search(r"יתרת הכספים בקרן בסוף השנה\s+([\d,]+)", rev_text)
        if m: result["accumulation"] = rev_num(m.group(1))
        # יתרת סוף רבעון (כבר מכוסה ע"י הטבלה לעיל, אבל גיבוי)
        if not result.get("accumulation"):
            m = re.search(r"יתרת הכספים בקרן ב?-?\s*[\d./]+\s+([\d,]+)", rev_text)
            if m: result["accumulation"] = rev_num(m.group(1))

    if not result.get("disability_insurance_cost"):
        m = re.search(r"עלות ביטוח לסיכוני נכות\s+([\d,]+)-", rev_text)
        if m: result["disability_insurance_cost"] = rev_num(m.group(1))

    if not result.get("death_insurance_cost"):
        m = re.search(r"עלות ביטוח למקרה מוות\*?\s+([\d,]+)-", rev_text)
        if m: result["death_insurance_cost"] = rev_num(m.group(1))

    # ── הפקדות — חיפוש גמיש, זיהוי חכם של עמודות ──
    t_dep = find_table_by_label(tables, ["תרוכשמ"])
    if not t_dep:
        for t in tables:
            if t and t[0] and any("תרוכשמ" in str(c) for c in t[0] if c):
                t_dep = t; break

    if t_dep:
        header = t_dep[0]
        # עמודת משכורת (לא "שדוח רובע תרוכשמ" = עבור חודש משכורת)
        # עמודת משכורת — בדוחות רבעוניים: "תרוכשמ" בלי "שדוח"
        # בדוחות שנתיים הכותרת ממוזגת (שתי שורות) — עדיין מכילה 'תרוכשמ' 
        # → לוקחים את העמודה הראשונה עם "תרוכשמ" בכל מקרה
        sal_col = next((i for i,h in enumerate(header) if h and "תרוכשמ" in str(h)), None)
        # עמודת סה"כ הפקדות — אם קיימת בכותרת
        total_col = next((i for i,h in enumerate(header)
                          if h and any(x in str(h) for x in ['כ"הס', 'סה"כ', "כ'הס"])), None)

        total_salary = total_deposits = 0.0
        for row in t_dep[1:]:
            try:
                # עמודת משכורת עשויה להיות ממוזגת עם תאריך: "-8,821 -12/2024"
                # → נחלץ רק את המספר הראשון
                raw_sal = str(row[sal_col] or "").strip() if sal_col is not None else ""
                m_sal = re.match(r"-?([\d,]+)", raw_sal)
                sal = float(m_sal.group(1).replace(",","")) if m_sal else 0
                if sal <= 0: continue
                if total_col is not None:
                    # יש עמודת סה"כ מוכנה (מגדל ואחרות)
                    raw_dep = str(row[total_col] or "").strip()
                    m_dep = re.match(r"-?([\d,]+)", raw_dep)
                    dep = float(m_dep.group(1).replace(",","")) if m_dep else 0
                else:
                    # אין עמודת סה"כ — סכום כל עמודות הנומריות (פיצויים+מעסיק+עובד)
                    dep = 0.0
                    for i, cell in enumerate(row):
                        if i == sal_col: continue
                        cell_str = str(cell or "").strip()
                        if re.match(r"^[\d,]+$", cell_str):
                            dep += float(cell_str.replace(",",""))
                        elif "/" in cell_str:
                            break  # הגענו לתאריך — עצור
                if dep > 0:
                    total_salary   += sal
                    total_deposits += dep
            except: pass
        if total_salary > 0:
            result["total_salaries"] = total_salary
            result["total_deposits"] = total_deposits

    # ── שנה ורבעון — חיפוש נפרד (שורה אחת לרבעון, שורה אחרת לשנה) ──
    m_q = re.search(r"לסוף הרבעון ה[-–]\s*(\d)", rev_text)
    m_y = re.search(r"לשנת\s+(\d{4})", rev_text)
    if m_q:
        result["report_quarter"] = int(m_q.group(1))
    if m_y:
        y = int(m_y.group(1))
        # הטקסט הפוך — השנה עשויה להגיע כ-"5202" (2025 הפוך)
        result["report_year"] = y if y < 2100 else int(str(y)[::-1])

    return result

# ─── חישובים ────────────────────────────────────────────────
def estimate_years_to_retirement(accumulation, monthly_pension):
    """NPER חודשי: n = log(FV/PV) / log(1 + r/12)"""
    if not accumulation or not monthly_pension or accumulation <= 0 or monthly_pension <= 0:
        return None
    fv = monthly_pension * PENSION_FACTOR
    try:
        n_months = math.log(fv / accumulation) / math.log(1 + RETURN_RATE / 12)
        return round(n_months / 12, 1)
    except: return None

def is_over_52(accumulation, monthly_pension, report_year):
    if not accumulation or not monthly_pension: return False
    return accumulation / 110 > monthly_pension and report_year == 2025

def calc_insured_salary(disability_release, total_deposits, total_salaries):
    if not all([disability_release, total_deposits, total_salaries]) or total_salaries == 0:
        return None
    deposit_rate = total_deposits / total_salaries
    if deposit_rate == 0: return None
    return (disability_release / DISABILITY_RELEASE_FACTOR) / deposit_rate

def annualize_insurance_cost(cost, quarter):
    if quarter is None: return cost
    return cost * {1: 4.0, 2: 2.0, 3: 1.333, 4: 1.0}.get(quarter, 1.0)

def calc_insurance_savings(annual_cost, years):
    if not years or years <= 0: return 0
    return round(annual_cost * 2 * (1 + RETURN_RATE) ** years)


# ─── GPT — רק לניתוח איכותי (לא נתונים מספריים) ───────────
def build_prompt_messages(text, gender, employment, family_status, numeric_data):
    """GPT מקבל את הנתונים המספריים כבר מחולצים — רק מנתח ומסכם."""
    data_summary = "\n".join(f"- {k}: {v}" for k, v in numeric_data.items() if v is not None)

    system_prompt = f"""אתה יועץ פנסיוני ישראלי.
קיבלת נתונים מספריים שכבר חולצו מדוח הפנסיה. עליך רק לאמת שהנתונים הגיוניים ולהחזיר JSON.

פרטי המשתמש:
- מגדר: {gender}
- סטטוס תעסוקתי: {employment}
- מצב משפחתי: {family_status}

נתונים שחולצו אוטומטית:
{data_summary}

החזר JSON בלבד:
{{
  "deposit_status": "<high|ok|unknown>",
  "accumulation_status": "<high|ok|unknown>"
}}

כללים:
- deposit_status: high אם deposit_fee > 1.0%, אחרת ok
- accumulation_status: high אם accumulation_fee > 0.145%, אחרת ok"""

    user_prompt = "בדוח הפנסיוני הבא, אמת את הנתונים שחולצו והחזר JSON.\n\n<PENSION_REPORT>\n" + text + "\n</PENSION_REPORT>"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]


# ─── פורמט תוצאות ───────────────────────────────────────────
def format_full_analysis(numeric_data: dict, gpt_result: dict, gender: str, family_status: str) -> str:
    lines = []
    icon = {"high": "🔴", "ok": "🟢", "unknown": "⚪"}

    deposit      = numeric_data.get("deposit_fee")
    accum_fee    = numeric_data.get("accumulation_fee")
    deposit_status = gpt_result.get("deposit_status", "unknown") if deposit is None else ("high" if deposit > 1.0 else "ok")
    accum_status   = gpt_result.get("accumulation_status", "unknown") if accum_fee is None else ("high" if accum_fee > 0.145 else "ok")

    lines.append("## 📊 דמי ניהול")
    lines.append(f"- דמי ניהול מהפקדה: **{deposit}%** {icon[deposit_status]}" if deposit is not None else "- דמי ניהול מהפקדה: לא נמצא ⚪")
    lines.append(f"- דמי ניהול על צבירה: **{accum_fee}%** {icon[accum_status]}" if accum_fee is not None else "- דמי ניהול על צבירה: לא נמצא ⚪")
    if "high" in [deposit_status, accum_status]:
        lines.append("\n🔴 **דמי הניהול גבוהים מהסטנדרט.** מומלץ לבדוק אפשרות להפחתה.")
    else:
        lines.append("\n🟢 דמי הניהול תקינים.")

    accumulation      = numeric_data.get("accumulation")
    monthly_pension   = numeric_data.get("monthly_pension")
    widow_pension     = numeric_data.get("widow_pension")
    disability_pension= numeric_data.get("disability_pension")
    disability_release= numeric_data.get("disability_release")
    disability_cost   = numeric_data.get("disability_insurance_cost")
    death_cost        = numeric_data.get("death_insurance_cost")
    total_deposits    = numeric_data.get("total_deposits")
    total_salaries    = numeric_data.get("total_salaries")
    report_year       = numeric_data.get("report_year")
    report_quarter    = numeric_data.get("report_quarter")

    years_to_retirement = estimate_years_to_retirement(accumulation, monthly_pension)
    over_52             = is_over_52(accumulation, monthly_pension, report_year)
    insured_salary      = calc_insured_salary(disability_release, total_deposits, total_salaries)

    lines.append("\n## 🧮 נתונים מחושבים")
    if years_to_retirement is not None:
        if over_52:
            lines.append("- **אומדן שנים לפרישה:** הרובוט מעריך שאתה מעל גיל 52-53 — בשלב זה הרובוט לא מיועד לייעץ לחוסכים בגיל זה.")
        else:
            lines.append(f"- **אומדן שנים לפרישה:** כ-{years_to_retirement} שנים")
    else:
        lines.append("- **אומדן שנים לפרישה:** לא ניתן לחשב (נתונים חסרים)")

    if insured_salary is not None:
        lines.append(f"- **שכר מבוטח מוערך:** ₪{insured_salary:,.0f} לחודש")
    else:
        lines.append("- **שכר מבוטח מוערך:** לא ניתן לחשב (נתונים חסרים)")

    lines.append("\n## 🛡️ בחינת הכיסוי הביטוחי")
    fund_active = disability_cost is not None and disability_cost > 0
    if not fund_active:
        lines.append("🔴 **קרן הפנסיה איננה פעילה ואין לך דרכה כיסויים ביטוחיים!**\nממליץ לשקול לנייד את הכספים לקרן הפנסיה הפעילה שלך.")
        return "\n".join(lines)

    is_single = family_status == "רווק/ה"
    is_coupled = family_status in ["נשוי/אה","לא נשוי/אה אך יש ילדים"]
    death_cost_val  = death_cost or 0
    annual_death    = annualize_insurance_cost(death_cost_val, report_quarter) if death_cost_val > 0 else 0

    if is_single:
        if death_cost_val < 1:
            lines.append("✅ אינך משלם על ביטוח שארים — זה מתאים למצבך כרווק/ה.\n\n💡 **מומלץ לפנות לקרן הפנסיה לרכוש 'ברות ביטוח'** — מה שיחסוך חיתום ותקופת אכשרה בעתיד. העלות זניחה.")
        elif annual_death > 13:
            savings = calc_insurance_savings(annual_death, years_to_retirement or 0)
            savings_str = f"**כ-₪{savings:,}**" if savings else "סכום משמעותי"
            lines.append(
                f"⚠️ **כרווק/ה, ביטוח השארים שאתה משלם ({annual_death:,.0f} ₪ לשנה) כנראה מיותר.**\n\n"
                f"1. ממליץ לשקול לבטל את ביטוח השארים.\n"
                f"2. ביטול לשנתיים צפוי לשפר את הצבירה בערך ב-{savings_str}.\n"
                f"3. יש לחדש את הביטול אחת לשנתיים דרך הקרן."
            )
        else:
            lines.append("✅ **מעולה — אינך מבזבז כסף על ביטוח שארים.**\n\nזכור לעדכן את הקרן אם מצבך המשפחתי משתנה, ולחדש את הוויתור אחת לשנתיים.")
    elif is_coupled:
        if death_cost_val < 13:
            lines.append("⚠️ **ייתכן שאתה בתקופת ויתור שארים.**\n\nמומלץ לעדכן את הקרן שמצבך המשפחתי השתנה כדי שירכשו לך ביטוח שארים מלא.")

    coverage_warnings = []
    if insured_salary and widow_pension is not None:
        min_widow = round(0.59 * insured_salary)
        if widow_pension < min_widow:
            coverage_warnings.append(f"כיסוי האלמן/ה ({widow_pension:,.0f} ₪) נמוך מ-59% מהשכר המבוטח ({min_widow:,.0f} ₪)")
    if insured_salary and disability_pension is not None:
        min_disability = round(0.74 * insured_salary)
        if disability_pension < min_disability:
            coverage_warnings.append(f"כיסוי נכות מלאה ({disability_pension:,.0f} ₪) נמוך מ-74% מהשכר המבוטח ({min_disability:,.0f} ₪)")

    if coverage_warnings:
        lines.append("\n🔴 **הכיסוי הביטוחי בקרן הפנסיה איננו מקסימלי:**")
        for w in coverage_warnings:
            lines.append(f"  - {w}")
        young_man = (gender == "גבר" and years_to_retirement is not None and years_to_retirement > 27)
        if gender == "אישה" or young_man:
            lines.append("\n💡 **מומלץ לשקול לשנות את מסלול הביטוח** כך שיקנה לך ולמשפחתך הגנה ביטוחית מקסימלית.")
    elif insured_salary is not None:
        # רווק עם ביטוח שארים לא תקין (0 או >13 ₪) — הכיסוי הביטוחי מבחינת ביטוח שארים איננו במצב האידיאלי
        single_insurance_ok = not is_single or (death_cost_val >= 1 and annual_death <= 13)
        if single_insurance_ok:
            lines.append("\n✅ **הכיסוי הביטוחי בקרן תקין ומקסימלי.**")

    return "\n".join(lines)


# ─── ניתוח ──────────────────────────────────────────────────
def analyze(pdf_bytes, text, gender, employment, family_status):
    # שלב 1: חלץ נתונים מספריים ב-Python (מדויק ואמין)
    numeric_data = extract_numeric_data(pdf_bytes)

    # שלב 2: שלח ל-GPT רק לאימות סטטוס דמי ניהול
    gpt_result = {}
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=build_prompt_messages(text, gender, employment, family_status, numeric_data),
            temperature=0.0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        gpt_result = json.loads(response.choices[0].message.content)
    except Exception:
        pass  # אם GPT נכשל — נשתמש בחישוב מקומי

    return format_full_analysis(numeric_data, gpt_result, gender, family_status)


# ─── ממשק משתמש ─────────────────────────────────────────────
st.title("🔍 בודק דמי ניהול וכיסוי ביטוחי")
st.write("הרובוט בוחן דוחות מקוצרים בלבד של קרן פנסיה מקיפה (עד 3 עמודים).")
st.write("ענה על מספר שאלות קצרות ולאחר מכן העלה את הדוח.")

with st.expander("ℹ️ מה הסטנדרטים?"):
    st.write("""
    **דמי ניהול תקינים:**
    - 🏦 מהפקדה: עד 1.0%
    - 💰 על צבירה: עד 0.145% בשנה

    **כיסוי ביטוחי מקסימלי:**
    - כיסוי אלמן/ה: לפחות 59% מהשכר המבוטח
    - כיסוי נכות מלאה: לפחות 74% מהשכר המבוטח
    """)

with st.expander("🔒 פרטיות ואבטחה"):
    st.write("""
    - הקובץ מעובד בזיכרון בלבד ואינו נשמר
    - מידע מזהה אישי מוסר לפני שליחה ל-AI
    - OpenAI מקבלת הוראה שלא לשמור את הנתונים
    """)

st.markdown("---")
st.subheader("📋 כמה שאלות לפני שנתחיל")

gender        = st.radio("מה המגדר שלך?", ["גבר","אישה"], index=None, horizontal=True, key="gender")
employment    = st.radio("מה היה מעמדך התעסוקתי במהלך תקופת הדוח?", ["שכיר","עצמאי","שכיר + עצמאי"], index=None, horizontal=True, key="employment")
family_status = st.radio("מה מצבך המשפחתי?", ["רווק/ה","נשוי/אה","לא נשוי/אה אך יש ילדים"], index=None, horizontal=True, key="family_status")

if not all([gender, employment, family_status]):
    st.info("⬆️ ענה על כל השאלות כדי להמשיך")
    st.stop()

st.markdown("---")
st.subheader("📄 העלאת הדוח")
st.write("העלה את הדוח המקוצר של קרן הפנסיה המקיפה שלך (עד 3 עמודים)")
file = st.file_uploader("בחר קובץ PDF", type=["pdf"])

if file:
    allowed, rate_error = _check_rate_limit()
    if not allowed: st.error(rate_error); st.stop()

    is_valid, result = validate_file(file)
    if not is_valid: st.error(result); st.stop()

    pdf_bytes = result

    try:
        with st.spinner("🔄 מנתח דוח... אנא המתן"):

            if not is_vector_pdf(pdf_bytes):
                st.error("הקובץ שהועלה נראה כצילום (PDF סרוק). נא להעלות קובץ PDF מקורי.")
                del pdf_bytes; st.stop()

            if get_page_count(pdf_bytes) > MAX_PAGES:
                st.warning(f"הדוח מכיל יותר מ-{MAX_PAGES} עמודים. אנא העלה את הדוח המקוצר.")
                del pdf_bytes; st.stop()

            if not is_comprehensive_pension(pdf_bytes):
                st.warning("⚠️ הדוח שהעלית אינו דוח של קרן פנסיה מקיפה.\n\nהרובוט בוחן דוחות מקוצרים בלבד של **קרן פנסיה מקיפה**.")
                del pdf_bytes; st.stop()

            full_text = extract_pdf_text_layout(pdf_bytes)
            if not full_text or len(full_text.strip()) < 50:
                st.error("לא הצלחתי לקרוא טקסט. נא להעלות קובץ PDF מקורי."); st.stop()

            anon_text    = anonymize_pii(full_text)
            trimmed_text = anon_text[:MAX_TEXT_CHARS]
            del full_text, anon_text; gc.collect()

            analysis = analyze(pdf_bytes, trimmed_text, gender, employment, family_status)
            del pdf_bytes, trimmed_text; gc.collect()

            if analysis:
                st.success("✅ הניתוח הושלם!")
                st.markdown(analysis)
                st.download_button("📥 הורד תוצאות", analysis, "pension_analysis.txt", "text/plain")

    except pypdf.errors.PdfReadError:
        st.error("הקובץ פגום או מוצפן.")
    except Exception:
        st.error("אירעה שגיאה בעיבוד הקובץ. נסה שוב מאוחר יותר.")

st.markdown("---")
st.caption("🏦 פותח על ידי pensya.info | מופעל על ידי OpenAI GPT-4")
st.caption("זהו כלי עזר בלבד ואינו מהווה ייעוץ פנסיוני מקצועי")
