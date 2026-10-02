#!/usr/bin/env python3
"""AX 命中测试 + AXPress（可控版，自己负责激活目标 App）。

用法:
  ax.py probe X Y        # 打印该点上的 AX 元素（截断，安全）
  ax.py press X Y        # 命中后 AXPress
  ax.py chain X Y        # 打印祖先链
  ax.py find PID 关键词   # 在进程 AX 树里搜按钮
"""
import sys
import time

from ApplicationServices import (
    AXUIElementCreateSystemWide, AXUIElementCreateApplication,
    AXUIElementCopyElementAtPosition, AXUIElementCopyAttributeValue,
    AXUIElementPerformAction, AXUIElementSetAttributeValue,
)

SHORT = ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXIdentifier",
         "AXEnabled", "AXHelp", "AXPlaceholderValue"]

APP_PID = None


def trunc(v, n=90):
    s = v if isinstance(v, str) else str(v)
    s = s.replace("\n", "\\n").replace("\x00", "\\0")
    return s if len(s) <= n else s[:n] + f"...<+{len(s)-n}>"


def attr(el, name):
    err, v = AXUIElementCopyAttributeValue(el, name, None)
    if err != 0 or v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    try:
        return str(v)
    except Exception:
        return None


def describe(el):
    out = []
    for a in SHORT:
        v = attr(el, a)
        if v is None or v == "":
            continue
        out.append(f"{a[2:]}={trunc(v)!r}")
    for a in ("AXPosition", "AXSize"):
        v = attr(el, a)
        if v:
            out.append(f"{a[2:]}={trunc(v, 70)}")
    print("  ".join(out) if out else "(no readable attrs)")


def activate(pid):
    """用 osascript/System Events 激活（AppKit 的 activateWithOptions_ 在本会话里无效）。"""
    import subprocess
    try:
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to tell process "cicada" to set frontmost to true'],
            capture_output=True, timeout=10)
        time.sleep(0.9)
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return out == "cicada"
    except Exception as e:
        print("activate err", e)
        return False


def hit(x, y):
    sysw = AXUIElementCreateSystemWide()
    err, el = AXUIElementCopyElementAtPosition(sysw, float(x), float(y), None)
    return err, el


def parent(el):
    err, p = AXUIElementCopyAttributeValue(el, "AXParent", None)
    return p if err == 0 else None


def find_pid(name="cicada"):
    import subprocess
    out = subprocess.run(["pgrep", "-f", "MacOS/%s" % name],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def walk(el, kw, depth=0, maxdepth=16):
    if depth > maxdepth:
        return
    role = attr(el, "AXRole") or ""
    for a in ("AXTitle", "AXDescription", "AXValue", "AXIdentifier"):
        v = attr(el, a)
        if v and kw in str(v):
            print(f"  {'  '*depth}{role} {a}={trunc(v)}")
            break
    err, kids = AXUIElementCopyAttributeValue(el, "AXChildren", None)
    if err == 0 and kids:
        try:
            for k in kids:
                walk(k, kw, depth + 1, maxdepth)
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    pid = APP_PID or find_pid()
    if cmd in ("probe", "press", "chain"):
        x, y = float(sys.argv[2]), float(sys.argv[3])
        if pid:
            activate(pid)
        err, el = hit(x, y)
        if err != 0 or el is None:
            print(f"hit-test failed err={err}")
            return 1
        if cmd == "probe":
            describe(el)
        elif cmd == "chain":
            cur, i = el, 0
            while cur is not None and i < 8:
                print(f"[{i}]", end=" ")
                describe(cur)
                cur, i = parent(cur), i + 1
        else:
            describe(el)
            print("AXPress ->", AXUIElementPerformAction(el, "AXPress"))
    elif cmd == "find":
        p = int(sys.argv[2]); kw = sys.argv[3]
        walk(AXUIElementCreateApplication(p), kw)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
