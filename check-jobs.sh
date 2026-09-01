#!/bin/bash
# เช็คสุขภาพ cron job ทั้งหมดในคำสั่งเดียว — ห่อ curl + jq ที่ยาวไว้ในนี้แทน
#
# ติดตั้ง (ครั้งเดียว บน EC2):
#   chmod +x check-jobs.sh
#   sudo apt install -y jq   # ถ้ายังไม่มี
#
# ใช้:
#   ./check-jobs.sh              # สรุปสั้น
#   ./check-jobs.sh -v           # ละเอียด (รวม error message เต็ม)
#
# อยากพิมพ์สั้นกว่านี้อีก เพิ่ม alias ใน ~/.bashrc:
#   alias jobs-health='~/ExternalFactor/check-jobs.sh'
# แล้วพิมพ์แค่ jobs-health ได้เลยจากทุกที่

URL="${HEALTH_URL:-http://localhost:8000/api/v1/health/jobs}"
DATA=$(curl -sf "$URL") || { echo "❌ เรียก $URL ไม่ได้ — API รันอยู่ไหม?"; exit 1; }

if [[ "$1" == "-v" ]]; then
    echo "$DATA" | jq .
    exit 0
fi

echo "$DATA" | jq -r '
  "สถานะรวม: " + .["สถานะรวม"] + "  (มีปัญหา " + (.["จำนวนงานที่มีปัญหา"]|tostring) + " งาน)",
  "",
  (.["งาน"][] | "  " +
    (if .["สถานะ"] == "ปกติ" then "✅" else "❌" end) + " " +
    (.["งาน"] + (" " * (14 - (.["งาน"]|length)))) +
    .["สถานะ"] + "  (" + (.["นานมาแล้ว (วัน)"]|tostring) + " วันก่อน, " +
    (.["แถวที่เขียน"]|tostring) + " แถว)"),
  (if (.["งานที่ยังไม่เคยรันเลย"] | length) > 0
   then "\nยังไม่เคยรันเลย: " + (.["งานที่ยังไม่เคยรันเลย"] | join(", "))
   else "" end)
'
