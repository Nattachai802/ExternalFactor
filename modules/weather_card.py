"""MODULE — การ์ดอากาศด้านบน (ST-01 §weather_hero)

ป้ายเตือน + สภาพอากาศปัจจุบัน + ค่าฝุ่น + แถบพยากรณ์ 10 วัน ในเส้นเดียว

ระดับ/ข้อความของป้ายไม่ได้คิดที่นี่ — เรียก modules.badge (สเปก §A2/§A4) ตัวเดิม
ไฟล์นี้ทำแค่ 2 อย่างที่ยังไม่มี: resolve icon/background และประกอบรูปการ์ด

ไม่มี null ในทุก field: ไม่มีข้อมูล = 0 / "" (frontend บังคับมา)

    python -m modules.weather_card test    # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""
from datetime import date, datetime, timedelta, timezone

TH_TZ = timezone(timedelta(hours=7))

WEEKDAY_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์"]


def resolve_visual(weather_id: int | None, is_day: bool) -> tuple[str, str]:
    """OWM weather.id → (icon_key, background_key) ตามตาราง §resolve

    ลำดับการเช็คสำคัญ: 520 กับ 500 เป็นฝนปรอย ส่วน 501-531 ที่เหลือเป็นฝนหนัก
    หิมะยังไม่มีไฟล์ไอคอน ใช้ many_clouds ไปก่อน (ไทยไม่เจอหิมะ)
    """
    wid = weather_id or 0

    if wid == 800:
        return ("clear_day", "clear_day") if is_day else ("night", "clear_night")
    if wid in (801, 802):
        return ("few_clouds_day", "cloudy_day") if is_day else ("night", "cloudy_night")
    if wid in (803, 804) or 700 <= wid < 800:
        return ("many_clouds", "cloudy_day") if is_day else ("many_clouds", "cloudy_night")
    if 300 <= wid < 400 or wid in (500, 520):
        return ("rain", "drizzle_day") if is_day else ("rain", "drizzle_night")
    if 200 <= wid < 300 or 501 <= wid <= 531:
        return ("rain", "rain_heavy_day") if is_day else ("rain", "rain_heavy_night")
    if 600 <= wid < 700:
        return ("many_clouds", "snow_day") if is_day else ("many_clouds", "snow_night")

    # id ที่ไม่รู้จัก (OWM เพิ่มใหม่) — เมฆมากเป็นค่ากลางที่ไม่หลอกคนดู
    return ("many_clouds", "cloudy_day") if is_day else ("many_clouds", "cloudy_night")


def is_daytime(now: datetime, sunrise, sunset) -> bool:
    """กลางวันไหม — ตัดด้วยพระอาทิตย์ขึ้น/ตกของพิกัดสาขา ไม่ใช่เวลาตายตัว

    เชียงรายกับหาดใหญ่ค่ำต่างกันราว 20 นาที ใช้เวลากลางไม่ได้
    ไม่มีข้อมูลพระอาทิตย์ → ถือว่ากลางวันช่วง 06:00-18:00 เวลาไทย
    """
    if not sunrise or not sunset:
        return 6 <= now.astimezone(TH_TZ).hour < 18
    return sunrise <= now <= sunset


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _label(day: date, today: date) -> str:
    """'วันนี้' สำหรับวันแรก ที่เหลือ '<เลขวันที่> <ชื่อวัน>' เช่น '5 ศุกร์'"""
    return "วันนี้" if day == today else f"{day.day} {WEEKDAY_TH[day.weekday()]}"


def format_card(hourly: list[dict], daily: list[dict], aqi: int | None,
                badge: dict, now: datetime | None = None) -> dict:
    """ประกอบการ์ด — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต ไม่แตะ DB

    hourly/daily : แถวจาก modules.weather (แถวแรกของ hourly = ชั่วโมงปัจจุบัน)
    aqi          : ค่าล่าสุดจาก modules.air_quality (None = แหล่งฝุ่นล่ม → ส่ง 0)
    badge        : ผลจาก modules.badge.evaluate() (key ภาษาไทย)
    """
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TH_TZ).date()

    current = hourly[0] if hourly else {}
    today_row = next((r for r in daily if r["target_date"] == today), daily[0] if daily else {})
    day_now = is_daytime(now, today_row.get("sunrise"), today_row.get("sunset"))
    icon_key, background_key = resolve_visual(current.get("weather_id"), day_now)

    updated = [r["updated_at"] for r in (*hourly, *daily) if r.get("updated_at")]
    observed = max(updated) if updated else now
    ago = max(int((now - observed).total_seconds() // 60), 0)

    return {
        "observed_at": observed.astimezone(TH_TZ).isoformat(),
        "updated_ago_minutes": ago,
        "badge": {
            "level": badge.get("ระดับ", 0),
            "label": badge.get("ป้าย", ""),
            "reason": badge.get("บรรทัดที่ 2", ""),
        },
        "headline": current.get("condition") or "",
        "icon_key": icon_key,
        "background_key": background_key,
        "temp": _num(current.get("temp")),
        "feels_like": _num(current.get("feels_like")),
        # แหล่งฝุ่นล่มส่ง 0 ไม่ใช่ null (ข้อตกลงกับ frontend)
        "aqi_us": int(aqi) if aqi is not None else 0,
        "daily": [{
            "date": r["target_date"].isoformat(),
            "label": _label(r["target_date"], today),
            "is_today": r["target_date"] == today,
            # พยากรณ์รายวันไม่มีกลางคืน ใช้ชุดกลางวันเสมอ ห้ามส่ง night
            "icon_key": resolve_visual(r.get("weather_id"), True)[0],
            "temp_max": _num(r.get("temp_max")),
            "temp_min": _num(r.get("temp_min")),
        } for r in daily],
    }


def demo():
    """self-check — ไม่ต่อเน็ต ไม่แตะ DB"""
    # ── ตารางไอคอน ────────────────────────────────────────────
    assert resolve_visual(800, True) == ("clear_day", "clear_day")
    assert resolve_visual(800, False) == ("night", "clear_night")
    assert resolve_visual(801, True) == ("few_clouds_day", "cloudy_day")
    assert resolve_visual(802, False) == ("night", "cloudy_night")
    assert resolve_visual(804, True) == ("many_clouds", "cloudy_day")
    assert resolve_visual(741, True) == ("many_clouds", "cloudy_day"), "หมอกยุบไป many_clouds"
    assert resolve_visual(300, True) == ("rain", "drizzle_day")
    assert resolve_visual(500, True) == ("rain", "drizzle_day")
    assert resolve_visual(520, True) == ("rain", "drizzle_day"), "520 เป็นปรอย ไม่ใช่หนัก"
    assert resolve_visual(502, True) == ("rain", "rain_heavy_day")
    assert resolve_visual(531, False) == ("rain", "rain_heavy_night")
    assert resolve_visual(211, True) == ("rain", "rain_heavy_day"), "พายุใช้ชุดฝนหนัก"
    assert resolve_visual(601, True) == ("many_clouds", "snow_day"), "หิมะไม่มีไอคอนของตัวเอง"
    assert resolve_visual(None, True) == ("many_clouds", "cloudy_day"), "id ว่างต้องไม่พัง"

    # ── กลางวัน/กลางคืนจากพระอาทิตย์จริง ──────────────────────
    noon = datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)          # 12:00 ไทย
    rise = datetime(2026, 9, 7, 23, 10, tzinfo=timezone.utc)        # 06:10 ไทย
    set_ = datetime(2026, 9, 8, 11, 20, tzinfo=timezone.utc)        # 18:20 ไทย
    assert is_daytime(noon, rise, set_)
    assert not is_daytime(datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc), rise, set_)
    assert is_daytime(noon, None, None), "ไม่มีข้อมูลพระอาทิตย์ต้อง fallback ไม่ใช่พัง"

    # ── ประกอบการ์ด ───────────────────────────────────────────
    now = datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)
    hourly = [{"ts": now, "condition": "มีเมฆมาก", "weather_id": 804,
               "temp": 38.0, "feels_like": 39.0, "pop": 10,
               "updated_at": now - timedelta(minutes=60)}]
    daily = [{"target_date": date(2026, 9, 8) + timedelta(days=i), "weather_id": 804 if i else 804,
              "temp_max": 38.0 - i, "temp_min": 28.0, "sunrise": rise, "sunset": set_,
              "updated_at": now - timedelta(minutes=60)} for i in range(10)]
    badge = {"ระดับ": 2, "ป้าย": "มีผลต่อยอดขาย", "reason": "x",
             "บรรทัดที่ 2": "อากาศร้อนจัด 39 °C"}

    out = format_card(hourly, daily, 42, badge, now)
    assert out["observed_at"] == "2026-09-08T11:00:00+07:00", out["observed_at"]
    assert out["updated_ago_minutes"] == 60
    assert out["badge"] == {"level": 2, "label": "มีผลต่อยอดขาย", "reason": "อากาศร้อนจัด 39 °C"}
    assert out["headline"] == "มีเมฆมาก" and out["icon_key"] == "many_clouds"
    assert out["background_key"] == "cloudy_day", "เที่ยงวันต้องเป็นชุดกลางวัน"
    assert (out["temp"], out["feels_like"], out["aqi_us"]) == (38.0, 39.0, 42)

    assert len(out["daily"]) == 10
    assert out["daily"][0]["label"] == "วันนี้" and out["daily"][0]["is_today"]
    assert out["daily"][1]["label"] == "9 พุธ", out["daily"][1]["label"]
    assert not any(d["is_today"] for d in out["daily"][1:]), "วันนี้ต้องมีวันเดียว"

    # กลางคืนต้องไม่หลุดไปแถบรายวัน
    night = format_card(hourly, daily, 42, badge,
                        datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc))
    assert night["icon_key"] == "many_clouds" and night["background_key"] == "cloudy_night"
    assert all(d["icon_key"] != "night" for d in night["daily"]), "รายวันห้ามใช้ชุดกลางคืน"

    # ไม่มีข้อมูลเลย: ห้าม null หลุด ห้าม error
    blank = format_card([], [], None, {}, now)
    assert blank["aqi_us"] == 0 and blank["temp"] == 0.0 and blank["headline"] == ""
    assert blank["badge"] == {"level": 0, "label": "", "reason": ""}
    assert blank["daily"] == [] and blank["updated_ago_minutes"] == 0

    print("✅ ผ่าน — ตารางไอคอน/พื้นหลังครบทุกช่วง id, กลางวัน-คืนจากพระอาทิตย์จริง, "
          "label รายวัน, ไม่มี null, ข้อมูลว่างไม่พัง")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        print("usage: python -m modules.weather_card test")
