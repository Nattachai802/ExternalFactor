"""MODULE — ระดับน้ำจากสถานีวัดจริง (thaiwater.net — สสน.)

เติมช่องว่างของ GISTDA: ภาพดาวเทียม (SAR) จับน้ำท่วมทุ่งได้ดี แต่จับน้ำท่วมขังในเมืองไม่ได้
(อาคาร/ต้นไม้บัง + น้ำระบายหมดก่อนดาวเทียมรอบถัดไป) — ยิงจริงตอนกรุงเทพท่วม 26 ก.ย. 2026
ได้ 0 แปลงทุกจุด ขณะที่สถานีวัดคลองลาดพร้าว/ลำปลาทิวขึ้นระดับ 5 (น้ำล้นตลิ่ง) ตามความจริง

Endpoint สาธารณะ ไม่ต้องมี API key — ยิง 1 ครั้งได้ทุกสถานีทั่วประเทศ (~800 จุด)

situation_level ของ สสน. อิง % ความจุตลิ่ง — ไม่ได้เรียงจากดีไปแย่:
    5 (>100%) น้ำล้นตลิ่ง   ← ภัยน้ำท่วม
    4 (>70%)  น้ำมาก        ← เฝ้าระวังน้ำท่วม
    3 (>30%)  ปกติ
    2 (>10%)  น้ำน้อย
    1 (<=10%) น้ำน้อยวิกฤติ ← ภัยแล้ง

    python -m modules.thaiwater                # สถานีวิกฤตใกล้จตุจักร (ยิงสด)
    python -m modules.thaiwater --rows         # แถวสำหรับ fact_water_level
    python -m modules.thaiwater test           # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

import requests

import db

CURRENT_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
GRAPH_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_graph"
SOURCE = "thaiwater (สสน.)"
TH_TZ = timezone(timedelta(hours=7))

# เก็บทุกระดับ (1-5) — ได้ timeline ครบ: ระดับปกติเป็นฐานเทียบ, เห็นแนวโน้มก่อนถึงเกณฑ์,
# และรู้ว่าน้ำลดกลับมาปกติตอนไหน (ถ้าเก็บแค่ตอนเป็นภัย น้ำลดแล้วแถวแค่ "หายไป")
# ~800 สถานี × 24 รอบ ≈ 19k แถว/วัน ≈ 7M แถว/ปี — endpoint ไม่ช้าลง เพราะกรองระดับใน SQL
FLOOD_LEVELS = {4, 5}        # น้ำมาก / น้ำล้นตลิ่ง
DROUGHT_LEVELS = {1}         # น้ำน้อยวิกฤติ — ไม่กระทบยอดขายทันที แต่กระทบราคาวัตถุดิบทีหลัง

# เตือนบน badge เฉพาะฝั่งน้ำท่วม — ระดับอื่นเก็บไว้ให้โมเดล ไม่ต้องขึ้นการ์ดหน้าแรก
ALERT_LEVELS = FLOOD_LEVELS
KIND_TH = {5: "น้ำล้นตลิ่ง", 4: "ระดับน้ำสูง", 1: "ภัยแล้ง (น้ำน้อยวิกฤติ)"}

# ค้นเป็นวงขยาย — วงไหนมีสถานี (ระดับใดก็ได้) ตัดสินจากวงนั้นแล้วจบ ไม่ขยายต่อ
# สถานีใกล้บอก "ปกติ" ต้องชนะสถานีไกลที่ล้นตลิ่ง (คนละคลอง) — ร้านอาหารสนใจรอบร้าน
# เกิน 8 กม. ถือว่าคนละเขตแล้ว ไม่มีสถานีเลย = ไม่เตือน
RINGS_KM = (4.0, 6.0, 8.0)
# cron ทุกชั่วโมง — เผื่อพลาดไป 1 รอบ ค่าที่วัดเก่ากว่านี้ถือว่าใช้ไม่ได้
# (สถานีเสียบางตัว API ยังส่งค่าค้างเป็นวัน เช่น คลองลำปลาทิวค้างระดับ 5 ตั้งแต่ 27 ก.ย.)
FRESH_WINDOW = timedelta(hours=2)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """haversine — ไทยยาวเหนือ-ใต้ 1,600 กม. ระยะแบบระนาบเพี้ยนเกินรับได้"""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * asin(sqrt(a))


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts(text: str | None) -> datetime | None:
    """'2026-09-26 22:00' (เวลาไทย) → datetime พร้อม timezone"""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TH_TZ)
    except ValueError:
        return None


def parse_current(payload: dict) -> list[dict]:
    """JSON → แถว fact_water_level (ทุกสถานี ทุกระดับ) — ฟังก์ชันบริสุทธิ์

    ทิ้งสถานีที่ไม่มีพิกัด (จับคู่กับสาขาไม่ได้) และที่ไม่มีเวลาวัด (ไม่รู้ว่าเป็นค่าของเมื่อไหร่)
    situation_level ว่างได้ (สถานีส่งค่าแต่ สสน. ไม่ได้จัดระดับ) — เก็บไว้ ไม่ขึ้น badge อยู่แล้ว
    """
    now = datetime.now(timezone.utc)
    rows = []

    for item in (payload.get("waterlevel_data") or {}).get("data") or []:
        level = item.get("situation_level")
        station = item.get("station") or {}
        lat, lon = _f(station.get("tele_station_lat")), _f(station.get("tele_station_long"))
        ts = _ts(item.get("waterlevel_datetime"))
        if lat is None or lon is None or ts is None:
            continue

        geo = item.get("geocode") or {}
        rows.append({
            "station_id": str(station.get("id") or item.get("id") or ""),
            "ts": ts,
            "name_th": (station.get("tele_station_name") or {}).get("th") or "",
            "province": (geo.get("province_name") or {}).get("th") or "",
            "amphoe": (geo.get("amphoe_name") or {}).get("th") or "",
            "lat": lat, "lon": lon,
            "level_msl": _f(item.get("waterlevel_msl")),
            "storage_percent": _f(item.get("storage_percent")),
            "situation_level": int(level) if level is not None else None,
            "source": SOURCE,
            "updated_at": now,
        })
    return rows


def fetch_current() -> list[dict]:
    """ยิง 1 ครั้งได้ทุกสถานีทั่วประเทศ (~800 จุด, ~1.4 MB, ~0.4 วิ)"""
    r = requests.get(CURRENT_URL, timeout=30)
    r.raise_for_status()
    return parse_current(r.json())


def parse_history(payload: dict, base: dict) -> list[dict]:
    """graph_data (ทุก 10 นาที, ย้อน ~4 วัน) → แถวรายชั่วโมงของสถานีเดียว

    base = ข้อมูลสถานี (ชื่อ/พิกัด/จังหวัด) จาก parse_current ซึ่ง endpoint นี้ไม่ส่งมาให้
    เก็บเฉพาะจุดนาที 00 — ทุก 10 นาทีละเอียดเกินความจำเป็นสำหรับพยากรณ์ยอดขายรายวัน
    """
    now = datetime.now(timezone.utc)
    rows = []
    for point in (payload.get("data") or {}).get("graph_data") or []:
        ts = _ts(point.get("datetime"))
        value = _f(point.get("value"))
        if ts is None or value is None or ts.minute != 0:
            continue
        rows.append({**base, "ts": ts, "level_msl": value, "updated_at": now})
    return rows


def fetch_history(station_id: str, base: dict) -> list[dict]:
    """ประวัติย้อนหลังของสถานีเดียว — ต้นทางให้ราว 4 วัน ไม่มีพารามิเตอร์เลือกช่วง"""
    r = requests.get(GRAPH_URL, params={"station_type": "tele_waterlevel",
                                        "station_id": station_id}, timeout=30)
    r.raise_for_status()
    return parse_history(r.json(), base)


def run(verbose: bool = True) -> list[dict]:
    """แถวสำหรับ fact_water_level — cron เรียกผ่าน jobs.py ทุกชั่วโมง"""
    rows = fetch_current()
    if verbose:
        print("\n" + "=" * 50)
        print("💧 MODULE: ระดับน้ำสถานีวัด (thaiwater)")
        print("=" * 50)
        flood = [r for r in rows if r["situation_level"] in FLOOD_LEVELS]
        drought = [r for r in rows if r["situation_level"] in DROUGHT_LEVELS]
        print(f"  ✅ ทั้งหมด {len(rows)} สถานี | น้ำมาก/ล้นตลิ่ง {len(flood)} | "
              f"น้ำน้อยวิกฤติ {len(drought)}")
        worst = [r for r in flood if r["situation_level"] == 5]
        if worst:
            top = max(worst, key=lambda r: r["storage_percent"] or 0)
            print(f"  🌊 สูงสุด: {top['name_th']} {top['storage_percent']}% ของตลิ่ง")
    return rows


def backfill(verbose: bool = True) -> list[dict]:
    """ดูดประวัติ ~4 วันของสถานีที่ "กำลังเป็นภัยอยู่ตอนนี้" — ใช้ครั้งเดียวตอนตั้งระบบ

    ไม่ดูดทั้ง 805 สถานี เพราะย้อนได้แค่ 4 วัน ไม่พอเป็น baseline อยู่ดี
    ยิงทีละสถานี (ต้นทางไม่มี bulk endpoint) — 250-300 request ใช้เวลาไม่กี่นาที
    """
    alert_levels = FLOOD_LEVELS | DROUGHT_LEVELS
    current = [s for s in fetch_current() if s["situation_level"] in alert_levels]
    rows = []
    for i, station in enumerate(current, 1):
        base = {k: station[k] for k in ("station_id", "name_th", "province", "amphoe",
                                        "lat", "lon", "storage_percent",
                                        "situation_level", "source")}
        try:
            got = fetch_history(station["station_id"], base)
        except Exception as e:
            if verbose:
                print(f"  ⚠️  {station['name_th']}: {type(e).__name__}: {e}")
            continue
        rows += got
        if verbose and i % 50 == 0:
            print(f"  ... {i}/{len(current)} สถานี ({len(rows)} แถว)")

    if verbose:
        print(f"  ✅ ย้อนหลัง {len(rows)} แถว จาก {len(current)} สถานี")
    return rows


def load_fresh() -> list[dict]:
    """สถานีทุกระดับที่มีค่าใหม่พอจะใช้ได้ — อ่านจาก DB ที่ cron เขียนไว้

    ไม่กรองระดับ — nearest_alert ต้องเห็นสถานีปกติด้วย ถึงจะรู้ว่า "ใกล้ร้านมีสถานี และปกติ"
    (~1,600 แถว เพิ่มราว 10-30 ms เฉพาะตอน cache ภัยพิบัติหมดอายุ)
    ตารางถูกสร้างตอน job แรกเขียน — deploy ใหม่ที่ยังไม่เคยรัน cron ยังไม่มีตาราง
    ถือว่า "ยังไม่มีข้อมูล" ให้ผู้เรียกไปยิงสดต่อ ไม่ใช่ error
    """
    now = datetime.now(timezone.utc)
    try:
        return db.rows_between("fact_water_level", "ts", now - FRESH_WINDOW,
                               now + timedelta(hours=1))
    except Exception:
        return []


def recent(rows: list[dict]) -> list[dict]:
    """ตัดค่าที่วัดเก่ากว่า FRESH_WINDOW — ใช้กับผลยิงสด (ทาง DB กรองด้วย SQL อยู่แล้ว)"""
    cutoff = datetime.now(timezone.utc) - FRESH_WINDOW
    return [r for r in rows if r["ts"] >= cutoff]


def nearest_alert(lat: float, lon: float, rows: list[dict] | None = None) -> dict:
    """สถานีเตือนภัยของสาขา — คืน {} ถ้าไม่ต้องเตือน

    วงขยาย RINGS_KM: วงแรกที่มีสถานี (ที่มีระดับ) ใช้ตัดสินแล้วจบ
      มีระดับ 4-5 ในวงนั้น → เตือน   /   ทุกตัวปกติ → ไม่เตือน (ไม่ไปหาวงที่ไกลกว่า)
    สถานีไม่จัดระดับไม่นับว่า "เจอ" — บอกไม่ได้ว่าปกติหรือไม่
    เอามาแห่งเดียวพอ: 3 สถานีในคลองเดียวกันคือเหตุการณ์เดียว
    ในวงเดียวกัน เลือกระดับสูงกว่าก่อน ระดับเท่ากันเอาที่ใกล้กว่า
    """
    located = []
    for r in (rows if rows is not None else load_fresh()):
        rlat, rlon = _f(r.get("lat")), _f(r.get("lon"))
        if r.get("situation_level") is None or rlat is None or rlon is None:
            continue
        located.append((r, distance_km(lat, lon, rlat, rlon)))

    for ring in RINGS_KM:
        in_ring = [(r, km) for r, km in located if km <= ring]
        if not in_ring:
            continue
        alerts = [(r, km) for r, km in in_ring if r["situation_level"] in ALERT_LEVELS]
        if not alerts:
            return {}
        best, best_km = max(alerts, key=lambda p: (p[0]["situation_level"], -p[1]))
        return {**best, "distance_km": round(best_km, 1)}
    return {}


def as_event(station: dict) -> dict:
    """แถวสถานี → เหตุการณ์รูปแบบเดียวกับที่ disaster.rows() ใช้"""
    level = station["situation_level"]
    return {
        "kind": KIND_TH.get(level, "ระดับน้ำผิดปกติ"),
        "level": "แดง" if level >= 5 else "ส้ม",
        "source": SOURCE,
        "detail": (f"{station.get('name_th', '')} ระดับน้ำ {station.get('level_msl')} ม.รทก. "
                   f"({station.get('storage_percent')}% ของตลิ่ง) "
                   f"ห่าง {station.get('distance_km')} กม. "
                   f"เมื่อ {station['ts'].astimezone(TH_TZ).strftime('%Y-%m-%d %H:%M')}"),
        "province": station.get("province") or "",
        "district": station.get("amphoe") or "",
    }


def demo():
    """self-check — ไม่ต่อเน็ต ไม่แตะ DB"""
    def station(sid, name, lat, lon, level, msl="2.79", pct="123.36", when="2026-09-26 22:00"):
        return {"id": sid, "situation_level": level, "waterlevel_msl": msl,
                "storage_percent": pct, "waterlevel_datetime": when,
                "station": {"id": sid, "tele_station_name": {"th": name},
                            "tele_station_lat": lat, "tele_station_long": lon},
                "geocode": {"province_name": {"th": "กรุงเทพมหานคร"},
                            "amphoe_name": {"th": "เขตจตุจักร"}}}

    HERE = (13.8479, 100.5697)          # จตุจักร
    payload = {"waterlevel_data": {"data": [
        station(1, "คลองลาดพร้าว วัดบางบัว", 13.85402, 100.58746, 5),
        station(2, "กรมชลประทานสามเสน", 13.7881, 100.5091, 4),
        station(3, "อโศก", 13.8479, 100.5697, 3),                      # ปกติ ตรงสาขาเป๊ะ
        station(4, "คลองลำปลาทิว", 13.7407, 100.7947, 5),              # วิกฤตแต่ไกล 26 กม.
        station(5, "อ่างเก็บน้ำแล้ง", 13.85, 100.57, 1, pct="4.2"),     # ภัยแล้ง เก็บแต่ไม่เตือน
        station(6, "ไม่มีพิกัด", None, None, 5),
        station(7, "ไม่มีเวลา", 13.85, 100.57, 5, when=""),
        station(8, "ไม่จัดระดับ", 13.85, 100.57, None),
    ]}}

    rows = parse_current(payload)
    ids = [r["station_id"] for r in rows]
    assert "3" in ids and "5" in ids, "ต้องเก็บทุกระดับ — ปกติเป็นฐานเทียบ ภัยแล้งให้โมเดล"
    assert "6" not in ids and "7" not in ids, "ไม่มีพิกัด/ไม่มีเวลาต้องถูกทิ้ง"
    assert next(r for r in rows if r["station_id"] == "8")["situation_level"] is None
    assert len(rows) == 6, ids

    # ระดับ 3 อยู่ตรงสาขาเป๊ะก็ต้องไม่เตือน — เก็บทุกระดับแล้ว badge ต้องไม่เพี้ยนตาม
    normal = [r for r in rows if r["station_id"] in ("3", "8")]
    assert nearest_alert(*HERE, rows=normal) == {}, "ระดับปกติ/ไม่จัดระดับห้ามขึ้น badge"

    # ค่าค้าง (สถานีเสีย API ส่งค่าเดิมเป็นวัน) ต้องถูกตัดออกจากผลยิงสด
    now = datetime.now(timezone.utc)
    assert [r["x"] for r in recent([{"x": 1, "ts": now - timedelta(minutes=30)},
                                    {"x": 2, "ts": now - timedelta(days=2)}])] == [1]

    first = next(r for r in rows if r["station_id"] == "1")
    assert first["situation_level"] == 5 and first["storage_percent"] == 123.36
    assert first["ts"].isoformat() == "2026-09-26T22:00:00+07:00"
    assert first["province"] == "กรุงเทพมหานคร"

    # ── เลือกสถานีเตือนภัย ────────────────────────────────────
    got = nearest_alert(*HERE, rows=rows)
    assert got["station_id"] == "1", "ต้องเลือกตัววิกฤตสุดในรัศมี"
    assert got["distance_km"] == 2.0, got["distance_km"]

    only4 = [r for r in rows if r["station_id"] == "2"]      # สามเสน ห่างจตุจักร ~11 กม.
    assert nearest_alert(*HERE, rows=only4) == {}, "เกิน 8 กม.ต้องไม่เตือน แม้ระดับ 4"
    near4 = [dict(only4[0], lat=HERE[0] + 0.02, lon=HERE[1])]   # ย้ายมาห่าง ~2 กม.
    assert nearest_alert(*HERE, rows=near4)["situation_level"] == 4

    # ภัยแล้งเก็บลง DB แต่ห้ามขึ้น badge (ไม่กระทบยอดขายวันนั้น)
    drought = [r for r in rows if r["station_id"] == "5"]
    assert nearest_alert(*HERE, rows=drought) == {}, "ภัยแล้งต้องไม่ทำให้ badge เตือน"

    far = [r for r in rows if r["station_id"] == "4"]
    assert nearest_alert(*HERE, rows=far) == {}, "เกิน 8 กม.ต้องไม่เตือน"
    assert nearest_alert(*HERE, rows=[]) == {}

    # ── วงขยาย 4 → 6 → 8 กม. ─────────────────────────────────
    def at(sid, km_north, level):     # สถานีทางเหนือของ HERE ห่าง km_north กม.
        return {"station_id": sid, "lat": HERE[0] + km_north / 111.2, "lon": HERE[1],
                "situation_level": level, "ts": now, "name_th": sid}

    near_ok = [at("ปกติ3กม", 3, 3), at("ล้น5กม", 5, 5)]
    assert nearest_alert(*HERE, rows=near_ok) == {}, "วง 4 กม.มีสถานีปกติ ต้องจบที่วงนั้น ไม่เตือน"

    ring2 = [at("ล้น5กม", 5, 5), at("ปกติ7กม", 7, 3)]
    assert nearest_alert(*HERE, rows=ring2)["station_id"] == "ล้น5กม", "วง 4 ว่าง → วง 6 เจอภัยต้องเตือน"

    assert nearest_alert(*HERE, rows=[at("ล้น9กม", 9, 5)]) == {}, "เกิน 8 กม.ไม่นับ"
    assert nearest_alert(*HERE, rows=[at("ไม่จัด2กม", 2, None), at("ล้น5กม", 5, 5)]) \
        ["station_id"] == "ล้น5กม", "สถานีไม่จัดระดับไม่นับว่าเจอ ต้องขยายวงต่อ"

    # เคสจริง เขตพระนคร 29 ก.ย. — เดิมรัศมี 15 กม.เลือกคลองลาดพร้าว (บางเขน 14.3 กม., ระดับ 5)
    PN = (13.7563, 100.5018)
    pn = [{"station_id": "สามเสน", "lat": 13.7881, "lon": 100.5091, "situation_level": 4},
          {"station_id": "ลาดพร้าว", "lat": 13.85402, "lon": 100.58746, "situation_level": 5}]
    assert nearest_alert(*PN, rows=pn)["station_id"] == "สามเสน", "ต้องเตือนจากเจ้าพระยาที่ใกล้"

    event = as_event(got)
    assert event["kind"] == "น้ำล้นตลิ่ง" and event["level"] == "แดง"
    assert "วัดบางบัว" in event["detail"] and "123.36% ของตลิ่ง" in event["detail"]
    assert as_event({**got, "situation_level": 4})["level"] == "ส้ม"

    # ── ประวัติย้อนหลัง ───────────────────────────────────────
    graph = {"data": {"graph_data": [
        {"datetime": "2026-09-23 00:00", "value": 1.283},
        {"datetime": "2026-09-23 00:10", "value": 1.282},      # ไม่ใช่นาที 00 ต้องข้าม
        {"datetime": "2026-09-23 01:00", "value": 1.281},
        {"datetime": "2026-09-23 02:00", "value": None},       # ไม่มีค่า ต้องข้าม
        {"datetime": "พัง", "value": 1.0},
    ]}}
    base = {"station_id": "1", "name_th": "x", "province": "", "amphoe": "",
            "lat": 13.85, "lon": 100.58, "storage_percent": 120.0,
            "situation_level": 5, "source": SOURCE}
    hist = parse_history(graph, base)
    assert len(hist) == 2, f"เก็บเฉพาะจุดนาที 00 ที่มีค่า ได้ {len(hist)}"
    assert hist[0]["level_msl"] == 1.283 and hist[0]["station_id"] == "1"

    assert parse_current({}) == [] and parse_history({}, base) == []
    assert distance_km(*HERE, 13.85402, 100.58746) < 3
    assert distance_km(*HERE, 18.79, 98.98) > 500

    print("✅ ผ่าน — เก็บทุกระดับ, ทิ้งสถานีไม่มีพิกัด/เวลา, badge เฉพาะ 4-5, ตัดค่าค้าง, "
          "วงขยาย 4/6/8 กม., ประวัติรายชั่วโมง, ข้อมูลพังไม่ล้ม")


if __name__ == "__main__":
    import json
    import sys

    args = sys.argv[1:]
    if args and args[0] == "test":
        demo()
    elif "--rows" in args:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1, default=str))
    elif "--backfill" in args:
        print(f"{len(backfill())} แถว")
    else:
        got = nearest_alert(13.8479, 100.5697, rows=fetch_current())
        print(json.dumps(as_event(got) if got else {}, ensure_ascii=False, indent=1, default=str))
