"""MODULE — Badge เตือนสภาพอากาศ (สเปก SuperTrend §A2)

คำนวณ "ระดับความรุนแรงของอากาศ" (0-3) ตัวเดียว ที่ทั้ง Badge และข้อความบรรทัดที่ 2 ใช้ร่วมกัน
ไม่ scrape/ไม่ต่อเน็ต — รับผลจาก weather.py + air_quality.py ที่ยิงมาแล้วมาคำนวณต่อ

เข้าหลายเงื่อนไข → เอาระดับสูงสุด ไม่บวกกัน (ตามสเปก)
ยังไม่มีแหล่งข้อมูล "ประกาศเตือนภัยพิบัติ" ในระบบ — พารามิเตอร์ disaster_alert รับไว้เผื่ออนาคต
ดีฟอลต์ False เสมอในตอนนี้ (ดูหมายเหตุใน app_v1.py จุดที่เรียกใช้)

    python -m modules.badge test    # self-check (ไม่ต่อเน็ต)
"""
from datetime import date, datetime, timedelta, timezone

TH_TZ = timezone(timedelta(hours=7))

# weather.id ของ OWM ที่เข้าเงื่อนไขพิเศษ (ดูตาราง §A3) — ตัวอื่นไม่กระทบระดับ ไม่ต้อง map ครบ 9 หมวด
THUNDERSTORM_IDS = {200, 201, 202, 210, 211, 212, 221, 230, 231, 232, 771, 781}
HEAVY_RAIN_IDS = {502, 503, 504, 522}
FOG_IDS = {701, 711, 721, 731, 741, 751, 761, 762}

BADGE_TEXT = {3: "กระทบยอดขายทั้งวัน", 2: "มีผลต่อยอดขาย", 1: "ไม่กระทบยอดขาย", 0: "ไม่กระทบยอดขาย"}


def _aqi_level(aqi: int | None) -> int:
    """ระดับความรุนแรงจาก AQI เพียงอย่างเดียว (ไม่ใช่ระดับรวม) — ตามตาราง §A5"""
    if aqi is None:
        return 0
    if aqi >= 151:
        return 3
    if aqi >= 101:
        return 2
    if aqi >= 51:
        return 1
    return 0


def _temp_level(temp_max: float | None) -> int:
    if temp_max is None:
        return 0
    if temp_max >= 40:
        return 2
    if temp_max >= 35:
        return 1
    return 0


def pop_periods_remaining_today(hourly: list[dict], now: datetime | None = None) -> list[dict]:
    """กลุ่มโอกาสฝนช่วง 3 ชม. เฉพาะ "ช่วงที่เหลือของวันนี้" — คืน [{start, end, pop}] เรียงตามเวลา

    ใช้ bucket 3 ชม.เดียวกับ weather.group_pop_3h — สเปกให้ตัวอย่างมาเป็นช่วง 3 ชม.พอดี
    (12:00–15:00, 15:00–18:00, ...) จึงยึด granularity เดิม ไม่ทำ sliding window แยกใหม่
    """
    now = now or datetime.now(TH_TZ)
    today = now.astimezone(TH_TZ).date()

    buckets: dict[tuple[int, int], list[int]] = {}
    for r in hourly:
        t = r["ts"].astimezone(TH_TZ)
        if t.date() != today or t < now.replace(minute=0, second=0, microsecond=0):
            continue
        start = (t.hour // 3) * 3
        buckets.setdefault((start, start + 3), []).append(int(r.get("pop") or 0))

    return [{"start": f"{s:02d}:00", "end": f"{e:02d}:00", "pop": max(vals)}
            for (s, e), vals in sorted(buckets.items())]


def _first_run(periods: list[dict], threshold: int) -> dict | None:
    """run แรก (เรียงตามเวลา) ของ bucket ติดกันที่ pop >= threshold — merge ช่วงที่ต่อกัน

    คืน {start, end, pop} โดย pop = ค่าสูงสุดใน run (ตรงตัวอย่างสเปก: 85%+80% ติดกัน → โชว์ 85%)
    """
    run: list[dict] = []
    for p in periods:
        if p["pop"] >= threshold:
            if run and run[-1]["end"] != p["start"]:
                break  # ไม่ติดกัน — run แรกจบแค่นี้
            run.append(p)
        elif run:
            break
    if not run:
        return None
    return {"start": run[0]["start"], "end": run[-1]["end"], "pop": max(p["pop"] for p in run)}


def evaluate(current_weather_id: int | None, pop_periods: list[dict],
            temp_max: float | None, aqi: int | None, disaster_alert: bool = False) -> dict:
    """คำนวณระดับ + badge + ข้อความ 2 บรรทัด ตามสเปก §A2/§A4 ทั้งหมด — ฟังก์ชันบริสุทธิ์"""
    is_thunder = current_weather_id in THUNDERSTORM_IDS
    is_heavy_rain = current_weather_id in HEAVY_RAIN_IDS
    is_fog = current_weather_id in FOG_IDS

    run70 = _first_run(pop_periods, 70)
    run50 = _first_run(pop_periods, 50)  # ใช้ตอนไม่มี run70 เท่านั้น (คำ-08 อยู่หลัง คำ-04)

    level = max(
        3 if disaster_alert else 0,
        3 if _aqi_level(aqi) == 3 else 0,
        2 if (is_thunder or is_heavy_rain) else 0,
        2 if run70 else 0,
        2 if _aqi_level(aqi) == 2 else 0,
        2 if _temp_level(temp_max) == 2 else 0,
        1 if is_fog else 0,
        1 if (run50 and not run70) else 0,
        1 if _temp_level(temp_max) == 1 else 0,
        1 if _aqi_level(aqi) == 1 else 0,
    )

    # ลำดับตาม §A4 เป๊ะ — เอาตัวแรกที่เข้าเงื่อนไข ไม่ใช่ตัวรุนแรงสุด
    if disaster_alert:
        line2 = "มีประกาศเตือนภัยในพื้นที่ — โปรดติดตามประกาศจากทางการ"
    elif is_thunder:
        line2 = "อาจส่งผลกระทบ: พื้นที่นั่งกลางแจ้ง, อุปกรณ์ไฟฟ้านอกอาคาร, การเดินทาง และรอบจัดส่ง"
    elif is_heavy_rain:
        line2 = "อาจส่งผลกระทบ: พื้นที่นั่งกลางแจ้ง, การเดินทาง และรอบจัดส่ง"
    elif run70:
        line2 = f"มีโอกาสฝนตก {run70['pop']}% ช่วง {run70['start']}–{run70['end']} — อาจกระทบพื้นที่นั่งกลางแจ้งและรอบจัดส่ง"
    elif _aqi_level(aqi) == 3:
        line2 = f"ค่าฝุ่นอยู่ในระดับมีผลกระทบต่อสุขภาพ (AQI {aqi}) — อาจส่งผลต่อพื้นที่นั่งกลางแจ้งและลูกค้ากลุ่มเสี่ยง"
    elif _aqi_level(aqi) == 2:
        line2 = f"ค่าฝุ่นเริ่มมีผลกระทบต่อกลุ่มเสี่ยง (AQI {aqi}) — อาจส่งผลต่อพื้นที่นั่งกลางแจ้ง"
    elif _temp_level(temp_max) == 2:
        line2 = f"อากาศร้อนจัด {round(temp_max)} °C — อาจส่งผลกระทบต่อพื้นที่นั่งกลางแจ้งและภาระระบบความเย็น"
    elif run50:
        line2 = f"อาจมีฝนตกในช่วง {run50['start']}–{run50['end']}"
    elif is_fog:
        line2 = "ทัศนวิสัยต่ำ — อาจส่งผลต่อการเดินทางและรอบจัดส่ง"
    elif _temp_level(temp_max) == 1:
        line2 = f"อากาศร้อน {round(temp_max)} °C — อาจส่งผลกระทบต่อพื้นที่นั่งกลางแจ้ง"
    else:
        line2 = "สภาพอากาศไม่กระทบการขายวันนี้"

    return {"ระดับ": level, "ป้าย": BADGE_TEXT[level], "บรรทัดที่ 2": line2}


def demo():
    """self-check — เดินตามตัวอย่างครบวงจรในสเปกเป๊ะ + ขอบเขตแต่ละเงื่อนไข ไม่ต่อเน็ต ไม่แตะ DB"""
    now = datetime(2026, 8, 22, 10, 0, tzinfo=TH_TZ)  # ช่องปัจจุบัน 09:00-12:00 ตามตัวอย่างสเปก
    hourly = []
    for h, pop in [(12, 45), (13, 45), (14, 45), (15, 85), (16, 85), (17, 85),
                   (18, 80), (19, 80), (20, 80), (21, 10), (22, 10), (23, 10)]:
        hourly.append({"ts": now.replace(hour=h % 24), "pop": pop})

    periods = pop_periods_remaining_today(hourly, now)
    assert periods[0] == {"start": "12:00", "end": "15:00", "pop": 45}

    out = evaluate(804, periods, temp_max=33, aqi=62)
    assert out["ระดับ"] == 2, out
    assert out["ป้าย"] == "มีผลต่อยอดขาย"
    assert out["บรรทัดที่ 2"] == "มีโอกาสฝนตก 85% ช่วง 15:00–21:00 — อาจกระทบพื้นที่นั่งกลางแจ้งและรอบจัดส่ง", out

    # ลำดับความสำคัญของข้อความ — คำ-01 มาก่อนทุกอย่างแม้ระดับเท่ากัน
    assert evaluate(200, [], None, None, disaster_alert=True)["บรรทัดที่ 2"].startswith("มีประกาศเตือนภัย")

    # ฝนฟ้าคะนอง (thunderstorm id) → ระดับ 2 แม้ pop/aqi/temp ปกติหมด
    out = evaluate(211, [], temp_max=30, aqi=20)
    assert out["ระดับ"] == 2 and "อุปกรณ์ไฟฟ้านอกอาคาร" in out["บรรทัดที่ 2"]

    # ฝนหนัก
    out = evaluate(502, [], temp_max=30, aqi=20)
    assert out["ระดับ"] == 2 and "รอบจัดส่ง" in out["บรรทัดที่ 2"]

    # หมอก → ระดับ 1
    out = evaluate(741, [], temp_max=30, aqi=20)
    assert out["ระดับ"] == 1 and out["บรรทัดที่ 2"] == "ทัศนวิสัยต่ำ — อาจส่งผลต่อการเดินทางและรอบจัดส่ง"

    # AQI ระดับ 3 (>=151) → ระดับรวม 3 แม้ทุกอย่างอื่นปกติ
    out = evaluate(800, [], temp_max=25, aqi=180)
    assert out["ระดับ"] == 3 and out["ป้าย"] == "กระทบยอดขายทั้งวัน"
    assert "AQI 180" in out["บรรทัดที่ 2"]

    # อุณหภูมิ >= 40 → ระดับ 2 (ไม่ใช่ 3)
    out = evaluate(800, [], temp_max=41, aqi=None)
    assert out["ระดับ"] == 2 and "ร้อนจัด" in out["บรรทัดที่ 2"]

    # อุณหภูมิ 35-39.9 → ระดับ 1
    out = evaluate(800, [], temp_max=36, aqi=None)
    assert out["ระดับ"] == 1 and out["บรรทัดที่ 2"].startswith("อากาศร้อน ")

    # ไม่เข้าเงื่อนไขไหนเลย → คำ-11
    out = evaluate(800, [], temp_max=28, aqi=10)
    assert out["ระดับ"] == 0 and out["บรรทัดที่ 2"] == "สภาพอากาศไม่กระทบการขายวันนี้"

    # เข้าหลายข้อพร้อมกัน → เอาสูงสุด ไม่บวก (ฝุ่น 120=ระดับ2 + ร้อน 36=ระดับ1 → 2 ไม่ใช่ 3)
    out = evaluate(800, [], temp_max=36, aqi=120)
    assert out["ระดับ"] == 2, "ต้องเอาระดับสูงสุด ไม่บวกกัน"

    # pop 50-69% (ไม่ถึง 70) → ระดับ 1, คำ-08
    periods_50 = [{"start": "12:00", "end": "15:00", "pop": 55}]
    out = evaluate(800, periods_50, temp_max=28, aqi=10)
    assert out["ระดับ"] == 1 and out["บรรทัดที่ 2"] == "อาจมีฝนตกในช่วง 12:00–15:00"

    print("✅ ผ่าน — ตัวอย่างสเปกเป๊ะ, ทุกเงื่อนไขระดับ 0-3, ลำดับข้อความ §A4, กฎเอาสูงสุดไม่บวกกัน")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        print("usage: python -m modules.badge test")
