from datetime import date, timedelta

SCHOOL_TERMS = [
    {"term": 1, "open": (5, 16), "close": (10, 11)},
    {"term": 2, "open": (11, 1), "close": (4, 1)},
]

HOLY_DAYS = ["วันมาฆบูชา", "วันวิสาขบูชา", "วันอาสาฬหบูชา", "วันเข้าพรรษา", "วันออกพรรษา"]

BAN_EXEMPT_VENUES = [
    "ท่าอากาศยานนานาชาติ",
    "สถานบริการตามกฎหมายสถานบริการ",
    "โรงแรมตามกฎหมายโรงแรม",
    "สถานที่ในโซนส่งเสริมการท่องเที่ยว",
    "สถานที่จัดงานระดับชาติ/นานาชาติ",
]

DAILY_SALE_WINDOWS = [
    {
        "effective_from": date(1972, 1, 1),
        "windows": [("11:00", "14:00"), ("17:00", "24:00")],
        "note": "ประกาศคณะปฏิวัติ 253/2515 — ห้ามขาย 14:00-17:00 และ 00:00-11:00",
    },
]


def _sale_windows(d: date) -> dict:
    active = [w for w in DAILY_SALE_WINDOWS if w["effective_from"] <= d]
    return max(active, key=lambda w: w["effective_from"])


def sale_windows_on(d: date, holy_days: dict[date, str] | None = None) -> list[tuple[str, str]]:
    if holy_days:
        for ban_d, name in holy_days.items():
            if ban_d == d and any(h in name for h in HOLY_DAYS) and "ชดเชย" not in name:
                return []
    return _sale_windows(d)["windows"]


def shift_to_monday(d: date) -> date:
    if d.weekday() == 5:
        return d + timedelta(days=2)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def school_term_rows(year: int) -> list[dict]:
    rows = []
    for t in SCHOOL_TERMS:
        open_d = shift_to_monday(date(year, *t["open"]))
        close_year = year + 1 if t["close"][0] < t["open"][0] else year
        close_d = date(close_year, *t["close"])
        rows.append({
            "event_date": open_d.isoformat(), "event_name": f"เปิดภาคเรียนที่ {t['term']}",
            "category": "โรงเรียน", "impact_level": "High",
            "impact_description": f"เปิดเทอม {t['term']} — นักเรียนกลับเข้าเรียน ลูกค้ากลางวันเพิ่ม",
            "is_ongoing": False, "source": "สพฐ. (ระเบียบปีการศึกษา)", "news_content": "",
        })
        rows.append({
            "event_date": close_d.isoformat(), "event_name": f"ปิดภาคเรียนที่ {t['term']}",
            "category": "โรงเรียน", "impact_level": "High",
            "impact_description": f"ปิดเทอม {t['term']} — ลูกค้ากลางวันลด ครอบครัวออกเที่ยวมากขึ้น",
            "is_ongoing": False, "source": "สพฐ. (ระเบียบปีการศึกษา)", "news_content": "",
        })
    return rows


def is_school_open(d: date) -> bool:
    for t in SCHOOL_TERMS:
        open_d = shift_to_monday(date(d.year, *t["open"]))
        if t["close"][0] < t["open"][0]:
            if d >= open_d or d <= date(d.year, *t["close"]):
                return True
        elif open_d <= d <= date(d.year, *t["close"]):
            return True
    return False


def derive_awk_phansa(khao_phansa: date) -> date:
    return khao_phansa + timedelta(days=88)


def alcohol_ban_rows(holy_days: dict[date, str], year: int) -> list[dict]:
    """เก็บไว้เพื่อความเข้ากันได้ย้อนหลัง — run() ใหม่ใช้ label แทน event แยก"""
    return [{
        "event_date": d.isoformat(),
        "event_name": f"ห้ามขายแอลกอฮอล์: {name}",
        "category": "กฎหมายแอลกอฮอล์",
        "impact_level": "Very High",
        "impact_description": f"{name} — ห้ามขายเครื่องดื่มแอลกอฮอล์ตลอด 24 ชม.",
        "is_ongoing": False,
        "source": "พ.ร.บ.ควบคุมเครื่องดื่มแอลกอฮอล์",
        "news_content": "",
    } for d, name in sorted(_all_ban_days(holy_days, year).items())]


def holiday_rows(holidays: dict[date, str], year: int, ban_days: dict[date, str]) -> list[dict]:
    """วันหยุด/เทศกาลจาก ICS + วันพระใหญ่ที่ ICS ไม่มี — ติด label ห้ามขายแอลกอฮอล์"""
    rows = []
    seen = set()

    for d, name in sorted(holidays.items()):
        if d.year != year:
            continue
        is_cultural = any(k in name for k in CULTURAL_KEYWORDS)
        seen.add(d)
        rows.append({
            "event_date": d.isoformat(),
            "event_name": name,
            "category": "วันเทศกาล" if is_cultural else "วันหยุดราชการ",
            "impact_level": "Medium" if is_cultural else "High",
            "impact_description": "วันเทศกาล — คนออกมาใช้จ่าย" if is_cultural
                                   else "วันหยุดราชการ — ร้านในย่านออฟฟิศเงียบ ห้างคึกคัก",
            "is_ongoing": False,
            "alcohol_ban": d in ban_days,
            "source": "Google Calendar (ICS)",
            "news_content": "",
        })

    # วันเข้าพรรษา/ออกพรรษาไม่มีใน ICS — ต้องสร้าง event เอง ไม่งั้น label ห้ามขายเหล้าไม่มีที่เกาะ
    for d, name in sorted(ban_days.items()):
        if d in seen:
            continue
        rows.append({
            "event_date": d.isoformat(),
            "event_name": name,
            "category": "วันสำคัญทางศาสนา",
            "impact_level": "High",
            "impact_description": f"{name} — ห้ามขายแอลกอฮอล์ตลอดวัน",
            "is_ongoing": False,
            "alcohol_ban": True,
            "source": "คำนวณจากวันอาสาฬหบูชา",
            "news_content": "",
        })
    return rows


def run(year: int | None = None, holy_days: dict | None = None, verbose: bool = True) -> list[dict]:
    """แถว fact_event — วันหยุด/เทศกาล + เปิดปิดภาคเรียน พร้อม label ห้ามขายแอลกอฮอล์

    ห้ามขายแอลกอฮอล์เป็น "label" บน event ไม่ใช่ event แยก — เดิมทำให้ 3 มี.ค.
    มี 2 แถว (วันมาฆบูชา + ห้ามขายแอลกอฮอล์: วันมาฆบูชา) ซ้ำซ้อนกันเอง
    ไม่มี label = ขายได้ตามเวลาปกติ (ห้ามขายเกิดแค่ 5 วัน/ปี ซึ่งมี event ครบทุกวัน)
    """
    y = year or date.today().year
    if holy_days is None:
        holy_days = fetch_holy_days()

    ban_days = _all_ban_days(holy_days, y)
    rows = holiday_rows(holy_days, y, ban_days)

    for r in school_term_rows(y):
        r["alcohol_ban"] = date.fromisoformat(r["event_date"]) in ban_days
        rows.append(r)

    rows.sort(key=lambda r: r["event_date"])

    if verbose:
        print("\n" + "=" * 50)
        print(f"📅 MODULE: ปฏิทินวันหยุด/ภาคเรียน — {y}")
        print("=" * 50)
        for r in rows:
            ban = " 🚫เหล้า" if r.get("alcohol_ban") else ""
            print(f"  {r['event_date']}  [{r['category']:14}] {r['event_name']}{ban}")
        print(f"  📊 รวม {len(rows)} แถว | ห้ามขายแอลกอฮอล์ {sum(1 for r in rows if r.get('alcohol_ban'))} วัน")
    return rows


def fetch_holy_days() -> dict[date, str]:
    import requests
    from icalendar import Calendar

    url = ("https://calendar.google.com/calendar/ical/"
           "th.th%23holiday%40group.v.calendar.google.com/public/basic.ics")
    gcal = Calendar.from_ical(requests.get(url, timeout=15).text)
    out = {}
    for comp in gcal.walk():
        if comp.name == "VEVENT":
            dt = comp.get("dtstart").dt
            out[dt if isinstance(dt, date) else dt.date()] = str(comp.get("summary"))
    return out


# วันเทศกาลที่ไม่ใช่วันหยุดราชการ — แยกจากวันหยุดจริงด้วย keyword
CULTURAL_KEYWORDS = ["วาเลนไทน์", "ตรุษจีน", "คริสต์มาส", "ลอยกระทง", "สารทจีน", "ฮาโลวีน"]


def _all_ban_days(holidays: dict[date, str], year: int) -> dict[date, str]:
    """5 วันพระใหญ่ที่ห้ามขายเหล้า — เติมเข้าพรรษา/ออกพรรษาที่ ICS ไม่มี"""
    ban = {d: h for d, n in holidays.items() if d.year == year
           for h in HOLY_DAYS if h in n and "ชดเชย" not in n}

    asalha = next((d for d, n in ban.items() if n == "วันอาสาฬหบูชา"), None)
    khao = next((d for d, n in ban.items() if n == "วันเข้าพรรษา"), None)
    if khao is None and asalha:
        khao = asalha + timedelta(days=1)
        ban[khao] = "วันเข้าพรรษา"
    if khao and not any(n == "วันออกพรรษา" for n in ban.values()):
        ban[derive_awk_phansa(khao)] = "วันออกพรรษา"
    return ban


def format_rows(rows: list[dict], start: date, end: date) -> dict:
    """แปลงแถว fact_event → ปฏิทินรายวัน ติด tag ครบ

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — รับเฉพาะสิ่งที่ scrape มา (วันหยุด/ห้ามขายเหล้า)
    ส่วน เปิดเทอม/เสาร์อาทิตย์ คำนวณสดตรงนี้ เพราะเป็นเลขคณิตล้วน ไม่ต้องเก็บซ้ำใน DB
    """
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(_iso(r["event_date"]), []).append(r)

    days, cur = [], start
    while cur <= end:
        key = cur.isoformat()
        events = by_date.get(key, [])
        banned = any(e.get("alcohol_ban") for e in events)
        is_weekend = cur.weekday() >= 5
        is_public = any(e["category"] == "วันหยุดราชการ" for e in events)
        school = is_school_open(cur) and not is_weekend and not is_public

        tags = [e["category"] for e in events]
        if is_weekend:
            tags.append("เสาร์อาทิตย์")
        tags.append("เปิดเทอม" if school else "ปิดเทอม")
        if banned:
            tags.append("ห้ามขายแอลกอฮอล์ทั้งวัน")

        days.append({
            "วันที่": key,
            "วัน": ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"][cur.weekday()],
            "เหตุการณ์": [e["event_name"] for e in events],
            "เปิดเทอม": school,
            "ขายแอลกอฮอล์ได้": [] if banned else _sale_windows(cur)["windows"],
            "tags": list(dict.fromkeys(tags)),
        })
        cur += timedelta(days=1)

    return {
        "ช่วงวันที่": f"{start.isoformat()} ถึง {end.isoformat()}",
        "จำนวนวัน": len(days),
        "จำนวนวันที่มีเหตุการณ์": sum(1 for d in days if d["เหตุการณ์"]),
        "ปฏิทิน": days,
    }


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def day_status(d: date, holidays: dict[date, str], ban_days: dict[date, str]) -> dict:
    """สถานะของวันเดียว — วันหยุด/เปิดเทอม/ขายเหล้า พร้อม tag"""
    name = holidays.get(d, "")
    is_cultural = any(k in name for k in CULTURAL_KEYWORDS)
    is_weekend = d.weekday() >= 5
    is_public = bool(name) and not is_cultural
    banned = d in ban_days
    school = is_school_open(d) and not is_weekend and not is_public

    tags = []
    if is_public:
        tags.append("วันหยุดราชการ")
    if is_cultural:
        tags.append("วันเทศกาล")
    if is_weekend:
        tags.append("เสาร์อาทิตย์")
    tags.append("เปิดเทอม" if school else "ปิดเทอม")
    if banned:
        tags.append("ห้ามขายแอลกอฮอล์ทั้งวัน")

    return {
        "วันที่": d.isoformat(),
        "วัน": ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"][d.weekday()],
        "ชื่อวันหยุด": name or None,
        "เปิดเทอม": school,
        "ขายแอลกอฮอล์ได้": [] if banned else _sale_windows(d)["windows"],
        "tags": tags,
    }


def summary(target_date: date | None = None, year: int | None = None,
            holidays: dict | None = None) -> dict:
    """สถานะรายวัน — ระบุวันเดียว หรือทั้งปี (เฉพาะวันที่มีอะไรพิเศษ)

    target_date : สถานะของวันนั้นวันเดียว
    year        : ปฏิทินทั้งปี เอาเฉพาะวันที่ไม่ใช่วันทำงานธรรมดา
    """
    if holidays is None:
        holidays = fetch_holy_days()

    if target_date or not year:
        d = target_date or date.today()
        return day_status(d, holidays, _all_ban_days(holidays, d.year))

    ban = _all_ban_days(holidays, year)
    days, cur = [], date(year, 1, 1)
    term_marks = {r["event_date"]: r["event_name"] for r in school_term_rows(year)}

    while cur.year == year:
        st = day_status(cur, holidays, ban)
        if cur.isoformat() in term_marks:
            st["tags"].append(term_marks[cur.isoformat()])
        # เก็บเฉพาะวันที่มีอะไรพิเศษ — วันทำงานธรรมดาไม่ต้องลิสต์
        if st["ชื่อวันหยุด"] or cur in ban or cur.isoformat() in term_marks:
            days.append(st)
        cur += timedelta(days=1)

    return {
        "ปี": year,
        "จำนวนวันที่มีเหตุการณ์": len(days),
        "วันห้ามขายแอลกอฮอล์": [d.isoformat() for d in sorted(ban)],
        "ปฏิทิน": days,
    }


def demo():
    # วันประกาศจริงจากปฏิทินไทย — (อาสาฬหบูชา, เข้าพรรษา, ออกพรรษา)
    # ไม่มี API ไหนให้ครบ (Google ICS มีเข้าพรรษาแค่ 2021-2022, ออกพรรษาไม่มีเลย
    # Nager.Date/OpenHolidays ไม่รองรับไทย) จึงคำนวณเอง แล้วตรึงด้วยปีที่รู้คำตอบ
    KNOWN = [
        (date(2021, 7, 24), date(2021, 7, 25), date(2021, 10, 21)),
        (date(2022, 7, 13), date(2022, 7, 14), date(2022, 10, 10)),
        (date(2023, 8, 1), date(2023, 8, 2), date(2023, 10, 29)),
        (date(2024, 7, 20), date(2024, 7, 21), date(2024, 10, 17)),
        (date(2025, 7, 10), date(2025, 7, 11), date(2025, 10, 7)),
    ]
    for asalha, khao, awk in KNOWN:
        # เข้าพรรษา = แรม 1 ค่ำ เดือน 8 = วันถัดจากอาสาฬหบูชาเสมอ
        derived_khao = asalha + timedelta(days=1)
        assert derived_khao == khao, f"เข้าพรรษา {asalha.year}: ได้ {derived_khao} ควรเป็น {khao}"
        got = derive_awk_phansa(khao)
        assert got == awk, f"ออกพรรษา {khao.year}: ได้ {got} ควรเป็น {awk}"

    # ปีอธิกมาส (2023 มีเดือน 8 สองหน) ต้องไม่กระทบ เพราะอาสาฬหบูชาเลื่อนไปแล้ว
    assert derive_awk_phansa(date(2023, 8, 2)) == date(2023, 10, 29)

    terms = school_term_rows(2026)
    assert len(terms) == 4, f"ภาคเรียนต้องมี 4 แถว ได้ {len(terms)}"
    assert terms[3]["event_date"] == "2027-04-01", "ภาคเรียน 2 ต้องปิดปีถัดไป"

    assert terms[0]["event_date"] == "2026-05-18", f"เปิดเทอม 1 ต้องเลื่อน ได้ {terms[0]['event_date']}"
    assert terms[2]["event_date"] == "2026-11-02", f"เปิดเทอม 2 ต้องเลื่อน ได้ {terms[2]['event_date']}"
    assert shift_to_monday(date(2027, 5, 16)).isoformat() == "2027-05-17"
    assert shift_to_monday(date(2028, 5, 16)).isoformat() == "2028-05-16"
    assert not is_school_open(date(2026, 5, 17)), "17 พ.ค. ยังไม่เปิดเทอม (เลื่อนไป 18)"

    assert is_school_open(date(2026, 6, 15)), "มิ.ย. ต้องเปิดเทอม"
    assert not is_school_open(date(2026, 4, 20)), "เม.ย. ต้องปิดเทอม"
    assert is_school_open(date(2026, 12, 5)), "ธ.ค. ต้องเปิดเทอม (ภาคเรียน 2)"

    fake_holy = {
        date(2026, 3, 3): "วันมาฆบูชา",
        date(2026, 5, 31): "วันวิสาขบูชา",
        date(2026, 7, 29): "วันอาสาฬหบูชา",
        date(2026, 7, 31): "วันหยุดชดเชยวันเข้าพรรษา",
    }
    ban = alcohol_ban_rows(fake_holy, 2026)
    ban_days = [r for r in ban if r["event_name"].startswith("ห้ามขาย")]
    assert len(ban_days) == 5, f"ต้องได้ 5 วันพระใหญ่ ได้ {len(ban_days)}"
    assert ban_days[-1]["event_date"] == "2026-10-26", "ออกพรรษาต้องถูกเติมเอง"

    OLD = [("11:00", "14:00"), ("17:00", "24:00")]
    assert _sale_windows(date(2020, 1, 1))["windows"] == OLD
    assert _sale_windows(date(2026, 1, 1))["windows"] == OLD, "ต้องไม่มีปลดล็อก 14:00-17:00"
    assert sale_windows_on(date(2026, 3, 3), {date(2026, 3, 3): "วันมาฆบูชา"}) == [], "วันพระใหญ่ต้องขายไม่ได้เลย"
    assert sale_windows_on(date(2026, 3, 4), {date(2026, 3, 3): "วันมาฆบูชา"}) == OLD

    # summary รายวัน — ติด tag ครบ
    hol = {date(2026, 3, 3): "วันมาฆบูชา", date(2026, 2, 17): "วันตรุษจีน",
           date(2026, 5, 31): "วันวิสาขบูชา", date(2026, 7, 29): "วันอาสาฬหบูชา"}
    ban = _all_ban_days(hol, 2026)

    magha = day_status(date(2026, 3, 3), hol, ban)
    assert "วันหยุดราชการ" in magha["tags"] and "ห้ามขายแอลกอฮอล์ทั้งวัน" in magha["tags"], magha
    assert magha["ขายแอลกอฮอล์ได้"] == [], "วันพระใหญ่ต้องขายไม่ได้เลย"

    cny = day_status(date(2026, 2, 17), hol, ban)
    assert "วันเทศกาล" in cny["tags"] and "วันหยุดราชการ" not in cny["tags"], cny
    assert cny["ขายแอลกอฮอล์ได้"] == OLD, "วันเทศกาลยังขายเหล้าได้ตามเวลาปกติ"

    sat = day_status(date(2026, 6, 20), hol, ban)      # เสาร์ กลางเทอม 1
    assert "เสาร์อาทิตย์" in sat["tags"] and "ปิดเทอม" in sat["tags"], sat
    assert sat["วัน"] == "เสาร์"

    wed = day_status(date(2026, 6, 17), hol, ban)      # พุธ กลางเทอม 1
    assert wed["เปิดเทอม"] and "เปิดเทอม" in wed["tags"], wed

    year = summary(year=2026, holidays=hol)
    assert len(year["วันห้ามขายแอลกอฮอล์"]) == 5, year["วันห้ามขายแอลกอฮอล์"]
    marked = {d["วันที่"] for d in year["ปฏิทิน"]}
    assert "2026-05-18" in marked and "2026-11-02" in marked, "วันเปิดเทอมต้องถูกมาร์ก"

    print(f"✅ ผ่าน — เข้า/ออกพรรษา {len(KNOWN)} ปี, เปิดเทอมเลื่อนวันหยุด, ห้ามขาย 5 วัน, กติกาเวลา, tag รายวัน")


if __name__ == "__main__":
    import sys

    import json
    from datetime import datetime

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args and args[0] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(int(args[0]) if args else None, verbose=False),
                         ensure_ascii=False, indent=1))
    elif args and "-" in args[0]:                       # 2026-08-20 = วันเดียว
        d = datetime.strptime(args[0], "%Y-%m-%d").date()
        print(json.dumps(summary(target_date=d), ensure_ascii=False, indent=1))
    elif args:                                          # 2026 = ทั้งปี
        print(json.dumps(summary(year=int(args[0])), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
