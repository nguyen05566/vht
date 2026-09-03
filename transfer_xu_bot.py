#!/usr/bin/env python3
"""
transfer_xu_bot.py - Module chuyển xu cho arena bots
Được gọi từ arena*.py để chuyển 20% xu về tài khoản đích.
"""

import struct
import time
import requests
import re
import websocket


# ==================== CONSTANTS ====================
CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    317: "TRANSFER", 319: "BALANCE_CHANGED",
}

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
MIN_TRANSFER = 200  # server: chuyển tối thiểu > 200 x


# ==================== PACK HELPERS ====================
def pack_cmd(cmd, data=b''):
    """Pack command với string name."""
    result = bytearray()
    if isinstance(cmd, str):
        cmd_bytes = cmd.encode('ascii')
        result.append((-len(cmd_bytes)) & 0xFF)
        result.extend(cmd_bytes)
    elif isinstance(cmd, int):
        result.extend(struct.pack('>H', cmd))
    result.extend(data)
    return bytes(result)


def pack_ascii(value):
    encoded = value.encode('ascii')[:255]
    return struct.pack('>b', len(encoded)) + encoded


def pack_int(value):
    return struct.pack('>i', value)


def pack_long(value):
    return struct.pack('>q', value)


def pack_string(value):
    encoded = value.encode('utf-16-be')
    return struct.pack('>h', len(encoded) // 2) + encoded


# ==================== PARSE HELPERS ====================
class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return val

    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2
        return val

    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return val

    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8
        return val

    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length
        return s

    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2
        return s

    def rem(self):
        return len(self.data) - self.offset


def parse_message(data):
    """Parse message, trả về (command_name, Reader)."""
    rd = Reader(data)
    length = rd.read_byte()
    if length < 0:
        cmd = data[rd.offset:rd.offset + (-length)].decode('ascii', errors='replace')
        rd.offset += (-length)
        return cmd, rd
    else:
        next_byte = data[rd.offset] & 0xFF
        rd.offset += 1
        return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte)), rd


# ==================== HTTP LOGIN ====================
def get_balance(session):
    """Lấy số dư từ trang profile."""
    try:
        prof = session.get(PROFILE_URL, timeout=15)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                      prof.text)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception as e:
        print(f"[TRANSFER] get_balance error: {e}")
    return 0


def http_login(user, passwd):
    """Đăng nhập HTTP, trả về dict với cookie, nick, token, balance hoặc None."""
    try:
        session = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        session.headers.update({
            "User-Agent": ua,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })

        session.get(LOGIN_URL, timeout=20)
        resp = session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
            allow_redirects=True)

        if "login.jsp" in resp.url:
            return None

        game_resp = session.get(GAME_URL, timeout=20)
        page = game_resp.text

        token_m = re.search(r"var\s+token\s*=\s*(-?\d+)", page)
        nick_m = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page)

        if not token_m or not nick_m:
            return None

        balance = get_balance(session)

        return {
            "cookie": "; ".join(f"{k}={v}" for k, v in session.cookies.items()),
            "nick": nick_m.group(1),
            "token": int(token_m.group(1)),
            "balance": balance,
        }
    except Exception as e:
        print(f"[TRANSFER] HTTP login error: {e}")
        return None


# ==================== WEBSOCKET ====================
def ws_login(cookie, nick, token):
    """Login WS, trả về websocket object hoặc None."""
    try:
        ws = websocket.create_connection(
            WS_URL,
            cookie=cookie,
            header={"Origin": "https://gamevh.net"},
            timeout=15
        )

        # Gửi LOGIN
        data = bytearray()
        data.extend(pack_ascii(nick))
        data.extend(pack_int(token))
        data.extend(pack_ascii("5.0.2"))
        data.extend(pack_ascii(""))
        data.extend(pack_ascii("xiangqi"))
        data.extend(struct.pack('>b', 1))
        ws.send_binary(pack_cmd("LOGIN", bytes(data)))

        # Đợi response
        deadline = time.time() + 10
        while time.time() < deadline:
            raw = ws.recv()
            if not raw:
                continue
            name, rd = parse_message(raw)
            if name == "PING":
                ws.send_binary(pack_cmd("PONG"))
                continue
            if name == "LOGIN":
                status = rd.read_byte()
                if status == 0:
                    return ws
                else:
                    print(f"[TRANSFER] WS login fail: status={status}")
                    ws.close()
                    return None

        ws.close()
        return None
    except Exception as e:
        print(f"[TRANSFER] WS login error: {e}")
        return None


def ws_transfer(ws, dest_id, amount, timeout=12):
    """Gửi TRANSFER. Trả (ok, status, text)."""
    ws.send_binary(pack_cmd(317, pack_long(dest_id) + pack_long(amount)))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
            if not raw:
                continue
            name, rd = parse_message(raw)
            if name == "PING":
                ws.send_binary(pack_cmd("PONG"))
                continue
            if name == "BALANCE_CHANGED":
                return True, 0, "BALANCE_CHANGED"
            if name == "TRANSFER" or name == "317":
                st = rd.read_byte()
                txt = rd.read_string() if rd.rem() > 0 else ""
                return st == 0, st, txt
            if name == "ALERT":
                txt = rd.read_string() if rd.rem() > 0 else ""
                print(f"[TRANSFER] ALERT: {txt[:100]}")
        except Exception as e:
            print(f"[TRANSFER] recv error: {e}")
            break
    return False, -1, "timeout"


# ==================== MAIN FUNCTION ====================
def transfer_xu_sync(user, passwd, dest_id=69284652, percent=20):
    """
    Chuyển X% xu từ tài khoản user về dest_id.
    Trả về True nếu thành công, False nếu thất bại.
    """
    print(f"[TRANSFER] 🔄 {user}: Chuyển {percent}% xu về ID {dest_id}...")

    # Bước 1: HTTP login + lấy balance
    ld = http_login(user, passwd)
    if not ld:
        print(f"[TRANSFER] ❌ {user}: Đăng nhập HTTP thất bại")
        return False

    balance = ld["balance"]
    print(f"[TRANSFER] ✅ {user}: Login OK, nick={ld['nick']}, balance={balance:,}")

    if balance <= MIN_TRANSFER:
        print(f"[TRANSFER] ⏭️ {user}: Số dư {balance:,} <= {MIN_TRANSFER}, bỏ qua")
        return True

    # Bước 2: Tính lượng cần chuyển
    transfer_amount = int(balance * percent / 100)
    if transfer_amount < MIN_TRANSFER:
        print(f"[TRANSFER] ⏭️ {user}: Lượng chuyển {transfer_amount:,} < {MIN_TRANSFER}, bỏ qua")
        return True

    # Bước 3: WS login
    ws = ws_login(ld["cookie"], ld["nick"], ld["token"])
    if not ws:
        print(f"[TRANSFER] ❌ {user}: Đăng nhập WS thất bại")
        return False

    print(f"[TRANSFER] ✅ {user}: WS connected")
    print(f"[TRANSFER] 💰 {user}: Chuyển {transfer_amount:,} xu ({percent}% của {balance:,})")

    try:
        # Bước 4: Thực hiện transfer
        ok, status, txt = ws_transfer(ws, dest_id, transfer_amount)
        if ok:
            print(f"[TRANSFER] ✅✅✅ {user}: CHUYỂN {transfer_amount:,} XU VỀ ID {dest_id} THÀNH CÔNG!")
            return True
        else:
            print(f"[TRANSFER] ❌ {user}: Transfer fail (status={status}): {txt}")
            return False

    except Exception as e:
        print(f"[TRANSFER] ❌ {user}: Lỗi: {e}")
        return False
    finally:
        try:
            ws.close()
        except:
            pass


# ==================== TEST ====================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python transfer_xu_bot.py <user> <password> [dest_id] [percent]")
        sys.exit(1)

    user = sys.argv[1]
    passwd = sys.argv[2]
    dest_id = int(sys.argv[3]) if len(sys.argv) > 3 else 69284652
    percent = int(sys.argv[4]) if len(sys.argv) > 4 else 20

    transfer_xu_sync(user, passwd, dest_id, percent)
