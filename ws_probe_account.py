#!/usr/bin/env python3
"""
Probe read-only protocol WebSocket của gamevh.net để xác định
cơ chế lưu trữ / quản lý tài khoản game (id, nickname, token, balance, place).

- Chỉ đọc: HTTP login -> lấy token/cookie/nickname/balance/player_id -> WS LOGIN
- Gửi PING/PONG để giữ kết nối, dump mọi frame nhận được (cmd id + payload hex)
- Không vào bàn, không đặt cược, không chuyển xu.
"""
import json
import re
import struct
import sys
import time

import requests
import websocket

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/caro/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION = "5.0.2"
GAME_ID = "caro"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

CMD = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 304: "RIBBON_MESSAGE",
    311: "BROADCAST", 312: "INVITE", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 405: "CREATE_RULE",
    406: "PLAYER_ENTERED", 407: "PLAYER_EXITED", 410: "KICK_PLAYER",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 417: "START_MATCH",
    418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR",
    431: "BALANCE_CHANGED", 432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY", 505: "CHAT", 518: "HIGHLIGHT",
    529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT",
}


def i8(v):
    return struct.pack(">b", v)


def i32(v):
    return struct.pack(">i", v)


def i64(v):
    return struct.pack(">q", v)


def asc(s):
    e = s.encode("ascii", "replace")[:255]
    return i8(len(e)) + e


def utf(s):
    b = s.encode("utf-16-be")
    return struct.pack(">h", len(s)) + b


def pack_num(cmd, payload=b""):
    return struct.pack(">H", cmd) + payload


class Reader:
    def __init__(self, d):
        self.d, self.p = bytes(d), 0

    def rem(self):
        return len(self.d) - self.p

    def u8(self):
        v = self.d[self.p] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def i8(self):
        v = struct.unpack_from(">b", self.d, self.p)[0] if self.p < len(self.d) else 0
        self.p += 1
        return v

    def i16(self):
        v = struct.unpack_from(">h", self.d, self.p)[0] if self.p + 2 <= len(self.d) else 0
        self.p += 2
        return v

    def i32(self):
        v = struct.unpack_from(">i", self.d, self.p)[0] if self.p + 4 <= len(self.d) else 0
        self.p += 4
        return v

    def i64(self):
        v = struct.unpack_from(">q", self.d, self.p)[0] if self.p + 8 <= len(self.d) else 0
        self.p += 8
        return v

    def utf16(self):
        n = self.i16()
        if n <= 0:
            return ""
        e = min(n * 2, self.rem())
        s = self.d[self.p:self.p + e].decode("utf-16-be", "replace")
        self.p += e
        return s

    def ascii(self):
        n = self.u8()
        e = min(n, self.rem())
        s = self.d[self.p:self.p + e].decode("ascii", "replace")
        self.p += e
        return s


def parse_frame(raw):
    if not raw:
        return None, None
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    if len(raw) < 2:
        return None, None
    first = struct.unpack_from(">b", raw, 0)[0]
    if first < 0:
        n = -first
        if len(raw) < 1 + n:
            return None, None
        return raw[1:1 + n].decode("ascii", "replace"), Reader(raw[1 + n:])
    return (first << 8) | raw[1], Reader(raw[2:])


def http_login(user, pwd):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7"})
    s.get(LOGIN_URL, timeout=15)
    r = s.post(LOGIN_URL, timeout=15,
               data={"redirect": "/", "USER_NAME": user, "PASSWORD": pwd,
                     "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
               headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
               allow_redirects=True)
    if "login.jsp" in r.url:
        return None, "HTTP_LOGIN_FAIL"
    g = s.get(GAME_URL, timeout=15)
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", g.text)
    token = int(tm.group(1)) if tm else 0
    nm = re.search(r"var\s+currentPlayerNickName\s*=\s*['\"]([^'\"]+)['\"]", g.text)
    nick = nm.group(1).strip() if nm else user
    pm = re.search(r"var\s+placePath\s*=\s*\"([^\"]+)\"", g.text)
    place = pm.group(1) if pm else ""
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())

    info = {"user": user, "nick": nick, "token": token, "placePath": place,
            "cookie_names": sorted(s.cookies.keys()), "balance": 0, "player_id": 0}
    try:
        prof = s.get(PROFILE_URL, timeout=15)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>',
                      prof.text)
        if m:
            info["balance"] = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        ids = re.findall(r"showProfile\((\d+)\)", prof.text) + \
              re.findall(r"showGameProfile\((\d+)", prof.text)
        if ids:
            info["player_id"] = int(ids[0])
        info["profile_ids"] = sorted({int(x) for x in ids})[:5]
    except Exception as e:
        info["balance_error"] = str(e)
    return {"session": s, "cookie": cookie, "info": info}, "OK"


def dump_frames(ws, seconds=25, max_frames=60):
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline and len(out) < max_frames:
        try:
            ws.settimeout(max(1, deadline - time.time()))
            raw = ws.recv()
        except Exception as e:
            break
        if not raw:
            continue
        name, rd = parse_frame(raw)
        if name is None:
            continue
        payload = raw[2:] if not isinstance(raw, str) else raw.encode()
        if isinstance(name, int):
            cmd_name = CMD.get(name, f"CMD_{name}")
        else:
            cmd_name = name
        rec = {"cmd": cmd_name, "id": name if isinstance(name, int) else None,
               "len": len(raw), "hex": raw[:64].hex()}
        # giải mã nhanh vài field quan trọng
        try:
            if cmd_name == "LOGIN":
                rec["status"] = rd.i8()
            elif cmd_name == "PLAYER_ENTERED":
                rec["place_level"] = rd.i8()
                rec["player_id"] = rd.i64()
                rec["name"] = rd.utf16()
            elif cmd_name == "PLAYER_EXITED":
                rec["place_level"] = rd.i8()
                rec["player_id"] = rd.i64()
            elif cmd_name == "BALANCE_CHANGED":
                rec["raw_i64_try"] = rd.i64()
            elif cmd_name == "ENTER_PLACE":
                rec["place"] = rd.ascii()
            elif cmd_name == "ENTER_STATE":
                rec["state_i32"] = rd.i32()
        except Exception as e:
            rec["decode_error"] = str(e)
        out.append(rec)
        if cmd_name == "PING":
            try:
                ws.send_binary(pack_num(300))
            except Exception:
                pass
    return out


def main():
    user = sys.argv[1]
    pwd = sys.argv[2]
    res, why = http_login(user, pwd)
    if res is None:
        print(json.dumps({"error": why}, ensure_ascii=False))
        return 1
    info = res["info"]
    print("== HTTP LOGIN OK ==")
    print(json.dumps(info, ensure_ascii=False, indent=2))

    ws = None
    try:
        ws = websocket.create_connection(
            WS_URL, timeout=15,
            header=[f"Cookie: {res['cookie']}", "Origin: https://gamevh.net",
                    f"User-Agent: {UA}"],
            cookie=res["cookie"])
        # LOGIN: nick + token + version + "" + game_id + flag(1)
        ws.send_binary(pack_num(302, asc(info["nick"]) + i32(info["token"]) +
                                asc(VERSION) + asc("") + asc(GAME_ID) + i8(1)))
        print("== WS FRAMES ==")
        frames = dump_frames(ws, seconds=25)
        for f in frames:
            print(json.dumps(f, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ws_error": str(e)}, ensure_ascii=False))
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
