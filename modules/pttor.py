"""MODULE — ราคาน้ำมัน PTTOR (ทางการ, ย้อนหลังได้) — https://orapiweb.pttor.com

ใช้เป็น fallback ของ modules/energy.py เท่านั้นตอนนี้ (ไม่มี job ของตัวเอง) — วันไหนที่ DB
ไม่มีข้อมูลของ "เมื่อวาน" ให้เทียบ (deploy วันแรก/มี gap) ยิงเส้นนี้ตรงแทนการเดียงเป็น None ทิ้ง

⚠️ namespace จริงคือ http://www.pttor.com — WSDL เขียน targetNamespace ผิดเป็น
   https://orapiweb.pttor.com ยิงตาม WSDL ตรงๆ จะได้ 400 "Language not provided" เสมอ
   (ยืนยันแล้วด้วยการยิงจริงทั้งสองแบบ)

⚠️ ราคาที่ได้คือ "ราคาที่มีผล ณ วันที่ถาม" ไม่ใช่ราคาที่ประกาศวันนั้นเป๊ะ — ปตท.ไม่ปรับราคา
   ทุกวัน ถามวันที่ไม่มีการปรับราคาจะได้ราคาของรอบก่อนหน้าย้อนไป ไม่ error (พฤติกรรมนี้ถูกต้อง
   สำหรับ "ราคาวันนั้น" — ยืนยันด้วยการยิงเทียบหลายวันแล้ว)

    python -m modules.pttor 2026-08-20        # ราคาที่มีผล ณ วันนั้น
    python -m modules.pttor test                # self-check (ไม่ต่อเน็ต)
"""
import re
from datetime import date

import requests

BASE = "https://orapiweb.pttor.com/oilservice/OilPrice.asmx"
NAMESPACE = "http://www.pttor.com"          # ไม่ใช่ targetNamespace ใน WSDL — ดูหมายเหตุหัวไฟล์
SOURCE = "PTTOR (orapiweb.pttor.com)"

# แปลงชื่อสินค้าของ PTTOR → metric_name เดียวกับที่ modules/energy.py ใช้ (มาจาก Kapook)
# เพื่อให้เทียบราคาข้ามแหล่งได้ด้วย key เดียวกัน — สินค้าที่ไม่มีคู่ใน Kapook (เช่น Super Power X99)
# ไม่ใส่ในนี้โดยตั้งใจ ปล่อยให้ parse() ข้ามไปเงียบๆ ดีกว่าเดา mapping ผิดแล้วเทียบราคาสับสน
PRODUCT_TO_METRIC = {
    "ดีเซล B20": "diesel B20",
    "ดีเซล": "diesel",
    "เบนซินแก๊สโซฮอล์ E20": "gasohol E20",
    "เบนซินแก๊สโซฮอล์ 91": "gasohol 91",
    "เบนซินแก๊สโซฮอล์ 95": "gasohol 95",
    "เบนซิน": "benzin 95",
    "Super Power GSH95": "superpower gasoline 95",
    "Super Power Diesel": "diesel premium",
}


def _envelope(dd: int, mm: int, yyyy: int) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soap:Body>'
        f'<GetOilPrice xmlns="{NAMESPACE}">'
        '<Language>TH</Language>'
        f'<DD>{dd}</DD><MM>{mm}</MM><YYYY>{yyyy}</YYYY>'
        '</GetOilPrice></soap:Body></soap:Envelope>'
    )


def fetch_raw(d: date) -> str:
    """ยิง SOAP ตรงๆ ไม่ใช้ zeep/suds — WSDL ผิด namespace ทำให้ client ทั่วไป autogen พัง"""
    headers = {"Content-Type": "text/xml; charset=utf-8",
               "SOAPAction": '"https://orapiweb.pttor.com/GetOilPrice"'}
    r = requests.post(BASE, data=_envelope(d.day, d.month, d.year).encode("utf-8"),
                      headers=headers, timeout=20)
    if r.status_code == 400:
        raise ValueError(f"PTTOR API ปฏิเสธคำขอ: {r.text.strip()}")
    r.raise_for_status()
    return r.text


def parse(xml_text: str) -> list[dict]:
    """แกะ <FUEL><PRODUCT>..</PRODUCT><PRICE>..</PRICE></FUEL> ที่ถูก HTML-escape ซ้อนมาในผลลัพธ์

    response เป็น SOAP envelope ที่ตัว GetOilPriceResult เป็น string เดียวซึ่งข้างในเป็น XML
    อีกชั้น (escape เป็น &lt;&gt;) — unescape ก่อนแล้วค่อย parse ด้วย regex (โครงคงที่ ไม่ซ้อน tag ลึก)
    """
    import html
    inner = html.unescape(xml_text)
    out = []
    for m in re.finditer(r"<PRODUCT>([^<]+)</PRODUCT>\s*<PRICE>([\d.]+)</PRICE>", inner):
        name, price = m.group(1).strip(), float(m.group(2))
        metric = PRODUCT_TO_METRIC.get(name)
        if metric:
            out.append({"metric_name": metric, "value": str(price), "product_th": name})
    return out


def fetch_prices(d: date) -> list[dict]:
    """วันที่ → แถวราคาที่มีผล ณ วันนั้น รูปแบบ [{metric_name, value}] พร้อมเทียบกับ fact_daily"""
    return parse(fetch_raw(d))


def demo():
    """self-check — parse response ตัวอย่างจริงที่บันทึกไว้ ไม่ต่อเน็ต"""
    SAMPLE = (
        "&lt;PTTOR_DS&gt;"
        "&lt;FUEL&gt;&lt;PRICE_DATE&gt;08/19/2026 5:00:00 AM&lt;/PRICE_DATE&gt;"
        "&lt;PRODUCT&gt;ดีเซล B20&lt;/PRODUCT&gt;&lt;PRICE&gt;33.39&lt;/PRICE&gt;&lt;/FUEL&gt;"
        "&lt;FUEL&gt;&lt;PRICE_DATE&gt;08/19/2026 5:00:00 AM&lt;/PRICE_DATE&gt;"
        "&lt;PRODUCT&gt;เบนซินแก๊สโซฮอล์ 95&lt;/PRODUCT&gt;&lt;PRICE&gt;37.69&lt;/PRICE&gt;&lt;/FUEL&gt;"
        "&lt;FUEL&gt;&lt;PRICE_DATE&gt;08/19/2026 5:00:00 AM&lt;/PRICE_DATE&gt;"
        "&lt;PRODUCT&gt;Super Power X99&lt;/PRODUCT&gt;&lt;PRICE&gt;49.79&lt;/PRICE&gt;&lt;/FUEL&gt;"
        "&lt;/PTTOR_DS&gt;"
    )
    rows = parse(SAMPLE)
    assert len(rows) == 2, "X99 ไม่มีใน mapping ต้องถูกข้าม เหลือ 2"
    assert {r["metric_name"] for r in rows} == {"diesel B20", "gasohol 95"}
    g95 = next(r for r in rows if r["metric_name"] == "gasohol 95")
    assert g95["value"] == "37.69"

    envelope = _envelope(20, 8, 2026)
    assert f'xmlns="{NAMESPACE}"' in envelope
    assert "<DD>20</DD><MM>8</MM><YYYY>2026</YYYY>" in envelope

    assert parse("ไม่มี FUEL เลย") == [], "ไม่มีข้อมูลต้องคืนลิสต์ว่าง ไม่ใช่พัง"

    print("✅ ผ่าน — parse FUEL/PRODUCT/PRICE, ข้ามสินค้าไม่รู้จัก mapping, envelope ถูก namespace")


if __name__ == "__main__":
    import json
    import sys
    from datetime import datetime

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    elif len(sys.argv) > 1:
        d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        print(json.dumps(fetch_prices(d), ensure_ascii=False, indent=1))
    else:
        print("usage: python -m modules.pttor <YYYY-MM-DD> | test")
