import re
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from . import almanac_th
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from modules import almanac_th

SOURCE = "qmrl888.com"



UA = {"User-Agent": "Mozilla/5.0"}

FIELD_TO_TYPE = [
    ("yi", "宜"), ("ji", "忌"), ("lunar_month_day", "农历"),
    ("ganzhi_raw", "干支"), ("pillar_day", "日柱"), ("zodiac_day", "日生肖"),
    ("pillar_month", "月柱"), ("pillar_year", "年柱"),
    ("day_clash", "日冲"), ("day_sha", "日煞"),
    ("zhishen", "值神"), ("day_quality", "黄道黑道"),
    ("wuxing", "五行"), ("taishen", "胎神"), ("pengzu", "彭祖百忌"),
    ("good_spirits", "吉神宜趋"), ("bad_spirits", "凶煞宜忌"),
    ("wealth_dir", "财神方位"), ("joy_dir", "喜神方位"), ("fortune_dir", "福神方位"),
    ("yang_noble_dir", "阳贵神方位"), ("yin_noble_dir", "阴贵神方位"),
]


def _txt(el) -> str:
    return el.get_text(" ", strip=True).replace(" ", " ").strip() if el else ""


def scrape(year: int, month: int, day: int) -> dict:
    url = f"https://www.qmrl888.com/{year}-{month}-{day}.html"
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    cls = lambda prefix: re.compile(prefix)
    out = {"url": url}

    for container in soup.find_all("div", class_=cls("lunar_head")):
        label = _txt(container.find("div", class_=cls("lunar_top")))
        content = _txt(container.find("div", class_=cls("lunar_bottom")))
        if label == "宜":
            out["yi"] = content
        elif label == "忌":
            out["ji"] = content

    lunar = _txt(soup.find("div", class_=cls("lunar_lunardate")))
    out["lunar_raw"] = lunar
    parts = lunar.replace("农历", "").split()
    out["lunar_month_day"] = parts[-1] if parts else ""

    pengzu_box = soup.find("div", class_=cls("lunar_pengzu"))
    for para in (pengzu_box.find_all("p") if pengzu_box else []):
        text = _txt(para)
        if text.startswith("彭祖百忌"):
            out["pengzu"] = text.replace("彭祖百忌：", "").strip()
        elif "年" in text and "日" in text:
            out["ganzhi_raw"] = text
    m = re.search(r"(\S\S)年（(\S)）(\S\S)月（(\S)）(\S\S)日（(\S)）", out.get("ganzhi_raw", ""))
    if m:
        (out["pillar_year"], out["zodiac_year"], out["pillar_month"],
         out["zodiac_month"], out["pillar_day"], out["zodiac_day"]) = m.groups()

    for span in soup.select("div[class*=lunar_title] span"):
        text = _txt(span)
        if text.startswith("五行："):
            out["wuxing"] = text.replace("五行：", "").strip()
        elif text.startswith("胎神："):
            out["taishen"] = text.replace("胎神：", "").strip()

    DIR_FIELDS = {
        "日冲：": "day_clash", "日煞：": "day_sha", "财神：": "wealth_dir",
        "喜神：": "joy_dir", "福神：": "fortune_dir",
        "阳贵神：": "yang_noble_dir", "阴贵神：": "yin_noble_dir",
    }
    for li in soup.select("div[class*=lunar_right] li"):
        text = _txt(li)
        for prefix, field in DIR_FIELDS.items():
            if text.startswith(prefix):
                out[field] = text.replace(prefix, "").strip()

    left_lists = [[_txt(li) for li in ul.find_all("li")]
                  for ul in soup.select("div[class*=lunar_left] ul")]
    if len(left_lists) >= 1:
        out["good_spirits"] = " ".join(left_lists[0])
    if len(left_lists) >= 2:
        out["bad_spirits"] = " ".join(left_lists[1])

    extra = _txt(soup.find("div", class_=cls("lunar_extra")))
    out["zhishen_raw"] = extra
    z = re.search(r"值神是(\S+?)，是(\S+?)日", extra)
    if z:
        out["zhishen"], out["day_quality"] = z.group(1), z.group(2)

    hours = []
    for card in soup.find_all("div", class_=cls("timeline_card")):
        card_rows = card.find_all("div", class_=cls("timeline_row"))
        name, _, time_range = _txt(card_rows[0]).partition(" ") if card_rows else ("", "", "")
        meta = _txt(card_rows[1]) if len(card_rows) > 1 else ""
        clash, _, star = meta.partition("丨")
        hours.append({
            "name": name,
            "time": time_range,
            "clash": clash.replace("冲煞：", "").strip(),
            "star": star.replace("星神：", "").strip(),
            "yi": _txt(card.find("div", class_=cls("timeline_yi"))).replace("时宜：", "").strip(),
            "ji": _txt(card.find("div", class_=cls("timeline_ji"))).replace("时忌：", "").strip(),
            "quality": "吉" if card.find("div", class_=cls("timeline_good")) else "凶",
        })
    out["hours"] = hours
    return out


def build_rows(alm: dict, date_str: str) -> list[dict]:
    rows = []

    def add(type_cn, value, level="", hex_code="", source=SOURCE):
        if value:
            rows.append({
                "date": date_str, "type": type_cn,
                "type_th": almanac_th.FIELD_NAME.get(type_cn, type_cn),
                "level": level, "value_cn": value,
                "value_th": almanac_th.translate_value(type_cn, value),
                "hex": hex_code, "source": source,
            })

    for field, type_cn in FIELD_TO_TYPE:
        add(type_cn, alm.get(field, ""))

    # สีมงคลไม่อยู่ใน fact_myth — แยกเป็น dim_lucky_color เพราะไม่แปรตามวัน (ดู color_rows)

    for h in alm.get("hours", []):
        parts = [h["quality"], h["star"], h["clash"]]
        if h["yi"]:
            parts.append("宜:" + " ".join(h["yi"].split()))
        if h["ji"]:
            parts.append("忌:" + " ".join(h["ji"].split()))
        add("时辰", " | ".join(p for p in parts if p), level=f"{h['time']} {h['name']}")

    return rows


def run(target_date: date | None = None, verbose: bool = True) -> list[dict]:
    """แถวยาวสำหรับเก็บลง fact_myth"""
    d = target_date or date.today()
    alm = scrape(d.year, d.month, d.day)
    rows = build_rows(alm, d.isoformat())

    if verbose:
        print("\n" + "=" * 50)
        print(f"🔮  MODULE 12: Chinese Almanac — {d}")
        print("=" * 50)
        print(f"  ✅ {alm.get('lunar_raw', '')} | {alm.get('ganzhi_raw', '')}")
        print(f"  ✅ ฤกษ์ {len(alm.get('hours', []))} ยาม")
        print(f"  📊 รวม {len(rows)} แถวใน fact_myth")
    return rows


# type ใน fact_myth → key ที่ API ส่งออก (ตัวที่ไม่อยู่ในนี้ไม่ถูกแสดง)
SUMMARY_FIELDS = {
    "农历": "วันจันทรคติจีน",
    "日生肖": "ราศีวัน",
    "日冲": "วันชง",
    "黄道黑道": "วันดีวันร้าย",
    "值神": "เทพเวรประจำวัน",
    "财神方位": "ทิศโชคลาภ",
    "喜神方位": "ทิศยินดี",
    "日煞": "ทิศอัปมงคล",
    "宜": "ควรทำ",
    "忌": "ห้ามทำ",
}
SPLIT_FIELDS = {"宜", "忌"}       # ค่าเป็นรายการคำ → คืนเป็น array


def format_rows(rows: list[dict], as_of: str) -> dict:
    """แปลงแถว fact_myth → record ภาษาไทยล้วน

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)
    """
    by_type = {r["type"]: r for r in rows}
    out = {"วันที่": as_of}

    for type_cn, key in SUMMARY_FIELDS.items():
        value = by_type.get(type_cn, {}).get("value_th", "")
        out[key] = value.split() if type_cn in SPLIT_FIELDS else value

    # 五行 เก็บ "ธาตุ | ประเภทวัน" รวมกันในแถวเดียว — แยกให้ frontend ไม่ต้อง parse เอง
    wuxing = (by_type.get("五行", {}).get("value_th", "") or "").split(" | ")
    out["ธาตุประจำวัน"] = wuxing[0] if wuxing else ""
    out["ประเภทวัน"] = wuxing[1] if len(wuxing) > 1 else ""

    # 时辰 เก็บแถวละยาม — level = "00:00 - 00:59 戊子时" ตัดชื่อยามจีนท้ายออก เหลือช่วงเวลา
    good, bad = [], []
    for r in sorted(rows, key=lambda x: x["level"]):
        if r["type"] != "时辰":
            continue
        time_range = r["level"].rsplit(" ", 1)[0]
        (good if r["value_th"].startswith("ฤกษ์ดี") else bad).append(time_range)
    out["ฤกษ์ดี"], out["ฤกษ์ร้าย"] = good, bad

    out["แหล่งข้อมูล"] = SOURCE
    return out


def summary(target_date: date | None = None) -> dict:
    """คุณสมบัติของวันนั้น — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)"""
    d = target_date or date.today()
    return format_rows(run(d, verbose=False), d.isoformat())


if __name__ == "__main__":
    import json
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    d = datetime.strptime(args[0], "%Y-%m-%d").date() if args else None
    if "--rows" in sys.argv:          # แถวยาวสำหรับเก็บลง DB
        print(json.dumps(run(d), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(summary(d), ensure_ascii=False, indent=1))
