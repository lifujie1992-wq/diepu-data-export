#!/usr/bin/env python3
"""蝶普 App「导出数据表」自动化（AX 驱动，不依赖坐标点击的运气）。

原理:
  1. 用 osascript 把 cicada 设为前台（AX hit-test 只认前台 App）
  2. 命中「导出数据表」按钮并 AXPress
  3. 等导出向导窗口出现（通过 CGWindowList 找新窗口）
  4. 用 AXUIElementSetAttributeValue 直接写「日期」和「保存路径」文本框
  5. AXPress「下一步」→ 等文件写出 → AXPress「完成」

用法:
  diepu_export.py --date "2026-09-15/2026-09-15" --out /path/to/out.xlsx [--timeout 600]
"""
import argparse
import os
import subprocess
import sys
import time

import Quartz
from ApplicationServices import (
    AXUIElementCreateSystemWide, AXUIElementCopyElementAtPosition,
    AXUIElementCopyAttributeValue, AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
)

APP = "蝶普电商管理平台"
PROC = "cicada"
BIN_PAT = "MacOS/cicada"


# ---------- 基础 ----------
def attr(el, name):
    err, v = AXUIElementCopyAttributeValue(el, name, None)
    if err != 0 or v is None:
        return None
    return v if isinstance(v, (int, float, bool)) else str(v)


def hit(x, y):
    err, el = AXUIElementCopyElementAtPosition(
        AXUIElementCreateSystemWide(), float(x), float(y), None)
    return (el if err == 0 else None)


def press_at(x, y, expect=None, tries=4):
    """命中 (x,y) 并 AXPress。AXPress 常返回非 0 但实际生效，所以以 expect 校验。"""
    for i in range(tries):
        activate()
        el = hit(x, y)
        if el is not None:
            t = attr(el, "AXTitle") or attr(el, "AXDescription") or ""
            if expect is None or expect in str(t):
                AXUIElementPerformAction(el, "AXPress")
                return True
        time.sleep(0.6)
    return False


def activate():
    subprocess.run(["osascript", "-e",
                    'tell application "System Events" to tell process "%s" to set frontmost to true' % PROC],
                   capture_output=True, timeout=10)
    time.sleep(0.7)
    out = subprocess.run(["osascript", "-e",
                          'tell application "System Events" to get name of first process whose frontmost is true'],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    return out == PROC


def commit_field():
    """AXValue 写入只改显示，Qt 要 focus-out / Return 才发 editingFinished 让 App 内部状态更新。"""
    activate()
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 48'],
                   capture_output=True, timeout=10)   # Tab
    time.sleep(0.4)
    subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 36'],
                   capture_output=True, timeout=10)   # Return
    time.sleep(0.6)


def set_text(x, y, value, label=""):
    activate()
    el = hit(x, y)
    if el is None:
        raise RuntimeError(f"{label}: 命中失败 @({x},{y})")
    r = AXUIElementSetAttributeValue(el, "AXValue", value)
    time.sleep(0.3)
    el2 = hit(x, y)
    got = attr(el2, "AXValue") if el2 else None
    ok = (got == value) or (isinstance(got, str) and got.strip() == value)
    print(f"   {label}: set={r} got={got!r} ok={ok}")
    return ok


def cicada_windows():
    ws = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    out = []
    for w in ws:
        if w.get("kCGWindowOwnerName") == APP:
            b = w.get("kCGWindowBounds") or {}
            out.append((w.get("kCGWindowNumber"), b))
    return out


def find_wizard(before_ids, timeout=25):
    """向导窗口 ~800x574"""
    end = time.time() + timeout
    while time.time() < end:
        for wid, b in cicada_windows():
            if wid in before_ids:
                continue
            if 600 < b.get("Width", 0) < 1000 and 400 < b.get("Height", 0) < 800:
                return wid, b
        time.sleep(0.5)
    return None, None


# ---------- 主流程 ----------
def run_export(date_range, out_path, timeout=900, wizard_offsets=(373.0, 166.0, 373.0, 193.0, 707.0, 548.0, 714.0, 556.0)):
    dx_date, dy_date, dx_path, dy_path, dx_next, dy_next, dx_done, dy_done = wizard_offsets

    if not activate():
        print("!! 无法把 cicada 设为前台")
        return False

    before = {w[0] for w in cicada_windows()}
    print("[1] 按「导出数据表」")
    if not press_at(213, 63, expect="导出数据表"):
        print("!! 没按到导出数据表")
        return False

    wid, b = find_wizard(before, 30)
    if not wid:
        print("!! 向导窗口没出现")
        return False
    ox, oy = b["X"], b["Y"]
    print(f"[2] 向导窗口 id={wid} bounds=({ox},{oy},{b['Width']},{b['Height']})")

    time.sleep(1.0)
    print("[3] 写日期 / 保存路径")
    if not set_text(ox + dx_date, oy + dy_date, date_range, "日期"):
        print("!! 日期写入失败")
        return False
    if not set_text(ox + dx_path, oy + dy_path, out_path, "保存路径"):
        print("!! 保存路径写入失败")
        return False
    commit_field()   # 必须！否则日期范围会被忽略

    print("[4] 按「下一步」")
    press_at(ox + dx_next, oy + dy_next, expect="下一步")

    print("[5] 等导出完成…")
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout:
        time.sleep(4)
        sz = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if sz != last:
            print(f"    {int(time.time()-t0):>4}s  {sz} bytes")
            last = sz
        if sz > 0 and sz == last:
            time.sleep(6)
            if os.path.getsize(out_path) == sz:
                break
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    print(f"[6] 文件: {out_path} -> {'OK ' + str(os.path.getsize(out_path)) + ' bytes' if ok else 'FAIL'}")

    # 关掉向导 + 「导出成功。是否打开？」确认框（选否）
    activate()
    el = hit(ox + dx_done, oy + dy_done)
    if el is not None:
        AXUIElementPerformAction(el, "AXPress")
        print("[7] 按「完成」关闭向导")

    time.sleep(1.2)
    for wid2, b2 in cicada_windows():
        if not (150 < b2.get("Width", 0) < 400 and 80 < b2.get("Height", 0) < 250):
            continue
        # 217x127 的小确认框：否N 在右下方
        press_at(b2["X"] + b2["Width"] * 0.56, b2["Y"] + b2["Height"] * 0.75,
                 expect="否", tries=3)
        print(f"[8] 关闭确认框 id={wid2} bounds=({b2['X']},{b2['Y']},{b2['Width']},{b2['Height']})")
        break
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sys.exit(0 if run_export(a.date, a.out, a.timeout) else 1)


if __name__ == "__main__":
    main()
