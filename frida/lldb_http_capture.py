"""lldb 版 qtng HTTP 抓取。

用法:
  lldb -b -p <pid> \
    -o "command script import <repo>/frida/lldb_http_capture.py" \
    -o "http_cap_start 45" \
    -o "continue" \
    -o "detach" -o "quit"

45 = 抓 45 秒后自动停下。
"""
import json
import os
import threading
import time

import lldb

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lldb_http.jsonl")
MAX_PREVIEW = 4000

_state = {"t0": time.time(), "hits": 0, "errors": 0}


def _log(obj):
    obj["t"] = round(time.time() - _state["t0"], 3)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _proc():
    return lldb.debugger.GetSelectedTarget().GetProcess()


def _read_mem(proc, addr, size, err):
    if addr == 0 or size <= 0 or size > (1 << 28):
        return None
    b = proc.ReadMemory(addr, size, err)
    return bytes(b) if b else None


def _read_ptr(proc, addr, err):
    b = _read_mem(proc, addr, 8, err)
    return int.from_bytes(b, "little") if b else 0


def _qarray(proc, obj_addr, err):
    """QArrayData: ref(i32) size(i32) alloc(u32)+reserved [pad] offset(i64) -> data"""
    d = _read_ptr(proc, obj_addr, err)
    if d == 0:
        return None
    hdr = _read_mem(proc, d, 24, err)
    if not hdr or len(hdr) < 24:
        return None
    size = int.from_bytes(hdr[4:8], "little", signed=True)
    offset = int.from_bytes(hdr[16:24], "little", signed=True)
    return d, size, offset


def _qstring(proc, obj_addr, err):
    r = _qarray(proc, obj_addr, err)
    if not r:
        return None
    d, size, offset = r
    if size <= 0:
        return ""
    if size > (1 << 22):
        return None
    raw = _read_mem(proc, d + offset, size * 2, err)
    return raw.decode("utf-16-le", "replace") if raw else None


def _qbytearray(proc, obj_addr, err):
    r = _qarray(proc, obj_addr, err)
    if not r:
        return None
    d, size, offset = r
    if size <= 0:
        return b""
    if size > (1 << 28):
        return None
    return _read_mem(proc, d + offset, size, err)


def _preview(blob, n=MAX_PREVIEW):
    if blob is None:
        return None
    s = blob[:n].decode("utf-8", "replace")
    return s


def _arg(frame, i):
    """x86_64 SysV: rdi, rsi, rdx, rcx, r8, r9。
    注意：成员函数第一个参数是 this，所以真实第 k 个参数在索引 k+1。"""
    regs = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]
    r = frame.FindRegister(regs[i])
    return r.GetValueAsUnsigned() if r.IsValid() else 0


WINDOW = {"until": 0.0}


def _timeup():
    return WINDOW["until"] and time.time() > WINDOW["until"]


# lldb 的 SetScriptCallbackFunction 只接受「函数名字符串」，所以要生成具名模块级函数
def _make_url_cb(site, method):
    def cb(frame, bp_loc, internal_dict):
        try:
            proc = _proc()
            err = lldb.SBError()
            url = _qstring(proc, _arg(frame, 1), err)      # rsi = 第1个真实参数
            rec = {"kind": "req", "method": method, "url": url, "site": site}
            if method == "POST":
                ba = _qbytearray(proc, _arg(frame, 2), err)  # rdx
                if ba is not None:
                    rec["body_len"] = len(ba)
                    rec["body_preview"] = _preview(ba, 2000)
            _log(rec)
        except Exception as e:
            _state["errors"] += 1
            _log({"kind": "err", "where": site, "err": str(e)})
        return not _timeup()
    return cb


def _resp_body_cb(frame, bp_loc, internal_dict):
    try:
        proc = _proc()
        err = lldb.SBError()
        ba = _qbytearray(proc, _arg(frame, 1), err)   # rsi
        rec = {"kind": "respbody"}
        if ba is not None:
            rec["len"] = len(ba)
            rec["preview"] = _preview(ba, MAX_PREVIEW)
        _log(rec)
    except Exception as e:
        _log({"kind": "err", "where": "respbody", "err": str(e)})
    return not _timeup()


def _status_cb(frame, bp_loc, internal_dict):
    _log({"kind": "status", "code": _arg(frame, 1) & 0xFFFFFFFF})  # rsi 低32位
    return not _timeup()


SYMS = [
    ("_ZN4qtng11HttpSession3getERK7QString", "GET", 0),
    ("_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_E", "GET", 0),
    ("_ZN4qtng11HttpSession3getERK7QStringRK4QMapIS1_S1_ERKS4_IS1_10QByteArrayE", "GET", 0),
    ("_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQuery", "GET", 0),
    ("_ZN4qtng11HttpSession3getERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE", "GET", 0),
    ("_ZN4qtng11HttpSession4postERK7QStringRK10QByteArray", "POST", 0),
    ("_ZN4qtng11HttpSession4postERK7QStringRK10QByteArrayRK4QMapIS1_S4_E", "POST", 0),
    ("_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQuery", "POST", 0),
    ("_ZN4qtng11HttpSession4postERK7QStringRK9QUrlQueryRK4QMapIS1_10QByteArrayE", "POST", 0),
    ("_ZN4qtng11HttpSession4postERK7QStringRK11QJsonObject", "POST", 0),
]


def http_cap_start(debugger, command, result, internal_dict):
    target = debugger.GetSelectedTarget()
    n = int(command.strip() or "45")
    created, missed = 0, []

    g = globals()
    for i, (sym, method, idx) in enumerate(SYMS):
        cb_name = "_cb_url_%d" % i
        if cb_name not in g:
            g[cb_name] = _make_url_cb(sym, method)
        bp = target.BreakpointCreateByName(sym)
        if bp.GetNumLocations() > 0:
            bp.SetScriptCallbackFunction("lldb_http_capture." + cb_name)
            created += 1
        else:
            missed.append(sym)

    for sym, cb in [("_ZN4qtng12HttpResponse7setBodyERK10QByteArray", "_resp_body_cb"),
                    ("_ZN4qtng12HttpResponse13setStatusCodeEi", "_status_cb")]:  # noqa
        bp = target.BreakpointCreateByName(sym)
        if bp.GetNumLocations() > 0:
            bp.SetScriptCallbackFunction("lldb_http_capture." + cb)
            created += 1
        else:
            missed.append(sym)

    _log({"kind": "info", "msg": "breakpoints=%d missed=%s" % (created, missed)})
    print("HTTP capture: %d breakpoints, missed=%s" % (created, missed))

    WINDOW["until"] = time.time() + n
    print("capture window: %ds" % n)
    return None


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f lldb_http_capture.http_cap_start http_cap_start")
    print("lldb_http_capture loaded; log ->", LOG_PATH)
