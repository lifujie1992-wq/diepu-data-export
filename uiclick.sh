#!/bin/bash
# 用法: uiclick.sh X Y [retries]
X=$1; Y=$2; N=${3:-6}
for i in $(seq 1 $N); do
  out=$(osascript <<EOF 2>&1
tell application "System Events"
  set frontmost of process "cicada" to true
  delay 0.5
  click at {$X, $Y}
end tell
EOF
)
  case "$out" in
    *-25211*|*-25204*|*"不允许辅助访问"*|*"类型错误"*) sleep 0.9; continue;;
    *) echo "$out"; exit 0;;
  esac
done
echo "FAILED after $N tries: $out"; exit 1
