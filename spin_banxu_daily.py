#!/usr/bin/env python3
"""
spin_banxu_daily.py — Tự động spin free + video spin cho ban_xu (FB-linked)
Login qua Facebook OAuth → WS login → spin → transfer về 10055407

Chạy trên GitHub Actions:
  - Cần FB cookies (set qua GitHub Secrets)
  - Login FB → OAuth gamevh.net → WS spin → transfer

Sử dụng urllib (stdlib) — không cần pip install requests
"""
import os, sys, struct, time, re, json, urllib.request, urllib.parse, http.cookiejar

# ==================== CONFIG ====================
FB_COOKIES_STR = os.environ.get("FB_COOKIES", "")
DEST_ID = 10055407
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client", "-q", "--break-system-packages"],
                   stderr=subprocess.DEVNULL)
    import websocket

# ==================== PACK HELPERS ====================
def pack_num(cmd, payload=b""): return struct.pack(">H", cmd) + payload
def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return struct.pack(">b", -len(b)) + b + payload
def asc(s):
    b = s.encode("ascii")[:255]
    return struct.pack(">b", len(b)) + b
def i8(v): return struct.pack(">b", v)
def i32(v): return struct.pack(">i", v)
def i64(v): return struct.pack(">q", v)

class Reader:
    def __init__(self, data): self.d = data; self.o = 0
    def rem(self): return len(self.d) - self.o
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.o)[0]; self.o += 1; return v
    def u8(self):
        v = self.d[self.o]; self.o += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.o)[0]; self.o += 2; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.o)[0]; self.o += 4; return v
    def i64(self):
        v = struct.unpack_from(">q", self.d, self.o)[0]; self.o += 8; return v
    def ascii(self):
        n = self.u8()
        s = self.d[self.o:self.o+n].decode("ascii", "replace"); self.o += n; return s
    def utf16(self):
        n = self.i16() if self.rem() >= 2 else 0
        s = self.d[self.o:self.o+n*2].decode("utf-16-be", "replace"); self.o += n*2; return s

def parse_frame(data):
    rd = Reader(data); first = rd.i8()
    if first < 0:
        n = -first
        name = data[1:1+n].decode("ascii", "replace"); rd.o = 1 + n
    else:
        second = rd.u8(); cmd_id = (first << 8) | second
        NAMES = {300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",317:"TRANSFER",
                 425:"SPIN_LUCKY_WHEEL",426:"GET_REMAIN_SPIN",431:"BALANCE_CHANGED"}
        name = NAMES.get(cmd_id, f"CMD_{cmd_id}"); rd.o = 2
    return name, rd

# ==================== HTTP LOGIN (qua FB OAuth) ====================
def fb_oauth_login():
    """Login gamevh.net qua Facebook OAuth using FB cookies."""
    print("[1] FB OAuth login...", flush=True)
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPRedirectHandler()
    )
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/139.0 Mobile Safari/537.36"),
        ("Accept-Language", "vi-VN,vi;q=0.9"),
    ]
    
    # Set FB cookies
    if FB_COOKIES_STR:
        for pair in FB_COOKIES_STR.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                op.addheaders.append(("Cookie", f"{k.strip()}={v.strip()}"))
    
    try:
        # Visit facebook.jsp → redirect to FB OAuth → auto-authorize → redirect back
        r = op.open("https://gamevh.net/facebook.jsp", timeout=20)
        final_url = r.geturl()
        print(f"  URL: {final_url[:80]}", flush=True)
        
        # If still on facebook.com (consent dialog), can't auto-click in Python
        if "facebook.com" in final_url:
            print("  ⚠️ FB consent dialog — need browser to click 'Tiếp tục'", flush=True)
            return None
        
        # Get token from GAME_URL
        g = op.open(GAME_URL, timeout=15).read().decode("utf-8", "replace")
        m_tok = re.search(r"var\s+token\s*=\s*(-?\d+)", g)
        m_nick = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g)
        m_pid = re.search(r"var\s+currentPlayerId\s*=\s*(-?\d+)", g)
        
        if not m_tok or not m_nick:
            print("  ❌ Token/nick not found", flush=True)
            return None
        
        cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
        pid = int(m_pid.group(1)) if m_pid else 0
        print(f"  ✅ nick={m_nick.group(1)}, pid={pid}", flush=True)
        
        return {
            "cookie": cookie, "nick": m_nick.group(1),
            "token": int(m_tok.group(1)), "pid": pid
        }
    except Exception as e:
        print(f"  ❌ Error: {e}", flush=True)
        return None

# ==================== WS LOGIN ====================
def ws_login(cookie, nick, token, timeout=15):
    try:
        ws = websocket.create_connection(WS_URL, cookie=cookie,
            header={"Origin": "https://gamevh.net"}, timeout=timeout)
        data = asc(nick) + i32(token) + asc("5.0.2") + asc("") + asc("caro") + struct.pack(">b", 1)
        ws.send_binary(pack_num(302, data))
        ws.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv()
            except:
                break
            if not raw: continue
            name, rd = parse_frame(raw)
            if name == "PING":
                ws.send_binary(pack_num(300)); continue
            if name in ("LOGIN", "CMD_302"):
                st = rd.i8()
                if st == 0:
                    print("  ✅ WS login OK", flush=True)
                    return ws
                # Check REFRESH
                path = rd.utf16() if rd.rem() >= 2 else ""
                if path == "REFRESH":
                    print("  ⚠️ Token expired (REFRESH)", flush=True)
                ws.close()
                return None
        return None
    except Exception as e:
        print(f"  ❌ WS error: {e}", flush=True)
        return None

# ==================== SPIN ====================
def drain(ws, timeout=1):
    ws.settimeout(timeout)
    try:
        while True:
            raw = ws.recv()
            if not raw: break
            n, _ = parse_frame(raw)
            if n == "PING": ws.send_binary(pack_num(300))
    except:
        pass

def get_remain_spin(ws, timeout=5):
    try:
        ws.send_binary(pack_str("GET_REMAIN_SPIN"))
    except:
        return None
    ws.settimeout(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except:
            return None
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            ws.send_binary(pack_num(300)); continue
        if name in ("GET_REMAIN_SPIN", "CMD_426"):
            try:
                st = rd.i8()
                return rd.i32() if rd.rem() >= 4 else 0
            except:
                return 0
    return None

def spin_once(ws, video=False, timeout=8):
    """Quay 1 lượt. video=True cho video spin (type=1)."""
    payload = i8(1) if video else b""
    try:
        ws.send_binary(pack_str("SPIN_LUCKY_WHEEL", payload))
    except:
        return None
    ws.settimeout(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except:
            return None
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            ws.send_binary(pack_num(300)); continue
        if name in ("SPIN_LUCKY_WHEEL", "CMD_425"):
            try:
                b = rd.d
                if rd.o < len(b) and b[rd.o] >= 0x80:
                    rd.i8()
                    err = rd.utf16()
                    return ("error", 0, err, 0)
                result = rd.i8()
                if result != 0:
                    err = rd.utf16() if rd.rem() >= 2 else ""
                    return ("fail", result, err, 0)
                slot = rd.u8()
                prize = rd.utf16()
                reward = rd.i32() if rd.rem() >= 4 else 0
                return ("ok", slot, prize, reward)
            except:
                return None
    return None

# ==================== TRANSFER ====================
def ws_transfer(ws, dest_id, amount, timeout=15):
    try:
        ws.send_binary(pack_num(317, i64(dest_id) + i64(amount)))
    except:
        return False, -1, "send_error"
    ws.settimeout(timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except:
            return False, -1, "timeout"
        if not raw: continue
        name, rd = parse_frame(raw)
        if name == "PING":
            ws.send_binary(pack_num(300)); continue
        if name == "BALANCE_CHANGED":
            try:
                rd.i8(); chip = rd.i64()
                print(f"    💰 BALANCE_CHANGED: {chip:,}", flush=True)
            except:
                pass
            continue
        if name in ("TRANSFER", "CMD_317"):
            st = rd.i8()
            txt = rd.utf16() if rd.rem() > 0 else ""
            return st == 0, st, txt
        if name == "ALERT":
            txt = rd.utf16() if rd.rem() > 0 else ""
    return False, -1, "timeout"

def get_balance(cookie, timeout=10):
    """Lấy balance từ profile page."""
    try:
        req = urllib.request.Request(PROFILE_URL, headers={
            "User-Agent": "Mozilla/5.0",
            "Cookie": cookie,
        })
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read().decode("utf-8", "replace")
        m = re.search(r"""<div\s+class=['"][^'"]*chipBalance[^'"]*['"][^>]*>([^<]+)</div>""", body, re.I)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except:
        pass
    return -1

# ==================== MAIN ====================
def main():
    print("=" * 60, flush=True)
    print("BAN_XU DAILY SPIN — FB OAuth + WS", flush=True)
    print("=" * 60, flush=True)
    
    # Step 1: FB OAuth login
    ld = fb_oauth_login()
    if not ld:
        print("\n❌ Login thất bại — cần FB cookies hợp lệ", flush=True)
        return 1
    
    if ld["pid"] == 0:
        print("  ⚠️ player_id=0 — FB session expired or account locked", flush=True)
        return 1
    
    # Step 2: WS login
    print("\n[2] WS login...", flush=True)
    ws = ws_login(ld["cookie"], ld["nick"], ld["token"])
    if not ws:
        print("  ❌ WS login fail", flush=True)
        return 1
    
    # Drain pending
    drain(ws, timeout=1)
    
    # Step 3: Free spin
    print("\n[3] Free spin...", flush=True)
    remain = get_remain_spin(ws)
    print(f"  remain = {remain}", flush=True)
    
    free_spins = 0
    free_reward = 0
    if remain and remain > 0:
        for i in range(remain):
            time.sleep(0.5)
            r = spin_once(ws, video=False)
            if not r or r[0] != "ok": break
            free_spins += 1
            free_reward += r[3]
            print(f"  Free {i+1}: ✅ {r[2]!r} +{r[3]}", flush=True)
    else:
        print("  (hết free spin hôm nay)", flush=True)
    
    # Step 4: Video spin (type=1)
    print(f"\n[4] Video spin (type=1, max 10)...", flush=True)
    video_spins = 0
    video_reward = 0
    for i in range(10):
        time.sleep(0.5)
        r = spin_once(ws, video=True)
        if not r:
            print(f"  Video {i+1}: timeout", flush=True)
            break
        if r[0] == "error":
            print(f"  Video {i+1}: ❌ {r[2][:60]}", flush=True)
            break
        if r[0] == "ok":
            video_spins += 1
            video_reward += r[3]
            print(f"  Video {i+1}: ✅ {r[2]!r} +{r[3]}", flush=True)
        else:
            print(f"  Video {i+1}: fail st={r[1]}", flush=True)
            break
    
    # Step 5: Get balance + transfer
    print(f"\n[5] Transfer xu về {DEST_ID}...", flush=True)
    time.sleep(1)
    balance = get_balance(ld["cookie"])
    print(f"  Balance: {balance:,} xu", flush=True)
    
    if balance > 200:
        # Transfer all except 0 (keep nothing — this is just a spin account)
        ok, st, txt = ws_transfer(ws, DEST_ID, balance)
        if ok:
            print(f"  ✅ Transfer {balance:,} xu → {DEST_ID}", flush=True)
        else:
            print(f"  ❌ Transfer fail (st={st}): {txt}", flush=True)
    else:
        print(f"  ⏭️ Balance ≤ 200, skip transfer", flush=True)
    
    # Summary
    total_reward = free_reward + video_reward
    print(f"\n{'='*60}", flush=True)
    print("TỔNG KẾT", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"  Free spins:    {free_spins} (+{free_reward} xu)", flush=True)
    print(f"  Video spins:   {video_spins} (+{video_reward} xu)", flush=True)
    print(f"  Total reward:   {total_reward} xu", flush=True)
    print(f"  Transferred:    {balance if balance > 200 else 0:,} xu → {DEST_ID}", flush=True)
    
    try:
        ws.close()
    except:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
