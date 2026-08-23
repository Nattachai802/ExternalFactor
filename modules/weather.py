"""MODULE — สภาพอากาศ (OpenWeatherMap One Call 4.0)

ต่างจากโมดูลอื่นตรงที่ไม่ได้รันด้วย cron: อากาศเปลี่ยนตลอด และมีโควตา API จำกัด
(1,000 call/วัน) จึงใช้ DB เป็น cache แทน — endpoint อ่านจาก DB ก่อน หมดอายุค่อยยิง OWM

cache key = (จังหวัด, เขต/อำเภอ) ไม่ใช่ lat/lon ของสาขา — สาขาในเขตเดียวกันใช้แถวร่วมกัน
60 เขต × (1h + 1day) × 4 รอบ/วัน ≈ 360 call/วัน

    python -m modules.weather                 # summary ที่พิกัด default (เสนานิคม)
    python -m modules.weather --hours 6       # จำกัดรายชั่วโมง 6 ชม.
    python -m modules.weather test            # self-check (ไม่ต่อเน็ต)
"""
import os
from datetime import date, datetime, timedelta, timezone

import requests

import db

BASE = "https://api.openweathermap.org/data/4.0/onecall"
SOURCE = "OpenWeatherMap One Call 4.0"
TH_TZ = timezone(timedelta(hours=7))
DEFAULT_LAT, DEFAULT_LON = 13.8479, 100.5697   # เสนานิคม กรุงเทพฯ — ใช้เมื่อไม่ระบุพิกัด

# TTL ต่างกันเพราะข้อมูลเปลี่ยนคนละอัตรา — รายชั่วโมงขยับเร็วกว่ารายวันมาก
HOURLY_TTL = timedelta(hours=6)
DAILY_TTL = timedelta(hours=12)

# เพดานจริงของ API (ทดสอบแล้ว): 1h ให้ 20 จุด/หน้า, 1day ให้ 10 วัน
HOURLY_HOURS = 20
DAILY_DAYS = 10


def _get(path: str, lat: float, lon: float, **params) -> dict:
    key = os.getenv("OPENWEATHER_API_KEY")
    if not key:
        raise RuntimeError("ไม่พบ OPENWEATHER_API_KEY ใน environment")
    r = requests.get(f"{BASE}/{path}",
                     params={"lat": lat, "lon": lon, "units": "metric", "appid": key, **params},
                     timeout=20)
    r.raise_for_status()
    return r.json()


def _rain(value) -> float:
    """OWM ส่ง rain มาสองแบบ — {"1h": 0.5} รายชั่วโมง, ตัวเลขล้วนรายวัน, ไม่ตกก็ไม่มี key"""
    if isinstance(value, dict):
        return value.get("1h", 0) or 0
    return value or 0


def hourly_rows(province: str, district: str, lat: float, lon: float) -> list[dict]:
    """แถว fact_weather_hourly — ~20 ชม.ข้างหน้า (1 call), เก็บทุกฟิลด์ที่ OWM ส่งมา"""
    now = datetime.now(timezone.utc)
    rows = []
    for h in _get("timeline/1h", lat, lon, cnt=HOURLY_HOURS).get("data", []):
        weather = h.get("weather", [{}])[0]
        rows.append({
            "province": province, "district": district,
            "ts": datetime.fromtimestamp(h["dt"], timezone.utc),
            "condition": weather.get("description", ""),
            "weather_main": weather.get("main", ""), "weather_id": weather.get("id"),
            "weather_icon": weather.get("icon", ""),
            "temp": h.get("temp"), "feels_like": h.get("feels_like"),
            "pressure": h.get("pressure"), "humidity": h.get("humidity"),
            "dew_point": h.get("dew_point"), "uvi": h.get("uvi"),
            "clouds": h.get("clouds"), "visibility": h.get("visibility"),
            "wind_speed": h.get("wind_speed"), "wind_deg": h.get("wind_deg"),
            "wind_gust": h.get("wind_gust"),
            "pop": round(h.get("pop", 0) * 100), "rain_mm": _rain(h.get("rain")),
            "ref_lat": lat, "ref_lon": lon, "updated_at": now,
        })
    return rows


def _epoch(v) -> datetime | None:
    return datetime.fromtimestamp(v, timezone.utc) if v else None


def daily_rows(province: str, district: str, lat: float, lon: float) -> list[dict]:
    """แถว fact_weather_daily — 10 วันข้างหน้า (1 call), เก็บทุกฟิลด์ที่ OWM ส่งมา"""
    now = datetime.now(timezone.utc)
    rows = []
    for d in _get("timeline/1day", lat, lon, cnt=DAILY_DAYS).get("data", []):
        weather = d.get("weather", [{}])[0]
        temp, feels_like = d.get("temp") or {}, d.get("feels_like") or {}
        rows.append({
            "province": province, "district": district,
            "target_date": datetime.fromtimestamp(d["dt"], TH_TZ).date(),
            "condition": weather.get("description", ""),
            "weather_main": weather.get("main", ""), "weather_id": weather.get("id"),
            "weather_icon": weather.get("icon", ""),
            "temp_day": temp.get("day"), "temp_max": temp.get("max"), "temp_min": temp.get("min"),
            "temp_night": temp.get("night"), "temp_eve": temp.get("eve"), "temp_morn": temp.get("morn"),
            "feels_like_day": feels_like.get("day"), "feels_like_night": feels_like.get("night"),
            "feels_like_eve": feels_like.get("eve"), "feels_like_morn": feels_like.get("morn"),
            "pressure": d.get("pressure"), "humidity": d.get("humidity"),
            "wind_speed": d.get("wind_speed"), "wind_deg": d.get("wind_deg"),
            "clouds": d.get("clouds"), "uvi": d.get("uvi"),
            "sunrise": _epoch(d.get("sunrise")), "sunset": _epoch(d.get("sunset")),
            "moonrise": _epoch(d.get("moonrise")), "moonset": _epoch(d.get("moonset")),
            "moon_phase": d.get("moon_phase"),
            "pop": round(d.get("pop", 0) * 100), "rain_mm": _rain(d.get("rain")),
            "ref_lat": lat, "ref_lon": lon, "updated_at": now,
        })
    return rows


def _where(province: str, district: str) -> tuple[str, tuple]:
    return "province = %s AND district = %s", (province, district)


def _fresh(rows: list[dict], ttl: timedelta) -> bool:
    """ยังใช้ได้ไหม — ดูแถวที่เก่าที่สุดในชุด ไม่ใช่ใหม่สุด

    เขียนทั้งชุดพร้อมกันทุกครั้ง ปกติ updated_at เท่ากันหมด แต่ถ้าชุดใหม่สั้นกว่าชุดเก่า
    (API คืนน้อยลง) จะมีแถวเก่าค้างอยู่ท้ายชุด — ยึดแถวเก่าสุดถึงจะไม่เสิร์ฟของค้าง
    """
    if not rows:
        return False
    return min(r["updated_at"] for r in rows) > datetime.now(timezone.utc) - ttl


def load(province: str, district: str) -> tuple[list[dict], list[dict]]:
    """อ่านพยากรณ์ของเขตนั้นจาก DB — เฉพาะช่วงเวลาที่ยังไม่ผ่านไปแล้ว"""
    where, params = _where(province, district)
    now = datetime.now(timezone.utc)
    hourly = db.rows_between("fact_weather_hourly", "ts",
                             now - timedelta(hours=1), now + timedelta(hours=48),
                             where=where, params=params, order_by="ts")
    today = datetime.now(TH_TZ).date()
    daily = db.rows_between("fact_weather_daily", "target_date", today, today + timedelta(days=15),
                            where=where, params=params, order_by="target_date")
    return hourly, daily


def get(province: str, district: str, lat: float, lon: float):
    """พยากรณ์ของเขตนั้น + สิ่งที่ต้องเขียนกลับ DB

    คืน (hourly, daily, to_save) โดย to_save = [(ชื่อตาราง, แถว)] ที่เพิ่งยิงมาใหม่ —
    ผู้เรียก (endpoint) เอาไปเขียนผ่าน BackgroundTasks ตอบ user ไปก่อนไม่ต้องรอ DB
    ยิงเฉพาะส่วนที่หมดอายุ: รายชั่วโมงหมดก่อนรายวันเสมอ จะได้ไม่เปลืองโควตาไปกับรายวัน
    """
    hourly, daily = load(province, district)
    to_save = []

    if not _fresh(hourly, HOURLY_TTL):
        hourly = hourly_rows(province, district, lat, lon)
        to_save.append(("fact_weather_hourly", hourly))
    if not _fresh(daily, DAILY_TTL):
        daily = daily_rows(province, district, lat, lon)
        to_save.append(("fact_weather_daily", daily))

    return hourly, daily, to_save


def group_pop_3h(hourly: list[dict]) -> dict[str, int]:
    """โอกาสฝนรายช่วง 3 ชม. — ใช้ค่าสูงสุดในช่วง (ฝนตกช่วงไหนของ 3 ชม.ก็นับ)"""
    buckets: dict[str, list[int]] = {}
    for r in hourly:
        h = r["ts"].astimezone(TH_TZ).hour
        start = (h // 3) * 3
        buckets.setdefault(f"{start:02d}:00-{start + 2:02d}:59", []).append(int(r["pop"] or 0))
    return {label: max(vals) for label, vals in buckets.items()}


def _num(v):
    return float(v) if v is not None else None


def format_rows(hourly: list[dict], daily: list[dict], hours: int | None = None) -> dict:
    """แถว DB → record ภาษาไทย — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต ไม่แตะ DB

    ใช้ร่วมกันทั้งของที่อ่านจาก DB และของที่เพิ่งยิงมาสด เพราะรูปแถวเหมือนกันทุกประการ
    """
    hourly = hourly[:hours] if hours else hourly
    updated = [r["updated_at"] for r in hourly + daily if r.get("updated_at")]

    def _iso(v):
        return v.astimezone(TH_TZ).isoformat() if v else None

    return {
        "รายชั่วโมง": [{
            "เวลา": r["ts"].astimezone(TH_TZ).strftime("%Y-%m-%d %H:%M"),
            "สภาพอากาศ": r["condition"], "สภาพอากาศ (หมวด)": r.get("weather_main"),
            "รหัสสภาพอากาศ": r.get("weather_id"),
            "ไอคอน": r.get("weather_icon"),
            "อุณหภูมิ": _num(r["temp"]),
            "อุณหภูมิที่รู้สึกได้": _num(r["feels_like"]),
            "ความกดอากาศ (hPa)": _num(r.get("pressure")),
            "ความชื้น (%)": _num(r.get("humidity")),
            "จุดน้ำค้าง": _num(r.get("dew_point")),
            "ดัชนี UV": _num(r.get("uvi")),
            "เมฆปกคลุม (%)": _num(r.get("clouds")),
            "ทัศนวิสัย (ม.)": _num(r.get("visibility")),
            "ความเร็วลม (ม./วิ)": _num(r.get("wind_speed")),
            "ทิศทางลม (องศา)": _num(r.get("wind_deg")),
            "ลมกระโชก (ม./วิ)": _num(r.get("wind_gust")),
            "โอกาสฝน (%)": r["pop"],
            "ปริมาณฝน (มม.)": _num(r["rain_mm"]),
        } for r in hourly],
        "โอกาสฝนรายช่วง 3 ชม.": group_pop_3h(hourly),
        "รายวัน": [{
            "วันที่": r["target_date"].isoformat(),
            "สภาพอากาศ": r["condition"], "สภาพอากาศ (หมวด)": r.get("weather_main"),
            "รหัสสภาพอากาศ": r.get("weather_id"),
            "ไอคอน": r.get("weather_icon"),
            "อุณหภูมิกลางวัน": _num(r.get("temp_day")),
            "อุณหภูมิสูงสุด": _num(r["temp_max"]),
            "อุณหภูมิต่ำสุด": _num(r["temp_min"]),
            "อุณหภูมิกลางคืน": _num(r.get("temp_night")),
            "อุณหภูมิเย็น": _num(r.get("temp_eve")),
            "อุณหภูมิเช้า": _num(r.get("temp_morn")),
            "รู้สึกได้กลางวัน": _num(r.get("feels_like_day")),
            "รู้สึกได้กลางคืน": _num(r.get("feels_like_night")),
            "รู้สึกได้เย็น": _num(r.get("feels_like_eve")),
            "รู้สึกได้เช้า": _num(r.get("feels_like_morn")),
            "ความกดอากาศ (hPa)": _num(r.get("pressure")),
            "ความชื้น (%)": _num(r.get("humidity")),
            "ความเร็วลม (ม./วิ)": _num(r.get("wind_speed")),
            "ทิศทางลม (องศา)": _num(r.get("wind_deg")),
            "เมฆปกคลุม (%)": _num(r.get("clouds")),
            "ดัชนี UV": _num(r.get("uvi")),
            "พระอาทิตย์ขึ้น": _iso(r.get("sunrise")),
            "พระอาทิตย์ตก": _iso(r.get("sunset")),
            "พระจันทร์ขึ้น": _iso(r.get("moonrise")),
            "พระจันทร์ตก": _iso(r.get("moonset")),
            "เฟสพระจันทร์": _num(r.get("moon_phase")),
            "โอกาสฝน (%)": r["pop"],
            "ปริมาณฝน (มม.)": _num(r["rain_mm"]),
        } for r in daily],
        "อัปเดตล่าสุด": max(updated).astimezone(TH_TZ).isoformat() if updated else None,
        "หน่วย": "°C",
        "แหล่งข้อมูล": SOURCE,
    }


def summary(province: str = "Bangkok", district: str = "Chatuchak",
            lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON,
            hours: int | None = None) -> dict:
    """ยิงสด + เขียน DB ทันที — ใช้ตอนรันมือ/อุ่น cache ไม่ใช่ทาง endpoint"""
    hourly, daily, to_save = get(province, district, lat, lon)
    for table, rows in to_save:
        db.save_rows(table, rows)
    out = {"พื้นที่": {"จังหวัด": province, "เขต/อำเภอ": district}}
    out.update(format_rows(hourly, daily, hours))
    return out


def demo():
    """self-check — ตรรกะ TTL + แปลงแถว ไม่ต่อเน็ต ไม่แตะ DB"""
    now = datetime.now(timezone.utc)

    assert _rain({"1h": 0.5}) == 0.5, "รายชั่วโมงส่ง rain เป็น dict"
    assert _rain(2.3) == 2.3, "รายวันส่ง rain เป็นตัวเลขล้วน"
    assert _rain(None) == 0, "ไม่ตกฝน = ไม่มี key ต้องได้ 0 ไม่ใช่พัง"

    assert not _fresh([], HOURLY_TTL), "ไม่มีแถวเลย = ไม่สด"
    assert _fresh([{"updated_at": now - timedelta(hours=1)}], HOURLY_TTL)
    assert not _fresh([{"updated_at": now - timedelta(hours=7)}], HOURLY_TTL)
    assert _fresh([{"updated_at": now - timedelta(hours=7)}], DAILY_TTL), \
        "รายวัน TTL 12 ชม. ของอายุ 7 ชม.ต้องยังสด"
    assert not _fresh([{"updated_at": now - timedelta(minutes=5)},
                       {"updated_at": now - timedelta(hours=9)}], HOURLY_TTL), \
        "มีแถวเก่าค้างในชุดต้องถือว่าไม่สด (ยึดแถวเก่าสุด)"

    base = datetime.now(TH_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    hourly = [{"ts": base + timedelta(hours=i), "condition": f"สภาพ{i}",
               "temp": 30 + i, "feels_like": 33 + i, "pop": i * 10, "rain_mm": 0.1 * i,
               "updated_at": now} for i in range(6)]
    daily = [{"target_date": date(2026, 8, 22), "condition": "ฝนตก",
              "temp_max": 33.5, "temp_min": 25.1, "pop": 80, "rain_mm": 4.2,
              "updated_at": now - timedelta(hours=2)}]

    out = format_rows(hourly, daily)
    assert out["รายชั่วโมง"][0]["เวลา"].endswith("00:00")
    assert out["รายชั่วโมง"][5]["โอกาสฝน (%)"] == 50
    assert out["รายวัน"][0]["อุณหภูมิสูงสุด"] == 33.5 and out["รายวัน"][0]["โอกาสฝน (%)"] == 80
    assert out["โอกาสฝนรายช่วง 3 ชม."] == {"00:00-02:59": 20, "03:00-05:59": 50}, \
        f"ต้องได้ค่าสูงสุดของแต่ละช่วง ได้ {out['โอกาสฝนรายช่วง 3 ชม.']}"
    assert out["อัปเดตล่าสุด"].startswith(now.astimezone(TH_TZ).strftime("%Y-%m-%dT%H:%M")), \
        "อัปเดตล่าสุดต้องเป็นเวลาไทยของแถวที่ใหม่สุด"

    assert len(format_rows(hourly, daily, hours=3)["รายชั่วโมง"]) == 3
    assert format_rows([], [])["อัปเดตล่าสุด"] is None, "ไม่มีข้อมูลต้องไม่พัง"

    print("✅ ผ่าน — rain สองรูปแบบ, TTL รายชั่วโมง/รายวัน, แถวเก่าค้าง, แปลงแถวไทย, กลุ่ม 3 ชม.")


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
            hours=opt("--hours", int),
        ), ensure_ascii=False, indent=1, default=str))
