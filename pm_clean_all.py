#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DỌN HỘP THƯ PM (tin nhắn nhận x) cho tk gamevh.net
===========================================================
Cách hoạt động (đã kiểm chứng 2026-08-31 với  - 5456 tin):
1) WebSocket PM.LIST   : đếm & xem tin (cấu trúc [i64 time][i64 fromId]
                         [utf16 name][u16 len][utf16 content][u8 read])
2) HTTP pm_remove_all  : XÓA TẤT CẢ tin đến (1 request là sạch!)
   GET /com/ftl/game/pm/pm_remove_all.jsp?position=inbox
3) Kiểm tra lại bằng cả 2 nguồn.

Kết quả thực tế: 5456 tin "You have received N coin from testXXX"
-> 0 tin. Hộp thư còn 6 tin chat khác (lời mời kết bạn, không phải tin nhận x).

Chú ý về WebSocket DELETE:
  - Client gửi PM.DELETE = [u8 3][i64 ...] nhưng id tin nhận x trả về là
    TIMESTAMP BATCH (giống nhau cho cả nhóm tin) -> xóa theo id này không được.
  - Endpoint HTTP pm_remove_all.jsp hiệu quả hơn nhiều (xóa cả hộp).
"""
import argparse
import re
import struct
import time

import requests
import websocket

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PM_URL = "https://gamevh.net/com/ftl/game/pm/pm.jsp"
PM_BY_POS_URL = "https://gamevh.net/com/ftl/game/pm/pm_by_position.jsp?excludeLayout=true&position=inbox&start=0"
PM_REMOVE_ALL_URL = "https://gamevh.net/com/ftl/game/pm/pm_remove_all.jsp?position=inbox"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"


def http_login(user, pwd):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    s.get(LOGIN_URL, timeout=15)
    r = s.post(LOGIN_URL, timeout=15,
               data={"redirect": "/", "USER_NAME": user, "PWD": pwd,
                     "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
               headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
               allow_redirects=True)
    if "login.jsp" in r.url:
        return None
    g = s.get(GAME_URL, timeout=15)
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
    mm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
    pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", g.text)
    return {"token": int(tm.group(1)) if tm else 0,
            "nick": mm.group(1).strip() if mm else user,
            "playerId": int(pid.group(1)) if pid else 0,
            "cookie": "; ".join(f"{k}={v}" for k, v in s.cookies.items()),
            "session": s}


# ===== WS helpers =====
def pack_num(cmd, payload=b""):  return struct.pack(">H", cmd) + payload
def i64(v): return struct.pack(">q", v)
def i32(v): return struct.pack(">i", v)
def i16(v): return struct.pack(">h", v)
def i8(v):  return struct.pack(">b", v)
def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e

def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return bytes([(-len(b)) & 0xFF]) + b + payload


class Reader:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0
    def rem(self): return len(self.d) - self.p
    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0; self.p += 1; return v
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4; return v
    def i64(self):
        v = struct.unpack_from(">q", self.d, self.p)[0] if self.p + 8 <= len(self.d) else 0
        self.p += 8; return v
    def utf16(self):
        n = self.i16()
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s


def parse_frame(raw):
    if not raw: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


def pm_list_ws(info, page=0, count=15):
    """Đếm & lấy tin qua WebSocket: PM.LIST [u8 2][u8 page][u8 count]."""
    ws = websocket.create_connection(WS_URL, timeout=12,
        header=[f"Cookie: {info['cookie']}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
        cookie=info["cookie"])
    ws.send_binary(pack_num(302, asc(info["nick"]) + i32(info["token"]) + asc("5.0.2")
                            + asc("") + asc("caro") + i8(1)))
    t0 = time.time()
    logged = False
    while time.time() - t0 < 8:
        raw = ws.recv()
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == 301 or name == "PING":
            ws.send_binary(pack_num(300)); continue
        if name == 302 or name == "LOGIN":
            if rd.i8() == 0:
                logged = True
                ws.send_binary(pack_str("PM.LIST", i8(2) + i8(page) + i8(count)))
        if logged and name == "PM.LIST":
            st = rd.i8()
            cnt = rd.u8()
            msgs = []
            for _ in range(cnt):
                tms = rd.i64()
                fid = rd.i64()
                nm = rd.utf16()
                content = rd.utf16()
                read = rd.u8() if rd.rem() > 0 else -1
                msgs.append((tms, fid, nm, content, read))
            ws.close()
            return msgs
    ws.close()
    return None


def main():
    ap = argparse.ArgumentParser(description="Dọn hộp thư PM (tin nhận x)")
    ap.add_argument("--user", default="")
    ap.add_argument("--pwd", default="")
    ap.add_argument("--dry-run", action="store_true", help="chỉ đếm, không xóa")
    args = ap.parse_args()

    info = http_login(args.user, args.pwd)
    if not info:
        print("❌ Login thất bại"); return
    print(f"✅ Login: {info['nick']} (pid={info['playerId']})")

    # 1) Đếm qua WS
    print("\n🔍 WebSocket PM.LIST ...")
    msgs = pm_list_ws(info)
    if msgs is None:
        print("   không nhận được PM.LIST"); return
    print(f"   WS: {len(msgs)} tin hiển thị (tối đa 15/trang)")
    coin_msgs = [m for m in msgs if "received" in m[3].lower() or "coin" in m[3].lower()]
    other = [m for m in msgs if m not in coin_msgs]
    if coin_msgs:
        print(f"   - {len(coin_msgs)} tin NHẬN X (vd: {coin_msgs[0][3][:60]!r})")
    if other:
        print(f"   - {len(other)} tin khác: {[(m[2], m[3][:45]) for m in other[:5]]}")

    # 2) Đếm unread qua HTTP
    s = info["session"]
    r = s.get(PM_URL, timeout=15)
    big = [n for n in re.findall(r'>(\d+)<', r.text) if n.isdigit() and int(n) > 100]
    print(f"   HTTP pm.jsp: số unread lớn = {big if big else '0 / không thấy'}")

    if args.dry_run:
        print("\n⚠️  DRY-RUN - không xóa. Dùng --execute để xóa tất cả!")
        return

    # 3) XÓA TẤT CẢ (HTTP endpoint)
    print("\n🗑️  GET pm_remove_all.jsp?position=inbox ...")
    r = s.get(PM_REMOVE_ALL_URL,
              headers={"Referer": PM_URL}, timeout=30, allow_redirects=False)
    print(f"   HTTP {r.status_code} -> Location: {r.headers.get('Location', '-')}")

    time.sleep(2)

    # 4) Kiểm tra lại
    print("\n✅ Kiểm tra lại:")
    msgs2 = pm_list_ws(info)
    if msgs2 is not None:
        print(f"   WS PM.LIST: {len(msgs2)} tin còn lại")
        for m in msgs2[:6]:
            print(f"     - {m[2]!r}: {m[3][:70]!r}")
    r2 = s.get(PM_URL, timeout=15)
    big2 = [n for n in re.findall(r'>(\d+)<', r2.text) if n.isdigit() and int(n) > 100]
    print(f"   HTTP pm.jsp unread: {big2 if big2 else '0'}")


if __name__ == "__main__":
    main()
