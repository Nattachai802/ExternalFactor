import re
from datetime import date

import requests
from bs4 import BeautifulSoup

from modules import pttor

KAPOOK_URL = "https://gasprice.kapook.com/gasprice.php"
EPPO_URL = "https://www.eppo.go.th/wp-json/oil-api/v1/lpg-prices"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.eppo.go.th/",
}

FUEL_NAME_MAP = {
    "แก๊สโซฮอล์ 95": "gasohol 95",
    "แก๊สโซฮอล์ E20": "gasohol E20",
    "แก๊สโซฮอล์ E85": "gasohol E85",
    "แก๊สโซฮอล์ 91": "gasohol 91",
    "เบนซิน 95": "benzin 95",
    "แก๊ส NGV": "NGV gas",
    "ดีเซลพรีเมียม": "diesel premium",
    "ดีเซล": "diesel",
    "ดีเซล B7": "diesel",
    "ดีเซล B20": "diesel B20",
    "แก๊สโซฮอล์ 95 พรีเมียม": "superpower gasoline 95",
}

LPG_SUFFIX_MAP = {
    "litre": ("LPG per Liter", "Baht/Liter"),
    "4kg": ("LPG barrel 4KG", "Baht/Tank"),
    "15kg": ("LPG barrel 15KG", "Baht/Tank"),
}


def _midpoint(raw) -> float | None:
    nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.?\d*", str(raw))]
    return sum(nums) / len(nums) if nums else None


def parse_kapook(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", id="brand-ptt")
    if not section:
        raise ValueError("ไม่พบ section#brand-ptt — โครงเว็บ Kapook อาจเปลี่ยน")

    out = []
    for li in section.find_all("li"):
        cells = li.find_all("p")
        if len(cells) < 2:
            continue
        name_th = cells[0].get_text(strip=True)
        if name_th not in FUEL_NAME_MAP:
            continue
        try:
            price = float(cells[1].get_text(strip=True))
        except ValueError:
            continue
        out.append({
            "metric": FUEL_NAME_MAP[name_th],
            "value": price,
            "unit": "Baht/kg" if "NGV" in name_th else "Baht/Liter",
        })
    return out


def parse_eppo(payload: dict) -> tuple[list[dict], str, str]:
    """เลือกยี่ห้อที่ปรับราคาล่าสุด — คืน (rows, brand, effective_date)

    แต่ละยี่ห้อมีวันปรับราคาไม่เท่ากัน (PTT ค้างที่ปี 2023) จึงต้องเลือกใหม่สุด
    """
    brands = payload.get("data", {})
    if not brands:
        return [], "", ""

    brand, prices = max(
        brands.items(),
        key=lambda kv: kv[1].get(f"lpg_{kv[0]}_date", ""),
    )
    eff_date = prices.get(f"lpg_{brand}_date", "")

    rows = []
    for suffix, (metric, unit) in LPG_SUFFIX_MAP.items():
        val = _midpoint(prices.get(f"lpg_{brand}_{suffix}"))
        if val is not None:
            rows.append({"metric": metric, "value": val, "unit": unit})
    return rows, brand, eff_date


def run(target_date: date | None = None, verbose: bool = True) -> list[dict]:
    """คืนแถว fact_daily ของราคาน้ำมัน + LPG

    ราคาเป็นค่า ณ วันที่ดึง — เว็บต้นทางไม่ระบุวันที่ของราคาน้ำมัน
    """
    d = (target_date or date.today()).isoformat()
    rows = []

    if verbose:
        print("\n" + "=" * 50)
        print("⛽  MODULE 2: Energy & Fuel Prices")
        print("=" * 50)

    # ── น้ำมัน (Kapook / ปตท.) ────────────────────────────────
    try:
        res = requests.get(KAPOOK_URL, timeout=15)
        res.encoding = "utf-8"
        fuels = parse_kapook(res.text)
        rows += [{"date": d, "metric_name": r["metric"], "value": str(r["value"]),
                  "unit": r["unit"], "source": "Kapook (PTT)"} for r in fuels]
        if verbose:
            print(f"  ✅ Kapook (PTT): {len(fuels)} ชนิด")
    except Exception as e:
        print(f"  ❌ Kapook: {e}")

    # ── LPG (EPPO) ───────────────────────────────────────────
    try:
        res = requests.get(EPPO_URL, headers=UA, timeout=15)
        res.raise_for_status()
        lpg, brand, eff = parse_eppo(res.json())
        source = f"EPPO ({brand.upper()} {eff})" if brand else "EPPO"
        rows += [{"date": d, "metric_name": r["metric"], "value": str(round(r["value"], 3)),
                  "unit": r["unit"], "source": source} for r in lpg]
        if verbose:
            print(f"  ✅ EPPO LPG: {len(lpg)} รายการ — brand={brand.upper()} effective={eff}")
    except Exception as e:
        print(f"  ❌ EPPO: {e}")

    if verbose:
        for r in rows:
            print(f"  {r['metric_name']:24} {r['value']:>10} {r['unit']:<12} [{r['source']}]")
        print(f"  📊 รวม {len(rows)} แถวใน fact_daily")
    return rows


METRIC_TH = {
    "gasohol 95": "แก๊สโซฮอล์ 95", "gasohol E20": "แก๊สโซฮอล์ E20",
    "gasohol E85": "แก๊สโซฮอล์ E85", "gasohol 91": "แก๊สโซฮอล์ 91",
    "benzin 95": "เบนซิน 95", "superpower gasoline 95": "แก๊สโซฮอล์ 95 พรีเมียม",
    "diesel": "ดีเซล", "diesel premium": "ดีเซลพรีเมียม", "diesel B20": "ดีเซล B20",
    "NGV gas": "NGV", "LPG per Liter": "LPG (ต่อลิตร)",
    "LPG barrel 4KG": "ถังแก๊ส 4 กก.", "LPG barrel 15KG": "ถังแก๊ส 15 กก.",
}


def _trend(change: float | None) -> str | None:
    if change is None:
        return None
    return "ขึ้น" if change > 0 else "ลง" if change < 0 else "เท่าเดิม"


def fallback_prev_day(target_date: date) -> list[dict]:
    """ไม่มี "เมื่อวาน" ใน DB เลย (deploy วันแรก / มี gap) → ยิง PTTOR ตรงแทนการปล่อย None ทิ้ง

    ใช้เฉพาะตอน DB ไม่มีข้อมูลจริงๆ ไม่ใช่ path ปกติ — ทางหลักยังอ่านจาก fact_daily เสมอ
    exception ปล่อยหลุดไปให้ผู้เรียกตัดสินใจเอง (เช่นเงียบแล้วได้ None เหมือนเดิม)
    """
    return pttor.fetch_prices(target_date)


def format_rows(rows: list[dict], as_of: str, prev_rows: list[dict] | None = None) -> dict:
    """แปลงแถว fact_daily (metric_name/value/unit/source) → record ภาษาไทย

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)

    prev_rows = แถวของ "วันก่อนหน้าที่มีข้อมูลจริง" (ไม่ใช่ as_of - 1 วันเป๊ะ เผื่อวันหยุด/ยังไม่ scrape)
    ไม่ precompute ด้วย cron แบบ food_price เพราะ energy มีแค่ ~12 แถว/วัน คำนวณสดถูกกว่า
    """
    prev_value = {r["metric_name"]: float(r["value"]) for r in (prev_rows or [])}

    fuel, gas = {}, {}
    for r in rows:
        target = gas if "LPG" in r["metric_name"] else fuel
        price = float(r["value"])
        prev = prev_value.get(r["metric_name"])
        change = round(price - prev, 4) if prev is not None else None
        target[METRIC_TH.get(r["metric_name"], r["metric_name"])] = {
            "ราคา": price, "หน่วย": r["unit"],
            "ส่วนต่างราคา": change, "แนวโน้ม": _trend(change),
        }

    return {
        "วันที่": as_of,
        "ราคาน้ำมัน": fuel,
        "ราคาแก๊สหุงต้ม": gas,
        "แหล่งข้อมูล": sorted({r["source"] for r in rows}),
    }


def summary(target_date: date | None = None) -> dict:
    """ราคาน้ำมัน/แก๊ส — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)"""
    d = target_date or date.today()
    return format_rows(run(d, verbose=False), d.isoformat())


def demo():
    SAMPLE_HTML = """
    <section id="brand-ptt"><ul>
      <li><p>แก๊สโซฮอล์ 95</p><p>37.69</p></li>
      <li><p>ดีเซล B7</p><p>38.39</p></li>
      <li><p>แก๊ส NGV</p><p>20.00</p></li>
      <li><p>ไม่รู้จัก</p><p>99.99</p></li>
      <li><p>เบนซิน 95</p><p>ราคาพัง</p></li>
    </ul></section>
    """
    fuels = parse_kapook(SAMPLE_HTML)
    assert len(fuels) == 3, f"ต้องได้ 3 (ข้ามชื่อไม่รู้จัก + ราคาอ่านไม่ออก) ได้ {len(fuels)}"
    assert {f["metric"] for f in fuels} == {"gasohol 95", "diesel", "NGV gas"}
    ngv = next(f for f in fuels if f["metric"] == "NGV gas")
    assert ngv["unit"] == "Baht/kg", "NGV ต้องเป็น Baht/kg ไม่ใช่ Baht/Liter"

    try:
        parse_kapook("<p>ไม่มี section</p>")
    except ValueError:
        pass
    else:
        raise AssertionError("เว็บเปลี่ยนโครงต้อง raise ไม่ใช่คืนลิสต์ว่างเงียบๆ")

    payload = {"data": {
        "ptt": {"lpg_ptt_date": "2023-01-01", "lpg_ptt_litre": "10.00-11.00",
                "lpg_ptt_4kg": "100-110", "lpg_ptt_15kg": "400-410"},
        "unique": {"lpg_unique_date": "2025-10-08", "lpg_unique_litre": "14.74-15.96",
                   "lpg_unique_4kg": "1,274-1,455", "lpg_unique_15kg": "420-426"},
    }}
    lpg, brand, eff = parse_eppo(payload)
    assert brand == "unique" and eff == "2025-10-08", f"ต้องเลือกยี่ห้อใหม่สุด ได้ {brand}"
    assert len(lpg) == 3
    litre = next(r for r in lpg if r["metric"] == "LPG per Liter")
    assert abs(litre["value"] - 15.35) < 0.01, f"ต้องเป็นกึ่งกลางช่วง ได้ {litre['value']}"

    assert parse_eppo({"data": {}}) == ([], "", ""), "ไม่มีข้อมูลต้องไม่พัง"
    assert _midpoint("1,274-1,455") == 1364.5
    assert _midpoint("") is None

    assert _trend(1.5) == "ขึ้น" and _trend(-0.5) == "ลง" and _trend(0) == "เท่าเดิม"
    assert _trend(None) is None

    today_rows = [{"metric_name": "gasohol 95", "value": "37.69", "unit": "Baht/Liter", "source": "Kapook"}]
    prev_rows = [{"metric_name": "gasohol 95", "value": "37.19", "unit": "Baht/Liter", "source": "Kapook"}]
    out = format_rows(today_rows, "2026-08-22", prev_rows)
    g95 = out["ราคาน้ำมัน"]["แก๊สโซฮอล์ 95"]
    assert g95["ส่วนต่างราคา"] == 0.5 and g95["แนวโน้ม"] == "ขึ้น"

    # ไม่มี prev_rows (วันแรกที่มีข้อมูล) ต้องได้ None ไม่ใช่พัง
    out_no_prev = format_rows(today_rows, "2026-08-22")
    assert out_no_prev["ราคาน้ำมัน"]["แก๊สโซฮอล์ 95"]["ส่วนต่างราคา"] is None

    # สินค้าที่เพิ่งมีในวันนี้ (ไม่มีเมื่อวาน) ต้องได้ None ไม่ error
    new_item = today_rows + [{"metric_name": "diesel B20", "value": "33.00",
                              "unit": "Baht/Liter", "source": "Kapook"}]
    out2 = format_rows(new_item, "2026-08-22", prev_rows)
    assert out2["ราคาน้ำมัน"]["ดีเซล B20"]["ส่วนต่างราคา"] is None, "สินค้าใหม่ไม่มีเมื่อวานต้องได้ None"

    # fallback_prev_day ต้องส่งต่อให้ pttor.fetch_prices ตรงๆ ไม่แปลงอะไรเพิ่ม
    import unittest.mock as _mock
    with _mock.patch(f"{pttor.__name__}.fetch_prices", return_value=[{"metric_name": "gasohol 95", "value": "37.19"}]) as spy:
        got = fallback_prev_day(date(2026, 8, 21))
    spy.assert_called_once_with(date(2026, 8, 21))
    assert got == [{"metric_name": "gasohol 95", "value": "37.19"}]

    print("✅ ผ่าน — parse น้ำมัน 3 ชนิด, ข้ามค่าเสีย, เลือกยี่ห้อ LPG ใหม่สุด, กึ่งกลางช่วงราคา, "
          "แนวโน้ม/ส่วนต่างราคา, กันสินค้าใหม่ไม่มีเมื่อวาน, fallback PTTOR")


if __name__ == "__main__":
    import sys

    import json

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
