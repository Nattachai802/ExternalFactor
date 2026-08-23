"""MODULE 4 — อัตราแลกเปลี่ยน + อัตราเงินเฟ้อ

โมดูลเอกเทศน์ ค่ากลางระดับประเทศ ใช้ร่วมกันได้ทุกสาขา

    python -m modules.economic              # summary JSON
    python -m modules.economic --rows       # แถวลง fact_daily
    python -m modules.economic test         # self-check (ไม่ต่อเน็ต)

แหล่ง:
  อัตราแลกเปลี่ยน  openexchangerates.org (ฐาน USD — ต้องคำนวณ cross rate เอง)
  เงินเฟ้อ         World Bank API (FP.CPI.TOTL.ZG)

⚠️ World Bank ให้เงินเฟ้อ "รายปี" ไม่ใช่รายเดือน — ข้อมูลล่าช้าหลายเดือน
   ถ้าต้องการรายเดือนต้องดึงจากกระทรวงพาณิชย์แทน (ยังไม่ได้ทำ)
"""
import os
import re
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

OXR_URL = "https://openexchangerates.org/api/latest.json"
WB_URL = "http://api.worldbank.org/v2/country/tha/indicator/FP.CPI.TOTL.ZG?format=json"
TE_URL = "https://tradingeconomics.com/thailand/inflation-cpi"
TE_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# สกุลเงินที่เกี่ยวกับต้นทุน/ลูกค้าของร้านอาหารไทย
# USD วัตถุดิบนำเข้า | CNY JPY KRW นักท่องเที่ยว+วัตถุดิบเอเชีย | EUR GBP ไวน์/ชีส
CURRENCIES = ["USD", "EUR", "CNY", "JPY", "GBP", "AUD", "SGD", "MYR", "KRW"]


def _app_id() -> str | None:
    return os.getenv("OPEN_EXCHANGE_RATES_ID")


def parse_rates(payload: dict, currencies=None) -> dict[str, float]:
    """แปลง rate ฐาน USD เป็น 'กี่บาทต่อ 1 หน่วยของสกุลนั้น'

    OXR แผนฟรีให้ฐาน USD เท่านั้น — cross rate คำนวณเอง
    THB ต่อ 1 XXX = (THB ต่อ USD) / (XXX ต่อ USD)
    """
    rates = payload.get("rates", {})
    thb_per_usd = rates.get("THB")
    if not thb_per_usd:
        raise ValueError("ไม่พบอัตรา THB ใน response — API หรือแผนอาจเปลี่ยน")

    out = {}
    for cur in (currencies or CURRENCIES):
        per_usd = rates.get(cur)
        if not per_usd:
            continue
        out[cur] = round(thb_per_usd / per_usd, 4)
    return out


def parse_inflation(payload: list, limit: int = 5) -> list[dict]:
    """ดึงเงินเฟ้อรายปีล่าสุด — คืน [{ปี, ค่า}] เรียงใหม่ไปเก่า

    ยังไม่ถูกใช้แล้ว (ย้ายไป TradingEconomics ที่ให้รายเดือน) เก็บไว้เผื่อ fallback
    """
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        raise ValueError("World Bank คืนรูปแบบที่ไม่รู้จัก")
    return [{"ปี": e["date"], "ค่า": round(e["value"], 2)}
            for e in payload[1] if e.get("value") is not None][:limit]


# ตัวชี้วัดที่เก็บจากตาราง Related — ชื่อบนเว็บ → ชื่อ metric ที่ใช้ใน DB
TE_INDICATORS = {
    "Inflation Rate YoY": "Inflation Rate",
    "Inflation Rate MoM": "Inflation Rate MoM",
    "Core Inflation Rate YoY": "Core Inflation Rate",
    "Food Inflation": "Food Inflation",
    "CPI Food": "CPI Food",
    "CPI Transportation": "CPI Transportation",
    "Producer Prices Change": "Producer Prices Change",
}


def parse_trading_economics(html: str) -> list[dict]:
    """ตาราง Related ของ TradingEconomics — คืน [{metric, value, unit, reference}]

    ใช้แทน World Bank เพราะให้ข้อมูล "รายเดือน" (World Bank ให้รายปี ล่าช้าเกือบปี)
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        head = table.find("tr")
        if not head:
            continue
        header = [c.get_text(" ", strip=True) for c in head.find_all(["th", "td"])]
        if header[:2] != ["Related", "Last"]:
            continue

        out = []
        for tr in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 5 or cells[0] not in TE_INDICATORS:
                continue
            try:
                value = float(cells[1])
            except ValueError:
                continue
            out.append({
                "metric": TE_INDICATORS[cells[0]],
                "value": value,
                "unit": "%" if cells[3] == "percent" else cells[3],
                "reference": cells[4],          # เช่น "Jul 2026"
            })
        return out
    raise ValueError("ไม่พบตาราง Related — โครงเว็บ TradingEconomics อาจเปลี่ยน")


def fetch_inflation_monthly() -> list[dict]:
    r = requests.get(TE_URL, headers=TE_UA, timeout=25)
    r.raise_for_status()
    return parse_trading_economics(r.text)


def fetch_rates() -> tuple[dict[str, float], str]:
    """คืน (อัตราต่อบาท, เวลาที่อัปเดต)"""
    app_id = _app_id()
    if not app_id:
        raise RuntimeError("ไม่พบ OPEN_EXCHANGE_RATES_ID ใน environment")

    r = requests.get(OXR_URL, params={"app_id": app_id}, timeout=15)
    r.raise_for_status()
    payload = r.json()
    updated = datetime.fromtimestamp(payload.get("timestamp", 0)).isoformat(" ", "seconds")
    return parse_rates(payload), updated


def fetch_inflation() -> list[dict]:
    r = requests.get(WB_URL, timeout=15)
    r.raise_for_status()
    return parse_inflation(r.json())


def run(target_date: date | None = None, verbose: bool = True) -> list[dict]:
    """แถว fact_daily — 1 แถวต่อ 1 สกุลเงิน + เงินเฟ้อปีล่าสุด"""
    d = (target_date or date.today()).isoformat()
    rows = []

    if verbose:
        print("\n" + "=" * 50)
        print("💰  MODULE 4: Exchange Rate + Inflation")
        print("=" * 50)

    try:
        rates, updated = fetch_rates()
        rows += [{"date": d, "metric_name": f"Exchange Rate {cur}/THB",
                  "value": str(v), "unit": f"THB/{cur}",
                  "source": f"Open Exchange Rates ({updated})"} for cur, v in rates.items()]
        if verbose:
            print(f"  ✅ อัตราแลกเปลี่ยน {len(rates)} สกุล — อัปเดต {updated}")
    except Exception as e:
        print(f"  ❌ Exchange Rate: {e}")

    try:
        indicators = fetch_inflation_monthly()
        rows += [{"date": d, "metric_name": i["metric"], "value": str(i["value"]),
                  "unit": i["unit"], "source": f"TradingEconomics ({i['reference']})"}
                 for i in indicators]
        if verbose:
            for i in indicators:
                print(f"  ✅ {i['metric']:24} {i['value']:>8} {i['unit']:8} ({i['reference']})")
    except Exception as e:
        print(f"  ❌ Inflation: {e}")

    if verbose:
        print(f"  📊 รวม {len(rows)} แถวใน fact_daily")
    return rows


def format_rows(rows: list[dict], as_of: str) -> dict:
    """แปลงแถว fact_daily → record ภาษาไทย

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)
    metric_name รูปแบบ "Exchange Rate USD/THB" และ "Inflation Rate"
    """
    METRIC_TH = {
        "Inflation Rate": "เงินเฟ้อทั่วไป (YoY)",
        "Inflation Rate MoM": "เงินเฟ้อทั่วไป (MoM)",
        "Core Inflation Rate": "เงินเฟ้อพื้นฐาน (YoY)",
        "Food Inflation": "เงินเฟ้อหมวดอาหาร (YoY)",
        "CPI Food": "ดัชนีราคาอาหาร",
        "CPI Transportation": "ดัชนีราคาขนส่ง",
        "Producer Prices Change": "ดัชนีราคาผู้ผลิต (YoY)",
    }

    rates, indicators, reference = {}, {}, None

    for r in rows:
        name = r["metric_name"]
        if name.startswith("Exchange Rate"):
            rates[name.replace("Exchange Rate ", "").split("/")[0]] = float(r["value"])
        elif name in METRIC_TH:
            indicators[METRIC_TH[name]] = {"ค่า": float(r["value"]), "หน่วย": r["unit"]}
            # เดือนอ้างอิงอยู่ใน source — เงินเฟ้อเป็นรายเดือน ไม่ตรงกับ date ของแถวที่ scrape
            match = re.search(r"\(([^)]+)\)", r.get("source", ""))
            if match:
                reference = match.group(1)

    return {
        "วันที่": as_of,
        "อัตราแลกเปลี่ยน (บาท ต่อ 1 หน่วย)": dict(sorted(rates.items())),
        "เงินเฟ้อและดัชนีราคา": indicators,
        "เดือนอ้างอิงเงินเฟ้อ": reference,
        "แหล่งข้อมูล": sorted({r["source"].split(" (")[0] for r in rows}),
    }


def summary(target_date: date | None = None) -> dict:
    """อัตราแลกเปลี่ยน + เงินเฟ้อ — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)"""
    d = target_date or date.today()
    return format_rows(run(d, verbose=False), d.isoformat())


def demo():
    """self-check — parse จากตัวอย่าง ไม่ต่อเน็ต"""
    payload = {"timestamp": 1787260800,
               "rates": {"USD": 1.0, "THB": 32.874, "EUR": 0.85, "JPY": 147.0, "XYZ": 0}}

    rates = parse_rates(payload, ["USD", "EUR", "JPY", "XYZ", "ไม่มีสกุลนี้"])
    assert rates["USD"] == 32.874, rates
    assert abs(rates["EUR"] - 38.6753) < 0.001, f"cross rate ผิด: {rates['EUR']}"
    assert abs(rates["JPY"] - 0.2236) < 0.001, f"เยนต้องน้อยกว่า 1 บาท: {rates['JPY']}"
    assert "XYZ" not in rates, "อัตรา 0 ต้องถูกข้าม ไม่ใช่หารด้วยศูนย์"
    assert "ไม่มีสกุลนี้" not in rates

    try:
        parse_rates({"rates": {"USD": 1.0}})
    except ValueError:
        pass
    else:
        raise AssertionError("ไม่มี THB ต้อง raise ไม่ใช่คืนค่าว่างเงียบๆ")

    wb = [{}, [{"date": "2026", "value": None},
               {"date": "2025", "value": -0.1312},
               {"date": "2024", "value": 0.4}]]
    infl = parse_inflation(wb)
    assert infl == [{"ปี": "2025", "ค่า": -0.13}, {"ปี": "2024", "ค่า": 0.4}], infl
    assert parse_inflation(wb, limit=1) == [{"ปี": "2025", "ค่า": -0.13}]

    try:
        parse_inflation([{}])
    except ValueError:
        pass
    else:
        raise AssertionError("payload พังต้อง raise")

    # TradingEconomics — ตาราง Related
    TE_HTML = """
    <table><tr><th>Related</th><th>Last</th><th>Previous</th><th>Unit</th><th>Reference</th></tr>
      <tr><td>Inflation Rate YoY</td><td>1.95</td><td>2.42</td><td>percent</td><td>Jul 2026</td></tr>
      <tr><td>Food Inflation</td><td>2.04</td><td>1.03</td><td>percent</td><td>Jul 2026</td></tr>
      <tr><td>CPI Food</td><td>103.94</td><td>103.53</td><td>points</td><td>Jul 2026</td></tr>
      <tr><td>CPI Clothing</td><td>97.81</td><td>97.82</td><td>points</td><td>Jul 2026</td></tr>
      <tr><td>Inflation Rate YoY</td><td>ค่าพัง</td><td>-</td><td>percent</td><td>Jul 2026</td></tr>
    </table>
    """
    te = parse_trading_economics(TE_HTML)
    names = [x["metric"] for x in te]
    assert "Inflation Rate" in names and "Food Inflation" in names and "CPI Food" in names
    assert "CPI Clothing" not in names, "ตัวชี้วัดนอก TE_INDICATORS ต้องถูกข้าม"

    headline = next(x for x in te if x["metric"] == "Inflation Rate")
    assert headline["value"] == 1.95 and headline["unit"] == "%", headline
    assert headline["reference"] == "Jul 2026", "ต้องเก็บเดือนอ้างอิง ไม่ใช่แค่ค่า"
    assert next(x for x in te if x["metric"] == "CPI Food")["unit"] == "points"

    try:
        parse_trading_economics("<p>ไม่มีตาราง</p>")
    except ValueError:
        pass
    else:
        raise AssertionError("เว็บเปลี่ยนโครงต้อง raise ไม่ใช่คืนลิสต์ว่างเงียบๆ")

    print("✅ ผ่าน — cross rate, ข้ามอัตราศูนย์, TradingEconomics parse + ข้ามค่าพัง, เคสเว็บเปลี่ยนโครง")


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(summary(), ensure_ascii=False, indent=1))
