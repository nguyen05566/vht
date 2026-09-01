#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QUÉT DẢI TK -> TÌM TK HỢP LỆ (gamevh.net)
======================================================
Dùng khi có VÀI NGHÌN tk dạng "tên + số thứ tự" (vd test1..test3000):
  - Thử đăng nhập HTTP từng tk (nhanh, 2 request).
  - Tk đăng nhập OK -> kiểm tra KẾT NỐI WEBSOCKET (login WS thành công).
  - Tk HỢP LỆ (HTTP OK + WS OK) -> ghi vào acc_valid.txt.
  - Tk SAI / không tồn tại -> bỏ qua, ghi vào acc_invalid.txt (có lý do).
  - Có thể lấy kèm số dư x + số lượt quay còn lại của từng acc hợp lệ.

Cách dùng:
  python3 discover_acc.py --prefix test --start 1 --end 3000 --pwd 
  python3 discover_acc.py --prefix test --start 1 --end 3000 --no-ws --workers 40 --fast
"""
import argparse
import csv
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import websocket

# ===== CẤU HÌNH =====
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION = "5.0.2"
GAME_ID = "caro"
CMD_LOGIN, CMD_PONG, CMD_PING = 302, 300, 301
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


def pack_num(cmd, payload=b""):  return struct_pack(">H", cmd) + payload
def struct_pack(fmt, v):         return __import__("struct").pack(fmt, v)
def i32(v): return struct_pack(">i", v)
def i8(v):  return struct_pack(">b", v)
def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


class Reader:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0
    def rem(self): return len(self.d) - self.p
    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0; self.p += 1; return v
    def i8(self):
        v = __import__("struct").unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i32(self):
        v = __import__("struct").unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4; return v
    def utf16(self):
        n = self.i16()
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s
    def i16(self):
        v = __import__("struct").unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2; return v


def parse_frame(raw):
    if not raw: return None, None
    first = __import__("struct").unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


def get_balance(sess):
    try:
        prof = sess.get(PROFILE_URL, timeout=10)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                      prof.text)
        return int(re.sub(r"[^\d]", "", m.group(1)) or 0) if m else 0
    except Exception:
        return 0


def http_check(user, pwd, with_balance=True):
    """Đăng nhập HTTP nhanh. Trả (True, info) hoặc (False, reason)."""
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
        s.get(LOGIN_URL, timeout=12)
        r = s.post(LOGIN_URL, timeout=12,
                   data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                         "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                   headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                   allow_redirects=True)
        if "login.jsp" in r.url:
            return False, "login_fail"
        g = s.get(GAME_URL, timeout=12)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        if not tm or int(tm.group(1)) == 0:
            return False, "no_token"
        mm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
        nick = mm.group(1).strip() if mm else user
        cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
        info = {"token": int(tm.group(1)), "cookie": cookie, "nick": nick,
                "balance": get_balance(s) if with_balance else 0}
        return True, info
    except Exception as e:
        return False, f"err:{type(e).__name__}"


def ws_check(info):
    """Kết nối WebSocket + login. Trả True/False. (acc hợp lệ theo tiêu chí WS)"""
    ws = None
    try:
        ws = websocket.create_connection(WS_URL, timeout=10,
                                         header=[f"Cookie: {info['cookie']}",
                                                 "Origin: https://gamevh.net",
                                                 f"User-Agent: {UA}"],
                                         cookie=info["cookie"])
        ws.send_binary(pack_num(CMD_LOGIN, asc(info["nick"]) + i32(info["token"])
                                + asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))
        deadline = time.time() + 8
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            name, rd = parse_frame(raw)
            if name == CMD_PING or name == "PING":
                ws.send_binary(pack_num(CMD_PONG)); continue
            if name == CMD_LOGIN or name == "LOGIN":
                if rd.i8() == 0:
                    return True
                break
        return False
    except Exception:
        return False
    finally:
        if ws:
            try: ws.close()
            except Exception: pass


def ws_remain(info, timeout=8):
    """Hỏi só lượt quay còn lại (optional). Trả remain hoặc None."""
    ws = None
    try:
        ws = websocket.create_connection(WS_URL, timeout=10,
                                         header=[f"Cookie: {info['cookie']}",
                                                 "Origin: https://gamevh.net",
                                                 f"User-Agent: {UA}"],
                                         cookie=info["cookie"])
        ws.send_binary(pack_num(CMD_LOGIN, asc(info["nick"]) + i32(info["token"])
                                + asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))
        deadline = time.time() + 8
        logged = False
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            name, rd = parse_frame(raw)
            if name == CMD_PING or name == "PING":
                ws.send_binary(pack_num(CMD_PONG)); continue
            if name == CMD_LOGIN or name == "LOGIN":
                if rd.i8() == 0:
                    logged = True
                    b = "GET_REMAIN_SPIN".encode("ascii")
                    ws.send_binary(bytes([(-len(b)) & 0xFF]) + b)
            if logged and name == "GET_REMAIN_SPIN":
                rd.i8()
                return rd.i32()
        return None
    except Exception:
        return None
    finally:
        if ws:
            try: ws.close()
            except Exception: pass


def scan_one(user, pwd, verify_ws, with_balance, with_remain):
    ok, info = http_check(user, pwd, with_balance)
    rec = {"user": user, "http": ok, "reason": "" if ok else info,
           "ws": None, "balance": 0, "remain": None}
    if ok:
        rec["balance"] = info["balance"]
        if verify_ws:
            rec["ws"] = ws_check(info)
            if with_remain and rec["ws"]:
                rec["remain"] = ws_remain(info)
    return rec


def main():
    ap = argparse.ArgumentParser(description="Quét dải tk tìm acc hợp lệ")
    ap.add_argument("--prefix", default="test", help="tên đầu (vd test)")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=3000)
    ap.add_argument("--exclude", default="", help="số cần loại, vd: 1,25")
    ap.add_argument("--password", "--pwd", default="")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--no-ws", action="store_true", help="chỉ check HTTP, không check WebSocket")
    ap.add_argument("--fast", action="store_true", help="không đọc số dư (nhanh hơn)")
    ap.add_argument("--remain", action="store_true", help="cũng lấy số lượt quay còn lại")
    ap.add_argument("--out-valid", default="acc_valid.txt")
    ap.add_argument("--out-invalid", default="acc_invalid.txt")
    ap.add_argument("--out-csv", default="scan_report.csv")
    args = ap.parse_args()

    excl = {int(x) for x in args.exclude.split(",") if x.strip().isdigit()}
    users = [f"{args.prefix}{i}" for i in range(args.start, args.end + 1) if i not in excl]
    print(f"🔍 Quét {len(users)} ứng viên: {args.prefix}{args.start}..{args.prefix}{args.end}"
          f"{' (bỏ ' + str(sorted(excl)) + ')' if excl else ''} | "
          f"{args.workers} luồng | verify_ws={'OFF' if args.no_ws else 'ON'}\n")

    t0 = time.time()
    lock = threading.Lock()
    done = {"n": 0}

    def log(msg):
        with lock:
            done["n"] += 1
            if done["n"] % 100 == 0 or msg.startswith("!!"):
                print(f"  [{done['n']}/{len(users)}] {msg}", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(scan_one, u, args.password, not args.no_ws,
                          not args.fast, args.remain) for u in users]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            with lock:
                done["n"] += 0
            if not r["http"]:
                log(f"!! {r['user']}: loại ({r['reason']})")
            else:
                log(f"✅ {r['user']}: ws={r['ws']} balance={r['balance']} remain={r['remain']}")

    wall = time.time() - t0
    valid = [r for r in results if r["http"] and (args.no_ws or r["ws"])]
    invalid = [r for r in results if r not in valid]
    print("\n" + "=" * 70)
    print(f"KẾT QUẢ QUÉT: {len(results)} ứng viên | {len(valid)} HỢP LỆ | "
          f"{len(invalid)} KHÔNG HỢP LỆ | {wall:.0f}s ({wall/len(users)*1000:.0f}ms/acc)")
    print(f"  - HTTP fail: {sum(1 for r in results if not r['http'])}")
    print(f"  - WS fail  : {sum(1 for r in results if r['http'] and not args.no_ws and not r['ws'])}")
    print("=" * 70)

    with open(args.out_valid, "w") as f:
        for r in sorted(valid, key=lambda x: x["user"]):
            f.write(f"{r['user']}\n")
    with open(args.out_invalid, "w") as f:
        for r in sorted(invalid, key=lambda x: x["user"]):
            f.write(f"{r['user']}\t{r['reason']}\n")
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user", "http_ok", "ws_ok", "balance", "remain", "reason"])
        for r in sorted(results, key=lambda x: x["user"]):
            w.writerow([r["user"], r["http"], r["ws"], r["balance"],
                        r["remain"] if r["remain"] is not None else "",
                        r["reason"]])
    print(f"💾  {args.out_valid} ({len(valid)} acc) | {args.out_invalid} | {args.out_csv}")


if __name__ == "__main__":
    main()
