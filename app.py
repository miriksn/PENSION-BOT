"""
Israeli Pension Report Extractor
=================================
Extracts 5 structured tables from Hebrew pension PDFs using PyMuPDF + GPT-4o.

Tables extracted:
  table_a – תשלומים צפויים        (Expected payments)
  table_b – תנועות בקרן           (Account movements)
  table_c – דמי ניהול והוצאות     (Management fees)
  table_d – מסלולי השקעה ותשואות  (Investment tracks & returns)
  table_e – פירוט הפקדות          (Deposit details)
"""

import io
import json
import re
import math

import fitz          # PyMuPDF
import openai
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="חילוץ נתוני קרן פנסיה",
    page_icon="📊",
    layout="wide",
)

st.title("📊 חילוץ נתוני קרן פנסיה — Pension Report Extractor")
st.markdown(
    "Upload an Israeli pension PDF report (Migdal, Altshuler, Clal, Meitav, More). "
    "The app extracts 5 structured tables using PyMuPDF + GPT-4o."
)

# ──────────────────────────────────────────────────────────────────────────────
# Sidebar – API key & settings
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    st.markdown(
        "**Supported companies:**\n"
        "- מגדל Migdal\n- אלטשולר שחם Altshuler Shaham\n"
        "- כלל Clal\n- מיטב Meitav\n- מור More"
    )
    st.markdown("---")
    st.caption("v1.0 · Hebrew RTL-safe extraction")

# ── Load API key from Streamlit Secrets ──
try:
    api_key = st.secrets["OPENAI_API_KEY"]
except KeyError:
    st.error(
        "⚠️ OpenAI API key not found in Streamlit Secrets.\n\n"
        "Add it in your app settings under **Secrets** as:\n"
        "```\nOPENAI_API_KEY = \"sk-...\"\n```"
    )
    st.stop()

# ──────────────────────────────────────────────────────────────────────────────
# Helper: extract all text from PDF
# ──────────────────────────────────────────────────────────────────────────────

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return concatenated text of every page using PyMuPDF."""
    pages_text = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")          # plain text, preserves lines
                pages_text.append(f"=== PAGE {page_num} ===\n{text}")
    except Exception as exc:
        st.error(f"PyMuPDF error: {exc}")
        raise
    return "\n".join(pages_text)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: call GPT-4o for structured extraction
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a RAW TEXT TRANSCRIBER for Israeli pension fund reports (קרן פנסיה).
Your ONLY job is to locate five specific tables in the raw PDF text and return
their contents in a strict JSON format.

CRITICAL RULES:
1. ZERO interpretation, rounding, or calculation — copy numbers EXACTLY as they appear.
2. If a cell is empty or not found, use null (JSON null).
3. Negative numbers may appear with a trailing minus sign, an en-dash (–), or a
   leading minus. Keep them EXACTLY as extracted — do NOT normalise them.
4. Hebrew text direction causes column shifting in raw extraction — do your best
   to align values to the correct column headers using context clues.
5. Return ONLY valid JSON — no markdown fences, no commentary.

OUTPUT SCHEMA (return this exact structure):
{
  "table_a": [
    {"description": "<string>", "amount": "<string>"}
  ],
  "table_b": [
    {"description": "<string>", "amount": "<string>"}
  ],
  "table_c": [
    {"description": "<string>", "percentage": "<string>"}
  ],
  "table_d": [
    {"track_name": "<string>", "return_percentage": "<string>"}
  ],
  "table_e": [
    {
      "month": "<string>",
      "salary": "<string>",
      "employee": "<string>",
      "employer": "<string>",
      "severance": "<string>",
      "total": "<string>"
    }
  ]
}

TABLE IDENTIFICATION GUIDE:
- table_a → תשלומים צפויים (expected future payments, has description + NIS amount)
- table_b → תנועות בקרן / תנועות בחשבון (account movements: deposits, withdrawals, fees)
- table_c → דמי ניהול / הוצאות (management fees as % of salary or savings)
- table_d → מסלול השקעה / תשואה (investment tracks with % return) — SEE CRITICAL NOTES BELOW
- table_e → פירוט הפקדות / הפקדות חודשיות (monthly deposit breakdown by component)

CRITICAL NOTES FOR table_d (תשואות — Return Percentages):
1. The section header is explicitly "ד. מסלולי השקעה ותשואות בתקופת הדוח" or similar.
2. The return_percentage is the % figure that appears DIRECTLY next to the track name
   inside the ד section ONLY — never take values from section ג (דמי ניהול).
3. The ONLY reliable way to distinguish between table_c and table_d values is by
   their SECTION HEADER — not by their size. Returns can be large (10%, 15%, 20%+).
4. LEGAL RULE YOU MUST USE: דמי ניהול מחיסכון (management fee from savings) is
   legally capped in Israel at 0.5% maximum. Any value above 0.5% CANNOT be a
   management fee from savings — but it CAN be an investment return.
5. DO NOT confuse section ג (דמי ניהול) with section ד (תשואות).
   The two sections are physically adjacent in the PDF — always identify which
   section a number belongs to by its section header (ג vs ד), not its magnitude.
6. If the track name contains "מסלול" and is followed by a % figure in the
   same box/cell under the ד header, that % is the תשואה — copy it EXACTLY.

If a table cannot be found in the document, return an empty array [] for that key.
"""

# ──────────────────────────────────────────────────────────────────────────────
# Helper: extract specific section from raw text by header keyword
# ──────────────────────────────────────────────────────────────────────────────

# Section header keywords (in Hebrew) that mark the START of each table section
_SECTION_HEADERS = [
    "תשלומים צפויים",       # א
    "תנועות בקרן",           # ב
    "דמי ניהול",             # ג
    "מסלולי השקעה",          # ד
    "פירוט הפקדות",          # ה
    "פרטי סוכן",             # ו — used only as an end-boundary
]

def extract_section(raw_text: str, section_keyword: str) -> str:
    """
    Return only the portion of raw_text that starts at the line containing
    section_keyword and ends at the first line containing any other section header.
    Falls back to the full raw_text if the keyword is not found.
    """
    lines = raw_text.splitlines()
    start_idx = None

    for i, line in enumerate(lines):
        if section_keyword in line:
            start_idx = i
            break

    if start_idx is None:
        return raw_text  # keyword not found — return full text as fallback

    # Find the end: first subsequent line that contains a DIFFERENT section header
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        for header in _SECTION_HEADERS:
            if header != section_keyword and header in lines[i]:
                end_idx = i
                break
        if end_idx != len(lines):
            break

    return "\n".join(lines[start_idx:end_idx])


def call_openai(raw_text: str, openai_client: openai.OpenAI) -> dict:
    """Send raw PDF text to GPT-4o and return parsed JSON dict.
    
    For table_d specifically, we extract only the text under the
    'מסלולי השקעה' section header — eliminating any chance of
    confusing returns (ד) with management fees (ג).
    """
    user_message = (
        "Here is the raw text extracted from the pension PDF.\n"
        "Extract tables a, b, c, and e according to your instructions.\n"
        "NOTE: table_d will be extracted separately by a coordinate-based algorithm — "
        "you still need to return a table_d key in your JSON but it can be an empty array.\n\n"
        f"{raw_text[:120_000]}"
    )

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        timeout=120,
    )
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        st.error(f"JSON parse error from GPT-4o response: {exc}")
        st.text_area("Raw GPT-4o output (debug)", content, height=300)
        raise


# ──────────────────────────────────────────────────────────────────────────────
# Helper: robust number cleaner
# ──────────────────────────────────────────────────────────────────────────────

def clean_num(value) -> float | None:
    """
    Convert an extracted string to a float.

    Handles:
    - Comma-separated thousands  (e.g. "1,234.56")
    - Israeli trailing minus     (e.g. "500-" or "500–")
    - Leading minus / en-dash    (e.g. "-500" or "–500")
    - Parenthesised negatives    (e.g. "(500)")
    - Junk strings               (None, "nan", "-", ".", "", whitespace)
    """
    if value is None:
        return None
    s = str(value).strip()

    # Reject obvious non-numbers
    if s in ("", "nan", "-", "–", ".", "N/A", "n/a"):
        return None

    # Remove thousands separators (commas)
    s = s.replace(",", "")

    # Parenthesised negative: (500) → -500
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]

    # Trailing minus / en-dash: 500- or 500– → -500
    if re.search(r"[\-–]$", s):
        s = "-" + re.sub(r"[\-–]$", "", s)

    # Replace leading en-dash with proper minus
    s = s.replace("–", "-")

    # Remove any stray non-numeric characters except . and leading -
    s = re.sub(r"[^\d.\-]", "", s)

    if not s or s in ("-", "."):
        return None

    try:
        return float(s)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Table E – "Shift Fix" heuristic
# ──────────────────────────────────────────────────────────────────────────────

def fix_table_e_shifts(rows: list[dict]) -> list[dict]:
    """
    Hebrew RTL PDFs often shift numeric columns left or right.
    Heuristic: the maximum value among (employee, employer, severance, total)
    is ALWAYS the Total (סה"כ) column.  Re-assign accordingly.

    Logic assumptions (standard Israeli pension structure):
        total  ≈  employee + employer + severance
        total  >  employee, employer, severance individually

    If only one numeric value is found in a row, it is treated as total.
    """
    fixed = []
    for row in rows:
        month    = row.get("month")
        salary   = clean_num(row.get("salary"))
        employee = clean_num(row.get("employee"))
        employer = clean_num(row.get("employer"))
        severance = clean_num(row.get("severance"))
        total    = clean_num(row.get("total"))

        numeric_vals = [v for v in [employee, employer, severance, total] if v is not None]

        if not numeric_vals:
            # Nothing to fix
            fixed.append({
                "month": month,
                "salary": salary,
                "employee": employee,
                "employer": employer,
                "severance": severance,
                "total": total,
            })
            continue

        # The maximum value is logically the total
        max_val = max(numeric_vals)

        # If the current "total" field is already the max, no shift needed
        if total == max_val:
            fixed.append({
                "month": month,
                "salary": salary,
                "employee": employee,
                "employer": employer,
                "severance": severance,
                "total": total,
            })
            continue

        # Shift detected: find which field holds max_val and reassign
        # Collect all four slots in extraction order [employee, employer, severance, total]
        raw_slots = [
            clean_num(row.get("employee")),
            clean_num(row.get("employer")),
            clean_num(row.get("severance")),
            clean_num(row.get("total")),
        ]

        # Identify position of max
        max_idx = None
        for i, v in enumerate(raw_slots):
            if v is not None and v == max_val:
                max_idx = i
                break

        if max_idx is None:
            # Can't determine shift, keep as-is
            fixed.append({
                "month": month,
                "salary": salary,
                "employee": employee,
                "employer": employer,
                "severance": severance,
                "total": total,
            })
            continue

        # Rotate the list so that max_val lands at index 3 (total slot)
        shift = 3 - max_idx
        rotated = raw_slots[-shift:] + raw_slots[:-shift] if shift else raw_slots

        fixed.append({
            "month": month,
            "salary": salary,
            "employee": rotated[0],
            "employer": rotated[1],
            "severance": rotated[2],
            "total": rotated[3],
        })

    return fixed


# ──────────────────────────────────────────────────────────────────────────────
# Cross-validation: Table B total deposits vs Table E sum
# ──────────────────────────────────────────────────────────────────────────────

_DEPOSIT_KEYWORDS = ["הפקדות", "הפקדה", "deposits", "deposit", "קרן", "כולל"]

def find_total_deposits_table_b(table_b_df: pd.DataFrame) -> float | None:
    """
    Scan table_b for a row whose description contains deposit-related keywords.
    Returns the numeric amount of the first match, or None.
    """
    if table_b_df is None or table_b_df.empty:
        return None
    for _, row in table_b_df.iterrows():
        desc = str(row.get("description", ""))
        if any(kw in desc for kw in _DEPOSIT_KEYWORDS):
            val = clean_num(row.get("amount"))
            if val is not None:
                return val
    return None


def cross_validate(table_b_df: pd.DataFrame, table_e_df: pd.DataFrame):
    """Display a Streamlit success/warning based on deposit cross-validation."""
    b_total = find_total_deposits_table_b(table_b_df)
    if b_total is None:
        st.info("ℹ️ Could not locate a deposit row in Table B for cross-validation.")
        return

    if table_e_df is None or table_e_df.empty or "total" not in table_e_df.columns:
        st.info("ℹ️ Table E is empty — skipping cross-validation.")
        return

    e_sum = table_e_df["total"].sum()
    if math.isnan(e_sum):
        st.info("ℹ️ Table E totals contain NaN — cross-validation skipped.")
        return

    diff = abs(b_total - e_sum)
    tolerance = max(1.0, abs(b_total) * 0.01)   # 1% tolerance

    if diff <= tolerance:
        st.success(
            f"✅ Cross-validation PASSED — "
            f"Table B deposits: ₪{b_total:,.2f} | Table E sum: ₪{e_sum:,.2f} | "
            f"Δ = ₪{diff:,.2f}"
        )
    else:
        st.warning(
            f"⚠️ Cross-validation MISMATCH — "
            f"Table B deposits: ₪{b_total:,.2f} | Table E sum: ₪{e_sum:,.2f} | "
            f"Δ = ₪{diff:,.2f}  (possible column-shift residual or report anomaly)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# DataFrame builders
# ──────────────────────────────────────────────────────────────────────────────

def build_table_a(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["description", "amount"])
    df["amount"] = df["amount"].apply(clean_num)
    return df

def build_table_b(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["description", "amount"])
    df["amount"] = df["amount"].apply(clean_num)
    return df

def build_table_c(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["description", "percentage"])
    df["percentage"] = df["percentage"].apply(clean_num)
    return df

def extract_table_d_by_coordinates(pdf_bytes: bytes) -> list[dict]:
    """
    חילוץ טבלה ד' לפי קואורדינטות X,Y של מילות טקסט (PyMuPDF "words" mode).

    למה לא regex על טקסט גולמי:
      PDF עברי RTL ממזג עמודות לשורה אחת — "מסלול כלל פנסיה  0.17%"
      הופך ל-"1.0מסלול כלל פנסיה לבני 0 5 ומטה" ללא סימן אחוז.

    הפתרון: page.get_text("words") מחזיר כל מילה עם (x0,y0,x1,y1).
    נקבץ מילים לשורות לפי y0, ובכל שורה שמכילה "מסלול" נחפש טוקן מספרי.
    """
    rows = []
    track_kw   = "מסלול"
    section_kw = "מסלולי השקעה"
    skip_kw    = ("השקעה", "תשואות")
    end_kws    = ("פירוט הפקדות", "פרטי סוכן", "תנועות בקרן")
    Y_TOL      = 4   # פיקסלים — סף לאיחוד מילים לאותה שורה

    num_re = re.compile(r"^-?\d+[.,]\d+%?$")

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                page_text = page.get_text("text")
                if section_kw not in page_text:
                    continue

                words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_no)

                # מצא y של תחילת וסוף סעיף ד'
                # חשוב: get_text("words") מחזיר מילה בודדת ב-w[4] — לא ביטוי שלם.
                # לכן מחפשים "מסלולי" ו-"השקעה" כמילים נפרדות בשורה קרובה.
                start_y, end_y = None, None

                # בנה מילון y -> רשימת מילים לאיתור ביטויים מרובי מילים
                y_to_words: dict[float, list[str]] = {}
                for w in words:
                    y_r = round(w[1], 0)
                    y_to_words.setdefault(y_r, []).append(w[4])

                for y_r, wtokens in sorted(y_to_words.items()):
                    line_str = " ".join(wtokens)
                    # תחילת סעיף ד': שורה שמכילה "מסלולי" ו"השקעה"
                    if start_y is None and "מסלולי" in line_str and "השקעה" in line_str:
                        start_y = y_r
                        continue
                    # סוף סעיף ד': שורה שמכילה אחת ממילות הסיום
                    if start_y is not None:
                        for ek in end_kws:
                            ek_words = ek.split()
                            if all(ew in line_str for ew in ek_words):
                                if end_y is None or y_r < end_y:
                                    end_y = y_r
                                break

                if start_y is None:
                    # Fallback: אם לא נמצאה כותרת — חפש ישירות מילות "מסלול" בכל העמוד
                    for w in words:
                        if "מסלול" in w[4] and not any(sk in w[4] for sk in ("השקעה","תשואות")):
                            start_y = w[1] - 5
                            break
                if start_y is None:
                    continue

                # סנן מילים לסעיף ד' בלבד
                sec_words = [
                    w for w in words
                    if w[1] >= start_y and (end_y is None or w[1] < end_y)
                ]

                # קבץ מילים לשורות לפי y0
                lines: dict[float, list] = {}
                for w in sec_words:
                    y = round(w[1], 0)
                    matched = next((k for k in lines if abs(k - y) <= Y_TOL), None)
                    key = matched if matched is not None else y
                    lines.setdefault(key, []).append(w)

                # עבור כל שורה ממוינת — מצא שורות מסלול
                for y_key in sorted(lines):
                    tokens = [w[4] for w in sorted(lines[y_key], key=lambda w: w[0])]
                    full   = " ".join(tokens)

                    if track_kw not in full:
                        continue
                    if any(sk in full for sk in skip_kw):
                        continue  # שורת כותרת

                    # חלץ: טוקנים מספריים = אחוז, השאר = שם מסלול
                    # PyMuPDF לפעמים מפריד "0.17" ו-"%" לשני טוקנים נפרדים
                    pct_tokens  = []
                    name_tokens = []
                    ti = 0
                    while ti < len(tokens):
                        tok = tokens[ti]
                        clean = tok.replace(",", ".").rstrip("%")
                        is_num = bool(re.match(r"^-?\d+[.,]\d+$", clean))
                        if is_num:
                            pct_tokens.append(clean)
                            # דלג על "%" שמגיע בטוקן נפרד מיד אחרי המספר
                            if ti + 1 < len(tokens) and tokens[ti + 1].strip() == "%":
                                ti += 1
                        elif tok.strip() == "%":
                            pass  # % בודד שכבר טופל
                        else:
                            name_tokens.append(tok)
                        ti += 1

                    percentage = pct_tokens[0] if pct_tokens else None

                    # אם לא נמצא אחוז — חפש בשורות הקרובות (y גדול יותר)
                    if percentage is None:
                        sorted_ys = sorted(lines.keys())
                        try:
                            cur_i = sorted_ys.index(y_key)
                        except ValueError:
                            cur_i = -1
                        for ny in sorted_ys[cur_i + 1: cur_i + 5]:
                            next_toks = [w[4] for w in sorted(lines[ny], key=lambda w: w[0])]
                            for tok in next_toks:
                                clean = tok.replace(",", ".").rstrip("%")
                                if num_re.match(tok) or re.match(r"^-?\d+[.,]\d+$", clean):
                                    percentage = clean
                                    break
                            if percentage:
                                break

                    track_name = " ".join(t for t in name_tokens if t.strip()).strip()
                    if track_name:
                        rows.append({
                            "track_name": track_name,
                            "return_percentage": percentage,
                        })

    except Exception as exc:
        st.warning(f"שגיאה בחילוץ טבלה ד' לפי קואורדינטות: {exc}")

    return rows


def build_table_d(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["track_name", "return_percentage"])
    df["return_percentage"] = df["return_percentage"].apply(clean_num)
    return df


def warn_table_d_sanity(table_d_df: pd.DataFrame, table_c_df: pd.DataFrame):
    """
    בדיקת שפיות לטבלה ד — מבוססת על כלל חוקי:
    דמי ניהול מחיסכון מוגבלים בחוק ל-0.5% מקסימום.
    לכן אם ערך בטבלה ג (דמי ניהול מחיסכון) עולה על 0.5% — כנראה שחולצה תשואה בטעות.
    בנוסף: אם ערך בטבלה ד זהה לאחד מהערכים בטבלה ג — כנראה שאותו מספר הועתק מהסעיף הלא נכון.
    """
    # בדיקה 1: דמי ניהול מחיסכון בטבלה ג לא יכולים לעלות על 0.5%
    if table_c_df is not None and not table_c_df.empty and "percentage" in table_c_df.columns:
        for _, row in table_c_df.iterrows():
            val = row.get("percentage")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            desc = str(row.get("description", "")).lower()
            if "חיסכון" in desc and float(val) > 0.5:
                st.warning(
                    f"⚠️ **בדיקת שפיות — טבלה ג:** דמי ניהול מחיסכון **{val}%** עולים על "
                    f"המקסימום החוקי של 0.5%. ייתכן שחולצה תשואה (טבלה ד) במקום דמי ניהול. "
                    f"אנא בדוק מול ה-PDF המקורי."
                )

    # בדיקה 2: ערך בטבלה ד זהה לערך בטבלה ג — סימן אזהרה לבלבול בין הסעיפים
    if table_d_df is None or table_d_df.empty or "return_percentage" not in table_d_df.columns:
        return
    if table_c_df is None or table_c_df.empty or "percentage" not in table_c_df.columns:
        return

    c_values = set(
        round(float(v), 4)
        for v in table_c_df["percentage"].dropna()
    )

    for _, row in table_d_df.iterrows():
        val = row.get("return_percentage")
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        if round(float(val), 4) in c_values:
            st.warning(
                f"⚠️ **בדיקת שפיות — טבלה ד:** התשואה **{val}%** עבור מסלול "
                f'"{row.get("track_name", "")}" זהה לאחד מערכי דמי הניהול בטבלה ג. '
                f"ייתכן בלבול בין סעיף ג לסעיף ד. אנא בדוק מול ה-PDF המקורי."
            )

def build_table_e(rows: list[dict]) -> pd.DataFrame:
    fixed = fix_table_e_shifts(rows)
    df = pd.DataFrame(fixed, columns=["month", "salary", "employee", "employer", "severance", "total"])
    for col in ["salary", "employee", "employer", "severance", "total"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────────────

TABLE_META = {
    "table_a": {
        "title": "Table A — תשלומים צפויים (Expected Payments)",
        "icon": "💰",
        "float_cols": ["amount"],
        "fmt": "{:,.2f}",
    },
    "table_b": {
        "title": "Table B — תנועות בקרן (Account Movements)",
        "icon": "🔄",
        "float_cols": ["amount"],
        "fmt": "{:,.2f}",
    },
    "table_c": {
        "title": "Table C — דמי ניהול והוצאות (Management Fees)",
        "icon": "📋",
        "float_cols": ["percentage"],
        "fmt": "{:.4f}%",
    },
    "table_d": {
        "title": "Table D — מסלולי השקעה ותשואות (Investment Tracks & Returns)",
        "icon": "📈",
        "float_cols": ["return_percentage"],
        "fmt": "{:.4f}%",
    },
    "table_e": {
        "title": "Table E — פירוט הפקדות (Monthly Deposit Details)",
        "icon": "🗂️",
        "float_cols": ["salary", "employee", "employer", "severance", "total"],
        "fmt": "{:,.2f}",
    },
}

def display_table(key: str, df: pd.DataFrame):
    meta = TABLE_META[key]
    st.subheader(f"{meta['icon']} {meta['title']}")

    if df.empty:
        st.info("No data found for this table in the report.")
        return

    # Build a styled copy for display (keeps underlying df clean)
    display_df = df.copy()
    for col in meta["float_cols"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: meta["fmt"].format(x) if pd.notna(x) else ""
            )

    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"Rows: {len(df)}")


# ──────────────────────────────────────────────────────────────────────────────
# Main app flow
# ──────────────────────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader(
    "📂 Upload Pension PDF Report",
    type=["pdf"],
    help="Supports reports from Migdal, Altshuler Shaham, Clal, Meitav, More.",
)

if uploaded_file:
    pdf_bytes = uploaded_file.read()

    # ── Step 1: Extract raw text ──
    with st.spinner("📄 Extracting text from PDF with PyMuPDF…"):
        try:
            raw_text = extract_pdf_text(pdf_bytes)
        except Exception:
            st.stop()

    with st.expander("🔍 Raw extracted text (debug)", expanded=False):
        st.text_area("Raw PDF Text", raw_text[:8000] + ("\n…[truncated]" if len(raw_text) > 8000 else ""),
                     height=300, label_visibility="collapsed")

    # ── Step 2: GPT-4o structuring ──
    client = openai.OpenAI(api_key=api_key)

    with st.spinner("🤖 Sending to GPT-4o for structured extraction…"):
        try:
            extracted: dict = call_openai(raw_text, client)
        except Exception:
            st.stop()

    with st.expander("📌 קטע 'מסלולי השקעה' מהטקסט הגולמי (debug)", expanded=False):
        _d_preview = extract_section(raw_text, "מסלולי השקעה")
        st.caption("הטקסט הגולמי של סעיף ד' — שימו לב שהמספרים עלולים להיות מבולגנים בגלל RTL. לכן משתמשים בקואורדינטות X,Y ולא בטקסט הזה.")
        st.text(_d_preview if _d_preview != raw_text else "⚠️ הכותרת 'מסלולי השקעה' לא נמצאה")

    with st.expander("🛠️ Raw JSON from GPT-4o (debug)", expanded=False):
        st.json(extracted)

    # ── Step 3: Build DataFrames ──
    builders = {
        "table_a": build_table_a,
        "table_b": build_table_b,
        "table_c": build_table_c,
        "table_e": build_table_e,
    }

    dfs: dict[str, pd.DataFrame] = {}
    for key, builder in builders.items():
        rows = extracted.get(key, [])
        try:
            dfs[key] = builder(rows) if rows else pd.DataFrame()
        except Exception as exc:
            st.warning(f"Could not build {key}: {exc}")
            dfs[key] = pd.DataFrame()

    # ── Table D: חילוץ לפי קואורדינטות X,Y — לא GPT, לא Regex על טקסט גולמי ──
    coord_rows_d = extract_table_d_by_coordinates(pdf_bytes)

    with st.expander("🔬 Debug טבלה ד' — תוצאות חילוץ קואורדינטות", expanded=True):
        if coord_rows_d:
            st.success(f"נמצאו {len(coord_rows_d)} שורות")
            st.json(coord_rows_d)
        else:
            st.error("לא נמצאו שורות — בדוק את ה-debug של הטקסט הגולמי למטה")

    if coord_rows_d:
        dfs["table_d"] = build_table_d(coord_rows_d)
        st.success(f"✅ טבלה ד' חולצה לפי קואורדינטות ({len(coord_rows_d)} מסלולים)")
    else:
        # fallback: GPT-4o אם החילוץ הגיאומטרי נכשל
        gpt_rows_d = extracted.get("table_d", [])
        dfs["table_d"] = build_table_d(gpt_rows_d) if gpt_rows_d else pd.DataFrame()
        st.warning("⚠️ חילוץ לפי קואורדינטות לא מצא מסלולים — נעשה שימוש בתוצאת GPT-4o כ-fallback")

    # ── Step 4: Cross-validation ──
    st.markdown("---")
    st.subheader("🔎 Cross-Validation: Table B ↔ Table E")
    cross_validate(dfs.get("table_b"), dfs.get("table_e"))

    # ── Step 5: Display all tables ──
    st.markdown("---")
    st.header("📊 Extracted Tables")

    for key in ["table_a", "table_b", "table_c", "table_d", "table_e"]:
        display_table(key, dfs[key])
        if key == "table_d":
            warn_table_d_sanity(dfs.get("table_d"), dfs.get("table_c"))
        st.markdown("---")

    # ── Step 6: Download as Excel ──
    with st.spinner("Preparing Excel export…"):
        excel_buffer = io.BytesIO()
        try:
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                sheet_names = {
                    "table_a": "A_Expected_Payments",
                    "table_b": "B_Account_Movements",
                    "table_c": "C_Management_Fees",
                    "table_d": "D_Investment_Tracks",
                    "table_e": "E_Deposit_Details",
                }
                for key, sheet in sheet_names.items():
                    df = dfs.get(key, pd.DataFrame())
                    if not df.empty:
                        df.to_excel(writer, sheet_name=sheet, index=False)
            excel_buffer.seek(0)
        except Exception as exc:
            st.warning(f"Excel export failed: {exc}")
            excel_buffer = None

    if excel_buffer:
        st.download_button(
            label="⬇️ Download all tables as Excel",
            data=excel_buffer,
            file_name=f"pension_report_{uploaded_file.name.replace('.pdf', '')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
else:
    st.info("👆 Upload a pension PDF report and enter your API key to get started.")
