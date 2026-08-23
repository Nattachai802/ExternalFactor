"""MODULE — ราคาวัตถุดิบอาหาร (DIT — pricelist.dit.go.th) ย้อนหลัง 7 วัน

โมดูลเอกเทศน์: เฉพาะ DIT เท่านั้น (ตัด NABC/BigC/Makro ออกตามที่ตกลง)
ใช้ seltime=range ของ DIT เอง — ยิง 1 request ต่อสินค้าได้ 7 วันรวด
แทนการวนถามทีละวัน (ของเดิมวนสูงสุด 3 request/สินค้า)

    python -m modules.food_price          # summary JSON (ราคาเฉลี่ยล่าสุดต่อสินค้า)
    python -m modules.food_price --rows   # แถวราย(สินค้า,วัน) ลง fact_dit_price
    python -m modules.food_price test     # self-check (ไม่ต่อเน็ต)
"""
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://pricelist.dit.go.th"
UA = {"User-Agent": "Mozilla/5.0"}
SOURCE = "pricelist.dit.go.th (DIT)"
PROTYPE_LABEL = {"1": "ขายปลีก", "2": "ขายส่ง"}


def fetch_catalog(protype: str) -> list[dict]:
    """กลุ่มสินค้า+รายการสินค้าทั้งหมดของ protype (1=ปลีก, 2=ส่ง)"""
    groups = requests.get(f"{BASE}/getdata.php", params={"ID": protype, "TYPE": "dit"},
                          headers=UA, timeout=15).json()
    if not isinstance(groups, list):
        raise ValueError(f"DIT catalog protype={protype} คืนรูปแบบที่ไม่รู้จัก")

    out = []
    for g in groups:
        products = requests.get(f"{BASE}/getdata.php",
                                params={"ID": g["group_id"], "TYPE": "product"},
                                headers=UA, timeout=15).json()
        if not isinstance(products, list):
            continue
        for p in products:
            if p.get("product_id") and p.get("product_name"):
                out.append({"group_id": g["group_id"], "group_name": g["group_name"],
                           "product_id": p["product_id"], "product_name": p["product_name"]})
    return out


def parse_price_table(html: str) -> list[dict]:
    """แถวราคารายวันจากตาราง HTML — คืน [{date_th, range, avg}], ข้าม 'ราคาเฉลี่ย' รวม"""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table")
    if not table or not table.find("tbody"):
        return []

    out = []
    for tr in table.find("tbody").find_all("tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cols) < 3 or cols[0] == "ราคาเฉลี่ย" or cols[2] in ("", "-"):
            continue
        try:
            avg = float(cols[2].replace(",", ""))
        except ValueError:
            continue
        out.append({"date_th": cols[0], "range": cols[1], "avg": avg})
    return out


THAI_MONTH = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}


def parse_thai_date(text: str) -> str | None:
    """'14 ส.ค. 2569' → '2026-08-14' (พ.ศ. → ค.ศ.) คืน None ถ้าอ่านไม่ออก

    column date ใน fact_dit_price เป็น DATE — ข้อความไทยดิบ insert ตรงไม่ได้
    """
    parts = text.split()
    if len(parts) != 3:
        return None
    day, month_th, year_be = parts
    month = THAI_MONTH.get(month_th)
    if not month or not day.isdigit() or not year_be.isdigit():
        return None
    return f"{int(year_be) - 543:04d}-{month:02d}-{int(day):02d}"


def _split_range(range_str: str) -> tuple[float | None, float | None]:
    parts = [p.strip().replace(",", "") for p in range_str.split("-")]
    try:
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
        if len(parts) == 1 and parts[0]:
            return float(parts[0]), float(parts[0])
    except ValueError:
        pass
    return None, None


def fetch_product_history(product: dict, protype: str, day1: date, day2: date) -> list[dict]:
    """ราคารายวันของสินค้า 1 ตัวในช่วง [day1, day2] — 1 request ต่อสินค้า"""
    payload = {
        "day1": f"{day1.day:02d}/{day1.month:02d}/{day1.year + 543}",
        "day2": f"{day2.day:02d}/{day2.month:02d}/{day2.year + 543}",
        "protype": protype, "progroup": product["group_id"],
        "proname": product["product_id"], "seltime": "range",
    }
    resp = requests.post(f"{BASE}/main_price.php", params={"seltime": "range"},
                         data=payload, headers=UA, timeout=15)
    resp.raise_for_status()

    rows = []
    for r in parse_price_table(resp.text):
        iso_date = parse_thai_date(r["date_th"])
        if not iso_date:
            continue          # อ่านวันที่ไม่ออก = แถวใช้ไม่ได้ ข้ามไปดีกว่าเดา
        price_min, price_max = _split_range(r["range"])
        rows.append({
            "date": iso_date, "protype": PROTYPE_LABEL[protype],
            "category": product["group_name"], "product_name": product["product_name"],
            "price_min": price_min, "price_max": price_max, "price_avg": r["avg"],
            "unit": "บาท", "source": SOURCE,
        })
    return rows


def run(days: int = 7, protypes=("1", "2"), verbose: bool = True) -> list[dict]:
    """แถวราคาราย (สินค้า, วัน) ย้อนหลัง `days` วัน — ลง fact_dit_price"""
    day2 = date.today()
    day1 = day2 - timedelta(days=days - 1)
    rows = []

    if verbose:
        print("\n" + "=" * 50)
        print(f"📈  MODULE: DIT Food Prices — {days} วันย้อนหลัง")
        print("=" * 50)

    for protype in protypes:
        try:
            catalog = fetch_catalog(protype)
        except Exception as e:
            print(f"  ❌ catalog protype={protype}: {e}")
            continue
        if verbose:
            print(f"  📋 {PROTYPE_LABEL[protype]}: {len(catalog)} สินค้า")

        ok = 0
        for product in catalog:
            try:
                got = fetch_product_history(product, protype, day1, day2)
                rows += got
                ok += 1 if got else 0
            except Exception:
                continue
        if verbose:
            print(f"  ✅ {PROTYPE_LABEL[protype]}: ดึงราคาได้ {ok}/{len(catalog)} สินค้า")

    if verbose:
        print(f"  📊 รวม {len(rows)} แถวใน fact_dit_price")
    return rows


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _trend(change: float | None) -> str | None:
    if change is None:
        return None
    return "ขึ้น" if change > 0 else "ลง" if change < 0 else "เท่าเดิม"


def format_rows(rows: list[dict], as_of: str | None = None,
                with_history: bool = False) -> dict:
    """แปลงแถว fact_dit_price → record ภาษาไทย จัดกลุ่ม ปลีก/ส่ง → หมวด → สินค้า

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)
    ค่าที่แสดงต่อสินค้าคือ "แถวล่าสุดของสินค้านั้น" ในชุดที่ส่งเข้ามา
    with_history=True จะแนบราคาทุกวันที่มีในชุดด้วย (ใช้ตอนขอย้อนหลังหลายวัน)
    """
    out: dict[str, dict] = {"ขายปลีก": {}, "ขายส่ง": {}}
    dates = set()

    # เรียงเก่า→ใหม่ ให้แถวหลังสุดของแต่ละสินค้าเป็นค่าล่าสุดเสมอ ไม่ต้องเดาลำดับที่ query มา
    for r in sorted(rows, key=lambda x: _iso(x["date"])):
        protype = r["protype"]
        out.setdefault(protype, {})
        row_date = _iso(r["date"])
        dates.add(row_date)
        change = _num(r.get("price_change"))

        entry = out[protype].setdefault(r["category"], {}).setdefault(r["product_name"], {})
        entry.update({
            "ราคาเฉลี่ย": _num(r["price_avg"]),
            "ราคาต่ำสุด": _num(r["price_min"]),
            "ราคาสูงสุด": _num(r["price_max"]),
            "ส่วนต่างราคา": change,
            "แนวโน้ม": _trend(change),
            "หน่วย": r["unit"],
            "วันที่ประกาศ": row_date,
        })
        if with_history:
            entry.setdefault("ประวัติ", []).append({
                "วันที่": row_date,
                "ราคาเฉลี่ย": _num(r["price_avg"]),
                "ราคาต่ำสุด": _num(r["price_min"]),
                "ราคาสูงสุด": _num(r["price_max"]),
                "ส่วนต่างราคา": change,
            })

    return {
        "ณ วันที่": as_of or (max(dates) if dates else None),
        "ช่วงวันที่ของข้อมูล": f"{min(dates)} ถึง {max(dates)}" if dates else None,
        "จำนวนสินค้า": sum(len(products) for cats in out.values() for products in cats.values()),
        "ราคา": out,
        "แหล่งข้อมูล": SOURCE,
    }


def summary(days: int = 7, protypes=("1", "2")) -> dict:
    """ราคาวัตถุดิบ — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)"""
    return format_rows(run(days, protypes, verbose=False))


def demo():
    """self-check — parse จากตัวอย่าง ไม่ต่อเน็ต"""
    SAMPLE = """
    <table class="table"><tbody>
      <tr><td>14 ส.ค. 2569</td><td>160 - 170</td><td>165.00</td></tr>
      <tr><td>15 ส.ค. 2569</td><td>-</td><td>-</td></tr>
      <tr><td>16 ส.ค. 2569</td><td>2</td><td>2.00</td></tr>
      <tr><td>ราคาเฉลี่ย</td><td>160 - 170</td><td>165.00</td></tr>
    </tbody></table>
    """
    rows = parse_price_table(SAMPLE)
    assert len(rows) == 2, f"ต้องข้ามแถว '-' และ 'ราคาเฉลี่ย' เหลือ 2 ได้ {len(rows)}"
    assert rows[0] == {"date_th": "14 ส.ค. 2569", "range": "160 - 170", "avg": 165.0}

    assert parse_thai_date("14 ส.ค. 2569") == "2026-08-14"
    assert parse_thai_date("1 ม.ค. 2570") == "2027-01-01"
    assert parse_thai_date("ราคาเฉลี่ย") is None, "ข้อความที่ไม่ใช่วันที่ต้องคืน None ไม่ใช่พัง"
    assert parse_thai_date("14 xxx 2569") is None, "เดือนไม่รู้จักต้องคืน None"

    assert _split_range("160 - 170") == (160.0, 170.0)
    assert _split_range("2") == (2.0, 2.0)
    assert _split_range("") == (None, None)
    assert _split_range("พัง") == (None, None)

    assert parse_price_table("<p>ไม่มีตาราง</p>") == [], "เว็บเปลี่ยนโครงต้องคืนว่าง ไม่ raise (สินค้าบางตัวไม่มีราคาจริงๆ)"

    print("✅ ผ่าน — ข้ามแถวไม่มีราคา/แถวสรุป, แยกช่วงราคา, กันเว็บเปลี่ยนโครง")


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
