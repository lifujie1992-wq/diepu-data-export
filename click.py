#!/usr/bin/env python3
"""在指定屏幕坐标投递真实鼠标点击（点坐标）。用法: click.py X Y [--list]"""
import sys, time, Quartz, AppKit

def list_windows():
    ws = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    for w in ws:
        print(f"  id={w.get('kCGWindowNumber'):>8}  owner={w.get('kCGWindowOwnerName','')!r:<22} "
              f"name={str(w.get('kCGWindowName',''))[:38]!r:<40} bounds={w.get('kCGWindowBounds')}")

if __name__ == "__main__":
    if "--list" in sys.argv:
        list_windows(); sys.exit(0)
    x, y = float(sys.argv[1]), float(sys.argv[2])
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        e = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.08)
    print(f"posted click at ({x}, {y})")
