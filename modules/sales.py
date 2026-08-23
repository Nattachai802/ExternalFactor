"""MODULE — ยอดขายจริงจาก POS (Super Gourmet super-trend-metrics)

ต่างจากโมดูลอื่นตรงที่ "ไม่ cache ลง DB" — ยอดขายเป็นข้อมูลการเงิน บิลปิดเพิ่มได้ตลอดวัน
cache ไว้แล้วเสิร์ฟตัวเลขเก่าคือรายงานเงินผิด ยิงสดทุกครั้ง (API ตอบเร็ว ไม่มีโควตาจำกัด)

กติกาการรวมยอด (ตามที่ตกลงกับทีม):
  1. ตัดบิลที่ payment_method มี no_revenue ออก (บิลที่ไม่เกิดรายได้จริง)
  2. จัดกลุ่มตามวันของ bill_closed_at
  3. sum total_net_sales ในแต่ละวัน

นิยาม "วัน" = วันที่บิลปิดตามเวลาไทย (ตกลงกับทีมแล้ว) — บิลปิดตี 2 นับเป็นยอดของวันใหม่
bill_closed_at ที่ API ส่งมาเป็น UTC ต้องแปลงเป็นเวลาไทยก่อนเสมอ ไม่งั้นบิลที่ปิดหลัง
17:00 UTC จะตกวันผิด

POS ไม่ได้ส่ง bill_opened_at หรือ business_date มาด้วย จึงแยกไม่ออกว่าบิลตี 2 คือลูกค้า
ที่นั่งมาตั้งแต่หัวค่ำหรือเพิ่งเข้าร้าน — ถ้าวันหนึ่ง POS ส่ง business_date มา ให้ใช้ค่านั้นแทน
แก้ที่ day_of() จุดเดียว ไม่ต้องแตะที่อื่น

    python -m modules.sales <owner_id> <branch_id>          # 7 วันล่าสุด
    python -m modules.sales <owner_id> <branch_id> --days 30
    python -m modules.sales test                             # self-check (ไม่ต่อเน็ต)
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import requests

API = "https://sys.apisupergourmet.com/api/pos/v1/report/super-trend-metrics"
SOURCE = "Super Gourmet POS"
TH_TZ = timezone(timedelta(hours=7))

# บิลที่ payment_method มีค่านี้ = ไม่เกิดรายได้ (แถม/พนักงาน/ยกเลิก) ไม่นับเข้ายอดขาย
NO_REVENUE = "no_revenue"


def fetch(owner_id: str, branch_id: str, start_at: int, end_at: int) -> list[dict]:
    """ยิง POS API — start_at/end_at เป็น unix timestamp (วินาที)"""
    r = requests.get(API, params={"owner_id": owner_id, "branch_id": branch_id,
                                  "start_at": start_at, "end_at": end_at}, timeout=30)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status", {}).get("code") != 1000:
        raise ValueError(f"POS API ไม่สำเร็จ: {payload.get('status')}")
    return (payload.get("data") or {}).get("metrics") or []


def day_of(bill_closed_at: str) -> date:
    """'2026-08-21 19:12:29 +0000 UTC' → วันตามปฏิทินไทย

    รูปแบบเวลาเป็นของ Go (มี ' UTC' ต่อท้าย offset) strptime ปกติอ่านไม่ออก ต้องระบุ %Z ด้วย
    จุดเดียวที่ตัดสินว่า "บิลนี้เป็นยอดของวันไหน" — เปลี่ยนนิยามวันทางธุรกิจให้แก้ที่นี่
    """
    dt = datetime.strptime(bill_closed_at, "%Y-%m-%d %H:%M:%S %z %Z")
    return dt.astimezone(TH_TZ).date()


def is_revenue(metric: dict) -> bool:
    return NO_REVENUE not in (metric.get("payment_method") or [])


def daily_totals(metrics: list[dict]) -> dict[date, Decimal]:
    """รวมยอดขายรายวัน — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต

    ใช้ Decimal ไม่ใช่ float เพราะเป็นจำนวนเงิน — 0.1 + 0.2 ใน float ได้ 0.30000000000000004
    บวกกันหลายพันบิลแล้วยอดเพี้ยนทีละสตางค์
    """
    totals: dict[date, Decimal] = {}
    for m in metrics:
        if not is_revenue(m):
            continue
        day = day_of(m["bill_closed_at"])
        totals[day] = totals.get(day, Decimal("0")) + Decimal(str(m.get("total_net_sales") or "0"))
    return totals


def format_rows(metrics: list[dict], start: date, end: date) -> dict:
    """record ภาษาไทย — วันที่ไม่มีบิลเลยแสดงยอด 0 ไม่ใช่หายไปจากลิสต์

    (frontend วาดกราฟต้องการทุกวันในช่วง ไม่งั้นแกน X ขาดช่วง)
    """
    totals = daily_totals(metrics)
    counted = [m for m in metrics if is_revenue(m)]

    days, cursor = [], start
    while cursor <= end:
        days.append({"วันที่": cursor.isoformat(),
                     "ยอดขายสุทธิ": float(totals.get(cursor, Decimal("0")))})
        cursor += timedelta(days=1)

    return {
        "รายวัน": days,
        "ยอดรวมทั้งช่วง": float(sum(totals.values(), Decimal("0"))),
        "จำนวนบิลที่นับ": len(counted),
        "จำนวนบิลที่ตัดออก": len(metrics) - len(counted),
        "หน่วย": "บาท",
        "หมายเหตุ": "ยอดของแต่ละวัน = บิลที่ปิดในวันนั้นตามเวลาไทย "
                    "(บิลที่ปิดหลังเที่ยงคืนนับเป็นยอดของวันใหม่) "
                    "ตัดบิล payment_method = no_revenue ออกแล้ว",
        "แหล่งข้อมูล": SOURCE,
    }


def summary(owner_id: str, branch_id: str, days: int = 7,
            end: date | None = None) -> dict:
    """ยอดขายย้อนหลัง N วันนับถึง end (ไม่ระบุ = วันนี้)"""
    end = end or datetime.now(TH_TZ).date()
    start = end - timedelta(days=days - 1)

    # ยิงเผื่อขอบทั้งสองด้าน 1 วัน เพราะ API กรองด้วย UTC แต่เราจัดกลุ่มด้วยวันไทย
    # บิลตี 2 ของวันที่ start (= 19:00 UTC ของวันก่อน) จะตกนอกช่วงถ้าไม่เผื่อ
    start_ts = int(datetime.combine(start - timedelta(days=1), datetime.min.time(), TH_TZ).timestamp())
    end_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), TH_TZ).timestamp())

    metrics = fetch(owner_id, branch_id, start_ts, end_ts)
    # ตัดบิลที่หลุดออกนอกช่วงจริงทิ้ง (ผลข้างเคียงจากการเผื่อขอบด้านบน)
    metrics = [m for m in metrics if start <= day_of(m["bill_closed_at"]) <= end]
    return format_rows(metrics, start, end)


def demo():
    """self-check — filter, จัดกลุ่มข้ามวัน, Decimal, ช่วงวันที่เต็ม ไม่ต่อเน็ต"""
    sample = [
        {"total_net_sales": "380.00", "payment_method": ["transfer"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-18 07:28:03 +0000 UTC"},
        {"total_net_sales": "61.00", "payment_method": ["cash"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-20 08:28:04 +0000 UTC"},
        {"total_net_sales": "94.00", "payment_method": ["cash"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-20 10:38:38 +0000 UTC"},
        {"total_net_sales": "90.00", "payment_method": ["cash"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-21 19:12:29 +0000 UTC"},
    ]

    # บิล 19:12 UTC ของ 21 ส.ค. = ตี 2 ของ 22 ส.ค.เวลาไทย ต้องตกวันที่ 22 ไม่ใช่ 21
    assert day_of("2026-08-21 19:12:29 +0000 UTC") == date(2026, 8, 22)
    assert day_of("2026-08-18 07:28:03 +0000 UTC") == date(2026, 8, 18)
    assert day_of("2026-08-21 16:59:59 +0000 UTC") == date(2026, 8, 21), "ก่อน 17:00 UTC ยังเป็นวันเดิม"
    assert day_of("2026-08-21 17:00:00 +0000 UTC") == date(2026, 8, 22), "17:00 UTC = เที่ยงคืนไทยพอดี"

    totals = daily_totals(sample)
    assert totals[date(2026, 8, 18)] == Decimal("380.00")
    assert totals[date(2026, 8, 20)] == Decimal("155.00"), "2 บิลวันเดียวกันต้องรวมกัน"
    assert totals[date(2026, 8, 22)] == Decimal("90.00")
    assert date(2026, 8, 21) not in totals, "วันที่ไม่มีบิลต้องไม่มี key"

    # no_revenue ต้องถูกตัดออก แม้อยู่ปนกับวิธีจ่ายอื่น
    with_free = sample + [
        {"total_net_sales": "500.00", "payment_method": ["no_revenue"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-18 08:00:00 +0000 UTC"},
        {"total_net_sales": "700.00", "payment_method": ["cash", "no_revenue"],
         "bill_status": "SUCCESS", "bill_closed_at": "2026-08-18 09:00:00 +0000 UTC"},
    ]
    assert daily_totals(with_free)[date(2026, 8, 18)] == Decimal("380.00"), \
        "บิล no_revenue ต้องไม่เข้ายอด แม้จ่ายด้วยวิธีอื่นปนอยู่"

    # ทศนิยมต้องไม่เพี้ยน — float จะได้ 0.30000000000000004
    cents = [{"total_net_sales": v, "payment_method": ["cash"], "bill_status": "SUCCESS",
              "bill_closed_at": "2026-08-18 07:00:00 +0000 UTC"} for v in ("0.10", "0.20")]
    assert daily_totals(cents)[date(2026, 8, 18)] == Decimal("0.30")

    out = format_rows(sample, date(2026, 8, 18), date(2026, 8, 22))
    assert len(out["รายวัน"]) == 5, "ต้องมีครบทุกวันในช่วง รวมวันที่ไม่มีบิล"
    assert out["รายวัน"][3] == {"วันที่": "2026-08-21", "ยอดขายสุทธิ": 0.0}, "วันไม่มีบิล = 0"
    assert out["ยอดรวมทั้งช่วง"] == 625.0
    assert out["จำนวนบิลที่นับ"] == 4 and out["จำนวนบิลที่ตัดออก"] == 0

    out2 = format_rows(with_free, date(2026, 8, 18), date(2026, 8, 22))
    assert out2["จำนวนบิลที่ตัดออก"] == 2 and out2["ยอดรวมทั้งช่วง"] == 625.0

    assert format_rows([], date(2026, 8, 18), date(2026, 8, 18))["ยอดรวมทั้งช่วง"] == 0.0

    print("✅ ผ่าน — แปลงวัน UTC→ไทย (รวมขอบ 17:00), ตัด no_revenue, Decimal ไม่เพี้ยน, ช่วงวันครบ")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args and args[0] == "test":
        demo()
    elif len(args) >= 2:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
        print(json.dumps(summary(args[0], args[1], days), ensure_ascii=False, indent=1))
    else:
        print("usage: python -m modules.sales <owner_id> <branch_id> [--days N] | test")
