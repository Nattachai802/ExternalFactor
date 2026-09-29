"""MODULE — ภัยพิบัติใกล้สาขา (thaiwater)

แหล่งเดียว: thaiwater (สสน.) — น้ำท่วมจากระดับน้ำสถานีวัด สดทุก 10 นาที จับน้ำท่วมในเมืองได้
ตรรกะเลือกสถานี/เกณฑ์ระดับ/รัศมีอยู่ใน modules/thaiwater.py ที่เดียว

ตัดออกแล้ว:
  GISTDA (ดาวเทียม น้ำท่วม/ไฟป่า) — flood/7days ฝั่ง GISTDA ตอบช้า ~7 วิ ทุกครั้ง (ไม่ขึ้นกับ limit)
    จน endpoint ช้าตาม ข้อมูลย้อน 7 วันเก่ากว่า thaiwater มาก และจับน้ำท่วมในเมืองไม่ได้
    (กทม. 26 ก.ย. 2026 ได้ 0 แปลง ขณะที่สถานีวัดขึ้นระดับ 5) ไฟป่าไม่ค่อยกระทบร้านในเมือง
  GDACS (ภัยระดับโลก) — กรอบ ±11 กม.รอบสาขาแทบไม่เคยมีศูนย์กลางเหตุการณ์ตกลงมา
    แต่ต้องโหลด RSS ทั้งโลกทุกรอบ

    python -m modules.disaster                    # ภัยพิบัติรอบพิกัด default
    python -m modules.disaster --lat 18.4 --lon 103.4
    python -m modules.disaster test                # self-check (ไม่ต่อเน็ต)
"""
from datetime import datetime, timedelta, timezone

import db
from modules import thaiwater

TH_TZ = timezone(timedelta(hours=7))
DEFAULT_LAT, DEFAULT_LON = 13.8479, 100.5697

TTL = timedelta(minutes=30)   # สั้นกว่า weather มาก — ภัยพิบัติต้องสด


def fetch_thaiwater(lat: float, lon: float) -> list[dict]:
    """ระดับน้ำคลอง/แม่น้ำใกล้สาขา — อ่านจาก DB ที่ cron เก็บไว้ (ทุกชั่วโมง)

    ยิงสดเฉพาะตอน DB ไม่มีข้อมูลใหม่เลย — ถ้ายิงทุกครั้งที่ "ไม่มีสถานีเตือนใกล้สาขา"
    ซึ่งคือสภาพปกติ ข้อมูลที่ cron เก็บไว้จะแทบไม่ได้ใช้ และโหลดทั้งประเทศทุกรอบ
    """
    rows_ = thaiwater.load_fresh()
    if not rows_:   # ponytail: DB ว่าง = cron ยังไม่รัน หรือทั้งประเทศไม่มีสถานีเข้าเกณฑ์
        rows_ = thaiwater.recent(thaiwater.fetch_current())   # ตัดสถานีค่าค้าง
    station = thaiwater.nearest_alert(lat, lon, rows=rows_)
    return [thaiwater.as_event(station)] if station else []


def rows(lat: float, lon: float, province: str, district: str) -> list[dict]:
    """แถว fact_disaster ของรอบนี้ — แหล่งล่มไม่ปล่อย exception ออกไป เก็บ error ไว้ในแถวแทน"""
    now = datetime.now(timezone.utc)
    events, error = [], None
    try:
        events = fetch_thaiwater(lat, lon)
    except Exception as e:
        error = f"thaiwater: {e}"

    # snapshot_at เดียวกันทั้งชุด = "ผลการเช็ครอบนี้" ต้องอ่านคู่กันทั้งก้อน ห้ามปนรอบอื่น
    base = {"province": province, "district": district, "snapshot_at": now,
            "ref_lat": lat, "ref_lon": lon, "fetch_error": error, "updated_at": now}

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
    rows_, _ = db.latest_snapshot("fact_disaster", "snapshot_at",
                                  where="province = %s AND district = %s",
                                  params=(province, district))
    return sorted(rows_, key=lambda r: (r["kind"], r["detail"]))


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


def badge_level(rows_: list[dict]) -> int:
    """ระดับ badge จากภัยพิบัติ — น้ำล้นตลิ่ง (แดง) 3, น้ำมาก (ส้ม) 1, ไม่มีภัย 0"""
    events = [r for r in rows_ if r.get("kind")]
    if not events:
        return 0
    return 3 if any(r.get("level") == "แดง" for r in events) else 1


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
        "แหล่งข้อมูล": "thaiwater (ระดับน้ำคลอง/แม่น้ำ จากสถานีวัด สสน.)",
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
    """self-check — TTL, snapshot, format, แหล่งล่ม ไม่ต่อเน็ต ไม่แตะ DB"""
    import unittest.mock as _mock
    now = datetime.now(timezone.utc)
    HERE = (13.8479, 100.5697)          # จตุจักร

    assert not _fresh([]), "ไม่มีแถว = ไม่สด"
    assert _fresh([{"updated_at": now - timedelta(minutes=10)}])
    assert not _fresh([{"updated_at": now - timedelta(minutes=45)}]), "เกิน 30 นาทีต้องไม่สด"

    # ทุกแถวในรอบเดียวต้องมี snapshot_at ตรงกันเป๊ะ — ไม่งั้น latest_snapshot() จะหยิบมาแค่บางส่วน
    with _mock.patch(f"{__name__}.fetch_thaiwater", return_value=[
             {"kind": "น้ำล้นตลิ่ง", "level": "แดง", "detail": f"สถานี{i}",
              "province": "", "district": "", "source": "thaiwater (สสน.)"}
             for i in range(3)]):
        batch = rows(*HERE, "Bangkok", "Chatuchak")
    assert len({r["snapshot_at"] for r in batch}) == 1, "ทั้งชุดต้องใช้ snapshot_at เดียวกัน"
    assert len(batch) == 3 and has_alert(batch) and batch[0]["kind"] == "น้ำล้นตลิ่ง"

    # แหล่งล่ม → ต้องได้แถวว่าง 1 แถว (ไม่ใช่ลิสต์ว่าง) ไม่งั้นแยกไม่ออกจาก "ยังไม่เคยเช็ค"
    with _mock.patch(f"{__name__}.fetch_thaiwater", side_effect=RuntimeError("down")):
        empty = rows(*HERE, "Bangkok", "Chatuchak")
    assert len(empty) == 1 and empty[0]["kind"] == "" and "thaiwater: down" == empty[0]["fetch_error"]
    assert not has_alert(empty)

    # DB มีข้อมูล → ห้ามยิงสด / DB ว่าง → ยิงสด
    with _mock.patch("modules.thaiwater.load_fresh", return_value=[{"x": 1}]), \
         _mock.patch("modules.thaiwater.fetch_current") as live, \
         _mock.patch("modules.thaiwater.nearest_alert", return_value={}):
        assert fetch_thaiwater(*HERE) == [] and not live.called, "DB มีข้อมูลต้องไม่ยิงสด"
    with _mock.patch("modules.thaiwater.load_fresh", return_value=[]), \
         _mock.patch("modules.thaiwater.fetch_current", return_value=[]) as live, \
         _mock.patch("modules.thaiwater.nearest_alert", return_value={}):
        fetch_thaiwater(*HERE)
        assert live.called, "DB ว่างต้องยิงสด"

    # แถวว่าง (ปลอดภัย) ต้องแยกออกจากแถวที่มีภัยจริง
    safe = [{"province": "Bangkok", "district": "Chatuchak", "kind": "", "level": "",
             "detail": "", "event_province": "", "event_district": "", "source": "",
             "fetch_error": None, "updated_at": now}]
    assert not has_alert(safe) and badge_level(safe) == 0
    assert badge_level([dict(safe[0], kind="น้ำล้นตลิ่ง", level="แดง")]) == 3
    assert badge_level([dict(safe[0], kind="ระดับน้ำสูง", level="ส้ม")]) == 1, "น้ำมากแค่เฝ้าระวัง"
    out = format_rows(safe)
    assert out["มีประกาศเตือนภัย"] is False and out["ภัยพิบัติ"] == []

    danger = [dict(safe[0], kind="น้ำล้นตลิ่ง", level="แดง", detail="คลองลาดพร้าว",
                   event_province="กรุงเทพมหานคร", event_district="เขตจตุจักร",
                   source="thaiwater (สสน.)")]
    out = format_rows(danger)
    assert out["มีประกาศเตือนภัย"] is True and out["ภัยพิบัติ"][0]["พื้นที่"] == "เขตจตุจักร กรุงเทพมหานคร"

    partial = [dict(danger[0], fetch_error="thaiwater: timeout")]
    assert format_rows(partial)["ข้อผิดพลาดบางแหล่ง"] == "thaiwater: timeout"
    assert format_rows([])["อัปเดตล่าสุด"] is None

    print("✅ ผ่าน — TTL 30 นาที, snapshot ชุดเดียวกัน, แหล่งล่มไม่พัง, DB ก่อนยิงสด, แยกปลอดภัย/มีภัย")


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
