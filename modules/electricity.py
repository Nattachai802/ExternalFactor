"""MODULE 2.5 — อัตราค่าไฟฟ้า MEA (การไฟฟ้านครหลวง)

โมดูลเอกเทศน์ คืนแถว fact_daily — ดึงครบทุกประเภทผู้ใช้ไฟ

    python -m modules.electricity          # ทุกประเภท
    python -m modules.electricity 2        # เฉพาะประเภท 2
    python -m modules.electricity test     # self-check (ไม่ต่อเน็ต)

เดิมโมดูลนี้ scrape ienergyguru.com หา PEA ด้วย regex ข้อความทั้งหน้า
ได้ผลผิด (ทุกขั้นค่าเท่ากัน) และผิดหน่วยงาน — ร้านในกรุงเทพ/นนทบุรี/
สมุทรปราการ ใช้ MEA ไม่ใช่ PEA

⚠️ MEA ครอบคลุมแค่ 3 จังหวัด ที่เหลือทั้งประเทศเป็น PEA — ยังไม่ได้ทำ
⚠️ อัตราในหน้านี้เป็น "อัตราฐาน" ค่าไฟจริง = ฐาน + Ft + ค่าบริการ + VAT 7%
"""
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

BASE = "https://www.mea.or.th"
INDEX_PATH = "/our-services/service-rates/other/D5xEaEwgU"   # หน้าประเภท 1 มีสารบัญครบทุกประเภท
UA = {"User-Agent": "Mozilla/5.0"}
SOURCE = "MEA (mea.or.th)"

# ID ในลิงก์เป็นสตริงสุ่ม ไม่สื่อความหมาย จึงไล่จากสารบัญแทนการ hardcode
TYPE_LINK_RE = re.compile(r"ประเภทที่\s*(\d+)\s*(.*?)\s*ดูเนื้อหา")
NUM_RE = re.compile(r"^-?[\d,]+\.?\d*$")

# ข้อความหัวคอลัมน์ → หน่วย
UNIT_HINTS = [
    ("บาท/หน่วย", "บาท/หน่วย"),
    ("บาท/เดือน", "บาท/เดือน"),
    ("กิโลวัตต์", "บาท/กิโลวัตต์"),
]


def _txt(el) -> str:
    return el.get_text(" ", strip=True).replace(" ", " ").strip() if el else ""


def _num(text: str) -> float | None:
    """ดึงตัวเลขจากเซลล์ — รองรับทั้ง '312.24' และ '2.3488 บาท'"""
    t = text.replace(",", "").strip()
    if NUM_RE.match(t):
        return float(t)
    m = re.search(r"\d+\.?\d*", t)
    return float(m.group()) if m else None


def discover_types(html: str) -> dict[int, tuple[str, str]]:
    """อ่านสารบัญท้ายหน้า → {เลขประเภท: (ชื่อ, path)}"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.find_all("a", href=True):
        m = TYPE_LINK_RE.match(_txt(a.find_parent("tr")) if a.find_parent("tr") else "")
        if m and "/service-rates/other/" in a["href"]:
            out[int(m.group(1))] = (m.group(2).strip(), a["href"])
    if not out:
        raise ValueError("ไม่พบสารบัญประเภทผู้ใช้ไฟ — โครงเว็บ MEA อาจเปลี่ยน")
    return out


def _column_labels(header_rows: list[list[str]]) -> list[str]:
    """รวมหัวตาราง 1-2 ชั้นให้เป็นชื่อคอลัมน์เรียงตามตำแหน่งของเซลล์ข้อมูล

    ชั้นบนบอกชนิดค่า (ค่าพลังงานไฟฟ้า / ค่าบริการ)
    ชั้นล่างบอก On Peak / Off Peak ซึ่งมีเฉพาะบางคอลัมน์
    """
    top = [c for c in header_rows[0][1:] if c]           # ตัดเซลล์มุมซ้ายบนที่ว่าง
    if len(header_rows) < 2:
        return top

    sub = [c for c in header_rows[1] if c]
    labels, sub_i = [], 0
    for col in top:
        # คอลัมน์ที่มี On/Off Peak จะกินหัวย่อย 2 ช่อง
        if sub_i + 1 < len(sub) and "Peak" in sub[sub_i] and "Peak" in sub[sub_i + 1]:
            labels += [f"{col} {sub[sub_i]}", f"{col} {sub[sub_i + 1]}"]
            sub_i += 2
        else:
            labels.append(col)
    return labels


def _unit_for(label: str) -> str:
    for hint, unit in UNIT_HINTS:
        if hint in label:
            return unit
    return "บาท"


def parse_rate_table(table) -> list[dict]:
    """แปลง 1 ตารางอัตราเป็น [{code, column, value, unit}]

    รองรับ 3 รูปแบบที่ MEA ใช้:
      - ขั้นบันได   ['15 หน่วยแรก', 'หน่วยละ', '2.3488 บาท']
      - ค่าบริการ   ['ค่าบริการ (บาท/เดือน) :', '24.62', '']
      - ตาราง TOU  ['2.2.1 แรงดัน...', '5.1135', '2.6037', '312.24']
    """
    rows = [[_txt(c) for c in tr.find_all(["th", "td"])] for tr in table.find_all("tr")]
    rows = [r for r in rows if r]
    if not rows:
        return []

    # แถวหัว = แถวที่ไม่มีตัวเลขเลย
    # แถวหัวคือแถวที่คอลัมน์ถัดจากคอลัมน์แรกไม่มีตัวเลขเลย
    # (คอลัมน์แรกเป็นชื่อ/รหัส ซึ่งมีเลขได้ เช่น "2.2.1 แรงดัน 12 – 24 กิโลโวลต์")
    header_rows, body_start = [], 0
    for i, r in enumerate(rows):
        if any(_num(c) is not None for c in r[1:]):
            body_start = i
            break
        header_rows.append(r)
    else:
        return []

    labels = _column_labels(header_rows) if header_rows else []
    out = []
    for r in rows[body_start:]:
        code = r[0]
        values = r[1:]

        # แถวขั้นบันได/ค่าบริการ ที่ตัวเลขปนหน่วยมาในเซลล์เดียว ("2.3488 บาท")
        nums = [_num(cell) for cell in values]

        numeric_idx = [i for i, v in enumerate(nums) if v is not None]
        if not numeric_idx:
            continue

        for i in numeric_idx:
            label = labels[i] if i < len(labels) else (values[i - 1] if i else "")
            if not label:
                label = code
            out.append({
                "code": code,
                "column": label,
                "value": nums[i],
                "unit": _unit_for(label + " " + code),
            })
    return out


def fetch_type(path: str) -> tuple[str, list[dict]]:
    """ดึงหน้าประเภทหนึ่ง — คืน (ชื่อหน้า, รายการอัตรา)"""
    r = requests.get(BASE + path if path.startswith("/") else path, headers=UA, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = _txt(soup.title)

    rates = []
    for table in soup.find_all("table"):
        text = _txt(table)
        if "ดาวน์โหลด" in text or "ดูเนื้อหา" in text:
            continue          # ตารางไฟล์แนบ / สารบัญ ไม่ใช่ตารางอัตรา
        rates += parse_rate_table(table)
    return title, rates


def run(only_type: int | None = None, target_date: date | None = None,
        verbose: bool = True) -> list[dict]:
    """คืนแถว fact_daily ของอัตราค่าไฟทุกประเภท (หรือเฉพาะประเภทที่ระบุ)"""
    d = (target_date or date.today()).isoformat()

    index = requests.get(BASE + INDEX_PATH, headers=UA, timeout=20)
    index.raise_for_status()
    types = discover_types(index.text)
    if only_type:
        types = {k: v for k, v in types.items() if k == only_type}

    if verbose:
        print("\n" + "=" * 50)
        print("⚡  MODULE 2.5: MEA Electricity Tariff")
        print("=" * 50)

    rows = []
    for num, (name, path) in sorted(types.items()):
        try:
            title, rates = fetch_type(path)
        except Exception as e:
            print(f"  ❌ ประเภท {num}: {e}")
            continue
        for r in rates:
            # ค่าบริการรายเดือนไม่เกี่ยวกับต้นทุนต่อหน่วยที่ร้านใช้คำนวณ — ตัดทิ้ง
            # เช็คทั้ง code และ column เพราะตารางประเภท 7 วางค่าบริการไว้ในคอลัมน์แรก
            if "ค่าบริการ" in r["column"] or "ค่าบริการ" in r["code"]:
                continue
            rows.append({
                "date": d,
                "metric_name": f"MEA ประเภท {num} {name} | {r['code']} | {r['column']}",
                "value": str(r["value"]),
                "unit": r["unit"],
                "source": SOURCE,
            })
        if verbose:
            print(f"  ✅ ประเภท {num} {name}: {len(rates)} อัตรา")

    if verbose:
        print(f"  📊 รวม {len(rows)} แถวใน fact_daily")
    return rows


def format_rows(rows: list[dict], as_of: str, detailed: bool = False) -> dict:
    """แปลงแถว fact_daily → record ภาษาไทย

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)
    metric_name เก็บ 3 มิติคั่นด้วย " | " เช่น
        "MEA ประเภท 2 กิจการขนาดเล็ก | 2.1.1 แรงดัน 12-24 กิโลโวลต์ | ค่าพลังงานไฟฟ้า(บาท/หน่วย)"

    ปกติคืนแค่ช่วงค่าไฟต่อหน่วยรายประเภท (พอสำหรับ "ค่าไฟบ้าน/ร้านค้ากี่บาท")
    detailed=True จึงคืนโครงเต็มแยกตามรหัสอัตรา/แรงดัน/On-Off Peak
    """
    if detailed:
        grouped: dict[str, dict[str, dict]] = {}
        for r in rows:
            user_type, code, column = (r["metric_name"].split(" | ") + ["", ""])[:3]
            grouped.setdefault(user_type, {}).setdefault(code, {})[column] = {
                "ค่า": float(r["value"]), "หน่วย": r["unit"],
            }
        detail = grouped
    else:
        detail = None

    # ── อัตราเดียวต่อประเภท ───────────────────────────────────
    # ใช้ "อัตราปกติขั้นสูงสุด" เป็นตัวแทน เพราะเป็นอัตราที่ผู้ใช้ทั่วไปโดนจริง
    # เมื่อใช้ไฟเกินขั้นต้น (ขั้นล่างครอบแค่ 10-200 หน่วยแรกของเดือน)
    # ตัด TOU ออกเพราะเป็นทางเลือกที่ต้องสมัครแยก ผู้ใช้ส่วนใหญ่ไม่ได้ใช้
    per_type: dict[str, list[float]] = {}
    for r in rows:
        _, _, column = (r["metric_name"].split(" | ") + ["", ""])[:3]
        if "Peak" in column or "ความต้องการ" in column:
            continue
        # หน่วยที่ parse ได้ไม่สม่ำเสมอ (บาท / บาท/หน่วย / บาท/กิโลวัตต์) — ดูชื่อคอลัมน์แทน
        if "บาท/หน่วย" not in column and column != "หน่วยละ":
            continue
        value = float(r["value"])
        if value <= 0:          # 0.0 = ช่องว่างในตารางต้นทาง ไม่ใช่ค่าไฟฟรี
            continue
        user_type = r["metric_name"].split(" | ")[0].replace("MEA ประเภท ", "")
        per_type.setdefault(user_type, []).append(value)

    rates = {name: max(values) for name, values in sorted(per_type.items())}

    out = {
        "วันที่": as_of,
        "หน่วย": "บาท/หน่วย",
        "หมายเหตุ": "อัตราปกติขั้นสูงสุด (ไม่รวม TOU) — ค่าไฟจริงต้องบวก Ft + VAT 7%",
        "ค่าไฟต่อหน่วย": rates,
        "แหล่งข้อมูล": SOURCE,
    }
    if detail is not None:
        out["รายละเอียด"] = detail
    return out


def summary(only_type: int | None = None, target_date: date | None = None) -> dict:
    """อัตราค่าไฟ — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)"""
    d = target_date or date.today()
    return format_rows(run(only_type=only_type, target_date=d, verbose=False), d.isoformat())


def demo():
    """self-check — parse จากตัวอย่างจริงที่ตัดมา ไม่ต่อเน็ต"""
    TIER = """
    <table>
      <tr><td>ค่าพลังงานไฟฟ้า</td></tr>
      <tr><td>15 หน่วย (กิโลวัตต์ชั่วโมง) แรก</td><td>หน่วยละ</td><td>2.3488 บาท</td></tr>
      <tr><td>เกินกว่า 400 หน่วย</td><td>หน่วยละ</td><td>4.3583 บาท</td></tr>
      <tr><td>ค่าบริการ (บาท/เดือน) :</td><td>24.62</td><td></td></tr>
    </table>
    """
    tiers = parse_rate_table(BeautifulSoup(TIER, "html.parser").find("table"))
    vals = [t["value"] for t in tiers]
    assert 2.3488 in vals and 4.3583 in vals, f"ขั้นบันไดต้องครบ ได้ {vals}"
    assert 24.62 in vals, "ค่าบริการต้องถูกดึงด้วย"
    assert len({t["code"] for t in tiers}) == 3, "แต่ละขั้นต้องแยก code กัน"

    TOU = """
    <table>
      <tr><td></td><td>ค่าพลังงานไฟฟ้า (บาท/หน่วย)</td><td>ค่าบริการ (บาท/เดือน)</td></tr>
      <tr><td>On Peak</td><td>Off Peak</td></tr>
      <tr><td>2.2.1 แรงดัน 12 – 24 กิโลโวลต์</td><td>5.1135</td><td>2.6037</td><td>312.24</td></tr>
    </table>
    """
    tou = parse_rate_table(BeautifulSoup(TOU, "html.parser").find("table"))
    by_col = {t["column"]: t["value"] for t in tou}
    assert by_col.get("ค่าพลังงานไฟฟ้า (บาท/หน่วย) On Peak") == 5.1135, by_col
    assert by_col.get("ค่าพลังงานไฟฟ้า (บาท/หน่วย) Off Peak") == 2.6037, by_col
    assert by_col.get("ค่าบริการ (บาท/เดือน)") == 312.24, by_col
    units = {t["column"]: t["unit"] for t in tou}
    assert units["ค่าบริการ (บาท/เดือน)"] == "บาท/เดือน"

    # ประเภท 3 มี 5 คอลัมน์ (ค่าความต้องการ On/Off + ค่าพลังงาน On/Off + ค่าบริการ)
    T3 = """
    <table>
      <tr><td></td><td>ค่าความต้องการพลังไฟฟ้า (บาท/กิโลวัตต์)</td>
          <td>ค่าพลังงานไฟฟ้า (บาท/หน่วย)</td><td>ค่าบริการ (บาท/เดือน)</td></tr>
      <tr><td>On Peak</td><td>Off Peak</td><td>On Peak</td><td>Off Peak</td></tr>
      <tr><td>3.2.2 แรงดัน 12-24 กิโลโวลต์</td><td>132.93</td><td>0</td>
          <td>4.1839</td><td>2.6037</td><td>312.24</td></tr>
    </table>
    """
    t3 = parse_rate_table(BeautifulSoup(T3, "html.parser").find("table"))
    got = {t["column"]: t["value"] for t in t3}
    assert got.get("ค่าความต้องการพลังไฟฟ้า (บาท/กิโลวัตต์) On Peak") == 132.93, got
    assert got.get("ค่าพลังงานไฟฟ้า (บาท/หน่วย) On Peak") == 4.1839, got
    assert got.get("ค่าบริการ (บาท/เดือน)") == 312.24, got

    INDEX = """
    <table>
      <tr><td>ประเภทที่ 2 กิจการขนาดเล็ก</td><td><a href="/our-services/service-rates/other/lhKD8oIlS">ดูเนื้อหา</a></td></tr>
      <tr><td>ประเภทที่ 3 กิจการขนาดกลาง</td><td><a href="/our-services/service-rates/other/vauexlKUw">ดูเนื้อหา</a></td></tr>
    </table>
    """
    types = discover_types(INDEX)
    assert types[2][1].endswith("lhKD8oIlS") and "ขนาดเล็ก" in types[2][0], types

    try:
        discover_types("<p>ไม่มีสารบัญ</p>")
    except ValueError:
        pass
    else:
        raise AssertionError("เว็บเปลี่ยนโครงต้อง raise")

    print("✅ ผ่าน — ขั้นบันได, TOU 2 ชั้นหัว, ประเภท 3 (5 คอลัมน์), สารบัญ, และเคสเว็บเปลี่ยนโครง")


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else None

    import json

    if arg == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1))
    else:
        nums = [a for a in sys.argv[1:] if a.isdigit()]
        print(json.dumps(summary(int(nums[0]) if nums else None), ensure_ascii=False, indent=1))
