"""MODULE — ค่าฝุ่นจากสถานีวัดจริง (Air4Thai — กรมควบคุมมลพิษ)

ต่างจาก modules/air_quality.py (OpenWeatherMap):
    air_quality.py — ค่า "พยากรณ์" จากโมเดลบรรยากาศ CAMS 20 ชม.ข้างหน้า
    ไฟล์นี้        — ค่าที่ "วัดได้จริง" จากสถานีภาคพื้น ณ ชั่วโมงนั้น

ทำไมต้องมีทั้งคู่: โมเดล CAMS กริดหยาบ ~45 กม. มองไม่เห็นฝุ่นจากจราจร/เผาในเมือง
ยิงเทียบแล้วหน้าฝนให้ PM2.5 = 0.66 µg/m³ ที่จตุจักร ขณะที่สถานีวัดจริงได้ 14.4
(ต่ำกว่า 20 เท่า) ส่วนหน้าแล้งกลับสูงเกินจริงราว 2 เท่า — ปรับด้วยค่าคงที่ไม่ได้
ค่าที่ "แสดงบนจอ" และที่ badge ใช้จึงต้องมาจากสถานีวัดจริง ส่วนพยากรณ์รายชั่วโมง
ยังต้องพึ่ง OWM เพราะ Air4Thai ไม่มีข้อมูลล่วงหน้า

Endpoint สาธารณะ ไม่ต้องมี API key ไม่มีโควตา — ยิง 1 ครั้งได้ทุกสถานีทั้งประเทศ
(~173 สถานี, 114 KB) จึง cache เป็นก้อนเดียวใช้ร่วมกันทุกสาขา

    python -m modules.air4thai              # ค่าฝุ่นที่พิกัดจตุจักร (ยิงสด)
    python -m modules.air4thai --rows       # แถวสำหรับเก็บลง fact_air_quality_station
    python -m modules.air4thai test         # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import requests
import urllib3

import db
from modules.air_quality import us_aqi

# ปิดคำเตือน "ยิงโดยไม่ตรวจ cert" ของ urllib3 — ตั้งใจปิด verify (ดูเหตุผลใน fetch())
# ไม่ปิดคำเตือนไว้ log ของ cron จะมีบรรทัดนี้ซ้ำทุกชั่วโมงจนกลบข้อความที่สำคัญจริง
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# https ของ air4thai ใช้ cert ที่ verify ไม่ผ่าน — ข้อมูลสาธารณะล้วน ไม่มีอะไรลับให้ดักฟัง
URL = "http://air4thai.pcd.go.th/services/getNewAQI_JSON.php"
SOURCE = "Air4Thai (กรมควบคุมมลพิษ)"
TH_TZ = timezone(timedelta(hours=7))

# สถานีอัปเดตรายชั่วโมง เผื่อ cron ช้าไป 1 รอบก่อนตัดสินว่าข้อมูลเก่าเกินใช้
FRESH_WINDOW = timedelta(hours=2)

# ไกลเกินนี้ถือว่าไม่ใช่อากาศของพื้นที่นั้นแล้ว — สาขาบนเกาะ/บนดอยจะตกไปใช้ค่า OWM แทน
MAX_DISTANCE_KM = 50.0


# เพดานความสมเหตุสมผล — PM2.5 สูงสุดที่เคยวัดได้ในไทย ~500 µg/m³ (แม่สาย ช่วงเผาป่า)
# เกินนี้คือเครื่องเสียหรือข้อมูลถูกแทรกกลางทาง ทิ้งดีกว่าเอาไปโชว์/ป้อนโมเดล
MAX_SANE = 2000.0


def _f(value) -> float | None:
    """ค่าใน JSON เป็น string ทั้งหมด และมีทั้ง "", "-1", "-999" ที่แปลว่า "ไม่มีค่า" """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num < 0 or num > MAX_SANE else num


def parse_stations(payload: dict) -> list[dict]:
    """JSON ดิบ → แถวสำหรับ fact_air_quality_station — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต

    ทิ้งสถานีที่ไม่มีพิกัด (ระบุตำแหน่งไม่ได้ = จับคู่กับสาขาไม่ได้) และที่ไม่มีทั้ง
    AQI และ PM2.5 (เครื่องเสีย/ไม่ส่งค่า) — เก็บไว้ก็เป็นแถวเปล่าที่ต้องมากรองทีหลังอยู่ดี
    """
    now = datetime.now(timezone.utc)
    rows = []

    for st in payload.get("stations") or []:
        lat, lon = _f(st.get("lat")), _f(st.get("long"))
        if lat is None or lon is None:
            continue

        last = st.get("AQILast") or {}
        ts = _parse_ts(last.get("date"), last.get("time"))
        if not ts:
            continue

        aqi_th = _f((last.get("AQI") or {}).get("aqi"))
        pm25 = _f((last.get("PM25") or {}).get("value"))
        if aqi_th is None and pm25 is None:
            continue

        rows.append({
            "station_id": st.get("stationID", ""),
            "ts": ts,
            "name_th": st.get("nameTH", ""),
            "area_th": st.get("areaTH", ""),
            "lat": lat, "lon": lon,
            "aqi_th": int(aqi_th) if aqi_th is not None else None,
            # สูตร EPA ตัวเดียวกับที่ air_quality.py ใช้ — เปลี่ยนแค่ค่าที่ป้อนเข้าไป
            # (PM10 ของ Air4Thai ส่วนใหญ่เป็น -1 จึงคิดจาก PM2.5 อย่างเดียว)
            "aqi_us": us_aqi(pm25, None),
            "pm25": pm25,
            "main_param": (last.get("AQI") or {}).get("param", ""),
            "source": SOURCE,
            "updated_at": now,
        })
    return rows


def _parse_ts(date_str: str | None, time_str: str | None) -> datetime | None:
    """'2026-09-09' + '14:00' (เวลาไทย) → datetime พร้อม timezone"""
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=TH_TZ)
    except ValueError:
        return None


def fetch() -> list[dict]:
    """ยิง Air4Thai 1 ครั้ง — ได้ทุกสถานีทั้งประเทศ

    verify=False เพราะเซิร์ฟเวอร์ redirect http → https เองแล้วส่ง cert chain ไม่ครบ
    (ขาด intermediate) ยิงแบบตรวจ cert จะพังทุกครั้ง — ข้อมูลเป็นสาธารณะทั้งหมด
    ไม่มี credential ไม่มีข้อมูลส่วนบุคคล ความเสี่ยงจากการไม่ตรวจ cert คือได้ค่าฝุ่นปลอม
    ซึ่งกันไว้อีกชั้นแล้วด้วยการตัดค่าที่อยู่นอกช่วงที่เป็นไปได้ใน parse_stations()
    """
    r = requests.get(URL, timeout=20, verify=False)
    r.raise_for_status()
    return parse_stations(r.json())


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """ระยะทางวงกลมใหญ่ (haversine) — ไทยยาวเหนือ-ใต้ 1,600 กม. ใช้ระยะแบบระนาบเพี้ยนเกิน"""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def nearest(rows: list[dict], lat: float, lon: float,
            max_km: float = MAX_DISTANCE_KM) -> dict | None:
    """สถานีที่ใกล้พิกัดนั้นที่สุด — คืน None ถ้าไม่มีสถานีในรัศมีที่ยอมรับได้

    คำนวณสดทุกครั้งไม่จำคู่ไว้ เพราะสถานีปิดซ่อม/หยุดส่งค่าเป็นระยะ
    ถ้าจำคู่ไว้จะติดอยู่กับสถานีที่เงียบไปแล้วทั้งที่มีสถานีอื่นที่ยังส่งค่าอยู่
    """
    best, best_km = None, None
    for r in rows:
        if r.get("aqi_us") is None and r.get("aqi_th") is None:
            continue
        km = distance_km(lat, lon, float(r["lat"]), float(r["lon"]))
        if best_km is None or km < best_km:
            best, best_km = r, km

    if best is None or best_km > max_km:
        return None
    return {**best, "distance_km": round(best_km, 1)}


def load_fresh() -> list[dict]:
    """สถานีที่มีค่าใหม่พอจะใช้ได้ — อ่านจาก DB ที่ cron เขียนไว้

    ยึด ts (เวลาที่สถานีวัด) ไม่ใช่ updated_at (เวลาที่เราดึง) — บางสถานีหยุดส่งค่า
    ไปหลายชั่วโมง ถ้าดู updated_at จะเห็นว่า "เพิ่งดึงมา" ทั้งที่ข้างในเป็นค่าเก่า
    """
    now = datetime.now(timezone.utc)
    return db.rows_between("fact_air_quality_station", "ts",
                           now - FRESH_WINDOW, now + timedelta(hours=1))


def current(lat: float, lon: float) -> tuple[dict, list]:
    """ค่าฝุ่นของพิกัดนั้น + สิ่งที่ต้องเขียนกลับ DB

    คืน (station, to_save) — station เป็น {} ถ้าไม่มีสถานีในรัศมี (ผู้เรียก fallback เอง)
    to_save ไม่ว่างเฉพาะตอนยิงสด ผู้เรียกเอาไปเขียนผ่าน BackgroundTasks ได้

    ทางปกติคือ cron เขียนไว้ล่วงหน้าทุกชั่วโมง endpoint จึงไม่ต้องยิงเน็ตเลย
    ยิงสดเฉพาะตอนเพิ่ง deploy หรือ cron พังข้ามไป 2 รอบ — ระบบซ่อมตัวเองได้
    """
    rows = load_fresh()
    if rows:
        return nearest(rows, lat, lon) or {}, []

    rows = fetch()
    return nearest(rows, lat, lon) or {}, [("fact_air_quality_station", rows)]


def run(verbose: bool = True) -> list[dict]:
    """แถวสำหรับ fact_air_quality_station — cron เรียกผ่าน jobs.py"""
    rows = fetch()
    if verbose:
        print("\n" + "=" * 50)
        print("🌫️  MODULE: Air4Thai (สถานีวัดฝุ่นภาคพื้น)")
        print("=" * 50)
        with_value = [r for r in rows if r["aqi_us"] is not None]
        print(f"  ✅ {len(rows)} สถานี (มีค่า PM2.5 {len(with_value)} สถานี)")
        if with_value:
            worst = max(with_value, key=lambda r: r["aqi_us"])
            print(f"  📊 แย่สุด: {worst['name_th']} AQI(US) {worst['aqi_us']} / ไทย {worst['aqi_th']}")
    return rows


def demo():
    """self-check — parse/haversine/เลือกสถานี ไม่ต่อเน็ต ไม่แตะ DB"""
    payload = {"stations": [
        {"stationID": "119t", "nameTH": "สวนสาธารณะธารา", "areaTH": "กระบี่",
         "lat": "8.0506237", "long": "98.9180489",
         "AQILast": {"date": "2026-09-09", "time": "14:00",
                     "PM25": {"aqi": "11", "value": "6.6"},
                     "AQI": {"aqi": "11", "param": "PM25"}}},
        {"stationID": "05t", "nameTH": "กรมอุตุนิยมวิทยาบางนา", "areaTH": "กรุงเทพฯ",
         "lat": "13.666183", "long": "100.605742",
         "AQILast": {"date": "2026-09-09", "time": "14:00",
                     "PM25": {"aqi": "24", "value": "14.4"},
                     "AQI": {"aqi": "24", "param": "PM25"}}},
        {"stationID": "dead", "nameTH": "สถานีเสีย", "areaTH": "กรุงเทพฯ",
         "lat": "13.83", "long": "100.57",          # ใกล้จตุจักรกว่าทุกตัว แต่ไม่ส่งค่า
         "AQILast": {"date": "2026-09-09", "time": "14:00",
                     "PM25": {"aqi": "-1", "value": "-1"},
                     "AQI": {"aqi": "-1", "param": "PM25"}}},
        {"stationID": "nogeo", "nameTH": "ไม่มีพิกัด", "lat": "", "long": "",
         "AQILast": {"date": "2026-09-09", "time": "14:00",
                     "PM25": {"aqi": "50", "value": "30"},
                     "AQI": {"aqi": "50", "param": "PM25"}}},
    ]}

    rows = parse_stations(payload)
    ids = [r["station_id"] for r in rows]
    assert "nogeo" not in ids, "สถานีไม่มีพิกัดต้องถูกทิ้ง (จับคู่กับสาขาไม่ได้)"
    assert "dead" not in ids, "สถานีที่ทุกค่าเป็น -1 ต้องถูกทิ้ง ไม่ใช่เก็บเป็น 0"
    assert len(rows) == 2, ids

    bangna = next(r for r in rows if r["station_id"] == "05t")
    assert bangna["pm25"] == 14.4 and bangna["aqi_th"] == 24
    assert bangna["aqi_us"] == us_aqi(14.4, None), "US AQI ต้องคิดด้วยสูตร EPA ตัวเดียวกับ air_quality"
    assert 50 <= bangna["aqi_us"] <= 60, f"PM2.5 14.4 ควรได้ US AQI ~56 ได้ {bangna['aqi_us']}"
    assert bangna["ts"].isoformat() == "2026-09-09T14:00:00+07:00", bangna["ts"]

    # ── ระยะทาง / เลือกสถานี ──────────────────────────────────
    assert 0 < distance_km(13.83, 100.57, 13.666, 100.606) < 25, "จตุจักร→บางนา ควรอยู่ราว 20 กม."
    assert distance_km(13.83, 100.57, 8.05, 98.92) > 600, "จตุจักร→กระบี่ ต้องไกลมาก"

    got = nearest(rows, 13.8479, 100.5697)
    assert got["station_id"] == "05t", "ต้องเลือกสถานีกรุงเทพ ไม่ใช่กระบี่"
    assert got["distance_km"] > 0

    # สถานีที่ใกล้กว่าแต่ไม่ส่งค่า ต้องไม่ถูกเลือก (ถูกกรองตั้งแต่ parse แล้ว)
    assert nearest(rows, 13.83, 100.57)["station_id"] == "05t"

    # ไกลเกินเพดาน → None ให้ผู้เรียกไป fallback เอง
    assert nearest(rows, 18.79, 98.98) is None, "เชียงใหม่ไม่มีสถานีในรัศมี 50 กม.ของชุดนี้"
    assert nearest(rows, 18.79, 98.98, max_km=1000) is not None, "ขยายรัศมีแล้วต้องเจอ"
    assert nearest([], 13.83, 100.57) is None, "ไม่มีสถานีเลยต้องคืน None ไม่ใช่พัง"

    # ── ข้อมูลพัง ต้องไม่ระเบิด ────────────────────────────────
    assert parse_stations({}) == []
    assert parse_stations({"stations": [{"lat": "13.8", "long": "100.5"}]}) == [], \
        "ไม่มี AQILast ต้องถูกข้าม"
    assert _f("-999") is None and _f("") is None and _f(None) is None and _f("0") == 0.0
    assert _f("99999") is None, "ค่าเกินช่วงที่เป็นไปได้ต้องถูกตัดทิ้ง ไม่เอาไปโชว์/ป้อนโมเดล"
    assert _f("450") == 450.0, "ค่าสูงแต่เกิดขึ้นจริงได้ (เผาป่า) ต้องไม่ถูกตัด"

    print("✅ ผ่าน — parse สถานี, ทิ้งสถานีไม่มีพิกัด/ไม่ส่งค่า, US AQI ด้วยสูตร EPA, "
          "haversine, เพดานระยะทาง, ข้อมูลพังไม่ล้ม")


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1, default=str))
    else:
        station = nearest(fetch(), 13.8479, 100.5697) or {}
        print(json.dumps(station, ensure_ascii=False, indent=1, default=str))
