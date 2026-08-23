"""exFactor API v1 — endpoint รวมของทุกโมดูลเอกเทศน์ใน modules/

แยกจาก app.py เดิม (ซึ่งยังผูกกับ pipeline.py monolith) — ไฟล์นี้คุยกับ
modules/*.py เท่านั้น ทุก endpoint เป็น GET (อ่านอย่างเดียว ไม่มีผลข้างเคียง)

    uvicorn app_v1:app --reload
"""
import calendar
from datetime import date as _date
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query

load_dotenv()

import db
import modules.air_quality
import modules.badge
import modules.branch
import modules.disaster
import modules.economic
import modules.electricity
import modules.energy
import modules.food_price
import modules.holiday
import modules.lucky_shirt
import modules.myth
import modules.sale_forecast
import modules.sales
import modules.wage
import modules.weather

app = FastAPI(title="exFactor API v1", version="1.0.0",
              description="ข้อมูลปัจจัยภายนอกสำหรับร้านอาหาร — ปฏิทินจีน วันหยุด พลังงาน ค่าไฟ ค่าแรง เศรษฐกิจ ราคาวัตถุดิบ สภาพอากาศ")


def _parse_date(s: str | None) -> tuple[_date | None, str | None]:
    """แปลง query string เป็นวันที่ — format ผิดไม่ error แต่คืน (None, คำเตือน) แทน

    ผู้เรียกส่ง None ต่อให้ summary() ซึ่ง fallback เป็นวันล่าสุด/วันนี้เองอยู่แล้ว
    """
    if not s:
        return None, None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"รูปแบบวันที่ '{s}' ไม่ถูกต้อง (ต้องเป็น YYYY-MM-DD) — แสดงข้อมูลล่าสุดแทน"


def _with_warning(result: dict, warning: str | None) -> dict:
    if warning:
        result["คำเตือน"] = warning
    return result


def _wrap(fn, *args, **kwargs):
    """เรียก summary() ของโมดูล — แปลง exception ที่หลุดมาเป็น 500 พร้อมข้อความ"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def resolve_owner(branch_id: str, owner_id: str | None = None) -> str:
    """หา owner_id ของสาขา — ใช้ค่าที่ส่งมาเองก่อนถ้ามี

    สาขาบางตัวมีอยู่ในระบบ POS/พยากรณ์ แต่ไม่มีใน Super Gourmet branch registry
    (คนละฐานข้อมูลกัน) กรณีนั้นหาเองไม่ได้ ต้องให้ผู้เรียกส่ง owner_id มาตรงๆ
    """
    if owner_id:
        return owner_id

    try:
        found = modules.branch.owner_of(branch_id)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"ไม่พบสาขา {branch_id} ในระบบ Super Gourmet — ถ้าทราบ owner_id ให้ส่งมาใน query param owner_id")

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"สาขา {branch_id} ไม่มี owner_id ในระบบ — ส่งมาใน query param owner_id ได้ถ้าทราบค่า")
    return found


# สาขาไม่จำเป็นต้องกรอกพิกัด/ที่อยู่ตอนสร้าง — ร้านใหม่จำนวนมากจึงมี lat/lon = 0
# ตอบข้อมูลของ กทม.ไปก่อนดีกว่า 404 เปล่าๆ (ผู้ใช้ส่วนใหญ่อยู่ กทม.)
DEFAULT_LAT, DEFAULT_LON = 13.7563, 100.5018       # ศาลาว่าการ กทม.
DEFAULT_PROVINCE, DEFAULT_DISTRICT = "Bangkok", "Phra Nakhon District"


def resolve_area(branch_id: str) -> tuple[dict, dict, bool]:
    """คืน (ข้อมูลสาขา, พื้นที่, ใช้ค่าเริ่มต้นหรือไม่)

    สาขาที่ยังไม่ตั้งพิกัด หรือ geocode หาเขตไม่เจอ → ใช้ กทม.แทน ไม่ error
    ผู้เรียกเอา flag ไปแนบใน response ให้ frontend รู้ว่าข้อมูลนี้ไม่ได้อิงตำแหน่งจริง
    """
    loc = _wrap(modules.branch.get_location, branch_id)

    if loc.get("lat") and loc.get("lon"):
        area = _wrap(modules.branch.area_from_coords, loc["lat"], loc["lon"])
        if area.get("district"):
            return loc, area, False

    return ({**loc, "lat": DEFAULT_LAT, "lon": DEFAULT_LON},
            {"province": DEFAULT_PROVINCE, "district": DEFAULT_DISTRICT}, True)


def default_area_warning(branch_id: str) -> str:
    return (f"สาขา {branch_id} ยังไม่ได้ตั้งค่าตำแหน่ง — แสดงข้อมูลของ "
            f"{DEFAULT_DISTRICT} {DEFAULT_PROVINCE} แทน")


def resolve_branch(branch_id: str) -> dict:
    """branch_id → {branch_id, name, lat, lon, province, province_source}

    ฟังก์ชันกลาง — ทุก endpoint ที่ต้องรู้ตำแหน่งสาขา (wage, weather, ...) เรียกจุดนี้จุดเดียว
    แปลง branch_id → lat/lon → จังหวัด เป็นหน้าที่ฝั่งเรา ไม่ใช่ frontend

    ไม่มีทั้งพิกัดและจังหวัดที่กรอกไว้ → ใช้ กทม.แทน ไม่ 404
    (พิกัดไม่ใช่ field บังคับตอนสร้างสาขา ร้านใหม่จำนวนมากจึงว่าง)
    """
    loc = _wrap(modules.branch.resolve_province, branch_id)
    if not loc["province"]:
        loc = {**loc, "province": DEFAULT_PROVINCE, "province_source": "default"}
    return loc


@app.get("/", summary="Health check")
def root():
    return {"status": "ok", "service": "exFactor API v1"}


# ── ปฏิทินจีน ────────────────────────────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_myth ที่ cron เขียนไว้ ไม่ scrape สดตอน request
@app.get("/api/v1/myth", summary="ปฏิทินจีน (โหราศาสตร์รายวัน)")
def get_myth(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันล่าสุดในระบบ",
                             examples=["2026-08-21"]),
):
    d, warning = _parse_date(date)

    rows, actual_date = db.latest_snapshot("fact_myth", "date", d)
    if not actual_date:
        raise HTTPException(status_code=404, detail="ยังไม่มีข้อมูลปฏิทินจีนใน DB เลย")

    result = modules.myth.format_rows(rows, actual_date)
    if d and actual_date != d.isoformat():
        warning = (warning + " " if warning else "") + \
                 f"ไม่มีข้อมูลของวันที่ {d.isoformat()} — แสดงข้อมูลล่าสุด ({actual_date}) แทน"
    return _with_warning(result, warning)


# ── สีเสื้อมงคล ──────────────────────────────────────────────
# ตารางคงที่ผูกวันในสัปดาห์ ไม่ใช่วันที่จริง — ไม่มี concept "ล่าสุดในระบบ" แบบ myth/holiday
# แปลง date → วันในสัปดาห์ก่อน แล้ว query ตรง ไม่ต้องถอยหาวันก่อนหน้า
@app.get("/api/v1/lucky-shirt", summary="สีเสื้อมงคลตามวันในสัปดาห์")
def get_lucky_shirt(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันนี้",
                             examples=["2026-08-24"]),
):
    d, warning = _parse_date(date)
    weekday = modules.lucky_shirt.weekday_th(d or _date.today())

    rows = db.query(
        "SELECT weekday, category, color_th, hex, source FROM dim_lucky_shirt WHERE weekday = %s",
        (weekday,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="ยังไม่มีข้อมูลสีเสื้อมงคลใน DB เลย")

    return _with_warning(modules.lucky_shirt.format_rows(rows, weekday), warning)


# ── วันหยุด / ภาคเรียน / แอลกอฮอล์ ──────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_event ที่ cron เขียนไว้
# เปิดเทอม/เสาร์อาทิตย์ คำนวณสดตอน format เพราะเป็นเลขคณิต ไม่ใช่สิ่งที่ scrape มา
@app.get("/api/v1/holiday", summary="ปฏิทินวันหยุด/ภาคเรียน/ห้ามขายแอลกอฮอล์")
def get_holiday(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันนี้",
                             examples=["2026-03-03"]),
    month: str | None = Query(None, description="ทั้งเดือน (YYYY-MM) — ใช้แทน date",
                              examples=["2026-03"]),
    year: int | None = Query(None, ge=2020, le=2100, description="ทั้งปี — ใช้แทน date/month"),
    events_only: bool = Query(False, description="true = เอาเฉพาะวันที่มีเหตุการณ์"),
):
    warning = None
    start = end = None

    if year:
        start, end = _date(year, 1, 1), _date(year, 12, 31)
    elif month:
        try:
            first = datetime.strptime(month, "%Y-%m").date()
            last_day = calendar.monthrange(first.year, first.month)[1]
            start, end = first, first.replace(day=last_day)
        except ValueError:
            # format ผิดไม่ error — fallback เป็นวันนี้ เหมือนที่ date ทำ
            warning = f"รูปแบบเดือน '{month}' ไม่ถูกต้อง (ต้องเป็น YYYY-MM) — แสดงข้อมูลวันนี้แทน"

    if start is None:
        d, date_warning = _parse_date(date)
        warning = " ".join(w for w in (warning, date_warning) if w) or None
        start = end = d or _date.today()

    rows = db.rows_between("fact_event", "event_date", start, end, order_by="event_date")
    result = modules.holiday.format_rows(rows, start, end)

    if events_only:
        result["ปฏิทิน"] = [day for day in result["ปฏิทิน"] if day["เหตุการณ์"]]

    return _with_warning(result, warning)


# ── น้ำมัน / แก๊ส ────────────────────────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_daily ที่ cron เขียนไว้ ไม่ scrape สดตอน request
@app.get("/api/v1/energy", summary="ราคาน้ำมัน/แก๊สรายวัน (จาก DB)")
def get_energy(date: str | None = None):
    d, warning = _parse_date(date)
    rows, actual_date = db.latest_snapshot(
        "fact_daily", "date", d, where="(source LIKE %s OR source LIKE %s)",
        params=("Kapook%", "EPPO%"),
    )
    if not actual_date:
        raise HTTPException(status_code=404, detail="ยังไม่มีข้อมูลราคาน้ำมัน/แก๊สใน DB เลย")

    # เทียบกับ "วันก่อนหน้าที่มีข้อมูลจริง" ไม่ใช่ actual_date - 1 วันเป๊ะ — เผื่อวันที่ cron ยังไม่รัน
    actual = datetime.strptime(actual_date, "%Y-%m-%d").date()
    prev_date = db.max_date("fact_daily", "date", on_or_before=actual - timedelta(days=1),
                            where="(source LIKE %s OR source LIKE %s)", params=("Kapook%", "EPPO%"))
    if prev_date:
        prev_rows = db.rows_between("fact_daily", "date", prev_date, prev_date,
                                    where="(source LIKE %s OR source LIKE %s)",
                                    params=("Kapook%", "EPPO%"))
    else:
        # ไม่มีข้อมูลของวันก่อนหน้าเลยใน DB — ยิง PTTOR ตรงแทนปล่อยส่วนต่างราคาเป็น None ทิ้ง
        # ล่มก็ไม่ทำให้ทั้ง endpoint พัง แค่ trend ไม่มี (เหมือนพฤติกรรมเดิม)
        try:
            prev_rows = modules.energy.fallback_prev_day(actual - timedelta(days=1))
        except Exception:
            prev_rows = []

    result = modules.energy.format_rows(rows, actual_date, prev_rows)
    if d and actual_date != d.isoformat():
        warning = (warning + " " if warning else "") + \
                 f"ไม่มีข้อมูลของวันที่ {d.isoformat()} — แสดงข้อมูลล่าสุด ({actual_date}) แทน"
    return _with_warning(result, warning)


# ── ค่าไฟ ────────────────────────────────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_daily ที่ cron เขียนไว้ ไม่ scrape สดตอน request
@app.get("/api/v1/electricity", summary="อัตราค่าไฟฟ้า (MEA)")
def get_electricity(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันล่าสุดในระบบ",
                             examples=["2026-08-21"]),
    user_type: int | None = Query(None, ge=1, le=8,
                                  description="ประเภทผู้ใช้ไฟ 1-8 — ไม่ระบุ = ทุกประเภท"),
    detailed: bool = Query(False,
                           description="แสดงรายละเอียดแยกตามรหัสอัตรา/แรงดัน/On-Off Peak"),
):
    d, warning = _parse_date(date)

    where, params = "source LIKE %s", ["MEA%"]
    if user_type:
        where += " AND metric_name LIKE %s"
        params.append(f"MEA ประเภท {user_type} %")

    rows, actual_date = db.latest_snapshot("fact_daily", "date", d,
                                           where=where, params=tuple(params))
    if not actual_date:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลอัตราค่าไฟใน DB ตามเงื่อนไขที่ระบุ")

    result = modules.electricity.format_rows(rows, actual_date, detailed=detailed)
    if d and actual_date != d.isoformat():
        warning = (warning + " " if warning else "") + \
                 f"ไม่มีข้อมูลของวันที่ {d.isoformat()} — แสดงข้อมูลล่าสุด ({actual_date}) แทน"
    return _with_warning(result, warning)


# ── ค่าแรงขั้นต่ำ ────────────────────────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_minimum_wage ที่ cron เขียนไว้ ไม่ scrape สดตอน request
def _query_wage(province: str | None, top: int | None, bottom: int | None) -> dict:
    where, params = "", ()
    if province:
        wanted = [p.strip().lower() for p in province.split(",") if p.strip()]
        where, params = "lower(jurisdiction) = ANY(%s)", (wanted,)

    rows, actual_date = db.latest_snapshot("fact_minimum_wage", "effective_date",
                                           where=where, params=params)
    if not actual_date:
        raise HTTPException(status_code=404, detail="ยังไม่มีข้อมูลค่าแรงขั้นต่ำใน DB เลย")

    records = [{"province": r["jurisdiction"], "wage": float(r["wage_value"]),
               "effective_date": r["effective_date"].isoformat()} for r in rows]
    return modules.wage.format_records(records, top, bottom)


@app.get("/api/v1/wage", summary="ค่าแรงขั้นต่ำรายจังหวัด (query ตรงจาก DB — สำหรับ admin/เทียบหลายจังหวัด)")
def get_wage(province: str | None = None, top: int | None = None, bottom: int | None = None):
    return _query_wage(province, top, bottom)


@app.get("/api/v1/wage/{branch_id}", summary="ค่าแรงขั้นต่ำของจังหวัดที่สาขานั้นตั้งอยู่")
def get_wage_by_branch(branch_id: str):
    loc = resolve_branch(branch_id)
    return {"branch": loc, "wage": _query_wage(loc["province"], None, None)}


# ── เศรษฐกิจ ─────────────────────────────────────────────────
# query อย่างเดียว — ข้อมูลมาจาก fact_daily ที่ cron เขียนไว้ ไม่ scrape สดตอน request
@app.get("/api/v1/economic", summary="อัตราแลกเปลี่ยนและเงินเฟ้อ")
def get_economic(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันล่าสุดในระบบ",
                             examples=["2026-08-22"]),
    currency: str | None = Query(None, description="กรองสกุลเงิน คั่นด้วย , — ไม่ระบุ = ทุกสกุล",
                                 examples=["USD,EUR,JPY"]),
):
    d, warning = _parse_date(date)

    where = "(source LIKE %s OR source LIKE %s)"
    params = ["Open Exchange%", "TradingEconomics%"]
    if currency:
        wanted = [c.strip().upper() for c in currency.split(",") if c.strip()]
        # เงินเฟ้อไม่ใช่สกุลเงิน — ให้ติดมาด้วยเสมอแม้กรองสกุล
        where += " AND (source LIKE %s OR metric_name = ANY(%s))"
        params += ["TradingEconomics%", [f"Exchange Rate {c}/THB" for c in wanted]]

    rows, actual_date = db.latest_snapshot("fact_daily", "date", d,
                                           where=where, params=tuple(params))
    if not actual_date:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลเศรษฐกิจใน DB ตามเงื่อนไขที่ระบุ")

    result = modules.economic.format_rows(rows, actual_date)
    if d and actual_date != d.isoformat():
        warning = (warning + " " if warning else "") + \
                 f"ไม่มีข้อมูลของวันที่ {d.isoformat()} — แสดงข้อมูลล่าสุด ({actual_date}) แทน"
    return _with_warning(result, warning)


# ── ราคาวัตถุดิบ (DIT) ───────────────────────────────────────
# query อย่างเดียว — DIT ไม่ประกาศราคาวันเสาร์อาทิตย์/วันหยุด
# ถามวันที่ไม่มีข้อมูล → ถอยไปวันล่าสุดก่อนหน้า, ถามวันที่เก่าเกินข้อมูลที่มี → วันล่าสุดในระบบ
@app.get("/api/v1/food-price", summary="ราคาอาหารและวัตถุดิบ")
def get_food_price(
    date: str | None = Query(None, description="วันที่ต้องการ (YYYY-MM-DD) — ไม่ระบุ = วันล่าสุดในระบบ",
                             examples=["2026-08-21"]),
    days: int = Query(1, ge=1, le=90,
                      description="ย้อนหลังกี่วันนับจาก date — มากกว่า 1 จะแนบ ประวัติ รายวันของแต่ละสินค้า"),
    protype: str = Query("ขายปลีก", description="ประเภทราคา: ขายปลีก / ขายส่ง / ทั้งหมด"),
    category: str | None = Query(None, description="กรองหมวดสินค้า — ไม่ระบุ = ทุกหมวด",
                                 examples=["เนื้อสัตว์"]),
):
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days ต้องอยู่ระหว่าง 1-90")

    d, warning = _parse_date(date)

    conditions, params = [], []
    # ไม่ระบุ = ขายปลีก (ราคาที่ผู้บริโภคจ่ายจริง) ส่ง "ทั้งหมด" เพื่อเอาทั้งปลีกและส่ง
    if protype and protype != "ทั้งหมด":
        conditions.append("protype = %s")
        params.append(protype)
    if category:
        conditions.append("category = %s")
        params.append(category)
    where, params = " AND ".join(conditions), tuple(params)

    # หาวันอ้างอิง: วันล่าสุดที่ <= วันที่ขอ ถ้าไม่มีเลย (ขอวันเก่าเกินไป) ถอยไปวันล่าสุดในระบบ
    anchor = db.max_date("fact_dit_price", "date", on_or_before=d, where=where, params=params)
    if not anchor:
        anchor = db.max_date("fact_dit_price", "date", where=where, params=params)
        if anchor and d:
            warning = (warning + " " if warning else "") + \
                     f"ไม่มีข้อมูลก่อนวันที่ {d.isoformat()} — แสดงวันล่าสุดในระบบ ({anchor.isoformat()}) แทน"
    if not anchor:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลราคาวัตถุดิบใน DB ตามเงื่อนไขที่ระบุ")

    if d and anchor.isoformat() != d.isoformat():
        warning = (warning + " " if warning else "") + \
                 f"ไม่มีข้อมูลของวันที่ {d.isoformat()} — ใช้วันล่าสุดก่อนหน้า ({anchor.isoformat()}) แทน"

    rows = db.rows_between(
        "fact_dit_price", "date", anchor - timedelta(days=days - 1), anchor,
        where=where, params=params, order_by="protype, product_name, date",
    )
    result = modules.food_price.format_rows(rows, as_of=anchor.isoformat(),
                                            with_history=days > 1)
    return _with_warning(result, warning)


# ── สภาพอากาศ ────────────────────────────────────────────────
# ต่างจาก endpoint อื่นตรงที่ยิง API สดได้ (ไม่มี cron) — DB เป็น cache ระดับเขต
# ยังหมดอายุไม่ครบ TTL ก็ตอบจาก DB ล้วน ไม่เปลืองโควตา OWM
@app.get("/api/v1/weather/{branch_id}", summary="พยากรณ์อากาศของสาขา — รายชั่วโมง 20 ชม. + รายวัน 10 วัน")
def get_weather(branch_id: str, background_tasks: BackgroundTasks,
                hours: int | None = Query(None, ge=1, le=20,
                                          description="จำกัดรายชั่วโมงกี่ชม.ข้างหน้า — ไม่ระบุ = ทั้งหมด")):
    loc, area, is_default = resolve_area(branch_id)

    try:
        hourly, daily, to_save = modules.weather.get(area["province"], area["district"],
                                                      loc["lat"], loc["lon"])
    except Exception:
        raise HTTPException(status_code=503, detail="ระบบพยากรณ์อากาศ (OWM) ขัดข้อง — กรุณาลองใหม่ภายหลัง")
    # ตอบก่อน เขียนทีหลัง — user ไม่ต้องรอ DB round-trip ที่ไม่มีผลกับคำตอบ
    for table, rows in to_save:
        background_tasks.add_task(db.save_rows, table, rows)

    result = {
        "branch": {"branch_id": branch_id, "name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"]},
        "พื้นที่": {"จังหวัด": area["province"], "เขต/อำเภอ": area["district"]},
        "weather": modules.weather.format_rows(hourly, daily, hours),
    }
    return _with_warning(result, default_area_warning(branch_id) if is_default else None)


# ── คุณภาพอากาศ ──────────────────────────────────────────────
# ts ตรงกับ weather hourly เป๊ะ — ยึด TTL/หน้าต่างเดียวกันตั้งใจให้ frontend match ts ได้ตรงๆ
@app.get("/api/v1/air-quality/{branch_id}", summary="คุณภาพอากาศของสาขา — รายชั่วโมง 20 ชม. (US AQI ประมาณ)")
def get_air_quality(branch_id: str, background_tasks: BackgroundTasks,
                    hours: int | None = Query(None, ge=1, le=20,
                                              description="จำกัดรายชั่วโมงกี่ชม.ข้างหน้า — ไม่ระบุ = ทั้งหมด")):
    loc, area, is_default = resolve_area(branch_id)

    try:
        rows, to_save = modules.air_quality.get(area["province"], area["district"],
                                                loc["lat"], loc["lon"])
    except Exception:
        raise HTTPException(status_code=503, detail="ระบบคุณภาพอากาศ (OWM) ขัดข้อง — กรุณาลองใหม่ภายหลัง")

    for table, save_rows in to_save:
        background_tasks.add_task(db.save_rows, table, save_rows)

    result = {
        "branch": {"branch_id": branch_id, "name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"]},
        "พื้นที่": {"จังหวัด": area["province"], "เขต/อำเภอ": area["district"]},
        "air_quality": modules.air_quality.format_rows(rows, hours),
    }
    return _with_warning(result, default_area_warning(branch_id) if is_default else None)


# ── Badge เตือนสภาพอากาศ ─────────────────────────────────────
# แยกจาก /weather และ /air-quality ตั้งใจ — แก้ตรรกะ Badge (สเปกยังไม่นิ่ง) ได้โดยไม่กระทบ
# 2 เส้นข้อมูลดิบ เรียก weather.get()/air_quality.get() ต่อจากทั้งคู่ (cache เดียวกัน ไม่ยิงซ้ำถ้ายังไม่หมดอายุ)
@app.get("/api/v1/weather-badge/{branch_id}", summary="Badge เตือนสภาพอากาศของสาขา (SuperTrend §A2)")
def get_weather_badge(branch_id: str, background_tasks: BackgroundTasks):
    loc, area, is_default = resolve_area(branch_id)

    try:
        hourly, daily, weather_save = modules.weather.get(area["province"], area["district"],
                                                           loc["lat"], loc["lon"])
        aqi_rows, aqi_save = modules.air_quality.get(area["province"], area["district"],
                                                      loc["lat"], loc["lon"])
    except Exception:
        raise HTTPException(status_code=503, detail="ระบบพยากรณ์อากาศ (OWM) ขัดข้อง — กรุณาลองใหม่ภายหลัง")

    # ภัยพิบัติล่มไม่ควรทำให้ badge ทั้งอันหาย — ไม่มีข้อมูลถือว่าไม่มีภัย (เงื่อนไขอื่นยังทำงาน)
    try:
        disaster_rows, disaster_save = modules.disaster.get(area["province"], area["district"],
                                                             loc["lat"], loc["lon"])
    except Exception:
        disaster_rows, disaster_save = [], []

    for table, rows in weather_save + aqi_save + disaster_save:
        background_tasks.add_task(db.save_rows, table, rows)

    current_id = hourly[0]["weather_id"] if hourly else None
    temp_max = daily[0]["temp_max"] if daily else None
    aqi = aqi_rows[0]["aqi_us"] if aqi_rows else None
    periods = modules.badge.pop_periods_remaining_today(hourly)

    badge = modules.badge.evaluate(current_id, periods, temp_max, aqi,
                                    disaster_alert=modules.disaster.has_alert(disaster_rows))

    result = {
        "branch": {"branch_id": branch_id, "name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"]},
        "พื้นที่": {"จังหวัด": area["province"], "เขต/อำเภอ": area["district"]},
        "badge": badge,
    }
    return _with_warning(result, default_area_warning(branch_id) if is_default else None)


# ── ยอดขายจริง ───────────────────────────────────────────────
# ไม่ผ่าน DB เลย — ยอดขายเป็นข้อมูลการเงิน บิลปิดเพิ่มได้ตลอดวัน เสิร์ฟของ cache = รายงานเงินผิด
# ไม่ต้องใช้พิกัดสาขา ร้านเปิดใหม่ที่ยังไม่ตั้ง lat/lon ก็ยิงได้
@app.get("/api/v1/sales/{branch_id}", summary="ยอดขายจริงรายวันจาก POS")
def get_sales(
    branch_id: str,
    owner_id: str | None = Query(None, description="รหัสเจ้าของร้าน — ไม่ระบุ = ระบบหาจาก branch_id ให้"),
    days: int = Query(7, ge=1, le=90, description="ย้อนหลังไปกี่วัน (นับรวมวันที่ระบุใน date)"),
    date: str | None = Query(None, description="ย้อนหลังจากวันไหน (YYYY-MM-DD) — ไม่ระบุ = ย้อนหลังจากวันนี้",
                             examples=["2026-08-22"]),
):
    d, warning = _parse_date(date)

    owner_id = resolve_owner(branch_id, owner_id)

    try:
        result = modules.sales.summary(owner_id, branch_id, days=days, end=d)
    except Exception:
        raise HTTPException(status_code=503, detail="ระบบยอดขาย (POS) ขัดข้อง — กรุณาลองใหม่ภายหลัง")

    return _with_warning({"branch": {"branch_id": branch_id, "owner_id": owner_id},
                          "sales": result}, warning)


# ── ยอดขายพยากรณ์ ────────────────────────────────────────────
# ค่าพยากรณ์ล้วน แม้แต่วันที่ผ่านมาแล้ว — ยอดขายจริงอยู่ที่ /api/v1/sales คนละตัวกัน
# ระบุ 2 หัวแทนที่จะเป็น days ย้อนหลังแบบ /sales เพราะช่วงมองไปข้างหน้าได้ด้วย
# frontend ที่วาดกราฟเทียบ "จริง vs คาด" ต้องเลือกหน้าต่างเองอยู่แล้ว
@app.get("/api/v1/sale-forecast/{branch_id}", summary="ยอดขายพยากรณ์ตามช่วงวัน (ไม่ใช่ยอดขายจริง)")
def get_sale_forecast(
    branch_id: str,
    owner_id: str | None = Query(None, description="รหัสเจ้าของร้าน — ไม่ระบุ = ระบบหาจาก branch_id ให้"),
    start: str | None = Query(None, description="วันเริ่มช่วง (YYYY-MM-DD) — ไม่ระบุ = วันนี้",
                              examples=["2026-08-01"]),
    end: str | None = Query(None, description="วันสุดท้ายของช่วง นับรวมวันนั้น (YYYY-MM-DD) — ไม่ระบุ = 6 วันหลังจาก start",
                            examples=["2026-08-31"]),
):
    today = _date.today()
    s, warn_s = _parse_date(start)
    e, warn_e = _parse_date(end)
    s = s or today
    e = e or s + timedelta(days=6)
    warning = " ".join(w for w in (warn_s, warn_e) if w) or None

    if e < s:
        raise HTTPException(status_code=400, detail="end ต้องไม่อยู่ก่อน start")

    owner_id = resolve_owner(branch_id, owner_id)

    try:
        result = modules.sale_forecast.summary(owner_id, branch_id, s, e)
    except Exception:
        raise HTTPException(status_code=503,
                            detail="ระบบพยากรณ์ยอดขายขัดข้อง — กรุณาลองใหม่ภายหลัง")

    return _with_warning({"branch": {"branch_id": branch_id, "owner_id": owner_id},
                          "sale_forecast": result}, warning)


# ── ภัยพิบัติ ────────────────────────────────────────────────
# แยกเส้นของตัวเอง — badge ใช้แค่ "มี/ไม่มี" แต่เส้นนี้ให้รายละเอียดครบ เอาไปใช้เรื่องอื่นได้
@app.get("/api/v1/disaster/{branch_id}", summary="ภัยพิบัติใกล้สาขา (GISTDA น้ำท่วม/ไฟป่า + GDACS)")
def get_disaster(branch_id: str, background_tasks: BackgroundTasks):
    loc, area, is_default = resolve_area(branch_id)

    try:
        rows, to_save = modules.disaster.get(area["province"], area["district"],
                                             loc["lat"], loc["lon"])
    except Exception:
        raise HTTPException(status_code=503, detail="ระบบข้อมูลภัยพิบัติขัดข้อง — กรุณาลองใหม่ภายหลัง")

    for table, save in to_save:
        background_tasks.add_task(db.save_rows, table, save)

    result = {
        "branch": {"branch_id": branch_id, "name": loc["name"],
                   "lat": loc["lat"], "lon": loc["lon"]},
        "พื้นที่": {"จังหวัด": area["province"], "เขต/อำเภอ": area["district"]},
        "disaster": modules.disaster.format_rows(rows),
    }
    return _with_warning(result, default_area_warning(branch_id) if is_default else None)
