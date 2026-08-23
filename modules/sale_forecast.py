"""MODULE — ยอดขายพยากรณ์ตามช่วงวัน (Super Coconut sale-forecast)

⚠️ ตัวเลขที่ได้เป็น "ค่าพยากรณ์ล้วน" ทุกวัน รวมถึงวันที่ผ่านมาแล้ว —
   วันในอดีตคือ "ค่าที่ระบบเคยพยากรณ์ไว้สำหรับวันนั้น" ไม่ใช่ยอดที่ขายได้จริง
   ห้ามเอาไปแสดงเป็นยอดขายจริงเด็ดขาด

   หลักฐานยืนยัน: สาขา BOON มีบิลจริงจาก POS 625 บาท แต่ API นี้คืน 0
   (ระบบยังไม่มีโมเดลพยากรณ์ให้สาขาใหม่) — ถ้าเป็นยอดจริงต้องได้ 625

ต่างจาก modules/sales.py:
    sales.py      — ยอดขาย "จริง" จาก POS (apisupergourmet) รวมเองจากบิลรายใบ
    ไฟล์นี้        — ยอด "พยากรณ์" จาก sale-forecast (supercoconut) เป็นตัวเลขสำเร็จรูป
    เอาสองอันมาเทียบกันได้ = ดูว่าพยากรณ์แม่นแค่ไหน

API เป็น POST แต่ความหมายคือ GET (อ่านอย่างเดียว) ทุก field ใน body บังคับส่งครบ

    python -m modules.sale_forecast <owner_id> <branch_id> 2026-08-01 2026-08-31
    python -m modules.sale_forecast test        # self-check (ไม่ต่อเน็ต)
"""
from datetime import date, datetime, timedelta, timezone

import requests

API = "https://sys.supercoconut.net/api/sale-forecast/v1/card/get_best_selling/menu"
SOURCE = "Super Coconut sale-forecast"
TH_TZ = timezone(timedelta(hours=7))


def to_ts(d: date) -> int:
    """วันที่ → unix timestamp ของเที่ยงคืนวันนั้น "ตามเวลาไทย"

    API ตีความ end_date เป็นระดับวัน (นับรวมทั้งวันนั้น ไม่สนเวลาในวัน) — ยิงเทียบแล้ว
    ส่ง 10 ส.ค. 00:00 กับ 10 ส.ค. 23:59:59 ได้ผลเท่ากัน ส่งเที่ยงคืนต้นวันจึงพอ
    """
    return int(datetime(d.year, d.month, d.day, tzinfo=TH_TZ).timestamp())


def fetch(owner_id: str, branch_id: str, start: date, end: date) -> dict:
    """ยิง sale-forecast API — คืน data block ดิบ

    limit=1 เพราะต้องการแค่ total_price ซึ่งเป็นยอดรวมฝั่ง server ไม่ขึ้นกับ pagination
    (ยิงเทียบ limit 1/5/500 แล้ว total_price เท่ากันหมด) ไม่ต้องดึงเมนูทั้งร้านมาเปล่าๆ
    """
    body = {"owner_id": owner_id, "branch_id": branch_id,
            "start_date": to_ts(start), "end_date": to_ts(end),
            "page": 1, "limit": 1}
    r = requests.post(API, json=body, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") != 200:
        raise ValueError(f"sale-forecast API ไม่สำเร็จ: {payload.get('messages')}")
    return payload.get("data") or {}


def format_result(data: dict, start: date, end: date) -> dict:
    """data ดิบ → record ภาษาไทย — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต

    ไม่แยกอดีต/อนาคต เพราะเป็นค่าพยากรณ์เหมือนกันหมด ไม่ว่าวันนั้นจะผ่านไปแล้วหรือยัง
    """
    return {
        "ช่วงวันที่": {"เริ่ม": start.isoformat(), "ถึง": end.isoformat()},
        "ยอดขายพยากรณ์": float(data.get("total_price") or 0),
        "หน่วย": "บาท",
        "หมายเหตุ": "ค่าพยากรณ์ทั้งหมด รวมถึงวันที่ผ่านมาแล้ว (= ค่าที่ระบบเคยพยากรณ์ไว้ "
                    "ไม่ใช่ยอดขายจริง) — ยอดขายจริงดูที่ /api/v1/sales",
        "แหล่งข้อมูล": SOURCE,
    }


def summary(owner_id: str, branch_id: str, start: date, end: date) -> dict:
    return format_result(fetch(owner_id, branch_id, start, end), start, end)


def demo():
    """self-check — แปลง timestamp, แยกประเภทตัวเลข, กันค่าว่าง ไม่ต่อเน็ต"""
    # เที่ยงคืนไทยของ 2 ส.ค. 2026 = 1785603600 (ค่าเดียวกับตัวอย่างที่ทีมส่งมา)
    assert to_ts(date(2026, 8, 2)) == 1785603600
    assert to_ts(date(2026, 8, 31)) == 1788109200
    assert to_ts(date(2026, 8, 3)) - to_ts(date(2026, 8, 2)) == 86400

    data = {"total_price": 138700.0, "total_item": 97}
    r = format_result(data, date(2026, 8, 2), date(2026, 8, 31))
    assert r["ยอดขายพยากรณ์"] == 138700.0
    assert r["ช่วงวันที่"] == {"เริ่ม": "2026-08-02", "ถึง": "2026-08-31"}
    assert "ไม่ใช่ยอดขายจริง" in r["หมายเหตุ"], "ต้องเตือนว่าไม่ใช่ยอดจริง ไม่งั้นคนอ่านเข้าใจผิด"
    assert "ยอดขายรวม" not in r, "ห้ามใช้ชื่อกำกวมที่อ่านแล้วนึกว่าเป็นยอดจริง"

    # ไม่มี total_price ต้องได้ 0 ไม่ใช่พัง (สาขาใหม่ที่ระบบยังไม่มีโมเดลพยากรณ์)
    assert format_result({}, date(2026, 8, 1), date(2026, 8, 2))["ยอดขายพยากรณ์"] == 0.0

    print("✅ ผ่าน — timestamp เที่ยงคืนไทย, ชื่อ field ไม่กำกวม, เตือนว่าไม่ใช่ยอดจริง, กันข้อมูลว่าง")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    if args and args[0] == "test":
        demo()
    elif len(args) >= 4:
        s = datetime.strptime(args[2], "%Y-%m-%d").date()
        e = datetime.strptime(args[3], "%Y-%m-%d").date()
        print(json.dumps(summary(args[0], args[1], s, e), ensure_ascii=False, indent=1))
    else:
        print("usage: python -m modules.sale_forecast <owner_id> <branch_id> <start> <end> | test")
