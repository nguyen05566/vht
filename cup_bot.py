#!/usr/bin/env python3
"""
cup_bot.py — Cờ Úp Bot (gamevh.net / mystery_xiangqi) — engine PKJQ.exe qua wine
================================================================================
Chuyển từ cờ tướng sang cờ úp (mystery_xiangqi / Jieqi).

Engine:
- Jieqibox/Engines/PikaJieQi0111/PKJQ.exe — engine cờ úp thật sự (Pikafish 2023-01-11
  với hỗ trợ 'x' (quân úp) trong FEN). Chạy qua wine portable (Kron4ek Wine-Builds 9.0).
- FEN gốc giữ nguyên 'x'/'X' (không scrub) — PKJQ hiểu trực tiếp.

Tài khoản: nguyen20 / nhat123456 (test).
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

# urllib (stdlib) thay requests — requests gây REFRESH token trên gamevh.net
import urllib.request, urllib.parse, http.cookiejar

class _UrllibSession:
    """Wrapper cho urllib opener có API giống requests.Session (get/post/url/text)."""
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPRedirectHandler()
        )
        # headers: dict-like object — requests.Session() có thuộc tính .headers
        class _Headers:
            def __init__(self): self.d = {}
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
        # Áp dụng headers mặc định vào opener
        for k, v in self.headers.d.items():
            self.op.addheaders.append((k, v))
        self.last_url = ""
        # cookies: alias cho CookieJar — requests.Session() có .cookies
        self.cookies = self.cj
    def get(self, url, timeout=20, allow_redirects=True, **kw):
        # Merge any extra headers from kw
        h = list(self.op.addheaders)
        if 'headers' in kw and kw['headers']:
            for k, v in kw['headers'].items(): h.append((k, v))
        req = urllib.request.Request(url, headers=dict(h))
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R: 
            def __init__(self, r): self.text = r.read().decode("utf-8", "replace"); self.url = r.geturl()
        return R(r)
    def post(self, url, data=None, timeout=20, headers=None, allow_redirects=True, **kw):
        body = urllib.parse.urlencode(data or {}).encode()
        h = dict(self.op.addheaders)
        if headers:
            for k, v in headers.items(): h[k] = v
        req = urllib.request.Request(url, data=body, headers=h)
        r = self.op.open(req, timeout=timeout)
        self.last_url = r.geturl()
        class R:
            def __init__(self, r): self.text = r.read().decode("utf-8", "replace"); self.url = r.geturl()
        return R(r)

requests = type('R', (), {'Session': _UrllibSession})()  # drop-in shim

# ==================== TÀI KHOẢN (KHÔNG CẦN COOKIE) ====================
# Đăng nhập trực tiếp bằng username/password giống các bot nguyen1..nguyen6
CARO_USER_DIRECT = "nguyen15"
CARO_PASSWD_DIRECT = "nhat123456"

def _clean_env(val, default):
    if val and str(val).strip():
        return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER19"), CARO_USER_DIRECT)
PASSWD = _clean_env(os.environ.get("CARO_PASSWD19"), CARO_PASSWD_DIRECT)

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
GAME_URL = "https://gamevh.net/play/mystery_xiangqi/0"
CURRENT_PLAYER_NICKNAME = USER
CURRENT_PLAYER_ID = 0
TOKEN = 0
GAME_ID = 'mystery_xiangqi'
PLACE_PATH = 'Lobby.mystery_xiangqi.0'

# ★ ĐÃ BỎ "RAM-LEARN THAY THẾ NƯỚC ĐI".
# Trước đây bot bật MultiPV=3 rồi để TrendAnalyzer ghi đè bestmove bằng nước
# "điểm cao nhất" đọc được từ các dòng info -> luôn chọn nhầm nước ở depth nông,
# đá hỏng kết quả tìm kiếm của engine. Nay bot DÙNG THẲNG bestmove của PKJQ.
#
# MultiPV = 1: mạnh nhất & nhanh nhất (mặc định).
# Chỉ khi bàn có chốt bị khóa, bot mới TẠM bật MultiPV để lấy danh sách nước
# thay thế hợp lệ, xong lại trả về 1.
ENGINE_MULTIPV = 1
# Số nhánh tạm bật khi cần né chốt cố định.
ENGINE_MULTIPV_FALLBACK = 3

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

BOT_BET_XU = 1000  # cờ úp không có 400 — 1000 xu là mức thấp nhất hợp lý (id=3)
BOT_USE_CREATE_TABLE = True
BOT_MATCH_DURATION = '5'
BOT_TURN_DURATION = '60'
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
        data['OLD_PASSWORD'] = PASSWD
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
    """Đổi avatar ngẫu nhiên giống cơ chế nguyen* (có thể phát sinh phí xu)."""
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
    """Đăng nhập bằng USER/PASSWD (không dùng cookie hardcode) và lấy token/nickname/playerId."""
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
            data={"redirect": "/", "USER_NAME": USER, "PASSWORD": PASSWD,
                  "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
            headers={"Origin": "https://gamevh.net",
                     "Referer": LOGIN_URL,
                     "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=True)
        # LƯU Ý: login.jsp self-redirects kể cả khi login OK — KHÔNG check "login.jsp" trong URL.
        # Phải kiểm tra success bằng cách GET game page và xem có token hay không (xem B3).

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
        # session.cookies có thể là http.cookiejar.CookieJar (urllib) hoặc RequestsCookieJar (requests)
        try:
            # urllib CookieJar: duyệt qua cookie objects
            cookie_parts = []
            for c in session.cookies:
                cookie_parts.append(f"{c.name}={c.value}")
            COOKIE = "; ".join(cookie_parts)
        except (TypeError, AttributeError):
            # requests: dùng .items()
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
    def rem(self):
        """Số byte còn lại chưa đọc."""
        return len(self.data) - self.offset
    def peek_hex(self, n=32):
        """Debug: xem n byte tiếp theo dạng hex."""
        end = min(self.offset + n, len(self.data))
        return self.data[self.offset:end].hex()

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

class XiangqiBoardTracker:
    # CỜ ÚP initial FEN: 'x' (black face-down) / 'X' (red face-down) / 'k','K' (kings)
    INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX w"
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
        # ★ FEN GỐC VÁN + TOÀN BỘ NƯỚC ĐI (kèm hậu tố lật quân).
        #   PKJQ KHÔNG parse được FEN mà quân úp đã rời ô xuất phát (ô đó bị đọc
        #   thành ô trống -> engine crash). Vì vậy bắt buộc phải nạp:
        #       position fen <FEN ĐẦU VÁN> moves <tất cả nước>
        #   và để engine tự cập nhật thế cờ. Đây chính là cách Jieqibox làm.
        self.start_fen = self.INITIAL_FEN.split(' ')[0]
        self.start_side = 'w'
        self.uci_moves = []   # nước đi cho engine, vd 'e3e4P' (P = quân vừa lật)
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
        # ★ Danh sách chữ cái các quân ĐÃ LẬT trong ván (để trừ BAG).
        #   Không cần map theo ô nữa: engine tự giữ thế cờ qua 'moves'.
        self.revealed_chars = []
        # ★ Hướng bàn cờ: True = server row 0 là phía ĐỎ (đỏ ở "top" theo server)
        # -> phải lật (fen_row = 9 - server_row) để FEN luôn có ĐỎ ở dưới.
        # Giá trị được XÁC ĐỊNH TỪ DỮ LIỆU THẬT (vị trí tướng đỏ/đen trong START_MATCH),
        # KHÔNG đoán theo màu bot như bản cũ (đó chính là lỗi flip FEN).
        self.flip = False
        self.flip_known = False
        # ★ Track số quân úp đã reveal mỗi màu để trừ BAG động
        self._revealed_red_count = 0
        self._revealed_black_count = 0
    # ---------------------------------------------------------------
    # ★ CỜ ÚP TỌA ĐỘ — MỘT NGUỒN SỰ THẬT DUY NHẤT: self.flip
    #
    #   FEN chuẩn Pikafish/PKJQ: hàng đầu chuỗi = rank 9 (ĐEN), hàng cuối = rank 0 (ĐỎ).
    #   => fen_row = 9 - rank.
    #
    #   Server gamevh: pos 0..89, server_row = pos // 9, col = pos % 9.
    #   Tùy ván, server có thể đặt ĐỎ ở row 0 hoặc ở row 9 → phải DÒ, không đoán.
    #
    #   flip=True  : ĐỎ nằm ở server row nhỏ → fen_row = 9 - server_row
    #   flip=False : ĐỎ nằm ở server row lớn → fen_row = server_row
    #
    #   Mọi chuyển đổi pos <-> FEN <-> nước đi UCI đều đi qua 4 hàm dưới đây.
    # ---------------------------------------------------------------
    def pos_to_rc(self, pos):
        """server pos -> (fen_row, col) trong lưới FEN (row 0 = rank 9 = ĐEN)."""
        s_row, col = pos // 9, pos % 9
        return ((9 - s_row) if self.flip else s_row), col

    def rc_to_pos(self, fen_row, col):
        """(fen_row, col) -> server pos."""
        s_row = (9 - fen_row) if self.flip else fen_row
        return s_row * 9 + col

    def pos_to_idx(self, pos):
        """server pos -> chỉ số ô trong lưới FEN (fen_row*9+col)."""
        r, c = self.pos_to_rc(pos)
        return r * 9 + c

    def pos_to_engine_move(self, source_pos, target_pos):
        """server pos -> nước đi UCI (vd 'h2e2'). rank = 9 - fen_row."""
        s_row, s_col = self.pos_to_rc(source_pos)
        t_row, t_col = self.pos_to_rc(target_pos)
        return (f"{chr(ord('a') + s_col)}{9 - s_row}"
                f"{chr(ord('a') + t_col)}{9 - t_row}")

    def engine_move_to_pos(self, engine_move):
        """nước đi UCI -> (source_pos, target_pos) của server."""
        s_col, s_rank = ord(engine_move[0]) - ord('a'), int(engine_move[1])
        t_col, t_rank = ord(engine_move[2]) - ord('a'), int(engine_move[3])
        return (self.rc_to_pos(9 - s_rank, s_col),
                self.rc_to_pos(9 - t_rank, t_col))
    def bag_string(self):
        """BAG (túi quân chưa lật) — trừ dần theo các quân ĐÃ LẬT."""
        bag = {'A': 2, 'B': 2, 'N': 2, 'R': 2, 'C': 2, 'P': 5,
               'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}
        for ch in self.revealed_chars:
            if ch in bag:
                bag[ch] = max(0, bag[ch] - 1)
        out = "".join(f"{k}{v}" for k, v in bag.items() if v > 0)
        return out or "-"

    def get_current_fen(self):
        """Trả về (fen_đầu_ván, danh_sách_nước) để nạp: position fen <fen> moves ...

        ★ FIX QUAN TRỌNG: trước đây bot tự áp từng nước vào chuỗi FEN rồi nạp FEN
          "hiện tại" cho engine. Nhưng PKJQ.exe KHÔNG đọc được FEN có quân úp đứng
          ngoài ô xuất phát — nó biến ô đó thành ô TRỐNG rồi crash
          (log cũ: "[ENGINE] ⚠️ Engine crashed!" ở gần như mọi lượt, khiến bot
          phải fallback về thế cờ ĐẦU VÁN và gửi nước đi của bàn cờ cũ ->
          server trả 'NoPieceAtSourcePosition').

          Cách đúng (giống Jieqibox): luôn nạp FEN ĐẦU VÁN + toàn bộ nước đi,
          mỗi nước kèm hậu tố chữ cái nếu nước đó làm LẬT một quân úp.
        """
        my_side = 'w' if self.is_red else 'b'
        turn_side = my_side if self.is_my_turn else ('b' if my_side == 'w' else 'w')
        # Bên đi được suy ra từ số nước đã đi (chuẩn hơn, vì engine tự tính lượt)
        fen = f"{self.start_fen} {self.bag_string()} {self.start_side} - - 0 1"
        return fen, list(self.uci_moves)

    def set_base(self, board_fen, side='w'):
        """Chốt FEN ĐẦU VÁN (gọi 1 lần khi START_MATCH) và xoá lịch sử nước đi."""
        board_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.fen = board_fen
        self.base_fen = board_fen
        self.base_side = side
        self.start_fen = board_fen
        self.start_side = side
        self.uci_moves = []
        self.moves_since_base = []
        self.move_history = []
        self.revealed_chars = []

    def record_move(self, mv, revealed_char=None):
        """Ghi 1 nước đi. revealed_char = chữ cái quân vừa được LẬT (nếu có).

        PKJQ nhận nước đi dạng 'e3e4' hoặc 'e3e4P' — hậu tố P nghĩa là quân úp
        vừa di chuyển hoá ra là TỐT ĐỎ. Không có hậu tố = không lật quân nào.
        """
        self.move_history.append(mv)
        self.moves_since_base.append(mv)
        uci = mv + (revealed_char or "")
        self.uci_moves.append(uci)
        if revealed_char:
            self.revealed_chars.append(revealed_char)
        return uci

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == first_turn_slot_id)

    # ---------------------------------------------------------------
    # ★ FIX LỖI FLIP FEN
    # Bản cũ: do_flip = (bot cầm ĐỎ). Sai, vì hướng bàn cờ do SERVER quyết định
    # (quân của mình luôn ở dưới màn hình), không liên quan tới việc bot cầm màu gì.
    # Khi bot cầm ĐEN mà server vẫn đặt ĐỎ ở row 0, FEN bị lật ngược:
    # engine nhận thế cờ "gương" -> nước đi trả về vô nghĩa / bị server từ chối.
    #
    # Bản mới: dò hướng THẬT từ vị trí TƯỚNG (tướng luôn ở cung nhà mình):
    #   - tướng ĐỎ ở server row <= 2  -> ĐỎ ở trên  -> flip = True
    #   - tướng ĐỎ ở server row >= 7  -> ĐỎ ở dưới  -> flip = False
    # Dùng cả tướng đen để kiểm tra chéo; nếu thiếu dữ liệu thì suy ra từ khối quân.
    # ---------------------------------------------------------------
    def detect_flip(self, pieces):
        """pieces: list (sid, face, position, is_open). Trả về True nếu cần lật."""
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

        flip = None
        # 1) Ưu tiên tuyệt đối: vị trí tướng
        if red_king_row is not None and black_king_row is not None:
            flip = red_king_row < black_king_row
        elif red_king_row is not None:
            flip = red_king_row <= 4
        elif black_king_row is not None:
            flip = black_king_row >= 5
        # 2) Fallback: trọng tâm khối quân mỗi bên
        elif red_rows and black_rows:
            flip = (sum(red_rows) / len(red_rows)) < (sum(black_rows) / len(black_rows))

        if flip is None:
            # 3) Cùng đường: giữ quy ước cũ (bot đỏ -> đỏ ở trên)
            flip = bool(self.is_red)
            self.flip_known = False
        else:
            self.flip_known = True
        self.flip = bool(flip)
        return self.flip

    def sanity_check_fen(self, board_fen):
        """Kiểm tra FEN có đúng chiều không: 'K' (tướng đỏ) phải nằm ở nửa DƯỚI
        (fen_row >= 7) và 'k' ở nửa TRÊN (fen_row <= 2). Trả về (ok, thông báo)."""
        rows = board_fen.split(' ')[0].split('/')
        if len(rows) != 10:
            return False, f"FEN có {len(rows)} hàng (cần 10)"
        k_row = K_row = None
        for i, r in enumerate(rows):
            if 'K' in r: K_row = i
            if 'k' in r: k_row = i
        if K_row is None or k_row is None:
            return False, "thiếu tướng trong FEN"
        if K_row < 7 or k_row > 2:
            return False, f"tướng sai chiều (K ở hàng {K_row}, k ở hàng {k_row})"
        return True, "ok"

class MultiPVCollector:
    """Thu thập các nhánh MultiPV mà engine in ra (info ... multipv N ... pv ...).

    ★ ĐÃ BỎ "RAM-LEARN": lớp này KHÔNG còn tự ý thay thế nước đi của engine nữa.
      Nó chỉ LƯU LẠI danh sách nước ứng viên theo ĐÚNG THỨ TỰ XẾP HẠNG CỦA ENGINE
      (multipv 1 = tốt nhất), và chỉ được dùng khi bot buộc phải né một nước
      (vd: chốt bị khóa). Không có heuristic "điểm trung bình", không ghi đè bestmove.
    """
    # info depth 12 seldepth 18 multipv 2 score cp -35 ... pv h2e2 b9c7
    _re = re.compile(
        r"info\b.*?\bdepth (\d+).*?\bmultipv (\d+).*?\bscore (cp|mate) (-?\d+).*?\bpv ([a-i]\d[a-i]\d(?:\s+\S+)*)"
    )

    def __init__(self):
        # {multipv_rank: {"move","score","is_mate","depth"}} — chỉ giữ depth sâu nhất
        self.lines = {}
        self._best_depth = -1

    def clear(self):
        self.lines.clear()
        self._best_depth = -1

    def parse_line(self, line_str):
        m = self._re.search(line_str)
        if not m:
            return
        depth = int(m.group(1))
        rank = int(m.group(2))
        is_mate = (m.group(3) == "mate")
        score = int(m.group(4))
        move = m.group(5).split()[0]
        # Chỉ giữ vòng lặp depth sâu nhất -> tránh lẫn dữ liệu depth nông
        if depth > self._best_depth:
            self._best_depth = depth
            self.lines.clear()
        elif depth < self._best_depth:
            return
        self.lines[rank] = {"move": move, "score": score,
                            "is_mate": is_mate, "depth": depth}

    def candidates(self):
        """Danh sách nước ứng viên theo đúng xếp hạng của engine (tốt -> kém)."""
        return [self.lines[r]["move"] for r in sorted(self.lines)]

class PikafishBot:
    def __init__(self):
        self.conn = Conn()
        self.board = XiangqiBoardTracker()
        self.multipv = MultiPVCollector()
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
        self._readyok = False            # engine đã trả 'readyok' chưa
        self._last_sent_move = None      # nước UCI vừa gửi (để cấm nếu bị từ chối)
        self._rejected_moves = set()     # các nước server đã từ chối trong lượt này
        self._mate_regex = re.compile(r"score mate (-?\d+)")
        self._score_regex = re.compile(r"depth (\d+).*score (cp|mate) (-?\d+)")
        self._last_score = "?"
        self._last_depth = "?"
        self._init_engine()

    def _kill_engine(self):
        """Giết tiến trình engine cũ (nếu còn) TRƯỚC khi khởi động cái mới.

        ★ FIX RÒ RỈ TIẾN TRÌNH: mỗi lần _init_engine() chạy mà không dọn tiến
          trình cũ, một PKJQ.exe + wineserver nữa lại nằm lại trong RAM. Sau vài
          lần restart, máy hết RAM và hệ điều hành (OOM killer) bắn chết engine
          -> "Engine chết" liên tục, bot mất lượt rồi thua giờ.
        """
        proc = getattr(self, '_engine_proc', None)
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    self._fsf_cmd("quit")
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                    try: proc.wait(timeout=3)
                    except Exception: pass
            for _s in (proc.stdin, proc.stdout, proc.stderr):
                try: _s and _s.close()
                except Exception: pass
        except Exception as e:
            print(f"[ENGINE] ⚠️  Lỗi khi dọn engine cũ: {e}")
        finally:
            self._engine_proc = None
            self.engine = None

    def _init_engine(self):
        """Khởi động PKJQ.exe (engine cờ úp thật) qua wine portable.

        PKJQ.exe là Pikafish 2023-01-11 bản cờ úp — hiểu trực tiếp 'x'/'X' trong FEN.
        Chạy qua wine (Kron4ek Wine-Builds 9.0 portable) vì .exe chỉ Windows.
        """
        # Dọn engine cũ trước (tránh rò rỉ tiến trình -> hết RAM -> bị OOM kill)
        self._kill_engine()

        # --- Tìm wine64 ---
        wine_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "wine-portable", "wine-9.0-staging-amd64", "bin", "wine64"),
            "/home/z/my-project/wine-portable/wine-9.0-staging-amd64/bin/wine64",
            os.path.expanduser("~/wine-portable/wine-9.0-staging-amd64/bin/wine64"),
            "/usr/lib/wine/wine64",      # wine64 của Debian/Ubuntu (apt install wine64)
            "/usr/bin/wine64",
            "/usr/local/bin/wine64",
            "/usr/bin/wine",
            "/usr/local/bin/wine",
        ]
        # Bổ sung: wine/wine64 nằm trong PATH
        import shutil as _sh
        for _w in ("wine64", "wine"):
            _p = _sh.which(_w)
            if _p: wine_candidates.append(_p)
        wine_path = next((p for p in wine_candidates if os.path.isfile(p) and os.access(p, os.X_OK)), None)

        # --- Tìm PKJQ.exe ---
        pkjq_candidates = [
            "/home/z/my-project/vht/jieqibox/Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "jieqibox", "Jieqibox", "Engines", "PikaJieQi0111", "PKJQ.exe"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "Jieqibox", "Engines", "PikaJieQi0111", "PKJQ.exe"),
            "./jieqibox/Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
            "./Jieqibox/Engines/PikaJieQi0111/PKJQ.exe",
        ]
        pkjq_path = next((p for p in pkjq_candidates if os.path.isfile(p)), None)

        if not wine_path:
            print("[ENGINE] ❌ KHÔNG TÌM THẤY wine64! Đã tìm ở: " + ", ".join(wine_candidates))
            print("[ENGINE] ❌ Cài wine portable:")
            print("[ENGINE]   curl -sL https://github.com/Kron4ek/Wine-Builds/releases/download/9.0/wine-9.0-staging-amd64.tar.xz | tar xJ -C wine-portable/")
            return
        if not pkjq_path:
            print("[ENGINE] ❌ KHÔNG TÌM THẤY PKJQ.exe! Đã tìm ở: " + ", ".join(pkjq_candidates))
            print("[ENGINE] ❌ Download Jieqibox.rar từ repo nguyen05566/vht, unrar x Jieqibox.rar")
            return

        print(f"[ENGINE] 🍷 wine   = {wine_path}")
        print(f"[ENGINE] ♟️  PKJQ  = {pkjq_path}")

        # --- Đảm bảo pikafish.nnue ở cùng thư mục với PKJQ.exe ---
        engine_dir = os.path.dirname(pkjq_path)
        nnue_path = os.path.join(engine_dir, "pikafish.nnue")
        if not os.path.isfile(nnue_path):
            # Copy từ pikafish-engine/ nếu có
            src_nnue = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pikafish-engine", "pikafish.nnue")
            if os.path.isfile(src_nnue):
                import shutil
                try:
                    shutil.copy(src_nnue, nnue_path)
                    print(f"[ENGINE] 📦 Đã copy pikafish.nnue → {nnue_path}")
                except Exception as e:
                    print(f"[ENGINE] ⚠️  Copy NNUE lỗi: {e}")
            else:
                # Tự tải NNUE nếu chưa có (chạy lần đầu trên máy mới / CI)
                print(f"[ENGINE] ⬇️  Chưa có pikafish.nnue -> đang tải về {nnue_path} ...")
                try:
                    _url = ("https://github.com/official-pikafish/Networks/"
                            "releases/download/master-net/pikafish.nnue")
                    urllib.request.urlretrieve(_url, nnue_path)
                    print(f"[ENGINE] ✅ Đã tải NNUE ({os.path.getsize(nnue_path)//1048576} MB)")
                except Exception as e:
                    print(f"[ENGINE] ⚠️  Tải NNUE thất bại: {e} (engine vẫn chạy nhưng yếu hơn)")

        # --- Khởi động PKJQ qua wine ---
        env = os.environ.copy()
        env["WINEPREFIX"] = env.get("WINEPREFIX", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wine"))
        env["WINEDEBUG"] = "-all"
        env["DISPLAY"] = ""
        # wine cần XDG_RUNTIME_DIR; môi trường CI/headless hay thiếu -> spam lỗi
        if not env.get("XDG_RUNTIME_DIR"):
            _xdg = os.path.join(tempfile.gettempdir(), f"xdg-{os.getuid()}")
            os.makedirs(_xdg, mode=0o700, exist_ok=True)
            env["XDG_RUNTIME_DIR"] = _xdg
        try:
            self._engine_proc = subprocess.Popen(
                [wine_path, pkjq_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=env, cwd=engine_dir
            )
        except Exception as e:
            print(f"[ENGINE] ❌ Lỗi khởi động wine+PKJQ: {e}")
            return

        # Thread: đọc stderr (bỏ qua log wine)
        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    if not proc.stderr.readline(): break
            except: pass
        threading.Thread(target=consume_stderr, args=(self._engine_proc,), daemon=True).start()

        # Thread: đọc stdout + filter info lines
        def consume_stdout_and_filter(proc):
            try:
                while proc.poll() is None:
                    line = proc.stdout.readline()
                    if not line: break
                    line_str = line.strip()

                    self.multipv.parse_line(line_str)

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

                    if line_str == "readyok":
                        self._readyok = True

                    if line_str.startswith("bestmove"):
                        self._latest_bestmove = line_str
            except: pass
        threading.Thread(target=consume_stdout_and_filter, args=(self._engine_proc,), daemon=True).start()

        try:
            self._fsf_cmd("uci")
            # Chừa ít nhất 1 nhân cho luồng WebSocket, nếu không pong/heartbeat bị trễ
            # và server cắt kết nối ngay giữa ván.
            _threads = max(1, min(4, (os.cpu_count() or 2) - 1))
            self._fsf_cmd(f"setoption name Threads value {_threads}")
            # Hash nhỏ lại: hộp chạy bot thường chỉ 2GB RAM. 256MB mỗi lần restart
            # rất dễ đẩy máy vào trạng thái hết bộ nhớ.
            self._fsf_cmd("setoption name Hash value 64")
            self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")
            # EvalFile: dùng pikafish.nnue ở cùng thư mục với PKJQ.exe (cwd đã set)
            self._fsf_cmd("setoption name EvalFile value pikafish.nnue")
            # ★ CHỜ 'readyok' THẬT SỰ thay vì sleep(1) đoán mò.
            #   Nạp NNUE 50MB qua wine có khi mất vài giây; bản cũ chỉ ngủ 1s rồi
            #   bắn 'go' ngay -> engine chưa sẵn sàng, lượt đó mất trắng.
            self._readyok = False
            self._fsf_cmd("isready")
            _t0 = time.time()
            while not self._readyok and time.time() - _t0 < 20:
                if self._engine_proc.poll() is not None:
                    print("[ENGINE] ❌ Engine thoát ngay khi khởi tạo")
                    return
                time.sleep(0.05)
            if not self._readyok:
                print("[ENGINE] ⚠️  Quá 20s chưa thấy 'readyok' — vẫn thử chạy tiếp")
            else:
                print(f"[ENGINE] ⏱️  readyok sau {time.time() - _t0:.1f}s")
            self.engine = True
            print(f"[ENGINE] ✅ Sẵn sàng (PKJQ via wine) | Threads={_threads} | MultiPV={ENGINE_MULTIPV}"
                  + ("" if ENGINE_MULTIPV > 1 else " (dùng thẳng bestmove của engine, không RAM-learn)"))
        except Exception as e:
            print(f"[ENGINE] ❌ Lỗi khởi tạo: {e}")

    def _fsf_cmd(self, text):
        if getattr(self, '_engine_proc', None) and self._engine_proc.poll() is None:
            self._engine_proc.stdin.write(text + "\n")
            self._engine_proc.stdin.flush()

    def _sync_engine(self, timeout=5.0):
        """Đảm bảo engine RẢNH và đã nuốt hết lệnh cũ trước khi nạp thế cờ mới.

        ★ FIX LỖI MẤT LƯỢT ("Engine không trả lời kịp"):
          Theo chuẩn UCI, engine ĐANG TÌM KIẾM sẽ BỎ QUA lệnh 'position'. Nếu một
          lượt trước đó hết giờ mà bot chưa nhận được 'bestmove', engine vẫn còn
          chạy; lượt sau bot gửi 'position ... / go' thì 'position' bị nuốt mất,
          engine trả lời cho thế cờ CŨ hoặc không trả lời -> bot mất lượt liên
          tiếp cho tới khi thua giờ (đúng chuỗi lỗi thấy trong log).

          Cách xử lý: gửi 'stop', rồi bắt tay 'isready' -> chờ 'readyok'. Engine
          chỉ trả 'readyok' khi đã xử lý xong mọi lệnh trước đó.
        """
        if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None:
            return False
        self._fsf_cmd("stop")
        self._readyok = False
        self._fsf_cmd("isready")
        _t0 = time.time()
        while not self._readyok:
            if self._engine_proc.poll() is not None:
                return False
            if time.time() - _t0 > timeout:
                print("[ENGINE] ⚠️  Engine không phản hồi 'readyok' -> khởi động lại", flush=True)
                try:
                    self._engine_proc.kill()
                except Exception:
                    pass
                return False
            time.sleep(0.02)
        return True

    def get_best_move(self, fen, moves, fixed_positions=None):
        try:
            if not getattr(self, '_engine_proc', None) or self._engine_proc.poll() is not None: return None
            self.multipv.clear() 
            
            if not self._sync_engine():
                return None

            if fixed_positions: return self._get_move_avoiding_fixed(fen, moves, fixed_positions)
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)

            # ★ FIX RACE: phải XOÁ bestmove cũ TRƯỚC khi gửi 'go'.
            #   Bản cũ xoá bên trong _read_bestmove() — tức là SAU khi 'go' đã gửi.
            #   Engine cờ tàn trả lời gần như tức thì, nên câu trả lời thật thường
            #   bị chính dòng xoá này ghi đè -> bot đứng hình cả lượt
            #   ("Không lấy được nước đi") và thua dù đang thắng to.
            self._latest_bestmove = None
            self._mate_status = None
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd("go movetime 3200")
            return self._read_bestmove(timeout=6.0, reset=False)
        except Exception as e: print(f"[ENGINE] Lỗi tính toán: {e}")
        return None

    def _read_bestmove(self, timeout=3, reset=True):
        _go_start = time.time()
        if reset:
            self._latest_bestmove = None
            self._mate_status = None

        while True:
            if self._engine_proc.poll() is not None: return None
            if self._latest_bestmove:
                return self._latest_bestmove
            
            if time.time() - _go_start > timeout:
                self._fsf_cmd("stop")
                # Chờ lâu hơn sau 'stop': engine cần vài trăm ms để kết thúc và in
                # bestmove. Bản cũ chỉ chờ 0.1s -> hay bỏ lỡ câu trả lời.
                _t = time.time()
                while time.time() - _t < 1.5:
                    if self._latest_bestmove:
                        return self._latest_bestmove
                    time.sleep(0.02)
                break
            time.sleep(0.02) # Phản xạ luồng đọc siêu tốc
        return None

    def _get_move_avoiding_fixed(self, fen, moves, fixed_positions):
        """Tìm nước đi KHÔNG xuất phát từ chốt bị khóa.

        ★ ĐÂY LÀ CHỖ DUY NHẤT CÒN DÙNG MultiPV — và chỉ khi thật sự cần thiết:
          1. Chạy bình thường (MultiPV hiện tại). Nếu bestmove không dính chốt -> xong.
          2. Chỉ khi bestmove dính chốt: TẠM bật MultiPV=ENGINE_MULTIPV_FALLBACK,
             tính lại, rồi lấy nước ứng viên TỐT NHẤT TIẾP THEO theo đúng thứ tự
             xếp hạng của engine (không dùng heuristic tự chế nào).
          3. Kết thúc luôn trả MultiPV về giá trị gốc.
        """
        def _search(movetime):
            self._sync_engine()
            self.multipv.clear()
            self._latest_bestmove = None
            self._mate_status = None
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd(f"go movetime {movetime}")
            _wait = time.time()
            while time.time() - _wait < (movetime / 1000.0) + 1.5:
                if self._latest_bestmove: break
                time.sleep(0.02)
            if not self._latest_bestmove:
                self._fsf_cmd("stop")
                _t = time.time()
                while time.time() - _t < 1.5:
                    if self._latest_bestmove: break
                    time.sleep(0.02)
            return self._latest_bestmove

        raw = _search(2500)
        if not raw:
            return None
        parts = raw.split()
        best_move = parts[1] if len(parts) >= 2 else None
        if not best_move or best_move in ("(none)", "0000"):
            return raw
        if not self._move_hits_fixed_pawn(best_move, fixed_positions):
            return raw

        # --- Bestmove dính chốt khóa -> mới cần MultiPV ---
        print(f"[ENGINE] ⚠️ Bestmove {best_move} xuất phát từ chốt bị khóa "
              f"-> tạm bật MultiPV={ENGINE_MULTIPV_FALLBACK} tìm nước thay thế")
        self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV_FALLBACK}")
        try:
            raw2 = _search(2500) or raw
            for cand in self.multipv.candidates():
                if not self._move_hits_fixed_pawn(cand, fixed_positions):
                    print(f"[ENGINE] ✅ Chọn nước thay thế hạng kế tiếp của engine: {cand}")
                    return f"bestmove {cand}"
            print("[ENGINE] ⚠️ Mọi nhánh MultiPV đều dính chốt -> giữ bestmove gốc")
            return raw2
        finally:
            # Luôn trả MultiPV về mặc định
            self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")

    def _next_candidate_excluding(self, fen, moves, excluded):
        """Lấy nước xếp hạng cao nhất của engine mà KHÔNG nằm trong `excluded`.

        Chỉ được gọi khi server đã từ chối nước tốt nhất (thế cờ bị lệch).
        Tạm bật MultiPV rồi trả về đúng giá trị cũ.
        """
        self._fsf_cmd(f"setoption name MultiPV value {max(ENGINE_MULTIPV_FALLBACK, len(excluded) + 2)}")
        try:
            self._sync_engine()
            self.multipv.clear()
            self._latest_bestmove = None
            pos_cmd = f"position fen {fen}"
            if moves: pos_cmd += " moves " + " ".join(moves)
            self._fsf_cmd(pos_cmd)
            self._fsf_cmd("go movetime 2000")
            _w = time.time()
            while time.time() - _w < 3.5:
                if self._latest_bestmove: break
                time.sleep(0.02)
            if not self._latest_bestmove:
                self._fsf_cmd("stop"); time.sleep(0.15)
            for cand in self.multipv.candidates():
                if cand not in excluded:
                    return cand
            return None
        except Exception as e:
            print(f"[ENGINE] Lỗi tìm nước thay thế: {e}")
            return None
        finally:
            self._fsf_cmd(f"setoption name MultiPV value {ENGINE_MULTIPV}")

    def _move_hits_fixed_pawn(self, move_str, fixed_positions):
        """Kiểm tra nước đi có bắt đầu từ vị trí chốt cố định không."""
        if not fixed_positions or len(move_str) < 4:
            return False
        try:
            # ★ FIX FLIP: đổi UCI -> server pos qua cùng một phép biến đổi với FEN.
            src_pos, _ = self.board.engine_move_to_pos(move_str)
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
        """Tìm bet_amt_id khớp với BOT_BET_XU. Nếu không thấy chính xác,
        lấy mức thấp nhất ≥ BOT_BET_XU; nếu vẫn không có, lấy mức cao nhất."""
        if not self.bet_amts: return None
        # Tìm mức chính xác
        exact = [ba for ba in self.bet_amts if ba["value"] == BOT_BET_XU]
        if exact:
            return exact[0]['id']
        # Mức thấp nhất >= BOT_BET_XU
        above = [ba for ba in self.bet_amts if ba["value"] >= BOT_BET_XU]
        if above:
            chosen = min(above, key=lambda x: x['value'])
            return chosen['id']
        # Fallback: mức cao nhất có
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
        self._rejected_moves = set()
        self._last_sent_move = None
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
            print(f"[START_MATCH] player_count={player_count}", flush=True)
            for i in range(player_count):
                slot = msg.read_byte()
                pid = msg.read_int()
                print(f"  player[{i}]: slot={slot} pid={pid}", flush=True)
            piece_count = msg.read_byte()
            print(f"[START_MATCH] piece_count={piece_count}", flush=True)
            board_pieces = []
            for _ in range(piece_count):
                raw_sid = msg.read_byte(); raw_face = msg.read_byte(); pos = msg.read_byte(); is_open = msg.read_byte()
                board_pieces.append((self._decode_piece_id(raw_sid), self._decode_piece_id(raw_face), pos, is_open))
                if True:  # debug all pieces
                    print(f"  piece[{_}]: sid=0x{raw_sid & 0xff:02x} face=0x{raw_face & 0xff:02x} pos={pos} is_open={is_open}", flush=True)
                elif False:  # disable
                    print(f"  ... (omitting middle pieces)", flush=True)

            msg.read_byte(); mystery_count = msg.read_byte()
            print(f"[START_MATCH] mystery_count={mystery_count}", flush=True)
            for _ in range(mystery_count): msg.read_byte()
            msg.read_byte(); msg.read_byte()

            first_turn_slot_id = msg.read_byte()
            my_slot_id = msg.read_byte()
            print(f"[START_MATCH] first_turn_slot={first_turn_slot_id} my_slot={my_slot_id}", flush=True)
            if my_slot_id < 0 or my_slot_id == 255:
                my_slot_id = self.board.my_slot_id if self.board.my_slot_id >= 0 else first_turn_slot_id

            self.board.set_my_slot(my_slot_id, first_turn_slot_id)

            for sid, face, position, is_open in board_pieces:
                piece_type = int(face[1]) if len(face) > 1 else 0
                if piece_type == 7 and position not in STANDARD_PAWN_POSITIONS:
                    self.fixed_pawn_positions.add(position)

            if self.fixed_pawn_positions:
                print(f"[GAME] 🛡️ Bàn đấu có {len(self.fixed_pawn_positions)} chốt bị liệt/khóa!")

            _built_fen = self._build_fen_from_pieces(board_pieces)
            # ★ TỰ KIỂM TRA CHIỀU FEN: nếu tướng sai nửa bàn -> lật lại và dựng lần 2.
            _ok, _why = self.board.sanity_check_fen(_built_fen)
            if not _ok:
                print(f"[FEN] ⚠️  FEN dựng sai chiều ({_why}) -> tự lật ngược và dựng lại", flush=True)
                self.board.flip = not self.board.flip
                _rebuilt = self._rebuild_fen_with_current_flip(board_pieces)
                _ok2, _why2 = self.board.sanity_check_fen(_rebuilt)
                if _ok2:
                    _built_fen = _rebuilt
                    print(f"[FEN] ✅ Đã sửa: flip={self.board.flip}", flush=True)
                else:
                    print(f"[FEN] ❌ Lật lại vẫn sai ({_why2}) -> giữ bản đầu", flush=True)
                    self.board.flip = not self.board.flip
            self.board.set_base(_built_fen, 'w')
            print(f"[FEN] 📋 base = {self.board.base_fen}", flush=True)

            # ★ Quân đã lộ sẵn từ đầu ván (tướng) KHÔNG nằm trong BAG cờ úp,
            #   nên không trừ gì cả — bag_string() chỉ trừ theo quân được LẬT
            #   trong lúc chơi (revealed_chars).
            self.board.revealed_chars = []
            print(f"[START_MATCH] BAG đầu ván = {self.board.bag_string()}", flush=True)

            if my_slot_id == first_turn_slot_id:
                self.board.is_my_turn = True
                self._turn_started_at = time.time()
                threading.Thread(target=self._make_auto_move, daemon=True).start()
        except Exception as e: print(f"[START_MATCH ERROR] {e}")

    def _build_fen_from_pieces(self, pieces):
        """Dựng FEN cờ úp từ danh sách (sid_str, face_str, position, is_open).

        ★ FIX FLIP: hướng bàn cờ được DÒ TỪ VỊ TRÍ TƯỚNG (board.detect_flip),
          không còn đoán theo màu bot. Kết quả: FEN luôn chuẩn PKJQ
          (ĐỎ ở dưới = rank 0..4, ĐEN ở trên = rank 5..9) dù bot cầm màu nào,
          dù server xoay bàn kiểu gì.
        """
        do_flip = self.board.detect_flip(pieces)
        print(f"[FEN] 🔄 flip={do_flip} (dò từ vị trí tướng, "
              f"{'chắc chắn' if self.board.flip_known else 'suy đoán'}) "
              f"| bot cầm {'ĐỎ' if self.board.is_red else 'ĐEN'}", flush=True)
        return self._rebuild_fen_with_current_flip(pieces)

    def _rebuild_fen_with_current_flip(self, pieces):
        """Dựng FEN với self.board.flip hiện tại (không dò lại hướng)."""
        board = [['.' for _ in range(9)] for _ in range(10)]
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90: continue
            fen_row, col = self.board.pos_to_rc(position)
            color = face[0] if len(face) > 0 else 'r'
            if is_open and len(face) > 1:
                piece_type = int(face[1])
                type_to_fen = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}
                fen_char = type_to_fen.get(piece_type, '?')
                if color == 'r': fen_char = fen_char.upper()
            else:
                # Quân úp: dùng 'x' (đen) / 'X' (đỏ)
                fen_char = 'X' if color == 'r' else 'x'
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

    # Mã loại quân của gamevh: |sid| >> 3
    PIECE_TYPE_MAP = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

    @classmethod
    def _sid_to_fen_char(cls, byte_val):
        """1 byte sid của gamevh -> ký tự FEN ('P' tốt đỏ, 'c' pháo đen...).

        Byte là SIGNED: dương = ĐỎ, âm = ĐEN. list(bytes) cho 0..255 nên phải
        chuyển về signed trước (bug cũ: quân đen bị đọc thành đỏ).
        """
        v = byte_val - 256 if byte_val > 127 else byte_val
        if v == 0:
            return None
        ch = cls.PIECE_TYPE_MAP.get(abs(v) >> 3)
        if not ch:
            return None
        return ch.upper() if v > 0 else ch

    def _handle_move(self, msg):
        """Gói MOVE: src(u8) tgt(u8) [reveal_count(u8) sid(u8) face(u8)]

        ★ GIẢI MÃ ĐÚNG (đối chiếu với START_MATCH + log thật):
            012921 -> count=1, sid=0x29(C1), face=0x21(R1)
            01c6c5 -> count=1, sid=0xc6(p2), face=0xc5(p3)
            00     -> count=0, không lật quân nào

          - sid  = ĐỊNH DANH THEO Ô XUẤT PHÁT (quân "lẽ ra" đứng đó theo bàn cờ
                   tướng chuẩn) — KHÔNG phải quân thật.
          - face = MẶT THẬT của quân vừa được lật  ← ĐÂY mới là thứ engine cần.

          Bug cũ: lấy nhầm sid làm quân lật ('b2b6C' trong khi thật ra là quân XE
          'b2b6R'), khiến thế cờ trong engine lệch hẳn so với server -> engine
          trả nước đi từ ô không có quân -> 'NoPieceAtSourcePosition'.
        """
        try:
            source_pos = msg.read_byte()
            target_pos = msg.read_byte()
            engine_move = self.board.pos_to_engine_move(source_pos, target_pos)
            self.last_action_timestamp = time.time()

            rest = list(msg.data[msg.offset:]) if msg.offset < len(msg.data) else []
            rest_hex = bytes(rest).hex()

            revealed_char = None
            if rest and rest[0] > 0 and len(rest) >= 3:
                # rest[0] = số quân được lật (thường 1)
                # rest[1] = sid  (định danh ô xuất phát — KHÔNG dùng)
                # rest[2] = face (mặt thật vừa lật — DÙNG CÁI NÀY)
                cand = self._sid_to_fen_char(rest[2])
                # Không bao giờ lật ra tướng (tướng luôn ngửa từ đầu ván)
                if cand and cand not in ('k', 'K'):
                    revealed_char = cand

            uci = self.board.record_move(engine_move, revealed_char)
            self._played_this_turn = False

            print(f"[MOVE] {engine_move} (src={source_pos} tgt={target_pos})"
                  + (f" 🔓 lật ra '{revealed_char}'" if revealed_char else "")
                  + f" -> engine '{uci}'  [raw {rest_hex}]", flush=True)
        except Exception as e:
            print(f"[MOVE ERROR] {e}")

    def _handle_play_response(self, msg):
        status = msg.read_byte()
        # Đọc thêm text lỗi nếu có (UTF-16 string)
        err_text = ""
        try:
            if msg.rem() >= 2:
                err_text = msg.read_string()
        except Exception:
            pass
        # ★ DEBUG: dump full packet hex
        # msg.data is bytes, msg.offset is current position. Use .offset (not .o)
        try:
            rem_hex = msg.data[msg.offset:].hex() if hasattr(msg, 'offset') else ''
        except Exception:
            rem_hex = '<err>'
        print(f"[PLAY-RESP] status={status} err={err_text!r} remaining_hex={rem_hex}", flush=True)
        if status != 0:
            # Server từ chối nước đi -> tự tính lại (không chờ SET_TURN mới).
            self.board.is_my_turn = True
            self._played_this_turn = False
            self._play_reject_count = getattr(self, '_play_reject_count', 0) + 1
            print(f"[PLAY] ⚠️ Server từ chối nước đi (status={status}, err={err_text!r}, lần {self._play_reject_count}) -> tính lại")

            # ★ CHỐNG LẶP VÔ HẠN: 'NoPieceAtSourcePosition' nghĩa là thế cờ trong
            #   engine đã LỆCH so với server (thường do đọc sai quân lật). Tính lại
            #   y hệt sẽ ra y hệt nước sai -> loop tới hết giờ và THUA.
            #   Từ lần thứ 2 trở đi: cấm nước vừa bị từ chối rồi tìm nước khác.
            if "NoPieceAtSource" in (err_text or "") or status == 51:
                bad = getattr(self, '_last_sent_move', None)
                if bad:
                    self._rejected_moves.add(bad)
                    print(f"[PLAY] 🚫 Cấm nước '{bad}' (tổng {len(self._rejected_moves)} nước bị cấm) "
                          f"-> buộc engine chọn nước khác", flush=True)

            if self._play_reject_count <= 6:
                threading.Thread(
                    target=lambda: (time.sleep(0.5), self._make_auto_move()), daemon=True).start()
            else:
                print("[PLAY] ❌ Từ chối quá nhiều lần -> ngừng thử để khỏi treo lượt", flush=True)
        else:
            self._play_reject_count = 0
            self._rejected_moves.clear()

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

        # ★ DEBUG: FEN đầu ván + toàn bộ nước đi (engine tự dựng thế cờ hiện tại)
        print(f"[ENGINE-IN] FEN: {fen}", flush=True)
        print(f"[ENGINE-IN] moves({len(moves)}): {' '.join(moves) if moves else '(chưa có)'}", flush=True)

        raw_bestmove_line = self.get_best_move(fen, moves, fixed_positions=fixed)
        # ★ FALLBACK: engine chết giữa chừng -> restart rồi nạp LẠI ĐÚNG ván này.
        #   (Bản cũ retry bằng FEN ĐẦU VÁN với 0 nước đi => bestmove tính trên bàn
        #    cờ lúc khai cuộc, gửi lên server là 'NoPieceAtSourcePosition'.)
        if not raw_bestmove_line:
            # Engine chết HOẶC không kịp trả lời -> khởi động lại (nếu cần) và thử lại
            # tối đa 2 lần với ĐÚNG ván đang chơi. Không bao giờ nạp lại ván khác.
            for _attempt in (1, 2):
                if self._engine_proc is None or self._engine_proc.poll() is not None:
                    print(f"[ENGINE] ⚠️  Engine chết! Khởi động lại (lần {_attempt}) "
                          f"rồi nạp lại đúng ván đang chơi...", flush=True)
                    self._init_engine()
                    if not self.engine:
                        break
                else:
                    print(f"[ENGINE] ⚠️  Engine không trả lời kịp -> thử lại (lần {_attempt})", flush=True)
                raw_bestmove_line = self.get_best_move(fen, moves, fixed_positions=fixed)
                if raw_bestmove_line:
                    break
        if not raw_bestmove_line:
            print("[ENGINE] ❌ Không lấy được nước đi -> bỏ lượt tính này", flush=True)
            return

        parts = raw_bestmove_line.split()
        if len(parts) < 2: return
        best_move = parts[1]
        print(f"[ENGINE-OUT] bestmove: {best_move}  (raw: {raw_bestmove_line})", flush=True)

        # ★ ĐÃ BỎ HOÀN TOÀN "RAM-LEARN THAY THẾ NƯỚC ĐI".
        #   Bot dùng THẲNG bestmove của PKJQ — đó đã là kết quả tìm kiếm sâu nhất.
        #   (Lớp cũ đọc điểm từ các dòng info rồi ghi đè bestmove => đi yếu hẳn.)

        # ★ CHỈ NGOẠI LỆ DUY NHẤT: nước này server ĐÃ TỪ CHỐI ở lượt hiện tại.
        #   Khi đó mới dùng MultiPV để lấy nước xếp hạng kế tiếp của engine.
        if best_move in self._rejected_moves:
            print(f"[ENGINE] ⚠️ '{best_move}' đã bị server từ chối -> tìm nước khác qua MultiPV", flush=True)
            alt = self._next_candidate_excluding(fen, moves, self._rejected_moves)
            if alt:
                print(f"[ENGINE] ✅ Dùng nước thay thế (hạng kế tiếp của engine): {alt}", flush=True)
                best_move = alt

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
                    self._last_sent_move = best_move
                    self.send_play(source_pos, target_pos)
            except Exception as e: print(f"[BOT ERROR] Dịch tọa độ lỗi: {e}")

    def _decode_piece_id(self, encoded_id):
        """Decode encoded_id thành "{color}{type}{instance}".

        CỜ ÚP trên gamevh.net: ENCODING GIỐNG CỜ TƯỚNG (positive=red, negative=black).
        Nhưng LAYOUT BÀO ĐẢO: BLACK king ở pos 85 (bottom), RED king ở pos 4 (top)
        — ngược với cờ tướng chuẩn (red bottom, black top).

        Từ START_MATCH thực tế:
        - piece[7]  sid=0xf8 (-8) pos=85 → 'b1' BLACK king (bottom) ✓
        - piece[31] sid=0x08 (+8) pos=4  → 'r1' RED king   (top)    ✓

        → Dùng encoding chuẩn (positive=red, negative=black). KHÔNG đảo color.
        """
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

        # BỎ CHUYỂN XU — user yêu cầu gỡ phần transfer xu
        # (vốn là từ transfer_xu_bot.transfer_xu_sync → 10055407, giờ không dùng)

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
                        print(f"[BOT] ⚠️ Nhiều khả năng tài khoản {USER} đang được ĐĂNG NHẬP Ở NƠI KHÁC "
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
                        elif BOT_USE_CREATE_TABLE:
                            # ★ CỜ ÚP TEST: tạo bàn thẳng (CREATE_RULE) thay vì QUICK_PLAY
                            bid = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
                            print(f"[CREATE] 🪑 Tạo bàn cờ úp {BOT_BET_XU} xu (bet_amt_id={bid})...")
                            self.send_create_table(bet_amt_id=bid)
                        else:
                            valid_bets = self.get_1k_to_5k_bet_objs()
                            if valid_bets:
                                bet_obj = random.choice(valid_bets)
                                room = random.choice(self.ROOM_LIST)
                                print(f"[SEARCH] 🔍 Dò bàn: {bet_obj['value']} xu (Bet ID {bet_obj['id']}) ở Phòng '{room}'")
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
        self._kill_engine()
        if self.ws:
            try: self.ws.close()
            except: pass

def acquire_single_instance_lock():
    """Chặn chạy 2 tiến trình bot cùng tài khoản trên cùng máy.

    Server gamevh chỉ cho 1 phiên/tài khoản: phiên mới đăng nhập sẽ ĐÁ phiên cũ ra
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
            print(f"[BOT] ❌ Đã có một bot khác đang chạy với tài khoản {USER} (khoá: {path}).")
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
