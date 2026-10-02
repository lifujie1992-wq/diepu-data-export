#!/usr/bin/env python3
"""投递真实鼠标点击（可重复多次）。用法: click2.py X Y [count]"""
import sys, time, Quartz

def click(x, y, count=2, gap=0.35):
    pt = Quartz.CGPointMake(x, y)
    for i in range(count):
        for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            e = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(e, Quartz.kCGMouseEventClickState, 1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
            time.sleep(0.06)
        time.sleep(gap)
    print(f"posted {count} clicks at ({x},{y})")

if __name__ == "__main__":
    x, y = float(sys.argv[1]), float(sys.argv[2])
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    click(x, y, n)
