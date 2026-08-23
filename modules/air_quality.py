"""MODULE — คุณภาพอากาศ (OpenWeatherMap Air Pollution)

คู่กับ weather.py: ts ของ Air Pollution forecast ตรงกับ timeline/1h ของ weather เป๊ะทุกจุด
(ยืนยันแล้วด้วยการยิงจริงเทียบ) จึงยึดเกณฑ์เดียวกับ weather ทั้งหมด — TTL 6 ชม., หน้าต่าง
20 ชม.เท่ากัน — เพื่อให้ frontend เอา ts ไป match กับข้อมูลอากาศตรงๆ ได้โดยไม่ต้องเดา

คนละตาราง/endpoint จาก weather เพราะ concern คนละเรื่อง (threshold ฝุ่น, action คนละแบบ)
แต่ PK (province, district, ts) เหมือนกันเป๊ะ — join กันได้ถ้าต้องใช้

    python -m modules.air_quality              # summary ที่พิกัด default
    python -m modules.air_quality test          # self-check (ไม่ต่อเน็ต)
"""
import os
from datetime import datetime, timedelta, timezone

import requests

import db

BASE = "https://api.openweathermap.org/data/2.5/air_pollution"
SOURCE = "OpenWeatherMap Air Pollution"
TH_TZ = timezone(timedelta(hours=7))
DEFAULT_LAT, DEFAULT_LON = 13.8479, 100.5697

TTL = timedelta(hours=6)      # เท่ากับ weather hourly — คีย์เวลาชุดเดียวกัน
HORIZON_HOURS = 20            # เท่ากับหน้าต่างของ weather hourly ไม่ใช่ 96 จุดเต็มที่ API ให้ได้

# EPA breakpoints (Conc_low, Conc_high, AQI_low, AQI_high) — หน่วย µg/m³
# ใช้สูตร US AQI มาตรฐานแต่ป้อนค่าความเข้มข้นรายชั่วโมงแทนค่าเฉลี่ย 24 ชม.ที่ EPA ออกแบบไว้จริง
# ตัวเลขจึงเป็นค่าประมาณ ไม่ใช่ US AQI ทางการ — พอเทียบระดับได้ ไม่ควรใช้แทนสถานีวัดจริง
PM25_BREAKPOINTS = [
    (0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
    (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300),
    (250.5, 350.4, 301, 400), (350.5, 500.4, 401, 500),
]
PM10_BREAKPOINTS = [
    (0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
    (255, 354, 151, 200), (355, 424, 201, 300),
    (425, 504, 301, 400), (505, 604, 401, 500),
]


def _aqi_from_breakpoints(conc: float, table: list[tuple]) -> int | None:
    if conc is None:
        return None
    for lo, hi, aqi_lo, aqi_hi in table:
        if lo <= conc <= hi:
            return round((aqi_hi - aqi_lo) / (hi - lo) * (conc - lo) + aqi_lo)
    return 500 if conc > table[-1][1] else None  # เกินสเกล = แย่สุด (500), ต่ำกว่า 0 ไม่ควรเกิดแต่กันไว้


def us_aqi(pm2_5: float | None, pm10: float | None) -> int | None:
    """US AQI ประมาณจาก PM2.5/PM10 — ใช้ค่าสูงสุดของสองตัว (มาตรฐาน EPA: AQI = max ของทุก pollutant)"""
    candidates = [a for a in (_aqi_from_breakpoints(pm2_5, PM25_BREAKPOINTS),
                              _aqi_from_breakpoints(pm10, PM10_BREAKPOINTS)) if a is not None]
    return max(candidates) if candidates else None


def _get(path: str, lat: float, lon: float) -> dict:
    key = os.getenv("OPENWEATHER_API_KEY")
    if not key:
        raise RuntimeError("ไม่พบ OPENWEATHER_API_KEY ใน environment")
    r = requests.get(f"{BASE}/{path}", params={"lat": lat, "lon": lon, "appid": key}, timeout=20)
    r.raise_for_status()
    return r.json()


def hourly_rows(province: str, district: str, lat: float, lon: float) -> list[dict]:
    """แถว fact_air_quality_hourly — ตัดเหลือ HORIZON_HOURS จุดแรกให้พอดีกับหน้าต่างของ weather"""
    now = datetime.now(timezone.utc)
    data = _get("forecast", lat, lon).get("list", [])[:HORIZON_HOURS]
    rows = []
    for p in data:
        c = p.get("components", {})
        rows.append({
            "province": province, "district": district,
            "ts": datetime.fromtimestamp(p["dt"], timezone.utc),
            "aqi_us": us_aqi(c.get("pm2_5"), c.get("pm10")),
            "pm2_5": c.get("pm2_5"), "pm10": c.get("pm10"),
            "co": c.get("co"), "no": c.get("no"), "no2": c.get("no2"),
            "o3": c.get("o3"), "so2": c.get("so2"), "nh3": c.get("nh3"),
            "ref_lat": lat, "ref_lon": lon, "updated_at": now,
        })
    return rows


def _where(province: str, district: str) -> tuple[str, tuple]:
    return "province = %s AND district = %s", (province, district)


def _fresh(rows: list[dict]) -> bool:
    """ยึดแถวเก่าสุดในชุด เหมือน weather._fresh — กันแถวเก่าค้างท้ายชุดตอน API คืนสั้นลง"""
    if not rows:
        return False
    return min(r["updated_at"] for r in rows) > datetime.now(timezone.utc) - TTL


def load(province: str, district: str) -> list[dict]:
    where, params = _where(province, district)
    now = datetime.now(timezone.utc)
    return db.rows_between("fact_air_quality_hourly", "ts",
                           now - timedelta(hours=1), now + timedelta(hours=HORIZON_HOURS + 1),
                           where=where, params=params, order_by="ts")


def get(province: str, district: str, lat: float, lon: float) -> tuple[list[dict], list[tuple]]:
    """คืน (rows, to_save) — to_save ไม่ว่างเมื่อยิงสดมาใหม่ ผู้เรียกเอาไปเขียน DB เอง (เช่นผ่าน BackgroundTasks)"""
    rows = load(province, district)
    if _fresh(rows):
        return rows, []
    rows = hourly_rows(province, district, lat, lon)
    return rows, [("fact_air_quality_hourly", rows)]


AQI_LEVEL = [
    (50, "ดี"), (100, "ปานกลาง"), (150, "มีผลต่อกลุ่มเสี่ยง"),
    (200, "ไม่ดีต่อสุขภาพ"), (300, "ไม่ดีต่อสุขภาพมาก"), (500, "อันตราย"),
]


def _level(aqi: int | None) -> str | None:
    if aqi is None:
        return None
    for ceiling, label in AQI_LEVEL:
        if aqi <= ceiling:
            return label
    return AQI_LEVEL[-1][1]


def _num(v):
    return float(v) if v is not None else None


def format_rows(rows: list[dict], hours: int | None = None) -> dict:
    """แถว DB → record ภาษาไทย — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต ไม่แตะ DB"""
    rows = rows[:hours] if hours else rows
    updated = [r["updated_at"] for r in rows if r.get("updated_at")]
    return {
        "รายชั่วโมง": [{
            "เวลา": r["ts"].astimezone(TH_TZ).strftime("%Y-%m-%d %H:%M"),
            "AQI (US)": r.get("aqi_us"),
            "ระดับ": _level(r.get("aqi_us")),
            "PM2.5 (µg/m³)": _num(r.get("pm2_5")),
            "PM10 (µg/m³)": _num(r.get("pm10")),
            "CO (µg/m³)": _num(r.get("co")),
            "NO (µg/m³)": _num(r.get("no")),
            "NO2 (µg/m³)": _num(r.get("no2")),
            "O3 (µg/m³)": _num(r.get("o3")),
            "SO2 (µg/m³)": _num(r.get("so2")),
            "NH3 (µg/m³)": _num(r.get("nh3")),
        } for r in rows],
        "อัปเดตล่าสุด": max(updated).astimezone(TH_TZ).isoformat() if updated else None,
        "แหล่งข้อมูล": SOURCE,
    }


def summary(province: str = "Bangkok", district: str = "Chatuchak District",
            lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict:
    """ยิงสด + เขียน DB ทันที — ใช้ตอนรันมือ ไม่ใช่ทาง endpoint"""
    rows, to_save = get(province, district, lat, lon)
    for table, save_rows in to_save:
        db.save_rows(table, save_rows)
    out = {"พื้นที่": {"จังหวัด": province, "เขต/อำเภอ": district}}
    out.update(format_rows(rows))
    return out


def demo():
    """self-check — สูตร US AQI + TTL + แปลงแถว ไม่ต่อเน็ต ไม่แตะ DB"""
    now = datetime.now(timezone.utc)

    # ค่าอ้างอิงจากตาราง EPA จริง — PM2.5=12.0 ต้องได้ AQI=50 พอดี (ขอบบนของช่วง "ดี")
    assert us_aqi(12.0, None) == 50
    assert us_aqi(35.4, None) == 100
    assert us_aqi(0, None) == 0
    assert us_aqi(None, None) is None, "ไม่มีทั้ง pm2_5/pm10 ต้องคืน None ไม่ใช่พัง"
    assert us_aqi(12.0, 200) > 50, "PM10 แย่กว่าต้องชนะ (AQI = max ของ pollutant)"
    assert us_aqi(600, 0) == 500, "เกินสเกลบนสุดต้องคืน 500 ไม่ใช่ None"

    assert _level(30) == "ดี" and _level(75) == "ปานกลาง" and _level(400) == "อันตราย"
    assert _level(None) is None

    assert not _fresh([]), "ไม่มีแถวเลย = ไม่สด"
    assert _fresh([{"updated_at": now - timedelta(hours=1)}])
    assert not _fresh([{"updated_at": now - timedelta(hours=7)}])

    rows = [{"ts": now, "aqi_us": 42, "pm2_5": 10.0, "pm10": 20.0, "co": 200.0,
             "no": 1.0, "no2": 5.0, "o3": 30.0, "so2": 2.0, "nh3": 1.0, "updated_at": now}]
    out = format_rows(rows)
    assert out["รายชั่วโมง"][0]["AQI (US)"] == 42 and out["รายชั่วโมง"][0]["ระดับ"] == "ดี"
    assert format_rows([])["อัปเดตล่าสุด"] is None
    assert len(format_rows(rows * 3, hours=2)["รายชั่วโมง"]) == 2

    print("✅ ผ่าน — สูตร US AQI ตรงตาราง EPA, ระดับคุณภาพอากาศ, TTL, แปลงแถวไทย")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        def opt(flag, cast):
            return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else None

        print(json.dumps(summary(
            lat=opt("--lat", float) or DEFAULT_LAT,
            lon=opt("--lon", float) or DEFAULT_LON,
        ), ensure_ascii=False, indent=1, default=str))
