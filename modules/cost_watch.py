"""MODULE — ต้นทุนและพลังงาน (การ์ด ST-01 + หน้าเต็ม ST-04)

4 ช่องคงที่ที่การ์ดใช้ — หมูสามชั้น / ไข่ไก่ เบอร์ 2 / ดีเซล B20 / แก๊สหุงต้ม
ไม่ใช่รายการเต็มของ DIT (อันนั้นยังอยู่ที่ /api/v1/food-price ตามเดิม)

ต่างจาก food_price.format_rows() ตรงที่ตอบเป็น **array แบน** ไม่ใช่ dict ซ้อนตามหมวด
frontend จึงวนลูปได้โดยไม่ต้อง hardcode ชื่อไทย และไม่พังตอน DIT เปลี่ยนคำเรียกสินค้า

    python -m modules.cost_watch test     # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""

# ชื่อที่ทีมตั้งเอง ไม่ใช่ชื่อดิบจากต้นทาง — ต้นทางเปลี่ยนชื่อแก้แค่ `match` ที่เดียว
# unit: DIT ส่งหน่วยจริงมาในแถวแล้ว (บาท/กก., บาท/ฟอง) ใช้ของต้นทางก่อนเสมอ
#       ค่าใน `unit` เป็นตัวสำรองของแถวเก่าที่ scrape ไว้ตอนยังเก็บหน่วยเป็น "บาท" เฉยๆ
#       ส่วน fact_daily เก็บหน่วยเป็นอังกฤษ (Baht/Tank) การ์ดต้องการไทย จึงใช้ค่านี้ตรงๆ
ITEMS = [
    {"name": "หมูสามชั้น", "unit": "บาท/กก.",
     "table": "dit", "match": "สุกรชำแหละ เนื้อสามชั้น"},
    {"name": "ไข่ไก่ เบอร์ 2", "unit": "บาท/ฟอง",
     "table": "dit", "match": "ไข่ไก่ เบอร์ 2"},
    {"name": "ดีเซล B20", "unit": "บาท/ลิตร",
     "table": "daily", "match": "diesel B20"},
    {"name": "แก๊สหุงต้ม", "unit": "บาท/ถัง",
     "table": "daily", "match": "LPG barrel 15KG"},
]

DIT_PRODUCTS = [i["match"] for i in ITEMS if i["table"] == "dit"]
DAILY_METRICS = [i["match"] for i in ITEMS if i["table"] == "daily"]

# สวิตช์โหมด — True  = ส่งเฉพาะ 4 รายการใน ITEMS (ที่ PO สั่ง)
#              False = ส่งสินค้าทุกตัวที่มีในระบบ (DIT ขายปลีกทั้งหมด + พลังงานทั้งหมด)
# แก้ค่านี้ค่าเดียวแล้ว rebuild — ไม่ต้องแตะ query, endpoint หรือรูป response
FILTER_ENABLED = True

# fact_daily เก็บหน่วยเป็นอังกฤษ (มาจากฝั่ง scrape) — โหมดส่งทุกตัวต้องแปลงเอง
# (โหมด 4 ช่องไม่ใช้ตารางนี้ เพราะหน่วยไทยเขียนไว้ใน ITEMS แล้ว)
UNIT_TH = {
    "Baht/Liter": "บาท/ลิตร",
    "Baht/kg": "บาท/กก.",
    "Baht/Tank": "บาท/ถัง",
}


def dit_filter() -> tuple[str, tuple]:
    """เงื่อนไข query ฝั่ง DIT ตามโหมดปัจจุบัน — (where, params)

    ต้องอ่านสวิตช์ตัวเดียวกับ build() ไม่งั้นปิด filter แล้วยัง query มาแค่ 4 แถว
    """
    if FILTER_ENABLED:
        return "protype = %s AND product_name = ANY(%s)", ("ขายปลีก", DIT_PRODUCTS)
    return "protype = %s", ("ขายปลีก",)


def daily_filter(energy_where: str, energy_sources: tuple) -> tuple[str, tuple]:
    """เงื่อนไข query ฝั่งพลังงาน — ต่อท้ายเงื่อนไข source ที่ผู้เรียกกำหนดมา"""
    if FILTER_ENABLED:
        return f"{energy_where} AND metric_name = ANY(%s)", (*energy_sources, DAILY_METRICS)
    return energy_where, energy_sources


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
    """รวมแถวจาก 2 ตารางเป็นการ์ด 4 ช่อง — เรียงตาม ITEMS เสมอ ไม่ขึ้นกับลำดับที่ query มา

    dit_rows   : fact_dit_price (มี price_change ที่ cron คำนวณไว้แล้ว = เทียบประกาศครั้งก่อน
                 ไม่ใช่เทียบเมื่อวานเป๊ะ เพราะ DIT ไม่ประกาศราคาวันหยุด)
    daily_rows : fact_daily ของวันล่าสุด (น้ำมัน/แก๊ส)
    prev_daily : fact_daily ของ "วันก่อนหน้าที่มีข้อมูลจริง" ใช้คิดส่วนต่างของพลังงาน
    ช่องที่ยังไม่มีข้อมูลก็ยังส่งครบ 4 ช่อง (ราคา 0) — การ์ดต้องไม่มีช่องหาย
    ค่าที่หาไม่ได้เป็น 0 เสมอ ไม่ใช่ null — field ห้ามหายและ type ห้ามเปลี่ยน
    """
    prev = {r["metric_name"]: _num(r["value"]) for r in prev_daily}
    items = (_filtered_items({r["product_name"]: r for r in dit_rows},
                             {r["metric_name"]: r for r in daily_rows}, prev)
             if FILTER_ENABLED else _all_items(dit_rows, daily_rows, prev))
    return {"updated_at": updated_at, "items": items}


def _filtered_items(dit: dict, daily: dict, prev: dict) -> list[dict]:
    """โหมด 4 ช่อง — วนตาม ITEMS ไม่ใช่วนตามแถว ช่องที่ไม่มีข้อมูลจึงยังอยู่ครบ"""
    items = []
    for spec in ITEMS:
        if spec["table"] == "dit":
            row = dit.get(spec["match"], {})
            unit = row.get("unit") or ""
            items.append(_item(
                name=spec["name"],
                price=_num(row.get("price_avg")),
                price_min=_num(row.get("price_min")),
                price_max=_num(row.get("price_max")),
                # แถวเก่าที่ scrape ก่อนแก้บั๊กเก็บหน่วยเป็น "บาท" เฉยๆ — ใช้ตัวสำรองแทน
                unit=unit if "/" in unit else spec["unit"],
                change=_num(row.get("price_change")),
            ))
        else:
            row = daily.get(spec["match"], {})
            price = _num(row.get("value"))
            before = prev.get(spec["match"])
            items.append(_item(
                name=spec["name"],
                price=price,
                # น้ำมัน/แก๊สประกาศราคาเดียว ไม่มีช่วงราคาให้เก็บ
                price_min=0.0,
                price_max=0.0,
                unit=spec["unit"],
                change=round(price - before, 2) if before is not None and price else 0.0,
            ))
    return items


def _all_items(dit_rows: list[dict], daily_rows: list[dict], prev: dict) -> list[dict]:
    """โหมดส่งทุกตัว — วัตถุดิบก่อน (เรียงตามหมวดแล้วชื่อ) แล้วต่อด้วยพลังงาน

    ชื่อที่ส่งออกเป็นชื่อดิบจากต้นทาง ไม่ผ่านตารางชื่อของทีม เพราะรายการเป็นร้อยตัว
    ตั้งชื่อเองไม่ไหว — ฝั่งพลังงานใช้ชื่อไทยจาก energy.METRIC_TH ที่มีอยู่แล้ว
    """
    from modules.energy import METRIC_TH        # import ในฟังก์ชัน — โหมดหลักไม่ต้องโหลด

    items = [_item(
        name=r["product_name"],
        price=_num(r.get("price_avg")),
        price_min=_num(r.get("price_min")),
        price_max=_num(r.get("price_max")),
        unit=r.get("unit") or "บาท",
        change=_num(r.get("price_change")),
    ) for r in sorted(dit_rows, key=lambda x: (x.get("category") or "", x["product_name"]))]

    for r in sorted(daily_rows, key=lambda x: x["metric_name"]):
        price = _num(r.get("value"))
        before = prev.get(r["metric_name"])
        items.append(_item(
            name=METRIC_TH.get(r["metric_name"], r["metric_name"]),
            price=price,
            price_min=0.0,
            price_max=0.0,
            unit=UNIT_TH.get(r.get("unit", ""), r.get("unit") or "บาท"),
            change=round(price - before, 2) if before is not None and price else 0.0,
        ))
    return items


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
        {"metric_name": "LPG per Liter", "value": "15.525", "unit": "Baht/Liter"},
    ]
    prev_daily = [{"metric_name": "diesel B20", "value": "36.55"}]

    out = build(dit_rows, daily_rows, prev_daily, "2026-09-08T01:00:00+07:00")
    assert isinstance(out["items"], list), "items ต้องเป็น array ห้ามเป็น object"
    assert [i["name"] for i in out["items"]] == [i["name"] for i in ITEMS], \
        "ต้องได้ 4 ช่องเรียงตาม ITEMS เสมอ"
    assert all(set(i) == {"name", "price", "price_min", "price_max", "unit", "change", "direction"}
               for i in out["items"]), "field ต้องครบและเท่ากันทุกตัว"

    pork, egg, diesel, lpg = out["items"]
    assert (pork["price"], pork["price_min"], pork["change"], pork["direction"]) == \
        (184.0, 169.0, 2.0, "up"), pork
    assert pork["unit"] == "บาท/กก." and egg["unit"] == "บาท/ฟอง", "ต้องใช้หน่วยจริงจาก DIT"
    assert egg["price_min"] == egg["price_max"] == 0.0, "หาช่วงราคาไม่ได้ต้องเป็น 0"
    assert egg["change"] == 0.0 and egg["direction"] == "flat", "price_change ว่างต้องเป็น 0/flat"

    assert (diesel["price"], diesel["change"], diesel["direction"]) == (35.30, -1.25, "down"), diesel
    assert lpg["price"] == 423.0, "ต้องเป็นราคาถัง 15 กก. ไม่ใช่ LPG ต่อลิตรที่ปนมาในชุด"
    assert diesel["unit"] == "บาท/ลิตร" and lpg["unit"] == "บาท/ถัง", "หน่วยพลังงานต้องเป็นไทย"
    assert diesel["price_min"] == diesel["price_max"] == 0.0, "พลังงานไม่มีช่วงราคา ต้องเป็น 0"
    assert lpg["change"] == 0.0, "ไม่มีราคาวันก่อนหน้าต้องเป็น 0 ไม่ใช่ null"

    # แถวนอก whitelist ต้องไม่โผล่ในการ์ด แม้ query จะติดมา
    noise = build(dit_rows + [{"category": "ผักสด", "product_name": "ผักบุ้ง",
                               "price_avg": 20.0, "unit": "บาท/กก.",
                               "price_min": 18.0, "price_max": 22.0, "price_change": 1.0}],
                  daily_rows, prev_daily, "")
    assert len(noise["items"]) == 4 and all(i["name"] != "ผักบุ้ง" for i in noise["items"])

    # ไม่มีข้อมูลเลยก็ยังต้องได้ครบ 4 ช่อง ทุกค่าเป็นตัวเลข ไม่มี None
    empty = build([], [], [], "")
    assert len(empty["items"]) == 4, "การ์ดห้ามมีช่องหาย แม้ DB ว่าง"
    assert all(isinstance(i[k], float) for i in empty["items"]
               for k in ("price", "price_min", "price_max", "change"))
    assert all(i["direction"] == "flat" for i in empty["items"])

    # ── โหมดส่งทุกตัว (FILTER_ENABLED = False) ────────────────
    global FILTER_ENABLED
    FILTER_ENABLED = False
    try:
        every = build(dit_rows, daily_rows, prev_daily, "")
        names = [i["name"] for i in every["items"]]
        assert len(names) == 5, f"ต้องได้ทุกแถวที่ส่งเข้าไป (2 DIT + 3 พลังงาน) ได้ {names}"
        assert "สุกรชำแหละ เนื้อสามชั้น" in names, "โหมดนี้ใช้ชื่อดิบจาก DIT ไม่ใช่ชื่อที่ทีมตั้ง"
        assert "ถังแก๊ส 15 กก." in names, "ฝั่งพลังงานต้องแปลชื่อด้วย energy.METRIC_TH"
        assert "LPG (ต่อลิตร)" in names, "ตัวที่ไม่อยู่ใน ITEMS ต้องติดมาด้วยในโหมดนี้"

        lpg_litre = next(i for i in every["items"] if i["name"] == "LPG (ต่อลิตร)")
        assert lpg_litre["unit"] == "บาท/ลิตร", "หน่วยอังกฤษต้องถูกแปลงเป็นไทย"

        # query ต้องสลับตามสวิตช์ด้วย ไม่งั้นปิด filter แล้วยังได้มาแค่ 4 แถว
        assert dit_filter() == ("protype = %s", ("ขายปลีก",))
        assert daily_filter("src = %s", ("x",)) == ("src = %s", ("x",))
    finally:
        FILTER_ENABLED = True

    assert dit_filter()[0].endswith("product_name = ANY(%s)"), "เปิด filter ต้องกรองที่ DB"
    assert daily_filter("src = %s", ("x",))[0].endswith("metric_name = ANY(%s)")

    print("✅ ผ่าน — ครบ 4 ช่องเรียงคงที่, items เป็น array, กรองแถวนอก whitelist, ไม่มี null, "
          "หน่วยจริงจาก DIT, สลับโหมดส่งทุกตัวได้ทั้ง build และ query")

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
