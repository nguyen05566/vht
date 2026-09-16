"""
cup_bot.py - v6.3 FIX: ENGINE FLIP + UCI SUFFIX + BỎ TRENDANALYZER
Đã sửa để PASS test_cup_engine.py + fix bot dừng giữa ván
"""

import struct
import threading
import time
import sys
import os
import re
import subprocess
import signal
import atexit
import tempfile
import json
import random
import traceback
import urllib.request, urllib.parse, http.cookiejar


# ============================================================================
# HTTP SESSION
# ============================================================================
class _UrllibSession:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        class _Headers:
            def __init__(self):
                self.d = {}
            def update(self, items): self.d.update(items)
            def __setitem__(self, k, v): self.d[k] = v
            def __getitem__(self, k): return self.d[k]
            def __contains__(self, k): return k in self.d
        self.headers = _Headers()
        self.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        for k, v in self.headers.d.items():
            self.op.addheaders.append((k, v))
        self.last_url = ""
        self.cookies = self.cj

    def get(self, url, timeout=20, allow_redirects=True, **kw):
        h = list(self.op.addheaders)
        if 'headers' in kw and kw['headers']:
            for k, v in kw['headers'].items(): h.append((k, v))
        req = urllib.request.Request(url, headers=dict(h))
        r = self.op.open(req, timeout=timeout)
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)

    def post(self, url, data=None, timeout=20, headers=None, allow_redirects=True, **kw):
        body = urllib.parse.urlencode(data or {}).encode()
        h = dict(self.op.addheaders)
        if headers:
            for k, v in headers.items(): h[k] = v
        req = urllib.request.Request(url, data=body, headers=h)
        r = self.op.open(req, timeout=timeout)
        class R:
            def __init__(self, r):
                self.text = r.read().decode("utf-8", "replace")
                self.url = r.geturl()
        return R(r)


requests = type('R', (), {'Session': _UrllibSession})()

CARO_USER_DIRECT = "nguyen15"
CARO_PASSWD_DIRECT = "nhat123456"


def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default


USER = _clean_env(os.environ.get("CARO_USER19"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("CARO_PASSWD19"), CARO_PASSWD_DIRECT)
COOKIE = ""

_venv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'lib')
for _py_ver in ['python3.12', 'python3.13', 'python3.11']:
    _candidate = os.path.join(_venv_path, _py_ver, 'site-packages')
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

ENGINE_MULTIPV = 1
ENGINE_MULTIPV_FALLBACK = 3

MIN_MOVE_SECONDS = 3.0
MOVE_DEADLINE_SECONDS = 25.0

MAX_SAFE_MOVES = 250
TRUST_ENGINE_AFTER = 100

MAX_ENGINE_RESTARTS_PER_GAME = 2

MOVE_DEDUP_WINDOW = 0.1

KICK_MODE = "when_lose"
KICK_DELAY = 5.0

BOT_BET_XU = 5000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '30'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

VN_TEN_DAU = [
    "Tuấn", "Minh", "Đức", "Hoàng", "Huy", "Hùng", "Dũng", "Cường", "Long", "Nam",
    "Sơn", "Hải", "Phong", "Thắng", "Trung", "Kiên", "Quân", "Thanh", "Đạt", "Khoa",
    "Phúc", "Nghĩa", "Trọng", "Quang", "Bảo", "Khánh", "Hiếu", "Lâm", "Trí", "Thịnh",
    "Lộc", "Phát", "Tiến", "Việt", "Duy", "Vĩnh", "Phước", "Bình", "Đăng", "Tùng",
]
VN_TEN_KHONG_DAU = [
    "Tuan", "Minh", "Duc", "Hoang", "Huy", "Hung", "Dung", "Cuong", "Long", "Nam",
    "Son", "Hai", "Phong", "Thang", "Trung", "Kien", "Quan", "Thanh", "Dat", "Khoa",
    "Phuc", "Nghia", "Trong", "Quang", "Bao", "Khanh", "Hieu", "Lam", "Tri", "Thinh",
    "Loc", "Phat", "Tien", "Viet", "Duy", "Vinh", "Phuoc", "Binh", "Dang", "Tung",
]

_IDENTITY_SYNCED = False


def generate_dotted_full_name():
    name = random.choice(VN_TEN_DAU if random.choice([True, False]) else VN_TEN_KHONG_DAU)
    if len(name) >= 2:
        pos = random.randint(1, len(name) - 1)
        name = name[:pos] + "." + name[pos:]
    return name


def sync_profile_name(session):
    try:
        edit_url = "https://gamevh.net/com/ftl/game/profile/update_profile.jsp"
        page = session.get(edit_url, timeout=15, allow_redirects=True)
        form_match = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>', page.text)
        if not form_match: return
        form = form_match.group(0)
        open_tag = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        action_match = re.search(r'action=["\']([^"\']+)["\']', open_tag)
        action = action_match.group(1) if action_match else edit_url
        if not action.startswith('http'):
            from urllib.parse import urljoin
            action = urljoin(edit_url, action)
        data = {}
        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            nm = re.search(r'name=["\']([^"\']+)["\']', tag)
            val = re.search(r'value=["\']([^"\']*)["\']', tag)
            if nm:
                k = nm.group(1)
                data[k] = val.group(1) if val else ''
        old_full_name = data.get('FULL_NAME', '')
        new_full_name = generate_dotted_full_name()
        data['FULL_NAME'] = new_full_name
        data['OLD_PASSWORD'] = PASSWD
        data['SAVE'] = '\uf046'
        session.post(action, timeout=15, data=data,
                     headers={'Origin': 'https://gamevh.net',
                              'Referer': page.url,
                              'Content-Type': 'application/x-www-form-urlencoded'},
                     allow_redirects=True)
        print(f"[PROFILE] 👤 '{old_full_name}' -> '{new_full_name}'")
    except Exception as e:
        print(f"[PROFILE] Lỗi: {e}")


def sync_random_avatar(session):
    try:
        profile_url = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
        before = session.get(profile_url, timeout=15)
        m = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', before.text, re.I)
        old_avatar = int(m.group(1)) if m else None
        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["\']?)(\d+)\1\s*,\s*(["\'])(.*?)\3\s*,\s*(["\']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = f"https://gamevh.net/com/ftl/game/profile/avatar_by_category.jsp?excludeLayout=true&category_id={category}"
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id not in seen:
                    seen.add(avatar_id)
                    catalog.append(avatar_id)
        choices = [a for a in catalog if a != old_avatar]
        if not choices: return
        selected = random.choice(choices)
        update_url = f"https://gamevh.net/com/ftl/game/profile/update_avatar.jsp?pk={selected}&redirect=/"
        session.post(update_url, timeout=20,
                     headers={"Origin": "https://gamevh.net",
                              "Referer": "https://gamevh.net/com/ftl/game/profile/avatar.jsp"},
                     allow_redirects=True)
    except Exception as e:
        print(f"[PROFILE] Lỗi avatar: {e}")


def is_block_software_message(raw_bytes):
    try:
        idx = raw_bytes.find(b"blockSoftware")
        if idx != -1:
            snippet = raw_bytes[idx:idx+40]
            if b"1" in snippet or b"true" in snippet.lower():
                return True
    except Exception:
        pass
    return False


ACTIVE_TABLES_FILE = os.path.join(tempfile.gettempdir(), "zaro_active_tables.json")


def get_active_bot_tables():
    try:
        if not os.path.exists(ACTIVE_TABLES_FILE): return {}
        with open(ACTIVE_TABLES_FILE, 'r') as f:
            content = f.read().strip()
            if not content: return {}
            data = json.loads(content)
        now = time.time()
        return {tp: info for tp, info in data.items()
                if isinstance(info, dict) and now - info.get("timestamp", 0) < 180}
    except Exception:
        return {}


def register_bot_table(table_path, user):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        data[table_path] = {"user": user, "timestamp": time.time(), "pid": os.getpid()}
        with open(ACTIVE_TABLES_FILE, 'w') as f: json.dump(data, f)
    except Exception:
        pass


def unregister_bot_table(table_path):
    if not table_path: return
    try:
        data = get_active_bot_tables()
        if table_path in data:
            data.pop(table_path, None)
            with open(ACTIVE_TABLES_FILE, 'w') as f: json.dump(data, f)
    except Exception:
        pass


def fetch_session_info():
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH, _IDENTITY_SYNCED
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })
        session.get(LOGIN_URL, timeout=20)
        session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if not _IDENTITY_SYNCED:
            _IDENTITY_SYNCED = True
            sync_profile_name(session)
            sync_random_avatar(session)
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text
        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm: return False
        TOKEN = int(tm.group(1))
        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if not nm: return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()
        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid: CURRENT_PLAYER_ID = int(pid.group(1))
        pm = re.search(r"var\s+placePath\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if pm: PLACE_PATH = pm.group(1)
        try:
            cookie_parts = [f"{c.name}={c.value}" for c in session.cookies]
            COOKIE = "; ".join(cookie_parts)
        except Exception:
            COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())
        print(f"[SESSION] Login OK | Token: {TOKEN} | Nick: {CURRENT_PLAYER_NICKNAME} | ID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi: {e}")
        return False


CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "GET_TABLE_DATA", 416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}


class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)
            result.extend(cmd_bytes)
        elif isinstance(cmd, int):
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)
    def pack_byte(self, value): return struct.pack('>b', value)
    def pack_int(self, value): return struct.pack('>i', value)
    def pack_ascii(self, value):
        encoded = value.encode('ascii')[:255]
        return struct.pack('>b', len(encoded)) + encoded
    def pack_string(self, value):
        encoded = value.encode('utf-16-be')
        return struct.pack('>h', len(encoded) // 2) + encoded


class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()
    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset + (-length)].decode('ascii', errors='replace')
            self.offset += (-length)
            return cmd
        else:
            next_byte = self.data[self.offset] & 0xFF
            self.offset += 1
            return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte))
    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1; return val
    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2; return val
    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4; return val
    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8; return val
    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length; return s
    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2; return s
    def rem(self):
        return len(self.data) - self.offset


STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)


# ★★★ BAG CHUẨN PIKAFISH - ĐỦ 12 ENTRIES ★★★
INITIAL_BAG = {'A': 2, 'B': 2, 'N': 2, 'R': 2, 'C': 2, 'P': 5,
               'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}
BAG_ORDER = ['A', 'B', 'N', 'R', 'C', 'P', 'a', 'b', 'n', 'r', 'c', 'p']


class XiangqiBoardTracker:
    """Theo dõi bàn cờ úp."""

    INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX w"

    def __init__(self):
        self.reset()

    def reset(self):
        self.start_fen = self.INITIAL_FEN.split(' ')[0]
        self.start_side = 'w'
        self.uci_moves = []
        self.revealed_chars = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
        self.dark_positions = set()
        self.flip = False
        self.flip_known = False
        self.side_to_move = 'w'  # ★ Track side thực tế đang đi

    def pos_to_rc(self, pos):
        s_row, col = pos // 9, pos % 9
        return ((9 - s_row) if self.flip else s_row), col

    def rc_to_pos(self, fen_row, col):
        s_row = (9 - fen_row) if self.flip else fen_row
        return s_row * 9 + col

    def pos_to_engine_move(self, source_pos, target_pos):
        s_row, s_col = self.pos_to_rc(source_pos)
        t_row, t_col = self.pos_to_rc(target_pos)
        return (f"{chr(ord('a') + s_col)}{9 - s_row}"
                f"{chr(ord('a') + t_col)}{9 - t_row}")

    def engine_move_to_pos(self, engine_move):
        """
        ★★★ FIX CHÍNH: Engine UCI -> server pos, TÔN TRỌNG flip ★★★

        Engine UCI dùng hệ tọa độ Pikafish:
          - rank 9 = hàng đầu bàn (phía xa bot nhất trong view hiển thị)
          - rank 0 = hàng cuối bàn (phía gần bot nhất)
        Khi flip=True (đỏ ở trên server row nhỏ), ta phải đảo rank.
        """
        move = engine_move[:4]
        s_col, s_rank = ord(move[0]) - ord('a'), int(move[1])
        t_col, t_rank = ord(move[2]) - ord('a'), int(move[3])
        # Chuẩn Pikafish: rank trong UCI LUÔN là 0..9 với rank 9 ở TRÊN engine view
        # Khi flip=True (đỏ ở server row nhỏ), cần map:
        #   fen_row = 9 - s_rank -> server_row = fen_row (đã đúng)
        #   Nếu flip=False (đỏ ở server row lớn):
        #   fen_row = s_rank -> server_row = fen_row
        # pos_to_rc đã làm đúng chuyện này.
        return (self.rc_to_pos(9 - s_rank, s_col),
                self.rc_to_pos(9 - t_rank, t_col))

    def bag_string(self):
        bag = dict(INITIAL_BAG)
        for ch in self.revealed_chars:
            if ch in bag:
                bag[ch] = max(0, bag[ch] - 1)
        return "".join(f"{k}{bag[k]}" for k in BAG_ORDER)

    def get_current_fen(self):
        fen = f"{self.start_fen} {self.bag_string()} {self.start_side} - - 0 1"
        return fen, list(self.uci_moves)

    def set_base(self, board_fen, side='w'):
        board_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.start_fen = board_fen
        self.start_side = side
        self.side_to_move = side
        self.uci_moves = []
        self.revealed_chars = []
        self.dark_positions.clear()

    def record_move(self, mv, revealed_char=None):
        """★ FIX: signature (mv, revealed_char) khớp test."""
        uci = mv + (revealed_char or "")
        self.uci_moves.append(uci)
        if revealed_char:
            self.revealed_chars.append(revealed_char)
        # Toggle side
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'
        return uci

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == self.first_turn_slot_id)
        # ★ Bot đỏ đi trước, bot đen đi sau
        self.side_to_move = 'w' if self.is_red else 'b'

    def detect_flip(self, pieces):
        red_rows, black_rows = [], []
        red_king_row = black_king_row = None
        for sid, face, position, is_open in pieces:
            if position is None or position < 0 or position >= 90:
                continue
            row = position // 9
            color = face[0] if face else (sid[0] if sid else 'r')
            ptype = int(face[1]) if len(face) > 1 and str(face[1]).isdigit() else 0
            if ptype == 0 and len(sid) > 1 and str(sid[1]).isdigit():
                ptype = int(sid[1])
            if color == 'r':
                red_rows.append(row)
                if ptype == 1: red_king_row = row
            else:
                black_rows.append(row)
                if ptype == 1: black_king_row = row

        if red_king_row is not None and black_king_row is not None:
            self.flip = red_king_row < black_king_row
            self.flip_known = True
        elif red_king_row is not None:
            self.flip = red_king_row <= 4
            self.flip_known = True
        elif black_king_row is not None:
            self.flip = black_king_row >= 5
            self.flip_known = True
        elif red_rows and black_rows:
            self.flip = (sum(red_rows) / len(red_rows)) < (sum(black_rows) / len(black_rows))
            self.flip_known = True
        else:
            self.flip = bool(self.is_red)
            self.flip_known = False
        return self.flip

    def sanity_check_fen(self, board_fen):
        rows = board_fen.split(' ')[0].split('/')
        if len(rows) != 10:
            return False, f"FEN có {len(rows)} hàng"
        k_row = K_row = None
        for i, r in enumerate(rows):
            if 'K' in r: K_row = i
            if 'k' in r: k_row = i
        if K_row is None or k_row is None:
            return False, "thiếu tướng"
        # Pikafish: Đỏ (K) ở hàng 7-9 (dưới), Đen (k) ở hàng 0-2 (trên)
        if K_row < 7 or k_row > 2:
            return False, f"tướng sai chiều (K hàng {K_row}, k hàng {k_row})"
        return True, "ok"


class MultiPVCollector:
    _re = re.compile(
        r"info\b.*?\bdepth (\d+).*?\bmultipv (\d+).*?\bscore (cp|mate) (-?\d+).*?\bpv ([a-i]\d[a-i]\d(?:\s+\S+)*)"
    )
    def __init__(self):
        self.lines = {}
        self._best_depth = -1
        self.lock = threading.Lock()
    def clear(self):
        with self.lock:
            self.lines.clear()
            self._best_depth = -1
    def parse_line(self, line_str):
        m = self._re.search(line_str)
        if not m: return
        depth = int(m.group(1)); rank = int(m.group(2))
        is_mate = (m.group(3) == "mate"); score = int(m.group(4))
        move = m.group(5).split()[0]
        with self.lock:
            if depth > self._best_depth:
                self._best_depth = depth
                self.lines.clear()
            elif depth < self._best_depth:
                return
            self.lines[rank] = {"move": move, "score": score,
                                "is_mate": is_mate, "depth": depth}
    def candidates(self):
        with self.lock:
            return [self.lines[r]["move"] for r in sorted(self.lines)]


# ★★★ KHÔNG CÓ TrendAnalyzer - test yêu cầu ★★★
# (Không khai báo class TrendAnalyzer, không hàm select_best_trend_move)


class PikafishBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.multipv = MultiPVCollector()
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 3.0
        self.ROOM_LIST = ["0", "1", "2", "3"]
        _bot_num = re.search(r"\d+", USER)
        _offset = int(_bot_num.group(0)) if _bot_num else 0
        self._search_room_idx = _offset % len(self.ROOM_LIST)
        self._quick_play_attempts = 0
        self._sit_alone_since = None
        self._table_created_by_me = False
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self.slot_players = {}
        self._pending_kick_id = None
        self._table_path = None
        self._table_path_ts = 0.0
        self._reconnect_streak = 0
        self._connected_since = 0.0
        self._enter_fail_at = 0.0
        self.player_names = {}

        # Engine
        self._engine_proc = None
        self.engine = False
        self._readyok = False
        self._latest_bestmove = None
        self._mate_status = None
        self._engine_lock = threading.Lock()
        self._last_score = "?"
        self._last_depth = "?"
        self._mate_regex = re.compile(r"score mate (-?\d+)")
        self._score_regex = re.compile(r"depth (\d+).*score (cp|mate) (-?\d+)")
        self._engine_crash_fingerprint = None
        self._engine_crash_count = 0
        self._engine_restart_count = 0

        # Turn
        self._thinking = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        self._played_this_turn = False
        self._last_sent_move = None
        self._rejected_moves = set()
        self._play_reject_count = 0

        # Move
        self._move_lock = threading.Lock()
        self._last_move_uci = None
        self._last_move_time = 0.0
        self._move_recv_count = 0
        self._move_skip_count = 0
        self._move_error_count = 0

        self._game_seq = 0
        self._moves_len_at_turn_start = 0

        self._init_engine()

    # ==================== ENGINE ====================
    def _kill_engine(self):
        proc = getattr(self, '_engine_proc', None)
        if proc is None: return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.write("quit\n"); proc.stdin.flush()
                except Exception: pass
                try: proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                    try: proc.wait(timeout=3)
                    except Exception: pass
            for s in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if s: s.close()
                except Exception: pass
        except Exception as e:
            print(f"[ENGINE] ⚠️ Dọn engine: {e}")
        finally:
            self._engine_proc = None
            self.engine = False
            self._readyok = False
            self._latest_bestmove = None

    def _init_engine(self):
        self._kill_engine()
        wine_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "wine-portable", "wine-9.0-staging-amd64", "bin", "wine64"),
            "/home/z/my-project/wine-portable/wine-9.0-staging-amd64/bin/wine64",
            os.path.expanduser("~/wine-portable/wine-9.0-staging-amd64/bin/wine64"),
            "/usr/lib/wine/wine64", "/usr/bin/wine64", "/usr/local/bin/wine64",
            "/usr/bin/wine", "/usr/local/bin/wine",
        ]
        import shutil as _sh
        for _w in ("wine64", "wine"):
            _p = _sh.which(_w)
            if _p: wine_candidates.append(_p)
        wine_path = next((p for p in wine_candidates
                          if os.path.isfile(p) and os.access(p, os.X_OK)), None)

        pkjq_candidates = [
            "/home/z/my-project/vht/jieqibox/Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "jieqibox", "Jieqibox", "Engines", "PikaJieQi0111", "PKJQ.exe"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "Jieqibox", "Engines", "PikaJieQi0111", "PKJQ.exe"),
            "./jieqibox/Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
            "./Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
        ]
        pkjq_path = next((p for p in pkjq_candidates if os.path.isfile(p)), None)
        if not wine_path:
            print("[ENGINE] ❌ Không có wine64!"); return
        if not pkjq_path:
            print("[ENGINE] ❌ Không có PKJQ.exe!"); return
        print(f"[ENGINE] 🍷 {wine_path}")
        print(f"[ENGINE] ♟️  {pkjq_path}")

        engine_dir = os.path.dirname(pkjq_path)
        nnue_path = os.path.join(engine_dir, "pikafish.nnue")
        if not os.path.isfile(nnue_path):
            src_nnue = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "pikafish-engine", "pikafish.nnue")
            if os.path.isfile(src_nnue):
                import shutil
                try: shutil.copy(src_nnue, nnue_path)
                except Exception as e: print(f"[ENGINE] ⚠️ Copy NNUE: {e}")
            else:
                try:
                    _url = ("https://github.com/official-pikafish/Networks/"
                            "releases/download/master-net/pikafish.nnue")
                    urllib.request.urlretrieve(_url, nnue_path)
                except Exception as e: print(f"[ENGINE] ⚠️ Tải NNUE: {e}")

        env = os.environ.copy()
        env["WINEPREFIX"] = env.get("WINEPREFIX",
                                    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wine"))
        env["WINEDEBUG"] = "-all"
        env["DISPLAY"] = ""
        if not env.get("XDG_RUNTIME_DIR"):
            _xdg = os.path.join(tempfile.gettempdir(), f"xdg-{os.getuid()}")
            os.makedirs(_xdg, mode=0o700, exist_ok=True)
            env["XDG_RUNTIME_DIR"] = _xdg

        try:
            self._engine_proc = subprocess.Popen(
                [wine_path, pkjq_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=env, cwd=engine_dir)
        except Exception as e:
            print(f"[ENGINE] ❌ Khởi động: {e}"); return

        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    if not proc.stderr.readline(): break
            except Exception: pass
        threading.Thread(target=consume_stderr, args=(self._engine_proc,), daemon=True).start()

        def consume_stdout(proc):
            try:
                while proc.poll() is None:
                    line = proc.stdout.readline()
                    if not line: break
                    line_str = line.strip()
                    if not line_str: continue
                    self.multipv.parse_line(line_str)
                    m = self._score_regex.search(line_str)
                    if m:
                        self._last_depth = m.group(1)
                        self._last_score = ("mate " + m.group(3)) if m.group(2) == "mate" else f"{int(m.group(3)):+d}"
                    if "score mate" in line_str:
                        mm = self._mate_regex.search(line_str)
                        if mm:
                            val = int(mm.group(1))
                            self._mate_status = f"WIN_IN_{val}" if val > 0 else f"LOSE_IN_{abs(val)}"
                    if line_str == "readyok":
                        self._readyok = True
                    if line_str.startswith("bestmove"):
                        self._latest_bestmove = line_str
            except Exception: pass
        threading.Thread(target=consume_stdout, args=(self._engine_proc,), daemon=True).start()

        try:
            with self._engine_lock:
                self._fsf_cmd_unlocked("uci")
                _threads = max(1, min(4, (os.cpu_count() or 2) - 1))
                self._fsf_cmd_unlocked(f"setoption name Threads value {_threads}")
                self._fsf_cmd_unlocked("setoption name Hash value 64")
                self._fsf_cmd_unlocked(f"setoption name MultiPV value {ENGINE_MULTIPV}")
                self._fsf_cmd_unlocked("setoption name EvalFile value pikafish.nnue")
                self._readyok = False
                self._fsf_cmd_unlocked("isready")
            _t0 = time.time()
            while not self._readyok and time.time() - _t0 < 20:
                if self._engine_proc.poll() is not None:
                    print("[ENGINE] ❌ Engine thoát ngay"); return
                time.sleep(0.05)
            if not self._readyok:
                print("[ENGINE] ⚠️ Quá 20s")
            else:
                print(f"[ENGINE] ⏱️ readyok sau {time.time() - _t0:.1f}s")
            self.engine = True
            print(f"[ENGINE] ✅ Sẵn sàng (Threads={_threads})")
        except Exception as e:
            print(f"[ENGINE] ❌ Init: {e}")

    def _fsf_cmd_unlocked(self, text):
        if self._engine_proc and self._engine_proc.poll() is None:
            try:
                self._engine_proc.stdin.write(text + "\n")
                self._engine_proc.stdin.flush()
            except Exception as e:
                print(f"[ENGINE] ⚠️ Gửi '{text}': {e}")

    def _fsf_cmd(self, text):
        with self._engine_lock:
            self._fsf_cmd_unlocked(text)

    def _engine_alive(self):
        return (self._engine_proc is not None
                and self._engine_proc.poll() is None)

    def _wait_ready(self, timeout=5.0):
        self._readyok = False
        with self._engine_lock:
            self._fsf_cmd_unlocked("isready")
        _t0 = time.time()
        while time.time() - _t0 < timeout:
            if not self._engine_alive(): return False
            if self._readyok: return True
            time.sleep(0.02)
        return False

    def _stop_engine_search(self, timeout=2.0):
        if not self._engine_alive(): return
        self._latest_bestmove = None
        with self._engine_lock:
            self._fsf_cmd_unlocked("stop")
        _t0 = time.time()
        while time.time() - _t0 < timeout:
            if self._latest_bestmove is not None:
                self._latest_bestmove = None  # tiêu thụ
                return
            time.sleep(0.02)

    def _wait_bestmove(self, timeout):
        self._latest_bestmove = None
        _t0 = time.time()
        while time.time() - _t0 < timeout:
            if not self._engine_alive(): return None
            if self._latest_bestmove:
                bm = self._latest_bestmove
                return bm
            time.sleep(0.02)
        return None

    def get_best_move(self, fen, moves, fixed_positions=None,
                      movetime_ms=3000, hard_timeout=6.0):
        try:
            if not self._engine_alive(): return None
            self._stop_engine_search(timeout=2.0)
            if not self._wait_ready(timeout=3.0):
                print("[ENGINE] ⚠️ Không ready"); return None
            self.multipv.clear()
            self._latest_bestmove = None
            self._mate_status = None
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            with self._engine_lock:
                self._fsf_cmd_unlocked(pos_cmd)
                self._fsf_cmd_unlocked(f"go movetime {movetime_ms}")
            best = self._wait_bestmove(timeout=hard_timeout)
            if best is None:
                with self._engine_lock:
                    self._fsf_cmd_unlocked("stop")
                _t = time.time()
                while time.time() - _t < 1.5:
                    if self._latest_bestmove: return self._latest_bestmove
                    time.sleep(0.02)
                return None
            return best
        except Exception as e:
            print(f"[ENGINE] Lỗi get_best_move: {e}"); return None

    def _find_legal_fallback(self, fen, moves):
        try:
            if not self._engine_alive(): return None
            self._stop_engine_search(timeout=1.0)
            if not self._wait_ready(timeout=3.0): return None
            self.multipv.clear()
            self._latest_bestmove = None
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            with self._engine_lock:
                self._fsf_cmd_unlocked("setoption name MultiPV value 10")
                self._fsf_cmd_unlocked(pos_cmd)
                self._fsf_cmd_unlocked("go movetime 500")
            best = self._wait_bestmove(timeout=2.5)
            try:
                for cand in self.multipv.candidates():
                    if cand not in self._rejected_moves:
                        return cand
                if best:
                    parts = best.split()
                    if len(parts) >= 2 and parts[1] not in ("(none)", "0000"):
                        return parts[1]
            finally:
                with self._engine_lock:
                    self._fsf_cmd_unlocked(f"setoption name MultiPV value {ENGINE_MULTIPV}")
            return None
        except Exception as e:
            print(f"[FALLBACK] Lỗi: {e}"); return None

    # ==================== WEBSOCKET ====================
    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"})
        self.ws_thread = threading.Thread(
            target=lambda: self.ws.run_forever(ping_interval=30, ping_timeout=None),
            daemon=True)
        self.ws_thread.start()
        for _ in range(25):
            if self.connected: break
            time.sleep(0.2)
        return self.connected

    def _on_open(self, ws):
        self.connected = True
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._connected_since = time.time()
        self._send_login()

    def _on_message(self, ws, message):
        self.last_recv_timestamp = time.time()
        if isinstance(message, bytes):
            self._handle_binary_message(message)

    def _on_error(self, ws, error):
        print(f"[WS] ❌ Lỗi: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            print(f"[WS] ⚠️ MẤT KẾT NỐI GIỮA VÁN (code={code})")
        else:
            print(f"[WS] Đóng (code={code})")
        if self._connected_since and time.time() - self._connected_since < 60:
            self._reconnect_streak += 1
        else:
            self._reconnect_streak = 0
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._bet_amts_loaded = False
        self._resolved_bet_id = None
        self.bet_amts = []
        self.fixed_pawn_positions = set()
        self._thinking = False
        self._played_this_turn = False
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try: self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except Exception: pass

    def _send_login(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(CURRENT_PLAYER_NICKNAME))
        data.extend(self.conn.pack_int(TOKEN))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        self.send_message("LOGIN", bytes(data))

    def send_enter_place(self, path=None, mode=1):
        data = bytearray()
        data.extend(self.conn.pack_ascii(path or PLACE_PATH))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self):
        self.send_message("LIST_BET_AMT")

    def get_1k_to_5k_bet_objs(self):
        if not self.bet_amts: return []
        valid = [ba for ba in self.bet_amts if 5000 <= ba["value"] <= 10000]
        if valid:
            random.shuffle(valid); return valid
        return [self.bet_amts[0]] if self.bet_amts else []

    def is_family_bot(self, name):
        if not name or name.strip().lower() == CURRENT_PLAYER_NICKNAME.lower():
            return False
        return "." in name

    def leave_table(self):
        if self.board.is_playing:
            print("[TABLE] ⚠️ Trong ván, không rời!"); return
        print("[TABLE] 🚪 Rời bàn...")
        if self._table_path: unregister_bot_table(self._table_path)
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self._table_created_by_me = False
        self._sit_alone_since = None
        self.slot_players.clear()
        self.board.reset()
        self._quick_play_attempts = 0
        self._enter_fail_at = 0.0
        self.send_enter_place(PLACE_PATH)

    def resolve_bet_amt_id(self):
        if not self.bet_amts: return None
        exact = [ba for ba in self.bet_amts if ba["value"] == BOT_BET_XU]
        if exact: return exact[0]['id']
        above = [ba for ba in self.bet_amts if ba["value"] >= BOT_BET_XU]
        if above: return min(above, key=lambda x: x['value'])['id']
        return self.bet_amts[-1]['id'] if self.bet_amts else 0

    def send_create_table(self, bet_amt_id=None):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL: return
        self._last_quick_play_time = now
        if bet_amt_id is None:
            bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None: return
        args = [
            ("matchDuration", str(BOT_MATCH_DURATION)),
            ("turnDuration", str(BOT_TURN_DURATION)),
            ("accDuration", str(BOT_ACC_DURATION)),
            ("blockSoftware", str(BOT_BLOCK_SOFTWARE)),
        ]
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id))
        data.extend(self.conn.pack_byte(len(args)))
        for arg_name, arg_value in args:
            data.extend(self.conn.pack_ascii(arg_name))
            data.extend(self.conn.pack_string(arg_value))
        self.send_message("CREATE_RULE", bytes(data))

    def send_quick_play(self, room_id="", bet_amt_id=-1):
        now = time.time()
        if now - self._last_quick_play_time < self._QUICK_PLAY_INTERVAL: return
        self._last_quick_play_time = now
        data = bytearray()
        data.extend(self.conn.pack_ascii(room_id))
        data.extend(self.conn.pack_byte(bet_amt_id))
        self.send_message("QUICK_PLAY", bytes(data))

    def send_play(self, source_pos, target_pos):
        self._played_this_turn = True
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        self.send_message("PLAY", bytes(data))

    def opponent_player_id(self):
        for sid, pid in self.slot_players.items():
            if pid and pid != CURRENT_PLAYER_ID and sid != self.board.my_slot_id:
                return pid
        return None

    def send_kick_player(self, player_id):
        self._pending_kick_id = player_id
        data = bytearray()
        data.extend(struct.pack('>q', int(player_id)))
        print(f"[KICK] Gửi playerId={player_id}")
        self.send_message(410, bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        print("[GAME] ⏳ READY...")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    # ==================== XỬ LÝ GÓI ĐẾN ====================
    def _handle_binary_message(self, data):
        cmd_for_log = "?"
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            cmd_for_log = cmd
            if cmd == "PING":
                self.send_message("PONG")
            elif cmd == "LOGIN":
                self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE":
                self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY":
                self._handle_quick_play_response(msg)
            elif cmd == "LIST_BET_AMT":
                self._handle_list_bet_amt_response(msg)
            elif cmd == "CREATE_RULE":
                self._handle_create_rule_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED":
                self._handle_slot_changed(msg)
            elif cmd == "PLAYER_ENTERED":
                self._handle_player_entered(msg)
            elif cmd == "START_MATCH":
                self._handle_start_match(msg)
            elif cmd == "MOVE":
                self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502":
                self._handle_play_response(msg)
            elif cmd == "SET_TURN":
                self._handle_set_turn(msg)
            elif cmd == "GAMEOVER":
                self._handle_gameover(msg)
            elif cmd == "KICK_PLAYER":
                self._handle_kick_response(msg)
            elif cmd == "ALERT":
                try: print(f"[SERVER] ALERT: {msg.read_string()}")
                except Exception: pass
        except Exception as e:
            print(f"[RECV ERROR] cmd={cmd_for_log} err={e}", flush=True)
            traceback.print_exc()

    def _handle_login_response(self, msg):
        if msg.read_byte() == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                fetch_session_info(); self._send_login(); return
            self.send_enter_place()

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            if self._joining_table:
                print(f"[TABLE] ENTER_PLACE status={status}")
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = time.time()
                threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
            return
        if self._joining_table:
            if is_block_software_message(msg.data):
                print("[GAME] 🛡️ Chống Software")
            self._joining_table = False
            self.in_game = True
            self._enter_fail_at = 0.0
            self.last_action_timestamp = time.time()
            threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
        elif not self.in_game:
            if self._table_path and time.time() - self._table_path_ts < 180:
                print(f"[TABLE] Ngồi lại bàn cũ: {self._table_path}")
                self.in_game = True; self._joining_table = True
                path = self._table_path
                threading.Thread(target=lambda: (time.sleep(0.5), self.send_enter_place(path=path, mode=1)), daemon=True).start()
                return
            self._bet_amts_loaded = False
            self._resolved_bet_id = None
            self.send_list_bet_amt()

    def _handle_quick_play_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            active_tables = get_active_bot_tables()
            if table_path in active_tables:
                owner = active_tables[table_path].get("user", "")
                if owner.lower() != USER.lower():
                    print(f"[AVOID] 🛑 Đồng đội {owner}")
                    self.in_game = False; self._joining_table = False; return
            self.in_game = True; self._joining_table = True
            self._table_created_by_me = False
            self._sit_alone_since = time.time()
            self._table_path = table_path
            self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[SEARCH] ✅ Vào bàn: {table_path}")
            threading.Thread(target=lambda: (time.sleep(0.5), self.send_enter_place(path=table_path, mode=1)), daemon=True).start()
        else:
            self._joining_table = False

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0: return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True

    def _handle_create_rule_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            self.in_game = True; self._joining_table = True
            self._table_created_by_me = True
            self._sit_alone_since = time.time()
            self._table_path = table_path
            self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[CREATE] 🎉 {table_path}")
            threading.Thread(target=lambda: (time.sleep(0.5), self.send_enter_place(path=table_path, mode=1)), daemon=True).start()
        else:
            print(f"[CREATE] ❌ status={status}")
            self._joining_table = False

    def _handle_player_entered(self, msg):
        try:
            _ = msg.read_byte()
            pid = msg.read_long()
            name = msg.read_string()
            if pid > 0 and pid != CURRENT_PLAYER_ID:
                self.player_names[pid] = name
                print(f"[PLAYER] 👤 '{name}' (id={pid})")
                if not self.board.is_playing and self.is_family_bot(name):
                    if self.opponent_player_id() == pid:
                        print(f"[AVOID] Đồng đội -> rời")
                        self.leave_table()
        except Exception: pass

    def _handle_slot_changed(self, msg):
        try:
            _ = msg.read_string()
            slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short()
            msg.read_ascii(); msg.read_byte(); msg.read_byte()
            player_id = msg.read_long()
            if player_id > 0:
                self.slot_players[slot_id] = player_id
            else:
                self.slot_players.pop(slot_id, None)
            if player_id == CURRENT_PLAYER_ID:
                self.board.my_slot_id = slot_id
            else:
                if player_id > 0:
                    name = self.player_names.get(player_id, "")
                    print(f"[TABLE] Ghế đối diện: pid={player_id}{f', {name}' if name else ''}")
                    if not self.board.is_playing and self.is_family_bot(name):
                        print(f"[AVOID] Đồng đội -> rời")
                        self.leave_table(); return
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        threading.Thread(target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        print("[TABLE] Không đối thủ, chờ 30s...")
                        self._sit_alone_since = time.time()
        except Exception: pass

    def _handle_start_match(self, msg):
        self._game_seq += 1
        print(f"[GAME] 🎮 Trận #{self._game_seq}")
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        self._play_reject_count = 0
        self._rejected_moves = set()
        self._last_sent_move = None
        self._reconnect_streak = 0
        self._enter_fail_at = 0.0
        self._sit_alone_since = None
        self._engine_crash_fingerprint = None
        self._engine_crash_count = 0
        self._engine_restart_count = 0
        self._move_recv_count = 0
        self._move_skip_count = 0
        self._move_error_count = 0
        self._last_move_uci = None
        self._last_move_time = 0.0
        self.board.reset()
        self.fixed_pawn_positions.clear()
        self.board.is_playing = True
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        try:
            player_count = msg.read_byte()
            for _ in range(player_count):
                msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte()
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte()
                pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid),
                                     self._decode_piece_id(raw_face),
                                     pos, is_open))
            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()
            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = (self.board.my_slot_id
                              if self.board.my_slot_id >= 0
                              else first_turn_slot_id)
            self.board.set_my_slot(my_slot_id, first_turn_slot_id)

            _built_fen = self._build_fen_from_pieces(board_pieces)
            _ok, _why = self.board.sanity_check_fen(_built_fen)
            if not _ok:
                print(f"[FEN] ⚠️ Sai chiều ({_why}) -> lật")
                self.board.flip = not self.board.flip
                _rebuilt = self._rebuild_fen_with_current_flip(board_pieces)
                _ok2, _why2 = self.board.sanity_check_fen(_rebuilt)
                if _ok2:
                    _built_fen = _rebuilt
                    print(f"[FEN] ✅ flip={self.board.flip}")
                else:
                    print(f"[FEN] ❌ Vẫn sai ({_why2})")
                    self.board.flip = not self.board.flip
            self.board.set_base(_built_fen, 'w')

            for sid, face, position, is_open in board_pieces:
                if not is_open and 0 <= position < 90:
                    self.board.dark_positions.add(position)
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            if self.fixed_pawn_positions:
                print(f"[GAME] 🛡️ {len(self.fixed_pawn_positions)} chốt khóa")
            self.board.revealed_chars = []
            print(f"[FEN] 📋 {self.board.start_fen}")
            print(f"[START] BAG={self.board.bag_string()}")
            print(f"[START] dark={len(self.board.dark_positions)} | "
                  f"my_slot={my_slot_id} | first={first_turn_slot_id} | "
                  f"flip={self.board.flip} | side_to_move={self.board.side_to_move}")
        except Exception as e:
            print(f"[START_MATCH ERROR] {e}")
            traceback.print_exc()

    def _build_fen_from_pieces(self, pieces):
        self.board.detect_flip(pieces)
        return self._rebuild_fen_with_current_flip(pieces)

    def _rebuild_fen_with_current_flip(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            fen_row, col = self.board.pos_to_rc(position)
            if is_open and len(face) > 1:
                color = face[0]; piece_type = int(face[1])
                type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r',
                               5: 'c', 6: 'n', 7: 'p'}
                fen_char = type_to_fen.get(piece_type, '?')
                if color == 'r': fen_char = fen_char.upper()
            else:
                fen_char = 'X' if sid.startswith('r') else 'x'
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""; empty = 0
            for cell in row:
                if cell == '.':
                    empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    PIECE_TYPE_MAP = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

    @classmethod
    def _sid_to_fen_char(cls, byte_val):
        v = byte_val - 256 if byte_val > 127 else byte_val
        if v == 0: return None
        ch = cls.PIECE_TYPE_MAP.get(abs(v) >> 3)
        if not ch: return None
        return ch.upper() if v > 0 else ch

    def _handle_move(self, msg):
        with self._move_lock:
            self._move_recv_count += 1
            try:
                source_pos = msg.read_byte()
                target_pos = msg.read_byte()
                engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
                self.last_action_timestamp = time.time()
                rest = list(msg.data[msg.offset:]) if msg.offset < len(msg.data) else []
                rest_hex = bytes(rest).hex()
                revealed_char = None
                if rest and rest[0] > 0 and len(rest) >= 3:
                    cand = self._sid_to_fen_char(rest[2])
                    if cand and cand not in ('k', 'K'):
                        revealed_char = cand
                is_dark_move = source_pos in self.board.dark_positions
                self.board.dark_positions.discard(source_pos)
                self.board.dark_positions.discard(target_pos)
                uci_suffix = None
                if revealed_char:
                    uci_suffix = revealed_char
                    self.board.revealed_chars.append(revealed_char)
                elif is_dark_move:
                    uci_suffix = '?'
                full_uci = engine_move + (uci_suffix or "")
                now = time.time()
                if (self._last_move_uci == full_uci
                        and (now - self._last_move_time) < MOVE_DEDUP_WINDOW):
                    self._move_skip_count += 1
                    print(f"[MOVE] ⚠️ Dup: {full_uci}", flush=True)
                    return
                self.board.record_move(engine_move, revealed_char)
                self._last_move_uci = full_uci
                self._last_move_time = now
                self._played_this_turn = False
                _rev = f" 🔓{revealed_char}" if revealed_char else ""
                _raw = f" [{rest_hex}]" if revealed_char else ""
                print(f"[MOVE] #{self._move_recv_count} {engine_move} -> "
                      f"'{full_uci}'{_rev}{_raw} | "
                      f"uci_total={len(self.board.uci_moves)} "
                      f"revealed={len(self.board.revealed_chars)} "
                      f"| side={self.board.side_to_move} "
                      f"| BAG={self.board.bag_string()}", flush=True)
            except Exception as e:
                self._move_error_count += 1
                print(f"[MOVE ERROR] #{self._move_recv_count} err={e}", flush=True)
                traceback.print_exc()

    def _handle_play_response(self, msg):
        status = msg.read_byte()
        err_text = ""
        try:
            if msg.rem() >= 2: err_text = msg.read_string()
        except Exception: pass
        print(f"[PLAY-RESP] status={status} err={err_text!r}", flush=True)
        if status != 0:
            self._play_reject_count += 1
            self.board.is_my_turn = True
            self._played_this_turn = False
            print(f"[PLAY] ⚠️ Reject lần {self._play_reject_count}", flush=True)
            if "NoPieceAtSource" in (err_text or "") or status == 51:
                bad = self._last_sent_move
                if bad:
                    self._rejected_moves.add(bad)
            if self._play_reject_count <= 6:
                threading.Thread(target=lambda: (time.sleep(0.5), self._make_auto_move()), daemon=True).start()
        else:
            self._play_reject_count = 0
            self._rejected_moves.clear()

    def _handle_set_turn(self, msg):
        try:
            slot_id = msg.read_byte()
            try: turn_timeout = msg.read_short()
            except Exception: turn_timeout = 0
            if slot_id == -2 or slot_id == -1 or not self.board.is_playing: return
            self.turn_timeout = turn_timeout
            was_my_turn = self.board.is_my_turn
            self.board.is_my_turn = (slot_id == self.board.my_slot_id)
            self.last_action_timestamp = time.time()
            if not self.board.is_my_turn: return
            self._turn_started_at = time.time()
            self._turn_deadline = self._turn_started_at + max(turn_timeout - 5, 10)
            self._played_this_turn = False
            self._moves_len_at_turn_start = len(self.board.uci_moves)
            if not was_my_turn:
                print(f"[TURN] Đến lượt | uci={len(self.board.uci_moves)} "
                      f"| BAG={self.board.bag_string()} "
                      f"| side={self.board.side_to_move} "
                      f"| timeout={turn_timeout}s", flush=True)
            threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            print(f"[SET_TURN ERROR] {e}")
            traceback.print_exc()

    def _handle_kick_response(self, msg):
        try:
            status = msg.read_byte(); content = msg.read_string()
        except Exception: status, content = None, ""
        if self._pending_kick_id is not None:
            pid = self._pending_kick_id; self._pending_kick_id = None
            print(f"[KICK] {'✅' if status == 0 else '❌'} pid={pid}")
            return
        print(f"[KICK] Bị đuổi: {content}")
        self.in_game = False; self._joining_table = False
        self._table_path = None; self.board.reset()

    def _handle_gameover(self, msg):
        my_result, results = None, {}
        try:
            count = msg.read_byte()
            for _ in range(count):
                sid = msg.read_byte(); res = msg.read_byte(); msg.read_long()
                results[sid] = res
                if sid == self.board.my_slot_id: my_result = res
        except Exception: results = {}
        bot_won = my_result in (1, 11)
        bot_lost = my_result in (2, 4, 12)
        if bot_won: print("[GAME] 🏁 THẮNG")
        elif bot_lost: print("[GAME] 🏁 THUA")
        elif my_result is None: print("[GAME] 🏁 Kết thúc")
        else: print("[GAME] 🏁 HOÀ")

        print(f"[SUMMARY] #{self._game_seq} | uci={len(self.board.uci_moves)} | "
              f"recv={self._move_recv_count} skip={self._move_skip_count} "
              f"err={self._move_error_count} reject={self._play_reject_count} "
              f"engine_restart={self._engine_restart_count} "
              f"BAG={self.board.bag_string()}", flush=True)

        should_kick = (KICK_MODE == "always"
                       or (KICK_MODE == "when_lose" and bot_lost)
                       or (KICK_MODE == "when_win" and bot_won))
        victim = None
        if should_kick:
            want = (1, 11) if KICK_MODE == "when_lose" else (2, 4, 12)
            target_sid = next((sid for sid, res in results.items()
                               if sid != self.board.my_slot_id and res in want), None)
            victim = (self.slot_players.get(target_sid)
                      if target_sid is not None else self.opponent_player_id())

        self.fixed_pawn_positions.clear()
        self.board.reset()
        self.board.is_playing = False
        self.board.is_my_turn = False
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()
        self._engine_crash_fingerprint = None
        self._engine_crash_count = 0
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._turn_deadline = 0.0
        if self._engine_proc and self._engine_proc.poll() is None:
            self._stop_engine_search(timeout=1.0)
            with self._engine_lock:
                self._fsf_cmd_unlocked("ucinewgame")
                self._fsf_cmd_unlocked("isready")

        def after_gameover():
            is_guest = not getattr(self, '_table_created_by_me', False)
            if bot_lost:
                if victim and not is_guest:
                    time.sleep(KICK_DELAY)
                    if self.connected and not self.board.is_playing:
                        self.send_kick_player(victim); time.sleep(2.0)
                elif is_guest:
                    print("[GAME] Khách -> không kick")
                print("[GAME] Thua -> rời")
                time.sleep(1.0); self.leave_table()
            else:
                print("[GAME] Thắng/Hoà -> ở lại")
                time.sleep(3.0); self.send_ready(1)
        threading.Thread(target=after_gameover, daemon=True).start()

    # ==================== TÍNH NƯỚC ĐI ====================
    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try:
            self._do_auto_move()
        except Exception as e:
            print(f"[MOVE-THREAD] Crash: {e}")
            traceback.print_exc()
        finally:
            self._thinking = False

    def _do_auto_move(self):
        if not self._engine_alive():
            print("[ENGINE] Engine chết -> restart")
            if self._engine_restart_count < MAX_ENGINE_RESTARTS_PER_GAME:
                self._engine_restart_count += 1
                self._init_engine()
            if not self.engine:
                print("[ENGINE] ❌ Không khởi động được"); return

        now = time.time()
        deadline = self._turn_deadline if self._turn_deadline > 0 else (now + MOVE_DEADLINE_SECONDS)
        remain = deadline - now
        if remain < 3.0:
            hard_timeout = max(remain - 0.5, 1.0)
            movetime_ms = int(max(hard_timeout * 1000 - 500, 500))
        else:
            hard_timeout = min(remain - 1.0, 6.0)
            movetime_ms = min(3000, int((hard_timeout - 1.0) * 1000))
        if hard_timeout < 1.0:
            print("[TURN] Sắp hết giờ"); return

        fen, moves = self.board.get_current_fen()
        fixed = self.fixed_pawn_positions if self.fixed_pawn_positions else None

        print(f"[ENGINE-IN] FEN: {fen}", flush=True)
        print(f"[ENGINE-IN] moves({len(moves)})", flush=True)
        print(f"[ENGINE-IN] timeout={hard_timeout:.1f}s", flush=True)

        if len(moves) > MAX_SAFE_MOVES:
            if len(self.board.uci_moves) >= TRUST_ENGINE_AFTER:
                pass
            else:
                print(f"[ENGINE] ⚠️ Moves quá dài"); return

        raw = self.get_best_move(fen, moves, fixed_positions=fixed,
                                 movetime_ms=movetime_ms, hard_timeout=hard_timeout)

        if not raw:
            fp = (fen, tuple(moves))
            if fp == self._engine_crash_fingerprint:
                self._engine_crash_count += 1
            else:
                self._engine_crash_fingerprint = fp
                self._engine_crash_count = 1
            if self._engine_crash_count >= 2:
                print(f"[ENGINE] Crash {self._engine_crash_count} lần -> fallback FEN trần", flush=True)
                fallback_fen = (f"{self.board.start_fen} {self.board.bag_string()} w - - 0 1")
                raw = self.get_best_move(fallback_fen, [], fixed_positions=None,
                                         movetime_ms=movetime_ms, hard_timeout=hard_timeout)
            else:
                for attempt in (1, 2):
                    if not self._engine_alive():
                        if self._engine_restart_count >= MAX_ENGINE_RESTARTS_PER_GAME: break
                        self._engine_restart_count += 1
                        self._init_engine()
                        if not self.engine: break
                    raw = self.get_best_move(fen, moves, fixed_positions=fixed,
                                             movetime_ms=movetime_ms, hard_timeout=hard_timeout)
                    if raw: break

        if not raw:
            print("[ENGINE] -> FALLBACK MultiPV", flush=True)
            fallback_move = self._find_legal_fallback(fen, moves)
            if fallback_move:
                raw = f"bestmove {fallback_move}"

        if not raw:
            print("[ENGINE] ❌ Bỏ lượt", flush=True); return

        parts = raw.split()
        if len(parts) < 2: return
        best_move = parts[1]
        print(f"[ENGINE-OUT] bestmove: {best_move}", flush=True)

        if best_move in self._rejected_moves:
            alt = self._find_legal_fallback(fen, moves)
            if alt: best_move = alt
        if best_move in ("(none)", "0000"):
            self.board.is_my_turn = False; return

        if best_move:
            try:
                source_pos, target_pos = self.board.engine_move_to_pos(best_move)
                _turn_start = self._turn_started_at if self._turn_started_at > 0 else time.time()
                _remain_min = MIN_MOVE_SECONDS - (time.time() - _turn_start)
                if _remain_min > 0: time.sleep(_remain_min)
                if not (self.board.is_my_turn and self.board.is_playing): return
                if self._played_this_turn: return

                # ★ DEBUG: in ra rank để verify
                s_rank = int(best_move[1])
                t_rank = int(best_move[3])
                print(f"-> Đi: {best_move} (pos {source_pos}->{target_pos}) "
                      f"s_rank={s_rank} t_rank={t_rank} "
                      f"[{self._last_score} d{self._last_depth}] "
                      f"| uci={len(self.board.uci_moves)} "
                      f"| flip={self.board.flip}", flush=True)
                self._last_sent_move = best_move
                self.send_play(source_pos, target_pos)
            except Exception as e:
                print(f"[BOT ERROR] {e}")
                traceback.print_exc()

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def loop():
            while self.connected:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=loop, daemon=True).start()

    def run(self):
        print("[BOT] Khởi chạy...")
        while True:
            try:
                now_ts = time.time()
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    print("[WS] 120s không nhận -> reconnect")
                    if self.ws:
                        try: self.ws.close()
                        except: pass
                    time.sleep(2)
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        print("[WS] Ván treo 300s -> reconnect")
                        if self.ws:
                            try: self.ws.close()
                            except: pass
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        print(f"[BOT] ⚠️ TK {USER} có thể đang đăng nhập chỗ khác")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        print(f"[WS] Rớt liên tiếp {self._reconnect_streak} -> chờ {delay}s")
                        time.sleep(delay)
                    if not fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False; self.in_game = False
                    self._joining_table = False
                    self._bet_amts_loaded = False
                    self._resolved_bet_id = None
                    self.bet_amts = []; self.fixed_pawn_positions = set()
                    self._thinking = False; self._played_this_turn = False
                    self.board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                # ★★★ BỎ WATCHDOG 12s - chỉ watchdog deadline ★★★
                if (self.board.is_playing and self.board.is_my_turn
                        and self._turn_deadline > 0
                        and time.time() > self._turn_deadline - 3.0
                        and not self._played_this_turn):
                    if not self._thinking:
                        print(f"[TURN-DEADLINE] {int(self._turn_deadline - time.time())}s")
                        threading.Thread(target=self._make_auto_move, daemon=True).start()

                if self.board.is_playing:
                    self._sit_alone_since = None
                else:
                    if self.in_game and not self._joining_table:
                        opp_id = self.opponent_player_id()
                        if opp_id is None:
                            if self._sit_alone_since is None:
                                self._sit_alone_since = time.time()
                            else:
                                elapsed = time.time() - self._sit_alone_since
                                if elapsed >= 30.0:
                                    print(f"[TABLE] Chờ {int(elapsed)}s -> rời")
                                    self.leave_table()
                        else:
                            self._sit_alone_since = None

                if (self._enter_fail_at and self.in_game
                        and not self.board.is_playing
                        and time.time() - self._enter_fail_at > 60):
                    print("[TABLE] 60s không vào ván -> bỏ")
                    self._enter_fail_at = 0.0
                    self.leave_table()

                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table):
                    now = time.time()
                    if now - self._last_quick_play_time >= self._QUICK_PLAY_INTERVAL:
                        if not self._bet_amts_loaded:
                            self.send_list_bet_amt()
                        elif BOT_USE_CREATE_TABLE:
                            bid = (self._resolved_bet_id
                                   if self._resolved_bet_id is not None
                                   else self.resolve_bet_amt_id())
                            print(f"[CREATE] 🪑 {BOT_BET_XU} xu (bet_id={bid})")
                            self.send_create_table(bet_amt_id=bid)
                        else:
                            valid_bets = self.get_1k_to_5k_bet_objs()
                            if valid_bets:
                                bet_obj = random.choice(valid_bets)
                                room = random.choice(self.ROOM_LIST)
                                self.send_quick_play(room_id=room, bet_amt_id=bet_obj['id'])
                                self._quick_play_attempts += 1
                            else:
                                self.send_create_table()
                                self._quick_play_attempts = 0
                time.sleep(1)
            except KeyboardInterrupt: break
            except Exception as e:
                print(f"[RUN ERROR] {e}")
                traceback.print_exc()
                time.sleep(5)

    def cleanup(self):
        self._kill_engine()
        if self.ws:
            try: self.ws.close()
            except: pass


def acquire_single_instance_lock():
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{USER}.lock")
        f = open(path, "w")
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"[BOT] ❌ Đã có bot khác chạy TK {USER}"); sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except ImportError:
        return None


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = PikafishBot()
    def signal_handler(sig, frame):
        bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try: bot.run()
    finally: bot.cleanup()
