#!/usr/bin/env python3
"""
77 - Xiangqi Bot (gamevh.net) - engine Pikafish
Tk: nguyen10
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

# ==================== TK (KHÔNG CẦN COOKIE) ====================
# Đăng nhập trực tiếp bằng username/pwd giống các bot nguyen1..nguyen6
CARO_USER_DIRECT = "arena11"
CARO_PWWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER19"), CARO_USER_DIRECT)
PWWD = _clean_env(os.environ.get("CARO_PWWD19"), CARO_PWWD_DIRECT)

# Cookie sẽ được tạo tự động sau khi đăng nhập (không hardcode nữa)
COOKIE = ""

_venv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'lib')
for _py_ver in ['python3.12', 'python3.13', 'python3.11']:
    _candidate = os.path.join(_venv_path, _py_ver, 'site-packages')
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'xiangqi'
PLACE_PATH = 'Lobby.xiangqi.0'

# Số nhánh engine phân tích song song. MultiPV=1: chỉ tin nước tốt nhất của engine
# (mạnh nhất & nhanh nhất). MultiPV>1: bật thêm lớp lọc x hướng TrendAnalyzer.
ENGINE_MULTIPV = 3

# Thoi gian TOI THIEU tu luc toi luot den khi gui nuoc di (giay).
# Engine tim ra nuoc thang/sat cuc se tra loi gan nhu tuc thi; neu di ngay
# thi nhip di nhanh bat thuong. Chi bu phan CON THIEU - engine da nghi lau
# hon MIN_MOVE_SECONDS roi thi di luon, khong cong them.
MIN_MOVE_SECONDS = 2.0

# Kick đối phương sau khi hết ván (giống nguyen1..nguyen6):
#   "when_lose" - bot THUA thì kick người thắng (đúng hành vi nguyen1..6, mặc định)
#   "when_win"  - bot THẮNG thì kick người thua
#   "always"    - kick đối phương ở mọi kết quả
#   "off"       - không kick
KICK_MODE = "when_lose"
KICK_DELAY = 5.0

BOT_BET_XU = 5000
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '99999'
BOT_TURN_DURATION = '99999'
BOT_ACC_DURATION = '0'
BOT_BLOCK_SOFTWARE = '0'

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

_IDENTITY_SYNCED = False

def generate_dotted_full_name():
    """Tạo tên tiếng Việt ngẫu nhiên + chèn 1 dấu chấm ngẫu nhiên (marker nhận diện đồng đội)."""
    name = random.choice(VN_TEN_DAU if random.choice([True, False]) else VN_TEN_KHONG_DAU)
    if len(name) >= 2:
        pos = random.randint(1, len(name) - 1)
        name = name[:pos] + "." + name[pos:]
    return name

def sync_profile_name(session):
    """Đổi FULL_NAME thành tên ngẫu nhiên có dấu chấm (ẩn danh + nhận diện đồng đội)."""

    try:
        edit_url = "https://gamevh.net/com/ftl/game/profile/update_profile.jsp"
        page = session.get(edit_url, timeout=15, allow_redirects=True)
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
        new_full_name = generate_dotted_full_name()
        data['FULL_NAME'] = new_full_name
        data['OLD_PWD'] = PWWD
        data['SAVE'] = '\uf046'

        session.post(
            action, timeout=15, data=data,
            headers={'Origin': 'https://gamevh.net',
                     'Referer': page.url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            allow_redirects=True)
        print(f"[PROFILE] 👤 Đổi tên hiển thị: '{old_full_name}' -> '{new_full_name}' (dấu chấm = đồng đội)")
    except Exception as e:
        print(f"[PROFILE] Lỗi cập nhật tên hiển thị: {e}")




def sync_random_avatar(session):
    """Đổi avatar ngẫu nhiên giống cơ chế nguyen* (có thể phát sinh phí x)."""
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
            url = ("https://gamevh.net/com/ftl/game/profile/"
                   f"avatar_by_category.jsp?excludeLayout=true&category_id={category}")
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id in seen:
                    continue
                seen.add(avatar_id)
                catalog.append(avatar_id)

        choices = [a for a in catalog if a != old_avatar]
        if not choices:
            print("[PROFILE] 🎭 Không tải được catalog avatar")
            return

        selected = random.choice(choices)
        update_url = ("https://gamevh.net/com/ftl/game/profile/update_avatar.jsp"
                      f"?pk={selected}&redirect=/")
        session.post(update_url, timeout=20,
                     headers={"Origin": "https://gamevh.net",
                              "Referer": "https://gamevh.net/com/ftl/game/profile/avatar.jsp"},
                     allow_redirects=True)

        after = session.get(profile_url, timeout=15)
        m2 = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', after.text, re.I)
        new_avatar = int(m2.group(1)) if m2 else None
        print(f"[PROFILE] 🎭 Avatar: builtin{old_avatar} -> builtin{new_avatar}")
    except Exception as e:
        print(f"[PROFILE] Lỗi đổi avatar: {e}")

def is_block_software_message(raw_bytes):

    """Kiểm tra xem dữ liệu gói tin bàn có cấu hình Chống Software (blockSoftware=1/true) hay không."""
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
    """Đọc danh sách các bàn đang do bot gia đình tạo hoặc đang ngồi."""
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
    """Đăng ký bàn do bot này đang tạo/ngồi vào tập tin dùng chung."""
    if not table_path: return
    try:
        data = get_active_bot_tables()
        data[table_path] = {"user": user, "timestamp": time.time(), "pid": os.getpid()}
        with open(ACTIVE_TABLES_FILE, 'w') as f:
            json.dump(data, f)
    except Exception: pass

def unregister_bot_table(table_path):
    """Hủy đăng ký bàn khi bot rời bàn."""
    if not table_path: return
    try:
        data = get_active_bot_tables()
        if table_path in data:
            data.pop(table_path, None)
            with open(ACTIVE_TABLES_FILE, 'w') as f:
                json.dump(data, f)
    except Exception: pass

def fetch_session_info():
    """Đăng nhập bằng USER/PWWD (không dùng cookie hardcode) và lấy token/nickname/playerId."""
    global COOKIE, TOKEN, CURRENT_PLAYER_NICKNAME, CURRENT_PLAYER_ID, PLACE_PATH, _IDENTITY_SYNCED
    try:
        session = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        session.headers.update({
            "User-Agent": ua,
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
        })

        # B1: mở trang login để lấy JSESSIONID
        session.get(LOGIN_URL, timeout=20)

        # B2: POST đăng nhập
        resp = session.post(
            LOGIN_URL, timeout=20,
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PWWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        if "login.jsp" in resp.url:
            print(f"[SESSION] Đăng nhập thất bại (sai tk/mk?): {resp.url}")
            return False

        # Đổi tên hiển thị (có dấu chấm) + avatar ngẫu nhiên: chỉ 1 lần mỗi lần chạy
        if not _IDENTITY_SYNCED:
            _IDENTITY_SYNCED = True
            sync_profile_name(session)
            sync_random_avatar(session)

        # B3: vào trang game để lấy token / nickname / playerId
        game_resp = session.get(GAME_URL, timeout=20)
        page_html = game_resp.text

        tm = re.search(r"var\s+token\s*=\s*(-?\d+)", page_html)
        if not tm:
            print("[SESSION] Không tìm thấy token")
            return False
        TOKEN = int(tm.group(1))

        nm = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if not nm:
            print("[SESSION] Không tìm thấy currentPlayerNickName")
            return False
        CURRENT_PLAYER_NICKNAME = nm.group(1).strip()

        pid = re.search(r"var\s+currentPlayerId\s*=\s*(\d+)", page_html)
        if pid:
            CURRENT_PLAYER_ID = int(pid.group(1))

        pm = re.search(r"var\s+placePath\s*=\s*[\"']([^\"']+)[\"']", page_html)
        if pm:
            PLACE_PATH = pm.group(1)

        # B4: dựng cookie từ session vừa đăng nhập
        COOKIE = "; ".join(f"{k}={v}" for k, v in session.cookies.items())

        if CURRENT_PLAYER_NICKNAME != USER:
            print(f"[SESSION] Cảnh báo: nickname server={CURRENT_PLAYER_NICKNAME!r} khác USER={USER!r}")
        print(f"[SESSION] Login OK | Token: {TOKEN} | NickName: {CURRENT_PLAYER_NICKNAME} | PlayerID: {CURRENT_PLAYER_ID}")
        return True
    except Exception as e:
        print(f"[SESSION] Lỗi đăng nhập: {e}")
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
        # Mốc để nạp engine: base_fen + các nước kể từ mốc. Giữ được danh sách nước
        # là engine mới phát hiện được LẶP NƯỚC (chiếu/đuổi liên tục = THUA theo
        # luật cờ tướng). Bản trước nạp FEN trần, engine mù về lặp nước -> tự sát.
        self.base_fen = self.INITIAL_FEN.split(' ')[0]
        self.base_side = 'w'
        self.moves_since_base = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
    # ---------------------------------------------------------------
    # TOẠ ĐỘ: số ô (position sid) của server là TUYỆT ĐỐI, giống nhau cho cả
    # hai bên. Client chính thức khai báo  positions[j*9+i] = {sid: j*9+i}
    # và setRedInBottom() chỉ XOAY HÌNH HIỂN THỊ chứ không đổi số ô.
    # Code cũ lật hàng (9 - row) khi bot cầm quân ĐEN -> sai ô -> server từ
    # chối mọi nước -> bot đứng hình rồi thua. Bỏ hẳn phép lật.
    #   sid = row * 9 + col (row 0 = phía ĐỎ);  engine: file='a'+col, rank=row
    # ---------------------------------------------------------------
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

        - Bình thường: giữ nguyên danh sách nước để engine biết thế cờ đã lặp lại
          bao nhiêu lần (cờ tướng: chiếu/đuổi liên tục là THUA, engine phải biết).
        - Khi đối phương BỎ LƯỢT: chuỗi luân phiên đứt, không diễn tả được bằng
          danh sách nước -> dựng thế hiện tại làm MỐC MỚI rồi ghi thẳng bên đi.
          Chỉ mất lịch sử lặp từ thời điểm đó, không sai bên đi.
        """
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
                print(f"[BOARD] ⚠️ Không áp được nước {mv}, giữ nguyên mốc cũ")
                return f"{self.base_fen} {self.base_side}", self.moves_since_base
            cur = nxt
        print(f"[BOARD] Đối phương bỏ lượt -> chốt mốc thế cờ mới, bên đi = {turn_side}")
        self.base_fen = cur
        self.base_side = turn_side
        self.moves_since_base = []
        return f"{cur} {turn_side}", []

    def set_base(self, board_fen, side='w'):
        """Chốt mốc thế cờ (gọi khi vào ván mới)."""
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
    """Bộ não phân tích dữ liệu RAM: Hỗ trợ quét kép Sát cục (Mate) và Điểm số x hướng (CP)"""
    def __init__(self):
        self.pv_ram_cache = {}
        self.info_regex = re.compile(r"info .* score cp (-?\d+) .* pv (.+)")
        self.mate_regex = re.compile(r"info .* score mate (-?\d+) .* pv (.+)")

    def clear(self):
        self.pv_ram_cache.clear()

    def parse_line(self, line_str):
        # 1. Quét thế trận sát cục (Mate) trước để tránh Bot đi vòng vo khi sắp thắng
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

        # 2. Nếu không có sát cục, tiến hành phân tích điểm cp thông thường
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

        # ƯU TIÊN TUYỆT ĐỐI: Có nhánh báo sát cục thắng (mate dương), xuất quân dứt điểm ngay!
        for move, data in self.pv_ram_cache.items():
            if data["mate_in"] is not None and data["mate_in"] > 0:
                print(f"[RAM-MATE] 🔥 Phát hiện nhánh sát cục tuyệt đối! Dứt điểm ngay: {move}")
                return move

        best_move = None
        avg_score = sum(d["current_score"] for d in self.pv_ram_cache.values()) / len(self.pv_ram_cache)
        is_negative = avg_score < 0

        if is_negative:
            # THẾ YẾU (ĐIỂM ÂM): Chọn nhánh có điểm âm thấp nhất (giảm thiểu suy thoái)
            max_recovery = -999999
            for move, data in self.pv_ram_cache.items():
                recovery_rate = data["current_score"] 
                if recovery_rate > max_recovery:
                    max_recovery = recovery_rate
                    best_move = move
            print(f"[RAM-LEARN] Đang lép vế ({int(avg_score)}). Ép chọn nước phòng thủ tốt nhất: {best_move}")
        else:
            # THẾ MẠNH (ĐIỂM DƯƠNG): Chọn nhánh có tốc độ bứt phá điểm cao nhất
            max_growth = -999999
            for move, data in self.pv_ram_cache.items():
                growth_rate = data["current_score"]
                if growth_rate > max_growth:
                    max_growth = growth_rate
                    best_move = move
            print(f"[RAM-LEARN] Đang ưu thế (+{int(avg_score)}). Ép chọn nước tăng điểm tốt nhất: {best_move}")

        return best_move

class PikafishBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.trend_analyzer = TrendAnalyzer()  
        self.engine = None
        self.ws = None
        self.connected = False
        self.logged_in = False
        self.in_game = False
        self._joining_table = False
        self._last_quick_play_time = 0
        self._QUICK_PLAY_INTERVAL = 3.0
        self.ROOM_LIST = ["0", "1", "2", "3"]
        self.player_names = {}
        # Lệch phòng khởi đầu theo index của bot để phân tán các bot ra các phòng khác nhau
        _bot_num = re.search(r"\d+", USER)
        _offset = int(_bot_num.group(0)) if _bot_num else 0
        self._search_room_idx = _offset % len(self.ROOM_LIST)
        self._search_bet_idx = 0
        self._quick_play_attempts = 0
        self._sit_alone_since = None
        self._table_created_by_me = False
        self.bet_amts = []
        self._resolved_bet_id = None
        self._bet_amts_loaded = False
        self.fixed_pawn_positions = set()
        self.last_action_timestamp = time.time()
        self.last_recv_timestamp = time.time()
        self._thinking = False           # đang tính nước -> không kích hoạt luồng thứ hai
        self._played_this_turn = False   # đã gửi nước cho lượt hiện tại chưa
        self._turn_started_at = 0.0      # lúc lượt chuyển sang bot
        self._last_play_sent_at = 0.0    # lúc bot gửi nước đi gần nhất
        self.turn_timeout = 0            # số giây còn lại cho lượt hiện tại (từ SET_TURN)
        self.slot_players = {}           # slot_id -> playerId (để biết kick ai)
        self._pending_kick_id = None
        self._table_path = None          # nhớ bàn đang ngồi để quay lại sau khi rớt mạng
        self._table_path_ts = 0.0
        self._reconnect_streak = 0       # số lần rớt liên tiếp -> giãn thời gian thử lại
        self._connected_since = 0.0
        self._enter_fail_at = 0.0        # lúc ENTER_PLACE bị từ chối (để dò bàn "ma")
        self._latest_bestmove = None
        self._mate_status = None
        self._mate_regex = re.compile(r"score mate (-?\d+)")
        self._score_regex = re.compile(r"depth (\d+).*score (cp|mate) (-?\d+)")
        self._last_score = "?"
        self._last_depth = "?"
        self._init_engine()

    def _init_engine(self):
        possible_paths = [
            os.path.expanduser("~/pikafish"),
            os.path.expanduser("~/Android/pikafish-armv8"),
            "/data/data/com.termux/files/home/pikafish",
            "./pikafish"
        ]
        pikafish_path = next((p for p in possible_paths if os.path.isfile(p) and os.access(p, os.X_OK)), None)
        if not pikafish_path:
            print("[ENGINE] ⚠️ Không tìm thấy pikafish, đang tải tự động...")
            pikafish_path = self._download_pikafish()
            if not pikafish_path:
                print("[ENGINE] ❌ Không thể tải pikafish. Bot sẽ không đánh được nước nào.")
                return
            return

        try:
            self._engine_proc = subprocess.Popen(
                [pikafish_path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
            )
            
            def consume_stderr(proc):
                try:
                    while proc.poll() is None:
                        if not proc.stderr.readline(): break
                except: pass
            threading.Thread(target=consume_stderr, args=(self._engine_proc,), daemon=True).start()

            def consume_stdout_and_filter(proc):
                try:
                    while proc.poll() is None:
                        line = proc.stdout.readline()
                        if not line: break
                        line_str = line.strip()
                        
                        self.trend_analyzer.parse_line(line_str)

                        _m = self._score_regex.search(line_str)
                        if _m:
                            self._last_depth = _m.group(1)
                            self._last_score = ("mate " + _m.group(3)) if _m.group(2) == "mate" \
                                               else f"{int(_m.group(3)):+d}"
                        
                        if "score mate" in line_str:
                            match = self._mate_regex.search(line_str)
                            if match:
                                val = int(match.group(1))
                                if val > 0: self._mate_status = f"WIN_IN_{val}"
                                elif val < 0: self._mate_status = f"LOSE_IN_{abs(val)}"
                        
                        if line_str.startswith("bestmove"):
                            self._latest_bestmove = line_str
                except: pass
            threading.Thread(target=consume_stdout_and_filter, args=(self._engine_proc,), daemon=True).start()

            self._fsf_cmd("uci")
            # Chừa ít nhất 1 nhân cho luồng WebSocket, nếu không pong/heartbeat bị trễ
            # và server cắt kết nối ngay giữa ván.
            _threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            self._fsf_cmd(f"setoption name Threads value {_threads}")
            self._fsf_cmd("setoption name Hash value 256")
            
            self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")
            
            time.sleep(1)
            
            nnue_path = os.path.expanduser("~/pikafish.nnue")
            if not os.path.isfile(nnue_path):
                nnue_path = os.path.join(os.path.dirname(pikafish_path), "pikafish.nnue")
            if os.path.isfile(nnue_path):
                self._fsf_cmd(f"setoption name EvalFile value {nnue_path}")
                self._fsf_cmd("setoption name UseNNUE value true")
            else:
                self._fsf_cmd("setoption name UseNNUE value false")
            self._fsf_cmd("isready")
            self.engine = True
            print(f"[ENGINE] ✅ Sẵn sàng | Threads={_threads} | MultiPV={ENGINE_MULTIPV}"
                  + ("" if ENGINE_MULTIPV > 1 else " (dùng thẳng bestmove của engine)"))
        except Exception as e:
            print(f"[ENGINE] ❌ Lỗi khởi tạo: {e}")

    def _download_pikafish(self):
        """Tự động tải Pikafish 2026-01-02 và NNUE."""
        try:
            import platform
            import urllib.request
            
            PIKA_URL = "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-01-02/Pikafish.2026-01-02.7z"
            NNUE_URL = "https://github.com/official-pikafish/Networks/releases/download/master-net/pikafish.nnue"
            
            home = os.path.expanduser("~")
            download_path = os.path.join(home, "pikafish")
            nnue_path = os.path.join(home, "pikafish.nnue")
            
            # Tải NNUE trước (nhẹ hơn, ~90MB)
            if not os.path.isfile(nnue_path):
                print(f"[ENGINE] 📥 Tải NNUE network...")
                try:
                    urllib.request.urlretrieve(NNUE_URL, nnue_path)
                    print(f"[ENGINE] ✅ NNUE downloaded: {os.path.getsize(nnue_path) // 1024 // 1024}MB")
                except Exception as e:
                    print(f"[ENGINE] ⚠️ NNUE download failed: {e}")
            
            # Tải Pikafish binary
            if not os.path.isfile(download_path):
                print(f"[ENGINE] 📥 Tải Pikafish 2026-01-02...")
                
                system = platform.system().lower()
                machine = platform.machine().lower()
                
                if "linux" in system and ("x86_64" in machine or "amd64" in machine):
                    import subprocess
                    
                    # Tải file 7z (~55MB)
                    archive_path = os.path.join(home, "pikafish.7z")
                    try:
                        subprocess.run(["wget", "-q", "--show-progress", "-O", archive_path, PIKA_URL], check=True, timeout=120)
                    except:
                        try:
                            subprocess.run(["curl", "-L", "-o", archive_path, PIKA_URL], check=True, timeout=120)
                        except:
                            print("[ENGINE] ❌ Cannot download (wget/curl failed)")
                            return None
                    
                    if not os.path.isfile(archive_path):
                        print("[ENGINE] ❌ Download failed")
                        return None
                    
                    # Giải nén 7z
                    print(f"[ENGINE] 📦 Giải nén...")
                    try:
                        subprocess.run(["7z", "x", archive_path, f"-o{home}", "-y"], check=True, timeout=60)
                    except subprocess.CalledProcessError:
                        print("[ENGINE] ❌ 7z extraction failed. Installing p7zip...")
                        try:
                            subprocess.run(["apt-get", "update", "-qq"], check=False)
                            subprocess.run(["apt-get", "install", "-y", "p7zip-full"], check=False)
                            subprocess.run(["7z", "x", archive_path, f"-o{home}", "-y"], check=True, timeout=60)
                        except:
                            print("[ENGINE] ❌ Cannot extract 7z")
                            return None
                    
                    # Xóa archive
                    try:
                        os.remove(archive_path)
                    except:
                        pass
                    
                    # Tìm binary trong thư mục giải nén
                    print(f"[ENGINE] 🔍 Tìm binary...")
                    for root, dirs, files in os.walk(home):
                        for f in files:
                            if "pikafish" in f.lower() and ("x86_64" in f.lower() or "linux" in f.lower()):
                                src = os.path.join(root, f)
                                if os.path.isfile(src) and os.access(src, os.X_OK):
                                    os.rename(src, download_path)
                                    print(f"[ENGINE] ✅ Found: {f}")
                                    break
                        if os.path.isfile(download_path):
                            break
                    
                    # Nếu không tìm thấy, thử tìm bất kỳ file executable nào
                    if not os.path.isfile(download_path):
                        for root, dirs, files in os.walk(home):
                            for f in files:
                                if "pikafish" in f.lower() and not f.endswith(".nnue") and not f.endswith(".7z"):
                                    src = os.path.join(root, f)
                                    if os.path.isfile(src):
                                        try:
                                            os.chmod(src, 0o755)
                                            if os.access(src, os.X_OK):
                                                os.rename(src, download_path)
                                                print(f"[ENGINE] ✅ Found: {f}")
                                                break
                                        except:
                                            pass
                            if os.path.isfile(download_path):
                                break
                
                elif "darwin" in system:
                    print("[ENGINE] ⚠️ macOS auto-download not implemented yet")
                    return None
                else:
                    print(f"[ENGINE] ⚠️ Platform {system}/{machine} not supported for auto-download")
                    return None
            
            if os.path.isfile(download_path) and os.access(download_path, os.X_OK):
                print(f"[ENGINE] ✅ Pikafish ready: {download_path}")
                return download_path
            else:
                print(f"[ENGINE] ❌ Download failed or binary not executable")
                return None
                
        except Exception as e:
            print(f"[ENGINE] ❌ Download error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _fsf_cmd(self, text):
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._engine_proc.stdin.write(text + "\n")
            self._engine_proc.stdin.flush()

    def get_best_move(self, fen, moves, fixed_positions=None):
        try:
            if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None: return None
            self.trend_analyzer.clear() 
            
            if fixed_positions: return self._get_move_avoiding_fixed(fen, moves, fixed_positions)
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            
            # ÉP THỜI GIAN: Cho Engine chạy đúng 1.2 giây để lấy đủ dữ liệu chuỗi PV
            self._fsf_cmd("go movetime 3200")
            # movetime 3200ms mà timeout 3s -> luôn bị "stop" trước khi engine trả lời
            return self._read_bestmove(timeout=4.2)
        except Exception as e: print(f"[ENGINE] Lỗi tính toán: {e}")
        return None

    def _read_bestmove(self, timeout=3):
        _go_start = time.time()
        self._latest_bestmove = None 
        self._mate_status = None     
        
        while True:
            if self._engine_proc.poll() is not None: return None
            if self._latest_bestmove:
                return self._latest_bestmove
            
            if time.time() - _go_start > timeout:
                self._fsf_cmd("stop")
                time.sleep(0.1)
                if self._latest_bestmove:
                    return self._latest_bestmove
                break
            time.sleep(0.02) # Phản xạ luồng đọc siêu tốc
        return None

    def _get_move_avoiding_fixed(self, fen, moves, fixed_positions):
        """Tìm nước đi không dính chốt cố định.

        Chiến lược:
        1. Tạm bật MultiPV=3 để engine phân tích nhiều nước thay thế
        2. Dùng TrendAnalyzer chọn nước tốt nhất không dính chốt
        3. Nếu vẫn dính -> retry (engine sẽ tránh nước cũ)
        """
        # Tạm bật MultiPV=3 để có nước thay thế
        original_multipv = ENGINE_MULTIPV
        if ENGINE_MULTIPV < 3:
            self._fsf_cmd("setoption name MultiPV value 3")

        excluded_moves = set()
        result = None

        for attempt in range(5):
            self.trend_analyzer.clear()

            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)

            self._latest_bestmove = None
            self._mate_status = None
            self._fsf_cmd("go movetime 2500")

            _wait_start = time.time()
            while time.time() - _wait_start < 3.5:
                if self._latest_bestmove: break
                time.sleep(0.05)

            self._fsf_cmd("stop")
            time.sleep(0.15)

            if not self._latest_bestmove or self._latest_bestmove in ("(none)", "0000"):
                result = self._latest_bestmove
                break

            parts = self._latest_bestmove.split()
            best_move = parts[1] if len(parts) >= 2 else None
            if not best_move:
                result = self._latest_bestmove
                break

            # Nước tốt nhất không dính chốt -> dùng luôn
            if not self._move_hits_fixed_pawn(best_move, fixed_positions):
                result = self._latest_bestmove
                break

            # Nước dính chốt -> thử TrendAnalyzer chọn nước thay thế
            print(f"[ENGINE] ⚠️ Bestmove {best_move} dính chốt cố định (lần {attempt + 1})")
            alt_move = self.trend_analyzer.select_best_trend_move()
            if alt_move and not self._move_hits_fixed_pawn(alt_move, fixed_positions):
                print(f"[ENGINE] ✅ TrendAnalyzer chọn nước thay thế: {alt_move}")
                result = f"bestmove {alt_move}"
                break

            # TrendAnalyzer cũng dính chốt -> loại nước này, tính lại
            print(f"[ENGINE] TrendAnalyzer cũng dính chốt, tính lại...")
            excluded_moves.add(best_move)

        # Khôi phục MultiPV gốc
        if original_multipv < 3:
            self._fsf_cmd(f"setoption name MultiPV value {original_multipv}")

        if result is None:
            print("[ENGINE] ⚠️ Hết 5 lần tránh chốt, trả về nước cuối")
            result = self._latest_bestmove

        return result

    def _move_hits_fixed_pawn(self, move_str, fixed_positions):
        """Kiểm tra nước đi có bắt đầu từ vị trí chốt cố định không."""
        if not fixed_positions or len(move_str) < 4:
            return False
        try:
            src_file = ord(move_str[0]) - ord('a')
            src_rank = int(move_str[1])
            src_pos = src_rank * 9 + src_file
            return src_pos in fixed_positions
        except (ValueError, IndexError):
            return False

    def connect(self):
        import websocket
        self.connected = False
        self.ws = websocket.WebSocketApp(
            WS_URL, cookie=COOKIE,
            on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
            header={"Origin": "https://gamevh.net"}
        )
        # ping_timeout=10 cũ khiến engine ăn hết CPU 3.2s/nước -> pong trả chậm -> tự ngắt
        # giữa ván. Bỏ pong-timeout, thay bằng kiểm tra liveness theo dữ liệu nhận được.
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
        print(f"[WS] ❌ Lỗi kết nối: {type(error).__name__}: {error}")

    def _on_close(self, ws, code, msg):
        if self.board.is_playing:
            print(f"[WS] ⚠️ MẤT KẾT NỐI GIỮA VÁN (code={code}, msg={msg}) -> mất bàn, sẽ phải tạo bàn mới")
        else:
            print(f"[WS] Đóng kết nối (code={code}, msg={msg})")
        # Nếu vừa kết nối đã đứt ngay (<60s) thì coi là rớt liên tiếp -> cần giãn nhịp,
        # tránh đăng nhập dồn dập tạo ra hàng loạt phiên/bàn rác trên server.
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
        self.board.reset()

    def send_message(self, cmd, data=b''):
        if self.ws and self.connected:
            try: self.ws.send(self.conn.pack(cmd, data), opcode=0x2)
            except: pass

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

    def send_list_bet_amt(self): self.send_message("LIST_BET_AMT")

    
    def get_1k_to_5k_bet_objs(self):
        """Trả về danh sách cược trong khoảng 500-10000xu, xáo trộn ngẫu nhiên."""
        if not self.bet_amts:
            return []
        valid = [ba for ba in self.bet_amts if 5000 <= ba["value"] <= 10000]
        if valid:
            random.shuffle(valid)
            return valid
        return [self.bet_amts[0]] if self.bet_amts else []

    def is_family_bot(self, name):
        """Nhận diện bot đồng đội: tên hiển thị chứa dấu chấm '.' (do chính bot đặt)."""
        if not name or name.strip().lower() == CURRENT_PLAYER_NICKNAME.lower():
            return False
        return "." in name

    def leave_table(self):
        """Rời bàn hiện tại và quay về sảnh để tiếp tục dò bàn 500-10k."""
        if self.board.is_playing:
            print("[TABLE] ⚠️ Đang trong ván đấu -> Khóa không rời bàn cho đến khi GAMEOVER!")
            return
        print("[TABLE] 🚪 Rời bàn chơi, quay lại sảnh tiếp tục dò tìm bàn 500-10k...")
        if getattr(self, '_table_path', None):
            unregister_bot_table(self._table_path)
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self._table_created_by_me = False
        self._sit_alone_since = None
        self.slot_players.clear()
        self.board.reset()
        self._quick_play_attempts = 0
        self._search_room_idx = 0
        self._search_bet_idx = 0
        self._enter_fail_at = 0.0
        self.send_enter_place(PLACE_PATH)

    def _lower_bet_level(self):
        """Giảm mức cược xuống 1 bậc rồi tạo bàn mới. Nếu đã ở mức thấp nhất thì giữ nguyên."""
        global BOT_BET_XU
        if not self.bet_amts:
            print("[BET] ⚠️ Chưa có danh sách mức cược, gửi yêu cầu lấy lại...")
            self._bet_amts_loaded = False
            self.send_list_bet_amt()
            return
        current = BOT_BET_XU
        all_values = sorted(set(ba['value'] for ba in self.bet_amts if ba['value'] > 0))
        lower_options = [v for v in all_values if v < current]
        if lower_options:
            new_bet = max(lower_options)
            print(f"[BET] 📉 Giảm mức cược: {current} -> {new_bet}")
            BOT_BET_XU = new_bet
            self._resolved_bet_id = self.resolve_bet_amt_id()
        else:
            print(f"[BET] ⚠️ Đã ở mức cược thấp nhất ({current}), giữ nguyên.")
            self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = False
        self.send_list_bet_amt()

    def resolve_bet_amt_id(self):
        if not self.bet_amts: return None
        in_range = [ba for ba in self.bet_amts if 5000 <= ba["value"] <= 10000]
        if in_range:
            return random.choice(in_range)['id']
        return 0

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
        self._last_play_sent_at = time.time()
        self._played_this_turn = True
        data = bytearray()
        data.extend(self.conn.pack_byte(source_pos))
        data.extend(self.conn.pack_byte(target_pos))
        self.send_message("PLAY", bytes(data))

    def opponent_player_id(self):
        """playerId của người ngồi ghế đối diện (nếu biết)."""
        for sid, pid in self.slot_players.items():
            if pid and pid != CURRENT_PLAYER_ID and sid != self.board.my_slot_id:
                return pid
        return None

    def send_kick_player(self, player_id):
        """Đuổi người chơi khỏi bàn - opcode 410 + int64 playerId (giống nguyen1..6)."""
        self._pending_kick_id = player_id
        data = bytearray()
        data.extend(struct.pack('>q', int(player_id)))
        print(f"[KICK] Gửi KICK_PLAYER playerId={player_id}")
        # Dùng ĐÚNG opcode số 410 như web client và nguyen1..6 (không gửi tên ASCII),
        # vì đây là lệnh hiếm, gửi sai dạng là server bỏ qua im lặng.
        self.send_message(410, bytes(data))

    def send_ready(self, is_ready=1):
        if self.board.is_playing: return
        print("[GAME] ⏳ Gửi trạng thái READY...")
        data = bytearray()
        data.extend(self.conn.pack_byte(is_ready))
        self.send_message("SET_READY", bytes(data))

    def _handle_binary_message(self, data):
        try:
            msg = InboundMessage(data)
            cmd = msg.command
            if cmd == "PING": self.send_message("PONG")
            elif cmd == "LOGIN": self._handle_login_response(msg)
            elif cmd == "ENTER_PLACE": self._handle_enter_place_response(msg)
            elif cmd == "QUICK_PLAY": self._handle_quick_play_response(msg)
            elif cmd == "LIST_BET_AMT": self._handle_list_bet_amt_response(msg)
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
                try: print(f"[SERVER] ALERT: {msg.read_string()}")
                except Exception: pass
        except Exception as e: print(f"[RECV ERROR] {e}")

    def _handle_login_response(self, msg):
        if msg.read_byte() == 0:
            self.logged_in = True
            path = msg.read_string()
            if path == 'REFRESH':
                fetch_session_info()
                self._send_login()
                return
            self.send_enter_place()

    def _handle_enter_place_response(self, msg):
        status = msg.read_byte()
        if status != 0:
            if self._joining_table:
                print(f"[TABLE] ENTER_PLACE trả status={status} -> coi như đã ở trong bàn, bấm Sẵn sàng")
                self._joining_table = False
                self.in_game = True
                self._enter_fail_at = time.time()
                threading.Thread(
                    target=lambda: (time.sleep(3.0), self.send_ready(1)), daemon=True).start()
            return

        if self._joining_table:
            if is_block_software_message(msg.data):
                print("[GAME] 🛡️ Bàn chơi có chế độ Chống Software (blockSoftware=1). Vẫn sẵn sàng thi đấu!")

            self._joining_table = False
            self.in_game = True
            self._enter_fail_at = 0.0
            self.last_action_timestamp = time.time()
            def delay_initial_ready():
                time.sleep(3.0)  
                self.send_ready(1)
            threading.Thread(target=delay_initial_ready, daemon=True).start()
        elif not self.in_game:
            if self._table_path and time.time() - self._table_path_ts < 180:
                print(f"[TABLE] Thử ngồi lại bàn cũ: {self._table_path}")
                self.in_game = True
                self._joining_table = True
                path = self._table_path
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self.send_enter_place(path=path, mode=1)),
                    daemon=True).start()
                return
            self._bet_amts_loaded = False
            self._resolved_bet_id = None
            self.send_list_bet_amt()
        else:
            # Gói 401 ngẫu nhiên server đẩy về khi bot ĐANG NỒI TRONG BÀN (người khác ra/vào).
            # Bỏ qua hoàn toàn, KHÔNG ĐỤNG vào self.in_game và KHÔNG reset board!
            pass

    def _handle_quick_play_response(self, msg):
        status = msg.read_byte()
        if status == 0:
            table_path = msg.read_ascii()
            active_tables = get_active_bot_tables()
            if table_path in active_tables:
                owner = active_tables[table_path].get("user", "đồng đội")
                if owner.lower() != USER.lower():
                    print(f"[AVOID] 🛑 Server gợi ý bàn '{table_path}' nhưng đây là bàn của đồng đội {owner}. HỦY BỎ không vào!")
                    self.in_game = False
                    self._joining_table = False
                    return

            self.in_game = True  
            self._joining_table = True
            self._quick_play_attempts = 0
            self._search_room_idx = 0
            self._search_bet_idx = 0
            self._table_created_by_me = False
            self._sit_alone_since = time.time()
            self._table_path = table_path; self._table_path_ts = time.time()
            register_bot_table(table_path, USER)

            print(f"[SEARCH] ✅ Tìm thấy bàn người dùng thực: {table_path}. Đang vào bàn...")
            def async_join():
                time.sleep(0.5)
                self.send_enter_place(path=table_path, mode=1)
            threading.Thread(target=async_join, daemon=True).start()
        else:
            print(f"[SEARCH] ℹ️ Phòng/cược vừa tìm không có bàn trống (status={status}). Tiếp tục chuyển phòng...")
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
            self.in_game = True  
            self._joining_table = True
            self._quick_play_attempts = 0
            self._search_room_idx = 0
            self._search_bet_idx = 0
            self._table_created_by_me = True
            self._sit_alone_since = time.time()
            self._table_path = table_path; self._table_path_ts = time.time()
            register_bot_table(table_path, USER)
            print(f"[CREATE] 🎉 Tạo bàn thành công: {table_path}. Đang vào bàn chờ người chơi (30s)...")
            def async_join():
                time.sleep(0.5)
                self.send_enter_place(path=table_path, mode=1)
            threading.Thread(target=async_join, daemon=True).start()
        else:
            print(f"[CREATE] ❌ Tạo bàn thất bại (status={status}). Bắt đầu dò lại...")
            self._joining_table = False
            self._quick_play_attempts = 0

    def _handle_player_entered(self, msg):
        try:
            place_level = msg.read_byte()
            pid = msg.read_long()
            name = msg.read_string()
            if pid > 0 and pid != CURRENT_PLAYER_ID:
                self.player_names[pid] = name
                print(f"[PLAYER] 👤 Người chơi '{name}' (id={pid}) vào bàn/phòng (level={place_level})")
                if not self.board.is_playing and self.is_family_bot(name) and self.opponent_player_id() == pid:
                    print(f"[AVOID] ⚠️ Phát hiện đồng đội '{name}' ở ghế đối diện! Rời bàn ngay + giảm cược...")
                    self.leave_table()
                    self._lower_bet_level()
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
            if player_id == CURRENT_PLAYER_ID: 
                self.board.my_slot_id = slot_id
            else:
                if player_id > 0:
                    name = self.player_names.get(player_id, "")
                    print(f"[TABLE] 👤 Ghế đối diện (slot={slot_id}): playerId={player_id}{f', name={name}' if name else ''}")
                    if not self.board.is_playing and self.is_family_bot(name):
                        print(f"[AVOID] ⚠️ Đối thủ '{name}' là bot đồng đội! Rời bàn + giảm cược...")
                        self.leave_table()
                        self._lower_bet_level()
                        return
                    self._sit_alone_since = None
                    if not self.board.is_playing:
                        def delay_ready_on_player():
                            time.sleep(3.0)  
                            self.send_ready(1)
                        threading.Thread(target=delay_ready_on_player, daemon=True).start()
                else:
                    if not self.board.is_playing and self.opponent_player_id() is None:
                        print("[TABLE] 🚪 Không còn đối thủ ở ghế chơi. Bắt đầu đếm ngược 30s chờ người chơi...")
                        self._sit_alone_since = time.time()
        except: pass

    def _handle_start_match(self, msg):
        print(f"[GAME] 🎮 Trận chiến bắt đầu!")
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
                print(f"[GAME] 🛡️ Bàn đấu có {len(self.fixed_pawn_positions)} chốt bị liệt/khóa!")

            self.board.set_base(self._build_fen_from_pieces(board_pieces), 'w')
            if my_slot_id == first_turn_slot_id:
                self.board.is_my_turn = True
                self._turn_started_at = time.time()
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e: print(f"[START_MATCH ERROR] {e}")

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
        except Exception as e: print(f"[MOVE ERROR] {e}")

    def _handle_play_response(self, msg):
        if msg.read_byte() != 0:
            # Server từ chối nước đi -> tự tính lại (không chờ SET_TURN mới).
            # KHÔNG được pop lịch sử ở đây! Nước bị từ chối chưa bao giờ được ghi
            # (chỉ ghi khi nhận gói MOVE), nên pop sẽ xoá nhầm nước THẬT của đối
            # thủ -> bàn cờ trong đầu bot lệch với bàn thật -> đi những nước như
            # tự sát ở giữa và cuối trận.
            self.board.is_my_turn = True
            self._played_this_turn = False
            self._play_reject_count = getattr(self, '_play_reject_count', 0) + 1
            print(f"[PLAY] ⚠️ Server từ chối nước đi (lần {self._play_reject_count}) -> tính lại")
            if self._play_reject_count <= 3:
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self._make_auto_move()), daemon=True).start()
        else:
            self._play_reject_count = 0

    def _handle_set_turn(self, msg):
        """SET_TURN theo đúng client gamevh:
             byte slotId, short turnTimeout, short playerRemainDuration
           slotId == -2 là bộ đếm ngược "Chuẩn bị/Bắt đầu", KHÔNG phải lượt đi.
        """
        try:
            slot_id = msg.read_byte()
            try:
                turn_timeout = msg.read_short()
            except Exception:
                turn_timeout = 0
            if slot_id == -2:
                return                      # chỉ là đếm ngược trước ván
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
            # ĐỐI PHƯƠNG BỎ LƯỢT: server gửi lại SET_TURN cho CÙNG một lượt, không
            # kèm MOVE nào. Vì vậy phải tính nước MỖI KHI nhận SET_TURN trỏ vào bot
            # (chỉ trừ lúc đang tính dở) - kể cả khi lịch sử nước đi không đổi.
            # Nước tính lại luôn đúng màu của bot nhờ get_current_fen() chốt bên đi
            # theo lượt thật, nên không còn cảnh ra nước của đối phương như trước.
            # Nếu thực sự không phải lượt bot, server chỉ việc từ chối - vô hại.
            if not self._thinking:
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e:
            print(f"[SET_TURN ERROR] {e}")

    def _handle_kick_response(self, msg):
        try:
            status = msg.read_byte()
            content = msg.read_string()
        except Exception:
            status, content = None, ""
        if self._pending_kick_id is not None:
            pid = self._pending_kick_id; self._pending_kick_id = None
            if status == 0: print(f"[KICK] ✅ Đã đuổi playerId={pid} khỏi bàn. {content}")
            else:           print(f"[KICK] ❌ Đuổi playerId={pid} thất bại (status={status}): {content}")
            return
        # Không phải phản hồi của mình -> chính bot bị đuổi
        print(f"[KICK] ⚠️ Bot bị đuổi khỏi bàn: {content}")
        self.in_game = False
        self._joining_table = False
        self._table_path = None
        self.board.reset()

    def _handle_gameover(self, msg):
        # --- Đọc kết quả từng ghế: count(u8) + [slot(i8), result(i8), int64] ---
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
        if bot_won:    print("[GAME] 🏁 Trận đấu kết thúc. >>> THẮNG <<<")
        elif bot_lost: print("[GAME] 🏁 Trận đấu kết thúc. >>> THUA <<<")
        elif my_result is None: print("[GAME] 🏁 Trận đấu kết thúc.")
        else: print("[GAME] 🏁 Trận đấu kết thúc. >>> HOÀ <<<")

        # --- Có kick đối phương không? ---
        should_kick = (KICK_MODE == "always"
                       or (KICK_MODE == "when_lose" and bot_lost)
                       or (KICK_MODE == "when_win" and bot_won))
        victim = None
        if should_kick:
            # Ưu tiên ghế được GAMEOVER đánh dấu, fallback sang ghế đối diện đang biết.
            want = (1, 11) if KICK_MODE == "when_lose" else (2, 4, 12)
            target_sid = next((sid for sid, res in results.items()
                               if sid != self.board.my_slot_id and res in want), None)
            victim = self.slot_players.get(target_sid) if target_sid is not None else None
            if not victim:
                victim = self.opponent_player_id()
            if not victim:
                print("[KICK] Không xác định được playerId đối phương -> bỏ qua")

        self.fixed_pawn_positions.clear()
        self.board.reset()
        self.board.is_playing = False
        self.board.is_my_turn = False
        self.in_game = True  
        self._joining_table = False
        self.last_action_timestamp = time.time()
        
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._fsf_cmd("ucinewgame")
            self._fsf_cmd("isready")

        def after_gameover():
            is_guest = not getattr(self, '_table_created_by_me', False)
            if bot_lost:
                if victim and not is_guest:
                    time.sleep(KICK_DELAY)
                    if self.connected and not self.board.is_playing:
                        self.send_kick_player(victim)
                        time.sleep(2.0)
                elif is_guest:
                    print("[GAME] 👤 Khách vào bàn -> không có quyền kick, rời bàn ngay...")
                print("[GAME] 🔄 Thua trận -> Rời bàn + giảm mức cược -> Tìm bàn mới...")
                time.sleep(1.0)
                self.leave_table()
                self._lower_bet_level()
            else:
                print("[GAME] ✅ Thắng/Hoà -> Ở lại bàn, sẵn sàng ván tiếp...")
                time.sleep(3.0)
                self.send_ready(1)
        threading.Thread(target=after_gameover, daemon=True).start()

    def _make_auto_move(self):
        if not self.board.is_my_turn or not self.board.is_playing: return
        if self._thinking: return
        self._thinking = True
        try:
            self._do_auto_move()
        finally:
            self._thinking = False

    def _do_auto_move(self):
        
        if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None:
            self._init_engine()
            if not self.engine: return

        fen, moves = self.board.get_current_fen()
        fixed = self.fixed_pawn_positions if self.fixed_pawn_positions else None
        
        raw_bestmove_line = self.get_best_move(fen, moves, fixed_positions=fixed)
        if not raw_bestmove_line: return

        parts = raw_bestmove_line.split()
        if len(parts) < 2: return
        best_move = parts[1]

        # ÁP DỤNG BỘ LỌC TỐI ƯU X HƯỚNG/SÁT CỤC TỪ RAM
        # CHỈ khi MultiPV > 1. Với MultiPV=1, pv_ram_cache chỉ chứa nước đầu của từng
        # vòng lặp depth (nông -> sâu); chọn theo điểm cao nhất có thể lôi ra một nước
        # ở depth nông và ghi đè lên bestmove cuối cùng của engine => đi yếu hơn.
        if ENGINE_MULTIPV > 1:
            trend_move = self.trend_analyzer.select_best_trend_move()
            if trend_move and best_move not in ["(none)", "0000"]:
                print(f"[RAM-LEARN] 🧠 Thay thế '{best_move}' bằng nước đi tối ưu: '{trend_move}'")
                best_move = trend_move

        # XỬ LÝ KỊCH BẢN KHI HẾT NƯỚC ĐI CỜ TÀN
        if best_move in ["(none)", "0000"]:
            print("\n[HỆ THỐNG TÀN CUỘC] ⚠️ Pikafish báo: bestmove (none) - Hết nước hợp lệ.")
            self.board.is_my_turn = False
            return

        # Gửi nước đi hợp lệ lên hệ thống GameVH
        if best_move:
            try:
                source_pos, target_pos = self.board.engine_move_to_pos(best_move)
                # Cho du MIN_MOVE_SECONDS ke tu luc toi luot (chi bu phan con
                # thieu). _turn_started_at = 0 nghia la chua ghi nhan duoc moc
                # -> coi nhu vua toi luot va cho tron.
                _turn_start = getattr(self, '_turn_started_at', 0.0) or time.time()
                _remain = MIN_MOVE_SECONDS - (time.time() - _turn_start)
                if _remain > 0:
                    time.sleep(_remain)
                if self.board.is_my_turn and self.board.is_playing:
                    print(f"-> Hành động: Xuất quân: {best_move} "
                          f"[điểm {self._last_score} depth {self._last_depth}]")
                    self.send_play(source_pos, target_pos)
            except Exception as e: print(f"[BOT ERROR] Dịch tọa độ lỗi: {e}")

    def _decode_piece_id(self, encoded_id):
        color = 'r'
        if encoded_id < 0: encoded_id = -encoded_id; color = 'b'
        return f"{color}{encoded_id >> 3}{'' if (encoded_id & 7) == 0 else (encoded_id & 7)}"

    def start_keep_alive(self):
        def keep_alive_loop():
            while self.connected:
                time.sleep(10)
                if self.connected: self.send_message("PING")
        threading.Thread(target=keep_alive_loop, daemon=True).start()

    def run(self):
        print("[BOT] Khởi chạy hệ thống giám sát tự động...")

        # ===== CHUYỂN X 50% NGAY KHI KHỞI ĐỘNG (TRƯỚC KHI VÀO BÀN) =====
        print("[TRANSFER] 🔄 Chuyển 50% x cho xxxx trước khi vào bàn...")
        try:
            from transfer_xu_bot import transfer_xu_sync
            if transfer_xu_sync(USER, PWWD, dest_id=69284652, percent=20):
                print("[TRANSFER] ✅ Chuyển x thành công!")
            else:
                print("[TRANSFER] ⚠️ Chuyển x thất bại, tiếp tục chạy bot...")
        except ImportError as ie:
            print(f"[TRANSFER] ❌ Không tìm thấy transfer_xu_bot: {ie}")
        except Exception as e:
            print(f"[TRANSFER] ❌ Lỗi chuyển x: {e}")
        print("[TRANSFER] ✅ Hoàn tất, bắt đầu vào bàn chơi...")
        # ===== END CHUYỂN X =====
        while True:
            try:
                now_ts = time.time()
                # (a) Không nhận được BẤT KỲ gói nào trong 120s -> kết nối đã chết thật
                if self.connected and now_ts - self.last_recv_timestamp > 120:
                    print("[WS] Không nhận dữ liệu 120s -> coi như chết, kết nối lại")
                    if self.ws: self.ws.close()
                    time.sleep(2)
                # (b) Đang trong ván mà 300s không có nước đi nào -> mới cắt (trước là 180s,
                #     dễ cắt nhầm khi đối thủ suy nghĩ lâu và làm mất luôn cái bàn)
                elif self.connected and self.board.is_playing:
                    if now_ts - self.last_action_timestamp > 300:
                        print("[WS] Ván treo 300s không có nước đi -> kết nối lại")
                        if self.ws: self.ws.close()
                        time.sleep(2)

                if not self.connected:
                    if self._reconnect_streak >= 3:
                        print("[BOT] ⚠️ Bị ngắt kết nối liên tục ngay sau khi đăng nhập.")
                        print(f"[BOT] ⚠️ Nhiều khả năng tk {USER} đang được ĐĂNG NHẬP Ở NƠI KHÁC "
                              "(GitHub Actions, máy khác, điện thoại...). Server chỉ cho 1 phiên nên hai bên đá nhau.")
                    if self._reconnect_streak > 0:
                        delay = min(60, 5 * (2 ** min(self._reconnect_streak - 1, 4)))
                        print(f"[WS] Rớt liên tiếp lần {self._reconnect_streak} -> chờ {delay}s rồi đăng nhập lại")
                        time.sleep(delay)
                    if not fetch_session_info():
                        time.sleep(5); continue
                    self.logged_in = False
                    self.in_game = False
                    self._joining_table = False
                    self._bet_amts_loaded = False
                    self._resolved_bet_id = None
                    self.bet_amts = []
                    self.fixed_pawn_positions = set()
                    self.board.reset()
                    if not self.connect():
                        time.sleep(5); continue
                    self.start_keep_alive()
                    time.sleep(2)

                # Tới lượt bot nhưng 12s vẫn chưa gửi được nước nào (đối phương bị bỏ
                # lượt, gói MOVE tới trễ, nước bị từ chối...) -> tự tính lại, tránh
                # đứng im cho tới khi hết giờ rồi thua oan.
                if (self.board.is_playing and self.board.is_my_turn and not self._thinking
                        and self._turn_started_at
                        and time.time() - self._turn_started_at > 12
                        and not self._played_this_turn):
                    print("[TURN] Tới lượt nhưng 12s chưa đi được -> tính lại")
                    self._turn_started_at = time.time()
                    threading.Thread(target=self._make_auto_move, daemon=True).start()

                # KHÓA CHẶT: Khi đang trong ván đấu (is_playing), tuyệt đối KHÔNG gửi lệnh tìm/tạo/rời bàn!
                if self.board.is_playing:
                    self._sit_alone_since = None
                else:
                    # Chỉ đếm 30s khi ĐANG Ở TRONG BÀN nhưng KHÔNG TRONG VÁN ĐẤU
                    if self.in_game and not self._joining_table:
                        opp_id = self.opponent_player_id()
                        if opp_id is None:
                            if self._sit_alone_since is None:
                                self._sit_alone_since = time.time()
                            else:
                                elapsed = time.time() - self._sit_alone_since
                                if elapsed >= 30.0:
                                    print(f"[TABLE] ⏱️ Đã chờ {int(elapsed)}s không có người chơi -> Rời bàn tiếp tục tìm bàn 500-10k")
                                    self.leave_table()
                        else:
                            self._sit_alone_since = None

                # Sau ENTER_PLACE lỗi: nếu 60s trôi qua mà không vào ván nào thì
                # có lẽ bot KHÔNG thực sự ở trong bàn -> nhả cờ để rời bàn, tìm lại.
                if (self._enter_fail_at and self.in_game and not self.board.is_playing
                        and time.time() - self._enter_fail_at > 60):
                    print("[TABLE] Chờ 60s không vào được ván nào -> bỏ bàn cũ, tìm bàn mới")
                    self._enter_fail_at = 0.0
                    self.leave_table()

                if (self.connected and self.logged_in and not self.in_game
                        and not self._joining_table):
                    now = time.time()
                    if now - self._last_quick_play_time >= self._QUICK_PLAY_INTERVAL:
                        if not self._bet_amts_loaded:
                            self.send_list_bet_amt()
                        else:
                            valid_bets = self.get_1k_to_5k_bet_objs()
                            if valid_bets:
                                bet_obj = random.choice(valid_bets)
                                room = random.choice(self.ROOM_LIST)
                                print(f"[SEARCH] 🔍 Dò bàn: {bet_obj['value']} x (Bet ID {bet_obj['id']}) ở Phòng '{room}'")
                                self.send_quick_play(room_id=room, bet_amt_id=bet_obj['id'])
                                self._quick_play_attempts += 1
                            else:
                                print(f"[SEARCH] ❌ Không tìm thấy mức cược 500-10k -> TẠO BÀN MỚI")
                                self.send_create_table()
                                self._quick_play_attempts = 0
                time.sleep(1)
            except KeyboardInterrupt: break
            except: time.sleep(5)

    def cleanup(self):
        proc = getattr(self, '_engine_proc', None)
        if proc:
            try: 
                if proc.poll() is None:
                    proc.stdin.write("quit\n"); proc.stdin.flush(); proc.wait(timeout=2)
            except:
                try: proc.terminate()
                except: pass
        if self.ws:
            try: self.ws.close()
            except: pass

def acquire_single_instance_lock():
    """Chặn chạy 2 tiến trình bot cùng tk trên cùng máy.

    Server gamevh chỉ cho 1 phiên/tk: phiên mới đăng nhập sẽ ĐÁ phiên cũ ra
    (WebSocket bị đóng code 1000). Hai bot cùng chạy sẽ đá nhau vô tận, cứ vài giây
    lại mất bàn và tạo bàn mới -> đúng triệu chứng "chơi được một lúc lại thoát ra".
    """
    try:
        import fcntl
        path = os.path.join(tempfile.gettempdir(), f"xiangqi_bot_{USER}.lock")
        f = open(path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"[BOT] ❌ Đã có một bot khác đang chạy với tk {USER} (khoá: {path}).")
            print("[BOT] Thoát để tránh hai phiên đá nhau. Hãy tắt bot kia trước.")
            sys.exit(1)
        f.write(str(os.getpid())); f.flush()
        atexit.register(lambda: (fcntl.flock(f, fcntl.LOCK_UN), f.close()))
        return f
    except ImportError:
        return None

if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    bot = PikafishBot()
    def signal_handler(sig, frame): bot.cleanup(); sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try: bot.run()
    finally: bot.cleanup()
