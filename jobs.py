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
import traceback
from datetime import datetime

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


def run_job(name: str, **kwargs) -> int:
    """ดึงข้อมูล + เขียนลง DB — คืนจำนวนแถว, raise ถ้าพัง (cron จะจับจาก exit code)"""
    if name not in JOBS:
        raise ValueError(f"ไม่รู้จักงาน '{name}' — ตัวเลือก: {', '.join(JOBS)}")

    table, fn = JOBS[name]
    started = datetime.now()
    print(f"[{started:%Y-%m-%d %H:%M:%S}] {name} → {table}")

    rows = fn(verbose=False, **kwargs)
    written = db.save_rows(table, rows)

    # ราคาต้องเทียบกับแถวก่อนหน้าที่อยู่ใน DB — คำนวณหลังเขียนเสร็จเท่านั้น
    if name == "food_price":
        changed = db.recompute_price_change()
        print(f"  📊 อัปเดต price_change {changed} แถว")

    elapsed = (datetime.now() - started).total_seconds()
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
