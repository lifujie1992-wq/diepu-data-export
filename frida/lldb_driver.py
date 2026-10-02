#!!! 危险：在 Apple Silicon（Rosetta x86_64 进程）上 detach 会踩坏 dyld 导致 App 崩溃。
#!!! 仅供 Intel 机器参考。详见 METHOD.md §6。
"""lldb SB API 驱动的 qtng HTTP 抓取器（异步模式，自己控制 breakpoint 继续）。

用法（在 lldb 里）:
  lldb -b -o "command script import <repo>/frida/lldb_driver.py" \
       -o "drv_attach 86068 60"

或在 shell 里（若 python 能 import lldb）:
  python3 frida/lldb_driver.py --pid 86068 --seconds 60
"""
import json
import os
import sys
import time

import lldb

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lldb_http.jsonl")
T0 = time.time()
MAX_PREVIEW = 3000

# ---- 断点目标 ----
SYMS_GET = [
    "_ZN4qtng11HttpSession3getERK7QString",
    "_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_E",
    "_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_ERKS4_IS1_10QByteArrayE",
    "_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQuery",
    "_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE",
    "_ZN4qtng11HttpSession3getERK4QUrl",
    "_ZN4qtng11HttpSession3getERK4QUrlRK9QUrlQuery",
    "_ZN4qtng11HttpSession3getERK4QUrlRK9QUrlQueryRK4QMapI7QString10QByteArrayE",
    "_ZN4qtng11HttpSession3getERK4QUrlRK4QMapI7QStringS5_E",
    "_ZN4qtng11HttpSession3getERK4QUrlRK4QMapI7QStringS5_ERKS4_IS5_10QByteArrayE",
]
SYMS_POST = [
    "_ZN4qtng11HttpSession4postERK7QStringRK10QByteArray",
    "_ZN4qtng11HttpSession4postERK7QStringRK10QByteArrayRK4QMapIS1_S4_E",
    "_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQuery",
    "_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE",
    "_ZN4qtng11HttpSession4postERK7QStringRK11QJsonObject",
    "_ZN4qtng11HttpSession4postERK7QStringRK4QMapIS1_S1_E",
    "_ZN4qtng11HttpSession4postERK4QUrlRK10QByteArray",
    "_ZN4qtng11HttpSession4postERK4QUrlRK4QMapI7QStringS5_E",
    "_ZN4qtng11HttpSession4postERK4QUrlRK11QJsonObject",
]

HITS = {"n": 0, "argidx": {}}


def log(o):
    o["t"] = round(time.time() - T0, 3)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _mem(proc, addr, size):
    if not addr or size <= 0 or size > (1 << 28):
        return None
    err = lldb.SBError()
    b = proc.ReadMemory(addr, size, err)
    return bytes(b) if b and b.GetSize() == size else None


def _ptr(proc, addr):
    b = _mem(proc, addr, 8)
    return int.from_bytes(b, "little") if b else 0


def _qarray(proc, obj_addr):
    d = _ptr(proc, obj_addr)
    if not d:
        return None
    hdr = _mem(proc, d, 24)
    if not hdr:
        return None
    size = int.from_bytes(hdr[4:8], "little", signed=True)
    offset = int.from_bytes(hdr[16:24], "little", signed=True)
    return d, size, offset


def qstring(proc, obj_addr):
    r = _qarray(proc, obj_addr)
    if not r:
        return None
    d, size, offset = r
    if size < 0 or size > (1 << 22) or d + offset <= 0:
        return None
    if size == 0:
        return ""
    raw = _mem(proc, d + offset, size * 2)
    return raw.decode("utf-16-le", "replace") if raw else None


def qbytearray(proc, obj_addr):
    r = _qarray(proc, obj_addr)
    if not r:
        return None
    d, size, offset = r
    if size < 0 or size > (1 << 28):
        return None
    if size == 0:
        return b""
    return _mem(proc, d + offset, size)


def reg(frame, name):
    r = frame.FindRegister(name)
    return r.GetValueAsUnsigned() if r.IsValid() else 0


ARGREGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]


def find_url_arg(frame, proc):
    """自动探测 URL 在第几个参数位（成员函数 this + 可能的 sret 都会占位）。"""
    for i in range(0, 5):
        v = qstring(proc, reg(frame, ARGREGS[i]))
        if v and (v.startswith("http") or "/" in v) and len(v) > 4:
            return i, v
    return None, None


def make_cb(method):
    def cb(frame, bp_loc, internal_dict):
        try:
            proc = frame.GetThread().GetProcess()
            idx, url = find_url_arg(frame, proc)
            rec = {"kind": "req", "method": method, "url": url, "argidx": idx,
                   "sym": bp_loc.GetBreakpoint().GetName()}
            if url is None and HITS["n"] < 40:
                # 诊断：把前 5 个寄存器的原始 8 字节打出来
                rec["raw"] = {r: hex(reg(frame, r)) for r in ARGREGS[:5]}
            if method == "POST" and idx is not None:
                ba = qbytearray(proc, reg(frame, ARGREGS[idx + 1])) if idx + 1 < 6 else None
                if ba is not None:
                    rec["body_len"] = len(ba)
                    rec["body_preview"] = ba[:2000].decode("utf-8", "replace")
            HITS["n"] += 1
            log(rec)
        except Exception as e:
            log({"kind": "err", "err": str(e)})
        return False  # False = 不停止，继续跑

    return cb


def make_resp_cb(tag):
    def cb(frame, bp_loc, internal_dict):
        try:
            proc = frame.GetThread().GetProcess()
            ba = qbytearray(proc, reg(frame, "rsi"))
            rec = {"kind": tag}
            if ba is not None:
                rec["len"] = len(ba)
                rec["preview"] = ba[:MAX_PREVIEW].decode("utf-8", "replace")
            log(rec)
        except Exception as e:
            log({"kind": "err", "err": str(e)})
        return False

    return cb


def make_status_cb():
    def cb(frame, bp_loc, internal_dict):
        log({"kind": "status", "code": reg(frame, "rsi") & 0xFFFFFFFF})
        return False

    return cb


def run(seconds, pid=None, target_path=None, dbg=None):
    dbg = dbg or lldb.SBDebugger.Create()
    dbg.SetAsync(True)
    dbg.SetUseColor(False)

    target = dbg.CreateTarget(target_path) if target_path else dbg.CreateTarget("")
    listener = dbg.GetListener()
    err = lldb.SBError()

    if pid:
        target.AttachToProcessWithID(listener, pid, err)
    else:
        target.LaunchSimple(None, None, os.getcwd())
    if err.Fail():
        print("[drv] start failed:", err.GetCString(), flush=True)
        return

    process = target.GetProcess()

    try:
        # 等 attach 真正完成（异步模式下要等事件）
        wait_state(listener, process, lldb.eStateStopped, 15)
        print("[drv] attached, state=%d" % process.GetState(), flush=True)

        g = globals()
        for name, method in [("cbGET", "GET"), ("cbPOST", "POST")]:
            g[name] = make_cb(method)

        created = 0
        for sym in SYMS_GET + SYMS_POST:
            bp = target.BreakpointCreateByName(sym)
            if bp.GetNumLocations() > 0:
                method = "GET" if sym in SYMS_GET else "POST"
                bp.SetScriptCallbackFunction("lldb_driver.cb" + method)
                bp.SetAutoContinue(True)
                created += 1
        g["cbRESP"] = make_resp_cb("respbody")
        g["cbSTATUS"] = make_status_cb()
        for sym, fn in [("_ZN4qtng12HttpResponse7setBodyERK10QByteArray", "cbRESP"),
                        ("_ZN4qtng12HttpResponse13setStatusCodeEi", "cbSTATUS")]:
            bp = target.BreakpointCreateByName(sym)
            if bp.GetNumLocations() > 0:
                bp.SetScriptCallbackFunction("lldb_driver." + fn)
                bp.SetAutoContinue(True)
                created += 1

        log({"kind": "info", "msg": "breakpoints=%d" % created})
        print("[drv] breakpoints:", created, flush=True)

        if process.GetState() == lldb.eStateStopped:
            process.Continue()

        deadline = time.time() + seconds
        while time.time() < deadline:
            ev = lldb.SBEvent()
            if listener.WaitForEvent(500, ev):
                st = lldb.SBProcess.GetStateFromEvent(ev)
                if st == lldb.eStateStopped:
                    process.Continue()
                elif st == lldb.eStateExited:
                    print("[drv] process exited", flush=True)
                    break
    except Exception as e:
        print("[drv] error:", repr(e), flush=True)
    finally:
        print("[drv] window done, detaching", flush=True)
        try:
            if process.GetState() != lldb.eStateDetached:
                process.Detach()
        except Exception as e:
            print("[drv] detach err:", e, flush=True)
        print("[drv] detached", flush=True)


def wait_state(listener, process, state, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if process.GetState() == state:
            return True
        ev = lldb.SBEvent()
        if listener.WaitForEvent(300, ev):
            st = lldb.SBProcess.GetStateFromEvent(ev)
            if st == state:
                return True
    return False


def drv_attach(debugger, command, result, internal_dict):
    parts = command.split()
    pid = int(parts[0]) if parts else 0
    secs = int(parts[1]) if len(parts) > 1 else 60
    run(secs, pid=pid if pid else None, dbg=debugger)


def drv_launch(debugger, command, result, internal_dict):
    parts = command.split()
    path = parts[0]
    secs = int(parts[1]) if len(parts) > 1 else 60
    run(secs, target_path=path, dbg=debugger)


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_driver.drv_attach drv_attach")
    debugger.HandleCommand("command script add -f lldb_driver.drv_launch drv_launch")
    print("lldb_driver loaded", flush=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--target")
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    run(a.seconds, pid=a.pid, target_path=a.target)
