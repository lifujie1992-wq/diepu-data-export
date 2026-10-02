#!/bin/bash
# 用法: run_range.sh 起 止     例: ./run_range.sh 2026-09-01 2026-09-30
# 依赖: App 已启动并登录；.venv 已就绪
set -u
cd "$(dirname "$0")"
D1=${1:?need start date}; D2=${2:?need end date}
mkdir -p xlsx logs
OUT="xlsx/蝶普产品_${D1}_${D2}.xlsx"
LOG="logs/${D1}_${D2}.log"
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== START $D1 ~ $D2  $(date) ===" >> "$LOG"
"$PY" diepu_export.py --date "$D1/$D2" --out "$PWD/$OUT" >> "$LOG" 2>&1
echo "=== EXPORT DONE rc=$? $(date) ===" >> "$LOG"
"$PY" build_product_db.py --db 产品数据.sqlite "$OUT" >> "$LOG" 2>&1
echo "=== MERGE DONE rc=$? $(date) ===" >> "$LOG"
