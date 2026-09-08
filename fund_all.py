#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_all.py - CẤP XU CHO CÁC NICK TRƯỚC KHI BOT VÀO CHƠI (chạy ở job GitHub Actions riêng)

Cơ chế chuyển xu lấy từ transfer_one.py trong repo (đã kiểm chứng chạy thật):
  - HTTP login lấy cookie/token/playerId, đọc số dư chipBalance trên trang profile
  - FUND_ACCOUNT (nguyenpy2) đăng nhập 1 phiên WS duy nhất
  - Gửi lệnh TRANSFER (317): pack_long(dest_player_id) + pack_long(amount)
  - Thành công khi server trả BALANCE_CHANGED (319) hoặc TRANSFER status=0
  - Server giới hạn: mỗi lần chuyển phải > 200 xu

Luồng:
  B1: Đọc file acc (1 user/dòng)
  B2: HTTP login TỪNG nick -> lấy playerId + số dư (không chơi, chỉ soi số dư)
  B3: Nick có số dư < FUND_MIN_BALANCE -> xếp hàng chờ cấp FUND_AMOUNT xu
  B4: FUND_ACCOUNT đăng nhập HTTP + WS -> chuyển lần lượt theo hàng đợi
Script LUÔN thoát mã 0 để các job bot phía sau vẫn được chạy.
"""
import os
import random
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
MIN_TRANSFER = 200          # server: mỗi lần chuyển phải > 200 xu

CMD_PONG = 300
CMD_PING = 301
CMD_LOGIN = 302
CMD_ALERT = 303
CMD_TRANSFER = 317
CMD_BALANCE_CHANGED = 319

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")


def _env_str(key, default):
    val = os.environ.get(key)
    if val is not None and str(val).strip():
        return str(val).strip()
    return default


def _env_int(key, default):
    try: return int(_env_str(key, default))
    except (TypeError, ValueError): return default


ACCOUNTS_FILE    = _env_str("ACCOUNTS_FILE", "acc_valid_2.txt")
BOT_PASSWD       = _env_str("BOT_PASSWD", "nhat123456")
FUND_ACCOUNT     = _env_str("FUND_ACCOUNT", "nguyenpy2")
FUND_PASSWD      = _env_str("FUND_PASSWD", "nhat434241")
FUND_AMOUNT      = _env_int("FUND_AMOUNT", 3000)
FUND_MIN_BALANCE = _env_int("FUND_MIN_BALANCE", 3000)   # chỉ cấp cho nick có số dư < ngưỡng này


# ==================== PACK / PARSE (giống transfer_one.py) ====================

def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload

def i64(v): return struct.pack(">q", v)
def i32(v): return struct.pack(">i", v)
def i8(v):  return struct.pack(">b", v)

def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


class Reader:
    def __init__(self, data):
        self.d = bytes(data)
        self.p = 0
    def rem(self): return len(self.d) - self.p
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4; return v
    def utf16(self):
        n = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s


def parse_frame(raw):
    if not raw or len(raw) < 2: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n: return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


# ==================== HTTP LOGIN (đọc số dư + playerId) ====================

def http_login(user, passwd):
    """Trả về dict(session, nick, token, player_id, balance) hoặc (None, reason)."""
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    try:
        r = sess.post(LOGIN_URL, timeout=15,
                      data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                      headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                      allow_redirects=True)
        if "login.jsp" in r.url:
            return None, "sai tài khoản/mật khẩu hoặc bị chặn"

        g = sess.get(GAME_URL, timeout=15)
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
        pid_m = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", g.text)
        if not tm:
            return None, "không lấy được token trang game"

        bal = None
        for _ in range(2):     # thử 2 lần đọc số dư
            try:
                prof = sess.get(PROFILE_URL, timeout=12)
                m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                              prof.text)
                if m:
                    bal = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
                    break
            except Exception:
                time.sleep(1.0)

        return {
            "session": sess,
            "cookie": "; ".join(f"{k}={v}" for k, v in sess.cookies.items()),
            "nick": user,
            "token": int(tm.group(1)),
            "player_id": int(pid_m.group(1)) if pid_m else 0,
            "balance": bal,
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ==================== WEBSOCKET FUNDER ====================

def ws_login_funder(cookie, nick, token):
    """Đăng nhập WS của funder. Trả về websocket object hoặc None."""
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=15,
            header=["Cookie: " + cookie, "Origin: https://gamevh.net", "User-Agent: " + UA],
        )
        ws.send_binary(pack_num(CMD_LOGIN, asc(nick) + i32(token)
                                + asc("5.0.2") + asc("") + asc("xiangqi") + i8(1)))
        deadline = time.time() + 12
        while time.time() < deadline:
            raw = ws.recv()
            if not raw: continue
            cmd, rd = parse_frame(raw)
            if cmd == CMD_PING or cmd == "PING":
                ws.send_binary(pack_num(CMD_PONG)); continue
            if cmd == CMD_LOGIN or cmd == "LOGIN":
                st = rd.i8()
                if st == 0:
                    return ws
                print(f"[FUND] ❌ WS login bị từ chối (status={st})")
                ws.close(); return None
        print("[FUND] ❌ Hết giờ chờ WS login")
        ws.close()
        return None
    except Exception as e:
        print(f"[FUND] ❌ WS lỗi: {type(e).__name__}: {e}")
        return None


def ws_transfer(ws, dest_id, amount, timeout=12):
    """Gửi TRANSFER, chờ kết quả. Trả (ok, msg)."""
    try:
        ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(amount)))
    except Exception as e:
        return False, f"gửi lệnh lỗi: {e}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception:
            break
        if not raw: continue
        cmd, rd = parse_frame(raw)
        if cmd == CMD_PING or cmd == "PING":
            ws.send_binary(pack_num(CMD_PONG)); continue
        if cmd == CMD_BALANCE_CHANGED or cmd == 319:
            return True, "BALANCE_CHANGED"
        if cmd == CMD_TRANSFER or cmd == "TRANSFER" or cmd == 317:
            st = rd.i8()
            msg = rd.utf16() if rd.rem() > 0 else ""
            return st == 0, (msg or f"status={st}")
        if cmd == CMD_ALERT or cmd == 303:
            msg = rd.utf16() if rd.rem() > 0 else ""
            print(f"[FUND] 📢 SERVER: {msg[:120]}")
            if "Successfully" in msg or "thành công" in msg.lower():
                return True, msg
    return False, "hết 12s không thấy phản hồi"


# ==================== MAIN ====================

def main():
    t0 = time.time()
    print("=" * 64)
    print(f"[FUND] CẤP XU: {FUND_ACCOUNT} -> mỗi nick thiếu < {FUND_MIN_BALANCE:,} xu "
          f"được cấp {FUND_AMOUNT:,} xu")
    print(f"[FUND] File acc: {ACCOUNTS_FILE}")
    print("=" * 64)

    if not os.path.isfile(ACCOUNTS_FILE):
        print(f"[FUND] ❌ Không tìm thấy file {ACCOUNTS_FILE} -> thoát")
        return 0

    users = []
    with open(ACCOUNTS_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            u = line.strip()
            if u and not u.startswith("#") and u not in users:
                users.append(u)
    print(f"[FUND] Có {len(users)} nick trong file")

    # ---------- B1: quét số dư từng nick ----------
    needy = []          # (user, player_id, balance)
    ok_count = 0
    for i, u in enumerate(users, 1):
        info, err = http_login(u, BOT_PASSWD)
        if not info:
            print(f"[SCAN] ❌ ({i}/{len(users)}) {u}: login thất bại - {err}")
            time.sleep(random.uniform(0.8, 1.5))
            continue
        ok_count += 1
        bal = info["balance"]
        pid = info["player_id"]
        if bal is None:
            print(f"[SCAN] ⚠️ ({i}/{len(users)}) {u}: không đọc được số dư -> xếp vào hàng cấp")
            needy.append((u, pid, None))
        elif bal < FUND_MIN_BALANCE:
            print(f"[SCAN] 💰 ({i}/{len(users)}) {u}: {bal:,} xu < {FUND_MIN_BALANCE:,} -> CẦN CẤP")
            needy.append((u, pid, bal))
        else:
            print(f"[SCAN] ✅ ({i}/{len(users)}) {u}: {bal:,} xu - đủ, không cần cấp")
        try: info["session"].close()
        except Exception: pass
        time.sleep(random.uniform(0.8, 1.5))

    print("-" * 64)
    print(f"[SCAN] Xong: {ok_count}/{len(users)} login OK | cần cấp: {len(needy)} nick "
          f"| dự kiến chuyển {len(needy) * FUND_AMOUNT:,} xu")
    if not needy:
        print("[FUND] 🎉 Mọi nick đều đủ xu -> không cần chuyển gì")
        return 0

    # ---------- B2: đăng nhập funder ----------
    finfo, err = http_login(FUND_ACCOUNT, FUND_PASSWD)
    if not finfo:
        print(f"[FUND] ❌ Đăng nhập {FUND_ACCOUNT} thất bại: {err} -> không cấp được, "
              f"các nick vẫn vào chơi với số dư hiện có")
        return 0
    fbal = finfo["balance"]
    print(f"[FUND] ✅ {FUND_ACCOUNT} login OK | số dư: {fbal:,} xu "
          f"({len(needy)} nick cần {len(needy) * FUND_AMOUNT:,} xu)")
    if fbal is not None and fbal < len(needy) * FUND_AMOUNT:
        print(f"[FUND] ⚠️ Số dư {FUND_ACCOUNT} KHÔNG ĐỦ -> sẽ chuyển được một phần. "
              f"CẦN NẠP THÊM XU!")

    ws = ws_login_funder(finfo["cookie"], finfo["nick"], finfo["token"])
    if not ws:
        print("[FUND] ❌ WS funder không kết nối được -> dừng cấp, bot vẫn chạy")
        return 0

    # ---------- B3: chuyển lần lượt ----------
    total_ok = total_fail = total_xu = 0
    for i, (u, pid, bal) in enumerate(needy, 1):
        if not pid:
            print(f"[FUND] ⏭️ ({i}/{len(needy)}) {u}: thiếu playerId -> bỏ qua")
            total_fail += 1
            continue
        ok, msg = ws_transfer(ws, pid, FUND_AMOUNT)
        if ok:
            total_ok += 1; total_xu += FUND_AMOUNT
            print(f"[FUND] ✅ ({i}/{len(needy)}) {u}: +{FUND_AMOUNT:,} xu (ID {pid}) "
                  f"| tổng {total_xu:,} xu")
        else:
            total_fail += 1
            print(f"[FUND] ❌ ({i}/{len(needy)}) {u}: thất bại - {msg}")
            # WS có thể đã rớt -> thử kết nối lại 1 lần
            ws2 = ws_login_funder(finfo["cookie"], finfo["nick"], finfo["token"])
            if ws2:
                try: ws.close()
                except Exception: pass
                ws = ws2
            time.sleep(2.0)
        time.sleep(random.uniform(1.2, 2.5))

    try: ws.close()
    except Exception: pass

    print("=" * 64)
    print(f"[FUND] HOÀN TẤT sau {time.time() - t0:.0f}s: thành công {total_ok} nick "
          f"({total_xu:,} xu) | thất bại {total_fail} | scan được {ok_count}/{len(users)} nick")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
