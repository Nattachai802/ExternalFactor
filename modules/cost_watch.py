"""MODULE — ต้นทุนและพลังงาน (การ์ด ST-01 + หน้าเต็ม ST-04)

รวมราคาวัตถุดิบ DIT (เฉพาะขายปลีก) กับราคาน้ำมัน/แก๊สไว้ใน array เดียว
ไม่คัดสินค้าให้ — ส่งทุกตัวที่มีในระบบ หน้าบ้านหยิบไปโชว์เองว่าจะเอากี่ช่อง

ต่างจาก food_price.format_rows() ตรงที่ตอบเป็น **array แบน** ไม่ใช่ dict ซ้อนตามหมวด
frontend จึงวนลูปได้โดยไม่ต้อง hardcode ชื่อไทย และไม่พังตอน DIT เปลี่ยนคำเรียกสินค้า

    python -m modules.cost_watch test     # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""

# fact_daily เก็บหน่วยเป็นอังกฤษ (มาจากฝั่ง scrape) — การ์ดต้องการไทย
UNIT_TH = {
    "Baht/Liter": "บาท/ลิตร",
    "Baht/kg": "บาท/กก.",
    "Baht/Tank": "บาท/ถัง",
}


def _num(value, default: float = 0.0) -> float:
    """คืน float เสมอ — ห้ามให้ None หลุดออก API (สัญญากับ frontend: ทุกช่องเป็นตัวเลข)"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction(change: float) -> str:
    return "up" if change > 0 else "down" if change < 0 else "flat"


def _item(name: str, price: float, price_min: float, price_max: float,
          unit: str, change: float) -> dict:
    return {
        "name": name,
        "price": price,
        "price_min": price_min,
        "price_max": price_max,
        "unit": unit,
        "change": change,
        "direction": _direction(change),
    }


def build(dit_rows: list[dict], daily_rows: list[dict],
          prev_daily: list[dict], updated_at: str) -> dict:
    """รวมแถวจาก 2 ตารางเป็น array เดียว — วัตถุดิบก่อน แล้วต่อด้วยพลังงาน

    dit_rows   : fact_dit_price (มี price_change ที่ cron คำนวณไว้แล้ว = เทียบประกาศครั้งก่อน
                 ไม่ใช่เทียบเมื่อวานเป๊ะ เพราะ DIT ไม่ประกาศราคาวันหยุด)
    daily_rows : fact_daily ของวันล่าสุด (น้ำมัน/แก๊ส)
    prev_daily : fact_daily ของ "วันก่อนหน้าที่มีข้อมูลจริง" ใช้คิดส่วนต่างของพลังงาน
    ค่าที่หาไม่ได้เป็น 0 เสมอ ไม่ใช่ null — field ห้ามหายและ type ห้ามเปลี่ยน
    """
    prev = {r["metric_name"]: _num(r["value"]) for r in prev_daily}

    items = []
    for r in sorted(dit_rows, key=lambda x: (x.get("category") or "", x["product_name"])):
        items.append(_item(
            name=r["product_name"],
            price=_num(r.get("price_avg")),
            price_min=_num(r.get("price_min")),
            price_max=_num(r.get("price_max")),
            unit=r.get("unit") or "บาท",
            change=_num(r.get("price_change")),
        ))

    # import ในฟังก์ชัน — energy.METRIC_TH เป็นตารางชื่อไทยที่ไม่อยากก๊อปมาไว้ 2 ที่
    from modules.energy import METRIC_TH

    for r in sorted(daily_rows, key=lambda x: x["metric_name"]):
        price = _num(r.get("value"))
        before = prev.get(r["metric_name"])
        items.append(_item(
            name=METRIC_TH.get(r["metric_name"], r["metric_name"]),
            price=price,
            # น้ำมัน/แก๊สประกาศราคาเดียว ไม่มีช่วงราคาให้เก็บ
            price_min=0.0,
            price_max=0.0,
            unit=UNIT_TH.get(r.get("unit", ""), r.get("unit") or "บาท"),
            change=round(price - before, 2) if before is not None and price else 0.0,
        ))

    return {"updated_at": updated_at, "items": items}


THAI_MONTH_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                   "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def compare_label(anchor, prev) -> str:
    """ข้อความบอกว่าเทียบกับประกาศไหน — DIT ไม่ประกาศทุกวัน ห่างกี่วันไม่แน่นอน

    anchor/prev เป็น date (prev = วันประกาศก่อนหน้า, None ถ้าไม่มี)
    """
    if not prev:
        return "ไม่มีข้อมูลก่อนหน้าให้เทียบ"
    gap = (anchor - prev).days
    if gap == 1:
        return "เทียบเมื่อวาน"
    if gap <= 3:
        return f"เทียบ {gap} วันก่อน"
    return f"เทียบประกาศวันที่ {prev.day} {THAI_MONTH_ABBR[prev.month - 1]}"


def summarize_change(dit_rows: list[dict], compared_to_label: str) -> dict:
    """เฉลี่ย % การเปลี่ยนแปลงราคาของสินค้าทั้งชุด

    เฉลี่ยแบบ "% รายตัวแล้วหารจำนวน" ไม่ใช่ % ของผลรวม — ของถูก (ไข่) กับของแพง (เนื้อโค)
    มีน้ำหนักเท่ากัน ตรงกับที่การ์ดสื่อว่า "ราคาสินค้าโดยเฉลี่ยขยับเท่าไหร่"

    นับรวมสินค้าที่ราคาไม่ขยับด้วย (change = 0) — ตลาดนิ่งคือข้อมูล ไม่ใช่ noise
    ตัดออกเฉพาะตัวที่ "เทียบไม่ได้": price_change ว่าง (เพิ่งเข้าระบบวันแรก)
    หรือราคาก่อนหน้า <= 0 (หารไม่ได้ / ข้อมูลเสีย)
    """
    percents = []
    for r in dit_rows:
        change = r.get("price_change")
        if change is None:
            continue
        change = _num(change)
        before = _num(r.get("price_avg")) - change
        if before <= 0:
            continue
        percents.append(change / before * 100)

    average = round(sum(percents) / len(percents), 1) if percents else 0.0
    return {
        "average_change_percent": average,
        "direction": _direction(average),
        "item_count": len(percents),
        "changed_count": sum(1 for p in percents if p != 0),
        "compared_to_label": compared_to_label,
    }


def demo():
    """self-check — ไม่ต่อเน็ต ไม่แตะ DB"""
    dit_rows = [
        {"category": "เนื้อสัตว์", "product_name": "สุกรชำแหละ เนื้อสามชั้น",
         "price_avg": 184.0, "unit": "บาท/กก.",
         "price_min": 169.0, "price_max": 184.0, "price_change": 2.0},
        {"category": "เนื้อสัตว์", "product_name": "ไข่ไก่ เบอร์ 2",
         "price_avg": 4.4, "unit": "บาท/ฟอง",
         "price_min": None, "price_max": None, "price_change": None},
    ]
    daily_rows = [
        {"metric_name": "diesel B20", "value": "35.30", "unit": "Baht/Liter"},
        {"metric_name": "LPG barrel 15KG", "value": "423.0", "unit": "Baht/Tank"},
    ]
    prev_daily = [{"metric_name": "diesel B20", "value": "36.55"}]

    out = build(dit_rows, daily_rows, prev_daily, "2026-09-08T01:00:00+07:00")
    assert isinstance(out["items"], list), "items ต้องเป็น array ห้ามเป็น object"
    assert len(out["items"]) == 4, "ต้องส่งทุกแถวที่ได้มา ไม่คัดออก"
    assert all(set(i) == {"name", "price", "price_min", "price_max", "unit", "change", "direction"}
               for i in out["items"]), "field ต้องครบและเท่ากันทุกตัว"

    pork, egg = out["items"][0], out["items"][1]   # เรียงตามหมวดแล้วชื่อ ("ส" มาก่อน "ไ")
    assert (pork["price"], pork["price_min"], pork["change"], pork["direction"]) == \
        (184.0, 169.0, 2.0, "up"), pork
    assert pork["unit"] == "บาท/กก." and egg["unit"] == "บาท/ฟอง", "ต้องใช้หน่วยจริงจาก DIT"
    assert egg["price_min"] == egg["price_max"] == 0.0, "หาช่วงราคาไม่ได้ต้องเป็น 0"
    assert egg["change"] == 0.0 and egg["direction"] == "flat", "price_change ว่างต้องเป็น 0/flat"

    lpg, diesel = out["items"][2], out["items"][3]
    assert diesel["name"] == "ดีเซล B20" and lpg["name"] == "ถังแก๊ส 15 กก.", (diesel, lpg)
    assert diesel["change"] == -1.25 and diesel["direction"] == "down", diesel
    assert diesel["unit"] == "บาท/ลิตร" and lpg["unit"] == "บาท/ถัง", "หน่วยพลังงานต้องแปลงเป็นไทย"
    assert diesel["price_min"] == diesel["price_max"] == 0.0, "พลังงานไม่มีช่วงราคา ต้องเป็น 0"
    assert lpg["change"] == 0.0, "ไม่มีราคาวันก่อนหน้าต้องเป็น 0 ไม่ใช่ null"

    empty = build([], [], [], "")
    assert empty == {"updated_at": "", "items": []}, "ไม่มีข้อมูลต้องได้ array ว่าง ไม่ใช่ null"

    print("✅ ผ่าน — array แบน ส่งครบทุกแถว, ไม่มี null, หน่วยจริงจาก DIT, แปลงหน่วยพลังงานเป็นไทย")

    _demo_summary()


def _demo_summary():
    """self-check ของ summarize_change / compare_label"""
    from datetime import date as _d

    # ขึ้น 2% + นิ่ง 0% → เฉลี่ย 1.0% (ตัวนิ่งถ่วงลงมาครึ่งหนึ่งตามตั้งใจ)
    rows = [
        {"price_avg": 102.0, "price_change": 2.0},
        {"price_avg": 50.0, "price_change": 0.0},
    ]
    out = summarize_change(rows, "เทียบเมื่อวาน")
    assert out["average_change_percent"] == 1.0, out
    assert out["direction"] == "up" and out["item_count"] == 2 and out["changed_count"] == 1

    # ลงต้องได้ down
    down = summarize_change([{"price_avg": 98.0, "price_change": -2.0}], "x")
    assert down["direction"] == "down" and down["average_change_percent"] == -2.0, down

    # ขึ้นเท่าลง → เฉลี่ย 0 ต้องเป็น flat (เครื่องหมายบอกไม่ได้ ต้องมี direction แยก)
    flat = summarize_change([{"price_avg": 102.0, "price_change": 2.0},
                             {"price_avg": 98.0, "price_change": -2.0}], "x")
    assert flat["average_change_percent"] == 0.0 and flat["direction"] == "flat", flat
    assert flat["changed_count"] == 2, "ค่าเฉลี่ย 0 ไม่ได้แปลว่าไม่มีตัวไหนขยับ"

    # เทียบไม่ได้ต้องถูกตัด ไม่ใช่นับเป็น 0%
    skip = summarize_change([{"price_avg": 100.0, "price_change": None},
                             {"price_avg": 2.0, "price_change": 2.0},      # ราคาก่อนหน้า = 0
                             {"price_avg": 102.0, "price_change": 2.0}], "x")
    assert skip["item_count"] == 1 and skip["average_change_percent"] == 2.0, skip

    # ไม่มีข้อมูลเลยต้องได้ 0/flat ไม่ใช่ error หรือ null
    assert summarize_change([], "x") == {
        "average_change_percent": 0.0, "direction": "flat",
        "item_count": 0, "changed_count": 0, "compared_to_label": "x"}

    # ปัดเหลือ 1 ตำแหน่ง (0.16% → 0.2) — ค่ากึ่งกลางพอดีอย่าง 0.15 ปล่อยให้ round() ตัดสิน
    assert summarize_change([{"price_avg": 100.16, "price_change": 0.16}], "x") \
        ["average_change_percent"] == 0.2, "ต้องปัดเป็น 1 ตำแหน่ง"

    a = _d(2026, 9, 8)
    assert compare_label(a, _d(2026, 9, 7)) == "เทียบเมื่อวาน"
    assert compare_label(a, _d(2026, 9, 5)) == "เทียบ 3 วันก่อน"
    assert compare_label(a, _d(2026, 9, 1)) == "เทียบประกาศวันที่ 1 ก.ย."
    assert compare_label(a, None) == "ไม่มีข้อมูลก่อนหน้าให้เทียบ"

    print("✅ ผ่าน — เฉลี่ย % รายตัว, ตัวนิ่งถ่วงดุล, ตัดตัวที่เทียบไม่ได้, กันหารศูนย์, ป้ายเทียบ 4 แบบ")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        import json
        print(json.dumps(build([], [], [], ""), ensure_ascii=False, indent=1))
