#!/usr/bin/env python3
"""
zaro_multi - Xiangqi Bot (gamevh.net) - engine Pikafish - ĐA TÀI KHOẢN TRONG 1 TIẾN TRÌNH
Kiến trúc:
  - Mỗi tài khoản = 1 luồng AccountSession riêng (WebSocket + logic game độc lập).
  - DÙNG CHUNG 1 EnginePool: ENGINE_POOL process Pikafish (Threads=1), rút thế cờ
    (FEN) từ hàng đợi chung. Không còn "1 bot = 1 engine" tốn tài nguyên.
  - movetime mặc định 150ms/nước -> pool 3 engine đáp ứng ~20 nước/giây, dư sức
    cho 20 bàn chơi đồng thời.
  - Tài khoản đọc từ file (mặc định acc_valid_2.txt), mật khẩu chung qua env.
  - Login lệch pha ngẫu nhiên giữa các tài khoản (tránh dồn dập cùng lúc).

Biến môi trường chính:
  ACCOUNTS_FILE      = acc_valid_2.txt   (file danh sách username, 1 user/dòng)
  MAX_ACCOUNTS       = 20                (số tài khoản lấy từ file)
  ACCOUNT_OFFSET     = 0                 (bỏ qua N dòng đầu - dùng lô acc khác)
  BOT_PASSWD         = nhat123456        (mật khẩu dùng chung)
  MOVETIME_MS        = 150               (thời gian engine nghĩ mỗi nước)
  ENGINE_POOL        = 3                 (số process Pikafish dùng chung)
  ENGINE_THREADS     = 1                 (số luồng mỗi engine)
  ENGINE_HASH        = 256               (MB hash mỗi engine)
  ENGINE_PATH        = (tự tìm ~/pikafish; hỗ trợ đường dẫn .py để test)
  CARO_RUNTIME_HOURS = 5.9               (thời gian chạy mỗi phiên)
  LOGIN_STAGGER      = 8-20              (giãn cách đăng nhập giữa các acc, giây)
  MIN_MOVE_SECONDS   = 0.2               (thời gian tối thiểu hiển thị mỗi nước)
  SNIFF_MODE         = 0                 (1 = in toàn bộ gói WS - log rất lớn!)

Cấp xu (tk chính chuyển xu cho các nick khi login, qua WS cmd TRANSFER=317):
  FUND_ACCOUNT       = (rỗng)            (tài khoản cấp xu, vd nguyenpy2; rỗng = tắt)
  FUND_PASSWD        = (rỗng)            (mật khẩu tài khoản cấp xu)
  FUND_AMOUNT        = 3000              (số xu chuyển mỗi lần cấp)
  FUND_MIN_BALANCE   = 3000              (chỉ cấp cho nick có số dư < ngưỡng; 0 = luôn cấp)
  FUND_WAIT_S        = 300               (nick chờ cấp tối đa bao nhiêu giây rồi vào chơi)
"""

import struct
import threading
import time
import sys
import os
import requests
import re
import subprocess
import signal
import atexit
import tempfile
import json
import random
import queue

# ==================== CẤU HÌNH (đọc từ biến môi trường) ====================

def _env_str(key, default):
    val = os.environ.get(key)
    if val is not None and str(val).strip():
        return str(val).strip()
    return default

def _env_int(key, default):
    try: return int(_env_str(key, default))
    except (TypeError, ValueError): return default

def _env_float(key, default):
    try: return float(_env_str(key, default))
    except (TypeError, ValueError): return default

ACCOUNTS_FILE   = _env_str("ACCOUNTS_FILE", "acc_valid_2.txt")
MAX_ACCOUNTS    = _env_int("MAX_ACCOUNTS", 20)
ACCOUNT_OFFSET  = _env_int("ACCOUNT_OFFSET", 0)
BOT_PASSWD      = _env_str("BOT_PASSWD", "nhat123456")

# ---- Cấp xu: tk chính chuyển xu cho các nick (qua WS cmd TRANSFER) ----
FUND_ACCOUNT      = _env_str("FUND_ACCOUNT", "")
FUND_PASSWD       = _env_str("FUND_PASSWD", "")
FUND_AMOUNT       = _env_int("FUND_AMOUNT", 3000)
FUND_MIN_BALANCE  = _env_int("FUND_MIN_BALANCE", 3000)   # 0 = luôn cấp cho mọi nick
FUND_WAIT_S       = _env_int("FUND_WAIT_S", 300)

MOVETIME_MS       = _env_int("MOVETIME_MS", 150)
ENGINE_POOL_SIZE  = max(1, _env_int("ENGINE_POOL", 3))
ENGINE_THREADS    = max(1, _env_int("ENGINE_THREADS", 1))
ENGINE_HASH_MB    = max(16, _env_int("ENGINE_HASH", 256))
ENGINE_PATH_ENV   = _env_str("ENGINE_PATH", "")

RUNTIME_HOURS     = _env_float("CARO_RUNTIME_HOURS", 5.9)
_LOGIN_STAGGER_RAW = _env_str("LOGIN_STAGGER", "8-20")
try:
    _sg = [float(x) for x in _LOGIN_STAGGER_RAW.replace(":", "-").split("-")]
    LOGIN_STAGGER_MIN, LOGIN_STAGGER_MAX = min(_sg), max(_sg)
except Exception:
    LOGIN_STAGGER_MIN, LOGIN_STAGGER_MAX = 8.0, 20.0

MIN_MOVE_SECONDS = _env_float("MIN_MOVE_SECONDS", 0.2)

# Kick đối phương sau khi hết ván (giống nguyen1..nguyen6):
#   "when_lose" / "when_win" / "always" / "off"
KICK_MODE  = _env_str("KICK_MODE", "when_lose")
KICK_DELAY = _env_float("KICK_DELAY", 5.0)

BOT_MATCH_DURATION  = '10'
BOT_TURN_DURATION   = '60'
BOT_ACC_DURATION    = '0'
BOT_BLOCK_SOFTWARE  = '0'
BOT_TABLE_PASSWORD  = ''
BOT_BET_XU          = _env_int("BOT_BET_XU", 1000)   # mức cược bàn bot tạo: 1000xu (create-only)

# ---- TÌM BÀN CÓ SẴN Ở CÁC SẢNH (JOIN người chơi thay vì chỉ tạo bàn chờ) ----
# Dựa trên giao thức client web gamevh: LIST_ZONE_ROOM (412) -> LIST_ZONE_TABLE (411)
# -> GET_TABLE_DATA (414) -> ENTER_PLACE vào bàn. Bot sẽ đi từng sảnh có người,
# tìm bàn chưa chơi/còn 1 ghế trống/đúng mức cược rồi VÀO CHƠI; nếu hết sảnh
# không thấy bàn nào thì quay về tạo bàn chờ như cũ.
SCAN_TABLES       = _env_str("SCAN_TABLES", "0") == "1"   # 0 = CHỈ TẠO BÀN (mặc định); 1 = quét sảnh tìm bàn
SCAN_INTERVAL     = _env_int("SCAN_INTERVAL", 25)         # giây nghỉ giữa 2 vòng quét
SCAN_PAUSE        = _env_int("SCAN_PAUSE", 150)           # quét hết sảnh không thấy bàn -> nghỉ lâu
SCAN_STEP_TIMEOUT = _env_int("SCAN_STEP_TIMEOUT", 12)     # 1 bước quét quá N giây coi như hỏng
SCAN_MAX_ROOMS    = _env_int("SCAN_MAX_ROOMS", 4)         # tối đa N sảnh mỗi vòng quét
SCAN_ANY_BET      = _env_str("SCAN_ANY_BET", "0") == "1"  # 1 = cho vào bàn mức cược CAO hơn BOT_BET_XU
WS_SNIFF_MODE = _env_str("SNIFF_MODE", "0") == "1"   # mặc định TẮT: 20 acc in sniff là tràn log

WS_URL    = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL  = "https://gamevh.net/play/xiangqi/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
GAME_ID   = 'xiangqi'
ZONE_BASE = 'Lobby.' + GAME_ID          # đường dẫn gốc các sảnh: Lobby.xiangqi.<roomId>
_DEFAULT_PLACE = 'Lobby.xiangqi.0'

# MultiPV luôn = 1 trong bản multi: TrendAnalyzer theo PV chỉ có ý nghĩa với
# engine riêng từng bot; ở pool dùng chung, worker chỉ dùng TrendAnalyzer tạm
# thời (MultiPV=3) khi cần né "chốt cố định" trên bàn đặc biệt.
ENGINE_MULTIPV = 1

STOP_EVENT = threading.Event()   # SIGTERM/SIGINT -> dừng gọn
_DEADLINE  = None                # thời điểm hết giờ runtime (set trong main)

def deadline_reached():
    return (STOP_EVENT.is_set()
            or (_DEADLINE is not None and time.time() > _DEADLINE))

_PRINT_LOCK = threading.Lock()
def log(user, tag, msg):
    """In log có tiền tố tài khoản, khoá để 20 luồng không trộn dòng."""
    with _PRINT_LOCK:
        print(f"[{user}][{tag}] {msg}", flush=True)

# ==================== TÊN HIỂN THỊ NGẪU NHIÊN (marker đồng đội) ====================

VN_TEN_DAU = [
    "Tuấn", "Minh", "Đức", "Hoàng", "Huy", "Hùng", "Dũng", "Cường", "Long", "Nam",
    "Sơn", "Hải", "Phong", "Thắng", "Trung", "Kiên", "Quân", "Thanh", "Đạt", "Khoa",
    "Phúc", "Nghĩa", "Trọng", "Quang", "Bảo", "Khánh", "Hiếu", "Lâm", "Trí", "Thịnh",
    "Lộc", "Phát", "Tiến", "Việt", "Duy", "Vĩnh", "Phước", "Bình", "Đăng", "Tùng",
    "Vũ", "An", "Bách", "Công", "Đại", "Hiệp", "Hòa", "Khai", "Khang", "Khôi",
    "Mạnh", "Nhật", "Phi", "Phú", "Sang", "Tài", "Tâm", "Thái", "Thuận", "Toàn",
    "Triết", "Từ", "Linh", "Trang", "Lan", "Mai", "Hương", "Ngọc", "Thảo", "Vy",
    "Hân", "Châu", "Nhi", "Yến", "Quỳnh", "Ngân", "Trâm", "Phương", "Huyền", "Thủy",
    "Hằng", "Nga", "Tuyết", "Loan", "Oanh", "Bích", "Diễm", "Kiều", "Liên", "Giang",
    "Quyên", "Như", "Hà", "Xuân", "My", "Thu", "Anh", "Hiền", "Huế", "Ly",
    "Nhung", "Thương", "Tiên", "Trinh", "Trúc", "Uyên", "Vân"
]

VN_TEN_KHONG_DAU = [
    "Tuan", "Minh", "Duc", "Hoang", "Huy", "Hung", "Dung", "Cuong", "Long", "Nam",
    "Son", "Hai", "Phong", "Thang", "Trung", "Kien", "Quan", "Thanh", "Dat", "Khoa",
    "Phuc", "Nghia", "Trong", "Quang", "Bao", "Khanh", "Hieu", "Lam", "Tri", "Thinh",
    "Loc", "Phat", "Tien", "Viet", "Duy", "Vinh", "Phuoc", "Binh", "Dang", "Tung",
    "Vu", "An", "Bach", "Cong", "Dai", "Hiep", "Hoa", "Hung", "Khai", "Khang",
    "Khoi", "Manh", "Nhat", "Phi", "Phu", "Sang", "Tai", "Tam", "Thai", "Thuan",
    "Toan", "Triet", "Tu", "Linh", "Trang", "Lan", "Mai", "Huong", "Ngoc", "Thao",
    "Vy", "Han", "Chau", "Nhi", "Yen", "Quynh", "Ngan", "Tram", "Phuong", "Huyen",
    "Thuy", "Hang", "Nga", "Tuyet", "Loan", "Oanh", "Bich", "Diem", "Kieu", "Lien",
    "Giang", "Quyen", "Nhu", "Ha", "Xuan", "My", "Thu", "Anh", "Dung", "Hien",
    "Hoa", "Hue", "Ly", "Nhung", "Thu", "Thuong", "Thuy", "Tien", "Trinh", "Truc", "Uyen", "Van"
]

def generate_dotted_full_name():
    """Tên tiếng Việt ngẫu nhiên + chèn 1 dấu chấm ngẫu nhiên (marker nhận diện đồng đội)."""
    name = random.choice(VN_TEN_DAU if random.choice([True, False]) else VN_TEN_KHONG_DAU)
    if len(name) >= 2:
        pos = random.randint(1, len(name) - 1)
        name = name[:pos] + "." + name[pos:]
    return name

# Danh sách tên của "đồng đội" (các acc do bot này quản lý) để KHÔNG đánh nhau.
_FAMILY_NAMES = set()
_FAMILY_LOCK = threading.Lock()

def add_family_name(name):
    if not name: return
    with _FAMILY_LOCK:
        _FAMILY_NAMES.add(str(name).strip().upper())

def is_known_family(name):
    if not name: return False
    with _FAMILY_LOCK:
        return str(name).strip().upper() in _FAMILY_NAMES

# ==================== ĐĂNG KÝ BÀN ĐANG CHƠI (dùng chung nhiều process) ====================

ACTIVE_TABLES_FILE = os.path.join(tempfile.gettempdir(), "zaro_active_tables.json")
_TABLES_LOCK = threading.Lock()

def get_active_bot_tables():
    try:
        if not os.path.exists(ACTIVE_TABLES_FILE):
            return {}
        with open(ACTIVE_TABLES_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
        now = time.time()
        return {tp: info for tp, info in data.items() if isinstance(info, dict) and now - info.get("timestamp", 0) < 180}
    except Exception:
        return {}

def register_bot_table(table_path, user):
    if not table_path: return
    with _TABLES_LOCK:
        try:
            data = get_active_bot_tables()
            data[table_path] = {"user": user, "timestamp": time.time(), "pid": os.getpid()}
            with open(ACTIVE_TABLES_FILE, 'w') as f:
                json.dump(data, f)
        except Exception: pass

def unregister_bot_table(table_path):
    if not table_path: return
    with _TABLES_LOCK:
        try:
            data = get_active_bot_tables()
            if table_path in data:
                data.pop(table_path, None)
                with open(ACTIVE_TABLES_FILE, 'w') as f:
                    json.dump(data, f)
        except Exception: pass

def is_block_software_message(raw_bytes):
    """Kiểm tra dữ liệu bàn có cấu hình Chống Software (blockSoftware=1/true) không."""
    try:
        idx = raw_bytes.find(b"blockSoftware")
        if idx != -1:
            snippet = raw_bytes[idx:idx+40]
            if b"1" in snippet or b"true" in snippet.lower():
                return True
    except Exception:
        pass
    return False

# ==================== GIAO THỨC BINARY (giữ nguyên từ bản gốc) ====================

CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 411: "LIST_ZONE_TABLE", 412: "LIST_ZONE_ROOM",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 415: "TABLE_IN_ROOM_CHANGED",
    416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
    317: "TRANSFER", 319: "BALANCE_CHANGED",
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

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

class XiangqiBoardTracker:
    INITIAL_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w"
    def __init__(self): self.reset()
    def reset(self):
        self.fen = self.INITIAL_FEN
        self.move_history = []
        # Mốc để nạp engine: base_fen + các nước kể từ mốc. Giữ danh sách nước để
        # engine phát hiện LẶP NƯỚC (chiếu/đuổi liên tục = THUA theo luật cờ tướng).
        self.base_fen = self.INITIAL_FEN.split(' ')[0]
        self.base_side = 'w'
        self.moves_since_base = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
    def pos_to_engine_move(self, source_pos, target_pos):
        s_col, s_row = source_pos % 9, source_pos // 9
        t_col, t_row = target_pos % 9, target_pos // 9
        return f"{chr(ord('a') + s_col)}{s_row}{chr(ord('a') + t_col)}{t_row}"
    def engine_move_to_pos(self, engine_move):
        s_col, s_row = ord(engine_move[0]) - ord('a'), int(engine_move[1])
        t_col, t_row = ord(engine_move[2]) - ord('a'), int(engine_move[3])
        return s_row * 9 + s_col, t_row * 9 + t_col
    @staticmethod
    def _apply_move_to_fen(board_fen, move):
        """Áp 1 nước vào bàn cờ (cờ tướng không có nhập thành/phong cấp nên chỉ là dời quân)."""
        try:
            grid = []
            for r in board_fen.split('/'):
                line = []
                for ch in r:
                    if ch.isdigit(): line.extend(['.'] * int(ch))
                    else: line.append(ch)
                if len(line) != 9: return None
                grid.append(line)
            if len(grid) != 10: return None
            s_col, s_rank = ord(move[0]) - 97, int(move[1])
            t_col, t_rank = ord(move[2]) - 97, int(move[3])
            s_row, t_row = 9 - s_rank, 9 - t_rank
            if not (0 <= s_row < 10 and 0 <= t_row < 10 and 0 <= s_col < 9 and 0 <= t_col < 9):
                return None
            piece = grid[s_row][s_col]
            if piece == '.': return None
            grid[t_row][t_col] = piece; grid[s_row][s_col] = '.'
            rows = []
            for line in grid:
                out, empty = "", 0
                for c in line:
                    if c == '.': empty += 1
                    else:
                        if empty: out += str(empty); empty = 0
                        out += c
                if empty: out += str(empty)
                rows.append(out)
            return '/'.join(rows)
        except Exception:
            return None

    def get_current_fen(self):
        """Nạp cho engine: mốc thế cờ + các nước kể từ mốc.
        Khi đối phương BỎ LƯỢT: chuỗi luân phiên đứt -> dựng thế hiện tại làm
        MỐC MỚI rồi ghi thẳng bên đi (chỉ mất lịch sử lặp từ thời điểm đó)."""
        my_side = 'w' if self.is_red else 'b'
        turn_side = my_side if self.is_my_turn else ('b' if my_side == 'w' else 'w')
        n = len(self.moves_since_base)
        expected = self.base_side if n % 2 == 0 else ('b' if self.base_side == 'w' else 'w')

        if expected == turn_side:
            return f"{self.base_fen} {self.base_side}", self.moves_since_base

        cur = self.base_fen
        for mv in self.moves_since_base:
            nxt = self._apply_move_to_fen(cur, mv)
            if nxt is None:
                log("SYS", "BOARD", f"Không áp được nước {mv}, giữ nguyên mốc cũ")
                return f"{self.base_fen} {self.base_side}", self.moves_since_base
            cur = nxt
        log("SYS", "BOARD", f"Đối phương bỏ lượt -> chốt mốc thế cờ mới, bên đi = {turn_side}")
        self.base_fen = cur
        self.base_side = turn_side
        self.moves_since_base = []
        return f"{cur} {turn_side}", []

    def set_base(self, board_fen, side='w'):
        self.fen = board_fen
        self.base_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.base_side = side
        self.moves_since_base = []
        self.move_history = []

    def record_move(self, mv):
        self.move_history.append(mv)
        self.moves_since_base.append(mv)

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == first_turn_slot_id)

class TrendAnalyzer:
    """Phân tích dòng info của engine: quét Sát cục (Mate) và điểm xu hướng (CP)."""
    def __init__(self):
        self.pv_ram_cache = {}
        self.info_regex = re.compile(r"info .* score cp (-?\d+) .* pv (.+)")
        self.mate_regex = re.compile(r"info .* score mate (-?\d+) .* pv (.+)")

    def clear(self):
        self.pv_ram_cache.clear()

    def parse_line(self, line_str):
        mate_match = self.mate_regex.search(line_str)
        if mate_match:
            mate_score = int(mate_match.group(1))
            pv_line = mate_match.group(2).split()
            if pv_line:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": 99999 if mate_score > 0 else -99999,
                    "mate_in": mate_score,
                    "pv_chain": pv_line
                }
                return

        match = self.info_regex.search(line_str)
        if match:
            score = int(match.group(1))
            pv_line = match.group(2).split()
            if len(pv_line) >= 3:
                first_move = pv_line[0]
                self.pv_ram_cache[first_move] = {
                    "current_score": score,
                    "mate_in": None,
                    "pv_chain": pv_line
                }

    def select_best_trend_move(self):
        if not self.pv_ram_cache:
            return None

        for move, data in self.pv_ram_cache.items():
            if data["mate_in"] is not None and data["mate_in"] > 0:
                return move

        best_move = None
        avg_score = sum(d["current_score"] for d in self.pv_ram_cache.values()) / len(self.pv_ram_cache)
        is_negative = avg_score < 0

        if is_negative:
            max_recovery = -999999
            for move, data in self.pv_ram_cache.items():
                if data["current_score"] > max_recovery:
                    max_recovery = data["current_score"]
                    best_move = move
        else:
            max_growth = -999999
            for move, data in self.pv_ram_cache.items():
                if data["current_score"] > max_growth:
                    max_growth = data["current_score"]
                    best_move = move

        return best_move

# ==================== ENGINE POOL: nhiều engine Pikafish dùng chung qua hàng đợi ====================
#
# Vì sao cần pool thay vì 1 engine duy nhất?
#   UCI engine chỉ chạy ĐÚNG 1 search tại một thời điểm. Với 20 bàn, nếu 20 lượt
#   đến dồn nhau, 1 engine xử lý tuần tự sẽ khiến bàn cuối chờ tới
#   20 * movetime. Pool N engine (mỗi con Threads=1) chia việc qua 1 queue chung:
#   thời gian chờ xấp xỉ (20 * movetime) / N.
#
# Mỗi worker:
#   - sở hữu 1 process Pikafish riêng (stdin/stdout pipe) + 1 luồng đọc stdout
#   - rút task từ queue chung, xử lý ĐỒNG BỘ (position -> go movetime -> bestmove)
#   - nếu process chết sẽ tự khởi động lại ở task kế tiếp
#   - hỗ trợ "né chốt cố định": tạm bật MultiPV=3 + TrendAnalyzer chọn nước thay thế

class EngineWorker(threading.Thread):
    def __init__(self, pool, worker_id):
        super().__init__(daemon=True, name=f"engine-worker-{worker_id}")
        self.pool = pool
        self.wid = worker_id
        self.proc = None
        self.trend = TrendAnalyzer()
        self._latest_bestmove = None
        self._last_score = "?"
        self._last_depth = "?"
        self._uci_ok = False
        self._ready_ok = False
        self._alive = True

    # ---------- quản lý process engine ----------

    def _engine_cmd_list(self):
        path = ENGINE_PATH_ENV or os.path.expanduser("~/pikafish")
        if not os.path.isfile(path):
            for cand in ["./pikafish", "/usr/local/bin/pikafish"]:
                if os.path.isfile(cand):
                    path = cand
                    break
        # Hỗ trợ engine giả lập bằng python (dùng cho test offline)
        if path.endswith(".py"):
            return [sys.executable, path]
        return [path]

    def _spawn_engine(self):
        cmd = self._engine_cmd_list()
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                cwd=os.path.expanduser("~"))
        except Exception as e:
            log("SYS", f"ENGINE-{self.wid}", f"❌ Không khởi động được engine {cmd}: {e}")
            self.proc = None
            return False

        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    if not proc.stderr.readline(): break
            except Exception: pass

        def consume_stdout(proc):
            mate_regex = re.compile(r"score mate (-?\d+)")
            score_regex = re.compile(r"depth (\d+).*score (cp|mate) (-?\d+)")
            try:
                while proc.poll() is None:
                    line = proc.stdout.readline()
                    if not line: break
                    line_str = line.strip()
                    if not line_str: continue

                    self.trend.parse_line(line_str)

                    m = score_regex.search(line_str)
                    if m:
                        self._last_depth = m.group(1)
                        self._last_score = ("mate " + m.group(3)) if m.group(2) == "mate" \
                            else f"{int(m.group(3)):+d}"
                    if "uciok" in line_str: self._uci_ok = True
                    if "readyok" in line_str: self._ready_ok = True
                    if line_str.startswith("bestmove"):
                        self._latest_bestmove = line_str
            except Exception:
                pass

        threading.Thread(target=consume_stderr, args=(self.proc,), daemon=True).start()
        threading.Thread(target=consume_stdout, args=(self.proc,), daemon=True).start()

        # Bắt tay UCI đúng chuẩn: uci -> chờ uciok -> setoption -> isready -> readyok
        self._uci_ok = False
        self._ready_ok = False
        self._cmd("uci")
        t0 = time.time()
        while not self._uci_ok and time.time() - t0 < 8:
            time.sleep(0.05)
        if not self._uci_ok:
            log("SYS", f"ENGINE-{self.wid}", "❌ Engine không trả lời uciok")
            self._kill_engine()
            return False

        self._cmd(f"setoption name Threads value {ENGINE_THREADS}")
        self._cmd(f"setoption name Hash value {ENGINE_HASH_MB}")
        self._cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")
        self._cmd("isready")
        t0 = time.time()
        while not self._ready_ok and time.time() - t0 < 8:
            time.sleep(0.05)

        log("SYS", f"ENGINE-{self.wid}",
            f"✅ Sẵn sàng | pid={self.proc.pid} | Threads={ENGINE_THREADS} | Hash={ENGINE_HASH_MB}MB | movetime={MOVETIME_MS}ms")
        return True

    def _kill_engine(self):
        proc = self.proc
        self.proc = None
        if proc:
            try:
                if proc.poll() is None:
                    proc.stdin.write("quit\n"); proc.stdin.flush()
                    proc.wait(timeout=2)
            except Exception:
                try: proc.terminate()
                except Exception: pass

    def _ensure_engine(self):
        if self.proc and self.proc.poll() is None:
            return True
        if self.proc is not None:
            log("SYS", f"ENGINE-{self.wid}", "⚠️ Engine chết -> khởi động lại...")
        self._kill_engine()
        time.sleep(0.5)
        return self._spawn_engine()

    def _cmd(self, text):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
        except Exception:
            pass

    # ---------- xử lý search ----------

    def _wait_bestmove(self, timeout_s):
        self._latest_bestmove = None
        t0 = time.time()
        while True:
            if self._latest_bestmove:
                return self._latest_bestmove
            if self.proc is None or self.proc.poll() is not None:
                return None
            if time.time() - t0 > timeout_s:
                self._cmd("stop")
                time.sleep(0.15)
                if self._latest_bestmove:
                    return self._latest_bestmove
                return None
            time.sleep(0.01)

    def _search(self, fen, moves, movetime_ms):
        self.trend.clear()
        pos_cmd = f"position fen {fen}"
        if moves:
            pos_cmd += " moves " + " ".join(moves)
        self._cmd(pos_cmd)
        self._cmd(f"go movetime {int(movetime_ms)}")
        return self._wait_bestmove(movetime_ms / 1000.0 + 1.5)

    @staticmethod
    def _move_hits_fixed_pawn(move_str, fixed_positions):
        if not fixed_positions or len(move_str) < 4:
            return False
        try:
            src_file = ord(move_str[0]) - ord('a')
            src_rank = int(move_str[1])
            src_pos = src_rank * 9 + src_file
            return src_pos in fixed_positions
        except (ValueError, IndexError):
            return False

    def _search_avoiding_fixed(self, fen, moves, fixed_positions):
        """Port từ _get_move_avoiding_fixed bản gốc: tạm bật MultiPV=3, nếu bestmove
        dính chốt cố định thì nhờ TrendAnalyzer chọn nước thay thế, thử tối đa 5 lần.
        Thời gian mỗi lần thử scale theo MOVETIME_MS (bản gốc cố định 2500ms)."""
        self._cmd("setoption name MultiPV value 3")
        result = None
        try:
            for attempt in range(5):
                self.trend.clear()
                self._cmd(f"position fen {fen}" + ((" moves " + " ".join(moves)) if moves else ""))
                self._cmd(f"go movetime {MOVETIME_MS}")
                bm = self._wait_bestmove(MOVETIME_MS / 1000.0 + 1.0)
                self._cmd("stop")
                time.sleep(0.1)

                if not bm or bm in ("(none)", "0000"):
                    result = bm
                    break
                parts = bm.split()
                best_move = parts[1] if len(parts) >= 2 else None
                if not best_move:
                    result = bm
                    break
                if not self._move_hits_fixed_pawn(best_move, fixed_positions):
                    result = bm
                    break

                log("SYS", f"ENGINE-{self.wid}",
                    f"⚠️ Bestmove {best_move} dính chốt cố định (lần {attempt + 1})")
                alt_move = self.trend.select_best_trend_move()
                if alt_move and not self._move_hits_fixed_pawn(alt_move, fixed_positions):
                    log("SYS", f"ENGINE-{self.wid}", f"✅ TrendAnalyzer chọn nước thay thế: {alt_move}")
                    result = f"bestmove {alt_move}"
                    break
            if result is None:
                result = self._latest_bestmove
        finally:
            self._cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")
        return result

    def _handle_task(self, task):
        fen, moves, fixed_positions, box = task
        try:
            if not self._ensure_engine():
                box["error"] = "engine unavailable"
                return
            if fixed_positions:
                bm = self._search_avoiding_fixed(fen, moves, fixed_positions)
            else:
                bm = self._search(fen, moves, MOVETIME_MS)
            box["bestmove"] = bm
            box["score"] = self._last_score
            box["depth"] = self._last_depth
        except Exception as e:
            box["error"] = str(e)
        finally:
            box["event"].set()

    def run(self):
        # Warm-up: khởi động engine NGAY khi pool start, để nước tính đầu tiên
        # của các bàn không phải chờ thời gian spawn giữa ván đấu.
        for attempt in range(3):
            if STOP_EVENT.is_set() or not self._alive:
                return
            if self._ensure_engine():
                break
            time.sleep(1.0 + attempt)
        while self._alive and not STOP_EVENT.is_set():
            try:
                task = self.pool.task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:   # lệnh dừng
                break
            self._handle_task(task)
        self._kill_engine()

    def stop(self):
        self._alive = False


class EnginePool:
    """Pool engine dùng chung cho mọi AccountSession.
    submit(fen, moves, fixed) -> {'bestmove': 'bestmove x y', 'score': .., 'depth': ..} hoặc None"""

    def __init__(self, size):
        self.size = size
        self.task_queue = queue.Queue()
        self.workers = []
        self._submit_seq = 0
        self._seq_lock = threading.Lock()

    def start(self):
        for i in range(self.size):
            w = EngineWorker(self, i)
            self.workers.append(w)
            w.start()
        log("SYS", "POOL", f"🚀 EnginePool khởi động với {self.size} engine "
                           f"(Threads={ENGINE_THREADS}, Hash={ENGINE_HASH_MB}MB, movetime={MOVETIME_MS}ms)")

    def submit(self, fen, moves, fixed_positions=None, timeout=None):
        box = {"event": threading.Event(), "bestmove": None,
               "score": "?", "depth": "?", "error": None}
        self.task_queue.put((fen, moves, fixed_positions, box))
        if timeout is None:
            # chờ tối đa: movetime + dự phòng queue đợi lâu nhất ~ (số bàn * movetime)
            timeout = MOVETIME_MS / 1000.0 + 1.5 + 20 * MOVETIME_MS / 1000.0 / max(1, self.size - 1)
        finished = box["event"].wait(timeout)
        if not finished or box["error"] or not box["bestmove"]:
            if not finished:
                log("SYS", "POOL", "⚠️ Task engine quá thời gian chờ (queue quá tải?)")
            return None
        return box

    def stop(self):
        for w in self.workers:
            w.stop()
        # Mỗi worker 1 sentinel None (queue vô hạn -> put_nowait không bao giờ raise,
        # vòng lặp put vô hạn cũ khiến stop() treo vĩnh viễn)
        for _ in self.workers:
            try:
                self.task_queue.put(None, timeout=1)
            except Exception:
                pass
        for w in self.workers:
            try: w.join(timeout=3)
            except Exception: pass

    def is_ready(self):
        return any(w.proc and w.proc.poll() is None for w in self.workers)


ENGINE_POOL = EnginePool(ENGINE_POOL_SIZE)

# ==================== ACCOUNT SESSION: 1 tài khoản = 1 luồng độc lập ====================

# ==================== CẤP XU (chuyển xu từ tk chính sang các nick) ====================
#
# Cơ chế chuyển xu giống transfer_one.py trong repo (đã kiểm chứng chạy thật):
#   - HTTP login lấy cookie/token, WS login bằng lệnh "LOGIN"
#   - Gửi lệnh TRANSFER (317): pack_long(dest_player_id) + pack_long(amount)
#   - Thành công khi server trả BALANCE_CHANGED (319) hoặc TRANSFER status=0
#   - Server giới hạn: mỗi lần chuyển phải > 200 xu
# Funder giữ MỘT kết nối WS duy nhất và phục vụ hàng đợi cấp xu cho các nick.

MIN_TRANSFER_XU = 200

class Funder:
    """Tài khoản cấp xu (vd nguyenpy2): đăng nhập 1 phiên riêng, nhận yêu cầu
    cấp xu từ các AccountSession qua hàng đợi, chuyển xu bằng lệnh TRANSFER."""

    def __init__(self):
        self.enabled = bool(FUND_ACCOUNT and FUND_PASSWD)
        self.user = FUND_ACCOUNT
        self.passwd = FUND_PASSWD
        self.amount = max(0, FUND_AMOUNT)
        self.min_balance = FUND_MIN_BALANCE
        self.conn = Conn()
        self.q = queue.Queue()
        self._pending = {}                 # user -> {'event': Event, 'result': str}
        self._pending_lock = threading.Lock()
        self.cookie = None
        self.nickname = None
        self.token = 0
        self.player_id = 0
        self.balance = 0
        self.ws = None
        self.total_ok = 0
        self.total_fail = 0
        self.total_xu = 0
        self._warned_out = False
        self.thread = None

    # ---------- API được gọi từ luồng AccountSession ----------

    def request_fund(self, user, player_id, balance):
        """Xếp hàng xin cấp xu. Trả Event chờ kết quả (None nếu tính năng tắt)."""
        if not self.enabled:
            return None
        with self._pending_lock:
            cur = self._pending.get(user)
            if cur and not cur['event'].is_set():
                return cur['event']        # đã có yêu cầu đang chờ -> tái sử dụng
            ev = threading.Event()
            self._pending[user] = {'event': ev, 'result': 'đang xếp hàng...'}
        self.q.put((user, int(player_id), int(balance)))
        return ev

    def result_of(self, user):
        with self._pending_lock:
            item = self._pending.get(user)
            return item['result'] if item else 'không có yêu cầu'

    # ---------- Vòng đời của luồng funder ----------

    def start(self):
        if not self.enabled:
            return
        self.thread = threading.Thread(target=self._run, daemon=True, name="funder")
        self.thread.start()

    def _run(self):
        log(self.user, "FUND", f"🚀 Funder khởi động | cấp {self.amount:,} xu/nick "
                               f"| ngưỡng cấp: số dư nick < {self.min_balance:,} (0 = luôn cấp)")
        while not deadline_reached():
            if self._http_login():
                break
            log(self.user, "FUND", "⚠️ Đăng nhập thất bại -> thử lại sau 60s")
            self._fail_all_queued("funder chưa đăng nhập được")
            if STOP_EVENT.wait(60):
                return
        if deadline_reached():
            return
        log(self.user, "FUND", f"✅ HTTP login OK | nick={self.nickname} | ID={self.player_id} "
                               f"| số dư={self.balance:,} xu")
        if self.balance <= self.amount:
            log(self.user, "FUND", f"⚠️ Số dư funder ({self.balance:,} xu) không dư dả "
                                   f"-> cân nhắc NẠP THÊM XU cho {self.user}!")

        while not deadline_reached():
            if not self._ws_login():
                log(self.user, "FUND", "⚠️ WS login thất bại -> thử lại sau 30s")
                self._fail_all_queued("funder WS chưa kết nối được")
                if STOP_EVENT.wait(30):
                    return
                continue
            log(self.user, "FUND", "✅ WS sẵn sàng - chờ các nick xin cấp xu")
            while not deadline_reached():
                item = None
                try:
                    item = self.q.get(timeout=0.5)
                except queue.Empty:
                    pass
                self._pump_socket(0.05)    # bắt PING giữ kết nối sống lúc rảnh
                if item:
                    self._fund_one(*item)
                if self.ws is None:
                    # kết nối rớt giữa chừng -> ra ngoài kết nối lại, giữ hàng đợi còn lại
                    log(self.user, "FUND", "⚠️ WS mất -> kết nối lại sau 5s")
                    if STOP_EVENT.wait(5):
                        break
                    break
            self._ws_close()

        log(self.user, "FUND", f"Funder dừng | đã cấp {self.total_xu:,} xu cho "
                               f"{self.total_ok} nick (lỗi: {self.total_fail})")

    # ---------- HTTP login ----------

    def _http_login(self):
        try:
            s = requests.Session()
            ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
            s.headers.update({"User-Agent": ua, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7"})
            s.get(LOGIN_URL, timeout=20)
            resp = s.post(LOGIN_URL, timeout=20,
                          data={"redirect": "/", "USER_NAME": self.user, "PASSWORD": self.passwd,
                                "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                          headers={"Origin": "https://gamevh.net", "Referer": LOGIN_URL},
                          allow_redirects=True)
            if "login.jsp" in resp.url:
                return False
            page = s.get(GAME_URL, timeout=20).text
            tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page)
            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page)
            pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page)
            if not tm or not nm:
                return False
            self.token = int(tm.group(1))
            self.nickname = nm.group(1).strip()
            self.player_id = int(pid.group(1)) if pid else 0
            self.cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
            bal = self._http_balance(s)
            if bal is not None:
                self.balance = bal
            return True
        except Exception as e:
            log(self.user, "FUND", f"Lỗi HTTP login: {type(e).__name__}: {e}")
            return False

    @staticmethod
    def _http_balance(session):
        try:
            r = session.get(PROFILE_URL, timeout=15)
            m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', r.text)
            if m:
                return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception:
            pass
        return None

    # ---------- WebSocket ----------

    def _ws_send(self, cmd, data=b''):
        if not self.ws:
            return
        try:
            self.ws.send_binary(self.conn.pack(cmd, data))
        except Exception:
            pass

    def _ws_close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def _ws_login(self):
        import websocket
        try:
            self.ws = websocket.create_connection(
                WS_URL, cookie=self.cookie,
                header={"Origin": "https://gamevh.net"}, timeout=15)
            data = bytearray()
            data.extend(self.conn.pack_ascii(self.nickname))
            data.extend(self.conn.pack_int(self.token))
            data.extend(self.conn.pack_ascii("5.0.2"))
            data.extend(self.conn.pack_ascii(""))
            data.extend(self.conn.pack_ascii(GAME_ID))
            data.extend(self.conn.pack_byte(1))
            self.ws.send_binary(self.conn.pack("LOGIN", bytes(data)))
            deadline = time.time() + 12
            while time.time() < deadline:
                try:
                    self.ws.settimeout(max(0.3, deadline - time.time()))
                    raw = self.ws.recv()
                except Exception:
                    break
                if not raw:
                    continue
                msg = InboundMessage(raw)
                if msg.command == "PING":
                    self._ws_send("PONG")
                    continue
                if msg.command == "LOGIN":
                    status = msg.read_byte()
                    if status == 0:
                        return True
                    log(self.user, "FUND", f"❌ WS login bị từ chối (status={status})")
                    self._ws_close()
                    return False
            self._ws_close()
            return False
        except Exception as e:
            log(self.user, "FUND", f"Lỗi WS login: {type(e).__name__}: {e}")
            self._ws_close()
            return False

    def _pump_socket(self, timeout):
        """Đọc 1 gói tin (nếu có) lúc rảnh: trả PONG cho PING, log ALERT."""
        if not self.ws:
            return
        try:
            self.ws.settimeout(timeout)
            raw = self.ws.recv()
        except Exception:
            return
        if not raw:
            return
        try:
            msg = InboundMessage(raw)
            if msg.command == "PING":
                self._ws_send("PONG")
            elif msg.command == "ALERT":
                try:
                    log(self.user, "SERVER", f"ALERT: {msg.read_string()}")
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- Chuyển xu ----------

    def _ws_transfer(self, dest_id, amount):
        """Gửi TRANSFER (317) và chờ phản hồi. Trả chuỗi kết quả ('OK...' = thành công)."""
        if not self.ws:
            return "WS chưa kết nối"
        try:
            self.ws.send_binary(self.conn.pack(
                317, struct.pack('>q', dest_id) + struct.pack('>q', amount)))
        except Exception as e:
            self._ws_close()
            return f"gửi lệnh thất bại: {e}"
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                self.ws.settimeout(max(0.3, deadline - time.time()))
                raw = self.ws.recv()
            except Exception:
                self._ws_close()
                return "mất kết nối khi chờ phản hồi server"
            if not raw:
                continue
            try:
                msg = InboundMessage(raw)
            except Exception:
                continue
            if msg.command == "PING":
                self._ws_send("PONG")
                continue
            if msg.command == "BALANCE_CHANGED":
                self.balance = max(0, self.balance - amount)
                return "OK (server xác nhận BALANCE_CHANGED)"
            if msg.command == "TRANSFER":
                st = msg.read_byte()
                try:
                    txt = msg.read_string()
                except Exception:
                    txt = ""
                if st == 0:
                    self.balance = max(0, self.balance - amount)
                    return f"OK (TRANSFER status=0 {txt})".strip()
                return f"server từ chối (status={st}): {txt}"
            if msg.command == "ALERT":
                try:
                    log(self.user, "SERVER", f"ALERT: {msg.read_string()}")
                except Exception:
                    pass
        return "hết 15s không thấy phản hồi server"

    def _fund_one(self, user, player_id, balance):
        try:
            if self.amount <= MIN_TRANSFER_XU:
                res = f"mức cấp {self.amount:,} xu phải > {MIN_TRANSFER_XU} (giới hạn server)"
            elif not player_id:
                res = "nick chưa có playerId"
            elif player_id == self.player_id:
                res = "trùng ID với funder -> bỏ qua"
            else:
                res = self._ws_transfer(player_id, self.amount)
        except Exception as e:
            res = f"lỗi: {type(e).__name__}: {e}"
        ok = res.startswith("OK")
        with self._pending_lock:
            item = self._pending.get(user)
            if item:
                item['result'] = ('thành công - ' + res[3:].strip()) if ok else ('thất bại - ' + res)
                item['event'].set()
        if ok:
            self.total_ok += 1
            self.total_xu += self.amount
            log(self.user, "FUND", f"✅ {user}: +{self.amount:,} xu (ID {player_id}) "
                                   f"| tổng {self.total_xu:,} xu / {self.total_ok} nick "
                                   f"| dư funder ~{self.balance:,}")
        else:
            self.total_fail += 1
            log(self.user, "FUND", f"❌ {user}: {res}")
            if self.balance <= self.amount and not self._warned_out:
                self._warned_out = True
                log(self.user, "FUND", f"⚠️ Số dư {self.user} gần hết ({self.balance:,} xu) "
                                       f"-> CẦN NẠP THÊM XU để tiếp tục cấp cho nick!")
        time.sleep(random.uniform(1.2, 2.5))    # nhịp giữa 2 lệnh chuyển, tránh flood

    def _fail_all_queued(self, reason):
        while True:
            try:
                user, _pid, _bal = self.q.get_nowait()
            except queue.Empty:
                return
            with self._pending_lock:
                item = self._pending.get(user)
                if item:
                    item['result'] = f'thất bại - {reason}'
                    item['event'].set()

FUNDER = Funder()

class AccountSession:
    """Port từ PikafishBot bản gốc. Mọi biến toàn cục (USER/PASSWD/COOKIE/TOKEN/
    CURRENT_PLAYER_ID/PLACE_PATH...) đều chuyển thành thuộc tính của phiên để
    20 tài khoản chạy song song không ghi đè nhau. Engine thay bằng EnginePool."""

    def __init__(self, user, passwd):
        self.user = user
        self.passwd = passwd
        self.display_name = generate_dotted_full_name()   # tên hiển thị riêng, marker đồng đội
        add_family_name(self.display_name)
        add_family_name(self.user)                        # server có thể hiển thị username

        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.http = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        self.http.headers.update({
            "User-Agent": ua,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })

        # Trạng thái đăng nhập / phiên
        self.cookie = ""
        self.token = 0
        self.nickname = user
        self.player_id = 0
        self.place_path = _DEFAULT_PLACE
        self._identity_synced = False

        # Trạng thái kết nối / game
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_create_time = 0
        self._CREATE_INTERVAL = 3.0
        self.player_names = {}
        self._sit_alone_since = None
        self._table_created_by_me = False
        self.bet_amts = []
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._thinking = False
        self._played_this_turn = False
        self._turn_started_at = 0.0
        self._last_play_sent_at = 0.0
        self.turn_timeout = 0
        self.slot_players = {}
        self._pending_kick_id = None
        self._table_path = None
        self._table_path_ts = 0.0
        self._reconnect_streak = 0
        self._connected_since = 0.0
        self._enter_fail_at = 0.0
        self._score = "?"
        self._depth = "?"

        # Trạng thái quét bàn qua các sảnh (SCAN)
        self._enter_kind = None          # 'room' | 'table' | None (phân biệt response với gói 401 đẩy)
        self._pending_enter = False      # True khi vừa gửi ENTER_PLACE -> gói ENTER_PLACE kế tiếp là response
        self._join_is_scan = False       # lần join này do quét sảnh (không phải bàn tự tạo)
        self._scan_state = None          # None|'room_list'|'enter_room'|'table_list'|'checking'
        self._scan_step_at = 0.0
        self._scan_next_at = 0.0         # thời điểm được phép quét tiếp
        self._scan_rooms = []            # hàng đợi room id còn phải thử trong vòng này
        self._scan_target_room = None
        self._current_room_id = 0        # room bot đang đứng (đăng nhập là 0)
        self._scan_candidates = []       # [(|bet-BOT_BET_XU|, table_id, bet, name), ...]
        self._scan_checks = 0            # số GET_TABLE_DATA đã dùng trong vòng quét

    def _log(self, tag, msg):
        log(self.user, tag, msg)

    # ==================== HTTP: đăng nhập + hồ sơ ====================

    def _sync_profile_name(self):
        """Đổi FULL_NAME thành tên ngẫu nhiên có dấu chấm (ẩn danh + nhận diện đồng đội)."""
        try:
            edit_url = "https://gamevh.net/com/ftl/game/profile/update_profile.jsp"
            page = self.http.get(edit_url, timeout=15, allow_redirects=True)
            form_match = re.search(r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>', page.text)
            if not form_match:
                return
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
                    v = val.group(1) if val else ''
                    data[k] = v

            old_full_name = data.get('FULL_NAME', '')
            data['FULL_NAME'] = self.display_name
            data['OLD_PASSWORD'] = self.passwd
            data['SAVE'] = '\uf046'

            self.http.post(
                action, timeout=15, data=data,
                headers={'Origin': 'https://gamevh.net',
                         'Referer': page.url,
                         'Content-Type': 'application/x-www-form-urlencoded'},
                allow_redirects=True)
            self._log("PROFILE", f"👤 Đổi tên hiển thị: '{old_full_name}' -> '{self.display_name}'")
        except Exception as e:
            self._log("PROFILE", f"Lỗi cập nhật tên hiển thị: {e}")

    def _fetch_balance(self):
        """Đọc số dư xu (chipBalance) trên trang profile của chính nick này."""
        try:
            r = self.http.get(PROFILE_URL, timeout=15)
            m = re.search(r'(?is)<div\s+class=["\'][^"\']*chipBalance[^"\']*["\'][^>]*>(.*?)</div>', r.text)
            if m:
                return int(re.sub(r"[^\d]", "", m.group(1)) or 0)
        except Exception as e:
            self._log("FUND", f"Lỗi đọc số dư: {e}")
        return None

    def _maybe_request_funding(self):
        """Nếu số dư nick dưới ngưỡng -> xin tk FUND_ACCOUNT cấp xu, chờ tới FUND_WAIT_S.
        Hết giờ chờ hoặc thất bại thì vẫn vào chơi bình thường (không chặn luồng chính)."""
        try:
            bal = self._fetch_balance()
            if bal is None:
                self._log("FUND", "Không đọc được số dư -> bỏ qua bước cấp xu")
                return
            if FUND_MIN_BALANCE > 0 and bal >= FUND_MIN_BALANCE:
                self._log("FUND", f"Số dư {bal:,} xu >= ngưỡng {FUND_MIN_BALANCE:,} -> không cần cấp")
                return
            ev = FUNDER.request_fund(self.user, self.player_id, bal)
            if ev is None:
                return
            self._log("FUND", f"Số dư {bal:,} xu < ngưỡng {FUND_MIN_BALANCE:,} -> xếp hàng xin "
                              f"{FUND_AMOUNT:,} xu từ {FUND_ACCOUNT} (chờ tối đa {FUND_WAIT_S}s)")
            if ev.wait(FUND_WAIT_S):
                self._log("FUND", f"Kết quả cấp xu: {FUNDER.result_of(self.user)}")
            else:
                self._log("FUND", f"⏰ Chờ cấp xu quá {FUND_WAIT_S}s -> vào chơi với số dư hiện có")
        except Exception as e:
            self._log("FUND", f"Lỗi xin cấp xu: {e}")

    def fetch_session_info(self):
        """Đăng nhập bằng user/passwd và lấy token/nickname/playerId cho TÀI KHOẢN NÀY."""
        try:
            session = self.http

            # B1: mở trang login để lấy JSESSIONID
            session.get(LOGIN_URL, timeout=20)

            # B2: POST đăng nhập
            resp = session.post(
                LOGIN_URL, timeout=20,
                data={"redirect": "/", "USER_NAME": self.user, "PASSWORD": self.passwd,
                      "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                headers={"Origin": "https://gamevh.net",
                         "Referer": LOGIN_URL,
                         "Content-Type": "application/x-www-form-urlencoded"},
                allow_redirects=True)
            if "login.jsp" in resp.url:
                self._log("SESSION", f"Đăng nhập thất bại (sai tài khoản/mật khẩu?): {resp.url}")
                return False

            # Đổi tên hiển thị (có dấu chấm): 1 lần mỗi phiên
            if not self._identity_synced:
                self._identity_synced = True
                self._sync_profile_name()

            # B3: vào trang game để lấy token / nickname / playerId
            game_resp = session.get(GAME_URL, timeout=20)
            page_html = game_resp.text

            tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
            if not tm:
                self._log("SESSION", "Không tìm thấy token")
                return False
            self.token = int(tm.group(1))

            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page_html)
            if not nm:
                self._log("SESSION", "Không tìm thấy currentPlayerNickName")
                return False
            self.nickname = nm.group(1).strip()
            add_family_name(self.nickname)

            pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
            if pid:
                self.player_id = int(pid.group(1))

            pm = re.search(r"var\s+placePath\s*=\s*[\"']([^\"']+)[\"']", page_html)
            if pm:
                self.place_path = pm.group(1)

            # B4: dựng cookie từ session vừa đăng nhập
            self.cookie = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

            if self.nickname != self.user:
                self._log("SESSION", f"Nickname server={self.nickname!r} khác USER={self.user!r}")
            self._log("SESSION", f"Login OK | Token: {self.token} | Nick: {self.nickname} | ID: {self.player_id}")

            # Cấp xu: nick số dư thấp xin tk FUND_ACCOUNT cấp xu trước khi vào chơi
            if FUNDER.enabled and self.player_id:
                self._maybe_request_funding()

            return True
        except Exception as e:
            self._log("SESSION", f"Lỗi đăng nhập: {e}")
            return False

    # ==================== WebSocket ====================

    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=self.cookie,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"}
        )
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
        if isinstance(message, bytes): self._handle_binary_message(message)

    def _on_error(self, ws, error):
        self._log("WS", f"❌ Lỗi kết nối: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            self._log("WS", f"⚠️ MẤT KẾT NỐI GIỮA VÁN (code={code}) -> mất bàn, sẽ tạo bàn mới")
        else:
            self._log("WS", f"Đóng kết nối (code={code}, msg={msg})")
        # Rớt ngay sau khi kết nối (<60s) -> giãn nhịp, tránh dồn dập tạo phiên rác
        if self._connected_since and time.time() - self._connected_since < 60:
            self._reconnect_streak += 1
        else:
            self._reconnect_streak = 0
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._bet_amts_loaded = False
        self.bet_amts = []
        self.fixed_pawn_positions = set()
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try:
                if WS_SNIFF_MODE and cmd not in ("PONG", "PING"):
                    self._log("WS-SEND", f"cmd={cmd} data_hex={data.hex() if data else 'empty'}")
                self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except Exception: pass

    def _send_login(self):
        data = bytearray()
        data.extend(self.conn.pack_ascii(self.nickname))
        data.extend(self.conn.pack_int(self.token))
        data.extend(self.conn.pack_ascii("5.0.2"))
        data.extend(self.conn.pack_ascii(""))
        data.extend(self.conn.pack_ascii(GAME_ID))
        data.extend(self.conn.pack_byte(1))
        if WS_SNIFF_MODE:
            self._log("WS-SNIFF", f"LOGIN send: nickname={self.nickname}, data_hex={bytes(data).hex()}")
        self.send_message("LOGIN", bytes(data))

    def send_enter_place(self, path=None, mode=1):
        data = bytearray()
        data.extend(self.conn.pack_ascii(path or self.place_path))
        data.extend(self.conn.pack_string(""))
        data.extend(self.conn.pack_byte(mode))
        self._pending_enter = True   # gói ENTER_PLACE kế tiếp từ server là RESPONSE (không phải gói 401 đẩy)
        if WS_SNIFF_MODE:
            self._log("WS-SNIFF", f"ENTER_PLACE send: path={path or self.place_path}")
        self.send_message("ENTER_PLACE", bytes(data))

    def send_list_bet_amt(self):
        self.send_message("LIST_BET_AMT")

    def send_list_zone_room(self):
        self.send_message("LIST_ZONE_ROOM")

    def send_list_zone_table(self, room_filter=1):
        # room_filter theo client web: 0=tất cả, 1=chưa đầy, 2=chưa chơi, 3=đang chơi
        self.send_message("LIST_ZONE_TABLE", self.conn.pack_byte(room_filter))

    def send_get_table_data(self, room_id, table_id):
        self.send_message("GET_TABLE_DATA",
                          self.conn.pack_ascii(f"{ZONE_BASE}.{room_id}.{table_id}"))

    def is_family_bot(self, name):
        """Nhận diện bot đồng đội: mọi tài khoản do tiến trình này điều khiển."""
        if not name: return False
        n = name.strip().upper()
        if n == (self.nickname or "").strip().upper():
            return False
        return is_known_family(n)

    def leave_table(self):
        if self.board.is_playing:
            self._log("TABLE", "⚠️ Đang trong ván đấu -> không rời bàn cho đến khi GAMEOVER!")
            return
        self._log("TABLE", "🚪 Rời bàn chơi, quay lại sảnh tạo bàn mới...")
        if getattr(self, '_table_path', None):
            unregister_bot_table(self._table_path)
        self.in_game = False
        self._joining_table = False
        self._join_is_scan = False
        self._table_path = None
        self._table_created_by_me = False
        self._sit_alone_since = None
        self.slot_players.clear()
        self.board.reset()
        self._enter_fail_at = 0.0
        self._current_room_id = 0      # ENTER_PLACE về Lobby.xiangqi.0 như cũ
        self.send_enter_place(self.place_path)

    def send_create_table(self):
        now = time.time()
        if now - self._last_create_time < self._CREATE_INTERVAL: return
        self._last_create_time = now
        bet_amt_id = 0
        for ba in self.bet_amts:
            if ba["value"] == BOT_BET_XU:
                bet_amt_id = ba["id"]
                break
        else:
            if self.bet_amts:
                self._log("CREATE", f"⚠️ Không tìm thấy mức cược {BOT_BET_XU}xu "
                                    f"(có: {[ba['value'] for ba in self.bet_amts]}) -> dùng mức mặc định")
        args = [
            ("matchDuration", str(BOT_MATCH_DURATION)),
            ("turnDuration", str(BOT_TURN_DURATION)),
            ("accDuration", str(BOT_ACC_DURATION)),
            ("blockSoftware", str(BOT_BLOCK_SOFTWARE)),
        ]
        if BOT_TABLE_PASSWORD:
            args.append(("password", BOT_TABLE_PASSWORD))
            self._log("CREATE", f"🔒 Đặt mật khẩu bàn: {BOT_TABLE_PASSWORD}")
        data = bytearray()
        data.extend(self.conn.pack_byte(bet_amt_id))
        data.extend(self.conn.pack_byte(len(args)))
        for arg_name, arg_value in args:
            data.extend(self.conn.pack_ascii(arg_name))
            data.extend(self.conn.pack_string(arg_value))
        self._log("CREATE", f"🎯 Tạo bàn {BOT_BET_XU}xu, bet_id={bet_amt_id}")
        if WS_SNIFF_MODE:
            self._log("WS-SNIFF", f"CREATE_RULE send: data_hex={bytes(data).hex()}")
        self.send_message("CREATE_RULE", bytes(data))

    def send_play(self, source_pos, target_pos):
        self._last_play_sent_at = time.time()
        self._played_this_turn = True
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        if WS_SNIFF_MODE:
            self._log("WS-SNIFF", f"PLAY send: src={source_pos}, tgt={target_pos}")
        self.send_message("PLAY", bytes(data))

    def opponent_player_id(self):
        for sid, pid in self.slot_players.items():
            if pid and pid != self.player_id and sid != self.board.my_slot_id:
                return pid
        return None

    def send_kick_player(self, player_id):
        self._pending_kick_id = player_id
        data = bytearray()
        data.extend(struct.pack('>q', int(player_id)))
        self._log("KICK", f"Gửi KICK_PLAYER playerId={player_id}")
        if WS_SNIFF_MODE:
            self._log("WS-SNIFF", f"KICK_PLAYER send: playerId={player_id}")
        self.send_message(410, bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        self._log("GAME", "⏳ Gửi trạng thái READY...")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    # ==================== XỬ LÝ GÓI TIN TỪ SERVER ====================

    def _handle_binary_message(self, data):
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            if WS_SNIFF_MODE:
                self._log("WS-RECV", f"cmd={cmd} data_hex={data.hex()}")
            if cmd == "PING":
                self.send_message("PONG")
            elif cmd == "LOGIN": self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE": self._handle_enter_place_response(msg)
            elif cmd == "LIST_BET_AMT": self._handle_list_bet_amt_response(msg)
            elif cmd == "LIST_ZONE_ROOM": self._handle_list_zone_room(msg)
            elif cmd == "LIST_ZONE_TABLE": self._handle_list_zone_table(msg)
            elif cmd == "GET_TABLE_DATA": self._handle_get_table_data(msg)
            elif cmd == "CREATE_RULE": self._handle_create_rule_response(msg)
            elif cmd == "SLOT_IN_TABLE_CHANGED": self._handle_slot_changed(msg)
            elif cmd == "PLAYER_ENTERED": self._handle_player_entered(msg)
            elif cmd == "START_MATCH": self._handle_start_match(msg)
            elif cmd == "MOVE": self._handle_move(msg)
            elif cmd == "PLAY" or cmd == "502": self._handle_play_response(msg)
            elif cmd == "SET_TURN": self._handle_set_turn(msg)
            elif cmd == "GAMEOVER": self._handle_gameover(msg)
            elif cmd == "KICK_PLAYER": self._handle_kick_response(msg)
            elif cmd == "ALERT":
                try:
                    self._log("SERVER", f"ALERT: {msg.read_string()}")
                except Exception: pass
        except Exception as e:
            self._log("RECV", f"Lỗi xử lý gói: {e}")

    def _handle_login_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                self.fetch_session_info()
                self._send_login()
                return
            self.send_enter_place()

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()

        # Phân biệt RESPONSE với gói 401 server ĐẨY khi có người vào/ra chỗ mình
        if not self._pending_enter:
            # Gói đẩy -> chỉ quan tâm khi đang trong bàn (bỏ qua như bản cũ)
            return
        self._pending_enter = False
        kind = self._enter_kind or 'room'
        self._enter_kind = None

        # ---- Quét sảnh: response của bước VÀO SẢNH ----
        if kind == 'room' and self._scan_state == 'enter_room':
            if status == 0:
                self._current_room_id = self._scan_target_room
                self._log("SCAN", f"🏠 Đã vào sảnh #{self._current_room_id} -> lấy danh sách bàn")
                self._scan_step('table_list')
                self.send_list_zone_table(1)
            else:
                self._log("SCAN", f"⚠️ Vào sảnh #{self._scan_target_room} bị từ chối (status={status}) -> thử sảnh khác")
                self._scan_next_room()
            return

        if status != 0:
            if self._joining_table:
                if self._join_is_scan:
                    # Bàn người khác: vừa đầy/đang chơi/cần mật khẩu -> hủy, thử bàn khác
                    self._log("TABLE", f"ENTER_PLACE vào bàn quét được trả status={status} -> bỏ bàn này")
                    self._joining_table = False
                    self._join_is_scan = False
                    self._table_path = None
                    self.in_game = False
                    if self._scan_candidates:
                        self._scan_try_join()
                    else:
                        self._scan_next_room()
                else:
                    self._log("TABLE", f"ENTER_PLACE trả status={status} -> coi như đã trong bàn, bấm Sẵn sàng")
                    self._joining_table = False
                    self.in_game = True
                    self._enter_fail_at = time.time()
                    threading.Thread(
                        target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
            return

        if self._joining_table:
            if is_block_software_message(msg.data):
                self._log("GAME", "🛡️ Bàn có chế độ Chống Software (blockSoftware=1). Vẫn sẵn sàng thi đấu!")
            self._joining_table = False
            self._join_is_scan = False
            self._scan_state = None
            self._scan_next_at = 0.0     # hết ván sẽ quét ngay
            self.in_game = True
            self._enter_fail_at = 0.0
            self.last_action_timestamp = time.time()
            def delay_initial_ready():
                time.sleep(3.0)
                self.send_ready(1)
            threading.Thread(target=delay_initial_ready, daemon=True).start()
        elif not self.in_game:
            if self._table_path and time.time() - self._table_path_ts < 180:
                self._log("TABLE", f"Thử ngồi lại bàn cũ: {self._table_path}")
                self.in_game = True
                self._joining_table = True
                path = self._table_path
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self._send_enter_table(path)),
                    daemon=True).start()
                return
            self._bet_amts_loaded = False
            self.send_list_bet_amt()
        else:
            # Gói 401 server đẩy khi đang ngồi trong bàn (người khác ra/vào) -> bỏ qua
            pass

    def _send_enter_table(self, path):
        self._enter_kind = 'table'
        self.send_enter_place(path=path, mode=1)

    def _handle_list_bet_amt_response(self, msg):
        if msg.read_byte() != 0: return
        count = msg.read_byte()
        self.bet_amts = [{"id": i, "value": msg.read_int()} for i in range(count)]
        self._bet_amts_loaded = True
        self._log("BET", f"Đã tải {count} mức cược: {[ba['value'] for ba in self.bet_amts]}")

    def _handle_create_rule_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            self.in_game = True
            self._joining_table = True
            self._table_created_by_me = True
            self._sit_alone_since = time.time()
            self._table_path = table_path; self._table_path_ts = time.time()
            register_bot_table(table_path, self.user)
            self._scan_state = None
            self._join_is_scan = False
            pwd_info = " (có mật khẩu)" if BOT_TABLE_PASSWORD else ""
            self._log("CREATE", f"🎉 Tạo bàn thành công{pwd_info}: {table_path}. Chờ người chơi...")
            def async_join():
                time.sleep(0.5)
                self._send_enter_table(table_path)
            threading.Thread(target=async_join, daemon=True).start()
        else:
            self._log("CREATE", f"❌ Tạo bàn thất bại (status={status}).")
            self._joining_table = False

    # ==================== QUÉT CÁC SẢNH TÌM BÀN CÓ NGƯỜI ====================

    def _scan_step(self, state):
        self._scan_state = state
        self._scan_step_at = time.time()

    def _scan_begin(self):
        self._log("SCAN", "🔎 Quét các sảnh tìm bàn có người chơi...")
        self._scan_checks = 0
        self._scan_candidates = []
        self._scan_step('room_list')
        self.send_list_zone_room()

    def _scan_pause(self, reason, pause=None):
        wait = SCAN_PAUSE if pause is None else pause
        self._scan_state = None
        self._scan_next_at = time.time() + wait
        self._log("SCAN", f"⏹ {reason} -> nghỉ {wait:.0f}s (trong lúc chờ sẽ tạo bàn chờ người vào)")

    def _scan_abort_step(self, reason):
        self._scan_state = None
        self._scan_next_at = time.time() + SCAN_INTERVAL
        self._log("SCAN", f"⚠️ Vòng quét dừng: {reason}")

    def _scan_next_room(self):
        """Thử sảnh tiếp theo trong hàng đợi; hết sảnh -> nghỉ một đoạn."""
        self._scan_candidates = []
        while self._scan_rooms:
            rid = self._scan_rooms.pop(0)
            if rid == self._current_room_id:
                # đã đứng trong sảnh này -> khỏi ENTER_PLACE
                self._scan_step('table_list')
                self.send_list_zone_table(1)
                return
            self._scan_target_room = rid
            self._scan_step('enter_room')
            self._enter_kind = 'room'
            self.send_enter_place(path=f"{ZONE_BASE}.{rid}", mode=1)
            return
        self._scan_pause("đã quét hết các sảnh không có bàn phù hợp")

    def _bet_value_of(self, bet_id):
        for ba in self.bet_amts:
            if ba["id"] == bet_id:
                return ba["value"]
        return None

    def _pick_scan_tables(self, tables):
        """Lọc bàn đáng vào: chưa chơi, còn 1 ghế trống, có 1 người đang chờ,
        không mật khẩu, bàn thường, đúng/không vượt mức cược của mình."""
        active = set()
        for tp in get_active_bot_tables():
            s = str(tp)
            active.add(s)
            if "." in s:
                active.add(s.rsplit(".", 1)[-1])
        cands = []
        for t in tables:
            if t["playing"]:   continue   # đang chơi -> không vào giữa chừng
            if t["pwd"]:       continue
            if t["type"] != 0: continue   # chỉ bàn thường
            if t["slots"] != 1: continue  # cần đúng 1 người chờ + 1 ghế trống
            if str(t["id"]) in active: continue   # bàn của bot nhà mình
            bet = self._bet_value_of(t["bet_id"])
            if bet is None or bet <= 0:   continue
            if not SCAN_ANY_BET and bet > BOT_BET_XU: continue
            cands.append((abs(bet - BOT_BET_XU), t["id"], bet, t["name"]))
        cands.sort(key=lambda c: (c[0], c[1]))
        return cands[:4]

    def _handle_list_zone_room(self, msg):
        if self._scan_state != 'room_list':
            return
        status = msg.read_byte()
        if status != 0:
            self._scan_abort_step(f"LIST_ZONE_ROOM status={status}")
            return
        try:
            count = msg.read_byte()
            rooms = []
            for _ in range(count):
                rid = msg.read_byte()
                name = msg.read_string()
                clients = msg.read_short()
                tables = msg.read_short()
                max_tables = msg.read_short()
                rooms.append({"id": rid, "name": name, "clients": clients,
                              "tables": tables, "max": max_tables})
        except Exception as e:
            self._scan_abort_step(f"parse sảnh lỗi: {e}")
            return
        if not rooms:
            self._scan_pause("server không trả sảnh nào")
            return
        info = ", ".join(f"#{r['id']}{r['name']}({r['clients']}ng/{r['tables']}b)"
                         for r in rooms[:12])
        self._log("SCAN", f"🏘️ {len(rooms)} sảnh: {info}")
        # Ưu tiên sảnh đang đứng (khỏi di chuyển), rồi tới sảnh đông người nhất
        rooms.sort(key=lambda r: (0 if r["id"] == self._current_room_id else 1, -r["clients"]))
        picked = [r["id"] for r in rooms if r["clients"] > 0 and r["tables"] > 0]
        if not picked:   # không sảnh nào có người -> vẫn thử sảnh có bàn
            picked = [r["id"] for r in rooms if r["tables"] > 0]
        if not picked:
            self._scan_pause("không có sảnh nào có bàn")
            return
        self._scan_rooms = picked[:max(1, SCAN_MAX_ROOMS)]
        self._scan_next_room()

    def _handle_list_zone_table(self, msg):
        if self._scan_state != 'table_list':
            return
        status = msg.read_byte()
        if status != 0:
            self._scan_abort_step(f"LIST_ZONE_TABLE status={status}")
            return
        try:
            count = msg.read_int()
            tables = []
            for _ in range(count):
                tid = msg.read_short()
                name = msg.read_string()
                ttype = msg.read_byte()
                bet_id = msg.read_byte()
                slots = msg.read_byte()
                playing = (msg.read_byte() == 0)   # byte 0 = ĐANG chơi (theo client web)
                pwd = (msg.read_byte() == 1)
                tables.append({"id": tid, "name": name, "type": ttype, "bet_id": bet_id,
                               "slots": slots, "playing": playing, "pwd": pwd})
        except Exception as e:
            self._scan_abort_step(f"parse bàn lỗi: {e}")
            return
        self._scan_candidates = self._pick_scan_tables(tables)
        waiting = sum(1 for t in tables if t["playing"] and t["slots"] == 1)
        if not self._scan_candidates:
            self._log("SCAN", f"👁 Sảnh #{self._current_room_id}: {count} bàn "
                              f"({waiting} đang chơi 1 người) -> không có bàn phù hợp")
            self._scan_next_room()
            return
        pretty = ", ".join(f"#{tid}({bet:,}xu)" for _, tid, bet, _ in self._scan_candidates)
        self._log("SCAN", f"🎯 Sảnh #{self._current_room_id}: {len(self._scan_candidates)}/{count} "
                          f"bàn phù hợp: {pretty}")
        self._scan_try_join()

    def _scan_try_join(self):
        """Lần lượt kiểm tra ứng viên bằng GET_TABLE_DATA (tránh bàn bot đồng đội)."""
        if not self._scan_candidates:
            self._scan_next_room()
            return
        if self._scan_checks >= 4:   # đủ bằng số ứng viên tối đa - check rẻ (1 gói/1 bàn)
            self._scan_pause("tất cả ứng viên đều là nhà mình -> tạo bàn chờ")
            return
        _, tid, bet, name = self._scan_candidates[0]
        self._scan_checks += 1
        self._scan_step('checking')
        self.send_get_table_data(self._current_room_id, tid)

    def _handle_get_table_data(self, msg):
        if self._scan_state != 'checking':
            return
        status = msg.read_byte()
        if status != 0 or not self._scan_candidates:
            if self._scan_candidates:
                self._scan_candidates.pop(0)   # bàn vừa đầy/mất -> bỏ, xét bàn kế
                self._scan_try_join()
            else:
                self._scan_next_room()
            return
        try:
            owner = msg.read_long()
            count = msg.read_byte()
            players = []
            for _ in range(count):
                pid = msg.read_long()
                fname = msg.read_string()
                _avatar = msg.read_ascii()
                _avatar_id = msg.read_short()
                _tag = msg.read_byte()
                chip = msg.read_long()
                _star = msg.read_long()
                _score = msg.read_long()
                _level = msg.read_byte()
                players.append((pid, fname, chip))
        except Exception as e:
            self._scan_abort_step(f"parse người trong bàn lỗi: {e}")
            return
        if not self._scan_candidates:
            self._scan_next_room()
            return
        _, tid, bet, name = self._scan_candidates.pop(0)
        if not players:
            self._log("SCAN", f"👁 Bàn #{tid} ({name}) vừa trống (người chờ đã đi) -> bỏ qua")
            self._scan_try_join()
            return
        fam = [f for pid, f, _c in players
               if pid != self.player_id and (is_known_family(f) or ("." in (f or "")))]
        if owner == self.player_id or fam:
            self._log("SCAN", f"🤝 Bàn #{tid} ({name}) là nhà mình ({fam or 'chính mình'}) -> bỏ qua")
            self._scan_try_join()
            return
        who = ", ".join(f"{f or '?'}({_c:,}xu)" for pid, f, _c in players[:2]) or "trống"
        self._log("SCAN", f"✅ Vào bàn #{tid} ({name}) sảnh #{self._current_room_id} "
                          f"cược {bet:,}xu - có {len(players)} người: {who}")
        self._scan_join_table(tid, bet, name)

    def _scan_join_table(self, table_id, bet, name):
        path = f"{ZONE_BASE}.{self._current_room_id}.{table_id}"
        self._joining_table = True
        self._join_is_scan = True
        self._table_path = path
        self._table_path_ts = time.time()
        self._scan_state = None
        self._scan_next_at = 0.0
        register_bot_table(str(table_id), self.user)
        self._log("TABLE", f"➡️ VÀO BÀN #{table_id} ({name}) sảnh #{self._current_room_id}, cược {bet:,}xu...")
        self._send_enter_table(path)

    def _handle_player_entered(self, msg):
        try:
            place_level = msg.read_byte()
            pid = msg.read_long()
            name = msg.read_string()
            if pid > 0 and pid != self.player_id:
                self.player_names[pid] = name
                self._log("PLAYER", f"👤 '{name}' (id={pid}) vào bàn/phòng (level={place_level})")
                if not self.board.is_playing and self.is_family_bot(name) and self.opponent_player_id() == pid:
                    self._log("AVOID", f"⚠️ Phát hiện đồng đội '{name}' ở ghế đối diện! Rời bàn...")
                    self.leave_table()
        except Exception: pass

    def _handle_slot_changed(self, msg):
        try:
            _ = msg.read_string()
            slot_id = msg.read_byte()
            msg.read_long(); msg.read_long(); msg.read_byte(); msg.read_short(); msg.read_ascii(); msg.read_byte(); msg.read_byte()
            player_id = msg.read_long()
            if player_id > 0:
                self.slot_players[slot_id] = player_id
            else:
                self.slot_players.pop(slot_id, None)
            if player_id == self.player_id:
                self.board.my_slot_id = slot_id
            else:
                if player_id > 0:
                    name = self.player_names.get(player_id, "")
                    self._log("TABLE", f"👤 Ghế đối diện (slot={slot_id}): playerId={player_id}{f', name={name}' if name else ''}")
                    if not self.board.is_playing and self.is_family_bot(name):
                        self._log("AVOID", f"⚠️ Đối thủ '{name}' là bot đồng đội! Rời bàn...")
                        self.leave_table()
                        return
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        def delay_ready_on_player():
                            time.sleep(3.0)
                            self.send_ready(1)
                        threading.Thread(target=delay_ready_on_player, daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        self._log("TABLE", "🚪 Không còn đối thủ. Đếm ngược 30s chờ người chơi...")
                        self._sit_alone_since = time.time()
        except Exception: pass

    def _handle_start_match(self, msg):
        self._log("GAME", "🎮 Trận chiến bắt đầu!")
        self._play_reject_count = 0
        self._thinking = False
        self._turn_started_at = 0.0
        self._last_play_sent_at = 0.0
        self._played_this_turn = False
        self._reconnect_streak = 0
        self._enter_fail_at = 0.0
        self._sit_alone_since = None
        self.board.reset()
        self.fixed_pawn_positions.clear()
        self.board.is_playing = True
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        try:
            player_count = msg.read_byte()
            for _ in range(player_count): msg.read_byte(); msg.read_int()
            piece_count = msg.read_byte()
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte(); pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid), self._decode_piece_id(raw_face), pos, is_open))

            msg.read_byte(); mystery_count = msg.read_byte()
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()

            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = self.board.my_slot_id if self.board.my_slot_id >= 0 else first_turn_slot_id

            self.board.set_my_slot(my_slot_id, first_turn_slot_id)

            for sid, face, position, is_open in board_pieces:
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            if self.fixed_pawn_positions:
                self._log("GAME", f"🛡️ Bàn đấu có {len(self.fixed_pawn_positions)} chốt bị liệt/khóa!")

            self.board.set_base(self._build_fen_from_pieces(board_pieces), 'w')
            if my_slot_id == first_turn_slot_id:
                self.board.is_my_turn = True
                self._turn_started_at = time.time()
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            self._log("GAME", f"Lỗi START_MATCH: {e}")

    def _build_fen_from_pieces(self, pieces):
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            game_row, col = position // 9, position % 9
            fen_row = 9 - game_row
            color = face[0]
            piece_type = int(face[1]) if len(face) > 1 else 0
            type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
            fen_char = type_to_fen.get(piece_type, '?')
            if color == 'r': fen_char = fen_char.upper()
            board[fen_row][col] = fen_char
        fen_rows = []
        for row in board:
            fen_row = ""
            empty = 0
            for cell in row:
                if cell == '.': empty += 1
                else:
                    if empty > 0: fen_row += str(empty); empty = 0
                    fen_row += cell
            if empty > 0: fen_row += str(empty)
            fen_rows.append(fen_row)
        return '/'.join(fen_rows) + ' w'

    def _handle_move(self, msg):
        try:
            source_pos = msg.read_byte()
            target_pos = msg.read_byte()
            engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
            self.last_action_timestamp = time.time()
            if not self.board.move_history or self.board.move_history[-1] != engine_move:
                self.board.record_move(engine_move)
                self._played_this_turn = False
        except Exception as e:
            self._log("MOVE", f"Lỗi: {e}")

    def _handle_play_response(self, msg):
        if msg.read_byte() != 0:
            # Server từ chối nước đi -> tự tính lại. KHÔNG pop lịch sử ở đây:
            # nước bị từ chối chưa được ghi, pop sẽ lệch bàn cờ trong đầu bot.
            self.board.is_my_turn = True
            self._played_this_turn = False
            self._play_reject_count = getattr(self, '_play_reject_count', 0) + 1
            self._log("PLAY", f"⚠️ Server từ chối nước đi (lần {self._play_reject_count}) -> tính lại")
            if self._play_reject_count <= 3:
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self._make_auto_move()), daemon=True).start()
        else:
            self._play_reject_count = 0

    def _handle_set_turn(self, msg):
        """SET_TURN: byte slotId, short turnTimeout, short playerRemainDuration.
        slotId == -2 là bộ đếm "Chuẩn bị/Bắt đầu", KHÔNG phải lượt đi."""
        try:
            slot_id = msg.read_byte()
            try:
                turn_timeout = msg.read_short()
            except Exception:
                turn_timeout = 0
            if slot_id == -2:
                return
            if slot_id == -1 or not self.board.is_playing:
                return
            self.turn_timeout = turn_timeout
            was_my_turn = self.board.is_my_turn
            self.board.is_my_turn = (slot_id == self.board.my_slot_id)
            self.last_action_timestamp = time.time()
            if not self.board.is_my_turn:
                return
            self._turn_started_at = time.time()
            if not was_my_turn:
                self._played_this_turn = False
            # Đối phương bỏ lượt: server gửi lại SET_TURN cùng lượt, không kèm MOVE
            # -> phải tính nước MỖI KHI nhận SET_TURN trỏ vào bot (trừ lúc đang tính dở).
            if not self._thinking:
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            self._log("SET_TURN", f"Lỗi: {e}")

    def _handle_kick_response(self, msg):
        try:
            status = msg.read_byte()
            content = msg.read_string()
        except Exception:
            status, content = None, ""
        if self._pending_kick_id is not None:
            pid = self._pending_kick_id; self._pending_kick_id = None
            if status == 0: self._log("KICK", f"✅ Đã đuổi playerId={pid}. {content}")
            else:           self._log("KICK", f"❌ Đuổi playerId={pid} thất bại (status={status}): {content}")
            return
        self._log("KICK", f"⚠️ Bot bị đuổi khỏi bàn: {content}")
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self.board.reset()

    def _handle_gameover(self, msg):
        # Đọc kết quả từng ghế: count(u8) + [slot(i8), result(i8), int64]
        my_result, results = None, {}
        try:
            count = msg.read_byte()
            for _ in range(count):
                sid = msg.read_byte(); res = msg.read_byte(); msg.read_long()
                results[sid] = res
                if sid == self.board.my_slot_id:
                    my_result = res
        except Exception:
            results = {}

        bot_won  = my_result in (1, 11)
        bot_lost = my_result in (2, 4, 12)
        if bot_won:    self._log("GAME", "🏁 Kết thúc. >>> THẮNG <<<")
        elif bot_lost: self._log("GAME", "🏁 Kết thúc. >>> THUA <<<")
        elif my_result is None: self._log("GAME", "🏁 Kết thúc.")
        else: self._log("GAME", "🏁 Kết thúc. >>> HOÀ <<<")

        should_kick = (KICK_MODE == "always"
                       or (KICK_MODE == "when_lose" and bot_lost)
                       or (KICK_MODE == "when_win" and bot_won))
        victim = None
        if should_kick:
            want = (1, 11) if KICK_MODE == "when_lose" else (2, 4, 12)
            target_sid = next((sid for sid, res in results.items()
                               if sid != self.board.my_slot_id and res in want), None)
            victim = self.slot_players.get(target_sid) if target_sid is not None else None
            if not victim:
                victim = self.opponent_player_id()
            if not victim:
                self._log("KICK", "Không xác định được playerId đối phương -> bỏ qua")

        self.fixed_pawn_positions.clear()
        self.board.reset()
        self.board.is_playing = False
        self.board.is_my_turn = False
        self.in_game = True
        self._joining_table = False
        self.last_action_timestamp = time.time()

        def after_gameover():
            is_guest = not getattr(self, '_table_created_by_me', False)
            if bot_lost:
                if victim and not is_guest:
                    time.sleep(KICK_DELAY)
                    if self.connected and not self.board.is_playing:
                        self.send_kick_player(victim)
                        time.sleep(2.0)
                elif is_guest:
                    self._log("GAME", "👤 Khách vào bàn -> không có quyền kick, rời bàn ngay...")
                self._log("GAME", "🔄 Thua trận -> Rời bàn -> Tạo bàn mới...")
                time.sleep(1.0)
                self.leave_table()
            else:
                self._log("GAME", "✅ Thắng/Hoà -> Ở lại bàn, sẵn sàng ván tiếp...")
                time.sleep(3.0)
                self.send_ready(1)
        threading.Thread(target=after_gameover, daemon=True).start()

    # ==================== TÍNH NƯỚC QUA ENGINE POOL ====================

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try:
            self._do_auto_move()
        finally:
            self._thinking = False

    def _do_auto_move(self):
        if not ENGINE_POOL.is_ready():
            time.sleep(0.5)
            if not ENGINE_POOL.is_ready():
                self._log("ENGINE", "❌ Pool engine chưa sẵn sàng")
                return

        fen, moves = self.board.get_current_fen()
        fixed = self.fixed_pawn_positions if self.fixed_pawn_positions else None

        result = ENGINE_POOL.submit(fen, moves, fixed)
        if not result or not result.get("bestmove"):
            return

        self._score = result.get("score", "?")
        self._depth = result.get("depth", "?")

        parts = result["bestmove"].split()
        if len(parts) < 2: return
        best_move = parts[1]

        if best_move in ["(none)", "0000"]:
            self._log("GAME", "⚠️ Pikafish báo: bestmove (none) - Hết nước hợp lệ.")
            self.board.is_my_turn = False
            return

        try:
            source_pos, target_pos = self.board.engine_move_to_pos(best_move)
            # Giữ tối thiểu MIN_MOVE_SECONDS kể từ lúc tới lượt (chỉ bù phần còn thiếu)
            _turn_start = getattr(self, '_turn_started_at', 0.0) or time.time()
            _remain = MIN_MOVE_SECONDS - (time.time() - _turn_start)
            if _remain > 0:
                time.sleep(_remain)
            if self.board.is_my_turn and self.board.is_playing:
                self._log("GAME", f"-> Xuất quân: {best_move} [điểm {self._score} depth {self._depth}]")
                self.send_play(source_pos, target_pos)
        except Exception as e:
            self._log("BOT", f"Dịch tọa độ lỗi: {e}")

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def keep_alive_loop():
            while self.connected and not deadline_reached():
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=keep_alive_loop, daemon=True).start()

    # ==================== VÒNG LẶP CHÍNH CỦA PHIÊN ====================

    def run(self):
        self._log("BOT", "Khởi chạy phiên tài khoản (engine pool dùng chung)...")

        while not deadline_reached():
            try:
                now_ts = time.time()
                # (a) Không nhận được BẤT KỲ gói nào trong 120s -> kết nối chết
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    self._log("WS", "Không nhận dữ liệu 120s -> coi như chết, kết nối lại")
                    if self.ws: self.ws.close()
                    time.sleep(2)
                # (b) Đang trong ván mà 300s không có nước đi -> cắt
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        self._log("WS", "Ván treo 300s không có nước đi -> kết nối lại")
                        if self.ws: self.ws.close()
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        self._log("BOT", "⚠️ Bị ngắt liên tục ngay sau khi đăng nhập.")
                        self._log("BOT", f"⚠️ Nhiều khả năng tài khoản {self.user} đang được ĐĂNG NHẬP Ở NƠI KHÁC (server chỉ cho 1 phiên).")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        self._log("WS", f"Rớt liên tiếp lần {self._reconnect_streak} -> chờ {delay}s rồi đăng nhập lại")
                        time.sleep(delay)
                    if deadline_reached(): break
                    if not self.fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False
                    self.in_game = False
                    self._joining_table = False
                    self._join_is_scan = False
                    self._enter_kind = None
                    self._pending_enter = False
                    self._scan_state = None
                    self._scan_rooms = []
                    self._scan_candidates = []
                    self._current_room_id = 0
                    self._bet_amts_loaded = False
                    self.bet_amts = []
                    self.fixed_pawn_positions = set()
                    self.board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                # Tới lượt nhưng 12s chưa gửi được nước -> tự tính lại
                if (self.board.is_playing and self.board.is_my_turn and not self._thinking
                        and self._turn_started_at
                        and time.time() - self._turn_started_at > 12
                        and not self._played_this_turn):
                    self._log("TURN", "Tới lượt nhưng 12s chưa đi được -> tính lại")
                    self._turn_started_at = time.time()
                    threading.Thread(target=self._make_auto_move, daemon=True).start()

                # Đang trong ván -> không gửi lệnh tìm/tạo/rời bàn
                if self.board.is_playing:
                    self._sit_alone_since = None
                else:
                    if self.in_game and not self._joining_table:
                        opp_id = self.opponent_player_id()
                        if opp_id is not None:
                            self._sit_alone_since = None

                # Sau ENTER_PLACE lỗi: 60s không vào ván -> bỏ bàn cũ, tạo bàn mới
                if (self._enter_fail_at and self.in_game and not self.board.is_playing
                        and time.time() - self._enter_fail_at > 60):
                    self._log("TABLE", "Chờ 60s không vào được ván nào -> bỏ bàn cũ, tạo bàn mới")
                    self._enter_fail_at = 0.0
                    self.leave_table()

                # Tạo bàn mới khi chưa trong bàn - hoặc QUÉT CÁC SẢNH tìm bàn có người
                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table):
                    now = time.time()
                    if not self._bet_amts_loaded:
                        if now - self._last_create_time >= self._CREATE_INTERVAL:
                            self._last_create_time = now
                            self.send_list_bet_amt()
                    elif self._scan_state:
                        # đang quét -> một bước quá lâu (mất gói) thì hủy vòng này
                        if now - self._scan_step_at > SCAN_STEP_TIMEOUT:
                            self._scan_abort_step("một bước quét quá lâu (mất gói?)")
                    elif SCAN_TABLES and now >= self._scan_next_at:
                        self._scan_begin()
                    elif now - self._last_create_time >= self._CREATE_INTERVAL:
                        self._log("CREATE", f"🎯 Tạo bàn mới {BOT_BET_XU}xu...")
                        self.send_create_table()
                time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception:
                time.sleep(5)

        # Hết giờ / dừng -> rời bàn gọn gàng
        if getattr(self, '_table_path', None):
            unregister_bot_table(self._table_path)
        self._log("BOT", "Phiên kết thúc.")

    def cleanup(self):
        if self.ws:
            try: self.ws.close()
            except Exception: pass

# ==================== NẠP TÀI KHOẢN ====================

def load_accounts():
    """Đọc danh sách username từ file (1 user/dòng), bỏ qua dòng trống/#comment.
    Lấy ACCOUNT_OFFSET dòng đầu để bỏ qua, rồi lấy tối đa MAX_ACCOUNTS."""
    path = ACCOUNTS_FILE
    if not os.path.isfile(path):
        # fallback: thử cùng thư mục script
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(path))
        if os.path.isfile(alt):
            path = alt
        else:
            log("SYS", "ACCOUNTS", f"❌ Không tìm thấy file tài khoản: {ACCOUNTS_FILE}")
            return []
    users = []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            u = line.strip()
            if u and not u.startswith('#'):
                users.append(u)
    offset = max(0, ACCOUNT_OFFSET)
    if MAX_ACCOUNTS > 0:
        picked = users[offset:offset + MAX_ACCOUNTS]
    else:
        picked = users[offset:]   # MAX_ACCOUNTS <= 0 -> lấy TẤT CẢ acc còn lại trong file
    # Chống trùng lặp (file có thể chứa user lặp)
    seen, uniq = set(), []
    for u in picked:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq

def acquire_account_lock(user):
    """Chặn 2 tiến trình bot chạy CÙNG MỘT tài khoản trên cùng máy.
    Server gamevh chỉ cho 1 phiên/tài khoản: hai bot cùng acc sẽ đá nhau vô tận."""
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{user}.lock")
        f = open(path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        f.write(str(os.getpid())); f.flush()
        return f
    except ImportError:
        return None   # nền không hỗ trợ fcntl -> bỏ qua (GitHub runner là Linux)

# ==================== LUỒNG CỦA MỖI TÀI KHOẢN ====================

def session_worker(user):
    """Vòng đời của 1 tài khoản: giữ lock -> tạo phiên -> chạy cho đến khi hết giờ.
    Nếu phiên gặp lỗi chưa xử lý được, tạo lại phiên mới (với jitter tránh dồn cụm)."""
    lock_file = acquire_account_lock(user)
    if lock_file is None:
        log(user, "BOT", "❌ Đã có bot khác chạy tài khoản này trên máy này -> bỏ qua")
        return

    try:
        attempt = 0
        while not deadline_reached():
            attempt += 1
            sess = AccountSession(user, BOT_PASSWD)
            try:
                sess.run()
            except Exception as e:
                log(user, "BOT", f"⚠️ Phiên lỗi chưa xử lý được (lần {attempt}): {type(e).__name__}: {e}")
            finally:
                sess.cleanup()
            if deadline_reached():
                break
            # Giãn nhịp giữa 2 phiên, ngẫu nhiên để 20 acc không tái kết nối cùng lúc
            backoff = random.uniform(10.0, 30.0) + min(60.0, 5.0 * attempt)
            log(user, "BOT", f"Tạo phiên mới sau {backoff:.0f}s...")
            if STOP_EVENT.wait(backoff):
                break
    finally:
        try:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass

# ==================== MAIN ====================

def _signal_handler(sig, frame):
    log("SYS", "SIGNAL", f"Nhận tín hiệu {sig} -> dừng gọn toàn bộ phiên...")
    STOP_EVENT.set()

def main():
    global _DEADLINE
    _DEADLINE = time.time() + RUNTIME_HOURS * 3600.0

    print("=" * 72, flush=True)
    print(f"ZARO MULTI-ACCOUNT | engine Pikafish (pool) | game gamevh.net", flush=True)
    print(f"  File tài khoản : {ACCOUNTS_FILE} (offset {ACCOUNT_OFFSET})", flush=True)
    print(f"  Số tài khoản   : tối đa {MAX_ACCOUNTS}", flush=True)
    print(f"  Engine pool    : {ENGINE_POOL_SIZE} x Pikafish (Threads={ENGINE_THREADS}, Hash={ENGINE_HASH_MB}MB)", flush=True)
    print(f"  Movetime/nước  : {MOVETIME_MS} ms (tối thiểu hiển thị {MIN_MOVE_SECONDS}s)", flush=True)
    print(f"  Runtime        : {RUNTIME_HOURS} giờ", flush=True)
    print(f"  Login stagger  : {LOGIN_STAGGER_MIN:.0f}-{LOGIN_STAGGER_MAX:.0f}s giữa các acc", flush=True)
    print(f"  Sniff mode     : {'BẬT (log cực lớn!)' if WS_SNIFF_MODE else 'tắt'}", flush=True)
    print("  Cấp xu         : " + (f"BẬT - {FUND_ACCOUNT} cấp {FUND_AMOUNT:,} xu/nick "
                                      f"(ngưỡng số dư < {FUND_MIN_BALANCE:,}; 0 = luôn cấp)"
                                      if FUNDER.enabled else "tắt (đặt FUND_ACCOUNT + FUND_PASSWD để bật)"), flush=True)
    print("=" * 72, flush=True)

    accounts = load_accounts()
    if not accounts:
        log("SYS", "ACCOUNTS", "Không có tài khoản nào hợp lệ -> thoát")
        sys.exit(1)
    log("SYS", "ACCOUNTS", f"Đã nạp {len(accounts)} tài khoản: {', '.join(accounts[:5])}"
                               + (f" ... (+{len(accounts)-5})" if len(accounts) > 5 else ""))

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    atexit.register(ENGINE_POOL.stop)

    ENGINE_POOL.start()

    if FUNDER.enabled:
        FUNDER.start()

    threads = []
    for i, user in enumerate(accounts):
        if deadline_reached():
            break
        t = threading.Thread(target=session_worker, args=(user,),
                             daemon=True, name=f"account-{user}")
        threads.append(t)
        t.start()
        if i < len(accounts) - 1:
            gap = random.uniform(LOGIN_STAGGER_MIN, LOGIN_STAGGER_MAX)
            log("SYS", "LOGIN", f"Đã khởi động {user} ({i+1}/{len(accounts)}) -> acc kế sau {gap:.0f}s")
            if STOP_EVENT.wait(gap):
                break

    log("SYS", "MAIN", f"🏁 Tất cả {len(threads)} phiên đã được kích hoạt. Chạy đến "
                       f"{time.strftime('%H:%M:%S', time.localtime(_DEADLINE))} UTC...")

    try:
        while any(t.is_alive() for t in threads):
            if STOP_EVENT.is_set():
                break
            time.sleep(2)
    except KeyboardInterrupt:
        STOP_EVENT.set()

    STOP_EVENT.set()
    log("SYS", "MAIN", "Đang dừng engine pool...")
    ENGINE_POOL.stop()
    log("SYS", "MAIN", "Tạm biệt.")

if __name__ == "__main__":
    main()
