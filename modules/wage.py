import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

URL = "https://wageindicator.org/work/minimum-wage/countries/thailand/"
SOURCE = "wageindicator.org"
UA = {"User-Agent": "Mozilla/5.0"}

ROW_RE = re.compile(r"^(?P<province>.+?)\s+Minimum wage with effect from\s+(?P<eff>.+)$")
WAGE_RE = re.compile(r"THB\s*([\d,]+(?:\.\d+)?)")


def parse_effective(text: str) -> str:
    for fmt in ("%B %d, %Y", "%d %B %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return text.strip()


def parse_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("ไม่พบ <table> ในหน้า — โครงเว็บอาจเปลี่ยน")

    out = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        m = ROW_RE.match(cells[0])
        w = WAGE_RE.search(cells[1])
        if not m or not w:
            continue
        out.append({
            "province": m.group("province").strip(),
            "wage": float(w.group(1).replace(",", "")),
            "effective_date": parse_effective(m.group("eff")),
        })
    return out


def to_policy_rows(records: list[dict]) -> list[dict]:
    """แถวลง fact_minimum_wage — เก็บแค่ที่ frontend query จริง (จังหวัด, วันที่, เงิน, สกุลเงิน)"""
    return [{
        "effective_date": r["effective_date"],
        "jurisdiction": r["province"],
        "wage_value": r["wage"],
        "currency": "THB",
    } for r in records]


def fetch() -> list[dict]:
    r = requests.get(URL, headers=UA, timeout=20)
    r.raise_for_status()
    return parse_table(r.text)


def run(verbose: bool = True) -> list[dict]:
    records = fetch()
    rows = to_policy_rows(records)

    if verbose:
        wages = [r["wage"] for r in records]
        eff = {r["effective_date"] for r in records}
        print("\n" + "=" * 50)
        print("💰 MODULE: Minimum Wage by Province")
        print("=" * 50)
        print(f"  ✅ {len(records)} จังหวัด | ต่ำสุด {min(wages):g} สูงสุด {max(wages):g} บาท/วัน")
        print(f"  📅 วันบังคับใช้: {', '.join(sorted(eff))}")
        top = sorted(records, key=lambda r: -r["wage"])[:3]
        print("  สูงสุด: " + ", ".join(f"{r['province']} {r['wage']:g}" for r in top))
        print(f"  📊 รวม {len(rows)} แถวใน fact_policy")
    return rows


def format_records(records: list[dict], top: int | None = None,
                    bottom: int | None = None) -> dict:
    """แปลงลิสต์ {province, wage, effective_date} → record ภาษาไทย

    ฟังก์ชันบริสุทธิ์ ไม่ต่อเน็ต — ใช้ร่วมกันทั้งตอน scrape สด (summary) และตอนอ่านจาก DB (API)
    """
    records = sorted(records, key=lambda r: (-r["wage"], r["province"]))
    if top:
        records = records[:top]
    if bottom:
        records = sorted(records, key=lambda r: (r["wage"], r["province"]))[:bottom]

    wages = [r["wage"] for r in records]
    return {
        "หน่วย": "บาท/วัน",
        "วันบังคับใช้": sorted({r["effective_date"] for r in records}),
        "จำนวนจังหวัด": len(records),
        "ค่าแรงเฉลี่ย": round(sum(wages) / len(wages), 2) if wages else None,
        "ค่าแรง": {r["province"]: r["wage"] for r in records},
        "แหล่งข้อมูล": SOURCE,
    }


def summary(province: str | None = None, top: int | None = None,
            bottom: int | None = None) -> dict:
    """ค่าแรงขั้นต่ำรายจังหวัด — scrape สด (ใช้จาก cron เท่านั้น API ต้อง query DB แทน)

    province : กรองเฉพาะจังหวัดที่ระบุ (คั่นด้วย , ได้หลายจังหวัด, ไม่สนตัวพิมพ์)
    top      : เอาเฉพาะ k จังหวัดที่ค่าแรงสูงสุด
    bottom   : เอาเฉพาะ k จังหวัดที่ค่าแรงต่ำสุด
    """
    records = fetch()
    if province:
        wanted = {p.strip().lower() for p in province.split(",") if p.strip()}
        records = [r for r in records if r["province"].lower() in wanted]
    return format_records(records, top, bottom)


def demo():
    SAMPLE = """
    <table><tbody>
      <tr><th></th><th>Per Day</th></tr>
      <tr><td>Chiang Mai Minimum wage with effect from July 1, 2025</td><td>THB357.00</td></tr>
      <tr><td>Bangkok Minimum wage with effect from July 1, 2025</td><td>THB400.00</td></tr>
      <tr><td>Mae Hong Son Minimum wage with effect from July 1, 2025</td><td>THB347.00</td></tr>
    </tbody></table>
    """
    recs = parse_table(SAMPLE)
    assert len(recs) == 3, f"ต้องได้ 3 แถว (header ต้องถูกข้าม) ได้ {len(recs)}"
    assert recs[1] == {"province": "Bangkok", "wage": 400.0, "effective_date": "2025-07-01"}, recs[1]
    assert recs[2]["province"] == "Mae Hong Son", "ชื่อจังหวัดหลายคำต้องไม่ถูกตัด"

    rows = to_policy_rows(recs)
    assert rows[1] == {"effective_date": "2025-07-01", "jurisdiction": "Bangkok",
                       "wage_value": 400.0, "currency": "THB"}, rows[1]

    assert parse_effective("July 1, 2025") == "2025-07-01"
    assert parse_effective("ข้อความมั่ว") == "ข้อความมั่ว", "อ่านวันที่ไม่ออกต้องคืนค่าเดิม ไม่ใช่พัง"

    try:
        parse_table("<p>ไม่มีตาราง</p>")
    except ValueError:
        pass
    else:
        raise AssertionError("ไม่มีตารางต้อง raise ให้รู้ว่าเว็บเปลี่ยนโครง")

    # summary — กรอง/จัดอันดับ โดยไม่ต่อเน็ต
    import unittest.mock as _mock
    fake = [
        {"province": "Bangkok", "wage": 400.0, "effective_date": "2025-07-01"},
        {"province": "Chiang Mai", "wage": 357.0, "effective_date": "2025-07-01"},
        {"province": "Mae Hong Son", "wage": 347.0, "effective_date": "2025-07-01"},
        {"province": "Yasothon", "wage": 337.0, "effective_date": "2025-07-01"},
    ]
    with _mock.patch(f"{__name__}.fetch", return_value=list(fake)):
        allp = summary()
        assert list(allp["ค่าแรง"]) == ["Bangkok", "Chiang Mai", "Mae Hong Son", "Yasothon"], allp
        assert allp["ค่าแรงเฉลี่ย"] == 360.25, allp["ค่าแรงเฉลี่ย"]

        assert list(summary(top=2)["ค่าแรง"]) == ["Bangkok", "Chiang Mai"]
        assert list(summary(bottom=2)["ค่าแรง"]) == ["Yasothon", "Mae Hong Son"]
        assert summary(province="bangkok")["ค่าแรง"] == {"Bangkok": 400.0}, "ต้องไม่สนตัวพิมพ์"
        assert set(summary(province="Bangkok, Yasothon")["ค่าแรง"]) == {"Bangkok", "Yasothon"}
        assert summary(province="ไม่มีจังหวัดนี้")["ค่าแรง"] == {}, "ไม่เจอต้องคืนว่าง ไม่พัง"

    print("✅ ผ่าน — parse ตาราง, จังหวัดหลายคำ, วันที่, เว็บเปลี่ยนโครง, กรองจังหวัด/top/bottom")


if __name__ == "__main__":
    import sys

    import json

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif "--rows" in sys.argv:
        print(json.dumps(run(verbose=False), ensure_ascii=False, indent=1))
    else:
        def opt(flag):
            return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None

        print(json.dumps(summary(
            province=opt("--province"),
            top=int(opt("--top")) if opt("--top") else None,
            bottom=int(opt("--bottom")) if opt("--bottom") else None,
        ), ensure_ascii=False, indent=1))
