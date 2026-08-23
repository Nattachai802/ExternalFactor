"""MODULE — branch_id → พิกัด / จังหวัด / เขต-อำเภอ

flow: frontend ส่ง branch_id → ยิง Super Gourmet API เอา lat/lon
      → reverse geocode (Nominatim) เอาจังหวัด+เขต → cache ลง Postgres

    python -m modules.branch <branch_id>     # ดูจังหวัดของสาขานั้น
    python -m modules.branch test             # self-check (ไม่ต่อเน็ต ไม่แตะ DB)

cache 2 ชั้นแยกกันคนละคีย์:
    dim_branch     — branch_id → พิกัด (กัน branch API)
    dim_grid_area  — grid พิกัด → จังหวัด+เขต (กัน Nominatim, สาขาใกล้กันแชร์แถวเดียว)

⚠️ Nominatim usage policy: อย่างมาก 1 request/วินาที ต้อง cache ผลไว้เสมอ
   ห้ามยิงรัวใน loop โดยไม่มี cache
"""
import os
import re
import time

import requests

import db

BRANCH_PATH = "/api/owner-branch/v2/branches/{branch_id}/detail"
NOMINATIM_UA = "ex-factor-pipeline"

# ลองตามลำดับ host แรกที่ตอบสำเร็จคือตัวที่ใช้
#     ros-api.supercoconut  = prod (ของจริง)
#     sys.apisupergourmet   = test server (สำรอง)
#     ไม่เจอทั้งคู่           → ชั้น app fallback เป็น กทม. (ดู resolve_area ใน app_v1.py)
#
# prod ไม่มี latitude/longitude ในผลลัพธ์ เพราะผู้ใช้จริงไม่ได้กรอกที่อยู่ — เป็นเรื่องปกติ
# ไม่ใช่ error ปล่อยให้ fallback กทม.รับไปตามปกติ
DEFAULT_BRANCH_HOSTS = "https://ros-api.supercoconut.net,https://sys.apisupergourmet.com"


def branch_hosts() -> list[str]:
    raw = os.getenv("BRANCH_API_HOSTS") or DEFAULT_BRANCH_HOSTS
    return [h.strip().rstrip("/") for h in raw.split(",") if h.strip()]

_last_nominatim_call = 0.0


def grid_key(lat: float, lon: float) -> tuple[float, float]:
    """ปัดพิกัด ~11 กม. — สาขาใกล้กันแชร์ผล geocode เดียวกัน ลดการยิง Nominatim"""
    return round(lat, 1), round(lon, 1)


FIELDS = ("branch_id", "name", "lat", "lon", "province_fallback",
          "owner_id", "brand_id", "merchant_id")


def _get_branch(host: str, branch_id: str) -> dict:
    r = requests.get(host + BRANCH_PATH.format(branch_id=branch_id), timeout=15)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status", {}).get("code") != 200:
        raise ValueError(f"branch API ไม่สำเร็จ: {payload.get('status')}")
    return payload


def _fetch_branch_payload(branch_id: str) -> dict:
    """ลองทีละ host ตามลำดับ — ล้มหมดค่อย raise พร้อมเหตุผลของทุก host

    รายงานทุก host ไม่ใช่แค่ตัวสุดท้าย ไม่งั้นเวลาตัวแรกพังเงียบๆ จะ debug ไม่ออก
    ว่าเกิดอะไรขึ้นกับตัวที่ควรจะเป็น host หลัก
    """
    errors = []
    for host in branch_hosts():
        try:
            return _get_branch(host, branch_id)
        except Exception as e:
            errors.append(f"{host}: {type(e).__name__}: {e}")
    raise RuntimeError("ยิง branch API ไม่สำเร็จทุก host — " + " | ".join(errors))


def fetch_branch_detail(branch_id: str) -> dict:
    """เรียก branch API — คืน dict ตาม FIELDS

    owner_id/brand_id/merchant_id มาจาก response เดียวกับพิกัด ไม่ต้องยิงเส้นเพิ่ม
    (endpoint บางเส้นต้องส่ง owner_id ต่อไปยังระบบอื่น)

    ใช้ .get() กับทุก field เพราะ prod กับ test server คืน schema ไม่เหมือนกัน —
    prod ไม่มี latitude/longitude/address/province เลย (ผู้ใช้จริงไม่ได้กรอก)
    เข้าถึงด้วย b["latitude"] ตรงๆ จะ KeyError ทั้งที่ข้อมูลส่วนอื่นใช้ได้ปกติ
    """
    b = _fetch_branch_payload(branch_id)["data"]["branch"]
    prov = b.get("province") or {}
    return {
        "branch_id": str(b.get("branch_id") or branch_id), "name": b.get("name") or "",
        "lat": b.get("latitude") or 0, "lon": b.get("longitude") or 0,
        "province_fallback": prov.get("name_en") or prov.get("name_th") or "",
        "owner_id": b.get("owner_id") or "",
        "brand_id": b.get("brand_id") or "",
        "merchant_id": b.get("merchant_id") or "",
    }


def get_location(branch_id: str) -> dict:
    """branch_id → {branch_id, name, lat, lon, province_fallback} — cache ไว้ ไม่ยิง branch API ซ้ำ

    ทางลัดสำหรับโมดูลที่ต้องการแค่พิกัด (เช่น weather) ไม่ต้องผ่าน Nominatim reverse geocode
    ซึ่งจำกัด 1 request/วินาที
    """
    hit = db.query(
        "SELECT name, lat, lon, province_fallback, owner_id, brand_id, merchant_id"
        " FROM dim_branch WHERE branch_id = %s",
        (branch_id,),
    )
    # owner_id เป็น NULL = แถวถูก cache ไว้ก่อนที่จะเพิ่มคอลัมน์นี้ — ดึงใหม่ให้ครบ
    # ไม่งั้น endpoint ที่ต้องใช้ owner_id จะพังเฉพาะสาขาที่เคยเรียกก่อนอัปเกรด
    if hit and hit[0]["owner_id"] is not None:
        row = hit[0]
        return {"branch_id": branch_id, "name": row["name"],
                "lat": float(row["lat"]) if row["lat"] is not None else 0,
                "lon": float(row["lon"]) if row["lon"] is not None else 0,
                "province_fallback": row["province_fallback"],
                "owner_id": row["owner_id"], "brand_id": row["brand_id"],
                "merchant_id": row["merchant_id"]}

    branch = fetch_branch_detail(branch_id)
    db.save_rows("dim_branch", [{k: branch[k] for k in FIELDS}])
    return branch


def owner_of(branch_id: str) -> str:
    """branch_id → owner_id — สำหรับส่งต่อไปยัง API ที่ต้องระบุเจ้าของร้าน

    ใช้ cache เดียวกับ get_location() ไม่ยิง Super Gourmet API ซ้ำ
    คืน "" ถ้าสาขานั้นไม่มี owner_id — ผู้เรียกต้องเช็คก่อนส่งต่อ ไม่งั้น API ปลายทาง
    จะฟ้อง error ที่อ่านไม่ออกว่าเกิดจากอะไร
    """
    return get_location(branch_id).get("owner_id") or ""


def ids_of(branch_id: str) -> dict:
    """branch_id → {owner_id, brand_id, merchant_id} — เผื่อ API ปลายทางต้องใช้มากกว่า owner_id"""
    loc = get_location(branch_id)
    return {k: loc.get(k) or "" for k in ("owner_id", "brand_id", "merchant_id")}


def parse_province(address: dict) -> str:
    """แปลง address dict ของ Nominatim → ชื่อจังหวัดแบบเดียวกับ wage.py

    ปกติมาจาก address['province'] เช่น "Chiang Mai Province" → ตัด " Province" ท้าย
    กรุงเทพฯ ไม่มี field province (เป็นเขตปกครองพิเศษ) มีแต่ city="Bangkok" แทน
    """
    province = address.get("province")
    if province:
        return re.sub(r"\s*Province$", "", province).strip()
    if address.get("city") == "Bangkok":
        return "Bangkok"
    return ""


def parse_district(address: dict) -> str:
    """เขต (กทม.) / อำเภอ (ต่างจังหวัด) — Nominatim ใส่มาคนละ field

    กทม.ไม่มี county มีแต่ suburb = "Chatuchak District" (เขต)
    ต่างจังหวัดมีทั้งคู่ แต่ suburb เป็นแขวง/ตำบล (ย่อยเกินไป) ต้องใช้ county = "Mueang Chiang Mai District"
    เลือกตาม field ไม่ใช่ลำดับ or — ไม่งั้นต่างจังหวัดจะได้ตำบลแทนอำเภอ
    """
    if address.get("county"):
        return address["county"]
    return address.get("suburb", "") if address.get("city") == "Bangkok" else ""


def reverse_geocode(lat: float, lon: float) -> dict:
    """ยิง Nominatim จริง 1 ครั้ง — เว้นจังหวะเองให้ไม่เกิน 1 req/วินาทีตาม usage policy"""
    global _last_nominatim_call
    wait = 1.0 - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)

    r = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1, "accept-language": "en"},
        headers={"User-Agent": NOMINATIM_UA},
        timeout=15,
    )
    _last_nominatim_call = time.time()
    r.raise_for_status()
    return r.json().get("address", {})


def area_from_coords(lat: float, lon: float) -> dict:
    """lat/lon → {province, district} พร้อม cache ตาม grid — ยิง Nominatim 1 ครั้งต่อ grid"""
    grid_lat, grid_lon = grid_key(lat, lon)
    hit = db.query(
        "SELECT province, district FROM dim_grid_area WHERE grid_lat = %s AND grid_lon = %s",
        (grid_lat, grid_lon),
    )
    if hit:
        return {"province": hit[0]["province"], "district": hit[0]["district"]}

    address = reverse_geocode(lat, lon)
    area = {"province": parse_province(address), "district": parse_district(address)}
    db.save_rows("dim_grid_area", [{"grid_lat": grid_lat, "grid_lon": grid_lon, **area}])
    return area


def resolve_province(branch_id: str) -> dict:
    """branch_id → {branch_id, name, lat, lon, province, province_source}

    ลำดับความสำคัญ: lat/lon → reverse geocode ก่อนเสมอ (แม่นสุด)
    ถ้า geocode หาไม่เจอ (พิกัด 0,0 หรือ Nominatim ไม่มีข้อมูล) → fallback ไปใช้
    province ที่ frontend กรอกเองตอนสร้างสาขา

    ไม่มี cache ของตัวเอง — get_location() และ area_from_coords() cache ไว้แล้วทั้งคู่
    ชั้นนี้เหลือแค่ตรรกะเลือก ไม่มี I/O ซ้ำให้ต้อง cache
    """
    branch = get_location(branch_id)
    province, source = "", "none"

    if branch["lat"] and branch["lon"]:
        province = area_from_coords(branch["lat"], branch["lon"])["province"]
        if province:
            source = "geocode"

    if not province and branch["province_fallback"]:
        province, source = branch["province_fallback"], "branch_profile"

    return {"branch_id": branch_id, "name": branch["name"], "lat": branch["lat"],
            "lon": branch["lon"], "province": province, "province_source": source}


def demo():
    """self-check — ตรรกะ parse + fallback ล้วน ไม่ต่อเน็ต ไม่แตะ DB"""
    assert parse_province({"province": "Chiang Mai Province"}) == "Chiang Mai"
    assert parse_province({"province": "Yasothon Province"}) == "Yasothon"
    assert parse_province({"city": "Bangkok"}) == "Bangkok"
    assert parse_province({"city": "Chiang Mai City Municipality"}) == "", \
        "city ที่ไม่ใช่ Bangkok ต้องไม่ถูกเดาเป็นจังหวัด (ชื่อเทศบาลไม่ตรงชื่อจังหวัดเสมอไป)"
    assert parse_province({}) == ""

    assert parse_district({"city": "Bangkok", "suburb": "Chatuchak District"}) == "Chatuchak District", \
        "กทม.ไม่มี county ต้องใช้ suburb"
    assert parse_district({"province": "Chiang Mai Province", "suburb": "แขวงศรีวิชัย",
                           "county": "Mueang Chiang Mai District"}) == "Mueang Chiang Mai District", \
        "ต่างจังหวัดต้องได้อำเภอ (county) ไม่ใช่ตำบล (suburb)"
    assert parse_district({"suburb": "แขวงศรีวิชัย"}) == "", \
        "ไม่ใช่ กทม.และไม่มี county ต้องคืนว่าง ไม่ใช่เดาเอาตำบล"
    assert parse_district({}) == ""

    assert grid_key(13.8337, 100.5729) == (13.8, 100.6)
    assert grid_key(13.84, 100.56) == grid_key(13.849, 100.564), "สาขาใกล้กันต้องได้ grid เดียวกัน"

    # fallback logic — lat/lon มาก่อนเสมอ, province ที่กรอกเองใช้ต่อเมื่อ geocode หาไม่เจอ
    import unittest.mock as _mock

    def _resolve(branch, area):
        with _mock.patch(f"{__name__}.get_location", return_value=branch), \
             _mock.patch(f"{__name__}.area_from_coords", return_value=area):
            return resolve_province(branch["branch_id"])

    r = _resolve({"branch_id": "b1", "name": "t", "lat": 13.8, "lon": 100.5,
                  "province_fallback": "Nonthaburi"}, {"province": "Bangkok", "district": "Chatuchak"})
    assert r["province"] == "Bangkok" and r["province_source"] == "geocode", \
        "geocode หาเจอต้องใช้ค่านั้นก่อน ไม่ใช่ fallback"

    r = _resolve({"branch_id": "b2", "name": "t", "lat": 0, "lon": 0,
                  "province_fallback": "Nonthaburi"}, {"province": "", "district": ""})
    assert r["province"] == "Nonthaburi" and r["province_source"] == "branch_profile", \
        "geocode หาไม่เจอต้อง fallback ไปใช้ province ที่กรอกเอง"

    r = _resolve({"branch_id": "b3", "name": "t", "lat": 0, "lon": 0, "province_fallback": ""},
                 {"province": "", "district": ""})
    assert r["province"] == "" and r["province_source"] == "none", \
        "ไม่มีทั้งพิกัดและ fallback ต้องคืนค่าว่าง ไม่ใช่ error"

    # แถว cache เก่า (owner_id เป็น NULL) ต้องถูกดึงใหม่ ไม่ใช่คืนของเก่าที่ไม่มี owner_id
    stale = [{"name": "t", "lat": 13.8, "lon": 100.5, "province_fallback": "",
              "owner_id": None, "brand_id": None, "merchant_id": None}]
    fresh = {"branch_id": "b4", "name": "t", "lat": 13.8, "lon": 100.5,
             "province_fallback": "", "owner_id": "OWNER1", "brand_id": "BR1", "merchant_id": "M1"}
    with _mock.patch(f"{__name__}.db.query", return_value=stale), \
         _mock.patch(f"{__name__}.db.save_rows"), \
         _mock.patch(f"{__name__}.fetch_branch_detail", return_value=fresh) as spy:
        got = get_location("b4")
    assert spy.called, "cache เก่าที่ไม่มี owner_id ต้องยิง API ใหม่"
    assert got["owner_id"] == "OWNER1"

    with _mock.patch(f"{__name__}.db.query", return_value=[dict(stale[0], owner_id="OWNER1",
                                                                 brand_id="BR1", merchant_id="M1")]), \
         _mock.patch(f"{__name__}.fetch_branch_detail") as spy2:
        got = get_location("b4")
    assert not spy2.called, "cache ที่ครบแล้วต้องไม่ยิง API ซ้ำ"
    assert got["owner_id"] == "OWNER1" and got["merchant_id"] == "M1"

    # ลำดับ host อ่านจาก env ได้ — ตอนย้าย prod จะได้ไม่ต้องแก้โค้ด
    with _mock.patch.dict(os.environ, {"BRANCH_API_HOSTS": "https://a.test/, https://b.test"}):
        assert branch_hosts() == ["https://a.test", "https://b.test"], "ต้องตัดช่องว่างและ / ท้าย"
    with _mock.patch.dict(os.environ, {}, clear=True):
        assert branch_hosts()[0] == "https://ros-api.supercoconut.net", "prod ต้องมาก่อน test server"

    # host แรกพัง ต้องไปต่อตัวถัดไป ไม่ใช่ล้มทั้งคำขอ
    calls = []
    def flaky(host, bid):
        calls.append(host)
        if host == "https://a.test":
            raise ConnectionError("down")
        return {"ok": True}

    with _mock.patch.dict(os.environ, {"BRANCH_API_HOSTS": "https://a.test,https://b.test"}), \
         _mock.patch(f"{__name__}._get_branch", side_effect=flaky):
        assert _fetch_branch_payload("x") == {"ok": True}
    assert calls == ["https://a.test", "https://b.test"]

    # พังทุก host ต้องแจ้งเหตุผลของทุกตัว ไม่ใช่แค่ตัวสุดท้าย
    with _mock.patch.dict(os.environ, {"BRANCH_API_HOSTS": "https://a.test,https://c.test"}), \
         _mock.patch(f"{__name__}._get_branch", side_effect=ConnectionError("down")):
        try:
            _fetch_branch_payload("x")
        except RuntimeError as e:
            assert "a.test" in str(e) and "c.test" in str(e)
        else:
            raise AssertionError("พังทุก host ต้อง raise")

    # owner_of / ids_of — ต้องอ่านผ่าน get_location() ไม่ยิง API เอง
    with _mock.patch(f"{__name__}.get_location", return_value=fresh):
        assert owner_of("b4") == "OWNER1"
        assert ids_of("b4") == {"owner_id": "OWNER1", "brand_id": "BR1", "merchant_id": "M1"}

    # สาขาที่ไม่มี owner_id ต้องได้ "" ไม่ใช่ None — ส่ง None ต่อไป API ปลายทางจะกลายเป็น "None"
    with _mock.patch(f"{__name__}.get_location", return_value=dict(fresh, owner_id=None)):
        assert owner_of("b5") == ""
        assert ids_of("b5")["owner_id"] == ""

    print("✅ ผ่าน — parse_province, parse_district, grid_key, fallback lat/lon→province, "
          "owner_of/ids_of, fallback host + รายงาน error ครบ")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args and args[0] == "test":
        demo()
    elif args and "--ids" in sys.argv:
        print(json.dumps(ids_of(args[0]), ensure_ascii=False, indent=1))
    elif args:
        print(json.dumps(resolve_province(args[0]), ensure_ascii=False, indent=1))
    else:
        print("usage: python -m modules.branch <branch_id> [--ids] | test")
