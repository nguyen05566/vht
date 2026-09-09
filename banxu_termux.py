#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BANXU TERMUX — GOM XU VỀ ACC ĐÍCH, 1 FILE DUY NHẤT, CHẠY TRÊN TERMUX (ANDROID)
================================================================================
Chỉ dùng thư viện chuẩn của Python (không cần cài gì thêm):

    pkg update -y && pkg install python -y
    python banxu_termux.py

Máy sẽ hỏi:
  1. 🔗 Link GitHub chứa danh sách acc (link raw, nhiều link cách nhau bởi dấu phẩy)
     vd: https://raw.githubusercontent.com/nguyen05566/vht/main/acc_valid_1.txt
  2. 🔑 Token GitHub (chỉ cần khi repo private — Enter để bỏ qua nếu public)
  3. 🔒 Mật khẩu chung các acc (Enter = nhat123456)
  4. 🎯 ID acc nhận xu (Enter = 10055407 - ban_xu)
  5. 👥 Số luồng (Enter = 8)  6. 🔢 Số acc chạy (Enter = hết)

Mỗi acc: đăng nhập -> vào game -> quay hết lượt VÒNG QUAY MAY MẮN
         -> chuyển 100% số dư về acc đích. Acc chết tự động bỏ qua.

CHẾ ĐỘ GIỐNG NGƯỜI (tránh nghi):
  - Xáo trộn thứ tự acc mỗi lần chạy (không đi tuần tự a->z)
  - Delay ngẫu nhiên 8-25 giây giữa các acc (mỗi acc "vào game" lệch nhau)
  - Nghỉ ngẫu nhiên 30-90 giây giữa các lô 120 acc
  - Ngủ ngẫu nhiên 0.4-1.3s giữa các lượt quay (như người bấm)
  - User-Agent điện thoại Android thật, mỗi acc một UA khác nhau
  - 1 phiên đăng nhập/acc (quay + chuyển cùng phiên) — ít request như người chơi

Tự nhớ: acc đã chạy được ghi vào banxu_done.txt -> lần chạy sau TỰ BỎ QUA,
không quay lại acc cũ. Xóa file này nếu muốn chạy lại từ đầu.

Chạy nhanh không cần hỏi:
    python banxu_termux.py --url <LINK> --yes
    python banxu_termux.py --url <LINK> --limit 100 --delay 5-15 --yes

Mẹo Termux: chạy `termux-wake-lock` (script tự bật) để tắt màn hình vẫn chạy.
"""
import argparse
import base64
import http.cookiejar
import os
import random
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== CẤU HÌNH ====================
WS_URL      = "wss://gamevh.net/ws/gameServer"
LOGIN_URL   = "https://gamevh.net/login.jsp"
GAME_URL    = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION     = "5.0.2"
GAME_ID     = "caro"

CMD_TRANSFER = 317
CMD_LOGIN    = 302
CMD_PONG     = 300
CMD_PING     = 301
CMD_BALANCE_CHANGED = 431
MIN_TRANSFER = 200          # server chỉ cho chuyển số lượng > 200 xu

DEST_ID      = 10055407     # acc Facebook "ban_xu" (jav jp) — đích nhận xu mặc định
DEFAULT_PASS = "nhat123456" # mật khẩu chung các acc

# UA điện thoại Android thật — bot chạy trên điện thoại thì phải "nhìn như điện thoại"
UA_POOL = [
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; 22120RN86G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; V2312) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
]
ACCEPT_LANG = "vi-VN,vi;q=0.9,en-US;q=0.8"


# ==================== FRAMING (gi hệ game) ====================
def pack_num(cmd, payload=b""):   return struct.pack(">H", cmd) + payload
def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return bytes([(-len(b)) & 0xFF]) + b + payload
def i64(v): return struct.pack(">q", v)
def i32(v): return struct.pack(">i", v)
def i8(v):  return struct.pack(">b", v)
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
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4; return v
    def utf16(self):
        n = self._i16()
        if n <= 0: return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace"); self.p += e
        return s
    def _i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2; return v


def parse_frame(raw):
    """Frame server: byte đầu <0x80 => cmd số 2 byte; ngược lại => tên ASCII độ dài '256-n'."""
    if not raw or len(raw) < 2: return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n: return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


# ==================== WEBSOCKET THUẦN PYTHON (RFC 6455) ====================
class MiniWS:
    """WebSocket client tối giản bằng socket + ssl — không cần thư viện ngoài."""

    def __init__(self, url, cookie, ua, timeout=12):
        u = urllib.parse.urlparse(url)
        self.scheme = u.scheme or "wss"
        self.host = u.hostname
        self.port = u.port or (443 if self.scheme == "wss" else 80)
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.cookie, self.ua, self.timeout = cookie, ua, timeout
        self.sock, self.buf = None, b""

    def connect(self):
        err = None
        for secure in ((True, False) if self.scheme == "wss" else (False,)):
            raw = None
            try:
                raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
                if secure:
                    ctx = ssl.create_default_context()
                    try:
                        sock = ctx.wrap_socket(raw, server_hostname=self.host)
                    except ssl.SSLError:
                        ctx2 = ssl.create_default_context()
                        ctx2.check_hostname = False
                        ctx2.verify_mode = ssl.CERT_NONE
                        sock = ctx2.wrap_socket(raw, server_hostname=self.host)
                else:
                    sock = raw
                sock.settimeout(self.timeout)
                key = base64.b64encode(os.urandom(16)).decode()
                req = ("GET " + self.path + " HTTP/1.1\r\n"
                       "Host: " + self.host + "\r\n"
                       "Upgrade: websocket\r\n"
                       "Connection: Upgrade\r\n"
                       "Sec-WebSocket-Key: " + key + "\r\n"
                       "Sec-WebSocket-Version: 13\r\n"
                       "Origin: https://" + self.host + "\r\n"
                       "User-Agent: " + self.ua + "\r\n"
                       "Cookie: " + self.cookie + "\r\n\r\n")
                sock.sendall(req.encode())
                buf = b""
                while b"\r\n\r\n" not in buf:
                    c = sock.recv(4096)
                    if not c: raise EOFError("handshake closed")
                    buf += c
                head, rest = buf.split(b"\r\n\r\n", 1)
                line0 = head.split(b"\r\n")[0].decode("latin-1", "replace")
                if " 101" not in line0:
                    raise IOError("handshake: " + line0)
                self.sock, self.buf = sock, rest
                return True
            except Exception as e:
                err = e
                if raw is not None:
                    try: raw.close()
                    except Exception: pass
        raise err or IOError("connect failed")

    def _read(self, n):
        if n <= 0: return b""
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk: raise EOFError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, payload=b""):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            hdr = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            hdr = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + mask + masked)

    def send_binary(self, data):
        self._send_frame(0x2, data)

    def recv(self):
        """Trả payload của frame text/binary kế tiếp. Tự trả lời WS-ping, bỏ qua pong."""
        frag = b""
        while True:
            h = self._read(2)
            b1, b2 = h[0], h[1]
            fin, op = b1 & 0x80, b1 & 0x0F
            masked, ln = b2 & 0x80, b2 & 0x7F
            if ln == 126:   ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127: ln = struct.unpack(">Q", self._read(8))[0]
            mk = self._read(4) if masked else b""
            pl = self._read(ln)
            if mk:
                pl = bytes(b ^ mk[i % 4] for i, b in enumerate(pl))
            if op == 0x9:                      # WS ping -> pong
                try: self._send_frame(0xA, pl)
                except Exception: pass
                continue
            if op == 0xA: continue             # WS pong
            if op == 0x8: raise EOFError("server closed")
            if op in (0x1, 0x2):
                if fin: return pl
                frag = pl
                continue
            if op == 0x0:                      # continuation frame
                frag += pl
                if fin:
                    out, frag = frag, b""
                    return out
                continue
            # opcode lạ -> bỏ qua

    def close(self):
        try: self.sock.close()
        except Exception: pass


# ==================== HTTP (thư viện chuẩn) ====================
def _new_opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", random.choice(UA_POOL)),
                     ("Accept-Language", ACCEPT_LANG)]
    return op, cj


def get_balance_op(op):
    """Đọc số dư từ trang profile (phiên đã đăng nhập)."""
    try:
        t = op.open(PROFILE_URL, timeout=20).read().decode("utf-8", "replace")
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', t)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception:
        pass
    return 0


def get_public_balance(pid):
    """Đọc số dư công khai của 1 playerId (không cần đăng nhập)."""
    try:
        req = urllib.request.Request(PROFILE_URL + "?playerId=" + str(pid),
                                     headers={"User-Agent": random.choice(UA_POOL)})
        t = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', t)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception:
        pass
    return None


def http_login(user, pwd):
    """Đăng nhập web như trình duyệt điện thoại. Trả dict hoặc None nếu acc chết."""
    op, cj = _new_opener()
    try:
        op.open(LOGIN_URL, timeout=20).read()   # lấy cookie trước như browser
        data = urllib.parse.urlencode({
            "redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"}).encode()
        r = op.open(LOGIN_URL, data=data, timeout=20)
        if "login.jsp" in r.geturl():
            return None
        g = op.open(GAME_URL, timeout=20).read().decode("utf-8", "replace")
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g)
        token = int(tm.group(1)) if tm else 0
        if not token:
            return None
        mm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g)
        nick = mm.group(1).strip() if mm else user
        cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
        return {"token": token, "cookie": cookie, "nick": nick,
                "op": op, "balance": get_balance_op(op)}
    except Exception:
        return None


# ==================== PHIÊN GAME QUA WS ====================
def ws_login(cookie, nick, token, ua, log):
    try:
        ws = MiniWS(WS_URL, cookie, ua, timeout=12)
        ws.connect()
        ws.send_binary(pack_num(CMD_LOGIN, asc(nick) + i32(token)
                                + asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))
        deadline = time.time() + 8
        while time.time() < deadline:
            try: raw = ws.recv()
            except Exception: break
            if not raw: continue
            name, rd = parse_frame(raw)
            if name in (CMD_PING, "PING"):
                try: ws.send_binary(pack_num(CMD_PONG))
                except Exception: pass
                continue
            if name in (CMD_LOGIN, "LOGIN"):
                st = rd.i8()
                if st == 0:
                    return ws
                path = rd.utf16() if rd.rem() > 0 else ""
                log(f"    [{nick}] login st={st} path={path!r}")
                break
        try: ws.close()
        except Exception: pass
    except Exception:
        pass
    return None


def ws_query_remain(ws, log, timeout=8):
    try: ws.send_binary(pack_str("GET_REMAIN_SPIN"))
    except Exception: return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try: raw = ws.recv()
        except Exception: break
        if not raw: continue
        name, rd = parse_frame(raw)
        if name in (CMD_PING, "PING"):
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name == "GET_REMAIN_SPIN":
            rd.i8()          # status
            return rd.i32()  # remain
    return None


def ws_spin(ws, log, timeout=10):
    try: ws.send_binary(pack_str("SPIN_LUCKY_WHEEL"))
    except Exception: return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try: raw = ws.recv()
        except Exception: break
        if not raw: continue
        name, rd = parse_frame(raw)
        if name in (CMD_PING, "PING"):
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name == "SPIN_LUCKY_WHEEL":
            result = rd.i8()
            slot = rd.u8()
            prize = rd.utf16()
            reward = rd.i32() if rd.rem() >= 4 else 0
            return result, slot, prize, reward
    return None


def ws_transfer(ws, log, dest_id, amount, timeout=12):
    try: ws.send_binary(pack_num(CMD_TRANSFER, i64(dest_id) + i64(amount)))
    except Exception: return False, -1, "send_error"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try: raw = ws.recv()
        except Exception: break
        if not raw: continue
        name, rd = parse_frame(raw)
        if name in (CMD_PING, "PING"):
            try: ws.send_binary(pack_num(CMD_PONG))
            except Exception: pass
            continue
        if name in (CMD_BALANCE_CHANGED, "BALANCE_CHANGED"):
            return True, 0, "BALANCE_CHANGED"
        if name in (CMD_TRANSFER, "TRANSFER"):
            st = rd.i8()
            txt = rd.utf16() if rd.rem() > 0 else ""
            return st == 0, st, txt
    return False, -1, "timeout"


# ==================== XỬ LÝ 1 ACC ====================
def process_account(user, pwd, dest, ua, log, attempt=1):
    """Có Retry: nếu transfer bị từ chối (server đôi khi báo thiếu xu khi
    số dư chưa cập nhật sau khi quay) -> nghỉ ~1 phút -> chạy lại phiên mới."""
    r = _acc_once(user, pwd, dest, ua, log)
    if r["outcome"] == "REJECTED" and attempt == 1:
        wait = random.uniform(45, 75)
        log(f"  [{user}] 🔁 chờ {wait:.0f}s rồi thử lại lần 2 (phiên mới)...")
        time.sleep(wait)
        r2 = _acc_once(user, pwd, dest, ua, log)
        r2["spun"] += r["spun"]
        r2["reward"] += r["reward"]
        if not r2["transferred"]:
            r2["outcome"] = "REJECTED"
        return r2
    return r


def _acc_once(user, pwd, dest, ua, log):
    r = {"user": user, "live": False, "spun": 0, "reward": 0,
         "transferred": 0, "outcome": ""}
    ld = http_login(user, pwd)
    if not ld:
        r["outcome"] = "DEAD_LOGIN"
        log(f"  [{user}] ❌ Đăng nhập thất bại (acc chết / sai mật khẩu)")
        return r
    r["live"] = True
    ws = None
    try:
        ws = ws_login(ld["cookie"], ld["nick"], ld["token"], ua, log)
        if not ws:
            log(f"  [{user}] ⚠️ vào game thất bại (WS)")
        else:
            remain = ws_query_remain(ws, log)
            if remain is None:
                log(f"  [{user}] ⚠️ không lấy được số lượt quay")
            elif remain > 0:
                for turn in range(1, remain + 1):
                    time.sleep(random.uniform(0.4, 1.3))   # bấm quay như người
                    sp = ws_spin(ws, log)
                    if not sp:
                        log(f"  [{user}] ⚠️ quay {turn}/{remain}: không phản hồi")
                        break
                    rc, slot, prize, reward = sp
                    r["spun"] += 1
                    r["reward"] += reward
                    log(f"  [{user}] 🎰 quay {turn}/{remain}: {prize or reward} (+{reward} x)")
            else:
                log(f"  [{user}] ⏭️ hết lượt quay hôm nay")
        # Số dư mới nhất sau khi quay (đọc lại profile bằng phiên cũ)
        bal = get_balance_op(ld["op"])
        if not bal or bal <= 0:
            bal = ld["balance"] + r["reward"]   # dự phòng nếu trang profile lỗi
        if bal <= MIN_TRANSFER:
            r["outcome"] = "LOW_BALANCE"
            log(f"  [{user}] ⏭️ số dư {bal:,} <= {MIN_TRANSFER}, không chuyển")
            return r
        if not ws:
            r["outcome"] = "WS_FAIL"
            return r
        ok, st, txt = ws_transfer(ws, log, dest, bal)
        if ok:
            r["transferred"] = bal
            r["outcome"] = "OK"
            log(f"  [{user}] ✅ TRANSFER {bal:,} x -> id {dest}")
        else:
            est = ld["balance"] + r["reward"]   # thử lại bằng con số ước tính
            if est > MIN_TRANSFER and est != bal:
                ok2, st2, txt2 = ws_transfer(ws, log, dest, est)
                if ok2:
                    r["transferred"] = est
                    r["outcome"] = "OK"
                    log(f"  [{user}] ✅ TRANSFER {est:,} x -> id {dest} (lần 2)")
            if not r["transferred"]:
                r["outcome"] = "REJECTED"
                log(f"  [{user}] ❌ TRANSFER bị từ chối (st={st}): {txt}")
    except Exception as e:
        r["outcome"] = "ERR:" + type(e).__name__
        log(f"  [{user}] ⚠️ lỗi: {e}")
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
    return r


# ==================== TẢI DANH SÁCH ACC ====================
def _load_token(script_dir):
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok: return tok
    for p in (os.path.join(script_dir, ".gh_token"), ".gh_token"):
        try:
            if os.path.exists(p):
                t = open(p).read().strip()
                if t: return t
        except Exception: pass
    return ""


def _fetch_url(url, token):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", random.choice(UA_POOL))
    if token:
        req.add_header("Authorization", "token " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def load_accounts(url_spec, token, log, allow_prompt=True):
    """url_spec: 1 hoặc nhiều link raw / file local, cách nhau bởi dấu phẩy."""
    users, seen = [], set()
    for part in [x.strip() for x in url_spec.split(",") if x.strip()]:
        text = None
        if part.startswith("http://") or part.startswith("https://"):
            m = re.match(r"https?://([^@/]+)@(.+)$", part)
            tok, url = (m.group(1), "https://" + m.group(2)) if m else (token, part)
            for attempt in (1, 2):
                try:
                    text = _fetch_url(url, tok)
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (401, 403, 404) and attempt == 1 and allow_prompt:
                        try: t = input("  🔑 Token GitHub cho repo private (Enter = bỏ qua): ").strip()
                        except EOFError: t = ""
                        if t: tok = t; continue
                    log(f"  ✗ Tải thất bại: {url} ({e})")
                    break
                except Exception as e:
                    log(f"  ✗ Tải thất bại: {url} ({e})")
                    break
        else:
            try:
                text = open(part, encoding="utf-8", errors="replace").read()
            except Exception as e:
                log(f"  ✗ {part}: {e}")
                continue
        if text is None: continue
        n0 = len(users)
        for line in text.splitlines():
            name = line.strip().split("\t")[0].strip()
            if name and not name.startswith("#") and name.lower() not in seen:
                seen.add(name.lower())
                users.append(name)
        log(f"  ✓ {part}: +{len(users) - n0} tk")
    return users


# ==================== TIỆN ÍCH ====================
def ask(prompt, default):
    try:
        s = input(f"{prompt} [{default}]: ").strip()
        return s or str(default)
    except EOFError:
        return str(default)


def try_wake_lock():
    try:
        if subprocess.call(["termux-wake-lock"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            print("🔒 termux-wake-lock: đã bật (tắt màn hình vẫn chạy)")
    except Exception:
        pass


def make_logger(path):
    lock = threading.Lock()
    f = open(path, "w", encoding="utf-8")
    def log(msg):
        with lock:
            print(msg, flush=True)
            try:
                f.write(str(msg) + "\n"); f.flush()
            except Exception: pass
    return log, f


# ==================== MAIN ====================
def main():
    ap = argparse.ArgumentParser(description="Gom xu về acc đích — 1 file, chạy Termux")
    ap.add_argument("--url", default="",
                    help="link GitHub raw chứa acc (nhiều link cách nhau bởi dấu phẩy)")
    ap.add_argument("--token", default="", help="token GitHub (repo private)")
    ap.add_argument("--password", "--pwd", default=DEFAULT_PASS)
    ap.add_argument("--dest", type=int, default=DEST_ID, help="playerId nhận xu")
    ap.add_argument("--workers", type=int, default=8, help="số luồng")
    ap.add_argument("--limit", type=int, default=0, help="số acc chạy (0 = hết)")
    ap.add_argument("--delay", default="8-25",
                    help="delay ngẫu nhiên mỗi acc (giây), vd: 8-25")
    ap.add_argument("--batch-size", type=int, default=120)
    ap.add_argument("--batch-pause", type=int, default=30,
                    help="nghỉ giữa các lô (sẽ random từ x1 đến x3)")
    ap.add_argument("--exclude", default="",
                    help="file/link chứa acc cần BỎ QUA (giữ làm vốn)")
    ap.add_argument("--done-file", default="", help="mặc định: banxu_done.txt cạnh script")
    ap.add_argument("--yes", action="store_true", help="chạy luôn không hỏi gì")
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    run_log = os.path.join(script_dir, "banxu_run_" +
                           time.strftime("%Y%m%d_%H%M%S") + ".log")
    log, logf = make_logger(run_log)

    log("=" * 62)
    log("🤖 BANXU TERMUX — quay vòng quay + chuyển 100% số dư về acc đích")
    log(f"   Log phiên này: {run_log}")
    log("=" * 62)

    # ---- Hỏi thông tin nếu chưa có ----
    if not args.url and not args.yes:
        try:
            args.url = input("🔗 Link GitHub chứa danh sách acc (link raw): ").strip()
        except EOFError:
            args.url = ""
    if not args.url:
        log("✗ Chưa có link/file danh sách acc! (vd: https://raw.githubusercontent.com/nguyen05566/vht/main/acc_valid_1.txt)")
        return 1

    token = args.token or _load_token(script_dir)
    if not token and not args.yes:
        try: t = input("🔑 Token GitHub (Enter nếu repo public): ").strip()
        except EOFError: t = ""
        if t: token = t
    if token and not args.token and not args.yes:
        try:
            if input("💾 Lưu token vào .gh_token cho lần sau? (y/N): ").strip().lower().startswith("y"):
                open(os.path.join(script_dir, ".gh_token"), "w").write(token)
                log("  ✓ đã lưu .gh_token")
        except EOFError:
            pass

    if not args.yes:
        args.password = ask("🔒 Mật khẩu chung các acc", args.password)
        args.dest     = int(ask("🎯 ID acc nhận xu", args.dest))
        args.workers  = int(ask("👥 Số luồng", args.workers))
        args.limit    = int(ask("🔢 Số acc chạy (0 = hết)", args.limit))

    m = re.match(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", args.delay or "")
    dmin, dmax = (float(m.group(1)), float(m.group(2))) if m else (8.0, 25.0)
    if dmax < dmin: dmin, dmax = dmax, dmin

    try_wake_lock()

    # ---- Tải + lọc danh sách acc ----
    log("\n[1/3] Tải danh sách acc:")
    users = load_accounts(args.url, token, log, allow_prompt=not args.yes)
    if not users:
        log("✗ Danh sách acc trống!")
        return 1
    if args.exclude:
        ex_users = load_accounts(args.exclude, token, log, allow_prompt=False)
        ex_set = {x.lower() for x in ex_users}
        before = len(users)
        users = [u for u in users if u.lower() not in ex_set]
        log(f"  🚫 loại {before - len(users)} acc theo --exclude")

    done_path = args.done_file or os.path.join(script_dir, "banxu_done.txt")
    if os.path.exists(done_path):
        with open(done_path, encoding="utf-8") as df:
            done_set = {ln.strip().lower() for ln in df if ln.strip()}
        before = len(users)
        users = [u for u in users if u.lower() not in done_set]
        log(f"  📌 bỏ qua {before - len(users)} acc đã chạy trước đó ({done_path})")

    random.shuffle(users)          # xáo trộn — không đi tuần tự như máy móc
    if args.limit > 0:
        users = users[:args.limit]
    if not users:
        log("🤷 Tất cả acc trong danh sách đã chạy hết rồi.")
        return 0
    log(f"  → SẼ CHẠY: {len(users)} acc | mật khẩu {args.password} | đích id {args.dest}")

    def mark_done(u):
        with open(done_path, "a", encoding="utf-8") as df:
            df.write(u + "\n")

    # ---- Đọc balance đích trước khi chạy ----
    bal0 = get_public_balance(args.dest)
    log(f"  💰 Balance đích ({args.dest}) trước khi chạy: "
        + (f"{bal0:,} x" if bal0 is not None else "?"))

    log(f"\n[2/3] CHẾ ĐỘ GIỐNG NGƯỜI: {args.workers} luồng | delay {dmin:.0f}-{dmax:.0f}s/acc"
        f" | lô {args.batch_size} | nghỉ lô {args.batch_pause}-{args.batch_pause * 3}s")

    # ---- Chạy theo lô ----
    log(f"\n[3/3] Bắt đầu… (Ctrl+C để dừng, acc đã chạy vẫn được lưu)\n")
    stats = {"done": 0, "live": 0, "dead": 0, "spun": 0, "reward": 0,
             "ok": 0, "xu": 0, "rejected": 0, "low": 0}
    t_start = time.time()
    batches = [users[i:i + args.batch_size] for i in range(0, len(users), args.batch_size)]
    try:
        for bi, chunk in enumerate(batches, 1):
            log(f"{'=' * 62}\n📦 LÔ {bi}/{len(batches)} — {len(chunk)} acc\n{'=' * 62}")
            with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(chunk)))) as ex:
                futs = {}
                for u in chunk:
                    def _job(uu=u):
                        time.sleep(random.uniform(dmin, dmax))   # mỗi acc vào lệch nhau
                        return process_account(uu, args.password, args.dest,
                                               random.choice(UA_POOL), log)
                    futs[ex.submit(_job)] = u
                for f in as_completed(futs):
                    r = f.result()
                    stats["done"] += 1
                    if r["live"]: stats["live"] += 1
                    else: stats["dead"] += 1
                    stats["spun"] += r["spun"]
                    stats["reward"] += r["reward"]
                    if r["transferred"]:
                        stats["ok"] += 1
                        stats["xu"] += r["transferred"]
                    elif r["outcome"] == "REJECTED":
                        stats["rejected"] += 1
                    elif r["outcome"] == "LOW_BALANCE":
                        stats["low"] += 1
                    mark_done(r["user"])
            if bi < len(batches):
                bp = random.uniform(args.batch_pause, args.batch_pause * 3)
                log(f"😴 Nghỉ {bp:.0f}s giữa lô {bi} và {bi + 1}…")
                time.sleep(bp)
    except KeyboardInterrupt:
        log("\n⏹️ DỪNG THEO YÊU CẦU (acc đã chạy vẫn được lưu)")

    # ---- TỔNG KẾT ----
    wall = time.time() - t_start
    bal1 = get_public_balance(args.dest)
    log("\n" + "=" * 62)
    log("🏁 TỔNG KẾT PHIÊN CHẠY")
    log("=" * 62)
    log(f"  Đã xử lý         : {stats['done']}/{len(users)} acc")
    log(f"  Acc sống         : {stats['live']} | Acc chết: {stats['dead']}")
    log(f"  Lượt quay        : {stats['spun']} (thưởng {stats['reward']:,} x)")
    log(f"  Chuyển thành công: {stats['ok']} acc — TỔNG {stats['xu']:,} xu")
    log(f"  Bị từ chối       : {stats['rejected']} | Số dư quá thấp: {stats['low']}")
    if bal0 is not None and bal1 is not None:
        log(f"  Balance đích ({args.dest}): {bal0:,} -> {bal1:,} (+{bal1 - bal0:,} x)")
    log(f"  ⏱️ Thời gian: {int(wall)}s ({wall / 60:.1f} phút)")
    log(f"  📌 Acc đã chạy: {done_path} (chạy lại file này sẽ tự bỏ qua)")
    try: logf.close()
    except Exception: pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n⏹️ Đã dừng.")
        sys.exit(130)
