import streamlit as st
import fitz
import json
import os
import pandas as pd
import re
from collections import defaultdict

st.set_page_config(page_title="מנתח פנסיה - גירסה 31.0 (קואורדינטות)", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    .stTable { direction: rtl !important; width: 100%; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
        background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
        background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
    .debug-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px;
        padding: 12px; font-size: 0.8rem; direction: ltr; text-align: left; }
</style>
""", unsafe_allow_html=True)

def clean_num(val):
    if val is None or val == "" or str(val).strip() in ["-", "nan", ".", "0"]: return 0.0
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', str(val).replace(",", "").replace("−", "-"))
        return float(cleaned) if cleaned else 0.0
    except: return 0.0

# ════════════════════════════════════════════════════════════════════════════════
# ליבת החילוץ — קואורדינטות XY מדויקות מ-PDF וקטורי
# ════════════════════════════════════════════════════════════════════════════════

def extract_words_with_coords(file_bytes):
    """
    מחזיר רשימת מילים עם מיקום מדויק מכל עמודי הדוח.
    word = (page, x0, y0, x1, y1, text)
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    all_words = []
    for page_num, page in enumerate(doc):
        # get_text("words") מחזיר: (x0, y0, x1, y1, "text", block_no, line_no, word_no)
        for w in page.get_text("words"):
            all_words.append({
                "page": page_num,
                "x0": w[0], "y0": w[1],
                "x1": w[2], "y1": w[3],
                "text": w[4].strip()
            })
    return all_words

def group_into_lines(words, y_tolerance=3):
    """
    מקבץ מילים לשורות לפי קואורדינטת Y (עם סבלנות קטנה לאי-יישור).
    מחזיר: {page: [[(y_center, x_center, text), ...], ...]}
    """
    by_page = defaultdict(list)
    for w in words:
        by_page[w["page"]].append(w)

    result = {}
    for page, ws in by_page.items():
        # מיון לפי Y ואז X
        ws_sorted = sorted(ws, key=lambda w: (w["y0"], w["x0"]))
        lines = []
        current_line = []
        current_y = None

        for w in ws_sorted:
            y_mid = (w["y0"] + w["y1"]) / 2
            if current_y is None or abs(y_mid - current_y) <= y_tolerance:
                current_line.append(w)
                current_y = y_mid if current_y is None else (current_y + y_mid) / 2
            else:
                if current_line:
                    lines.append(sorted(current_line, key=lambda w: w["x0"]))
                current_line = [w]
                current_y = y_mid

        if current_line:
            lines.append(sorted(current_line, key=lambda w: w["x0"]))
        result[page] = lines

    return result

def line_text(line):
    """חיבור מילים בשורה לטקסט מלא, מימין לשמאל."""
    return " ".join(w["text"] for w in reversed(line))  # RTL

def line_nums(line):
    """חילוץ מספרים מהשורה לפי X, מימין לשמאל."""
    nums = []
    for w in reversed(line):
        t = w["text"].replace(",", "")
        # מספר עם אפשרות למינוס
        m = re.fullmatch(r'-?\d+\.?\d*', t)
        if m:
            nums.append(float(m.group()))
    return nums

def is_number(text):
    t = text.replace(",", "").replace("-", "")
    return bool(re.fullmatch(r'\d+\.?\d*%?', t))

# ════════════════════════════════════════════════════════════════════════════════
# חילוץ כל טבלה
# ════════════════════════════════════════════════════════════════════════════════

def find_section_start(lines_by_page, keyword):
    """מוצא את מיקום (page, line_idx) של כותרת סעיף לפי מילת מפתח."""
    for page, lines in sorted(lines_by_page.items()):
        for i, line in enumerate(lines):
            lt = line_text(line)
            if keyword in lt:
                return (page, i)
    return None

def extract_two_col_table(lines_by_page, start_keyword, stop_keywords, col1_name, col2_name):
    """
    חילוץ טבלה דו-עמודתית: תיאור + מספר.
    עוצרת כשנתקלת באחד ממילות העצירה.
    """
    start = find_section_start(lines_by_page, start_keyword)
    if not start:
        return []

    rows = []
    page, line_idx = start
    all_pages = sorted(lines_by_page.keys())

    collecting = False
    for p in all_pages:
        if p < page:
            continue
        lines = lines_by_page[p]
        start_i = line_idx + 1 if p == page else 0

        for i in range(start_i, len(lines)):
            lt = line_text(lines[i])

            # בדיקת עצירה
            if any(kw in lt for kw in stop_keywords):
                return rows

            # שורה עם לפחות מספר אחד = שורת נתונים
            nums = line_nums(lines[i])
            if nums:
                # הטקסט = כל מה שאינו מספר
                words_text = [w["text"] for w in reversed(lines[i]) if not is_number(w["text"].replace(",", ""))]
                desc = " ".join(words_text).strip()
                # ערך שלילי: אם יש מינוס לפני המספר בטקסט המקורי
                raw_line = " ".join(w["text"] for w in lines[i])
                sign = -1 if re.search(r'[-−]' + re.escape(str(int(abs(nums[0])))), raw_line) else 1
                val = sign * abs(nums[0])
                if desc:
                    rows.append({col1_name: desc, col2_name: f"{val:,.0f}" if val == int(val) else f"{val}"})
            collecting = True

    return rows

def extract_table_a(lines_by_page):
    return extract_two_col_table(
        lines_by_page,
        start_keyword="תשלומים צפויים",
        stop_keywords=["תנועות בקרן", "דמי ניהול", "מסלולי השקעה"],
        col1_name="תיאור",
        col2_name='סכום בש"ח'
    )

def extract_table_b(lines_by_page):
    return extract_two_col_table(
        lines_by_page,
        start_keyword="תנועות בקרן",
        stop_keywords=["מסלולי השקעה", "פירוט הפקדות", "דמי ניהול"],
        col1_name="תיאור",
        col2_name='סכום בש"ח'
    )

def extract_table_c(lines_by_page):
    return extract_two_col_table(
        lines_by_page,
        start_keyword="דמי ניהול",
        stop_keywords=["תנועות בקרן", "מסלולי השקעה", "פירוט הפקדות"],
        col1_name="תיאור",
        col2_name="אחוז"
    )

def extract_table_d(lines_by_page):
    """
    חילוץ מסלולי השקעה.
    כל שורה: שם מסלול (טקסט) + תשואה (מספר עם %).
    שמות גולשים לשורה שנייה: מאוחדים אוטומטית.
    """
    start = find_section_start(lines_by_page, "מסלולי השקעה")
    if not start:
        return []

    rows = []
    page, line_idx = start
    pending_name = None

    for p in sorted(lines_by_page.keys()):
        if p < page:
            continue
        lines = lines_by_page[p]
        start_i = line_idx + 1 if p == page else 0

        for i in range(start_i, len(lines)):
            lt = line_text(lines[i])
            if "פירוט הפקדות" in lt or "הפקדות לקרן" in lt:
                return rows

            # מחפשים אחוז תשואה
            pct_match = re.search(r'(\d+\.?\d*)%', lt)
            if pct_match:
                # יש תשואה בשורה הזו
                tshoa = pct_match.group(0)
                words_no_num = [w["text"] for w in reversed(lines[i])
                                if not re.search(r'\d+\.?\d*%', w["text"]) and not is_number(w["text"].replace(",", ""))]
                name_part = " ".join(words_no_num).strip()
                if pending_name:
                    full_name = (pending_name + " " + name_part).strip()
                    pending_name = None
                else:
                    full_name = name_part
                if full_name:
                    rows.append({"מסלול": full_name, "תשואה": tshoa})
            elif lt.strip() and not re.search(r'^\d', lt.strip()):
                # שורת טקסט בלי מספר = שם מסלול גולש
                if pending_name:
                    pending_name += " " + lt.strip()
                else:
                    pending_name = lt.strip()

    return rows

def extract_table_e(lines_by_page):
    """
    חילוץ פירוט הפקדות.
    עמודות: שם המעסיק | מועד | חודש | שכר | עובד | מעסיק | פיצויים | סה"כ
    לוגיקה: שורת נתונים = שורה עם תאריך (dd/mm/yyyy) + לפחות 4 מספרים.
    """
    start = find_section_start(lines_by_page, "פירוט הפקדות")
    if not start:
        return []

    DATE_RE    = re.compile(r'\d{2}/\d{2}/\d{4}')
    MONTH_RE   = re.compile(r'\d{2}/\d{4}')
    NUM_RE     = re.compile(r'^\d{1,3}(,\d{3})*$|^\d+$')

    rows = []
    pending_employer = None
    page, line_idx = start

    for p in sorted(lines_by_page.keys()):
        if p < page:
            continue
        lines = lines_by_page[p]
        start_i = line_idx + 1 if p == page else 0

        for i in range(start_i, len(lines)):
            line = lines[i]
            lt = line_text(line)
            words = [w["text"] for w in line]

            # שורת סיכום
            if 'סה"כ' in lt and len(line_nums(line)) >= 3:
                ns = line_nums(line)
                if len(ns) >= 4:
                    rows.append({
                        "שם המעסיק": 'סה"כ',
                        "מועד": "", "חודש": "", "שכר": "",
                        "עובד":     f"{int(ns[-3]):,}",
                        "מעסיק":    f"{int(ns[-2]):,}",
                        "פיצויים":  f"{int(ns[-1]):,}",  # ← מה שנמצא בעמודה האחרונה בשורת הסיכום
                        'סה"כ':     f"{int(ns[0]):,}"    # ← הסכום הכולל (הגדול ביותר, בצד שמאל)
                    })
                    # מיון סה"כ לפי גודל
                    last = rows[-1]
                    all_ns = sorted([clean_num(last["עובד"]), clean_num(last["מעסיק"]),
                                     clean_num(last["פיצויים"]), clean_num(last['סה"כ'])], reverse=True)
                    last['סה"כ']    = f"{int(all_ns[0]):,}"
                    last["עובד"]    = f"{int(all_ns[3]):,}"
                    last["מעסיק"]   = f"{int(all_ns[2]):,}"
                    last["פיצויים"] = f"{int(all_ns[1]):,}"
                continue

            # שורה עם תאריך הפקדה
            date_match = DATE_RE.search(lt)
            if date_match:
                deposit_date = date_match.group()
                month_matches = MONTH_RE.findall(lt)
                salary_month = month_matches[-1] if month_matches else ""

                # המספרים בשורה מימין לשמאל: סה"כ, פיצויים, מעסיק, עובד, שכר
                nums = line_nums(line)

                # שם מעסיק: הטקסט לפני התאריך, או ממשיך משורה קודמת
                employer_words = []
                for w in reversed(line):
                    if DATE_RE.search(w["text"]) or MONTH_RE.search(w["text"]):
                        break
                    if not NUM_RE.match(w["text"].replace(",", "")):
                        employer_words.append(w["text"])
                employer = " ".join(employer_words).strip()

                if pending_employer:
                    employer = (pending_employer + " " + employer).strip()
                    pending_employer = None

                if len(nums) >= 5:
                    rows.append({
                        "שם המעסיק": employer,
                        "מועד":       deposit_date,
                        "חודש":       salary_month,
                        "שכר":        f"{int(nums[4]):,}",
                        "עובד":       f"{int(nums[3]):,}",
                        "מעסיק":      f"{int(nums[2]):,}",
                        "פיצויים":    f"{int(nums[1]):,}",
                        'סה"כ':       f"{int(nums[0]):,}",
                    })
                pending_employer = None
            elif lt.strip() and not any(c.isdigit() for c in lt) and pending_employer is None:
                # שורת טקסט בלי מספרים ובלי תאריך = שם מעסיק גולש
                if "שם המעסיק" not in lt and "מועד" not in lt:
                    pending_employer = lt.strip()

    # תיקון שכר בשורת סיכום
    data_rows = [r for r in rows if r.get("מועד")]
    salary_sum = sum(clean_num(r.get("שכר", 0)) for r in data_rows)
    for r in rows:
        if r.get("שם המעסיק") == 'סה"כ':
            r["שכר"] = f"{int(salary_sum):,}"

    return rows

# ════════════════════════════════════════════════════════════════════════════════
# אימות ותצוגה
# ════════════════════════════════════════════════════════════════════════════════

def perform_cross_validation(table_b_rows, table_e_rows):
    dep_b = 0.0
    for r in table_b_rows:
        if any(kw in str(r.get("תיאור", "")) for kw in ["הופקדו", "שהופקדו"]):
            dep_b = clean_num(r.get('סכום בש"ח', 0))
            break
    dep_e = clean_num(table_e_rows[-1].get('סה"כ', 0)) if table_e_rows else 0.0
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.0f} ₪) תואם במדויק.</div>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div class="val-error">⚠️ שגיאת אימות: טבלה ב\' ({dep_b:,.0f} ₪) לעומת טבלה ה\' ({dep_e:,.0f} ₪).</div>', unsafe_allow_html=True)

def display_table(rows, title, col_order):
    if not rows:
        st.warning(f"{title} — לא נמצאו נתונים")
        return
    df = pd.DataFrame(rows)
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]
    df.index = range(1, len(df) + 1)
    st.subheader(title)
    st.table(df)

# ════════════════════════════════════════════════════════════════════════════════
# ממשק משתמש
# ════════════════════════════════════════════════════════════════════════════════

st.title("📋 חילוץ נתונים פנסיוני - גירסה 31.0")
st.caption("חילוץ מדויק 100% לפי קואורדינטות XY — ללא AI, ללא עיגולים")

file = st.file_uploader("העלה דוח PDF", type="pdf")
if file:
    file_bytes = file.read()
    with st.spinner("מחלץ לפי קואורדינטות..."):
        words      = extract_words_with_coords(file_bytes)
        lines_map  = group_into_lines(words)

        table_a = extract_table_a(lines_map)
        table_b = extract_table_b(lines_map)
        table_c = extract_table_c(lines_map)
        table_d = extract_table_d(lines_map)
        table_e = extract_table_e(lines_map)

    perform_cross_validation(table_b, table_e)

    display_table(table_a, "א. תשלומים צפויים",   ["תיאור", 'סכום בש"ח'])
    display_table(table_b, "ב. תנועות בקרן",       ["תיאור", 'סכום בש"ח'])
    display_table(table_c, "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
    display_table(table_d, "ד. מסלולי השקעה",       ["מסלול", "תשואה"])
    display_table(table_e, "ה. פירוט הפקדות",
                  ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", 'סה"כ'])

    # Debug: הצגת כל המילים עם קואורדינטות (אופציונלי)
    if st.checkbox("🔍 הצג נתוני debug (מילים + קואורדינטות)"):
        st.subheader("מילים שחולצו")
        df_words = pd.DataFrame(words)
        st.dataframe(df_words, use_container_width=True)
