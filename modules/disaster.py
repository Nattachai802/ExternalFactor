"""MODULE — ภัยพิบัติใกล้สาขา (GISTDA + GDACS)

รวม 2 แหล่งที่เติมช่องว่างกัน:
  GISTDA (ไทย, ต้อง API key)  — น้ำท่วม + ไฟป่า ละเอียดถึงระดับตำบล มีพื้นที่/ผลกระทบจริง
  GDACS  (ทั่วโลก, ไม่ต้อง key) — แผ่นดินไหว/พายุ/สึนามิ/ภูเขาไฟ ที่ GISTDA ไม่มี

ภัยแล้งของ GISTDA ไม่มี /features/ JSON (มีแต่ WMS/WMTS/TMS = แผนที่ภาพ) จึงทำไม่ได้ใน phase นี้

ค้นด้วย bbox สี่เหลี่ยมรอบพิกัดสาขา ไม่ใช่รัศมีวงกลม — GISTDA รับแต่ bbox
ระยะจึงเป็น "ประมาณ ±11 กม." ไม่ใช่วงกลมเป๊ะ ยอมรับได้สำหรับคำถาม "ใกล้สาขาไหม"

    python -m modules.disaster                    # ภัยพิบัติรอบพิกัด default
    python -m modules.disaster --lat 18.4 --lon 103.4
    python -m modules.disaster test                # self-check (ไม่ต่อเน็ต)
"""
import os
from datetime import datetime, timedelta, timezone

import feedparser
import requests

import db

GISTDA_BASE = "https://api-gateway.gistda.or.th/api/2.0/resources"
GDACS_RSS = "https://www.gdacs.org/xml/rss.xml"
TH_TZ = timezone(timedelta(hours=7))
DEFAULT_LAT, DEFAULT_LON = 13.8479, 100.5697

TTL = timedelta(minutes=30)   # สั้นกว่า weather มาก — ภัยพิบัติต้องสด
BBOX_DEG = 0.1                # ~11 กม.ต่อด้าน (ละติจูดไทย 1° ≈ 111 กม.)

# น้ำท่วมใช้ 7 วัน — ภาพดาวเทียมไม่ได้ถ่ายทุกวัน 1day มักว่างทั้งที่ยังท่วมอยู่จริง
# ไฟป่าใช้ 1 วัน — จุดความร้อนเป็นเหตุการณ์ ณ ขณะนั้น ของเก่าไม่ได้แปลว่ายังไหม้อยู่
FLOOD_PATH = "features/flood/7days"
FIRE_PATH = "features/viirs/1day"

# ชื่อไทยของ GDACS eventtype — ตัวที่ไม่รู้จักใช้โค้ดดิบไปเลย ไม่ต้องแปล
GDACS_TYPE_TH = {
    "EQ": "แผ่นดินไหว", "TC": "พายุหมุนเขตร้อน", "FL": "น้ำท่วม",
    "VO": "ภูเขาไฟ", "WF": "ไฟป่า", "DR": "ภัยแล้ง", "TS": "สึนามิ",
}
GDACS_LEVEL_TH = {"Green": "เขียว", "Orange": "ส้ม", "Red": "แดง"}


def bbox_around(lat: float, lon: float, deg: float = BBOX_DEG) -> str:
    """x-min,y-min,x-max,y-max ตามที่ GISTDA กำหนด (lon มาก่อน lat)"""
    return f"{lon - deg},{lat - deg},{lon + deg},{lat + deg}"


def _gistda(path: str, bbox: str, limit: int = 50) -> dict:
    key = os.getenv("GISTDA_API_KEY")
    if not key:
        raise RuntimeError("ไม่พบ GISTDA_API_KEY ใน environment")
    r = requests.get(f"{GISTDA_BASE}/{path}", params={"bbox": bbox, "limit": limit},
                     headers={"API-Key": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_gistda(lat: float, lon: float) -> list[dict]:
    """น้ำท่วม + ไฟป่า ใกล้พิกัด — คืนลิสต์เหตุการณ์รูปแบบกลาง (ยังไม่ใช่แถว DB)

    น้ำท่วมยุบหลายแปลงเป็น 1 เหตุการณ์ต่อตำบล เพราะ GISTDA แบ่งเป็น polygon ย่อยเยอะ
    (บึงกาฬตำบลเดียว 228 แปลง) — ผู้ใช้อยากรู้ว่า "ตำบลนี้ท่วม" ไม่ใช่รายแปลง
    """
    bbox = bbox_around(lat, lon)
    events = []

    by_area: dict[tuple, dict] = {}
    for f in _gistda(FLOOD_PATH, bbox).get("features", []):
        p = f.get("properties", {})
        area_key = (p.get("pv_tn"), p.get("ap_tn"), p.get("tb_tn"))
        entry = by_area.setdefault(area_key, {"area": 0.0, "count": 0})
        entry["area"] += p.get("f_area") or 0
        entry["count"] += 1

    for (province, amphoe, tambon), agg in by_area.items():
        where = " ".join(x for x in (tambon, amphoe, province) if x)
        events.append({
            "kind": "น้ำท่วม", "level": "ส้ม", "source": "GISTDA",
            "detail": f"พื้นที่น้ำท่วม {where} รวม {agg['area'] / 1_000_000:.2f} ตร.กม. ({agg['count']} แปลง)",
            "province": province or "", "district": amphoe or "",
        })

    fires = _gistda(FIRE_PATH, bbox).get("features", [])
    if fires:
        # จุดความร้อนนับรวมเป็นเหตุการณ์เดียว — 20 จุดในตำบลเดียวคือไฟกองเดียวกัน ไม่ใช่ 20 เหตุการณ์
        p = fires[0].get("properties", {})
        events.append({
            "kind": "ไฟป่า", "level": "ส้ม", "source": "GISTDA",
            "detail": f"พบจุดความร้อน {len(fires)} จุดใกล้พื้นที่",
            "province": p.get("pv_tn") or "", "district": p.get("ap_tn") or "",
        })

    return events


def _event_coords(entry) -> tuple[float, float] | None:
    """พิกัดของ GDACS entry — geo_lat/long ก่อน ถ้าไม่มีใช้จุดกลางของ bbox"""
    lat, lon = getattr(entry, "geo_lat", None), getattr(entry, "geo_long", None)
    if lat and lon:
        return float(lat), float(lon)

    raw = getattr(entry, "gdacs_bbox", None)
    if raw:
        parts = [float(x) for x in raw.split()]
        if len(parts) == 4:   # lon-min lon-max lat-min lat-max ตามรูปแบบของ GDACS
            return (parts[2] + parts[3]) / 2, (parts[0] + parts[1]) / 2
    return None


def fetch_gdacs(lat: float, lon: float, deg: float = BBOX_DEG) -> list[dict]:
    """ภัยระดับโลกที่พิกัดตกในกรอบรอบสาขา — ข้าม Green (เหตุการณ์เล็ก ไม่กระทบจริง)"""
    events = []
    for entry in feedparser.parse(GDACS_RSS).entries:
        coords = _event_coords(entry)
        if not coords:
            continue
        elat, elon = coords
        if not (lat - deg <= elat <= lat + deg and lon - deg <= elon <= lon + deg):
            continue

        level = getattr(entry, "gdacs_alertlevel", "Green")
        if level == "Green":
            continue

        kind = GDACS_TYPE_TH.get(getattr(entry, "gdacs_eventtype", ""),
                                 getattr(entry, "gdacs_eventtype", "ภัยพิบัติ"))
        events.append({
            "kind": kind, "level": GDACS_LEVEL_TH.get(level, level), "source": "GDACS",
            "detail": entry.get("title", ""),
            "province": "", "district": "",
        })
    return events


def rows(lat: float, lon: float, province: str, district: str) -> list[dict]:
    """แถว fact_disaster — ยิงทั้ง 2 แหล่ง แหล่งไหนล่มก็ใช้อีกแหล่งต่อได้

    ไม่ปล่อย exception ออกไปเมื่อแหล่งเดียวล่ม เพราะ "ไม่รู้ว่ามีภัยไหม" จากแหล่งหนึ่ง
    ไม่ควรทำให้ข้อมูลอีกแหล่งที่ดึงสำเร็จหายไปด้วย — เก็บ error ไว้ในแถวแทน
    """
    now = datetime.now(timezone.utc)
    events, errors = [], []

    for name, fetch in (("GISTDA", fetch_gistda), ("GDACS", fetch_gdacs)):
        try:
            events.extend(fetch(lat, lon))
        except Exception as e:
            errors.append(f"{name}: {e}")

    # snapshot_at เดียวกันทั้งชุด = "ผลการเช็ครอบนี้" ต้องอ่านคู่กันทั้งก้อน ห้ามปนรอบอื่น
    base = {"province": province, "district": district, "snapshot_at": now,
            "ref_lat": lat, "ref_lon": lon,
            "fetch_error": "; ".join(errors) or None, "updated_at": now}

    return [{
        **base,
        "kind": e["kind"], "level": e["level"], "detail": e["detail"],
        "event_province": e["province"], "event_district": e["district"], "source": e["source"],
    } for e in events] or [{
        # ไม่มีภัย = ยังต้องเขียน 1 แถวไว้ ไม่งั้นแยกไม่ออกระหว่าง "ปลอดภัย" กับ "ยังไม่เคยเช็ค"
        **base,
        "kind": "", "level": "", "detail": "",
        "event_province": "", "event_district": "", "source": "",
    }]


def _fresh(cached: list[dict]) -> bool:
    if not cached:
        return False
    return min(r["updated_at"] for r in cached) > datetime.now(timezone.utc) - TTL


def load(province: str, district: str) -> list[dict]:
    """เฉพาะ snapshot ล่าสุดของเขตนั้น — รอบเก่ายังอยู่ใน DB แต่ไม่ปนเข้ามา

    ถ้า query ทั้งเขตโดยไม่กรอง snapshot แถวน้ำท่วมของสัปดาห์ก่อนจะไหลกลับมาด้วย
    ทำให้ has_alert() คืน True ทั้งที่รอบล่าสุดเช็คแล้วว่าปลอดภัย
    """
    rows, _ = db.latest_snapshot("fact_disaster", "snapshot_at",
                                 where="province = %s AND district = %s",
                                 params=(province, district))
    return sorted(rows, key=lambda r: (r["kind"], r["detail"]))


def get(province: str, district: str, lat: float, lon: float) -> tuple[list[dict], list[tuple]]:
    """คืน (rows, to_save) — to_save ไม่ว่างเมื่อยิงสดมาใหม่"""
    cached = load(province, district)
    if _fresh(cached):
        return cached, []
    fresh = rows(lat, lon, province, district)
    return fresh, [("fact_disaster", fresh)]


def has_alert(rows_: list[dict]) -> bool:
    """มีภัยพิบัติจริงไหม — ใช้ป้อน badge ระดับ 3 (เจออะไรก็ตามในรัศมี = เตือน)"""
    return any(r.get("kind") for r in rows_)


def format_rows(rows_: list[dict]) -> dict:
    """แถว DB → record ภาษาไทย — ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต ไม่แตะ DB"""
    events = [r for r in rows_ if r.get("kind")]
    updated = [r["updated_at"] for r in rows_ if r.get("updated_at")]
    errors = [r["fetch_error"] for r in rows_ if r.get("fetch_error")]

    return {
        "มีประกาศเตือนภัย": bool(events),
        "ภัยพิบัติ": [{
            "ประเภท": r["kind"],
            "ระดับ": r["level"],
            "รายละเอียด": r["detail"],
            "พื้นที่": " ".join(x for x in (r.get("event_district"), r.get("event_province")) if x),
            "แหล่งข้อมูล": r["source"],
        } for r in events],
        "อัปเดตล่าสุด": max(updated).astimezone(TH_TZ).isoformat() if updated else None,
        "ข้อผิดพลาดบางแหล่ง": errors[0] if errors else None,
        "แหล่งข้อมูล": "GISTDA (น้ำท่วม/ไฟป่า) + GDACS (ภัยระดับโลก)",
    }


def summary(province: str = "Bangkok", district: str = "Chatuchak District",
            lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict:
    """ยิงสด + เขียน DB ทันที — ใช้ตอนรันมือ ไม่ใช่ทาง endpoint"""
    got, to_save = get(province, district, lat, lon)
    for table, save in to_save:
        db.save_rows(table, save)
    out = {"พื้นที่": {"จังหวัด": province, "เขต/อำเภอ": district}}
    out.update(format_rows(got))
    return out


def demo():
    """self-check — bbox, จัดกลุ่ม, TTL, format ไม่ต่อเน็ต ไม่แตะ DB"""
    now = datetime.now(timezone.utc)

    # GISTDA รับ bbox เป็น lon ก่อน lat — สลับแล้วจะ query ผิดพื้นที่แบบเงียบๆ
    assert bbox_around(13.8, 100.5, 0.1) == "100.4,13.700000000000001,100.6,13.9"

    assert not _fresh([]), "ไม่มีแถว = ไม่สด"
    assert _fresh([{"updated_at": now - timedelta(minutes=10)}])
    assert not _fresh([{"updated_at": now - timedelta(minutes=45)}]), "เกิน 30 นาทีต้องไม่สด"

    # ทุกแถวในรอบเดียวต้องมี snapshot_at ตรงกันเป๊ะ — ไม่งั้น latest_snapshot() จะหยิบมาแค่บางส่วน
    # (เช่นได้แถวน้ำท่วม 3 จาก 5 ตำบล เพราะอีก 2 แถวมี timestamp ต่างไปเสี้ยววินาที)
    import unittest.mock as _mock
    with _mock.patch(f"{__name__}.fetch_gistda", return_value=[
             {"kind": "น้ำท่วม", "level": "ส้ม", "detail": f"ตำบล{i}",
              "province": "", "district": "", "source": "GISTDA"}
             for i in range(3)]), \
         _mock.patch(f"{__name__}.fetch_gdacs", return_value=[]):
        batch = rows(13.8, 100.5, "Bangkok", "Chatuchak")
    assert len({r["snapshot_at"] for r in batch}) == 1, "ทั้งชุดต้องใช้ snapshot_at เดียวกัน"
    assert len(batch) == 3

    # แหล่งล่มทั้งคู่ → ต้องได้แถวว่าง 1 แถว (ไม่ใช่ลิสต์ว่าง) ไม่งั้นแยกไม่ออกจาก "ยังไม่เคยเช็ค"
    with _mock.patch(f"{__name__}.fetch_gistda", side_effect=RuntimeError("down")), \
         _mock.patch(f"{__name__}.fetch_gdacs", side_effect=RuntimeError("down")):
        empty = rows(13.8, 100.5, "Bangkok", "Chatuchak")
    assert len(empty) == 1 and empty[0]["kind"] == "" and "down" in empty[0]["fetch_error"]

    # แถวว่าง (ปลอดภัย) ต้องแยกออกจากแถวที่มีภัยจริง
    safe = [{"province": "Bangkok", "district": "Chatuchak", "kind": "", "level": "",
             "detail": "", "event_province": "", "event_district": "", "source": "",
             "fetch_error": None, "updated_at": now}]
    assert not has_alert(safe)
    out = format_rows(safe)
    assert out["มีประกาศเตือนภัย"] is False and out["ภัยพิบัติ"] == []

    danger = [{"province": "Bueng Kan", "district": "Mueang", "kind": "น้ำท่วม", "level": "ส้ม",
               "detail": "พื้นที่น้ำท่วม ต.หอคำ อ.บึงกาฬ จ.บึงกาฬ รวม 1.47 ตร.กม. (228 แปลง)",
               "event_province": "จ.บึงกาฬ", "event_district": "อ.บึงกาฬ", "source": "GISTDA",
               "fetch_error": None, "updated_at": now}]
    assert has_alert(danger)
    out = format_rows(danger)
    assert out["มีประกาศเตือนภัย"] is True
    assert out["ภัยพิบัติ"][0]["ประเภท"] == "น้ำท่วม"
    assert out["ภัยพิบัติ"][0]["พื้นที่"] == "อ.บึงกาฬ จ.บึงกาฬ"

    # แหล่งเดียวล่มต้องไม่ทำให้ทั้ง response หาย แค่แนบ error ไว้
    partial = [dict(danger[0], fetch_error="GDACS: timeout")]
    assert format_rows(partial)["ข้อผิดพลาดบางแหล่ง"] == "GDACS: timeout"
    assert format_rows(partial)["มีประกาศเตือนภัย"] is True

    assert format_rows([])["อัปเดตล่าสุด"] is None

    print("✅ ผ่าน — bbox (lon ก่อน lat), TTL 30 นาที, snapshot ชุดเดียวกัน, แยกปลอดภัย/มีภัย, แหล่งล่มไม่พัง")


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
