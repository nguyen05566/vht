#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transfer_one.py - Chuyển xu đơn lẻ từ 1 tài khoản tới BẤT KỲ Player ID nào với số xu TÙY Ý (qua WebSocket)
"""

import argparse
import os
import re
import struct
import sys
import time
import requests
import websocket

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
MIN_TRANSFER = 200

CMD_PONG = 300
CMD_PING = 301
CMD_LOGIN = 302
CMD_ALERT = 303
CMD_TRANSFER = 317
CMD_BALANCE_CHANGED = 319
CMD_GET_REMAIN_SPIN = 320
CMD_SPIN = 321

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload

def i64(v): return struct.pack(">q", v)
def i32(v): return struct.pack(">i", v)
def i8(v):  return struct.pack(">b", v)

def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e

def parse_amount(val_str):
    if not val_str:
        return 0
    s = str(val_str).strip().lower().replace(",", "").replace(" ", "")
    if s.endswith("tr"):
        try: return int(float(s[:-2]) * 1_000_000)
        except Exception: pass
    elif s.endswith("m"):
        try: return int(float(s[:-1]) * 1_000_000)
        except Exception: pass
    elif s.endswith("k"):
        try: return int(float(s[:-1]) * 1_000)
        except Exception: pass
    try:
        if s.count(".") > 1:
            s = s.replace(".", "")
        elif s.count(".") == 1 and len(s.split(".")[1]) == 3:
            s = s.replace(".", "")
        return int(float(s))
    except Exception:
        return 0


class Reader:
    def __init__(self, data):
        self.d = bytes(data)
        self.p = 0
    def rem(self): return len(self.d) - self.p
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
    def ascii(self):
        n = self.i8()
        if n < 0: n += 256
        e = min(n, self.rem())
        s = self.d[self.p:self.p + e].decode("ascii", "replace"); self.p += e
        return s

def parse_frame(raw):
    if not raw or len(raw) < 2: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n: return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


def http_login(user, passwd):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        r = sess.post(LOGIN_URL, timeout=12,
                      data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                      headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                      allow_redirects=True)
        if "login.jsp" in r.url:
            return None, "HTTP_LOGIN_FAIL"

        g = sess.get(GAME_URL, timeout=12)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        token = int(tm.group(1)) if tm else 0
        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g.text)
        nick = nm.group(1).strip() if nm else user
        pid_m = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", g.text)
        player_id = int(pid_m.group(1)) if pid_m else 0

        bal = 0
        try:
            prof = sess.get(PROFILE_URL, timeout=10)
            m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', prof.text)
            if m:
                bal = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception:
            pass

        cookie = "; ".join(f"{k}={v}" for k, v in sess.cookies.items())
        return {
            "session": sess,
            "cookie": cookie,
            "nick": nick,
            "token": token,
            "player_id": player_id,
            "balance": bal
        }, None
    except Exception as e:
        return None, f"HTTP_EXCEPTION: {e}"


def run_spin_and_transfer(user, passwd, dest_id, amount=0, percent=100, do_spin=False):
    print(f"============================================================")
    print(f"🚀 BẮT ĐẦU XỬ LÝ CHUYỂN XU CHO TÀI KHOẢN: {user}")
    print(f"🎯 Đích nhận xu (Player ID): {dest_id}")
    print(f"============================================================")

    data, err = http_login(user, passwd)
    if not data:
        print(f"❌ [LỖI] Đăng nhập thất bại: {err}")
        return False

    print(f"✅ Đăng nhập HTTP thành công!")
    print(f"   - Tên hiển thị (Nick): {data['nick']}")
    print(f"   - Player ID gửi: {data['player_id']}")
    print(f"   - Số dư ban đầu: {data['balance']:,} xu")

    ws = None
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=12,
            header=[f"Cookie: {data['cookie']}", "Origin: https://gamevh.net", f"User-Agent: {UA}"],
            cookie=data['cookie']
        )
        ws.send_binary(pack_num(CMD_LOGIN, asc(data['nick']) + i32(data['token'])
                                + asc("5.0.2") + asc("") + asc("xiangqi") + i8(1)))

        ws_ok = False
        deadline = time.time() + 10
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            cmd, rd = parse_frame(raw)
            if cmd == CMD_PING or cmd == "PING":
                ws.send_binary(pack_num(CMD_PONG))
                continue
            if cmd == CMD_LOGIN or cmd == "LOGIN":
                st = rd.i8()
                if st == 0:
                    ws_ok = True
                    break
                else:
                    print(f"❌ [LỖI] WebSocket login bị từ chối (status={st})")
                    ws.close()
                    return False

        if not ws_ok:
            print(f"❌ [LỖI] Quá thời gian chờ phản hồi WebSocket login")
            if ws: ws.close()
            return False

        print(f"✅ Kết nối WebSocket & Handshake thành công!")

        if do_spin:
            print(f"🎰 [QUAY THƯỞNG] Đang kiểm tra lượt quay may mắn...")
            ws.send_binary(pack_num(CMD_GET_REMAIN_SPIN))
            remain_spins = 0
            deadline = time.time() + 6
            while time.time() < deadline:
                raw = ws.recv()
                if not raw: continue
                cmd, rd = parse_frame(raw)
                if cmd == CMD_PING or cmd == "PING":
                    ws.send_binary(pack_num(CMD_PONG))
                    continue
                if cmd == CMD_GET_REMAIN_SPIN or cmd == "GET_REMAIN_SPIN" or cmd == 320:
                    remain_spins = rd.i32()
                    break

            print(f"   - Số lượt quay còn lại: {remain_spins}")
            spin_count = 0
            while remain_spins > 0:
                ws.send_binary(pack_num(CMD_SPIN))
                spin_done = False
                spin_deadline = time.time() + 6
                while time.time() < spin_deadline:
                    raw = ws.recv()
                    if not raw: continue
                    cmd, rd = parse_frame(raw)
                    if cmd == CMD_PING or cmd == "PING":
                        ws.send_binary(pack_num(CMD_PONG))
                        continue
                    if cmd == CMD_SPIN or cmd == "SPIN" or cmd == 321:
                        st = rd.i8()
                        prize_xu = rd.i32() if rd.rem() >= 4 else 0
                        spin_count += 1
                        print(f"   🎉 Quay lần {spin_count}: Trúng +{prize_xu:,} xu (st={st})")
                        remain_spins -= 1
                        spin_done = True
                        break
                if not spin_done:
                    break
                time.sleep(0.5)

        current_bal = data['balance']
        try:
            prof = data['session'].get(PROFILE_URL, timeout=8)
            m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', prof.text)
            if m:
                current_bal = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception:
            pass

        print(f"💰 Số dư khả dụng hiện tại: {current_bal:,} xu")

        if current_bal <= MIN_TRANSFER:
            print(f"⚠️ Số dư ({current_bal:,} xu) không vượt quá mức tối thiểu ({MIN_TRANSFER} xu). Không thể chuyển.")
            ws.close()
            return False

        if amount and amount > 0:
            transfer_amt = int(amount)
            if transfer_amt > current_bal:
                print(f"⚠️ Số dư hiện tại ({current_bal:,} xu) nhỏ hơn số xu yêu cầu ({transfer_amt:,} xu)!")
                print(f"   -> Sẽ chuyển toàn bộ số dư hiện có: {current_bal:,} xu")
                transfer_amt = current_bal
        else:
            pct = max(1, min(100, percent))
            transfer_amt = int(current_bal * pct / 100)

        if transfer_amt <= MIN_TRANSFER:
            print(f"⚠️ Số xu cần chuyển ({transfer_amt:,} xu) nhỏ hơn mức tối thiểu ({MIN_TRANSFER} xu).")
            ws.close()
            return False

        print(f"📤 Đang thực hiện chuyển {transfer_amt:,} xu tới Player ID: {dest_id}...")
        ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(transfer_amt)))

        transfer_ok = False
        transfer_msg = ""
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw: continue
            cmd, rd = parse_frame(raw)
            if cmd == CMD_PING or cmd == "PING":
                ws.send_binary(pack_num(CMD_PONG))
                continue
            if cmd == CMD_BALANCE_CHANGED or cmd == "BALANCE_CHANGED" or cmd == 319:
                transfer_ok = True
                transfer_msg = "BALANCE_CHANGED"
                break
            if cmd == CMD_TRANSFER or cmd == "TRANSFER" or cmd == 317:
                st = rd.i8()
                msg = rd.utf16() if rd.rem() > 0 else ""
                if st == 0:
                    transfer_ok = True
                    transfer_msg = msg or "SUCCESS"
                else:
                    transfer_msg = msg or f"FAIL(st={st})"
                break
            if cmd == CMD_ALERT or cmd == "ALERT" or cmd == 303:
                msg = rd.utf16() if rd.rem() > 0 else ""
                print(f"📢 [SERVER ALERT] {msg}")
                if "Successfully" in msg or "thành công" in msg.lower():
                    transfer_ok = True
                    transfer_msg = msg

        ws.close()

        if transfer_ok:
            print(f"============================================================")
            print(f"✅✅✅ CHUYỂN XU THÀNH CÔNG!")
            print(f"   - Số xu đã chuyển: {transfer_amt:,} xu")
            print(f"   - Tài khoản nhận (Player ID): {dest_id}")
            print(f"   - Phản hồi từ máy chủ: {transfer_msg}")
            print(f"============================================================")
            return True
        else:
            print(f"❌ Chuyển xu không thành công. Phản hồi: {transfer_msg}")
            return False

    except Exception as e:
        print(f"❌ [LỖI KẾT NỐI] {e}")
        if ws:
            try: ws.close()
            except Exception: pass
        return False


def main():
    parser = argparse.ArgumentParser(description="Chuyển xu WebSocket từ 1 tài khoản tới BẤT KỲ Player ID nào với số xu TÙY Ý")
    parser.add_argument("--user", required=True, help="Tên đăng nhập tài khoản gửi")
    parser.add_argument("--password", required=True, help="Mật khẩu tài khoản gửi")
    parser.add_argument("--dest", required=True, help="Player ID tài khoản nhận (bất kỳ ID nào)")
    parser.add_argument("--amount", default="0", help="Số xu tùy ý cần chuyển (vd: 50000, 1000000, 1m, 50m, 500k... Hoặc 0 = chuyển tất cả)")
    parser.add_argument("--percent", type=int, default=100, help="Phần trăm số dư cần chuyển (mặc định: 100% nếu không nhập amount)")
    parser.add_argument("--spin", action="store_true", help="Tự động quay vòng quay may mắn trước khi chuyển")

    args = parser.parse_args()
    user = args.user.strip().replace('"', '').replace("'", "")
    password = args.password.strip().replace('"', '').replace("'", "")
    dest_id = int(str(args.dest).strip().replace('"', '').replace("'", ""))
    amount = parse_amount(args.amount)

    success = run_spin_and_transfer(
        user=user,
        passwd=password,
        dest_id=dest_id,
        amount=amount,
        percent=args.percent,
        do_spin=args.spin
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
