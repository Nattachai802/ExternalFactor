"""MODULE — การ์ดพยากรณ์ยอดขาย (ST-01 §sales_forecast)

กราฟแท่ง 7 วัน (จันทร์-อาทิตย์ ของสัปดาห์ที่มีวันนี้) — ยอดจริง + ค่าพยากรณ์ ในเส้นเดียว

เส้นเดิมใช้ไม่ได้: /api/v1/sale-forecast คืนยอดรวมก้อนเดียวต่อช่วง (ต้องยิง 7 รอบ)
และยังต้องไปดึง /api/v1/sales อีกเส้นให้หน้าบ้าน join เอง — ที่นี่รวมให้เสร็จ

ไม่มี null ในทุก field: ไม่มีข้อมูล = 0.0 (frontend บังคับมา)

    python -m modules.sales_forecast_card test    # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""
from datetime import date, timedelta

WEEKDAY_ABBR = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]        # date.weekday(): จันทร์=0
WEEKDAY_FULL = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
MONTH_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
              "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def week_window(today: date) -> tuple[date, date]:
    """จันทร์-อาทิตย์ ของสัปดาห์ที่มี today (ไม่ใช่ 7 วันย้อนหลังตายตัว)"""
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


def _headline(day: date, value: float) -> str:
    """'ศุกร์ 5 ส.ค. นี้ คาดว่ายอดขายจะอยู่ที่ 45,854 บาท' — จัดรูปแบบเสร็จก่อนส่ง"""
    return (f"{WEEKDAY_FULL[day.weekday()]} {day.day} {MONTH_ABBR[day.month - 1]} นี้ "
            f"คาดว่ายอดขายจะอยู่ที่ {value:,.0f} บาท")


def format_card(today: date, actual_by_date: dict[str, float],
                forecast_by_date: dict[str, float]) -> dict:
    """ประกอบการ์ด — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต ไม่แตะ DB

    actual_by_date / forecast_by_date: {"YYYY-MM-DD": ยอด} ขาดวันไหนถือเป็น 0.0
    ยอดจริงของวันนี้/อนาคตเป็น 0 เสมอ — บิลยังปิดไม่ครบวัน เอามาวาดกราฟจะดูเหมือนยอดตก
    (POS ตัดวันที่เที่ยงคืนไทย ยอดจริงของวันนี้จึงขึ้นพร้อมกันตอนข้ามวัน)
    """
    start, end = week_window(today)

    series, cursor = [], start
    while cursor <= end:
        iso = cursor.isoformat()
        series.append({
            "date": iso,
            "label": f"{WEEKDAY_ABBR[cursor.weekday()]} {cursor.day}",
            "is_today": cursor == today,
            "actual_net_sales": float(actual_by_date.get(iso, 0.0)) if cursor < today else 0.0,
            "forecast_value": float(forecast_by_date.get(iso, 0.0)),
        })
        cursor += timedelta(days=1)

    return {
        "window": {"start_date": start.isoformat(), "end_date": end.isoformat(),
                   "today": today.isoformat()},
        "series": series,
        "summary": {
            "headline": _headline(today, float(forecast_by_date.get(today.isoformat(), 0.0))),
            # ยังไม่มีตัวประเมินน้ำหนักผลกระทบ — ส่ง array ว่างไปก่อน ไม่ใช่ null
            "reasons": [],
        },
    }


def demo():
    """self-check — ไม่ต่อเน็ต ไม่แตะ DB"""
    today = date(2026, 9, 8)                     # อังคาร
    actual = {"2026-09-07": 41230.0, "2026-09-08": 999.0, "2026-09-09": 888.0}
    forecast = {"2026-09-07": 43900.0, "2026-09-08": 44120.0, "2026-09-09": 45854.0}

    out = format_card(today, actual, forecast)
    assert out["window"] == {"start_date": "2026-09-07", "end_date": "2026-09-13",
                             "today": "2026-09-08"}, out["window"]
    assert len(out["series"]) == 7
    assert [s["label"] for s in out["series"]] == \
        ["จ 7", "อ 8", "พ 9", "พฤ 10", "ศ 11", "ส 12", "อา 13"]
    assert sum(s["is_today"] for s in out["series"]) == 1, "ต้องมีวันนี้วันเดียว"

    past, now, future = out["series"][0], out["series"][1], out["series"][2]
    assert past["actual_net_sales"] == 41230.0 and past["forecast_value"] == 43900.0
    assert now["is_today"] and now["actual_net_sales"] == 0.0, "ยอดจริงของวันนี้ต้องเป็น 0"
    assert now["forecast_value"] == 44120.0
    assert future["actual_net_sales"] == 0.0, "อนาคตต้องไม่มียอดจริง แม้ POS จะส่งมา"

    # ห้ามมี null หลุดออกไปเลย ทุก field ต้องครบทุกแท่ง
    assert all(s[k] is not None for s in out["series"]
               for k in ("date", "label", "is_today", "actual_net_sales", "forecast_value"))

    assert out["summary"]["reasons"] == []
    assert out["summary"]["headline"] == "อังคาร 8 ก.ย. นี้ คาดว่ายอดขายจะอยู่ที่ 44,120 บาท", \
        out["summary"]["headline"]

    # สัปดาห์เริ่มจันทร์เสมอ — อาทิตย์ต้องได้ช่วงที่จบที่ตัวเอง ไม่ใช่เริ่มที่ตัวเอง
    assert week_window(date(2026, 9, 13)) == (date(2026, 9, 7), date(2026, 9, 13))
    assert week_window(date(2026, 9, 7)) == (date(2026, 9, 7), date(2026, 9, 13))

    # ไม่มีข้อมูลเลยต้องได้ 7 แท่งค่า 0 ไม่ใช่ error หรือ array ว่าง
    blank = format_card(today, {}, {})
    assert len(blank["series"]) == 7
    assert all(s["actual_net_sales"] == 0.0 and s["forecast_value"] == 0.0
               for s in blank["series"])
    assert "0 บาท" in blank["summary"]["headline"]

    print("✅ ผ่าน — สัปดาห์จันทร์-อาทิตย์, label, is_today, ยอดจริงวันนี้/อนาคตเป็น 0, ไม่มี null")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        import json
        from datetime import datetime, timezone
        th_today = datetime.now(timezone(timedelta(hours=7))).date()
        print(json.dumps(format_card(th_today, {}, {}), ensure_ascii=False, indent=1))
