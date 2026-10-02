#!!! 危险：在 Apple Silicon（Rosetta x86_64 进程）上 detach 会踩坏 dyld 导致 App 崩溃。
#!!! 仅供 Intel 机器参考。详见 METHOD.md §6。
"""lldb 批处理版 qtng HTTP 抓取。

配合用法（在 shell 里）:
  lldb -b -p <pid> \
    -o "command script import <repo>/frida/lldb_cap.py" \
    -o "cap_setup" \
    -o "continue" \
    -o "detach" -o "quit"
  然后外部 sleep N; kill -INT <lldb-pid>  -> continue 返回 -> 自动 detach/quit

断点回调只写文件并 auto-continue，不参与流程控制。
"""
import json
import os
import time

import lldb

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "http.jsonl")
T0 = time.time()
N = {"hits": 0, "resp": 0}

GET_SYMS = [
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
POST_SYMS = [
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

_HAVE = {}


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
    if not b or b.GetSize() != size:
        return None
    return bytes(b)


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
    return d, int.from_bytes(hdr[4:8], "little", signed=True), \
        int.from_bytes(hdr[16:24], "little", signed=True)


def qstring(proc, obj_addr):
    r = _qarray(proc, obj_addr)
    if not r:
        return None
    d, size, offset = r
    if size < 0 or size > (1 << 22):
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


ARGREGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]


def reg(frame, name):
    r = frame.FindRegister(name)
    return r.GetValueAsUnsigned() if r.IsValid() else 0


def find_url(frame, proc):
    """成员函数: rdi=this, 若有 sret 还要再挪一位。自动探测。"""
    for i in range(0, 5):
        v = qstring(proc, reg(frame, ARGREGS[i]))
        if v and len(v) > 4 and (v.startswith("http") or "/" in v):
            return i, v
    return None, None


def _post_body(frame, proc, idx):
    if idx is None or idx + 1 >= 6:
        return None, None
    for j in (idx + 1, idx + 2):
        if j >= 6:
            break
        ba = qbytearray(proc, reg(frame, ARGREGS[j]))
        if ba:
            return len(ba), ba[:3000].decode("utf-8", "replace")
    return None, None


def make_cb(method):
    def cb(frame, bp_loc, internal_dict):
        try:
            proc = frame.GetThread().GetProcess()
            idx, url = find_url(frame, proc)
            rec = {"kind": "req", "method": method, "url": url, "argidx": idx,
                   "sym": bp_loc.GetBreakpoint().GetName()}
            if url is None and N["hits"] < 30:
                rec["raw"] = {r: hex(reg(frame, r)) for r in ARGREGS[:5]}
            elif method == "POST":
                blen, btxt = _post_body(frame, proc, idx)
                if blen is not None:
                    rec["body_len"], rec["body"] = blen, btxt
            N["hits"] += 1
            log(rec)
        except Exception as e:
            log({"kind": "err", "err": repr(e)})
        return False

    return cb


def make_resp_cb(frame_tag):
    def cb(frame, bp_loc, internal_dict):
        try:
            proc = frame.GetThread().GetProcess()
            ba = qbytearray(proc, reg(frame, "rsi"))
            rec = {"kind": frame_tag}
            if ba is not None:
                rec["len"] = len(ba)
                rec["preview"] = ba[:4000].decode("utf-8", "replace")
            N["resp"] += 1
            log(rec)
        except Exception as e:
            log({"kind": "err", "err": repr(e)})
        return False

    return cb


def make_status_cb():
    def cb(frame, bp_loc, internal_dict):
        log({"kind": "status", "code": reg(frame, "rsi") & 0xFFFFFFFF})
        return False

    return cb


def _mk(name, fn):
    g = globals()
    if name not in g:
        g[name] = fn
    return "lldb_cap." + name


def cap_setup(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    if not target or not target.GetProcess().IsValid():
        print("CAP: no process"); return
    _HAVE["cbGET"] = _mk("_cbGET", make_cb("GET"))
    _HAVE["cbPOST"] = _mk("_cbPOST", make_cb("POST"))
    _HAVE["cbRESP"] = _mk("_cbRESP", make_resp_cb("respbody"))
    _HAVE["cbSTATUS"] = _mk("_cbSTATUS", make_status_cb())

    n = 0
    miss = []
    for sym in GET_SYMS:
        bp = target.BreakpointCreateByName(sym)
        if bp.GetNumLocations() > 0:
            bp.SetScriptCallbackFunction(_HAVE["cbGET"]); bp.SetAutoContinue(True); n += 1
        else:
            miss.append(sym)
    for sym in POST_SYMS:
        bp = target.BreakpointCreateByName(sym)
        if bp.GetNumLocations() > 0:
            bp.SetScriptCallbackFunction(_HAVE["cbPOST"]); bp.SetAutoContinue(True); n += 1
        else:
            miss.append(sym)
    for sym, key in [("_ZN4qtng12HttpResponse7setBodyERK10QByteArray", "cbRESP"),
                     ("_ZN4qtng12HttpResponse13setStatusCodeEi", "cbSTATUS")]:
        bp = target.BreakpointCreateByName(sym)
        if bp.GetNumLocations() > 0:
            bp.SetScriptCallbackFunction(_HAVE[key]); bp.SetAutoContinue(True); n += 1
        else:
            miss.append(sym)

    log({"kind": "info", "msg": "breakpoints=%d missed=%s" % (n, miss)})
    print("CAP: breakpoints=%d missed=%d" % (n, len(miss)), flush=True)


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_cap.cap_setup cap_setup")
    print("lldb_cap loaded ->", LOG_PATH, flush=True)
