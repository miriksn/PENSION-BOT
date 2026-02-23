import streamlit as st
import fitz
import os
import pandas as pd
import re
from collections import defaultdict

st.set_page_config(page_title="מנתח פנסיה - גירסה 33.0", layout="wide")
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;700&display=swap');
    * { font-family: 'Assistant', sans-serif; direction: rtl; text-align: right; }
    th, td { text-align: right !important; padding: 12px !important; white-space: nowrap; }
    .val-success { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
        background-color: #f0fdf4; border: 1px solid #16a34a; color: #16a34a; }
    .val-error { padding: 12px; border-radius: 8px; margin-bottom: 10px; font-weight: bold;
        background-color: #fef2f2; border: 1px solid #dc2626; color: #dc2626; }
</style>
""", unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════════════════════
# שלב 1 — חילוץ מילים עם קואורדינטות מדויקות
# ════════════════════════════════════════════════════════════════════════════════

def extract_words(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    words = []
    for pn, page in enumerate(doc):
        page_width = page.rect.width
        for w in page.get_text("words"):
            t = w[4].strip()
            if t:
                words.append({"page": pn, "x0": w[0], "y0": w[1],
                               "x1": w[2], "y1": w[3], "text": t,
                               "page_width": page_width})
    return words

# ════════════════════════════════════════════════════════════════════════════════
# שלב 2 — איתור כותרות הסעיפים + טווח ה-X שלהן
# ════════════════════════════════════════════════════════════════════════════════

SECTION_KEYWORDS = {
    "a": ["א. תשלומים צפויים מקרן הפנסיה",  "א . תשלומים צפויים מקרן הפנסיה"],
    "b": ["ב. תנועות בקרן הפנסיה",           "ב . תנועות בקרן הפנסיה"],
    "c": ["ג. אחוז דמי ניהול",               "ג . אחוז דמי ניהול"],
    "d": ["ד. מסלולי השקעה ותשואות",         "ד . מסלולי השקעה ותשואות"],
    "e": ["ה. פירוט הפקדות לקרן הפנסיה",    "ה . פירוט הפקדות לקרן הפנסיה"],
}

# טבלה א' — תמיד בדיוק 6 שורות, השורה האחרונה ידועה
TABLE_A_LAST_ROW = "שחרור מתשלום הפקדות לקרן במקרה של נכות"

def is_table_a_last_row(lt):
    return TABLE_A_LAST_ROW in lt

def find_sections(words):
    """
    מחזיר לכל סעיף: page, y0, x_min, x_max.
    מטפל במקרה שכמה כותרות נמצאות על אותה שורה (כמו ב' ו-ג' באלטשולר).
    """
    buckets = defaultdict(list)
    for w in words:
        bucket_y = round(w["y0"] / 6) * 6
        buckets[(w["page"], bucket_y)].append(w)

    sections = {}
    for (page, _), line_words in sorted(buckets.items()):
        lw_sorted = sorted(line_words, key=lambda w: w["x0"])
        # בנה טקסט בשני כיוונים
        ltr = " ".join(w["text"] for w in lw_sorted)
        rtl = " ".join(w["text"] for w in reversed(lw_sorted))

        for sec_id, kws in SECTION_KEYWORDS.items():
            if sec_id in sections:
                continue
            for kw in kws:
                if kw not in ltr and kw not in rtl:
                    continue

                # מצא את המילים שמרכיבות את הכותרת הספציפית
                # חפש את מילת המפתח הראשונה שמזהה את הסעיף (האות + הנקודה)
                anchor = kw.split()[0]  # "א." או "א ."
                anchor_clean = anchor.replace(" ", "").replace(".", "")

                # מצא את המילה המזהה בשורה
                kw_words = []
                found_anchor = False
                for w in lw_sorted:
                    wt = w["text"].replace(".", "").replace(" ", "")
                    if not found_anchor and wt == anchor_clean:
                        found_anchor = True
                    if found_anchor:
                        kw_words.append(w)
                        # עצור כשמגיעים לכותרת הבאה (אות עברית + נקודה)
                        if len(kw_words) > 1 and re.match(r'^[א-ת][\s\.]', w["text"]) and w != kw_words[0]:
                            kw_words.pop()
                            break

                if not kw_words:
                    kw_words = line_words  # fallback

                xs = [w["x0"] for w in kw_words] + [w["x1"] for w in kw_words]
                ys = [w["y0"] for w in kw_words]
                sections[sec_id] = {
                    "page":       page,
                    "y0":         min(ys),
                    "x_min":      min(xs),
                    "x_max":      max(xs),
                    "page_width": line_words[0]["page_width"]
                }
                break
    return sections

def get_section_x_range(sec_info, all_sections, margin=30):
    """
    קובע את טווח ה-X לאיסוף נתוני הסעיף.
    אם הכותרת מכסה יותר מ-60% מרוחב הדף — הסעיף full-width.
    אחרת — מרחיבים ב-margin לכל כיוון, ועוצרים לפי שכנים ב-X.
    """
    pw = sec_info["page_width"]
    sec_w = sec_info["x_max"] - sec_info["x_min"]

    if sec_w / pw > 0.6:
        # כותרת רחבה = full-width section
        return 0, pw

    # כותרת צרה = חלק מעמודה
    # מרחיבים, אבל לא חורגים לעמודות של סעיפים שכנים באותו עמוד
    left  = max(0,  sec_info["x_min"] - margin)
    right = min(pw, sec_info["x_max"] + margin)

    for other_id, other in all_sections.items():
        if other["page"] != sec_info["page"]: continue
        if other is sec_info: continue
        # אם סעיף אחר נמצא ממש לצד — מגביל
        if other["x_min"] > sec_info["x_max"]:  # שכן משמאל
            right = min(right, other["x_min"] - 5)
        if other["x_max"] < sec_info["x_min"]:  # שכן מימין
            left  = max(left,  other["x_max"] + 5)

    return left, right

# ════════════════════════════════════════════════════════════════════════════════
# שלב 3 — בניית שורות לכל סעיף (מסונן לפי X + Y)
# ════════════════════════════════════════════════════════════════════════════════

def build_section_lines(words, sections, sec_id, y_tol=3):
    """
    מחזיר שורות (רשימת מילים ממוינות) השייכות לסעיף נתון,
    מסוננות לפי טווח X של הסעיף ולפי טווח Y (בין כותרת הסעיף לכותרת הבאה).
    """
    if sec_id not in sections:
        return []

    sec = sections[sec_id]
    x_min, x_max = get_section_x_range(sec, sections)

    # מצא Y תחתון: ה-y0 הקטן ביותר של סעיף אחר שמתחיל מתחת לסעיף הנוכחי,
    # ונמצא בטווח ה-X שלו (כדי לא לחסום סעיפים בעמודה אחרת)
    y_end = float("inf")
    p_end = float("inf")
    for other_id, other in sections.items():
        if other_id == sec_id: continue
        # בדיקה אם הסעיף האחר נמצא מתחת ובאותה עמודה
        ox_min, ox_max = get_section_x_range(other, sections)
        x_overlap = min(x_max, ox_max) - max(x_min, ox_min)
        if other["page"] > sec["page"] or (other["page"] == sec["page"] and other["y0"] > sec["y0"]):
            if x_overlap > 20:  # יש חפיפה משמעותית ב-X → סעיפים באותה עמודה
                if (other["page"], other["y0"]) < (p_end, y_end):
                    p_end, y_end = other["page"], other["y0"]

    # איסוף מילים שעומדות בתנאים
    relevant = []
    for w in words:
        # סינון דף
        if w["page"] < sec["page"]: continue
        if w["page"] > p_end: continue
        # סינון Y
        if w["page"] == sec["page"] and w["y0"] <= sec["y0"]: continue
        if w["page"] == p_end and w["y0"] >= y_end: continue
        # סינון X
        w_x_mid = (w["x0"] + w["x1"]) / 2
        if w_x_mid < x_min or w_x_mid > x_max: continue
        relevant.append(w)

    # קיבוץ לשורות
    relevant.sort(key=lambda w: (w["page"], w["y0"], w["x0"]))
    lines, cur, cur_y = [], [], None
    for w in relevant:
        ym = (w["y0"] + w["y1"]) / 2
        if cur_y is None or abs(ym - cur_y) <= y_tol:
            cur.append(w)
            cur_y = ym if cur_y is None else (cur_y + ym) / 2
        else:
            if cur: lines.append(sorted(cur, key=lambda w: w["x0"]))
            cur, cur_y = [w], ym
    if cur: lines.append(sorted(cur, key=lambda w: w["x0"]))
    return lines

# ════════════════════════════════════════════════════════════════════════════════
# שלב 4 — כלי עזר
# ════════════════════════════════════════════════════════════════════════════════

def ltext(line):
    return " ".join(w["text"] for w in reversed(line))

def is_num(t):
    return bool(re.fullmatch(r'-?\d{1,3}(,\d{3})*(\.\d+)?|-?\d+(\.\d+)?', t.replace(",", "")))

def parse_num(t):
    try:
        cleaned = re.sub(r'[^\d\.\-]', '', t.replace(",", ""))
        return float(cleaned) if cleaned else None
    except:
        return None

def get_nums(line):
    """מספרים בשורה מסודרים לפי X מימין לשמאל, ללא אחוזים."""
    nums = []
    for w in reversed(line):
        t = w["text"].replace(",", "")
        if re.search(r'%', w["text"]): continue
        n = parse_num(t)
        if n is not None and re.search(r'\d', w["text"]):
            nums.append(n)
    return nums

def is_footnote(line):
    """שורה שמתחילה ב-* או ** = הערת שוליים."""
    lt = ltext(line).strip()
    return lt.startswith("*") or lt.startswith("**") or lt.startswith("1 ")

# ════════════════════════════════════════════════════════════════════════════════
# שלב 5 — חילוץ כל טבלה
# ════════════════════════════════════════════════════════════════════════════════

def extract_table_a(lines):
    rows = []
    AGE_RE = re.compile(r'\bבגיל\b')
    for line in lines:
        if is_footnote(line): continue
        lt = ltext(line)
        nums = get_nums(line)
        if not nums: continue
        # בשורה עם "בגיל" — הגיל הוא מספר קטן (< 100), הסכום גדול ממנו
        if AGE_RE.search(lt):
            nums = [n for n in nums if n >= 100]
        if not nums: continue
        amount = max(nums)
        desc_words = [w["text"] for w in reversed(line)
                      if not is_num(w["text"]) and not re.fullmatch(r'\d{2}', w["text"])]
        desc = " ".join(desc_words).strip()
        if desc:
            rows.append({"תיאור": desc, 'סכום בש"ח': f"{int(amount):,}"})
        # עצירה מוחלטת בשורה האחרונה הידועה — תמיד 6 שורות בלבד
        if is_table_a_last_row(lt):
            break
    return rows[:6]  # גיבוי: לא יותר מ-6 שורות בשום מקרה

def extract_table_b(lines):
    rows = []
    for line in lines:
        if is_footnote(line): continue
        lt = ltext(line)
        nums = get_nums(line)
        if not nums: continue
        desc_words = [w["text"] for w in reversed(line) if not is_num(w["text"])]
        desc = " ".join(desc_words).strip()
        if not desc: continue
        amount = max(nums, key=abs)
        # זיהוי שליליות
        raw = " ".join(w["text"] for w in line).replace(",", "")
        neg = bool(re.search(r'[-−]' + str(int(abs(amount))), raw))
        val = -abs(amount) if neg else amount
        rows.append({"תיאור": desc, 'סכום בש"ח': f"{int(val):,}"})
    return rows

def extract_table_c(lines):
    rows = []
    for line in lines:
        if is_footnote(line): continue
        lt = ltext(line)
        pct = re.search(r'(\d+\.\d+)%?', lt)
        if not pct: continue
        desc_words = [w["text"] for w in reversed(line)
                      if not re.search(r'\d+\.\d+', w["text"])]
        desc = " ".join(desc_words).strip()
        val = pct.group(1) + ("%" if "%" in lt else "%")
        if desc:
            rows.append({"תיאור": desc, "אחוז": val})
    return rows

def extract_table_d(lines):
    rows = []
    pending = None
    for line in lines:
        if is_footnote(line): continue
        lt = ltext(line)
        pct = re.search(r'-?\d+\.?\d*%', lt)
        if pct:
            name_words = [w["text"] for w in reversed(line)
                          if not re.search(r'-?\d+\.?\d*%', w["text"])
                          and not re.match(r'^\d+\.\d+$', w["text"])]
            name = " ".join(name_words).strip()
            if pending:
                name = (pending + " " + name).strip()
                pending = None
            if name:
                rows.append({"מסלול": name, "תשואה": pct.group(0)})
        elif lt.strip() and not any(c.isdigit() for c in lt):
            pending = (pending + " " + lt.strip()) if pending else lt.strip()
    return rows

def extract_table_e(lines, employer_fallback=""):
    DATE_FULL = re.compile(r'\d{2}/\d{2}/\d{4}')
    MONTH_RE  = re.compile(r'\d{2}/\d{4}')
    SKIP_HDRS = ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק",
                 "פיצויים", 'סה"כ', "הפקדה", "משכורת", "תגמולי", "עבור"]
    rows = []
    pending_emp = None

    for line in lines:
        lt = ltext(line)

        # שורת סיכום
        if 'סה"כ' in lt and not DATE_FULL.search(lt):
            nums = sorted([n for n in get_nums(line) if n > 0], reverse=True)
            if len(nums) >= 3:
                rows.append({
                    "שם המעסיק": 'סה"כ', "מועד": "", "חודש": "", "שכר": "",
                    'סה"כ':    f"{int(nums[0]):,}",
                    "פיצויים": f"{int(nums[1]):,}",
                    "מעסיק":   f"{int(nums[2]):,}",
                    "עובד":    f"{int(nums[3]):,}" if len(nums) > 3 else "0",
                })
            continue

        # שורת נתונים עם תאריך הפקדה
        date_m = DATE_FULL.search(lt)
        if date_m:
            deposit = date_m.group()
            months  = MONTH_RE.findall(lt)
            salary_month = months[-1] if months else ""
            nums = [n for n in get_nums(line) if n > 0]

            # שם מעסיק מהשורה (לפני התאריך, RTL)
            emp_words = []
            for w in reversed(line):
                if DATE_FULL.search(w["text"]) or MONTH_RE.search(w["text"]): break
                if not is_num(w["text"]): emp_words.append(w["text"])
            emp_inline = " ".join(emp_words).strip()

            employer = pending_emp or emp_inline or employer_fallback
            pending_emp = None

            if len(nums) >= 4:
                rows.append({
                    "שם המעסיק": employer,
                    "מועד":       deposit,
                    "חודש":       salary_month,
                    "שכר":        f"{int(nums[4]):,}" if len(nums) > 4 else "",
                    "עובד":       f"{int(nums[3]):,}",
                    "מעסיק":      f"{int(nums[2]):,}",
                    "פיצויים":    f"{int(nums[1]):,}",
                    'סה"כ':       f"{int(nums[0]):,}",
                })
            continue

        # שורת טקסט = שם מעסיק גולש
        if lt.strip() and not any(c.isdigit() for c in lt):
            if not any(h in lt for h in SKIP_HDRS):
                pending_emp = (pending_emp + " " + lt.strip()) if pending_emp else lt.strip()

    # תיקון שכר סיכום
    salary_sum = sum(
        float(str(r.get("שכר","0")).replace(",",""))
        for r in rows if r.get("מועד")
    )
    for r in rows:
        if r.get("שם המעסיק") == 'סה"כ':
            r["שכר"] = f"{int(salary_sum):,}"
    return rows

# ════════════════════════════════════════════════════════════════════════════════
# שלב 6 — שם מעסיק מכותרת
# ════════════════════════════════════════════════════════════════════════════════

def get_employer_from_header(words):
    buckets = defaultdict(list)
    for w in words:
        buckets[(w["page"], round(w["y0"]/4)*4)].append(w)
    for (page, _), lw in sorted(buckets.items()):
        lt = " ".join(w["text"] for w in reversed(sorted(lw, key=lambda w: w["x0"])))
        if "שם המעסיק" in lt:
            m = re.search(r'שם המעסיק[:\s]+(.+)', lt)
            if m:
                emp = re.sub(r'מספר ת\.ז.*', '', m.group(1)).strip()
                if emp: return emp
    return ""

# ════════════════════════════════════════════════════════════════════════════════
# אימות ותצוגה
# ════════════════════════════════════════════════════════════════════════════════

def cross_validate(table_b, table_e):
    dep_b = 0.0
    for r in table_b:
        if any(kw in str(r.get("תיאור","")) for kw in ["הופקדו","שהופקדו"]):
            try: dep_b = float(str(r.get('סכום בש"ח',0)).replace(",",""))
            except: pass
            break
    dep_e = 0.0
    if table_e:
        try: dep_e = float(str(table_e[-1].get('סה"כ',0)).replace(",",""))
        except: pass
    if abs(dep_b - dep_e) < 5 and dep_e > 0:
        st.markdown(f'<div class="val-success">✅ אימות הצלבה עבר: סכום ההפקדות ({dep_e:,.0f} ₪) תואם במדויק.</div>', unsafe_allow_html=True)
    elif dep_e > 0:
        st.markdown(f'<div class="val-error">⚠️ שגיאת אימות: טבלה ב\' ({dep_b:,.0f} ₪) לעומת טבלה ה\' ({dep_e:,.0f} ₪).</div>', unsafe_allow_html=True)

def show_table(rows, title, cols):
    if not rows:
        st.warning(f"{title} — לא נמצאו נתונים")
        return
    df = pd.DataFrame(rows)
    existing = [c for c in cols if c in df.columns]
    df = df[existing].fillna("")
    df.index = range(1, len(df)+1)
    st.subheader(title)
    st.table(df)

# ════════════════════════════════════════════════════════════════════════════════
# ממשק
# ════════════════════════════════════════════════════════════════════════════════

st.title("📋 חילוץ נתונים פנסיוני - גירסה 33.0")
st.caption("חילוץ לפי עמודות X + Y — תומך בפריסה רב-עמודתית")

file = st.file_uploader("העלה דוח PDF", type="pdf")
if file:
    fb = file.read()
    with st.spinner("מחלץ..."):
        words    = extract_words(fb)
        sections = find_sections(words)
        employer = get_employer_from_header(words)

        lines = {k: build_section_lines(words, sections, k) for k in "abcde"}

        ta = extract_table_a(lines["a"])
        tb = extract_table_b(lines["b"])
        tc = extract_table_c(lines["c"])
        td = extract_table_d(lines["d"])
        te = extract_table_e(lines["e"], employer_fallback=employer)

    cross_validate(tb, te)
    show_table(ta, "א. תשלומים צפויים",   ["תיאור", 'סכום בש"ח'])
    show_table(tb, "ב. תנועות בקרן",       ["תיאור", 'סכום בש"ח'])
    show_table(tc, "ג. דמי ניהול והוצאות", ["תיאור", "אחוז"])
    show_table(td, "ד. מסלולי השקעה",       ["מסלול", "תשואה"])
    show_table(te, "ה. פירוט הפקדות",
               ["שם המעסיק", "מועד", "חודש", "שכר", "עובד", "מעסיק", "פיצויים", 'סה"כ'])

    if st.checkbox("🔍 Debug — שורות לפי סעיף"):
        sec_names = {"a":"א","b":"ב","c":"ג","d":"ד","e":"ה"}
        
        # אם אף סעיף לא נמצא — הצג טקסט גולמי לאבחון
        if not sections:
            st.error("⚠️ לא נמצאה אף כותרת סעיף! הצגת 50 השורות הראשונות של הדוח:")
            buckets = defaultdict(list)
            for w in words:
                buckets[(w["page"], round(w["y0"]/6)*6)].append(w)
            for i, ((page, _), lw) in enumerate(sorted(buckets.items())):
                rtl = " ".join(w["text"] for w in sorted(lw, key=lambda w: -w["x0"]))
                st.text(f"עמוד {page} | {rtl}")
                if i > 50: break
        else:
            for k in "abcde":
                sec = sections.get(k, {})
                xr = get_section_x_range(sec, sections) if sec else (0,0)
                with st.expander(f"סעיף {sec_names[k]} — {len(lines[k])} שורות | X: {xr[0]:.0f}–{xr[1]:.0f}"):
                    for ln in lines[k]:
                        st.text(ltext(ln))
