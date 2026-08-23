"""MODULE — สีเสื้อมงคลตามวันในสัปดาห์ (OFM.co.th)

ต่างจาก modules/myth.py (สีมงคลตามธาตุ 5 ระดับของ qmrl888, ไม่ผูกวัน) —
ตารางนี้ผูกกับ "วันในสัปดาห์" (จันทร์-อาทิตย์) ตามหลักทักษา คงที่เหมือนกัน
ไม่ต้อง scrape ซ้ำรายวัน scrape ครั้งเดียวพอ (หรือ hardcode ตรงจากตารางต้นฉบับก็ได้
เพราะหน้าเว็บนี้เป็นบทความ static ไม่ใช่ dashboard ที่อัปเดตเอง)

ที่มา: https://www.ofm.co.th/blog/colors-for-lucky-shirts-2026-daily-forecast/
หน้านี้ไม่มีโค้ดสี (hex) ให้ — HEX ด้านล่างเป็นค่าที่เทียบเอง (เทียบชื่อ EN ที่บทความ
แปะไว้บางจุด เช่น Navy/Terracotta/Sage Green + สายตาสำหรับที่เหลือ) ไม่ใช่ค่าทางการ
แก้ให้ตรงขึ้นทีหลังได้ที่ตาราง dim_lucky_shirt โดยตรง ไม่ต้องแก้โค้ด/deploy ใหม่

    python -m modules.lucky_shirt              # สีของ "วันนี้"
    python -m modules.lucky_shirt --date 2026-08-24
    python -m modules.lucky_shirt test           # self-check (ไม่ต่อเน็ต)
"""
from datetime import date, datetime

SOURCE = "ofm.co.th (สีเสื้อมงคล 2569)"

WEEKDAY_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]

# หมวดตามตารางต้นฉบับ — "เสน่ห์" คือคอลัมน์ "ความรัก/เสน่ห์" ของบทความ
CATEGORIES = ["โชคลาภ", "บารมี", "เสน่ห์", "กาลกิณี"]

# 1 แถวต่อ (วัน, หมวด) → list สี — ทับศัพท์ตรงจากตารางต้นฉบับ ไม่ตัดคำ
TABLE: dict[str, dict[str, list[str]]] = {
    "จันทร์":    {"โชคลาภ": ["เขียวใบไม้", "เขียวขี้ม้า"], "บารมี": ["ดำ", "ม่วงเข้ม", "เทาเข้ม"],
                 "เสน่ห์": ["ส้มอิฐ", "น้ำตาลทอง"], "กาลกิณี": ["แดงเพลิง"]},
    "อังคาร":    {"โชคลาภ": ["ส้มปะการัง", "ทอง"], "บารมี": ["น้ำเงินเข้ม", "กรมท่า"],
                 "เสน่ห์": ["ชมพูกลีบบัว", "แดงสด"], "กาลกิณี": ["ขาว", "ครีม"]},
    "พุธ":       {"โชคลาภ": ["เทาควันบุหรี่", "ดำ"], "บารมี": ["ส้มแสด", "เหลืองอำพัน"],
                 "เสน่ห์": ["ขาวนวล", "เหลืองอ่อน"], "กาลกิณี": ["ชมพูทุกเฉด"]},
    "พฤหัสบดี":  {"โชคลาภ": ["แดงเลือดหมู", "ชมพู"], "บารมี": ["ขาว", "มุก", "บรอนซ์เงิน"],
                 "เสน่ห์": ["เขียวทุกโทน"], "กาลกิณี": ["ม่วง", "ดำ"]},
    "ศุกร์":     {"โชคลาภ": ["ชมพูพาสเทล"], "บารมี": ["เขียวมิ้นต์", "เขียวสด"],
                 "เสน่ห์": ["ส้ม", "พีช"], "กาลกิณี": ["เทา", "น้ำตาล"]},
    "เสาร์":     {"โชคลาภ": ["น้ำเงิน", "ฟ้าคราม"], "บารมี": ["แดงเข้ม", "ทับทิม"],
                 "เสน่ห์": ["ม่วง", "ดำ"], "กาลกิณี": ["เขียวสด"]},
    "อาทิตย์":   {"โชคลาภ": ["ขาว", "ครีม", "เบจ"], "บารมี": ["ม่วงเปลือกมังคุด", "ดำ"],
                 "เสน่ห์": ["เขียวอ่อน", "เทา"], "กาลกิณี": ["น้ำเงิน", "ฟ้า"]},
}

# ponytail: hex เทียบเอง ไม่ใช่ของ official — ดูหมายเหตุหัวไฟล์
COLOR_HEX: dict[str, str] = {
    "เขียวใบไม้": "#3C8D40", "เขียวขี้ม้า": "#6B8E23", "ดำ": "#000000",
    "ม่วงเข้ม": "#4A148C", "เทาเข้ม": "#4A4A4A", "ส้มอิฐ": "#C1440E",
    "น้ำตาลทอง": "#B8860B", "แดงเพลิง": "#E63212", "ส้มปะการัง": "#FF7F50",
    "ทอง": "#FFD700", "น้ำเงินเข้ม": "#000080", "กรมท่า": "#12232E",
    "ชมพูกลีบบัว": "#F8C8DC", "แดงสด": "#FF0000", "ขาว": "#FFFFFF",
    "ครีม": "#FFFDD0", "เทาควันบุหรี่": "#8C8C8C", "ส้มแสด": "#FF5722",
    "เหลืองอำพัน": "#FFBF00", "ขาวนวล": "#FFF8E7", "เหลืองอ่อน": "#FFF59D",
    "ชมพูทุกเฉด": "#FFC0CB", "แดงเลือดหมู": "#7B1F1F", "ชมพู": "#FFC0CB",
    "มุก": "#F0EAD6", "บรอนซ์เงิน": "#C0C0C0", "เขียวทุกโทน": "#4CAF50",
    "ม่วง": "#800080", "ชมพูพาสเทล": "#FFD1DC", "เขียวมิ้นต์": "#98FF98",
    "เขียวสด": "#39B54A", "ส้ม": "#FFA500", "พีช": "#FFE5B4", "เทา": "#808080",
    "น้ำตาล": "#8B4513", "น้ำเงิน": "#1565C0", "ฟ้าคราม": "#3F51B5",
    "แดงเข้ม": "#8B0000", "ทับทิม": "#9B111E", "เบจ": "#F5F5DC",
    "ม่วงเปลือกมังคุด": "#4B0082", "เขียวอ่อน": "#C8E6C9", "ฟ้า": "#4A90D9",
}


def weekday_th(d: date) -> str:
    """date.weekday(): จันทร์=0 ... อาทิตย์=6 — ตรงกับลำดับ WEEKDAY_TH พอดี"""
    return WEEKDAY_TH[d.weekday()]


def run(verbose: bool = True) -> list[dict]:
    """แถว dim_lucky_shirt — ตารางคงที่ 7 วัน ไม่ต้องอิงวันที่ปัจจุบัน รันครั้งเดียวพอ"""
    rows = [
        {"weekday": weekday, "category": category, "color_th": color,
         "hex": COLOR_HEX.get(color, ""), "source": SOURCE}
        for weekday, categories in TABLE.items()
        for category, colors in categories.items()
        for color in colors
    ]
    if verbose:
        print("\n" + "=" * 50)
        print("👕 MODULE: Lucky Shirt Color")
        print("=" * 50)
        print(f"  📊 รวม {len(rows)} แถวใน dim_lucky_shirt")
    return rows


def format_rows(rows: list[dict], weekday: str) -> dict:
    """แถวของ "วันเดียว" → record ภาษาไทย จัดกลุ่มตามหมวด"""
    by_category: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for r in rows:
        by_category.setdefault(r["category"], []).append({"ชื่อ": r["color_th"], "รหัสสี": r["hex"]})

    return {
        "วัน": weekday,
        "สีมงคล": by_category,
        "หมายเหตุ": "รหัสสีเป็นค่าประมาณที่เทียบเอง ไม่ใช่ค่าทางการจากแหล่งข้อมูล",
        "แหล่งข้อมูล": SOURCE,
    }


def demo():
    """self-check — weekday mapping + ครบทุกวัน/หมวด + format ไม่ต่อเน็ต ไม่แตะ DB"""
    assert weekday_th(date(2026, 8, 24)) == "จันทร์"   # 24 ส.ค. 2569 ตรงกับวันจันทร์จริง
    assert weekday_th(date(2026, 8, 30)) == "อาทิตย์"

    assert set(TABLE.keys()) == set(WEEKDAY_TH), "ต้องมีครบ 7 วัน ไม่ตกวันไหน"
    for weekday, categories in TABLE.items():
        assert set(categories.keys()) == set(CATEGORIES), f"{weekday} หมวดไม่ครบ"

    all_colors = {c for cats in TABLE.values() for colors in cats.values() for c in colors}
    missing = all_colors - set(COLOR_HEX)
    assert not missing, f"สีไม่มี hex: {missing}"

    rows = run(verbose=False)
    assert len(rows) == sum(len(c) for day in TABLE.values() for c in day.values())
    assert all(r["hex"].startswith("#") for r in rows), "ทุกแถวต้องมี hex (เทียบไว้ครบแล้ว)"

    monday_rows = [r for r in rows if r["weekday"] == "จันทร์"]
    out = format_rows(monday_rows, "จันทร์")
    assert out["สีมงคล"]["โชคลาภ"][0] == {"ชื่อ": "เขียวใบไม้", "รหัสสี": "#3C8D40"}
    assert out["สีมงคล"]["กาลกิณี"][0]["ชื่อ"] == "แดงเพลิง"

    print("✅ ผ่าน — mapping วันในสัปดาห์, ครบ 7 วัน×4 หมวด, hex ครบทุกสี, จัดกลุ่ม format")


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        demo()
    else:
        def opt(flag, cast):
            return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else None

        d_str = opt("--date", str)
        d = datetime.strptime(d_str, "%Y-%m-%d").date() if d_str else date.today()
        weekday = weekday_th(d)
        rows = [r for r in run(verbose=False) if r["weekday"] == weekday]
        print(json.dumps(format_rows(rows, weekday), ensure_ascii=False, indent=1))
