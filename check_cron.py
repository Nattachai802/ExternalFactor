"""ตรวจสอบ crontab.txt ก่อนติดตั้งจริง — ไม่ต้องรอให้ถึงเวลาจริง

ปัญหาคลาสสิกของ cron คือ "รันมือได้ แต่พอ cron รันแล้วพังเงียบ" เพราะ cron ให้ environment
ที่ต่างจาก shell ปกติมาก (ไม่มี PATH เต็ม ไม่มี virtualenv activate ไม่มี cwd)
สคริปต์นี้จำลองสภาพนั้นให้เจอปัญหาตั้งแต่ก่อน deploy

    python check_cron.py                      # ตรวจ crontab.txt (เครื่อง dev)
    python check_cron.py crontab.ec2.txt      # ตรวจไฟล์อื่น เช่นของ EC2
    python check_cron.py --run                # รันทุก job จริงด้วย environment เปล่าแบบ cron
    python check_cron.py --run energy myth    # รันเฉพาะ job ที่ระบุ

⚠️ --run เขียนลง DB จริง (upsert ทับตาม PK ไม่สร้างแถวซ้ำ) — รันบนเครื่อง dev เท่านั้น
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

TH_TZ = timezone(timedelta(hours=7))
CRONTAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crontab.txt")

FIELD_RANGE = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]   # นาที ชม. วันที่ เดือน วันในสัปดาห์
FIELD_NAME = ["นาที", "ชั่วโมง", "วันที่", "เดือน", "วันในสัปดาห์"]


def parse_field(raw: str, lo: int, hi: int) -> set[int]:
    """แปลง field เดียวของ cron → เซตของค่าที่ตรงเงื่อนไข

    รองรับ * , */n , a-b , a-b/n , a,b,c และตัวเลขเดี่ยว
    ค่าที่หลุดช่วงถือเป็น error ไม่ใช่ปัดเข้าช่วงเงียบๆ — cron จริงก็ปฏิเสธเหมือนกัน
    """
    out: set[int] = set()
    for part in raw.split(","):
        step = 1
        if "/" in part:
            part, step_raw = part.split("/", 1)
            step = int(step_raw)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError(f"ค่า '{part}' อยู่นอกช่วง {lo}-{hi}")
        out.update(range(start, end + 1, step))
    return out


def parse_crontab(path: str) -> tuple[list[dict], dict]:
    """คืน (รายการ job, ตัวแปร env ที่ตั้งไว้หัวไฟล์)"""
    jobs, env = [], {}
    for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^[A-Z_]+=", line):
            k, v = line.split("=", 1)
            env[k] = v
            continue

        parts = line.split(None, 5)
        if len(parts) < 6:
            jobs.append({"lineno": lineno, "raw": line, "error": "ไม่ครบ 5 field เวลา + คำสั่ง"})
            continue

        schedule_raw, command = parts[:5], parts[5]
        try:
            sets = [parse_field(f, *FIELD_RANGE[i]) for i, f in enumerate(schedule_raw)]
        except ValueError as e:
            jobs.append({"lineno": lineno, "raw": line, "error": str(e)})
            continue

        jobs.append({"lineno": lineno, "raw": line, "schedule": schedule_raw,
                     "sets": sets, "command": command, "error": None})
    return jobs, env


def matches(sets: list[set[int]], dt: datetime) -> bool:
    """เวลานี้เข้าเงื่อนไขไหม

    กฎพิเศษของ cron: ถ้า "วันที่" กับ "วันในสัปดาห์" ถูกจำกัดทั้งคู่ (ไม่ใช่ *) จะเป็น OR ไม่ใช่ AND
    — จุดนี้คนพลาดกันบ่อย เขียน 0 0 1 * 1 นึกว่า "วันที่ 1 ที่เป็นวันจันทร์" แต่จริงคือ
    "ทุกวันที่ 1 หรือทุกวันจันทร์"
    """
    minute, hour, dom, mon, dow = sets
    if dt.minute not in minute or dt.hour not in hour or dt.month not in mon:
        return False

    cron_dow = dt.isoweekday() % 7          # cron: อาทิตย์=0, จันทร์=1 ... เสาร์=6
    dow_ok = cron_dow in dow or (7 in dow and cron_dow == 0)
    dom_ok = dt.day in dom

    dom_restricted = dom != set(range(1, 32))
    dow_restricted = dow != set(range(0, 8))
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def next_fires(sets: list[set[int]], start: datetime, count: int = 3,
               horizon_days: int = 400) -> list[datetime]:
    """เวลาที่จะรันครั้งถัดไป — เดินทีละนาทีจนครบ count หรือหมด horizon

    ไม่ใช้ croniter เพื่อไม่เพิ่ม dependency ให้แค่ตรวจสอบ — เดินนาทีละก้าวก็เร็วพอ
    (400 วัน ≈ 576,000 ก้าว ใช้เวลาไม่ถึงวินาที)
    """
    cur = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(days=horizon_days)
    found = []
    while cur < end and len(found) < count:
        if matches(sets, cur):
            found.append(cur)
        cur += timedelta(minutes=1)
    return found


def check_command(command: str) -> list[str]:
    """ตรวจว่าคำสั่งใช้ได้จริงไหม — path มีอยู่, python มีอยู่, ชื่อ job ถูกต้อง"""
    problems = []

    cd_match = re.search(r"cd\s+(\S+)", command)
    if not cd_match:
        problems.append("ไม่มี cd นำหน้า — cron รันจาก home ไม่ใช่โฟลเดอร์ project จะหา .env ไม่เจอ")
    elif not os.path.isdir(cd_match.group(1)):
        problems.append(f"โฟลเดอร์ไม่มีอยู่จริง: {cd_match.group(1)}")

    py_match = re.search(r"(\S*python\S*)\s+jobs\.py", command)
    if py_match and cd_match:
        py_path = py_match.group(1)
        full = py_path if os.path.isabs(py_path) else os.path.join(cd_match.group(1), py_path)
        if not os.path.exists(full):
            problems.append(f"ไม่พบ python ที่ใช้: {full}")

    job_match = re.search(r"jobs\.py\s+(\w+)", command)
    if job_match:
        try:
            sys.path.insert(0, os.path.dirname(CRONTAB))
            import jobs
            if job_match.group(1) not in jobs.JOBS:
                problems.append(f"ไม่รู้จัก job '{job_match.group(1)}' — มี: {', '.join(jobs.JOBS)}")
        except Exception as e:
            problems.append(f"import jobs.py ไม่ได้: {e}")

    log_match = re.search(r">>\s*(\S+)", command)
    if log_match and cd_match:
        log_dir = os.path.dirname(os.path.join(cd_match.group(1), log_match.group(1)))
        if log_dir and not os.path.isdir(log_dir):
            problems.append(f"โฟลเดอร์ log ไม่มีอยู่ — cron จะเขียน log ไม่ได้: {log_dir}")

    return problems


def run_isolated(command: str, env_vars: dict) -> tuple[int, str]:
    """รันคำสั่งด้วย environment เปล่าแบบที่ cron ให้ — จับปัญหา PATH/cwd/env ที่ shell ปกติมองไม่เห็น

    cron ให้ env แค่ไม่กี่ตัว (HOME, LOGNAME, PATH สั้นๆ, SHELL) ไม่มี virtualenv activate
    ไม่มี env ที่ตั้งใน .zshrc/.bash_profile — จำลองด้วย env -i แล้วใส่เฉพาะที่ crontab ตั้งไว้เอง
    """
    clean = {"HOME": os.path.expanduser("~"), "LOGNAME": os.environ.get("USER", ""),
             "USER": os.environ.get("USER", ""), **env_vars}
    clean.setdefault("PATH", "/usr/bin:/bin")

    proc = subprocess.run(["/bin/sh", "-c", command], env=clean, cwd="/",
                          capture_output=True, text=True, timeout=600)
    return proc.returncode, (proc.stdout + proc.stderr)


def main() -> int:
    global CRONTAB
    args = sys.argv[1:]
    do_run = "--run" in args
    positional = [a for a in args if not a.startswith("--")]

    # arg แรกที่ลงท้าย .txt ถือเป็นไฟล์ crontab ที่เหลือคือชื่อ job ที่จะรัน
    if positional and positional[0].endswith(".txt"):
        CRONTAB = os.path.abspath(positional.pop(0))
    only = positional

    if not os.path.exists(CRONTAB):
        print(f"❌ ไม่พบไฟล์: {CRONTAB}")
        return 1

    jobs, env_vars = parse_crontab(CRONTAB)
    now = datetime.now(TH_TZ)

    print(f"ตรวจ {CRONTAB}")
    print(f"เวลาเครื่องตอนนี้: {now:%Y-%m-%d %H:%M} (TZ={now.tzname()})")
    if env_vars:
        print(f"env ที่ตั้งไว้: {env_vars}")
    print()

    failed = 0
    for job in jobs:
        name_match = re.search(r"jobs\.py\s+(\w+)", job.get("command", ""))
        name = name_match.group(1) if name_match else f"บรรทัด {job['lineno']}"

        if job.get("error"):
            print(f"❌ บรรทัด {job['lineno']}: {job['error']}")
            print(f"   {job['raw']}")
            failed += 1
            continue

        problems = check_command(job["command"])
        fires = next_fires(job["sets"], now)
        if not fires:
            # เช่น 31 ก.พ. — syntax ถูกทุกอย่างแต่ไม่มีวันเกิดขึ้นจริง cron จะเงียบสนิทตลอดไป
            problems.append("ตารางนี้ไม่มีวันรันเลยใน 400 วัน — เงื่อนไขวัน/เดือนเป็นไปไม่ได้")
        when = ", ".join(f"{f:%a %d %b %H:%M}" for f in fires) or "ไม่มีเลย"

        icon = "❌" if problems else "✅"
        print(f"{icon} {name:14} [{' '.join(job['schedule'])}]")
        print(f"   รอบถัดไป: {when}")
        for p in problems:
            print(f"   ⚠️  {p}")
            failed += 1
        print()

    if do_run:
        print("=" * 60)
        print("รันจริงด้วย environment เปล่าแบบ cron")
        print("=" * 60)
        for job in jobs:
            if job.get("error"):
                continue
            name_match = re.search(r"jobs\.py\s+(\w+)", job["command"])
            name = name_match.group(1) if name_match else "?"
            if only and name not in only:
                continue

            print(f"\n▶ {name}")
            try:
                code, output = run_isolated(job["command"], env_vars)
            except subprocess.TimeoutExpired:
                print("   ❌ timeout เกิน 10 นาที")
                failed += 1
                continue

            tail = "\n".join(output.strip().splitlines()[-6:])
            if code == 0:
                print(f"   ✅ exit 0\n   {tail.replace(chr(10), chr(10) + '   ')}")
            else:
                print(f"   ❌ exit {code}\n   {tail.replace(chr(10), chr(10) + '   ')}")
                failed += 1

    print()
    print("=" * 60)
    print(f"{'❌ พบปัญหา ' + str(failed) + ' จุด' if failed else '✅ ผ่านทั้งหมด'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
