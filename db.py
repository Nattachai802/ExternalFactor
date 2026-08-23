"""บันทึกแถวจากโมดูล → Postgres

สร้างตารางถ้ายังไม่มี (CREATE TABLE IF NOT EXISTS) แล้ว upsert ตาม PK ของแต่ละตาราง
(INSERT ... ON CONFLICT DO UPDATE) — เรียกซ้ำได้ทุกวันโดยไม่ซ้ำแถว

    from db import save_rows
    save_rows("fact_daily", modules.energy.run(verbose=False))

ต้องตั้ง DATABASE_URL ใน environment เช่น
    postgresql://user:pass@host:5432/exfactor
"""
import os

import psycopg
from psycopg.rows import dict_row

# ── schema ต่อ 1 ตาราง — (DDL, primary key columns) ────────────
# ลำดับคอลัมน์ของ DDL ไม่ต้องตรงกับ dict ที่ส่งเข้ามา — เขียนด้วยชื่อคอลัมน์เสมอ
TABLES: dict[str, dict] = {
    "fact_daily": {
        "pk": ["date", "metric_name"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_daily (
                date DATE NOT NULL,
                metric_name TEXT NOT NULL,
                value TEXT,
                unit TEXT,
                source TEXT,
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (date, metric_name)
            )
        """,
    },
    "fact_myth": {
        "pk": ["date", "type", "level"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_myth (
                date DATE NOT NULL,
                type TEXT NOT NULL,
                type_th TEXT,
                level TEXT NOT NULL DEFAULT '',
                value_cn TEXT,
                value_th TEXT,
                hex TEXT,
                source TEXT,
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (date, type, level)
            )
        """,
    },
    "fact_event": {
        "pk": ["event_date", "event_name"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_event (
                event_date DATE NOT NULL,
                event_name TEXT NOT NULL,
                category TEXT,
                impact_level TEXT,
                impact_description TEXT,
                is_ongoing BOOLEAN,
                alcohol_ban BOOLEAN DEFAULT false,
                source TEXT,
                news_content TEXT,
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (event_date, event_name)
            );
            ALTER TABLE fact_event ADD COLUMN IF NOT EXISTS alcohol_ban BOOLEAN DEFAULT false;
        """,
    },
    "fact_minimum_wage": {
        "pk": ["jurisdiction", "effective_date"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_minimum_wage (
                effective_date DATE NOT NULL,
                jurisdiction TEXT NOT NULL,
                wage_value NUMERIC NOT NULL,
                currency TEXT NOT NULL DEFAULT 'THB',
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (jurisdiction, effective_date)
            )
        """,
    },
    # dim ไม่ใช่ fact — ผูกกับวันในสัปดาห์ (จันทร์-อาทิตย์) ไม่ใช่วันที่จริง คงที่เหมือนกัน
    # hex เทียบเอง ไม่ใช่ค่าทางการ (ดูหมายเหตุใน modules/lucky_shirt.py) — แก้ได้จากแถวนี้ตรงๆ
    # ไม่ต้องแก้โค้ด/deploy ใหม่
    "dim_lucky_shirt": {
        "pk": ["weekday", "category", "color_th"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS dim_lucky_shirt (
                weekday TEXT NOT NULL,
                category TEXT NOT NULL,
                color_th TEXT NOT NULL,
                hex TEXT,
                source TEXT,
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (weekday, category, color_th)
            )
        """,
    },
    "fact_dit_price": {
        "pk": ["date", "protype", "product_name"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_dit_price (
                date DATE NOT NULL,
                protype TEXT NOT NULL,
                category TEXT,
                product_name TEXT NOT NULL,
                price_min NUMERIC,
                price_max NUMERIC,
                price_avg NUMERIC,
                price_change NUMERIC,
                unit TEXT,
                source TEXT,
                fetched_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (date, protype, product_name)
            );
            -- ตารางที่สร้างไว้ก่อนมีคอลัมน์นี้ ต้องเติมย้อนหลัง
            ALTER TABLE fact_dit_price ADD COLUMN IF NOT EXISTS price_change NUMERIC;
        """,
    },
    # บันทึกทุกครั้งที่ cron รัน job — ตอบคำถาม "cron ยังทำงานอยู่ไหม" ได้ด้วย SQL
    # ไม่มีตารางนี้ = job พังตอนตี 1 แล้วไม่มีใครรู้จนลูกค้าบ่นว่าข้อมูลไม่อัปเดต
    # (traceback ลง log ไฟล์ก็จริง แต่ไม่มีใครเปิดอ่านทุกวัน)
    #
    # PK มี started_at ด้วย เก็บทุกรอบเป็นประวัติ ไม่ทับของเก่า — ดูย้อนหลังได้ว่า
    # เริ่มพังตั้งแต่เมื่อไหร่ ตารางโตช้ามาก (8 job/วัน = ~3,000 แถว/ปี)
    "job_run_log": {
        "pk": ["job_name", "started_at"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS job_run_log (
                job_name TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL,
                attempts INT,
                rows_written INT,
                error TEXT,
                PRIMARY KEY (job_name, started_at)
            )
        """,
    },
    # cache ของ branch API + Nominatim — ไม่ใช่ fact เพราะไม่แปรตามวัน (สาขาไม่ย้ายที่)
    # อยู่ Postgres ไม่ใช่ sqlite เพราะหลาย instance ต้องแชร์ cache กัน ไม่งั้น
    # แต่ละเครื่อง geocode ซ้ำเอง ทั้งที่ Nominatim จำกัด 1 req/วินาที
    "dim_branch": {
        "pk": ["branch_id"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS dim_branch (
                branch_id TEXT NOT NULL,
                name TEXT,
                lat NUMERIC,
                lon NUMERIC,
                province_fallback TEXT,
                owner_id TEXT,
                brand_id TEXT,
                merchant_id TEXT,
                cached_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (branch_id)
            );
            -- ตารางที่สร้างก่อนมีคอลัมน์พวกนี้ ต้องเติมย้อนหลัง (แถวเก่าจะเป็น NULL
            -- จนกว่าจะถูก resolve ใหม่ — get_location() เช็ค owner_id ว่าง แล้วดึงซ้ำให้เอง)
            ALTER TABLE dim_branch ADD COLUMN IF NOT EXISTS owner_id TEXT;
            ALTER TABLE dim_branch ADD COLUMN IF NOT EXISTS brand_id TEXT;
            ALTER TABLE dim_branch ADD COLUMN IF NOT EXISTS merchant_id TEXT;
        """,
    },
    # คีย์เป็น grid ~11 กม. ไม่ใช่พิกัดดิบ — สาขาใกล้กันใช้ผล geocode ร่วมกัน
    "dim_grid_area": {
        "pk": ["grid_lat", "grid_lon"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS dim_grid_area (
                grid_lat NUMERIC(4,1) NOT NULL,
                grid_lon NUMERIC(5,1) NOT NULL,
                province TEXT,
                district TEXT,
                cached_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (grid_lat, grid_lon)
            )
        """,
    },
    # ภัยพิบัติใกล้สาขา — TTL 30 นาที (สั้นสุดในระบบ)
    #
    # snapshot_at อยู่ใน PK เพราะจำนวนเหตุการณ์ต่อเขต "ผันแปร" ไม่เหมือน weather ที่มี 20 ชม.เสมอ
    # การที่ภัยหายไปคือสัญญาณเอง (= ปลอดภัย) แต่ upsert ลบแถวไม่ได้ ถ้าไม่มี snapshot
    # แถวน้ำท่วมเก่าจะค้างตลอดกาล ทำให้ has_alert() คืน True ทั้งที่น้ำลดไปแล้ว
    # อ่านด้วย latest_snapshot() → เห็นเฉพาะรอบล่าสุด ส่วนรอบเก่าเก็บไว้เป็นประวัติ
    #
    # detail อยู่ใน PK ด้วยเพราะ 1 เขต 1 รอบ มีได้หลายเหตุการณ์ (น้ำท่วมหลายตำบล + ไฟป่า)
    # เขตที่ปลอดภัยเก็บ 1 แถว kind='' ไว้ ไม่งั้นแยกไม่ออกจาก "ยังไม่เคยเช็ค"
    "fact_disaster": {
        "pk": ["province", "district", "snapshot_at", "kind", "detail"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_disaster (
                province TEXT NOT NULL,
                district TEXT NOT NULL,
                snapshot_at TIMESTAMPTZ NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                level TEXT,
                detail TEXT NOT NULL DEFAULT '',
                event_province TEXT,
                event_district TEXT,
                source TEXT,
                ref_lat NUMERIC,
                ref_lon NUMERIC,
                fetch_error TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (province, district, snapshot_at, kind, detail)
            )
        """,
    },
    # ts ตรงกับ fact_weather_hourly เป๊ะ (ยิงจริงเทียบแล้ว) — PK รูปแบบเดียวกัน join ได้ตรงๆ
    "fact_air_quality_hourly": {
        "pk": ["province", "district", "ts"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_air_quality_hourly (
                province TEXT NOT NULL,
                district TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                aqi_us INT,
                pm2_5 NUMERIC,
                pm10 NUMERIC,
                co NUMERIC,
                no NUMERIC,
                no2 NUMERIC,
                o3 NUMERIC,
                so2 NUMERIC,
                nh3 NUMERIC,
                ref_lat NUMERIC,
                ref_lon NUMERIC,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (province, district, ts)
            )
        """,
    },
    # อากาศคีย์ด้วย (จังหวัด, เขต/อำเภอ) ไม่ใช่พิกัด — สาขาในเขตเดียวกันแชร์แถวเดียวกัน
    # ref_lat/ref_lon คือพิกัดที่ใช้ยิง API จริง เก็บไว้ debug ว่าแถวนี้มาจากจุดไหนของเขต
    # แทน fact_forecast เดิม (grid พิกัด) ซึ่งเลิกใช้แล้ว — ตารางเก่ายังอยู่ใน DB แต่ไม่มีอะไรเขียน
    # เก็บทุกฟิลด์ที่ OWM ส่งมา ไม่ตัดทิ้ง — ยิงมาฟรีในก้อนเดียวกันอยู่แล้ว เผื่อใช้ทีหลัง
    "fact_weather_hourly": {
        "pk": ["province", "district", "ts"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_weather_hourly (
                province TEXT NOT NULL,
                district TEXT NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                condition TEXT,
                weather_main TEXT,
                weather_id INT,
                weather_icon TEXT,
                temp NUMERIC,
                feels_like NUMERIC,
                pressure NUMERIC,
                humidity NUMERIC,
                dew_point NUMERIC,
                uvi NUMERIC,
                clouds NUMERIC,
                visibility NUMERIC,
                wind_speed NUMERIC,
                wind_deg NUMERIC,
                wind_gust NUMERIC,
                pop INT,
                rain_mm NUMERIC,
                ref_lat NUMERIC,
                ref_lon NUMERIC,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (province, district, ts)
            )
        """,
    },
    "fact_weather_daily": {
        "pk": ["province", "district", "target_date"],
        "ddl": """
            CREATE TABLE IF NOT EXISTS fact_weather_daily (
                province TEXT NOT NULL,
                district TEXT NOT NULL,
                target_date DATE NOT NULL,
                condition TEXT,
                weather_main TEXT,
                weather_id INT,
                weather_icon TEXT,
                temp_day NUMERIC,
                temp_max NUMERIC,
                temp_min NUMERIC,
                temp_night NUMERIC,
                temp_eve NUMERIC,
                temp_morn NUMERIC,
                feels_like_day NUMERIC,
                feels_like_night NUMERIC,
                feels_like_eve NUMERIC,
                feels_like_morn NUMERIC,
                pressure NUMERIC,
                humidity NUMERIC,
                wind_speed NUMERIC,
                wind_deg NUMERIC,
                clouds NUMERIC,
                uvi NUMERIC,
                sunrise TIMESTAMPTZ,
                sunset TIMESTAMPTZ,
                moonrise TIMESTAMPTZ,
                moonset TIMESTAMPTZ,
                moon_phase NUMERIC,
                pop INT,
                rain_mm NUMERIC,
                ref_lat NUMERIC,
                ref_lon NUMERIC,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (province, district, target_date)
            )
        """,
    },
    # ponytail: job_run_log/dim_calendar ยังพักไว้ — เพิ่มทีหลังตอนตกลง scope ชัดแล้ว
}


def _connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("ไม่พบ DATABASE_URL ใน environment")
    return psycopg.connect(url, row_factory=dict_row)


def ensure_table(conn, table: str) -> None:
    """สร้างตารางถ้ายังไม่มี — เรียกซ้ำได้ ไม่มีผลถ้ามีอยู่แล้ว"""
    if table not in TABLES:
        raise ValueError(f"ไม่รู้จักตาราง '{table}' — ตัวเลือก: {list(TABLES)}")
    conn.execute(TABLES[table]["ddl"])


def upsert(conn, table: str, rows: list[dict]) -> int:
    """เขียนแถวลงตาราง — สร้างตารางก่อนถ้ายังไม่มี แล้ว upsert ตาม PK

    คืนจำนวนแถวที่เขียน — [] ไม่ทำอะไรเลย ไม่ต้องเปิด connection ก็ได้ (ผู้เรียกเช็คก่อนได้)
    ถ้าแถวมี key ไม่ตรงกันเอง (module คืน dict ไม่ครบคอลัมน์เดียวกัน) จะ error ทันที
    ไม่ silent — เพื่อจับ bug ตั้งแต่ dev ไม่ใช่ตอนข้อมูลหายไปเงียบๆ ใน prod
    """
    if not rows:
        return 0

    ensure_table(conn, table)
    pk = TABLES[table]["pk"]
    columns = list(rows[0].keys())

    col_list = ", ".join(columns)
    placeholders = ", ".join(f"%({c})s" for c in columns)
    update_cols = [c for c in columns if c not in pk]
    conflict_clause = (
        f"DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in update_cols)}"
        if update_cols else "DO NOTHING"
    )
    sql = f"""
        INSERT INTO {table} ({col_list}) VALUES ({placeholders})
        ON CONFLICT ({', '.join(pk)}) {conflict_clause}
    """

    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def query(sql: str, params: tuple = ()) -> list[dict]:
    """SELECT ดิบ — สำหรับ lookup ที่ไม่ได้อิงวันที่ (เช่น cache ของ branch/grid)"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def latest_snapshot(table: str, date_col: str, target_date=None,
                     where: str = "", params: tuple = ()) -> tuple[list[dict], str | None]:
    """แถวของ snapshot ล่าสุดที่ <= target_date (หรือล่าสุดสุดถ้าไม่ระบุ target_date)

    คือ fallback ที่ API ใช้: ถ้าวันที่ขอมาไม่มีข้อมูล (เช่น cron ยังไม่รันวันนั้น)
    ถอยไปหาวันล่าสุดที่มีข้อมูลจริงแทน — ไม่ scrape สด ไม่มีข้อมูลก็คือไม่มี ([], None)

    where/params: เงื่อนไขเสริม เช่น "source LIKE %s" กรองแถวในตารางที่ใช้ร่วมกันหลายโมดูล
    """
    extra = f" AND {where}" if where else ""
    with _connect() as conn, conn.cursor() as cur:
        if target_date:
            cur.execute(f"SELECT max({date_col}) AS d FROM {table} WHERE {date_col} <= %s{extra}",
                       (target_date, *params))
        else:
            cur.execute(f"SELECT max({date_col}) AS d FROM {table} WHERE true{extra}", params)
        found = cur.fetchone()
        actual_date = found["d"] if found else None
        if not actual_date:
            return [], None

        cur.execute(f"SELECT * FROM {table} WHERE {date_col} = %s{extra}", (actual_date, *params))
        rows = cur.fetchall()
        return rows, actual_date.isoformat()


def max_date(table: str, date_col: str, on_or_before=None,
             where: str = "", params: tuple = ()):
    """วันล่าสุดที่มีข้อมูลจริง (จำกัดไม่เกิน on_or_before ถ้าระบุ) — None ถ้าไม่มีเลย"""
    conditions, values = [], []
    if on_or_before:
        conditions.append(f"{date_col} <= %s")
        values.append(on_or_before)
    if where:
        conditions.append(where)
        values.extend(params)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT max({date_col}) AS d FROM {table} {where_clause}", tuple(values))
        found = cur.fetchone()
        return found["d"] if found else None


def rows_between(table: str, date_col: str, start, end,
                 where: str = "", params: tuple = (), order_by: str = "") -> list[dict]:
    """แถวทั้งหมดในช่วง [start, end] — ใช้ตอนขอข้อมูลย้อนหลังหลายวัน"""
    conditions = [f"{date_col} BETWEEN %s AND %s"]
    values = [start, end]
    if where:
        conditions.append(where)
        values.extend(params)

    sql = f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}"
    if order_by:
        sql += f" ORDER BY {order_by}"

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(values))
        return cur.fetchall()


def latest_per_key(table: str, date_col: str, key_cols: list[str],
                    target_date=None, where: str = "", params: tuple = ()) -> list[dict]:
    """แถวล่าสุดของ "แต่ละ key" ที่ <= target_date — fallback รายรายการ ไม่ใช่ทั้งก้อน

    ต่างจาก latest_snapshot() ตรงที่ถอยหลังแยกกันต่อ key: สินค้า A มีราคาวันศุกร์
    สินค้า B มีวันพฤหัส ก็ได้ของใครของมัน ไม่ต้องรอให้ทั้งตารางมีวันเดียวกัน
    (ข้อมูล DIT วันเสาร์อาทิตย์/วันหยุดไม่มีประกาศ และแต่ละสินค้าขาดคนละวัน)

    ใช้ DISTINCT ON ของ Postgres — เร็วกว่า window function สำหรับ "เอาแถวแรกของแต่ละกลุ่ม"
    """
    keys = ", ".join(key_cols)
    conditions = [f"{date_col} <= %s"] if target_date else []
    values = [target_date] if target_date else []
    if where:
        conditions.append(where)
        values.extend(params)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = f"""
        SELECT DISTINCT ON ({keys}) * FROM {table}
        {where_clause}
        ORDER BY {keys}, {date_col} DESC
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(values))
        return cur.fetchall()


def recompute_price_change() -> int:
    """เติมคอลัมน์ price_change ใน fact_dit_price — ส่วนต่างราคาเทียบ "แถวก่อนหน้าของสินค้านั้น"

    บวก = ราคาขึ้น, ลบ = ราคาลง, 0 = เท่าเดิม, NULL = ไม่มีข้อมูลก่อนหน้าให้เทียบ

    ใช้ LAG แทนการลบวันที่ตรงๆ เพราะ DIT ไม่ประกาศราคาวันเสาร์อาทิตย์/วันหยุด —
    "วันก่อนหน้า" ต้องหมายถึงวันที่มีราคาจริงก่อนหน้า ไม่ใช่ date - 1
    คำนวณที่ SQL ไม่ใช่ในโมดูล เพราะ cron รายวันได้ข้อมูลมาแค่วันเดียว
    ไม่มีวันก่อนหน้าอยู่ใน batch ให้เทียบ ต้องอาศัยของที่อยู่ใน DB แล้ว
    """
    sql = """
        WITH deltas AS (
            SELECT date, protype, product_name,
                   price_avg - LAG(price_avg) OVER (
                       PARTITION BY protype, product_name ORDER BY date
                   ) AS chg
            FROM fact_dit_price
        )
        UPDATE fact_dit_price f SET price_change = d.chg
        FROM deltas d
        WHERE f.date = d.date AND f.protype = d.protype AND f.product_name = d.product_name
          AND f.price_change IS DISTINCT FROM d.chg
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        return cur.rowcount


def save_rows(table: str, rows: list[dict]) -> int:
    """เปิด connection ใหม่ + เขียน + ปิด — ใช้จาก cron script/endpoint ทีละครั้ง"""
    if not rows:
        return 0
    with _connect() as conn:
        return upsert(conn, table, rows)


def ensure_all_tables() -> None:
    """สร้างทุกตารางที่รู้จัก — เรียกครั้งเดียวตอน deploy/first run"""
    with _connect() as conn:
        for table in TABLES:
            ensure_table(conn, table)
        conn.commit()


if __name__ == "__main__":
    # self-check โครง SQL ที่ generate — ไม่ต่อ DB จริง
    fake_rows = [{"date": "2026-08-20", "metric_name": "gasohol 95",
                  "value": "37.69", "unit": "Baht/Liter", "source": "Kapook"}]

    class _FakeCursor:
        sql_seen = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def executemany(self, sql, rows):
            _FakeCursor.sql_seen = sql

    class _FakeConn:
        def cursor(self): return _FakeCursor()
        def execute(self, sql): pass
        def commit(self): pass

    upsert(_FakeConn(), "fact_daily", fake_rows)
    sql = _FakeCursor.sql_seen
    assert "INSERT INTO fact_daily" in sql
    assert "ON CONFLICT (date, metric_name)" in sql
    assert "value = EXCLUDED.value" in sql
    assert "date = EXCLUDED.date" not in sql, "คอลัมน์ที่เป็น PK ต้องไม่อยู่ใน SET"

    try:
        upsert(_FakeConn(), "ไม่มีตารางนี้", fake_rows)
    except ValueError:
        pass
    else:
        raise AssertionError("ตารางที่ไม่รู้จักต้อง raise")

    assert upsert(_FakeConn(), "fact_daily", []) == 0, "แถวว่างต้องไม่พัง"

    print("✅ ผ่าน — สร้าง SQL upsert ถูกต้อง, กันตารางไม่รู้จัก, กันแถวว่าง")
