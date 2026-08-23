# exFactor API — เฉพาะไฟล์ที่ใช้งานจริง ไม่ก็อบทั้ง repo
# repo มี notebook/สคริปต์ทดลองเก่าปนอยู่เยอะ (analysis_pipeline.py, *.ipynb, noted/, scratch/ ฯลฯ)
# ที่ endpoint/cron ไม่ได้เรียกเลย — COPY ทีละไฟล์แทน `COPY . .` กันเอาของที่ไม่ใช้ติดเข้ามา
FROM python:3.13-slim

WORKDIR /app

# แยก layer requirements ออกจาก code — code เปลี่ยนบ่อยกว่า dependency มาก
# แก้แค่ app_v1.py ก็ไม่ต้องโหลด pip ใหม่ทุกครั้ง
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# เฉพาะไฟล์ที่ import จริงใน app_v1.py / jobs.py (เช็คจาก grep import แล้ว)
COPY app_v1.py db.py jobs.py check_cron.py crontab.txt ./
COPY modules ./modules

# รันด้วย user ที่ไม่ใช่ root — psycopg[binary]/pip ไม่ต้องการสิทธิ์ root ตอนรัน
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# healthcheck ด้วย python เฉยๆ ไม่ต้องลง curl เพิ่ม (image slim ไม่มี curl ติดมาด้วย)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# jobs.py ไม่ได้รันในนี้ — ตั้ง cron ฝั่ง host แล้วยิงเข้ามาด้วย:
#     docker exec <container> python jobs.py energy
# (ดู crontab.txt สำหรับตารางเวลาเต็ม)
CMD ["uvicorn", "app_v1:app", "--host", "0.0.0.0", "--port", "8000"]
