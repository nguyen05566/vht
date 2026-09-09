#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xito_test.py — Bot Xì Tố (Texas Hold'em) cho gamevh.net — tài khoản arena11

=== HAI CHẾ ĐỘ ===

1) TỰ TẠO BÀN (khuyên dùng — khỏi cần vào bàn người khác):
     XITO_CREATE=1 XITO_CREATE_BET=1000 XITO_MINUTES=60 python3 xito_test.py
   - Tự tạo bàn mới mức 1000 xu, ngồi làm chủ bàn chờ người vào
   - Có người vào ghế là chơi luôn (AI tự đánh giá bài, tự check/call/raise/fold)
   - Bàn trống quá XITO_REMAKE_S giây (mặc định 900) -> tự tạo bàn mới (lên đầu danh sách)

2) TÌM BÀN THEO MỤC TIÊU:
     XITO_TARGET="Chào Mào" XITO_MINUTES=60 python3 xito_test.py
   - Quét mọi sảnh, dò từng bàn (GET_TABLE_DATA) tìm nick mục tiêu, vào chơi/xem chờ ghế
   - Bàn bị khóa thì thử chen lại mỗi 5s (đợi chủ bàn mở khóa)

Biến môi trường chính:
  XITO_USER / XITO_PASS   tài khoản (mặc định arena11 / nhat123456)
  XITO_MINUTES            số phút chạy (mặc định 20)
  XITO_CREATE             1 = chế độ tự tạo bàn
  XITO_CREATE_BET         mức cược bàn tự tạo (mặc định 1000)
  XITO_REMAKE_S           bàn trống bao lâu thì tạo lại (mặc định 900)
  XITO_TARGET             nick cần tìm (chế độ tìm bàn); "" = chơi với bất kỳ ai
  XITO_ANY_BET            1 = không giới hạn mức cược bàn (mặc định 1)

Protocol rút ra từ client JS (không mã hóa):
  - Khung lệnh 4xx/5xx giống hệt cờ tướng; game bài thêm:
    RAISE_REQ=564 (kèm i32 tiền), CALL_REQ=565, CHECK_REQ=566, FOLD_REQ=567
  - Server đẩy: RAISE=556, CHECK=557, FOLD=560, SHOW_PLAYER_CARD=522,
    DIVIDE_CHIP=562, MOVE=529 (chia bài), SET_TURN=420, ENTER_STATE=419,
    START_MATCH=417, GAMEOVER=418
  - GET_TABLE_DATA_EX=433 (body: ascii "") -> response khai báo state machine
  - CREATE_RULE=405: byte bet_amt_id + byte n_args + [ascii tên + string giá trị]*
    -> response: byte status + ascii đường dẫn bàn (chỉ là ID tương đối "1846")
  - Bị đá: KICK_PLAYER=410 kèm lý do (vd "Table is locked." khi chủ bàn khóa bàn)
"""

import struct
import time
import sys
import os
import re
import random
import faulthandler
import requests
import websocket

# ==================== CẤU HÌNH ====================

WS_URL     = "wss://gamevh.net/ws/gameServer"
LOGIN_URL  = "https://gamevh.net/login.jsp"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
GAME_ID    = "xito"
ZONE_BASE  = "Lobby." + GAME_ID
ROOT_PLACE = ZONE_BASE + ".0"

USER       = os.environ.get("XITO_USER", "arena11")
PASS       = os.environ.get("XITO_PASS", "nhat123456")
RUN_MIN    = float(os.environ.get("XITO_MINUTES", "20"))
RAISE_TEST = os.environ.get("XITO_RAISE_TEST", "0") == "1"   # (cũ) 1 = thử raise 1 lần/ván
ANY_BET    = os.environ.get("XITO_ANY_BET", "1") == "1"      # 1 = không giới hạn mức cược
TARGET_PLAYER = os.environ.get("XITO_TARGET", "Chào Mào")     # "" = chơi với bất kỳ ai
ALONE_WAIT_S = float(os.environ.get("XITO_ALONE_S", "60"))    # ngồi 1 mình bao lâu thì đi tìm bàn khác
RESCAN_S   = float(os.environ.get("XITO_RESCAN_S", "20"))     # chu kỳ quét lại sảnh khi rảnh

# ---- CHẾ ĐỘ TỰ TẠO BÀN ----
# XITO_CREATE=1 : không đi tìm bàn người khác — tự tạo bàn chờ người vào
# XITO_CREATE_BET   : mức cược của bàn (mặc định 1000 xu)
# XITO_REMAKE_S     : ngồi trống quá N giây thì tạo lại bàn mới (lên đầu danh sách)
CREATE_MODE  = os.environ.get("XITO_CREATE", "0") == "1"
CREATE_BET   = int(os.environ.get("XITO_CREATE_BET", "1000"))
CREATE_REMAKE_S = float(os.environ.get("XITO_REMAKE_S", "900"))

# Bản đồ lệnh đầy đủ (từ connection.js của client web)
CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 311: "BROADCAST",
    313: "GET_CLIENT_MODE", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    316: "REQUEST_FREE_CHIP", 317: "TRANSFER", 331: "CHAT.SEND", 335: "CHAT.MSG",
    336: "GET_CURRENT_TIME", 343: "REQUEST_BUY_IN",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 403: "ENTER_PARENT_PLACE",
    404: "ENTER_SIBLING_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED",
    407: "PLAYER_EXITED", 408: "QUICK_PLAY", 409: "SET_TABLE_PASSWORD",
    410: "KICK_PLAYER", 411: "LIST_ZONE_TABLE", 412: "LIST_ZONE_ROOM",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 415: "TABLE_IN_ROOM_CHANGED",
    416: "SLOT_IN_TABLE_CHANGED", 417: "START_MATCH", 418: "GAMEOVER",
    419: "ENTER_STATE", 420: "SET_TURN", 421: "SET_PLAYER_STATUS",
    422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR", 428: "UPDATE_LEVEL",
    431: "BALANCE_CHANGED", 432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 435: "ENTER_PATH_PLACE", 436: "SCORE_CHANGED",
    501: "BET", 502: "PLAY", 518: "HIGHLIGHT", 521: "TAKE_CARD",
    522: "SHOW_PLAYER_CARD", 523: "CLEAR_CARDS", 524: "SET_CARDS",
    525: "SELECT_CARDS", 527: "COMPARE_BAND", 528: "SEND_CARD", 529: "MOVE",
    530: "CHANGE_PIECE", 531: "SET_REMAIN_TURN", 532: "ADD_LOG",
    533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT", 536: "ACCEPT",
    537: "HIT", 538: "STAY", 539: "FIRE_CARD", 540: "PASS_TURN",
    541: "SORT_CARD", 542: "TAKE", 543: "EAT", 544: "REMOVE", 545: "DROP_BAND",
    546: "SELECT_BAND", 547: "DROP_AVAILABLE_BAND", 548: "HINT", 549: "SUBMIT",
    550: "SHOOT", 551: "MOVE_CUE_BALL", 552: "UPDATE_TABLE_STATE", 553: "IGNORE",
    554: "GRAB", 555: "DROP", 556: "RAISE", 557: "CHECK", 558: "DEAL",
    559: "CALL", 560: "FOLD", 561: "OPEN", 562: "DIVIDE_CHIP", 563: "SPREAD",
    564: "RAISE_REQ", 565: "CALL_REQ", 566: "CHECK_REQ", 567: "FOLD_REQ",
    568: "OPEN_REQ", 569: "HIT_REQ", 570: "DROP_REQ", 571: "ANIMATE_EARNED",
    601: "LOGIN_EX", 604: "PLAYER_PROFILE", 605: "LIST_ZONE_PLAYER",
    606: "LIST_TABLE_PLAYER", 609: "SIDE_BET.STARTED", 610: "SIDE_BET.GET_DATA",
    611: "SIDE_BET.BET", 612: "SIDE_BET.CREATED", 613: "SIDE_BET.COMMITTED",
    614: "SIDE_BET.RESULT",
}

# Giải mã lá bài — XÁC MINH BẰNG 23 MẪU SHOWDOWN LIVE (sid thô + tên bài server, xito_run5.out):
#   pair sid%13=0  → «đôi A» (8 mẫu); pair sid%13=12 → «đôi K» (6 mẫu);
#   trips sid%13=6 → «sám cô 7»; thú {7,8} → «thú 9 & 8»; pair sid%13=10 → «đôi J»;
#   pair sid%13=9 → «đôi 10»; mậu thầu: mọi mẫu khớp TRUE_RANKS[sid%13].
#   => rank = TRUE_RANKS[sid % 13] — KHÔNG dịch chuyển. (Log run4 cũ bị lệch do bug
#      parse của bản code cũ — đã loại bỏ khỏi cơ sở chứng cứ.)
#   Thứ tự mạnh→yếu: A K Q J 10 9 8 7 6 5 4 3 2.
TRUE_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["B", "T", "R", "C"]  # suit = sid // 13 (0..3)

def target_in(names):
    """Tìm mục tiêu trong danh sách nick — không phân biệt hoa/thường, khớp một phần."""
    if not TARGET_PLAYER or not names:
        return False
    t = TARGET_PLAYER.lower()
    return any(t in str(n).lower() for n in names)

def rank_idx(sid):
    """rank thật 0=A, 1=2, ..., 12=K — ĐÃ XÁC MINH LIVE"""
    return sid % 13

def pval(r):
    """giá trị poker: A=14, 2..K = 2..13 (A cao nhất)"""
    return 14 if r == 0 else r + 1

def kv(r):
    """độ mạnh kicker 0..1: A=1.0, K=0.917, ..., 2=0.0"""
    return (pval(r) - 2) / 12.0

def card_str(sid):
    if sid == -1: return "??"
    if sid == -2: return "__"
    if sid < 0: return "??"
    try:
        return f"{SUITS[sid // 13]}{TRUE_RANKS[rank_idx(sid)]}"
    except Exception:
        return f"s{sid}"

# ==================== AI: ĐÁNH GIÁ BÀI POKER ====================
# Thứ hạng VN: mậu thầu < đôi < thú < sám cô < sảnh < thùng < cù lũ < tứ quý < thùng phá sảnh
(CAT_HIGH, CAT_PAIR, CAT_TWO, CAT_TRIPS, CAT_STRAIGHT,
 CAT_FLUSH, CAT_FULL, CAT_QUADS, CAT_SF) = range(9)
CAT_NAMES = ["mậu thầu", "đôi", "thú", "sám cô", "sảnh", "thùng", "cù lũ", "tứ quý", "thùng phá sảnh"]
CAT_SCORE = {CAT_HIGH: 0.0, CAT_PAIR: 0.42, CAT_TWO: 0.62, CAT_TRIPS: 0.74,
             CAT_STRAIGHT: 0.82, CAT_FLUSH: 0.88, CAT_FULL: 0.93, CAT_QUADS: 0.97, CAT_SF: 1.0}

def eval_hand(cards):
    """cards: list sid lá (bỏ qua <0). Trả về (cat, score 0..1, mô_tả)."""
    cs = [c for c in cards if c is not None and c >= 0]
    if not cs:
        return (CAT_HIGH, 0.0, "—")
    ranks = [rank_idx(c) for c in cs]
    suits = [c // 13 for c in cs]
    cnt = {}
    for r in ranks:
        cnt[r] = cnt.get(r, 0) + 1
    groups = sorted(cnt.items(), key=lambda g: (-g[1], -pval(g[0])))
    n4 = groups[0][1]
    n2 = groups[1][1] if len(groups) > 1 else 0
    straight_hi = None; flush = False; sf = False
    if len(cs) >= 5:
        pset = set(pval(r) for r in ranks)
        for hi in (14, 13, 12, 11, 10, 9, 8, 7, 6):
            if {hi, hi - 1, hi - 2, hi - 3, hi - 4} <= pset:
                straight_hi = hi; break
        if straight_hi is None and 14 in pset and {2, 3, 4, 5} <= pset:
            straight_hi = 5                      # sảnh A-2-3-4-5
        for s in set(suits):
            if suits.count(s) >= 5:
                flush = True
                ss = set(pval(rank_idx(c)) for c in cs if c // 13 == s)
                for hi in (14, 13, 12, 11, 10, 9, 8, 7, 6):
                    if {hi, hi - 1, hi - 2, hi - 3, hi - 4} <= ss:
                        sf = True; break
                if not sf and 14 in ss and {2, 3, 4, 5} <= ss:
                    sf = True
                break
    if sf: cat = CAT_SF
    elif n4 == 4: cat = CAT_QUADS
    elif n4 == 3 and n2 == 2: cat = CAT_FULL
    elif flush: cat = CAT_FLUSH
    elif straight_hi: cat = CAT_STRAIGHT
    elif n4 == 3: cat = CAT_TRIPS
    elif n4 == 2 and n2 == 2: cat = CAT_TWO
    elif n4 == 2: cat = CAT_PAIR
    else: cat = CAT_HIGH
    top_r = groups[0][0]
    if cat == CAT_HIGH:
        score = 0.04 + 0.34 * kv(top_r)
    elif cat == CAT_PAIR:
        score = CAT_SCORE[cat] + 0.10 * kv(top_r)
    elif cat == CAT_TWO:
        score = CAT_SCORE[cat] + 0.06 * kv(top_r)
    else:
        score = CAT_SCORE[cat] + 0.03 * kv(top_r)
    if cat == CAT_HIGH:
        desc = f"mậu thầu {TRUE_RANKS[top_r]}"
    elif cat == CAT_TWO:
        second = groups[1][0]
        desc = f"thú {TRUE_RANKS[top_r]} & {TRUE_RANKS[second]}"
    elif cat == CAT_FULL:
        second = groups[1][0]
        desc = f"cù lũ {TRUE_RANKS[top_r]} & {TRUE_RANKS[second]}"
    else:
        desc = f"{CAT_NAMES[cat]} {TRUE_RANKS[top_r]}"
    return (cat, min(1.0, score), desc)

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "logs", f"xito_test_{time.strftime('%Y%m%d_%H%M%S')}.log")
_logf = open(LOG_PATH, "a", encoding="utf-8")

def log(tag, msg):
    line = f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}"
    print(line, flush=True)
    try:
        _logf.write(line + "\n"); _logf.flush()
    except Exception:
        pass

# ==================== GIAO THỨC BINARY ====================

class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cb = cmd.encode('ascii')
            result.append((-len(cb)) & 0xFF)
            result.extend(cb)
        else:
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)
    def pack_byte(self, v):  return struct.pack('>b', v)
    def pack_int(self, v):   return struct.pack('>i', v)
    def pack_ascii(self, v):
        enc = v.encode('ascii')[:255]
        return struct.pack('>b', len(enc)) + enc
    def pack_string(self, v):
        enc = v.encode('utf-16-be')
        return struct.pack('>h', len(enc) // 2) + enc

class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()
    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset - length].decode('ascii', errors='replace')
            self.offset += (-length)          # ⚠️ bắt buộc: dịch con trỏ qua phần tên lệnh
            return cmd
        nxt = self.data[self.offset] & 0xFF
        self.offset += 1
        return CMD_NAMES.get((length << 8) | nxt, str((length << 8) | nxt))
    def read_byte(self):
        v = struct.unpack_from('>b', self.data, self.offset)[0]; self.offset += 1; return v
    def read_short(self):
        v = struct.unpack_from('>h', self.data, self.offset)[0]; self.offset += 2; return v
    def read_int(self):
        v = struct.unpack_from('>i', self.data, self.offset)[0]; self.offset += 4; return v
    def read_long(self):
        v = struct.unpack_from('>q', self.data, self.offset)[0]; self.offset += 8; return v
    def read_ascii(self):
        n = self.read_byte()
        if n < 0: n += 256
        s = self.data[self.offset:self.offset + n].decode('ascii', errors='replace')
        self.offset += n; return s
    def read_string(self):
        n = self.read_short()
        s = self.data[self.offset:self.offset + n * 2].decode('utf-16-be', errors='replace')
        self.offset += n * 2; return s
    def read_byte_array(self):
        n = self.read_short()
        if n < 0: n = 0
        b = self.data[self.offset:self.offset + n]
        self.offset += n
        return list(struct.unpack(f'>{n}b', b)) if n else []
    def remaining(self):
        return len(self.data) - self.offset

# ==================== HTTP LOGIN ====================

def http_login(user, passwd):
    s = requests.Session()
    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
    s.headers.update({"User-Agent": ua, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7"})
    s.get(LOGIN_URL, timeout=20)
    resp = s.post(LOGIN_URL, timeout=20,
                  data={"redirect": "/", "USER_NAME": user, "PASSWORD": passwd,
                        "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                  headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                  allow_redirects=True)
    if "login.jsp" in resp.url:
        return None
    game_url = f"https://gamevh.net/play/{GAME_ID}/0"
    page = s.get(game_url, timeout=20).text
    tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page)
    nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page)
    pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page)
    if (not tm or not nm):
        # fallback: token là cấp tài khoản -> lấy từ trang cờ tướng
        page = s.get("https://gamevh.net/play/xiangqi/0", timeout=20).text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page)
        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page)
        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page)
    if not tm or not nm:
        log("HTTP", f"Không lấy được token! url={resp.url}, len(page)={len(page)}")
        return None
    bal = None
    try:
        r = s.get(PROFILE_URL, timeout=15)
        m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', r.text)
        if m:
            bal = int(re.sub(r"[^\d]", "", m.group(1)) or 0)
    except Exception:
        pass
    return {
        "token": int(tm.group(1)),
        "nickname": nm.group(1).strip(),
        "player_id": int(pid.group(1)) if pid else 0,
        "cookie": "; ".join(f"{k}={v}" for k, v in s.cookies.items()),
        "balance": bal,
    }

# ==================== SESSION XÌ TỐ ====================

BET_CODE_CMDS = {"CHECK", "CALL", "RAISE", "FOLD", "OPEN", "BET"}  # client->server: đúng tên lệnh server khai báo trong state

class XitoTester:
    def __init__(self, info):
        self.user = USER
        self.passwd = PASS
        self.token = info["token"]
        self.nickname = info["nickname"]
        self.player_id = info["player_id"]
        self.cookie = info["cookie"]
        self.balance = info["balance"]
        self.conn = Conn()
        self.ws = None
        self.deadline = time.time() + RUN_MIN * 60

        # trạng thái sảnh
        self.bet_amts = []            # [{id, value}]
        self.rooms = []
        self.current_room = 0
        self.table_path = None
        self.in_table = False
        self.is_viewer = False

        # trạng thái bàn
        self.states = {}              # sid -> {code, commands:[{code,name,fill}]}
        self.state_by_code = {}
        self.my_slot = -1
        self.current_state = None
        self.turn_slot = -1
        self.players = {}             # slotId -> {pid, name, chip}
        self.slots_cards = {}         # slotId -> [card sid]
        self.slot_bets = {}           # slotId -> tiền đã cược trong vòng
        self.max_bet = 0
        self.available = 0
        self.match_active = False
        self.raised_this_match = False
        self.ready_sent = False
        self.last_act_at = 0.0
        self._want_play = False
        self._upgrade_at = 0.0
        self._retry_join_at = 0.0
        self._target_tid = None
        self._create_retry_at = 0.0
        self._created_table = False
        self._room_ready = False

        # AI + thống kê
        self.stats = {"hands": 0, "won": 0, "lost": 0, "push": 0, "net": 0,
                      "t_hands": 0, "t_won": 0, "t_lost": 0, "t_net": 0}
        self.target_here = False
        self._acted_key = None
        # tìm bàn theo mục tiêu (GET_TABLE_DATA trước khi vào)
        self._td_queue = []
        self._td_results = {}
        self._td_cands = []
        self._td_deadline = 0.0
        self._td_done = False

    # ---------- tiện ích ----------

    def send(self, cmd, data=b''):
        if not self.ws:
            return
        try:
            self.ws.send_binary(self.conn.pack(cmd, data))
            name = cmd if isinstance(cmd, str) else CMD_NAMES.get(cmd, str(cmd))
            log("SEND", f"{name} hex={data.hex() if data else '-'}")
        except Exception as e:
            log("SEND", f"lỗi gửi: {e}")

    # ---------- kết nối / login ----------

    def connect(self):
        import websocket
        log("WS", "Đang kết nối " + WS_URL)
        self.ws = websocket.create_connection(
            WS_URL, cookie=self.cookie,
            header={"Origin": "https://gamevh.net"}, timeout=15)
        log("WS", "Đã kết nối WS")
        data = bytearray()
        data.extend(self.conn.pack_ascii(self.nickname))
        data.extend(self.conn.pack_int(self.token))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send("LOGIN", bytes(data))
        # chờ LOGIN response
        end = time.time() + 12
        while time.time() < end:
            raw = self.recv(2)
            if raw is None:
                continue
            msg = InboundMessage(raw)
            log("WS", f"帧 [{msg.command}] hex={bytes(raw).hex()[:160]}")
            if msg.command == "PING":
                self.send("PONG"); continue
            if msg.command == "LOGIN":
                status = msg.read_byte()
                if status == 0:
                    path = msg.read_string()
                    cookie = msg.read_ascii()
                    log("WS", f"Login OK — resume_path='{path}'")
                    return True
                log("WS", f"Login BỊ TỪ CHỐI status={status}")
                # thử đọc tiếp xem server kèm thông điệp lỗi
                try:
                    err = msg.read_string()
                    log("WS", f"Server nói: '{err}'")
                except Exception:
                    pass
                return False
            log("WS", f"gói khác khi chờ login: {msg.command}")
        log("WS", "Hết giờ chờ LOGIN response")
        return False

    def recv(self, timeout=2.0):
        try:
            self.ws.settimeout(timeout)
            return self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        except Exception as e:
            log("WS", f"recv lỗi: {type(e).__name__}: {e}")
            raise

    # ---------- bước 1: vào sảnh, lấy mức cược + danh sách sảnh ----------

    def enter_zone(self):
        self._pending = ('zone', None)
        self.send("ENTER_PLACE",
                  self.conn.pack_ascii(ROOT_PLACE) + self.conn.pack_string("")
                  + self.conn.pack_byte(1))

    def after_zone_ok(self):
        log("ZONE", f"Đã vào {ROOT_PLACE} — lấy mức cược & danh sách sảnh")
        self.send("LIST_BET_AMT")
        self.send("LIST_ZONE_ROOM")

    # ---------- bước 2: quét sảnh tìm bàn ----------

    def scan_rooms(self, msg):
        status = msg.read_byte()
        if status != 0:
            log("ROOMS", f"status={status}"); return
        n = msg.read_byte()
        self.rooms = []
        for _ in range(n):
            rid = msg.read_byte()
            name = msg.read_string()
            clients = msg.read_short()
            tables = msg.read_short()
            maxt = msg.read_short()
            self.rooms.append({"id": rid, "name": name, "clients": clients, "tables": tables})
        info = ", ".join(f"#{r['id']}{r['name']}({r['clients']}ng/{r['tables']}b)" for r in self.rooms)
        log("ROOMS", f"{n} sảnh: {info}")
        # ưu tiên sảnh ĐÔNG NGƯỜI NHẤT (nơi mục tiêu dễ xuất hiện)
        for r in sorted(self.rooms, key=lambda x: -x["clients"]):
            if r["clients"] > 0 and r["tables"] > 0:
                self.enter_room(r["id"])
                return
        log("ROOMS", "Không sảnh nào có người — nghỉ 30s rồi quét lại")
        self._rescan_at = time.time() + 30

    def enter_room(self, rid):
        log("ROOMS", f"➡️ Vào sảnh #{rid}")
        self.current_room = rid
        self._pending = ('room', rid)
        self.send("ENTER_PLACE",
                  self.conn.pack_ascii(f"{ZONE_BASE}.{rid}") + self.conn.pack_string("")
                  + self.conn.pack_byte(1))

    def after_room_ok(self):
        self._room_ready = True
        if CREATE_MODE:
            log("TABLES", "Chế độ tạo bàn — bỏ qua quét, sắp tạo bàn mới")
            return
        self.send("LIST_ZONE_TABLE", self.conn.pack_byte(1))  # 1 = bàn còn trống chỗ

    def scan_tables(self, msg):
        status = msg.read_byte()
        if status != 0:
            log("TABLES", f"status={status}"); return
        n = msg.read_int()
        cands = []
        for _ in range(n):
            tid = msg.read_short()
            name = msg.read_string()
            ttype = msg.read_byte()
            bet_id = msg.read_byte()
            slots = msg.read_byte()
            playing = (msg.read_byte() == 0)   # byte 0 = đang chơi (theo client web)
            pwd = (msg.read_byte() == 1)
            bet = self.bet_value(bet_id)
            if pwd: continue
            if slots < 1: continue             # cần ít nhất 1 người
            if not ANY_BET and bet and bet > 5000: continue
            cands.append({"id": tid, "name": name, "bet": bet, "slots": slots,
                          "playing": playing, "type": ttype})
        if not cands:
            log("TABLES", f"Sảnh #{self.current_room}: {n} bàn, không có bàn hợp lệ")
            self._next_room_or_wait()
            return
        # ưu tiên: bàn đang chờ có người > bàn đang chơi (xem)
        cands.sort(key=lambda t: (0 if not t["playing"] else 1, -t["slots"]))
        if TARGET_PLAYER:
            # hỏi dữ liệu từng bàn (414) để tìm bàn có mục tiêu trước khi vào
            self._td_cands = cands
            self._td_results = {}
            self._td_queue = [t["id"] for t in cands[:30]]   # dò tối đa 30 bàn để không lỡ mục tiêu
            self._td_deadline = time.time() + 9.0
            self._td_done = False
            log("TABLES", f"Sảnh #{self.current_room}: {n} bàn -> tìm '{TARGET_PLAYER}' trong "
                          f"{len(self._td_queue)} bàn ưu tiên...")
            for tid in list(self._td_queue):
                self.send("GET_TABLE_DATA", self.conn.pack_ascii(
                    f"{ZONE_BASE}.{self.current_room}.{tid}"))
            return
        self._pick_and_join(cands, None)

    def _pick_and_join(self, cands, td_map):
        """Chọn bàn: ưu tiên bàn có mục tiêu, rồi tới bàn tốt nhất còn lại."""
        chosen = None; mode = "PLAY"
        if td_map and TARGET_PLAYER:
            for t in cands:
                names = td_map.get(t["id"])
                if names and target_in(names) and not t["playing"]:
                    chosen = t; mode = "PLAY"
                    self._target_tid = t["id"]
                    log("TABLES", f"🎯 Tìm thấy {TARGET_PLAYER} ở bàn #{t['id']} "
                                  f"'{t['name']}' cược={t['bet']}xu -> vào CHƠI")
                    break
            if chosen is None:
                for t in cands:
                    names = td_map.get(t["id"])
                    if names and target_in(names):
                        chosen = t; mode = "PLAY_FALLBACK"
                        self._target_tid = t["id"]
                        log("TABLES", f"🎯 {TARGET_PLAYER} đang chơi ở bàn #{t['id']} "
                                      f"'{t['name']}' -> thử vào CHƠI (bị chặn thì XEM)")
                        break
        if chosen is None:
            t = cands[0]
            chosen = t
            mode = "PLAY" if not t["playing"] else "VIEW"
            log("TABLES", f"Không thấy mục tiêu -> chọn #{t['id']} '{t['name']}' "
                          f"cược={t['bet']}xu ghế={t['slots']} "
                          f"{'ĐANG CHƠI(xem)' if t['playing'] else 'ĐANG CHỜ(chơi)'} -> {mode}")
        self._join_table(chosen["id"], mode in ("PLAY", "PLAY_FALLBACK"),
                         fallback_view=(mode == "PLAY_FALLBACK"))

    def _next_room_or_wait(self):
        others = [r for r in self.rooms if r["id"] != self.current_room
                  and r["clients"] > 0 and r["tables"] > 0]
        if others:
            self.enter_room(others[0]["id"])
        else:
            log("TABLES", "Hết sảnh không có bàn — nghỉ 30s")
            self._rescan_at = time.time() + 30
            self.enter_zone()

    def bet_value(self, bet_id):
        for ba in self.bet_amts:
            if ba["id"] == bet_id:
                return ba["value"]
        return None

    # ---------- chế độ tự tạo bàn ----------

    def send_create_table(self, bet_value):
        """CREATE_RULE: byte bet_amt_id + byte n_args + [ascii tên + string giá trị]*"""
        bet_amt_id = None
        for ba in self.bet_amts:
            if ba["value"] == bet_value:
                bet_amt_id = ba["id"]
                break
        if bet_amt_id is None and self.bet_amts:
            vals = sorted(b["value"] for b in self.bet_amts)
            lower = [v for v in vals if v <= bet_value]
            pick = lower[-1] if lower else vals[0]
            log("CREATE", f"⚠️ Không có mức {bet_value}xu (mức hiện có: {vals}) -> dùng {pick}xu")
            bet_value = pick
            for ba in self.bet_amts:
                if ba["value"] == bet_value:
                    bet_amt_id = ba["id"]
                    break
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id or 0))
        data.extend(self.conn.pack_byte(0))          # 0 tham số — dùng mặc định server
        log("CREATE", f"🎯 Gửi CREATE_RULE — bàn {bet_value}xu (bet_id={bet_amt_id})")
        self.send("CREATE_RULE", bytes(data))

    def _handle_create_rule(self, msg):
        status = msg.read_byte()
        if status != 0:
            self._pending = None
            self._create_retry_at = time.time() + 10.0
            log("CREATE", f"❌ Tạo bàn thất bại (status={status}) -> thử lại sau 10s")
            return
        rel = msg.read_ascii()
        # path từ server có thể là ID tương đối ("1842") -> ghép thành path đầy đủ
        if rel.isdigit():
            table_path = f"{ZONE_BASE}.{self.current_room}.{rel}"
        else:
            table_path = rel
        log("CREATE", f"🎉 Tạo bàn thành công: {table_path} — vào ngồi chờ người...")
        self.table_path = table_path
        self.is_viewer = False
        self._created_table = True
        self._want_play = False
        self._alone_since = None
        self._pending = ('table', table_path)
        self.send("ENTER_PLACE",
                  self.conn.pack_ascii(table_path) + self.conn.pack_string("")
                  + self.conn.pack_byte(1))

    # ---------- bước 3: vào bàn ----------

    def _join_table(self, tid, play_mode, fallback_view=False):
        self.table_path = f"{ZONE_BASE}.{self.current_room}.{tid}"
        self.is_viewer = not play_mode
        self._pending = ('table', self.table_path)
        self._join_fallback_view = fallback_view
        mode = 1 if play_mode else 0
        log("TABLE", f"🚪 ENTER_PLACE {self.table_path} mode={'CHƠI' if play_mode else 'XEM'}")
        self.send("ENTER_PLACE",
                  self.conn.pack_ascii(self.table_path) + self.conn.pack_string("")
                  + self.conn.pack_byte(mode))

    def after_table_ok(self, msg):
        self.in_table = True
        self.ready_sent = False
        log("TABLE", f"✅ Đã vào bàn {self.table_path} ({'người xem' if self.is_viewer else 'người chơi'})")
        # Gói 401 response còn dữ liệu blockSoftware... bỏ qua, lấy dữ liệu bàn:
        self.send("GET_TABLE_DATA_EX", self.conn.pack_ascii(""))

    def parse_table_data_ex(self, msg):
        """Response 433: status + states + bảng + điểm + bài + tiền + args"""
        try:
            # ---- fillStateData ----
            n_states = msg.read_byte()
            self.states = {}; self.state_by_code = {}
            for _ in range(n_states):
                sid = msg.read_byte()
                code = msg.read_ascii()
                mode = msg.read_byte()
                ncmds = msg.read_byte()
                cmds = []
                for _c in range(ncmds):
                    pos = msg.read_byte()
                    ccode = msg.read_ascii()
                    cname = msg.read_string()
                    fill = msg.read_byte()
                    confirm = msg.read_byte()
                    cmds.append({"position": pos, "code": ccode, "name": cname,
                                 "fill": fill == 1, "confirm": confirm == 1})
                st = {"sid": sid, "code": code, "mode": mode, "commands": cmds}
                self.states[sid] = st
                if code:
                    self.state_by_code[code] = st
            self.begin_state = msg.read_byte()
            states_desc = "; ".join(
                f"{s['code'] or s['sid']}:[{','.join(c['code'] or c['name'] for c in s['commands'])}]"
                for s in self.states.values())
            log("STATE", f"State machine ({n_states}): {states_desc} | begin={self.begin_state}")

            # ---- fillTableData ----
            self.my_slot = msg.read_byte()
            playing = msg.read_byte() == 1
            self.match_active = playing          # đồng bộ ván đang chạy khi mới vào bàn
            if self.my_slot >= 0 and self._want_play:
                self.is_viewer = False
                self._want_play = False
                log("TABLE", "🎉 Đã LẤY ĐƯỢC GHẾ — chuyển từ XEM sang CHƠI")
            elif self.my_slot < 0 and not self.is_viewer:
                # vào CHƠI OK nhưng chưa có ghế -> coi như đang xem, tiếp tục xin
                self.is_viewer = True
                self._want_play = True
                log("TABLE", "🪑 Vào bàn rồi nhưng chưa có ghế -> chuyển chế độ chờ ghế")
            self.players = {}
            n = msg.read_byte()
            for _ in range(n):
                slot = msg.read_byte()
                pid = msg.read_long()
                fname = msg.read_string()
                av_id = msg.read_short()
                av = msg.read_ascii()
                tag = msg.read_byte()
                chip = msg.read_long()
                star = msg.read_long()
                score = msg.read_long()
                level = msg.read_byte()
                owner = msg.read_byte() == 1
                self.players[slot] = {"pid": pid, "name": fname, "chip": chip, "owner": owner}
            turn_slot = msg.read_byte()
            turn_to = msg.read_short()
            remain = msg.read_short()
            cur_state = msg.read_byte()
            names = ", ".join(f"s{k}:{v['name']}({v['chip']:,}xu{'*' if v['owner'] else ''})"
                              for k, v in self.players.items())
            log("TABLE", f"Dữ liệu bàn: slot_của_ta={self.my_slot} chơi={playing} | người: {names}")
            log("TABLE", f"Lượt hiện tại: slot={turn_slot} timeout={turn_to}s state={cur_state}")
            self.turn_slot = turn_slot
            self.current_state = cur_state
            self.match_active = playing

            # ---- fillPlayerMatchPoint ----
            n = msg.read_byte()
            for _ in range(n):
                slot = msg.read_byte()
                pt = msg.read_int()

            # ---- fillBoardData (xì tố) ----
            first_bet = msg.read_int()
            self.first_bet = first_bet
            n = msg.read_byte()
            self.slots_cards = {}; self.slot_bets = {}; self.max_bet = first_bet
            for _ in range(n):
                slot = msg.read_byte()
                nlines = msg.read_byte()
                cards = []
                for _l in range(nlines):
                    line = msg.read_byte() - 1
                    ccnt = msg.read_byte()
                    if ccnt < 0:
                        cards = msg.read_byte_array()
                    else:
                        cards = [-1] * ccnt
                avail = msg.read_int()
                bet = msg.read_int()
                self.slot_bets[slot] = bet
                if slot == self.my_slot:
                    self.available = avail
                    self.max_bet = max(self.max_bet, bet)
                self.slots_cards[slot] = cards
                cs = " ".join(card_str(c) for c in cards) if cards else "-"
                log("BOARD", f"slot={slot} bài=[{cs}] sẵn={avail:,} cược_vòng={bet:,}")
            log("BOARD", f"Cược đầu (firstBet)={first_bet:,} | max_bet={self.max_bet:,} | "
                         f"xu trống của ta={self.available:,}")

            # ---- isAutoStart + currency + table args ----
            self.is_autostart = msg.read_byte() == 1
            currency = msg.read_byte()
            n_args = msg.read_byte()
            args = {}
            for _a in range(n_args):
                k = msg.read_ascii()
                v = msg.read_string()
                args[k] = v
            log("TABLE", f"autoStart={self.is_autostart} currency={currency} args={args}")
            return True
        except Exception as e:
            log("PARSE", f"Lỗi parse 433: {e} | hex={msg.data.hex()}")
            return False

    # ---------- lượt chơi: ra quyết định ----------

    def state_commands(self):
        st = self.states.get(self.current_state) or {}
        return st.get("commands", [])

    def _turn_key(self):
        """Ngữ cảnh lượt: chống trigger trùng (SET_TURN + ENTER_STATE cùng lúc)."""
        return (self.current_state, self.turn_slot, self.max_bet,
                len(self.slots_cards.get(self.my_slot, [])))

    def ai_decide(self, betting):
        """Bộ não xì tố: đánh giá bài mình + bài ngửa đối thủ + giá gọi."""
        my_cards = self.slots_cards.get(self.my_slot, [])
        my_cat, my_score, my_desc = eval_hand(my_cards)
        opp_best = 0.0; opp_desc = "—"
        for s in self.players:
            if s == self.my_slot:
                continue
            oc = self.slots_cards.get(s, [])
            vis = [c for c in oc if c >= 0]
            hidden = len([c for c in oc if c == -1])
            _c, s2, d2 = eval_hand(vis)
            if hidden:
                s2 = min(1.0, s2 + 0.08)      # lá úp chưa lật — cộng rủi ro
            if s2 > opp_best:
                opp_best, opp_desc = s2, d2
        my_bet = self.slot_bets.get(self.my_slot, 0)
        call_cost = max(0, self.max_bet - my_bet)
        step = self.first_bet or (self.bet_amts[0]["value"] if self.bet_amts else 500)
        pot = sum(self.slot_bets.values())
        log("AI", f"bài_ta=[{' '.join(card_str(c) for c in my_cards) or '—'}] "
                  f"«{my_desc}» {my_score:.2f} | đối_thủ≈{opp_best:.2f} ({opp_desc}) | "
                  f"cost={call_cost:,} pot≈{pot:,} sẵn={self.available:,}")
        strong = my_cat >= CAT_TWO or my_score >= 0.72
        beats_opp = my_score >= opp_best + 0.08
        premium_pair = my_cat == CAT_PAIR and my_score >= 0.50   # đôi Q/K/A
        codes = set(betting.keys())
        if "CHECK" in codes:
            if (strong or beats_opp) and random.random() < 0.75:
                return ("RAISE", self.max_bet + step)
            if my_score >= 0.36 and random.random() < 0.45:
                return ("RAISE", self.max_bet + step)   # c-bet ăn ante khi bàn bị động
            return ("CHECK", None)
        if "CALL" in codes:
            # TRẦN AN TOÀN: không đuổi all-in của cá mập với bài vừa
            if my_cat >= CAT_TRIPS:               # sám cô+ — theo mọi mức
                return ("CALL", None)
            if my_cat >= CAT_TWO and call_cost <= step * 50:   # thú — theo tới 50x cược gốc
                return ("CALL", None)
            if premium_pair:
                if call_cost <= step * 30:        # đôi Q/K/A — chỉ theo mức hợp lý
                    return ("CALL", None)
                log("AI", f"✂️ Đôi cao nhưng phải trả {call_cost:,} (>30x cược gốc) -> FOLD")
                return ("FOLD", None)
            if my_score >= opp_best - 0.03 and call_cost <= step * 4:
                return ("CALL", None)
            if my_score >= 0.36 and call_cost <= step * 2:
                return ("CALL", None)
            if my_score >= 0.30 and call_cost <= step:
                return ("CALL", None)
            return ("FOLD", None)
        if "OPEN" in codes:
            if strong or (premium_pair and random.random() < 0.8) \
                    or (my_score >= 0.45 and opp_best < 0.50):
                return ("OPEN", self.max_bet + step)
            return ("FOLD", None)
        if "FOLD" in codes:
            return ("FOLD", None)
        return (None, None)

    def act_on_turn(self):
        """Server vừa chuyển state / lượt — nếu là lượt cược của ta thì AI quyết định."""
        if self.is_viewer or self.my_slot < 0:
            return
        if self.turn_slot != self.my_slot:
            return
        cmds = self.state_commands()
        codes = {c["code"]: c for c in cmds if c["code"]}
        betting = {k: v for k, v in codes.items() if k in BET_CODE_CMDS}
        if not betting:
            return
        key = self._turn_key()
        if key == self._acted_key:
            log("AI", "bỏ qua trigger trùng (cùng ngữ cảnh lượt)")
            return
        action, amount = self.ai_decide(betting)
        if action and action in betting:
            if self._fire_command(action, betting[action], amount):
                return
            log("AI", f"{action} gửi thất bại -> phương án dự phòng")
        for want in ("CALL", "CHECK", "FOLD"):
            if want in betting:
                self._fire_command(want, betting[want])
                return

    def _fire_command(self, code, cmd, amount=None):
        body = b''
        if cmd.get("fill"):
            step = self.first_bet or (self.bet_amts[0]["value"] if self.bet_amts else 500)
            if amount is None:
                amount = self.max_bet + step
            amount = int(amount)
            if amount > self.available:
                amount = int(self.available)          # tất tay
            if amount <= self.max_bet:
                log("TURN", f"Muốn {code} nhưng amount={amount:,} <= max_bet={self.max_bet:,} "
                            f"(sẵn={self.available:,}) -> không gửi")
                return False
            body = self.conn.pack_int(amount)
            log("TURN", f"🃏 Gửi {code} amount={amount:,} (sẵn={self.available:,}, max_bet={self.max_bet:,})")
            if code in ("RAISE", "OPEN"):
                self.raised_this_match = True
        else:
            log("TURN", f"🃏 Gửi {code} (không kèm tiền)")
        self.last_act_at = time.time()
        self._acted_key = self._turn_key()
        self.send(code, body)
        return True

    # ---------- xử lý gói sự kiện ----------

    def handle(self, msg):
        cmd = msg.command
        if cmd == "PING":
            log("PKT", "PING từ server -> PONG")
            self.send("PONG"); return
        if cmd == "CREATE_RULE":
            self._handle_create_rule(msg)
            return
        if cmd == "KICK_PLAYER":
            try:
                reason = msg.read_string() if msg.remaining() else ""
            except Exception:
                reason = ""
            log("TABLE", f"🚫 Bị ĐÁ khỏi bàn ({reason or 'không rõ'}) -> rời bàn quét lại")
            self.in_table = False
            self.my_slot = -1
            self.match_active = False
            self.is_viewer = False
            self._want_play = False
            self.table_path = None
            self._upgrade_at = time.time() + 12.0
            self.enter_zone()
            return
        if cmd == "ENTER_PLACE":
            self._pending = getattr(self, "_pending", None)
            status = msg.read_byte()
            kind, ref = self._pending if self._pending else (None, None)
            if kind is None and self.in_table and getattr(self, '_want_play', False):
                kind, ref = 'table', self.table_path   # phản hồi cho yêu cầu xin ghế lần 2
            self._pending = None
            if kind == 'zone':
                if status == 0: self.after_zone_ok()
                else: log("ZONE", f"Vào zone lỗi status={status}")
            elif kind == 'room':
                if status == 0: self.after_room_ok()
                else: log("ROOMS", f"Vào sảnh #{ref} lỗi status={status}")
            elif kind == 'table':
                if status == 0:
                    self.after_table_ok(msg)
                elif getattr(self, '_join_fallback_view', False) and not self.is_viewer:
                    # vào CHƠI bị chặn (bàn đầy?) -> rơi về XEM chờ ghế
                    self.is_viewer = True
                    self._join_fallback_view = False
                    self._upgrade_at = time.time() + 6.0
                    log("TABLE", "👀 Vào CHƠI bị chặn (bàn đầy?) -> chuyển XEM chờ ghế")
                elif self.is_viewer and self.my_slot < 0:
                    # xin ghế bị từ chối (bàn đầy/đang giữa ván) — ngồi xem tiếp, thử lại sau
                    self._want_play = False
                    self._upgrade_at = time.time() + 6.0
                    log("TABLE", "⏳ Xin ghế chưa được — ngồi xem tiếp, chờ ván xong thử lại")
                else:
                    # bị chặn — thử lại sau 5s (không quét lại toàn bộ)
                    self.in_table = False
                    self._retry_join_at = time.time() + 5.0
                    log("TABLE", "⏳ Vào bàn bị chặn (khóa/đầy?) -> thử lại sau 5s")
            else:
                log("PLACE", f"401 đẩy (status={status})")
            return
        if cmd == "LIST_BET_AMT":
            if msg.read_byte() != 0: return
            n = msg.read_byte()
            self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(n)]
            log("BET", f"Mức cược: {[b['value'] for b in self.bet_amts]}")
            return
        if cmd == "LIST_ZONE_ROOM":
            self.scan_rooms(msg); return
        if cmd == "LIST_ZONE_TABLE":
            self.scan_tables(msg); return
        if cmd == "GET_TABLE_DATA_EX":
            if msg.read_byte() == 0:
                self.parse_table_data_ex(msg)
            return
        if cmd == "GET_TABLE_DATA":
            status = msg.read_byte()
            tid = None
            if self._td_queue:
                tid = self._td_queue.pop(0)
            names = []
            if status == 0:
                try:
                    owner = msg.read_long()
                    n = msg.read_byte()
                    for _ in range(n):
                        pid = msg.read_long()
                        fname = msg.read_string()
                        _av = msg.read_ascii()
                        _avid = msg.read_short()
                        _tag = msg.read_byte()
                        chip = msg.read_long()
                        _star = msg.read_long()
                        _score = msg.read_long()
                        _level = msg.read_byte()
                        if fname:
                            names.append(fname)
                except Exception as e:
                    log("SCAN414", f"parse lỗi: {e}")
            if tid is not None:
                self._td_results[tid] = names
                log("SCAN414", f"bàn #{tid}: {names or 'trống'}")
            if (not self._td_queue) and (not self._td_done) and self._td_cands:
                self._td_done = True
                self._pick_and_join(self._td_cands, self._td_results)
            return
        if cmd == "SET_TURN":
            slot = msg.read_byte()
            to = msg.read_short()
            if slot == -2:
                log("TURN", f"⏱ Đếm ngược bắt đầu: {to}s")
                return
            remain = msg.read_short()
            self.turn_slot = slot
            who = self.players.get(slot, {}).get("name", f"slot{slot}")
            mine = " (LƯỢT CỦA TA)" if slot == self.my_slot else ""
            log("TURN", f"👉 Lượt của {who} [{slot}] timeout={to}s{mine}")
            if slot == self.my_slot:
                time.sleep(random.uniform(0.5, 1.2))
                self.act_on_turn()
            return
        if cmd == "ENTER_STATE":
            sid = msg.read_byte()
            self.current_state = sid
            st = self.states.get(sid)
            codes = [c['code'] or c['name'] for c in (st or {}).get('commands', [])]
            log("STATE", f"→ state={sid} ({(st or {}).get('code','?')}) lệnh={codes}")
            time.sleep(random.uniform(0.5, 1.2))
            self.act_on_turn()
            return
        if cmd == "START_MATCH":
            # fillPlayerMatchPoint + fillBoardData
            try:
                n = msg.read_byte()
                for _ in range(n):
                    slot = msg.read_byte(); pt = msg.read_int()
                first_bet = msg.read_int()
                self.first_bet = first_bet
                self.max_bet = first_bet
                self.raised_this_match = False
                self.slots_cards = {}; self.slot_bets = {}
                n = msg.read_byte()
                for _ in range(n):
                    slot = msg.read_byte()
                    nlines = msg.read_byte()
                    cards = []
                    for _l in range(nlines):
                        line = msg.read_byte() - 1
                        ccnt = msg.read_byte()
                        if ccnt < 0:
                            cards = msg.read_byte_array()
                        else:
                            cards = [-1] * ccnt
                    avail = msg.read_int()
                    bet = msg.read_int()
                    self.slot_bets[slot] = bet
                    if slot == self.my_slot:
                        self.available = avail
                    self.slots_cards[slot] = cards
                    cs = " ".join(card_str(c) for c in cards) if cards else "-"
                    log("BOARD", f"slot={slot} bài=[{cs}] sẵn={avail:,} cược={bet:,}")
                self.match_active = True
                self.turn_slot = -1
                log("GAME", f"🎬 BẮT ĐẦU VÁN — cược đầu={first_bet:,}, ta có "
                            f"{' '.join(card_str(c) for c in self.slots_cards.get(self.my_slot, [])) or '?'}")
            except Exception as e:
                log("PARSE", f"Lỗi parse START_MATCH: {e} hex={msg.data.hex()}")
            return
        if cmd == "MOVE":
            try:
                ids = msg.read_byte_array()
                s_slot = msg.read_byte(); s_line = msg.read_byte() - 1
                t_slot = msg.read_byte(); t_line = msg.read_byte() - 1
                t_idx = msg.read_byte()
                cur = self.slots_cards.setdefault(t_slot, [])
                if t_idx < 0:
                    cur.extend(ids)                      # targetIndex=-1: nối vào cuối
                else:
                    while len(cur) < t_idx + len(ids):   # đảm bảo đủ chỗ cho dãy lá
                        cur.append(-2)
                    for i, sid in enumerate(ids):
                        cur[t_idx + i] = sid
                cs = " ".join(card_str(c) for c in ids)
                log("CARD", f"🂠 Chia {len(ids)} lá [{cs}] -> slot={t_slot}")
            except Exception as e:
                log("PARSE", f"Lỗi parse MOVE: {e}")
            return
        if cmd == "SHOW_PLAYER_CARD":
            try:
                slot = msg.read_byte()
                ids = msg.read_byte_array()
                band = msg.read_string()
                self.slots_cards[slot] = ids
                cs = " ".join(card_str(c) for c in ids)
                log("CARD", f"👁 Lật bài slot={slot}: [{cs}] {'«'+band+'»' if band else ''}")
            except Exception as e:
                log("PARSE", f"Lỗi parse SHOW_PLAYER_CARD: {e}")
            return
        if cmd == "RAISE":
            try:
                slot = msg.read_byte()
                rem = msg.remaining()
                amt = None
                if rem >= 6:
                    rtype = msg.read_ascii()
                    amt = msg.read_int()
                elif rem == 4:
                    rtype = "raise"          # dạng rút gọn: slot + i32 (không có tên)
                    amt = msg.read_int()
                elif rem >= 1:
                    rtype = msg.read_ascii()
                else:
                    rtype = "?"
                if amt is not None:
                    self.slot_bets[slot] = amt
                    self.max_bet = max(self.max_bet, amt)
                who = self.players.get(slot, {}).get("name", f"slot{slot}")
                log("BET", f"💰 {who} [{slot}] {rtype}" +
                          (f" -> tổng cược vòng={amt:,} (max={self.max_bet:,})" if amt is not None else ""))
            except Exception as e:
                log("PARSE", f"Lỗi parse RAISE: {e}")
            return
        if cmd == "CHECK":
            try:
                slot = msg.read_byte()
                who = self.players.get(slot, {}).get("name", f"slot{slot}")
                log("BET", f"✋ {who} [{slot}] CHECK")
            except Exception as e:
                log("PARSE", f"Lỗi parse CHECK: {e}")
            return
        if cmd == "FOLD":
            try:
                slot = msg.read_byte()
                who = self.players.get(slot, {}).get("name", f"slot{slot}")
                log("BET", f"🗑 {who} [{slot}] FOLD")
            except Exception as e:
                log("PARSE", f"Lỗi parse FOLD: {e}")
            return
        if cmd == "DIVIDE_CHIP":
            try:
                n = msg.read_byte()
                parts = []
                for _ in range(n):
                    slot = msg.read_byte()
                    earn = msg.read_int()
                    parts.append(f"{self.players.get(slot, {}).get('name', f'slot{slot}')}={earn:+,}")
                log("GAME", f"💰 CHIA TIỀN: {', '.join(parts)}")
            except Exception as e:
                log("PARSE", f"Lỗi parse DIVIDE_CHIP: {e}")
            return
        if cmd == "GAMEOVER":
            try:
                n = msg.read_byte()
                parts = []; rows = []
                for _ in range(n):
                    slot = msg.read_byte()
                    grade = msg.read_byte()
                    earn = msg.read_long()
                    rows.append((slot, grade, earn))
                    parts.append(f"{self.players.get(slot, {}).get('name', f'slot{slot}')}"
                                 f"(grade={grade}, {earn:+,}xu)")
                result = msg.read_string()
                log("GAME", f"🏁 KẾT THÚC: {', '.join(parts)}")
                if result:
                    log("GAME", f"Kết quả: {result}")
                # ---- thống kê từng ván ----
                earn_me = next((e for s, g, e in rows if s == self.my_slot), None)
                st = self.stats
                if earn_me is not None:
                    st["hands"] += 1
                    st["net"] += earn_me
                    if earn_me > 0: st["won"] += 1
                    elif earn_me < 0: st["lost"] += 1
                    else: st["push"] += 1
                    names = [p.get("name", "") for p in self.players.values()]
                    if target_in(names):
                        st["t_hands"] += 1
                        st["t_net"] += earn_me
                        if earn_me > 0: st["t_won"] += 1
                        elif earn_me < 0: st["t_lost"] += 1
                    log("STATS", f"Ván #{st['hands']}: {earn_me:+,}xu | tổng "
                                 f"{st['won']}-{st['lost']}-{st['push']} net={st['net']:+,} | "
                                 f"với '{TARGET_PLAYER}': {st['t_hands']} ván "
                                 f"({st['t_won']}-{st['t_lost']}) net={st['t_net']:+,}")
                self.match_active = False
                self.slots_cards = {}; self.slot_bets = {}
                # bàn không auto-start mới cần bấm "Bắt đầu" (SET_READY body rỗng như client)
                if (not self.is_viewer and self.my_slot >= 0
                        and not getattr(self, 'is_autostart', True) and len(self.players) >= 2):
                    time.sleep(3.0)
                    self.send("SET_READY")
            except Exception as e:
                log("PARSE", f"Lỗi parse GAMEOVER: {e}")
            return
        if cmd == "SLOT_IN_TABLE_CHANGED":
            try:
                fname = msg.read_string()
                slot = msg.read_byte()
                chip = msg.read_long()
                score = msg.read_long()
                level = msg.read_byte()
                av_id = msg.read_short()
                av = msg.read_ascii()
                tag = msg.read_byte()
                owner = msg.read_byte() == 1
                pid = msg.read_long()
                star = msg.read_long()
                if pid > 0:
                    self.players[slot] = {"pid": pid, "name": fname, "chip": chip, "owner": owner}
                    if pid == self.player_id and self.my_slot < 0:
                        self.my_slot = slot
                    log("PLAYER", f"🪑 slot{slot} <- {fname} (id={pid}, {chip:,}xu)"
                                  f"{' [CHỦ BÀN]' if owner else ''}")
                    if pid == self.player_id:
                        self.my_slot = slot
                else:
                    self.players.pop(slot, None)
                    log("PLAYER", f"🚪 slot{slot} trống")
            except Exception as e:
                log("PARSE", f"Lỗi parse SLOT_IN_TABLE_CHANGED: {e}")
            return
        if cmd in ("PLAYER_ENTERED", "PLAYER_EXITED"):
            try:
                lvl = msg.read_byte()
                pid = msg.read_long()
                name = msg.read_string()
                log("PLAYER", f"{'👤 vào' if cmd == 'PLAYER_ENTERED' else '👋 ra'} {name} (id={pid})")
            except Exception:
                pass
            return
        if cmd == "BALANCE_CHANGED":
            try:
                slot = msg.read_byte()
                chip = msg.read_long(); star = msg.read_long()
                ch = msg.read_long(); st = msg.read_long()
                if slot == self.my_slot:
                    log("XU", f"Số dư của ta: {chip:,} xu (giữ lại {ch:,})")
            except Exception:
                pass
            return
        if cmd == "ALERT":
            try:
                content = msg.read_string()
                atype = msg.read_byte()
                log("ALERT", f"⚠️ {content} (type={atype})")
            except Exception:
                log("ALERT", f"hex={msg.data.hex()}")
            return
        if cmd in ("CHAT.MSG", "CHAT.SEND", "BROADCAST", "CONFIG", "UPDATE_LEVEL",
                   "SET_PLAYER_STATUS", "SET_PLAYER_POINT", "SCORE_CHANGED", "PLAYER_PROFILE",
                   "LIST_ZONE_PLAYER", "SIDE_BET.STARTED", "ANIMATE_EARNED"):
            return  # bỏ qua im lặng
        if cmd == "PONG":
            return
        log("PKT", f"[{cmd}] hex={msg.data.hex()[:120]}")

    # ---------- vòng lặp chính ----------

    def idle_housekeeping(self):
        now = time.time()
        if not self.in_table:
            # CHẾ ĐỘ TẠO BÀN: tự tạo bàn chờ người vào, không đi tìm
            if CREATE_MODE:
                if (self._room_ready and self.bet_amts
                        and now >= self._create_retry_at):
                    self._create_retry_at = now + 15.0
                    self.send_create_table(CREATE_BET)
                return
            # thử vào lại bàn mục tiêu mỗi 5s (chen lúc bàn mở khóa)
            if self._target_tid is not None and now >= self._retry_join_at:
                self._retry_join_at = now + 5.0
                self.table_path = f"{ZONE_BASE}.{self.current_room}.{self._target_tid}"
                self.is_viewer = False
                self._join_fallback_view = False
                self._pending = ('table', self.table_path)
                log("TABLE", f"🔁 Thử vào bàn mục tiêu #{self._target_tid} (CHƠI)")
                self.send("ENTER_PLACE",
                          self.conn.pack_ascii(self.table_path) + self.conn.pack_string("")
                          + self.conn.pack_byte(1))
                return
            if now >= getattr(self, '_rescan_at', 0):
                self._rescan_at = now + 30
                self.enter_zone()
            return
        # đang XEM bàn mục tiêu mà ván vừa xong -> xin ghế CHƠI
        if (self.is_viewer and self.my_slot < 0 and not self.match_active
                and now >= self._upgrade_at and self.table_path):
            self._upgrade_at = now + 7.0
            self._want_play = True
            log("TABLE", "🙋 Xin vào ghế CHƠI (ENTER_PLACE mode=1)")
            self.send("ENTER_PLACE",
                      self.conn.pack_ascii(self.table_path) + self.conn.pack_string("")
                      + self.conn.pack_byte(1))
        if (self.my_slot >= 0 and not self.is_viewer and not self.match_active
                and not self.ready_sent and len(self.players) >= 2
                and not getattr(self, 'is_autostart', True)):
            self.ready_sent = True
            log("GAME", f"⏳ Gửi SET_READY ({len(self.players)} người ngồi)")
            self.send("SET_READY")
        alone = self.my_slot >= 0 and not self.match_active and len(self.players) <= 1
        if alone:
            if self._alone_since is None:
                self._alone_since = now
            elif now - self._alone_since > (CREATE_REMAKE_S if CREATE_MODE else 600):
                if CREATE_MODE:
                    log("GAME", f"⏳ Bàn trống quá {CREATE_REMAKE_S:.0f}s -> tạo lại bàn mới (lên đầu danh sách)")
                    self.in_table = False
                    self.my_slot = -1
                    self.table_path = None
                    self._alone_since = None
                    self._create_retry_at = now + 5.0
                    return
                log("GAME", "⏳ Ngồi 1 mình quá 10 phút -> rời bàn quét tiếp")
                self.in_table = False
                self.my_slot = -1
                self._alone_since = None
                self.table_path = None
                self.enter_zone()
        else:
            self._alone_since = None


def main():
    faulthandler.dump_traceback_later(45, repeat=True, file=sys.stderr)
    log("MAIN", f"=== XÌ TỐ TEST — user={USER}, chạy {RUN_MIN:.0f} phút, "
                f"RAISE_TEST={'BẬT' if RAISE_TEST else 'TẮT'} "
                f"| CHẾ ĐỘ: {'TỰ TẠO BÀN ' + str(CREATE_BET) + 'xu' if CREATE_MODE else 'TÌM BÀN mục tiêu=' + (TARGET_PLAYER or 'bất kỳ')} ===")
    info = http_login(USER, PASS)
    if not info:
        log("MAIN", "❌ HTTP login thất bại (sai mật khẩu hoặc server chặn?)")
        return 1
    log("MAIN", f"✅ HTTP login: nick={info['nickname']} id={info['player_id']} "
                f"balance={info['balance']:,} xu" if info['balance'] is not None else
                f"✅ HTTP login: nick={info['nickname']} id={info['player_id']}")
    t = XitoTester(info)
    t._pending = None
    t._rescan_at = 0.0
    t._alone_since = None
    t.first_bet = 0
    t.begin_state = 0
    try:
        if not t.connect():
            return 1
    except Exception as e:
        log("MAIN", f"❌ WS connect lỗi: {type(e).__name__}: {e}")
        return 1
    t.enter_zone()
    last_idle = time.time()
    try:
        while time.time() < t.deadline:
            try:
                raw = t.recv(1.0)
            except Exception as e:
                log("WS", f"WS đứt kết nối: {type(e).__name__}: {e}")
                break
            if raw:
                try:
                    t.handle(InboundMessage(raw))
                except Exception as e:
                    log("MAIN", f"Lỗi xử lý gói: {type(e).__name__}: {e}")
            t.idle_housekeeping()
            t._iters = getattr(t, '_iters', 0) + 1
            if t._iters % 20 == 0:
                log("LOOP", f"iter={t._iters}")
            if time.time() - last_idle > 120:
                last_idle = time.time()
                log("IDLE", f"alive | bàn={t.table_path} slot={t.my_slot} "
                            f"người={len(t.players)} ván={t.match_active} "
                            f"state={t.current_state} viewer={t.is_viewer}")
    except KeyboardInterrupt:
        log("MAIN", "Dừng theo Ctrl+C")
    log("MAIN", "=== KẾT THÚC PHIÊN ===")
    try:
        t.ws.close()
    except Exception:
        pass
    # ---- tổng kết phiên + lưu file ----
    st = t.stats
    log("STATS", "=" * 64)
    log("STATS", f"TỔNG KẾT PHIÊN: {st['hands']} ván | thắng {st['won']} | thua {st['lost']} | "
                f"hòa {st['push']} | NET {st['net']:+,} xu")
    log("STATS", f"Với '{TARGET_PLAYER}': {st['t_hands']} ván | thắng {st['t_won']} | "
                f"thua {st['t_lost']} | NET {st['t_net']:+,} xu")
    try:
        info2 = http_login(USER, PASS)
        if info2 and info2.get("balance") is not None:
            log("STATS", f"Số dư cuối phiên {info2['nickname']}: {info2['balance']:,} xu")
    except Exception:
        pass
    try:
        import json
        sp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "logs", f"xito_match_{time.strftime('%Y%m%d_%H%M%S')}.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump({"stats": st, "user": USER, "target": TARGET_PLAYER},
                      f, ensure_ascii=False, indent=2)
        log("STATS", f"Đã lưu: {sp}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
