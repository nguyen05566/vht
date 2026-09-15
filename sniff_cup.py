#!/usr/bin/env python3
"""
sniff_cup.py — Sniff WebSocket cho cờ úp (mystery_xiangqi) trên gamevh.net
================================================================================
Đăng nhập nguyen10 → kết nối WS → ENTER_PLACE Lobby.mystery_xiangqi.0
→ LIST_BET_AMT → dump toàn bộ packet nhận được để hiểu protocol START_MATCH.

Cách chạy:
  python3 sniff_cup.py
"""
import os, sys, struct, time, re
import urllib.request, urllib.parse, http.cookiejar

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client", "-q", "--break-system-packages"],
                   stderr=subprocess.DEVNULL)
    import websocket

# ==================== CONFIG ====================
USER = "nguyen10"
PASSWD = "nhat123456"
WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
GAME_ID = "mystery_xiangqi"
PLACE_PATH = "Lobby.mystery_xiangqi.0"

# ==================== PACK HELPERS ====================
def pack_num(cmd, payload=b""): return struct.pack(">H", cmd) + payload
def pack_str(cmd, payload=b""):
    b = cmd.encode("ascii")
    return struct.pack(">b", -len(b)) + b + payload
def asc(s):
    b = s.encode("ascii")[:255]
    return struct.pack(">b", len(b)) + b
def i8(v):  return struct.pack(">b", v)
def i16(v): return struct.pack(">h", v)
def i32(v): return struct.pack(">i", v)
def i64(v): return struct.pack(">q", v)
def u16(v): return struct.pack(">H", v)

# ==================== READER ====================
class Reader:
    def __init__(self, data): self.d = data; self.o = 0
    def rem(self): return len(self.d) - self.o
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.o)[0]; self.o += 1; return v
    def u8(self):
        v = self.d[self.o]; self.o += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.o)[0]; self.o += 2; return v
    def u16(self):
        v = struct.unpack_from(">H", self.d, self.o)[0]; self.o += 2; return v
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
    def hex(self, n=9999):
        return self.d[self.o:self.o+n].hex()

# ==================== COMMAND NAME MAP ====================
CMD_NAMES = {
    300: "PONG", 301: "PING",
    302: "LOGIN", 303: "ALERT",
    317: "TRANSFER", 425: "SPIN_LUCKY_WHEEL", 426: "GET_REMAIN_SPIN", 431: "BALANCE_CHANGED",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "PLAYER_READY", 415: "SET_TURN", 416: "ENTER_TABLE",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}

def parse_frame(data):
    rd = Reader(data); first = rd.i8()
    if first < 0:
        n = -first
        name = data[1:1+n].decode("ascii", "replace"); rd.o = 1 + n
        return name, rd
    second = rd.u8(); cmd_id = (first << 8) | second
    return CMD_NAMES.get(cmd_id, f"CMD_{cmd_id}"), rd


def hex_dump(data, max_bytes=128):
    """In dữ liệu dạng hex để debug."""
    if not data:
        return "<empty>"
    s = data[:max_bytes].hex()
    chunks = [s[i:i+2] for i in range(0, len(s), 2)]
    return ' '.join(chunks) + ('...' if len(data) > max_bytes else '')


# ==================== HTTP LOGIN ====================
def http_login():
    print(f"[1] HTTP login user={USER}...", flush=True)
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/139.0 Mobile Safari/537.36"),
        ("Accept-Language", "vi-VN,vi;q=0.9"),
    ]
    try:
        op.open(LOGIN_URL, timeout=12).read()
        data = urllib.parse.urlencode({
            "redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
            "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"}).encode()
        op.open(LOGIN_URL, data=data, timeout=12)
        g = op.open(GAME_URL, timeout=12).read().decode("utf-8", "replace")
        m_tok = re.search(r"var\s+token\s*=\s*(-?\d+)", g)
        m_nick = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g)
        m_pid = re.search(r"var\s+currentPlayerId\s*=\s*(-?\d+)", g)
        if not m_tok or not m_nick:
            print("  ❌ Token/nick not found", flush=True)
            return None
        cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
        pid = int(m_pid.group(1)) if m_pid else 0
        print(f"  ✅ nick={m_nick.group(1)} pid={pid} token={m_tok.group(1)}", flush=True)
        return {
            "cookie": cookie, "nick": m_nick.group(1),
            "token": int(m_tok.group(1)), "pid": pid
        }
    except Exception as e:
        print(f"  ❌ Error: {e}", flush=True)
        return None


# ==================== WS LOGIN + SNIFF ====================
def ws_login_and_sniff(ld, duration_sec=60):
    print(f"\n[2] WS login + sniff for {duration_sec}s...", flush=True)
    try:
        ws = websocket.create_connection(WS_URL, cookie=ld["cookie"],
            header={"Origin": "https://gamevh.net"}, timeout=15)
    except Exception as e:
        print(f"  ❌ WS connect fail: {e}", flush=True)
        return

    # Send LOGIN
    data = asc(ld["nick"]) + i32(ld["token"]) + asc("5.0.2") + asc("") + asc(GAME_ID) + i8(1)
    ws.send_binary(pack_num(302, data))
    ws.settimeout(2)

    print("\n[3] Sending ENTER_PLACE + LIST_BET_AMT...", flush=True)
    # ENTER_PLACE Lobby.mystery_xiangqi.0
    time.sleep(1)
    enter_data = asc(PLACE_PATH) + struct.pack(">h", 0) + i8(1)
    ws.send_binary(pack_str("ENTER_PLACE", enter_data))
    time.sleep(0.5)
    ws.send_binary(pack_str("LIST_BET_AMT"))

    # Sniff
    deadline = time.time() + duration_sec
    pkt_count = 0
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            # Send PING to keep alive
            try: ws.send_binary(pack_num(301))
            except: pass
            continue
        except Exception as e:
            print(f"  recv err: {e}", flush=True)
            break
        if not raw: continue
        pkt_count += 1
        name, rd = parse_frame(raw)

        # Respond to PING
        if name == "PING":
            try: ws.send_binary(pack_num(300))
            except: pass
            continue

        # Print
        ts = time.strftime("%H:%M:%S")
        rem_hex = rd.hex(64)
        print(f"\n[{ts}] #{pkt_count} {name} ({len(raw)} bytes)", flush=True)
        print(f"  raw hex: {rem_hex}", flush=True)

        # Try to parse known packets
        try:
            if name in ("LIST_BET_AMT", "CMD_413"):
                parse_list_bet_amt(rd)
            elif name in ("START_MATCH", "CMD_417"):
                parse_start_match(rd)
            elif name in ("ENTER_PLACE", "CMD_401"):
                parse_enter_place(rd)
            elif name in ("CREATE_RULE", "CMD_405"):
                parse_create_rule(rd)
            elif name in ("PLAYER_ENTERED", "CMD_406"):
                parse_player_entered(rd)
            elif name in ("MOVE", "CMD_529"):
                parse_move(rd)
            elif name in ("SET_TURN", "CMD_415"):
                parse_set_turn(rd)
            elif name in ("GAMEOVER", "CMD_418"):
                parse_gameover(rd)
            elif name in ("ALERT", "CMD_303"):
                txt = rd.utf16()
                print(f"  ALERT text: {txt!r}", flush=True)
            elif name in ("LOGIN", "CMD_302"):
                st = rd.i8()
                print(f"  LOGIN status={st}", flush=True)
        except Exception as e:
            print(f"  parse error: {e}", flush=True)

    print(f"\n[done] sniffed {pkt_count} packets in {duration_sec}s", flush=True)
    try: ws.close()
    except: pass


def parse_list_bet_amt(rd):
    """LIST_BET_AMT response: byte count, then each entry: id byte, value int."""
    try:
        n = rd.u8()
        print(f"  LIST_BET_AMT: {n} entries", flush=True)
        for i in range(n):
            if rd.rem() < 5: break
            bet_id = rd.u8()
            bet_value = rd.i32()
            print(f"    id={bet_id} value={bet_value} xu", flush=True)
    except Exception as e:
        print(f"  parse err: {e}", flush=True)


def parse_enter_place(rd):
    try:
        # Skip strings
        place = rd.ascii()
        print(f"  ENTER_PLACE place={place!r}", flush=True)
    except: pass


def parse_create_rule(rd):
    try:
        st = rd.i8()
        print(f"  CREATE_RULE status={st}", flush=True)
    except: pass


def parse_player_entered(rd):
    try:
        name = rd.ascii()
        pid = rd.i32()
        print(f"  PLAYER_ENTERED name={name!r} pid={pid}", flush=True)
    except: pass


def parse_start_match(rd):
    """START_MATCH parser cho cờ úp - dạng tương tự xiangqi nhưng face có thể là 'x' (face-down)."""
    try:
        # Player list
        player_count = rd.u8()
        print(f"  START_MATCH: {player_count} players", flush=True)
        for i in range(player_count):
            slot = rd.u8()
            pid = rd.i32()
            print(f"    player[{i}]: slot={slot} pid={pid}", flush=True)
        # Pieces
        piece_count = rd.u8()
        print(f"  pieces: {piece_count}", flush=True)
        for i in range(piece_count):
            if rd.rem() < 4: break
            raw_sid = rd.u8()
            raw_face = rd.u8()
            pos = rd.u8()
            is_open = rd.u8()
            color = 'r' if raw_sid >= 0 else 'b'
            abs_sid = abs(raw_sid)
            piece_type_sid = abs_sid >> 3
            piece_type_face = raw_face >> 3
            print(f"    piece[{i}]: sid=0x{raw_sid:02x}({color}{piece_type_sid}) face=0x{raw_face:02x}(type={piece_type_face}) pos={pos} is_open={is_open}", flush=True)
        # Mystery bag (cờ úp-specific)
        if rd.rem() >= 2:
            bag_marker = rd.u8()
            mystery_count = rd.u8()
            print(f"  bag_marker={bag_marker} mystery_count={mystery_count}", flush=True)
            for i in range(mystery_count):
                if rd.rem() < 1: break
                v = rd.u8()
                print(f"    bag[{i}] = 0x{v:02x}", flush=True)
        # First turn + my slot
        if rd.rem() >= 2:
            extra_byte = rd.u8()
            extra_byte2 = rd.u8()
            print(f"  extra bytes: 0x{extra_byte:02x} 0x{extra_byte2:02x}", flush=True)
        if rd.rem() >= 2:
            first_turn_slot = rd.u8()
            my_slot = rd.u8()
            print(f"  first_turn_slot={first_turn_slot} my_slot={my_slot}", flush=True)
    except Exception as e:
        print(f"  parse err: {e}", flush=True)


def parse_move(rd):
    try:
        src = rd.u8()
        tgt = rd.u8()
        print(f"  MOVE: src={src} ({src//9},{src%9}) tgt={tgt} ({tgt//9},{tgt%9})", flush=True)
    except: pass


def parse_set_turn(rd):
    try:
        slot = rd.u8()
        to = rd.u16() if rd.rem() >= 2 else 0
        rem = rd.u16() if rd.rem() >= 2 else 0
        print(f"  SET_TURN slot={slot} turn_timeout={to} remain={rem}", flush=True)
    except: pass


def parse_gameover(rd):
    try:
        st = rd.u8()
        print(f"  GAMEOVER status={st}", flush=True)
    except: pass


# ==================== MAIN ====================
def main():
    ld = http_login()
    if not ld:
        return 1
    if ld["pid"] == 0:
        print("  ⚠️ playerId=0 — account locked!", flush=True)
        return 1
    ws_login_and_sniff(ld, duration_sec=int(sys.argv[1] if len(sys.argv) > 1 else 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
