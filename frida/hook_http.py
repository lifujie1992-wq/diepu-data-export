#!/usr/bin/env python3
"""把 hook_http.js 注入 cicada，把所有 qtng HTTP 活动写成 JSONL。

用法:
    python frida/hook_http.py                # 附加到正在运行的 cicada，记录到 frida/http_log.jsonl
    python frida/hook_http.py --seconds 120  # 记录 120 秒后退出
"""
import sys, os, json, time, argparse

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "hook_http.js")
TARGET_HINT = "蝶普电商管理平台"


def find_pid(dev):
    for p in dev.enumerate_processes():
        if p.name == TARGET_HINT:
            return p.pid
    raise SystemExit(f"找不到进程 {TARGET_HINT}; 先启动 App")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=0, help="0 = 一直跑")
    ap.add_argument("--out", default=os.path.join(HERE, "http_log.jsonl"))
    args = ap.parse_args()

    dev = frida.get_local_device()
    pid = find_pid(dev)
    print(f"[+] attaching pid={pid}")

    session = dev.attach(pid)
    script = session.create_script(open(AGENT, encoding="utf-8").read())

    fh = open(args.out, "a", encoding="utf-8")
    t0 = time.time()

    def on_message(message, data):
        if message["type"] == "send":
            rec = message["payload"]
            rec["t"] = round(time.time() - t0, 3)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            k = rec.get("kind")
            if k == "req":
                print(f"  -> {rec.get('method')} {rec.get('url')}")
            elif k == "seturl":
                print(f"  .. setUrl {rec.get('url')}")
            elif k == "respbody":
                print(f"  <- resp len={rec.get('len')} sha={str(rec.get('sha256'))[:12]}")
            elif k in ("hooked", "miss", "hookfail", "warn", "info"):
                print(f"  [{k}] {rec.get('name') or rec.get('msg') or rec.get('err')}")
        else:
            print("  !!", json.dumps(message, ensure_ascii=False)[:300])

    script.on("message", on_message)
    script.load()
    print(f"[+] agent loaded, logging -> {args.out}")

    try:
        if args.seconds:
            time.sleep(args.seconds)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
        try:
            session.detach()
        except Exception:
            pass
        print("[+] detached")


if __name__ == "__main__":
    main()
