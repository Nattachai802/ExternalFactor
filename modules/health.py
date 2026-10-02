"""MODULE — health check ของ service + dependency ที่ใช้ (รูปแบบกลางของทีม)

    {"service", "status": healthy|degraded|unhealthy, "checks": {ชื่อ: {status, latency_ms, operation}}}

Postgres ล่ม = unhealthy (ทุก endpoint พัง) → ผู้เรียกตอบ 503
API ภายนอกล่ม = degraded (ระบบยังตอบได้ แค่บางส่วนใช้ fallback) → ยังตอบ 200

API ภายนอกเช็คแค่ "host ตอบไหม" (status < 500 = ตอบ) ด้วย GET หน้าแรก ไม่ยิง endpoint จริง:
  - OWM จำกัด 1,000 call/วัน — monitor ยิงทุกนาทีก็เกินแล้ว หน้าแรกของ host ไม่กินโควตา
  - thaiwater endpoint จริงหนัก 1.4 MB / sale-forecast เป็น POST — ไม่ควรยิงทุกนาที
  ยืนยันไม่ได้ว่า key/endpoint ใช้ได้ — แค่ยืนยันว่าต่อถึงและ server ไม่ล่ม

ยิงพร้อมกัน timeout ตัวละ 3 วิ + cache ผล 60 วิ — monitor ยิงถี่แค่ไหนก็ไม่ไปทุบ API คนอื่น

    python -m modules.health          # เช็คจริง
    python -m modules.health test     # self-check (ไม่ต่อเน็ต ไม่แตะ DB)
"""
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import db

SERVICE = "exfactor-api"
TIMEOUT = 3
CACHE_SECONDS = 60

# ชื่อ → (URL ที่ยิง, คำอธิบาย operation) — host เดียวกับที่ modules/* ใช้จริง
EXTERNAL = {
    "openweathermap": ("https://api.openweathermap.org/", "GET / (reachability)"),
    "supergourmet_api": ("https://ros-api.supercoconut.net/", "GET / (reachability)"),   # Branch + POS
    "sale_forecast_api": ("https://sys.supercoconut.net/", "GET / (reachability)"),
    "nominatim": ("https://nominatim.openstreetmap.org/status?format=json", "GET /status"),
    "thaiwater": ("https://api-v3.thaiwater.net/", "GET / (reachability)"),
    "air4thai": ("http://air4thai.pcd.go.th/", "GET / (reachability)"),
}
HEADERS = {"User-Agent": "exfactor-healthcheck"}   # Nominatim บังคับมี UA

_cache: tuple[float, dict] | None = None


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def check_postgres() -> dict:
    start = time.perf_counter()
    try:
        db.query("SELECT 1")
        return {"status": "healthy", "latency_ms": _ms(start), "operation": "SELECT 1"}
    except Exception as e:
        return {"status": "unhealthy", "latency_ms": _ms(start), "operation": "SELECT 1",
                "error": f"{type(e).__name__}: {e}"[:200]}


def check_http(url: str, operation: str) -> dict:
    start = time.perf_counter()
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=False, headers=HEADERS)
        ok = r.status_code < 500
        out = {"status": "healthy" if ok else "unhealthy", "latency_ms": _ms(start),
               "operation": operation}
        if not ok:
            out["error"] = f"HTTP {r.status_code}"
        return out
    except Exception as e:
        return {"status": "unhealthy", "latency_ms": _ms(start), "operation": operation,
                "error": type(e).__name__}


def overall(checks: dict) -> str:
    if checks["postgresql"]["status"] != "healthy":
        return "unhealthy"
    return "healthy" if all(c["status"] == "healthy" for c in checks.values()) else "degraded"


def run() -> dict:
    """ผลเช็ค — ใช้ cache ถ้ายังไม่เกิน CACHE_SECONDS"""
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]

    with ThreadPoolExecutor(len(EXTERNAL) + 1) as ex:
        pg = ex.submit(check_postgres)
        jobs = {name: ex.submit(check_http, url, op) for name, (url, op) in EXTERNAL.items()}
    checks = {"postgresql": pg.result(), **{n: j.result() for n, j in jobs.items()}}

    result = {"service": SERVICE, "status": overall(checks), "checks": checks}
    _cache = (time.monotonic(), result)
    return result


def demo():
    """self-check — ไม่ต่อเน็ต ไม่แตะ DB"""
    from unittest import mock
    global _cache

    ok = {"status": "healthy"}
    bad = {"status": "unhealthy"}
    assert overall({"postgresql": ok, "thaiwater": ok}) == "healthy"
    assert overall({"postgresql": ok, "thaiwater": bad}) == "degraded", "API นอกล่ม = degraded"
    assert overall({"postgresql": bad, "thaiwater": ok}) == "unhealthy", "DB ล่ม = unhealthy"

    # 404/301 = host ตอบ = healthy / 5xx หรือ timeout = unhealthy
    for code, want in ((404, "healthy"), (301, "healthy"), (503, "unhealthy")):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=code)):
            assert check_http("http://x", "op")["status"] == want, code
    with mock.patch("requests.get", side_effect=requests.Timeout()):
        got = check_http("http://x", "op")
        assert got["status"] == "unhealthy" and got["error"] == "Timeout"

    with mock.patch("db.query", side_effect=RuntimeError("down")):
        assert check_postgres()["status"] == "unhealthy"

    # cache: เรียกซ้ำภายใน 60 วิ ต้องไม่ยิงใหม่
    _cache = None
    with mock.patch("db.query"), \
         mock.patch("requests.get", return_value=mock.Mock(status_code=200)) as get:
        first = run()
        run()
        assert get.call_count == len(EXTERNAL), "เรียกซ้ำต้องใช้ cache"
    assert first["status"] == "healthy" and set(first["checks"]) == {"postgresql", *EXTERNAL}
    _cache = None

    print("✅ ผ่าน — healthy/degraded/unhealthy, 4xx/3xx = ตอบ, 5xx/timeout = ล่ม, cache 60 วิ")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        print(json.dumps(run(), ensure_ascii=False, indent=1))
