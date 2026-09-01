#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Khám phá logic "Vòng quay may mắn" (Lucky Wheel) - gamevh.net qua WebSocket arena.

Cơ chế tìm được từ client GWT (wc/20240721/html/....cache.js):
  - Lệnh WS:   GET_REMAIN_SPIN      (không tham số) -> trả về số lượt quay còn lại (int32)
  - Lệnh WS:   SPIN_LUCKY_WHEEL     (không tham số) -> thực hiện quay 1 lượt
                phản hồi: [u8 result][string utf-16][int32 reward]
  - Lệnh WS:   BUY_ITEM 'WOODEN_WHEEL' + u8 count   -> mua thêm lượt (tốn x, KHÔNG dùng)
  - Nút quay hiển thị ở sảnh chính (icon ic_vqmm).

Cách dùng:
  python3 lucky_wheel_probe.py --user ngan4 --pwd 
  python3 lucky_wheel_probe.py --user ngan4 --pwd  --spin   # quay thử 1 lượt
  python3 lucky_wheel_probe.py --list uploads/acc2.txt --spin-off     # quét nhiều acc
"""
import argparse
import json
import re
import struct
import sys
import time

import requests
import websocket

# ==================== CẤU HÌNH ====================
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
GAME_ID = "xiangqi"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

# ==================== FRAMING (giống arena1.py) ====================
class Conn:
    @staticmethod
    def pack(cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)   # 1 byte: độ dài âm (chuỗi ASCII)
            result.extend(cmd_bytes)
        result.extend(data)
        return bytes(result)

    @staticmethod
    def pack_byte(v):    return struct.pack('>b', v)
    @staticmethod
    def pack_byte_u(v):  return struct.pack('>B', v)
    @staticmethod
    def pack_int(v):     return struct.pack('>i', v)
    @staticmethod
    def pack_ascii(s):
        e = s.encode('ascii')[:255]
        return struct.pack('>b', len(e)) + e
    @staticmethod
    def pack_string(s):  # UTF-16BE, 2-byte length
        e = s.encode('utf-16-be')
        return struct.pack('>h', len(e) // 2) + e


class Reader:
    """Nhị phân payload: đọc theo đúng primitive của client GWT."""
    def __init__(self, data):
        self.data = bytes(data)
        self.off = 0

    def byte(self):
        v = self.data[self.off]; self.off += 1
        return v

    def ubyte(self):
        return self.byte() & 0xFF

    def short(self):   # oNf: 2 bytes BE có dấu
        v = struct.unpack_from('>h', self.data, self.off)[0]; self.off += 2
        return v

    def int32(self):   # lNf: 4 bytes BE
        v = struct.unpack_from('>i', self.data, self.off)[0]; self.off += 4
        return v

    def long(self):    # mNf: 8 bytes BE
        v = struct.unpack_from('>q', self.data, self.off)[0]; self.off += 8
        return v

    def ascii(self):   # jNf: 1-byte length + ASCII
        n = self.ubyte()
        s = self.data[self.off:self.off + n].decode('ascii', 'replace'); self.off += n
        return s

    def utf16(self):   # pNf: 2-byte length + UTF-16BE
        n = self.short()
        s = self.data[self.off:self.off + n * 2].decode('utf-16-be', 'replace'); self.off += n * 2
        return s

    def rest(self):
        r = self.data[self.off:]; self.off = len(self.data)
        return r

    def hexdump(self):
        return ' '.join(f'{b:02X}' for b in self.data)


# ==================== ĐĂNG NHẬP HTTP (giống arena1.py) ====================
def fetch_session_info(user, pwd):
    """Đăng nhập HTTP -> cookies + token + nickname + playerId."""
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7"})
    session.get(LOGIN_URL, timeout=20)
    resp = session.post(
        LOGIN_URL, timeout=20,
        data={"redirect": "/", "USER_NAME": user, "PWD": pwd,
              "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
        headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL,
                 "Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True)
    if "login.jsp" in resp.url:
        return None, "login fail"
    game = session.get(GAME_URL, timeout=20)
    html = game.text
    m = re.search(r"var\s+token\s*=\s*(-?\d+)", html)
    if not m:
        return None, "no token"
    token = int(m.group(1))
    nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", html)
    nickname = nm.group(1).strip() if nm else user
    pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", html)
    cookie = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
    return {
        "cookie": cookie,
        "token": token,
        "nickname": nickname,
        "playerId": int(pid.group(1)) if pid else 0,
    }, "ok"


# ==================== WS PROBE ====================
class WheelProbe:
    def __init__(self, user, pwd, do_spin=False, force_spin=False, timeout=12):
        self.user, self.pwd = user, pwd
        self.do_spin = do_spin
        self.force_spin = force_spin
        self.timeout = timeout
        self.info = None
        self.conn = Conn()
        self.ws = None
        self.result = {}

    def send(self, cmd, data=b''):
        if self.ws and self.ws.connected:
            self.ws.send(self.conn.pack(cmd, data), opcode=0x2)

    def run(self):
        info, status = fetch_session_info(self.user, self.pwd)
        if not info:
            print(f"[{self.user}] ❌ Đăng nhập HTTP thất bại: {status}")
            return None
        self.info = info
        print(f"[{self.user}] ✅ HTTP login: nickname={info['nickname']} "
              f"playerId={info['playerId']} token={info['token']}")

        self.ws = websocket.WebSocket()
        self.ws.connect(WS_URL, cookie=info["cookie"], origin="https://gamevh.net",
                        timeout=self.timeout)
        print(f"[{self.user}] 🔌 WS connected")
        self._send_login()

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except Exception as e:
                print(f"[{self.user}] WS recv err: {e}")
                break
            if isinstance(raw, str):
                raw = raw.encode()
            if not raw:
                continue
            self._handle(raw)
            if self.result.get("done"):
                break
        try:
            self.ws.close()
        except Exception:
            pass
        return self.result

    def _send_login(self):
        i = self.info
        data = bytearray()
        data.extend(self.conn.pack_ascii(i["nickname"]))
        data.extend(self.conn.pack_int(i["token"]))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send("LOGIN", bytes(data))

    def _handle(self, raw):
        # tách lệnh: byte đầu < 0 -> chuỗi ASCII
        cmd = None
        payload = raw
        first = struct.unpack_from('>b', raw, 0)[0]
        if first < 0:
            n = -first
            cmd = raw[1:1 + n].decode('ascii', 'replace')
            payload = raw[1 + n:]
        else:
            cmd = "0x" + raw[:2].hex()
            payload = raw[2:]
        print(f"[{self.user}] << {cmd} payload({len(payload)}B): {payload.hex()}")

        if cmd == "PING":
            self.send("PONG")
            return
        if cmd == "LOGIN":
            r = Reader(payload)
            status = r.byte()
            if status != 0:
                print(f"[{self.user}] LOGIN status={status}")
                self.result["done"] = True
                self.result["login_status"] = status
                return
            # đăng nhập OK -> thử hỏi lượt quay ngay
            self._query_remain()
            return
        if cmd == "GET_REMAIN_SPIN":
            # Phản hồi thực tế (5 bytes): [u8 status=0][int32 remain]
            #   - ngan4..ngan100 (tk mới): 00 00 00 00 01 -> 1 lượt
            #   - arena1:                          00 00 00 00 03 -> 3 lượt
            # (ky tự GWT đã xác nhận: sau khi strip byte status, lNf đọc int32)
            r = Reader(payload)
            status = r.byte()
            try:
                remain = r.int32()
            except Exception:
                remain = None
            print(f"[{self.user}] 🎡 GET_REMAIN_SPIN -> status={status}, "
                  f"{remain} lượt còn lại (raw: {payload.hex()})")
            self.result["remain_status"] = status
            self.result["remain"] = remain
            self.result["remain_raw"] = payload.hex()
            if self.do_spin and (self.force_spin or remain not in (None, 0)):
                time.sleep(0.6)
                print(f"[{self.user}] 🎰 Gửi SPIN_LUCKY_WHEEL (quay thử)...")
                self.send("SPIN_LUCKY_WHEEL")
            else:
                self.result["done"] = True
            return
        if cmd == "SPIN_LUCKY_WHEEL":
            # Phản hồi quay thực tế (20 bytes), ví dụ:
            #   00 06 0006 "150 x" 00000096
            # -> [u8 result=0 (thành công)][u8 ô/segment=6][string UTF-16 "150 x"][int32 150]
            out = {"raw": payload.hex()}
            try:
                r = Reader(payload)
                result_code = r.byte()
                slot = r.ubyte()
                desc = r.utf16()
                reward = r.int32()
                out.update(result=result_code, slot=slot, desc=desc, reward=reward)
                print(f"[{self.user}] 🎰 KẾT QUẢ QUAY: result={result_code} "
                      f"slot={slot} | phần thưởng={desc!r} (={reward})")
            except Exception:
                # fallback: [u8 result][utf16][int32]
                try:
                    r = Reader(payload)
                    result_code = r.byte()
                    desc = r.utf16()
                    reward = r.int32()
                    out.update(result=result_code, desc=desc, reward=reward)
                    print(f"[{self.user}] 🎰 KẾT QUẢ QUAY: result={result_code} "
                          f"phần thưởng={desc!r} (={reward})")
                except Exception as e:
                    out["parse_error"] = str(e)
                    print(f"[{self.user}] 🎰 raw spin response (chưa parse hết): {payload.hex()}")
            self.result["spin"] = out
            self.result["done"] = True
            return
        # các lệnh khác: bỏ qua, nhưng in ra
        if cmd in ("ENTER_PLACE", "SET_CLIENT_MODE", "CONFIG", "BROADCAST",
                   "ALERT", "LIST_BET_AMT"):
            r = Reader(payload)
            if cmd == "ENTER_PLACE" and payload:
                st = r.byte()
                print(f"[{self.user}] ENTER_PLACE status={st}")
                if st == 0:
                    self.send("GET_REMAIN_SPIN")

    def _query_remain(self):
        print(f"[{self.user}] 🔍 Gửi GET_REMAIN_SPIN...")
        self.send("GET_REMAIN_SPIN")


# ==================== MAIN ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=None)
    ap.add_argument("--pwd", default="")
    ap.add_argument("--list", default="uploads/acc2.txt", help="file danh sách acc")
    ap.add_argument("--spin", action="store_true", help="gửi lệnh quay thử SPIN_LUCKY_WHEEL")
    ap.add_argument("--force-spin", action="store_true",
                    help="luôn gửi SPIN_LUCKY_WHEEL kể cả khi số lượt = 0 (để xem phản hồi)")
    ap.add_argument("--max", type=int, default=3, help="số acc tối đa khi dùng --list")
    ap.add_argument("--timeout", type=int, default=10)
    args = ap.parse_args()

    if args.user:
        users = [args.user]
    else:
        users = []
        try:
            for line in open(args.list, encoding="utf-8"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                users.append(line.split("\t")[0])
        except FileNotFoundError:
            print(f"Không đọc được {args.list}")
            return
        users = users[:args.max]

    results = {}
    for u in users:
        p = WheelProbe(u, args.pwd, do_spin=args.spin, force_spin=args.force_spin,
                       timeout=args.timeout)
        try:
            res = p.run()
        except Exception as e:
            print(f"[{u}] ❌ Lỗi: {e}")
            res = None
        if res:
            results[u] = res
        print("-" * 70)
        time.sleep(1.0)

    print("\n================= TỔNG KẾT =================")
    for u, r in results.items():
        print(f"{u}: lượt còn lại = {r.get('remain')} | spin = {r.get('spin')}")


if __name__ == "__main__":
    main()
