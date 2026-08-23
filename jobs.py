"""Cron entry point — ดึงข้อมูลจากโมดูล แล้วเขียนลง Postgres

API (app_v1.py) อ่านจาก DB อย่างเดียว ไม่ scrape — การ scrape เกิดที่นี่เท่านั้น

    python jobs.py food_price          # รันงานเดียว
    python jobs.py food_price --days 7 # ส่งพารามิเตอร์เพิ่ม
    python jobs.py --list              # ดูงานทั้งหมด

crontab (เวลาไทย):
    0  0 * * *  cd /path/to/ex-factor && .venv/bin/python jobs.py energy
    30 0 * * *  cd /path/to/ex-factor && .venv/bin/python jobs.py food_price
    0  1 * * 1  cd /path/to/ex-factor && .venv/bin/python jobs.py wage
"""
import sys
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import db
import modules.economic
import modules.electricity
import modules.energy
import modules.food_price
import modules.holiday
import modules.lucky_shirt
import modules.myth
import modules.wage
import modules.weather

# ชื่องาน → (ตารางปลายทาง, ฟังก์ชันที่คืนแถว)
JOBS = {
    "energy":      ("fact_daily",         modules.energy.run),
    "electricity": ("fact_daily",         modules.electricity.run),
    "economic":    ("fact_daily",         modules.economic.run),
    "myth":        ("fact_myth",          modules.myth.run),
    "lucky_shirt": ("dim_lucky_shirt",    modules.lucky_shirt.run),
    "holiday":     ("fact_event",         modules.holiday.run),
    "wage":        ("fact_minimum_wage",  modules.wage.run),
    "food_price":  ("fact_dit_price",     modules.food_price.run),
    # ponytail: weather ไม่มีงาน cron — endpoint ยิงเองตอน cache หมดอายุ
    #           เพิ่มงานอุ่น cache ทีหลังถ้า user คนแรกของวันรอนานเกินรับได้
}


# เว็บต้นทางเกือบทั้งหมดเป็นเว็บราชการไทย (MEA, EPPO, DIT, กระทรวงแรงงาน) ซึ่งล่มเป็นระยะ
# ยิงซ้ำมักผ่าน — วัดจริงแล้วครั้งที่ 1 fail ครั้งที่ 2-3 ผ่าน (ConnectionResetError)
# cron รันตี 1-4 ไม่มีคนเฝ้า ไม่มี retry = สะดุดวินาทีเดียวก็ไม่มีข้อมูลทั้งวัน
ATTEMPTS = 3
RETRY_WAIT = 30      # วินาที — เว็บล่มมักใช้เวลาฟื้นระดับนี้ ยิงรัวไม่ช่วยอะไร


def _log_run(name: str, started, status: str, attempts: int,
             rows_written: int, error: str | None) -> None:
    """บันทึกผลการรันลง job_run_log — ห้ามทำให้ job พังเพราะ log เขียนไม่ได้

    error ถูกตัดที่ 2000 ตัวอักษร เพราะ traceback บางตัวยาวเป็นหมื่น (เห็นจาก MEA ตอนล่ม)
    เก็บทั้งหมดก็อ่านไม่ไหวอยู่ดี ส่วนต้นบอกสาเหตุครบแล้ว
    """
    try:
        db.save_rows("job_run_log", [{
            "job_name": name, "started_at": started,
            "finished_at": datetime.now(timezone.utc), "status": status,
            "attempts": attempts, "rows_written": rows_written,
            "error": error[:2000] if error else None,
        }])
    except Exception as e:
        print(f"  ⚠️  เขียน job_run_log ไม่สำเร็จ: {type(e).__name__}: {e}")


def run_job(name: str, **kwargs) -> int:
    """ดึงข้อมูล + เขียนลง DB — คืนจำนวนแถว, raise ถ้าพังครบทุกครั้ง (cron จับจาก exit code)

    retry ทั้ง job ไม่ใช่ราย request เพราะแก้ที่เดียวครอบทุกโมดูล ไม่ต้องแตะ 8 ไฟล์
    แลกกับการ scrape ซ้ำตั้งแต่ต้นเมื่อพลาด — ยอมได้ เพราะเกิดเฉพาะตอน error
    และ job พวกนี้รันกลางดึกไม่มีใครรอ
    """
    if name not in JOBS:
        raise ValueError(f"ไม่รู้จักงาน '{name}' — ตัวเลือก: {', '.join(JOBS)}")

    table, fn = JOBS[name]
    started = datetime.now(timezone.utc)
    print(f"[{started.astimezone():%Y-%m-%d %H:%M:%S}] {name} → {table}")

    used = 0
    try:
        for attempt in range(1, ATTEMPTS + 1):
            used = attempt
            try:
                rows = fn(verbose=False, **kwargs)
                break
            except Exception as e:
                if attempt == ATTEMPTS:
                    raise
                print(f"  ⚠️  ครั้งที่ {attempt}/{ATTEMPTS} ล้มเหลว ({type(e).__name__}: {e}) "
                      f"— รอ {RETRY_WAIT} วิแล้วลองใหม่")
                time.sleep(RETRY_WAIT)

        written = db.save_rows(table, rows)

        # ราคาต้องเทียบกับแถวก่อนหน้าที่อยู่ใน DB — คำนวณหลังเขียนเสร็จเท่านั้น
        if name == "food_price":
            changed = db.recompute_price_change()
            print(f"  📊 อัปเดต price_change {changed} แถว")
    except Exception as e:
        # บันทึกความพังไว้ก่อนโยนต่อ — ไม่งั้นรู้แค่ว่า exit 1 แต่ไม่รู้ว่าพังเพราะอะไร
        # เขียน log ไม่สำเร็จก็ห้ามกลบ error ตัวจริง (เช่น DB ล่มจนเขียนอะไรไม่ได้เลย)
        _log_run(name, started, "failed", used, 0, f"{type(e).__name__}: {e}")
        raise

    _log_run(name, started, "success", used, written, None)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(f"  ✅ {written} แถว ({elapsed:.1f}s)")
    return written


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "--list":
        print("งานที่มี:")
        for name, (table, _) in JOBS.items():
            print(f"  {name:14} → {table}")
        sys.exit(0)

    job_name = args[0]
    kwargs = {}
    if "--days" in args:
        kwargs["days"] = int(args[args.index("--days") + 1])

    try:
        run_job(job_name, **kwargs)
    except Exception:
        # print traceback เต็มให้ cron log เก็บไว้ แล้ว exit 1 ให้ cron รู้ว่าพัง
        traceback.print_exc()
        sys.exit(1)
