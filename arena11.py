#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  BOT CARO - SET_TURN Timer Monitor v4.0                            ║
║  Engine: Embryo Caro6 v1.2.3 (Linux Native)                        ║
║  Mục đích:                                                       ║
║  - Chơi Caro tự động trên gamevh.net                             ║
║  - HUNT: hết ván đóng WS + đổi tên/avatar + WS mới + hunt lại   ║
║  - Monitor SET_TURN packets, phát hiện timer reset bug           ║
║  - SET_READY handling chính xác                                   ║
╚══════════════════════════════════════════════════════════════════════╝
"""
import subprocess, sys, os, importlib, urllib.request, json, time, struct
import re, logging, asyncio, random, threading, shutil, selectors, html as html_lib
from typing import List, Tuple, Dict, Optional
from pathlib import Path
from urllib.parse import urljoin

# ======================== LOGGING ========================
log = logging.getLogger("caro")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)

# ======================== SETUP & IMPORTS ========================
REQUIRED = ["websockets", "requests"]
for pkg in REQUIRED:
    try:
        importlib.import_module(pkg)
    except ImportError:
        print(f"[SETUP] Installing {pkg}...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"],
                           stderr=subprocess.DEVNULL, check=True)
            importlib.import_module(pkg)
        except Exception:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"],
                               stderr=subprocess.DEVNULL, check=True)
                importlib.import_module(pkg)
            except Exception as e:
                print(f"[SETUP] Failed to install {pkg}: {e}")

import websockets, requests

# ======================== SAFE IDENTITY CONFIG ========================
VN_TEN_DAU = [
    "Tuấn", "Minh", "Đức", "Hoàng", "Huy", "Hùng", "Dũng", "Cường", "Long", "Nam",
    "Sơn", "Hải", "Phong", "Thắng", "Trung", "Kiên", "Quân", "Thành", "Đạt", "Khoa",
    "Phúc", "Nghĩa", "Trọng", "Quang", "Bảo", "Khánh", "Hiếu", "Lâm", "Trí", "Thịnh",
    "Lộc", "Phát", "Tiến", "Việt", "Duy", "Vinh", "Phước", "Bình", "Đăng", "Tùng",
    "Vũ", "An", "Bách", "Công", "Đại", "Hiệp", "Hòa", "Hưng", "Khải", "Khang",
    "Khôi", "Mạnh", "Nhật", "Phi", "Phú", "Sang", "Tài", "Tâm", "Thái", "Thuận",
    "Toàn", "Triết", "Tú", "Linh", "Trang", "Lan", "Mai", "Hương", "Ngọc", "Thảo",
    "Vy", "Hân", "Châu", "Nhi", "Yến", "Quỳnh", "Ngân", "Trâm", "Phương", "Huyền",
    "Thúy", "Hằng", "Nga", "Tuyết", "Loan", "Oanh", "Bích", "Diễm", "Kiều", "Liên",
    "Giang", "Quyên", "Như", "Hà", "Xuân", "Mỹ", "Thu", "Ánh", "Dung", "Hiền",
    "Hoa", "Huệ", "Ly", "Nhung", "Thư", "Thương", "Thùy", "Tiên", "Trinh", "Trúc", "Uyên", "Vân"
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

def generate_random_full_name() -> str:
    """arena11: tên VN ngẫu nhiên, KHÔNG dấu chấm (tránh false-positive family + đổi tên mỗi ván)."""
    has_accent = random.choice([True, False])
    name_list = VN_TEN_DAU if has_accent else VN_TEN_KHONG_DAU
    # Đôi khi ghép 2 tiếng cho đỡ trùng (vẫn không có '.')
    if random.random() < 0.35:
        a = random.choice(name_list)
        b = random.choice(name_list)
        if a != b:
            return f"{a} {b}"
    return random.choice(name_list)


# ==================== NHẬN DIỆN ĐỒNG ĐỘI (thống nhất arena + zaro) ====================
_FAMILY_PREFIX_RE = re.compile(
    r'^(?:arena|zaro|nguyen|nguyenpy)\d+[a-z0-9_]*$',
    re.IGNORECASE,
)

def _family_extra_names():
    raw = os.environ.get("FAMILY_EXTRA", "") or ""
    return {x.strip().upper() for x in raw.split(",") if x and x.strip()}

def is_family_name(name, self_names=None, allow_dot_marker=True):
    """True nếu `name` là bot đồng đội (không phải chính mình).

    allow_dot_marker=False: bỏ heuristic '.' — tránh false-positive 'A.n' khiến hunt loop.
    """
    if not name:
        return False
    n = str(name).strip()
    if not n:
        return False
    nu = n.upper()
    self_set = set()
    for x in (self_names or []):
        if x is None:
            continue
        s = str(x).strip()
        if s:
            self_set.add(s.upper())
    if nu in self_set:
        return False
    if _FAMILY_PREFIX_RE.match(n):
        return True
    if nu in _family_extra_names():
        return True
    if allow_dot_marker and "." in n and n.count(".") == 1:
        left, right = n.split(".", 1)
        # Bot generate: tên VN chèn 1 chấm, mỗi phía đủ dài — loại 'A.n', 'J.k'
        if len(left) >= 2 and len(right) >= 2 and len(n) >= 5:
            return True
    return False


# ======================== EMBRYO CONFIG ========================
try:
    _BASE_DIR = Path(__file__).parent
except NameError:
    _BASE_DIR = Path.cwd()

ENGINE_DIR = _BASE_DIR / "embryo-engine"
EMBRYO_BINARY = "pbrain-embryo"
EMBRYO_VERSION = "1.2.0-c6"
EMBRYO_DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/Hexik/Embryo_engine/master/"
    "Caro6/Linux/pbrain-embryo-1.2.0-6f650fab-c6.bz2"
)
EMBRYO_TIMEOUT = 1000
EMBRYO_MOVE_TIMEOUT = 15.0
EMBRYO_MATCH_TIMEOUT = 1800000

def auto_download_embryo() -> Optional[str]:
    binary_path = ENGINE_DIR / EMBRYO_BINARY
    if binary_path.exists():
        try: binary_path.chmod(0o755)
        except Exception: pass
        return str(binary_path)
    log.info(f"[Embryo] Downloading Embryo (Linux Caro6) ...")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import bz2
        bz2_path = str(binary_path) + ".bz2"
        req = urllib.request.Request(EMBRYO_DOWNLOAD_URL, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(bz2_path, "wb") as out:
                out.write(resp.read())
        with open(bz2_path, "rb") as src_bz2, open(binary_path, "wb") as dst:
            dst.write(bz2.decompress(src_bz2.read()))
        try: os.remove(bz2_path)
        except Exception: pass
        try: binary_path.chmod(0o755)
        except Exception: pass
        return str(binary_path)
    except Exception as e:
        log.error(f"[Embryo] Download failed: {e}")
        return None

def detect_embryo_binary() -> Optional[str]:
    if not ENGINE_DIR.exists(): return None
    b = ENGINE_DIR / EMBRYO_BINARY
    if b.exists():
        try: b.chmod(0o755)
        except Exception: pass
        return str(b)
    for f in ENGINE_DIR.glob("pbrain-embryo*"):
        return str(f)
    return None


# ======================== ENGINE WRAPPER ========================
class EmbryoEngine:
    def __init__(self, timeout_turn=5000, board_width=15, board_height=19, match_timeout_ms=1800000):
        self.binary = detect_embryo_binary()
        self.timeout_turn = timeout_turn
        self.match_timeout_ms = match_timeout_ms
        self.time_left_ms = self.match_timeout_ms
        self._match_start_mono = None
        self.board_width = board_width; self.board_height = board_height
        self.proc = None
        self.lock = threading.Lock()
        self._buffer = bytearray()
        self.my_side = 1
        self._initialized = False
        self._selector = None
        self._rectstart_sent = False

    def _init_selector(self):
        self._close_selector()
        if self.proc and self.proc.stdout:
            try:
                self._selector = selectors.DefaultSelector()
                self._selector.register(self.proc.stdout, selectors.EVENT_READ)
            except Exception as e:
                log.warning(f"[Embryo] Selector register error: {e}")
                self._selector = None

    def _close_selector(self):
        if self._selector:
            try: self._selector.close()
            except Exception: pass
            self._selector = None

    def _send_time_infos(self):
        left = self.match_timeout_ms
        self._send(f"INFO timeout_turn {self.timeout_turn}")
        self._send(f"INFO timeout_match {self.match_timeout_ms}")
        self._send(f"INFO time_left {left}")

    def _send(self, cmd: str):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except Exception: pass

    def _read_line(self, timeout=10.0) -> str:
        if not self.proc or self.proc.poll() is not None:
            return ""
        deadline = time.monotonic() + timeout
        while True:
            idx = self._buffer.find(b"\n")
            if idx >= 0:
                line_bytes = bytes(self._buffer[:idx]).strip()
                del self._buffer[:idx + 1]
                return line_bytes.decode("utf-8", errors="replace")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ""
            try:
                if self._selector:
                    ready = self._selector.select(timeout=min(remaining, 0.1))
                else:
                    sel = selectors.DefaultSelector()
                    sel.register(self.proc.stdout, selectors.EVENT_READ)
                    ready = sel.select(timeout=min(remaining, 0.1))
                    sel.close()
                if ready:
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk:
                        return ""
                    self._buffer.extend(chunk)
            except Exception:
                return ""

    def _drain_output(self):
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            line = self._read_line(timeout=0.05)
            if not line:
                break
            if line.startswith(('MESSAGE', 'DEBUG', 'ERROR')):
                log.debug(f'[Embryo] drain: {line}')

    def start_game(self, my_symbol=1) -> bool:
        with self.lock:
            self._match_start_mono = time.monotonic()
            self.time_left_ms = self.match_timeout_ms
            if self.proc and self.proc.poll() is None:
                self._send("RESTART")
                for _ in range(5):
                    line = self._read_line(timeout=0.5)
                    if line.upper() == "OK": break
                self._send("RECTSTART 15,19")
                for _ in range(5):
                    line = self._read_line(timeout=0.5)
                    if line.upper() == "OK": break
                self._synced = False
                self._send_time_infos()
                self._send("INFO ponder 1")
                self.my_side = my_symbol
                self._initialized = True
                log.info("[Embryo] RESTART + RECTSTART (opening book active)")
                return True

            self._synced = False
            self._rectstart_sent = False
            self._stop_unlocked()
            if not self.binary:
                return False
            try:
                cmd = [self.binary]
                self.proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=str(ENGINE_DIR)
                )
                self._buffer = bytearray()
                self._init_selector()
                self.my_side = my_symbol
                self._send("RECTSTART 15,19")
                self._rectstart_sent = True
                for _ in range(10):
                    line = self._read_line(timeout=1.0)
                    if line.upper() == "OK": break
                self._send_time_infos()
                self._send("INFO ponder 1")
                time.sleep(0.2)
                self._drain_output()
                self._initialized = True
                return True
            except Exception as e:
                log.error(f"[Embryo] Start error: {e}")
                self._initialized = False
                return False

    def restart_game(self) -> bool:
        with self.lock:
            if not self._initialized or not self.proc or self.proc.poll() is not None:
                return False
            self._match_start_mono = time.monotonic()
            self.time_left_ms = self.match_timeout_ms
            self._send("RESTART")
            for _ in range(5):
                line = self._read_line(timeout=2.0)
                if line.upper() == "OK": break
            self._send("RECTSTART 15,19")
            for _ in range(5):
                line = self._read_line(timeout=2.0)
                if line.upper() == "OK": break
            self._synced = False
            self._send_time_infos()
            self._send("INFO ponder 1")
            log.info("[Embryo] RESTART + ponder OK (opening book active)")
            return True

    def get_move(self, board_history: list, my_side: int) -> Optional[Tuple[int, int]]:
        with self.lock:
            try:
                if not self._initialized or not self.proc or self.proc.poll() is not None:
                    return None
                if self._match_start_mono is not None:
                    elapsed_ms = int((time.monotonic() - self._match_start_mono) * 1000)
                    self.time_left_ms = max(self.match_timeout_ms - elapsed_ms, self.timeout_turn)
                else:
                    self._match_start_mono = time.monotonic()
                self._send_time_infos()
                t0 = time.monotonic()
                _sync_state = getattr(self, "_synced", False)
                _hist_len = len(board_history)
                _exp_len = getattr(self, "_expected_history_len", -1)
                can_use_turn = _sync_state and _hist_len == _exp_len + 1
                if not can_use_turn:
                    log.info(f"[Embryo] sync debug: synced={_sync_state} hist={_hist_len} exp={_exp_len} can_turn={can_use_turn}")
                if can_use_turn:
                    last_x, last_y, _ = board_history[-1]
                    self._send(f"TURN {last_x},{last_y}")
                else:
                    self._send("BOARD")
                    for (x, y, sym) in board_history:
                        c = 1 if sym == self.my_side else 2
                        self._send(f"{x},{y},{c}")
                    self._send("DONE")
                deadline = time.monotonic() + (self.timeout_turn / 1000.0) + 2.0
                move_count = 0
                while time.monotonic() < deadline:
                    rem_time = deadline - time.monotonic()
                    line = self._read_line(timeout=min(0.1, rem_time))
                    if not line: continue
                    if line.startswith(("MESSAGE", "ERROR", "DEBUG")):
                        log.info(f"[Embryo] engine msg: {line}")
                        continue
                    match = re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*$", line)
                    if match:
                        mx, my = int(match.group(1)), int(match.group(2))
                        if not (0 <= mx < self.board_width and 0 <= my < self.board_height):
                            log.warning(f"[Embryo] Bỏ qua nước ngoài bàn: {mx},{my}")
                            continue
                        think_ms = int((time.monotonic() - t0) * 1000)
                        move_count += 1
                        if move_count > 1:
                            log.warning(f"[Embryo] Nhận {move_count} nước, dùng nước cuối: {mx},{my}")
                        self._synced = True
                        self._expected_history_len = len(board_history) + 1
                        self.time_left_ms = max(self.time_left_ms - think_ms, self.timeout_turn)
                        log.info(f"[Embryo] Move=({mx},{my}) think={think_ms}ms (max 5s) [sync={'T' if can_use_turn else 'F'}]")
                        return mx, my
                log.warning("[Embryo] Timeout — engine không trả kết quả")
                return None
            except Exception as e:
                log.warning(f"[Embryo] get_move error: {e}")
                self._synced = False
                return None

    def _stop_unlocked(self):
        if self.proc:
            try: self._send("END")
            except Exception: pass
            try:
                self.proc.terminate()
                self.proc.wait(3)
            except Exception:
                try: self.proc.kill()
                except Exception: pass
            self.proc = None
            self._initialized = False
        self._close_selector()

    def stop(self):
        with self.lock:
            self._stop_unlocked()


# ======================== CONSTANTS & CONFIG ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"

CARO_USER_DIRECT = "arena11"  # login nick (đã restore; WS 323 từng đổi tạm sang vu829)
CARO_PWWD_DIRECT = "nhat123456"

def _clean_env(val: Optional[str], default: str) -> str:
    if val and str(val).strip(): return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER1") or os.environ.get("CARO_USER"), CARO_USER_DIRECT)
PWWD = _clean_env(os.environ.get("CARO_PWWD1") or os.environ.get("CARO_PWWD"), CARO_PWWD_DIRECT)

# Username login có thể đã bị WS CHANGE_NICK_NAME đổi (arena11 → vu829).
_LOGIN_USER_CANDIDATES = []
for _u in [USER, CARO_USER_DIRECT, "vu829", "arena11", os.environ.get("CARO_USER_ALT", "")]:
    _u = (_u or "").strip()
    if _u and _u not in _LOGIN_USER_CANDIDATES:
        _LOGIN_USER_CANDIDATES.append(_u)

def _set_active_user(u: str):
    """Cập nhật USER global sau khi login thành công với nick thực tế."""
    global USER
    u = (u or "").strip()
    if u and u != USER:
        log.info(f"[Auth] USER active: {USER!r} → {u!r}")
        USER = u

VERSION = "5.0.2"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or
              float(os.environ.get("CARO_RUNTIME_HOURS", "5.9")) * 3600)
AUTO_IDENTITY = os.environ.get("CARO_AUTO_IDENTITY", "1") == "1"
IDENTITY_TEST_ONLY = os.environ.get("CARO_IDENTITY_TEST_ONLY", "0") == "1"
BOT_BET_XU = 1000
BOT_MATCH_DURATION = '1800'
BOT_TURN_DURATION = '60'

# ---- CHẾ ĐỘ THÍ NGHIỆM: HUNT (chỉ dò bàn, KHÔNG tự tạo) ----
# CARO_MODE=hunt (mặc định thí nghiệm) | create (hành vi cũ tự tạo bàn)
CARO_MODE = os.environ.get("CARO_MODE", "hunt").strip().lower()
HUNT_MODE = CARO_MODE in ("hunt", "scan", "join", "1", "true", "yes")
# Mức cược mục tiêu: 10k / 20k / 40k (csv). Cho phép sai số nhỏ do làm tròn id.
def _parse_bet_list(raw, default):
    try:
        vals = [int(x.strip()) for x in str(raw).split(",") if x.strip()]
        return vals or list(default)
    except Exception:
        return list(default)
HUNT_BETS = _parse_bet_list(os.environ.get("CARO_HUNT_BETS", "40000"),
                            (4000, 10000))
HUNT_BET_TOLERANCE = int(os.environ.get("CARO_HUNT_TOL", "500") or 500)
HUNT_INTERVAL = float(os.environ.get("CARO_HUNT_INTERVAL", "2") or 2)      # nghỉ giữa 2 vòng quét
HUNT_PAUSE = float(os.environ.get("CARO_HUNT_PAUSE", "2") or 2)            # quét hết sảnh không thấy
HUNT_STEP_TIMEOUT = float(os.environ.get("CARO_HUNT_STEP_TIMEOUT", "12") or 12)
HUNT_MAX_ROOMS = int(os.environ.get("CARO_HUNT_MAX_ROOMS", "8") or 8)
HUNT_MAX_CHECKS = int(os.environ.get("CARO_HUNT_MAX_CHECKS", "6") or 6)
# filter LIST_ZONE_TABLE: 0=all, 1=chưa đầy, 2=chưa chơi, 3=đang chơi
HUNT_TABLE_FILTER = int(os.environ.get("CARO_HUNT_FILTER", "1") or 1)
# arena11 riêng: hết ván → đóng WS → đổi tên+avatar → WS mới → hunt lại
HUNT_LEAVE_AFTER = os.environ.get("CARO_HUNT_LEAVE_AFTER", "1") == "1"  # mặc định 1
HUNT_RENAME_AFTER = os.environ.get("CARO_HUNT_RENAME_AFTER", "1") == "1"  # đổi FULL_NAME
HUNT_AVATAR_AFTER = os.environ.get("CARO_HUNT_AVATAR_AFTER", "1") == "1"  # đổi avatar
HUNT_WS_RESTART_AFTER = os.environ.get("CARO_HUNT_WS_RESTART", "1") == "1"  # restart websocket mỗi ván
HUNT_AVOID_FAMILY = os.environ.get("CARO_HUNT_AVOID_FAMILY", "0") == "1"  # 0=không tránh family khi hunt
HUNT_TABLE_COOLDOWN = float(os.environ.get("CARO_HUNT_COOLDOWN", "180") or 180)
HUNT_EMPTY_LEAVE_S = float(os.environ.get("CARO_HUNT_EMPTY_S", "90") or 90)  # ngồi 1 mình quá N giây mới về sảnh

ZONE_BASE = "Lobby.caro"

EMPTY = -1
CIRCLE = 0
CROSS = 1

CMD_MAP = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT", 304: "RIBBON_MESSAGE",
    311: "BROADCAST", 312: "INVITE", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    401: "ENTER_PLACE", 402: "ENTER_CHILD_PLACE", 405: "CREATE_RULE",
    406: "PLAYER_ENTERED", 407: "PLAYER_EXITED", 408: "QUICK_PLAY",
    410: "KICK_PLAYER", 411: "LIST_ZONE_TABLE", 412: "LIST_ZONE_ROOM",
    413: "LIST_BET_AMT", 414: "GET_TABLE_DATA", 417: "START_MATCH",
    418: "GAMEOVER", 419: "ENTER_STATE", 420: "SET_TURN",
    421: "SET_PLAYER_STATUS", 422: "SET_PLAYER_POINT", 423: "SET_PLAYER_ATTR",
    318: "LIST_AVATAR_CATEGORY", 319: "LIST_AVATAR", 320: "BUY_AVATAR",
    321: "REFINE_PROFILE", 322: "GET_NICK_CHANGE_COUNT", 323: "CHANGE_NICK_NAME",
    324: "CHANGE_PASSWORD", 325: "UPDATE_PROFILE",
    431: "BALANCE_CHANGED", 432: "OWNER_CHANGED", 433: "GET_TABLE_DATA_EX",
    434: "SET_READY", 501: "BET", 502: "PLAY", 505: "CHAT", 518: "HIGHLIGHT",
    529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 535: "RETREAT",
}

# WS 323/324 chỉ map opcode — bot KHÔNG gọi đổi nick login / mật khẩu.
# Chỉ đổi FULL_NAME (tên hiển thị) qua HTTP update_profile.


# ======================== BINARY PROTOCOL ========================
class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data; self.pos = 0
    def remaining(self) -> int: return len(self.data) - self.pos
    def u8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = self.data[self.pos]; self.pos += 1; return v
    def i8(self) -> int:
        if self.pos >= len(self.data): return 0
        v = struct.unpack_from('>b', self.data, self.pos)[0]; self.pos += 1; return v
    def i16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>h', self.data, self.pos)[0]; self.pos += 2; return v
    def u16(self) -> int:
        if self.pos + 2 > len(self.data): return 0
        v = struct.unpack_from('>H', self.data, self.pos)[0]; self.pos += 2; return v
    def i32(self) -> int:
        if self.pos + 4 > len(self.data): return 0
        v = struct.unpack_from('>i', self.data, self.pos)[0]; self.pos += 4; return v
    def i64(self) -> int:
        if self.pos + 8 > len(self.data): return 0
        hi = struct.unpack_from('>i', self.data, self.pos)[0]
        lo = struct.unpack_from('>I', self.data, self.pos + 4)[0]
        self.pos += 8; return (hi << 32) | lo
    def read_ascii(self) -> str:
        if self.pos >= len(self.data): return ""
        n = self.u8()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
        self.pos += n; return s
    def read_utf(self) -> str:
        if self.pos + 2 > len(self.data): return ""
        n = self.i16()
        if n <= 0: return ""
        byte_len = n * 2
        if self.pos + byte_len > len(self.data): byte_len = len(self.data) - self.pos
        s = self.data[self.pos:self.pos + byte_len].decode('utf-16-be', 'replace')
        self.pos += byte_len; return s
    def read_bytes(self) -> List[int]:
        if self.pos + 2 > len(self.data): return []
        n = self.i16()
        if self.pos + n > len(self.data): n = len(self.data) - self.pos
        result = list(self.data[self.pos:self.pos + n])
        self.pos += n; return result
    def read_command(self) -> str:
        first = self.i8()
        if first < 0:
            n = -first
            if self.pos + n > len(self.data): n = len(self.data) - self.pos
            s = self.data[self.pos:self.pos + n].decode('ascii', 'replace')
            self.pos += n; return s
        second = self.u8()
        cmd_id = (first << 8) | second
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")

class BinaryWriter:
    def __init__(self): self.parts = []
    def u8(self, v: int): self.parts.append(struct.pack('>B', v))
    def i8(self, v: int): self.parts.append(struct.pack('>b', v))
    def i16(self, v: int): self.parts.append(struct.pack('>h', v))
    def i32(self, v: int): self.parts.append(struct.pack('>i', v))
    def i64(self, v: int): self.parts.append(struct.pack('>q', v))
    def write_ascii(self, s: str):
        encoded = s.encode('ascii', 'replace'); self.u8(len(encoded)); self.parts.append(encoded)
    def write_utf(self, s: str):
        encoded = s.encode('utf-16-be'); self.i16(len(encoded) // 2); self.parts.append(encoded)
    def write_command(self, cmd: str):
        cmd_id = next((k for k, v in CMD_MAP.items() if v == cmd), None)
        if cmd_id: self.parts.append(struct.pack('>H', cmd_id))
        else:
            b = cmd.encode('ascii'); self.i8(-len(b)); self.parts.append(b)
    def build(self) -> bytes: return b''.join(self.parts)


# ======================== SET_TURN TIMER TRACKER ========================
class SetTurnTracker:
    """
    Theo dõi SET_TURN packets, phát hiện timer reset bug.
    Ghi nhận chi tiết: khi gameover, người vào/ra, timer có reset về 0 không.
    """
    def __init__(self):
        self.timer_start = None
        self.timer_duration = 0
        self.current_slot = -99
        self.entries = []       # Chi tiết trong ván hiện tại
        self.all_logs = []      # Lưu mọi ván
        self.bug_events = []    # Bằng chứng bug
        self.timeline = []      # Timeline tổng hợp

    def on_set_turn(self, slot_id: int, turn_timeout: int, acc_timeout: int,
                    context: str = "", ts: float = None):
        ts = ts or time.time()
        old_start = self.timer_start
        old_dur = self.timer_duration

        # Mô phỏng client: startTimer() → timerStart = now()
        self.timer_start = ts
        self.timer_duration = turn_timeout
        prev_slot = self.current_slot
        self.current_slot = slot_id

        # Tính progress TRƯỚC khi reset
        if old_start is not None and old_dur > 0:
            old_progress = min((ts - old_start) / old_dur, 1.0)
            old_remaining = max(0, old_dur - (ts - old_start))
        else:
            old_progress = 0
            old_remaining = 0

        entry = {
            "idx": len(self.entries) + 1,
            "time": ts,
            "ts": time.strftime("%H:%M:%S", time.localtime(ts)),
            "slot": slot_id,
            "prev_slot": prev_slot,
            "timeout": turn_timeout,
            "acc": acc_timeout,
            "old_progress": round(old_progress * 100, 1),
            "old_remaining": round(old_remaining, 1),
            "context": context,
        }
        self.entries.append(entry)
        self.timeline.append(entry)

        # Kiểm tra lặp cùng slot
        is_repeat = False
        if len(self.entries) >= 2:
            prev = self.entries[-2]
            if prev["slot"] == slot_id:
                is_repeat = True
                gap_ms = (ts - prev["time"]) * 1000
                bug = {
                    "game": len(self.all_logs),
                    "idx": entry["idx"],
                    "slot": slot_id,
                    "gap_ms": round(gap_ms, 1),
                    "old_progress_pct": round(old_progress * 100, 1),
                    "old_remaining_s": round(old_remaining, 1),
                    "context": context,
                    "ts": entry["ts"],
                }
                self.bug_events.append(bug)

        return {
            "is_repeat": is_repeat,
            "old_progress": old_progress,
            "old_remaining": old_remaining,
            "entry": entry,
        }

    def on_gameover(self):
        if self.entries:
            self.all_logs.append(list(self.entries))

    def on_new_game(self):
        if self.entries:
            self.all_logs.append(list(self.entries))
        self.entries.clear()
        self.timer_start = None
        self.timer_duration = 0
        self.current_slot = -99

    def print_game_summary(self, game_num: int):
        log.info(f"{'─' * 60}")
        log.info(f"📊 GAME #{game_num} SET_TURN SUMMARY")
        log.info(f"{'─' * 60}")
        total = len(self.entries)
        repeats = sum(1 for i in range(1, total)
                      if self.entries[i]["slot"] == self.entries[i-1]["slot"])
        log.info(f"  Total SET_TURN: {total}")
        log.info(f"  Same-slot repeats: {repeats}")

        if repeats > 0:
            log.info(f"  ❌ TIMER RESET BUG DETECTED! {repeats} lần lặp cùng slot")
        else:
            log.info(f"  ✅ Timer bình thường, không lặp cùng slot")

    def print_final_summary(self):
        total_turns = sum(len(l) for l in self.all_logs)
        total_bugs = len(self.bug_events)

        log.info(f"\n{'=' * 60}")
        log.info(f"📊 FINAL SUMMARY - SET_TURN TIMER ANALYSIS")
        log.info(f"{'=' * 60}")
        log.info(f"  Games played: {len(self.all_logs)}")
        log.info(f"  Total SET_TURN packets: {total_turns}")
        log.info(f"  Bug events (same-slot repeat): {total_bugs}")

        if self.bug_events:
            log.info(f"\n  ❌❌ BUG EVIDENCE ({total_bugs} events):")
            for b in self.bug_events:
                log.info(f"    Game #{b['game']} | #{b['idx']} | slot={b['slot']} | "
                         f"gap={b['gap_ms']}ms | {b['old_progress_pct']}%→0% "
                         f"(lost {b['old_remaining_s']}s) | {b['context']}")
        else:
            log.info(f"\n  ✅ No bug detected in this session")

        # Phân loại bug theo context
        contexts = {}
        for b in self.bug_events:
            ctx = b["context"]
            contexts[ctx] = contexts.get(ctx, 0) + 1
        if contexts:
            log.info(f"\n  Bug breakdown by context:")
            for ctx, count in sorted(contexts.items(), key=lambda x: -x[1]):
                log.info(f"    {ctx}: {count} lần")

        # Xuất JSON
        out = {
            "games": len(self.all_logs),
            "total_set_turns": total_turns,
            "total_bugs": total_bugs,
            "bug_events": self.bug_events,
            "all_logs": self.all_logs,
        }
        with open("set_turn_analysis.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        log.info(f"\n  💾 Full log: set_turn_analysis.json")


# ======================== BOARD ========================
class Board:
    def __init__(self, width: int = 15, height: int = 19):
        self.width = width; self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history = []; self.placed = set()

    def resize(self, width: int, height: int):
        self.width = width; self.height = height
        self.grid = [[EMPTY] * width for _ in range(height)]
        self.history.clear(); self.placed.clear()

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height: return self.grid[y][x]
        return EMPTY

    def put(self, x: int, y: int, symbol: int):
        if self.get(x, y) == EMPTY and 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = symbol; self.history.append((x, y, symbol)); self.placed.add((x, y))

    def undo(self, x: int, y: int):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.grid[y][x] = EMPTY
            if self.history and self.history[-1][:2] == (x, y): self.history.pop()
            self.placed.discard((x, y))

    def xy_to_pos(self, x: int, y: int) -> int: return y * self.width + x
    def pos_to_xy(self, pos: int) -> tuple: return pos % self.width, pos // self.width

    def load_rle(self, data: List[int]):
        self.grid = [[EMPTY] * self.width for _ in range(self.height)]
        self.history.clear(); self.placed.clear()
        pos = 0
        for value in data:
            symbol = value - 256 if value > 127 else value
            if symbol >= 0:
                y, x = pos // self.width, pos % self.width
                if 0 <= x < self.width and 0 <= y < self.height:
                    self.grid[y][x] = symbol; self.placed.add((x, y))
                pos += 1
            else: pos += -symbol
        for y in range(self.height):
            for x in range(self.width):
                s = self.grid[y][x]
                if s >= 0: self.history.append((x, y, s))

    def get_empty_near_center(self) -> tuple:
        cx, cy = self.width // 2, self.height // 2
        for r in range(max(self.width, self.height)):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.width and 0 <= y < self.height and self.grid[y][x] == EMPTY:
                        return (x, y)
        return (0, 0)

    def get_empty_near(self, x0: int, y0: int) -> tuple:
        for r in range(10):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    x, y = x0 + dx, y0 + dy
                    if 0 <= x < self.width and 0 <= y < self.height:
                        if self.grid[y][x] == EMPTY:
                            return (x, y)
        return self.get_empty_near_center()


# ======================== BOT ========================
class CaroBot:
    def __init__(self):
        self.ws = None; self.board = Board(width=15, height=19)
        self.slot = -1; self.my_symbol = CROSS; self.opponent_symbol = CIRCLE
        self.is_playing = False; self.in_table = False; self.ready = False
        self.players = {}; self.nickname = ""; self.token = 0; self.cookie = ""
        self.place_path = "Lobby.caro.0"; self.lock_key = ""
        self.start_time = None; self.last_activity = time.time(); self._running = True
        self.wins = 0; self.losses = 0; self.draws = 0; self.total_games = 0
        self.pending_move = False
        self.bet_amts = []; self._resolved_bet_id = None
        self._bet_amts_loaded = False; self._joining_table = False

        self.engine = None; self.embryo_available = False
        self.embryo_moves = 0; self.embryo_errors = 0; self.embryo_fallback_count = 0
        self._moving = False; self._last_move_xy = None
        self._embryo_reinit_attempts = 0
        self._embryo_reinit_cooldown_until = 0.0
        self._pending_opponent_moves = []

        self.table_id = None
        self.player_slot_by_id = {}
        self._pending_kick_player_id = None
        self.opponent_gone_at = None
        self._table_lost_at = None
        self._want_rejoin = False; self._rejoining = False; self._rejoin_attempts = 0

        self._identity_attempted = False
        self.identity_result = {}

        # === SET_TURN TIMER TRACKER ===
        self.timer_tracker = SetTurnTracker()

        # === HUNT (dò bàn 10k → 100k) ===
        self._current_room_id = 0
        self._scan_state = None          # None|'room_list'|'enter_room'|'table_list'|'checking'
        self._scan_step_at = 0.0
        self._scan_next_at = 0.0
        self._scan_rooms = []
        self._scan_target_room = None
        self._scan_candidates = []       # [(priority, tid, bet, name), ...]
        self._scan_checks = 0
        self._join_is_scan = False
        self._enter_kind = None          # 'room' | 'table' | None
        self.player_id = 0
        # Khóa hunt khi ĐÃ VÀO BÀN — chỉ mở lại khi thật sự về sảnh
        self._seated = False
        self._sit_alone_since = None
        self._join_lock_until = 0.0      # chặn leave/hunt ngay sau khi join
        self._table_cooldown = {}       # table_id -> expire ts
        self._ws_restart_requested = False  # hết ván: đóng WS để đổi identity rồi vào lại
        self._post_game_reason = ""

    def init_engine(self):
        if self.engine is not None: return self.embryo_available
        binary = detect_embryo_binary()
        if not binary:
            binary = auto_download_embryo()
        if not binary:
            log.warning("[Embryo] No binary found!")
            self.embryo_available = False
            return False
        try:
            self.engine = EmbryoEngine(timeout_turn=EMBRYO_TIMEOUT, board_width=15, board_height=19)
            self.engine.binary = binary
            ok = self.engine.start_game(my_symbol=self.my_symbol)
            if ok:
                self.embryo_available = True
                log.info(f"[Embryo] Embryo v{EMBRYO_VERSION} OK! (pbrain-embryo Linux - caro6)")
            else:
                self.embryo_available = False
                log.warning("[Embryo] Start failed!")
            return self.embryo_available
        except Exception as e:
            log.error(f"[Embryo] Init error: {e}")
            self.embryo_available = False
            return False

    @property
    def running(self) -> bool: return self._running

    def stop(self):
        self._running = False
        if self.engine: self.engine.stop(); self.engine = None; self.embryo_available = False

    def _hard_reset_engine(self, reason: str = ""):
        try:
            if self.engine is not None:
                self.engine.stop()
        except Exception as e:
            log.warning(f"[Embryo] _hard_reset_engine stop error: {e}")
        finally:
            self.engine = None
            self.embryo_available = False
        log.warning(f"[Embryo] HARD-RESET engine (reason={reason}) → lượt sau sẽ init_engine() lại")

    def _try_reinit_engine(self) -> bool:
        MAX_REINIT = 3
        COOLDOWN = 15.0
        now = time.time()
        if now < self._embryo_reinit_cooldown_until:
            return False
        if self._embryo_reinit_attempts >= MAX_REINIT:
            log.warning(f"[Embryo] Đã thử reinit {self._embryo_reinit_attempts}x, tạm ngưng.")
            return False
        self._embryo_reinit_attempts += 1
        self._embryo_reinit_cooldown_until = now + COOLDOWN
        log.warning(f"[Embryo] Thử tái khởi tạo engine (lần {self._embryo_reinit_attempts}/{MAX_REINIT})...")
        self._hard_reset_engine("reinit-mid-match")
        ok = self.init_engine()
        if ok:
            log.info("[Embryo] Engine đã phục hồi")
            self._embryo_reinit_attempts = 0
        return ok

    def save_stats(self):
        try:
            with open("/tmp/caro_ag_stats.json", "w") as f:
                json.dump({'W': self.wins, 'L': self.losses, 'D': self.draws, 'G': self.total_games}, f)
        except Exception: pass

    def update_symbols(self):
        self.my_symbol = CIRCLE if self.slot == 0 else CROSS
        self.opponent_symbol = CROSS if self.my_symbol == CIRCLE else CIRCLE
        log.info(f"Slot={self.slot} Me={'X' if self.my_symbol == CROSS else 'O'}")

    # ======================== PACKET BUILDERS ========================
    def make_login(self) -> bytes:
        w = BinaryWriter(); w.write_command("LOGIN"); w.write_ascii(self.nickname)
        w.i32(self.token); w.write_ascii(VERSION); w.write_ascii(self.lock_key)
        w.write_ascii(GAME_ID); w.i8(1); return w.build()

    def make_enter(self, path: str, pw: str = "", mode: int = 1) -> bytes:
        w = BinaryWriter(); w.write_command("ENTER_PLACE"); w.write_ascii(path)
        w.write_utf(pw); w.i8(mode); return w.build()

    def make_list_bet_amt(self) -> bytes:
        w = BinaryWriter(); w.write_command("LIST_BET_AMT"); return w.build()

    def resolve_bet_amt_id(self) -> Optional[int]:
        if not self.bet_amts: return None
        for ba in self.bet_amts:
            if ba['value'] == BOT_BET_XU: return ba['id']
        lower = [ba for ba in self.bet_amts if 0 < ba['value'] <= BOT_BET_XU]
        if lower: return max(lower, key=lambda x: x['value'])['id']
        return 0

    def make_create_rule(self) -> bytes:
        bet_amt_id = self._resolved_bet_id if self._resolved_bet_id is not None else self.resolve_bet_amt_id()
        if bet_amt_id is None: bet_amt_id = 0
        args = [("matchDuration", BOT_MATCH_DURATION), ("turnDuration", BOT_TURN_DURATION),
                ("accDuration", "0"), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_amt_id); w.i8(len(args))
        for name, val in args: w.write_ascii(name); w.write_utf(val)
        return w.build()

    def make_get_table(self) -> bytes:
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA_EX"); w.write_ascii(""); return w.build()

    def make_list_zone_room(self) -> bytes:
        w = BinaryWriter(); w.write_command("LIST_ZONE_ROOM"); return w.build()

    def make_list_zone_table(self, room_filter: int = 1) -> bytes:
        w = BinaryWriter(); w.write_command("LIST_ZONE_TABLE"); w.i8(room_filter); return w.build()

    def make_get_table_data(self, room_id: int, table_id: int) -> bytes:
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA")
        w.write_ascii(f"{ZONE_BASE}.{room_id}.{table_id}"); return w.build()

    def _bet_value_of(self, bet_id: int):
        for ba in self.bet_amts:
            if ba["id"] == bet_id:
                return ba["value"]
        return None

    def _bet_matches_hunt(self, bet) -> bool:
        if bet is None or bet <= 0:
            return False
        for target in HUNT_BETS:
            if abs(int(bet) - int(target)) <= HUNT_BET_TOLERANCE:
                return True
        return False

    def make_play(self, pos: int) -> bytes:
        w = BinaryWriter(); w.write_command("PLAY"); w.i16(pos); return w.build()

    def make_pong(self) -> bytes:
        w = BinaryWriter(); w.write_command("PONG"); return w.build()

    def make_ready(self) -> bytes:
        if self.is_playing: return b''
        w = BinaryWriter(); w.write_command("SET_READY"); return w.build()

    def make_kick_player(self, player_id: int) -> bytes:
        w = BinaryWriter(); w.write_command("KICK_PLAYER"); w.i64(player_id)
        return w.build()

    async def send(self, data: bytes):
        if self.ws and data:
            try: await self.ws.send(data)
            except Exception: pass

    async def create_new_table(self):
        """Hành vi cũ: tự tạo bàn. Ở HUNT_MODE → chuyển sang dò sảnh."""
        if HUNT_MODE:
            if self._seated or self.in_table or self.is_playing:
                log.info("[HUNT] create_new_table bị gọi khi đang ngồi → BỎ QUA (ở lại bàn)")
                self._stop_hunt_activity("create blocked")
                return
            await self.leave_to_lobby_and_hunt("create_new_table→hunt")
            return
        if not self._bet_amts_loaded:
            self._bet_amts_loaded = False
            await self.send(self.make_list_bet_amt())
        else:
            await self.send(self.make_create_rule())

    def _blacklist_table(self, table_id, reason: str = "", cooldown=None):
        if table_id is None:
            return
        tid = str(table_id)
        cd = HUNT_TABLE_COOLDOWN if cooldown is None else float(cooldown)
        now = time.time()
        self._table_cooldown[tid] = now + cd
        self._table_cooldown = {k: v for k, v in self._table_cooldown.items() if v > now}
        log.info(f"[HUNT] 🚫 Blacklist bàn #{tid} {cd:.0f}s ({reason})")

    def _is_table_blacklisted(self, table_id) -> bool:
        if table_id is None:
            return False
        return time.time() < self._table_cooldown.get(str(table_id), 0)

    def _stop_hunt_activity(self, reason: str = ""):
        """Dừng MỌI hoạt động dò bàn (giữ nguyên chỗ đang ngồi)."""
        self._scan_state = None
        self._scan_rooms = []
        self._scan_candidates = []
        self._scan_checks = 0
        self._scan_target_room = None
        self._scan_next_at = time.time() + 86400 * 365  # đừng quét lại cho đến khi unlock
        if reason:
            log.info(f"[HUNT] 🔒 Khóa dò bàn — đã có chỗ ngồi ({reason})")

    def _lock_seat(self, reason: str = ""):
        """Đánh dấu đã vào bàn: không leave/hunt cho đến khi unlock."""
        self._seated = True
        self.in_table = True
        self._joining_table = False
        self._join_is_scan = False
        self._enter_kind = None
        self._sit_alone_since = time.time()
        self._join_lock_until = time.time() + 15.0  # 15s đầu không cho leave vì parse lỗi
        self._stop_hunt_activity(reason or "lock_seat")

    def _unlock_seat(self, reason: str = ""):
        self._seated = False
        self._sit_alone_since = None
        self._join_lock_until = 0.0
        self._scan_next_at = 0.0
        if reason:
            log.info(f"[HUNT] 🔓 Mở khóa dò bàn ({reason})")

    def _can_leave_table(self, reason: str = "", force: bool = False) -> bool:
        if self.is_playing and not force:
            log.info(f"[HUNT] Đang trong ván — không rời bàn ({reason})")
            return False
        if (not force) and time.time() < getattr(self, "_join_lock_until", 0):
            log.info(f"[HUNT] Vừa vào bàn — bỏ qua leave ({reason})")
            return False
        return True

    async def leave_to_lobby_and_hunt(self, reason: str = "", force: bool = False):
        """Rời bàn (nếu có) về sảnh gốc rồi bắt đầu/tiếp tục dò bàn mục tiêu."""
        if not self._can_leave_table(reason, force=force):
            return
        log.info(f"[HUNT] 🚪 Về sảnh dò bàn {HUNT_BETS} xu ({reason})")
        if self.table_id:
            self._blacklist_table(self.table_id, reason)
        self.ready = False
        self.in_table = False
        self._seated = False
        self.table_id = None
        self.players = {}
        self.player_slot_by_id = {}
        self._joining_table = False
        self._join_is_scan = False
        self._rejoining = False
        self._want_rejoin = False
        self.slot = -1
        self._sit_alone_since = None
        self._join_lock_until = 0.0
        self._scan_state = None
        self._scan_candidates = []
        self._scan_rooms = []
        self._scan_checks = 0
        self._scan_next_at = 0.0
        self._enter_kind = None  # KHÔNG đặt 'room' — tránh nhầm với scan enter_room
        # Về Lobby.caro.0
        self.place_path = f"{ZONE_BASE}.0"
        self._current_room_id = 0
        await self.send(self.make_enter(self.place_path, mode=1))

    def _scan_step(self, state: str):
        self._scan_state = state
        self._scan_step_at = time.time()

    async def _scan_begin(self):
        # ĐÃ CÓ CHỖ → tuyệt đối không quét / không quick-play
        if self.is_playing or self.in_table or self._seated or self._joining_table or self._join_is_scan:
            log.info("[HUNT] Bỏ qua quét — đang ngồi/vào bàn")
            self._stop_hunt_activity("already seated")
            return
        # Đang giữa vòng quét → đừng restart (ENTER_PLACE spam từ server)
        if self._scan_state is not None:
            log.info(f"[HUNT] Bỏ qua quét — đang bước '{self._scan_state}'")
            return
        if not self._bet_amts_loaded:
            log.info("[HUNT] Chưa có LIST_BET_AMT → xin danh sách mức cược trước")
            await self.send(self.make_list_bet_amt())
            return
        log.info(f"[HUNT] 🔎 Bắt đầu quét sảnh tìm bàn {HUNT_BETS} xu...")
        self._scan_checks = 0
        self._scan_candidates = []
        self._scan_step('room_list')
        await self.send(self.make_list_zone_room())

    def _scan_pause(self, reason: str, pause=None):
        wait = HUNT_PAUSE if pause is None else pause
        self._scan_state = None
        self._scan_next_at = time.time() + wait
        log.info(f"[HUNT] ⏹ {reason} → nghỉ {wait:.0f}s rồi quét lại")

    def _scan_abort_step(self, reason: str):
        self._scan_state = None
        self._scan_next_at = time.time() + HUNT_INTERVAL
        log.info(f"[HUNT] ⚠️ Vòng quét dừng: {reason}")

    async def _scan_next_room(self):
        self._scan_candidates = []
        while self._scan_rooms:
            rid = self._scan_rooms.pop(0)
            if rid == self._current_room_id:
                self._scan_step('table_list')
                await self.send(self.make_list_zone_table(HUNT_TABLE_FILTER))
                return
            self._scan_target_room = rid
            self._scan_step('enter_room')
            self._enter_kind = 'room'
            await self.send(self.make_enter(f"{ZONE_BASE}.{rid}", mode=1))
            return
        self._scan_pause("đã quét hết sảnh, không có bàn mục tiêu (tới 100k) phù hợp")

    def _pick_scan_tables(self, tables):
        """Lọc bàn: chưa chơi, còn ghế, không pwd, mức cược ∈ HUNT_BETS (tới 100k)."""
        cands = []
        for t in tables:
            if self._is_table_blacklisted(t.get("id")):
                continue
            if t.get("playing"):
                continue
            if t.get("pwd"):
                continue
            if t.get("type", 0) not in (0,):
                # type 0 = bàn thường
                continue
            # slots: số ghế CÒN TRỐNG (theo client). Cần >=1 trống + có người chờ.
            # LIST_ZONE_TABLE: slots==1 thường = 1 ghế trống (bàn 2 người có 1 người).
            slots = t.get("slots", 0)
            if slots < 1:
                continue
            bet = self._bet_value_of(t["bet_id"])
            if not self._bet_matches_hunt(bet):
                continue
            # ưu tiên đúng mức cao hơn (40k > 20k > 10k), rồi gần target
            prio = -int(bet or 0)
            cands.append((prio, t["id"], int(bet or 0), t.get("name") or ""))
        cands.sort(key=lambda c: (c[0], c[1]))
        return cands[:HUNT_MAX_CHECKS]

    async def _scan_try_join(self):
        if not self._scan_candidates:
            await self._scan_next_room()
            return
        if self._scan_checks >= HUNT_MAX_CHECKS:
            self._scan_pause("đã check đủ ứng viên trong vòng này")
            return
        _, tid, bet, name = self._scan_candidates[0]
        self._scan_checks += 1
        self._scan_step('checking')
        log.info(f"[HUNT] 👁 Check bàn #{tid} ({name}) cược {bet:,}xu sảnh #{self._current_room_id}")
        await self.send(self.make_get_table_data(self._current_room_id, tid))

    async def _scan_join_table(self, table_id: int, bet: int, name: str):
        path = f"{ZONE_BASE}.{self._current_room_id}.{table_id}"
        self._joining_table = True
        self._join_is_scan = True
        self._enter_kind = 'table'
        self.table_id = str(table_id)
        # Dừng quét NGAY khi quyết định vào — không để watchdog/response cũ kéo đi chỗ khác
        self._stop_hunt_activity(f"joining #{table_id}")
        self._scan_rooms = []
        self._scan_candidates = []
        log.info(f"[HUNT] ✅ VÀO BÀN #{table_id} ({name}) sảnh #{self._current_room_id} cược {bet:,}xu → {path}")
        await self.send(self.make_enter(path, mode=1))

    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0: return
        if self._moving:
            log.warning("[BOT] do_move đang chạy -> bỏ qua")
            return
        self._moving = True
        self.pending_move = False
        self._last_move_xy = None
        try:
            start = time.time()
            x, y = -1, -1

            if self._pending_opponent_moves:
                log.info(f"[Embryo] Flushing {len(self._pending_opponent_moves)} queued opponent moves")
                self._pending_opponent_moves.clear()

            history = list(self.board.history)

            if not self.embryo_available:
                self._try_reinit_engine()

            if self.embryo_available:
                try:
                    move = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.engine.get_move(history, self.my_symbol)
                        ),
                        timeout=EMBRYO_MOVE_TIMEOUT
                    )

                    if not self.is_playing or not self.running:
                        log.info("[BOT] Ván đã kết thúc trong lúc engine tính, bỏ qua nước đi")
                        return

                    if (move and 0 <= move[0] < self.board.width and 0 <= move[1] < self.board.height
                            and self.board.get(*move) == EMPTY):
                        x, y = move; self.embryo_moves += 1
                    else:
                        self.embryo_errors += 1
                        log.warning(f"[Embryo] Nước không hợp lệ: {move}, fallback gần nước cuối + hard reset")
                        if history:
                            lx, ly = history[-1][0], history[-1][1]
                        else:
                            lx, ly = 7, 9
                        x, y = self.board.get_empty_near(lx, ly)
                        self.embryo_fallback_count += 1
                        self._hard_reset_engine("invalid-move")
                        self._try_reinit_engine()
                except asyncio.TimeoutError:
                    self.embryo_errors += 1
                    log.warning(f"[Embryo] TIMEOUT nước >{EMBRYO_MOVE_TIMEOUT}s → engine treo, reset")
                    self._hard_reset_engine("timeout")
                    self._try_reinit_engine()
                    if history:
                        lx, ly = history[-1][0], history[-1][1]
                    else:
                        lx, ly = 7, 9
                    x, y = self.board.get_empty_near(lx, ly)
                    self.embryo_fallback_count += 1
                except Exception as e:
                    self.embryo_errors += 1; log.warning(f"[Embryo] Error: {e}")
                    self._hard_reset_engine("exception")
                    self._try_reinit_engine()
                    if history:
                        lx, ly = history[-1][0], history[-1][1]
                    else:
                        lx, ly = 7, 9
                    x, y = self.board.get_empty_near(lx, ly)
                    self.embryo_fallback_count += 1
            else:
                if history:
                    lx, ly = history[-1][0], history[-1][1]
                else:
                    lx, ly = 7, 9
                x, y = self.board.get_empty_near(lx, ly)

            elapsed = time.time() - start
            pos = self.board.xy_to_pos(x, y)
            log.info(f"MOVE ({x},{y}) took {elapsed:.2f}s [Embryo]")
            await self.send(self.make_play(pos))
            self._last_move_xy = (x, y)
            self.board.put(x, y, self.my_symbol)
        finally:
            self._moving = False

    # ======================== PACKET HANDLERS ========================
    async def handle(self, raw: bytes):
        r = BinaryReader(raw)
        cmd = r.read_command()
        if cmd != "PING": log.info(f"RECV {cmd}")
        self.last_activity = time.time()
        try:
            if cmd == "PING": await self.send(self.make_pong())
            elif cmd == "LOGIN": await self.handle_login(r)
            elif cmd == "ENTER_PLACE": await self.handle_enter(r)
            elif cmd == "LIST_BET_AMT": await self.handle_list_bet_amt(r)
            elif cmd == "CREATE_RULE": await self.handle_create_rule(r)
            elif cmd == "LIST_ZONE_ROOM": await self.handle_list_zone_room(r)
            elif cmd == "LIST_ZONE_TABLE": await self.handle_list_zone_table(r)
            elif cmd == "GET_TABLE_DATA": await self.handle_get_table_data(r)
            elif cmd == "GET_TABLE_DATA_EX": await self.handle_table(r)
            elif cmd == "START_MATCH": await self.handle_start(r)
            elif cmd == "SET_TURN": await self.handle_turn(r)
            elif cmd == "MOVE": await self.handle_move(r)
            elif cmd == "GAMEOVER": await self.handle_gameover(r)
            elif cmd == "PLAY": await self.handle_play(r)
            elif cmd == "KICK_PLAYER": await self.handle_kick(r)
            elif cmd == "PLAYER_ENTERED": await self.handle_player_enter(r)
            elif cmd == "PLAYER_EXITED": await self.handle_player_exit(r)
        except Exception as e: log.error(f"Error {cmd}: {e}", exc_info=True)

    async def handle_login(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            path = r.read_utf()
            if path == "REFRESH":
                login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                if login_ok: await self.send(self.make_login())
                return
            if r.remaining() > 0: self.lock_key = r.read_ascii()
            await self.send(self.make_enter(self.place_path))
        else:
            log.error(f"LOGIN failed status={status}")

    async def handle_enter(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            # Thành công vào place (sảnh hoặc bàn)
            if self._enter_kind == 'room' and self._scan_state == 'enter_room':
                self._current_room_id = self._scan_target_room if self._scan_target_room is not None else self._current_room_id
                self.place_path = f"{ZONE_BASE}.{self._current_room_id}"
                self._enter_kind = None
                log.info(f"[HUNT] 🏘️ Vào sảnh #{self._current_room_id}")
                self._scan_step('table_list')
                await self.send(self.make_list_zone_table(HUNT_TABLE_FILTER))
                return

            if self._joining_table or self._join_is_scan or self._enter_kind == 'table':
                self._rejoining = False
                self._lock_seat(f"ENTER ok table={self.table_id}")
                log.info(f"[HUNT] 🪑 Đã vào bàn id={self.table_id} — DỪNG dò, ở lại chơi")
                await asyncio.sleep(0.3); await self.send(self.make_get_table())
                return

            if not self.in_table:
                if self._want_rejoin and self.table_id and not HUNT_MODE:
                    self._want_rejoin = False; self._rejoining = True; self._joining_table = True
                    path = f"{self.place_path}.{self.table_id}"
                    log.info(f"[BOT] Thử vào lại bàn cũ: {path}")
                    await self.send(self.make_enter(path))
                else:
                    # Vào sảnh (LOGIN lần đầu hoặc leave_to_lobby) — hoặc gói ENTER đẩy khi đang ngồi
                    if self._seated or self.in_table or self.is_playing:
                        # Server hay đẩy ENTER_PLACE khi người khác ra/vào — BỎ QUA, đừng hunt
                        log.info("[HUNT] Bỏ qua ENTER_PLACE (đang ngồi bàn)")
                        return
                    if self._current_room_id is None:
                        self._current_room_id = 0
                    try:
                        parts = (self.place_path or "").split(".")
                        if len(parts) >= 3 and parts[-1].isdigit():
                            self._current_room_id = int(parts[-1])
                    except Exception:
                        pass
                    if HUNT_MODE and self._bet_amts_loaded:
                        if self._scan_state is not None or self._joining_table or self._join_is_scan:
                            log.info(f"[HUNT] Bỏ qua ENTER_PLACE (scan={self._scan_state})")
                            return
                        self._scan_next_at = 0.0
                        await self._scan_begin()
                    else:
                        self._bet_amts_loaded = False; self._resolved_bet_id = None
                        await self.send(self.make_list_bet_amt())
        else:
            # ENTER fail
            if self._enter_kind == 'room' and self._scan_state == 'enter_room':
                log.info(f"[HUNT] ⚠️ Vào sảnh #{self._scan_target_room} bị từ chối (status={status})")
                self._enter_kind = None
                await self._scan_next_room()
                return
            if self._joining_table or self._join_is_scan:
                was_scan = self._join_is_scan
                self._joining_table = False
                self._join_is_scan = False
                self._enter_kind = None
                if self._rejoining:
                    self._rejoining = False; self._rejoin_attempts += 1; self.table_id = None
                    await asyncio.sleep(1)
                    if HUNT_MODE:
                        await self.leave_to_lobby_and_hunt("rejoin fail")
                    else:
                        await self.send(self.make_list_bet_amt())
                elif was_scan:
                    log.info(f"[HUNT] ❌ Vào bàn thất bại status={status} → thử bàn khác")
                    self.table_id = None
                    if self._scan_candidates:
                        await self._scan_try_join()
                    else:
                        await self._scan_next_room()
                else:
                    await asyncio.sleep(1)
                    if HUNT_MODE:
                        await self.leave_to_lobby_and_hunt("enter fail")
                    else:
                        await self.send(self.make_create_rule())

    async def handle_list_bet_amt(self, r: BinaryReader):
        status = r.i8()
        if status != 0: return
        count = r.i8()
        self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True
        pretty = ", ".join(f"{ba['value']:,}xu(id={ba['id']})" for ba in self.bet_amts[:16])
        log.info(f"[BET] {count} mức cược: {pretty}")
        # Map hunt targets → id
        for t in HUNT_BETS:
            hit = next((ba for ba in self.bet_amts if abs(ba["value"] - t) <= HUNT_BET_TOLERANCE), None)
            if hit:
                log.info(f"[HUNT] Mục tiêu {t:,}xu ↔ bet_id={hit['id']} (value={hit['value']:,})")
            else:
                log.warning(f"[HUNT] ⚠️ Không thấy mức ~{t:,}xu trên server")
        if HUNT_MODE:
            if self._seated or self.in_table or self.is_playing or self._joining_table:
                log.info("[HUNT] Có LIST_BET_AMT nhưng đang ngồi bàn → không quét")
                self._stop_hunt_activity("bet list while seated")
            else:
                self._scan_next_at = 0.0
                await self._scan_begin()
        else:
            await self.send(self.make_create_rule())

    async def handle_create_rule(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            table_id = r.read_ascii()
            self.table_id = table_id; self._rejoin_attempts = 0
            log.info(f"[CREATE_RULE] Bàn mới! id={table_id}")
            await asyncio.sleep(0.5); self._joining_table = False
            await self.send(self.make_get_table())
        else:
            self._joining_table = False

    async def handle_list_zone_room(self, r: BinaryReader):
        if self._seated or self.in_table or self.is_playing:
            return
        if self._scan_state != 'room_list':
            return
        status = r.i8()
        if status != 0:
            self._scan_abort_step(f"LIST_ZONE_ROOM status={status}")
            return
        try:
            count = r.u8()
            rooms = []
            for _ in range(count):
                rid = r.u8()
                name = r.read_utf()
                clients = r.i16()
                tables = r.i16()
                max_tables = r.i16()
                rooms.append({"id": rid, "name": name, "clients": clients,
                              "tables": tables, "max": max_tables})
        except Exception as e:
            self._scan_abort_step(f"parse sảnh lỗi: {e}")
            return
        if not rooms:
            self._scan_pause("server không trả sảnh nào")
            return
        info = ", ".join(f"#{rm['id']}{rm['name']}({rm['clients']}ng/{rm['tables']}b)"
                         for rm in rooms[:12])
        log.info(f"[HUNT] 🏘️ {len(rooms)} sảnh: {info}")
        rooms.sort(key=lambda rm: (0 if rm["id"] == self._current_room_id else 1, -rm["clients"]))
        picked = [rm["id"] for rm in rooms if rm["clients"] > 0 and rm["tables"] > 0]
        if not picked:
            picked = [rm["id"] for rm in rooms if rm["tables"] > 0]
        if not picked:
            picked = [rm["id"] for rm in rooms]
        if not picked:
            self._scan_pause("không có sảnh nào")
            return
        self._scan_rooms = picked[:max(1, HUNT_MAX_ROOMS)]
        await self._scan_next_room()

    async def handle_list_zone_table(self, r: BinaryReader):
        if self._seated or self.in_table or self.is_playing:
            return
        if self._scan_state != 'table_list':
            return
        status = r.i8()
        if status != 0:
            self._scan_abort_step(f"LIST_ZONE_TABLE status={status}")
            return
        try:
            count = r.i32()
            tables = []
            for _ in range(count):
                tid = r.i16()
                name = r.read_utf()
                ttype = r.u8()
                bet_id = r.u8()
                slots = r.u8()
                playing = (r.u8() == 0)   # 0 = đang chơi (client web)
                pwd = (r.u8() == 1)
                tables.append({"id": tid, "name": name, "type": ttype, "bet_id": bet_id,
                               "slots": slots, "playing": playing, "pwd": pwd})
        except Exception as e:
            self._scan_abort_step(f"parse bàn lỗi: {e}")
            return
        self._scan_candidates = self._pick_scan_tables(tables)
        waiting = sum(1 for t in tables if (not t["playing"]) and t["slots"] >= 1)
        if not self._scan_candidates:
            # debug: show bet distribution of waiting tables
            sample = []
            for t in tables:
                if t["playing"] or t["pwd"]:
                    continue
                b = self._bet_value_of(t["bet_id"])
                sample.append(f"#{t['id']}({b}xu,slot={t['slots']})")
                if len(sample) >= 8:
                    break
            log.info(f"[HUNT] 👁 Sảnh #{self._current_room_id}: {count} bàn "
                     f"({waiting} chờ) — không khớp HUNT_BETS (tới 100k). Mẫu: {', '.join(sample) or '—'}")
            await self._scan_next_room()
            return
        pretty = ", ".join(f"#{tid}({bet:,}xu)" for _, tid, bet, _ in self._scan_candidates)
        log.info(f"[HUNT] 🎯 Sảnh #{self._current_room_id}: {len(self._scan_candidates)}/{count} "
                 f"bàn khớp {HUNT_BETS}: {pretty}")
        await self._scan_try_join()

    async def handle_get_table_data(self, r: BinaryReader):
        if self._seated or self.in_table or self.is_playing:
            return
        if self._scan_state != 'checking':
            return
        status = r.i8()
        if status != 0 or not self._scan_candidates:
            if self._scan_candidates:
                self._scan_candidates.pop(0)
                await self._scan_try_join()
            else:
                await self._scan_next_room()
            return
        try:
            owner = r.i64()
            count = r.u8()
            players = []
            for _ in range(count):
                pid = r.i64()
                fname = r.read_utf()
                _avatar = r.read_ascii()
                _avatar_id = r.i16()
                _tag = r.u8()
                chip = r.i64()
                _star = r.i64()
                _score = r.i64()
                _level = r.u8()
                players.append((pid, fname, chip))
        except Exception as e:
            self._scan_abort_step(f"parse người trong bàn lỗi: {e}")
            return
        if not self._scan_candidates:
            await self._scan_next_room()
            return
        _, tid, bet, name = self._scan_candidates.pop(0)
        if not players:
            log.info(f"[HUNT] 👁 Bàn #{tid} ({name}) vừa trống → bỏ qua")
            await self._scan_try_join()
            return
        if self.player_id and owner == self.player_id:
            log.info(f"[HUNT] 🤝 Bàn #{tid} ({name}) là bàn mình → bỏ qua")
            self._blacklist_table(tid, "own", cooldown=30)
            await self._scan_try_join()
            return
        if HUNT_AVOID_FAMILY:
            fam = [f for pid, f, _c in players
                   if pid != getattr(self, "player_id", 0) and self.is_family_bot(f)]
            if fam:
                log.info(f"[HUNT] 🤝 Bàn #{tid} family={fam} → bỏ qua")
                self._blacklist_table(tid, f"family {fam}", cooldown=60)
                await self._scan_try_join()
                return
        # Caro 2 ghế; packet có thể kèm viewer. >=4 chắc đông.
        if len(players) >= 4:
            log.info(f"[HUNT] 👁 Bàn #{tid} đông ({len(players)} người) → bỏ qua")
            await self._scan_try_join()
            return
        if len(players) >= 2:
            log.info(f"[HUNT] 👁 Bàn #{tid} có {len(players)} người — vẫn thử vào")
        who = ", ".join(f"{f or '?'}({_c:,}xu)" for pid, f, _c in players[:2]) or "trống"
        log.info(f"[HUNT] ✅ Chọn bàn #{tid} ({name}) cược {bet:,}xu — {who}")
        await self._scan_join_table(tid, bet, name)

    async def handle_table(self, r: BinaryReader):
        if self._moving:
            log.info("[TABLE] Engine đang tính, bỏ qua board reload")
            return
        try:
            first_byte = r.i8()
            if first_byte != 0:
                msg = r.read_utf().lower() if r.remaining() else ""
                if "not in table" in msg:
                    # Vừa join có thể GET_TABLE sớm → đừng vội leave
                    if self._joining_table or self._join_is_scan or time.time() < self._join_lock_until:
                        log.info("[HUNT] GET_TABLE 'not in table' lúc vừa vào — chờ, không leave")
                        return
                    self.in_table = False
                    self._seated = False
                    self.table_id = None
                    if HUNT_MODE:
                        self._unlock_seat("not in table")
                        await self.leave_to_lobby_and_hunt("not in table")
                    else:
                        await self.create_new_table()
                return

            seat_count = r.u8()
            for _ in range(seat_count):
                r.u8(); r.read_ascii(); r.u8(); child_count = r.u8()
                for _ in range(child_count): r.u8(); r.read_ascii(); r.read_utf(); r.u8(); r.u8()

            r.u8(); self.slot = r.i8(); is_playing = r.u8() == 1
            player_count = r.u8(); self.players = {}
            self.player_slot_by_id = {}

            for _ in range(player_count):
                sid = r.i8(); pid = r.i64(); name = r.read_utf()
                r.u16(); r.read_ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
                self.players[sid] = {'id': pid, 'name': name}
                self.player_slot_by_id[pid] = sid

            current_player = r.i8(); r.i16(); r.i16(); r.u8()
            self.in_table = True
            if self.slot >= 0 or self.table_id:
                # Đã parse được state bàn → khóa hunt chắc chắn
                if not self._seated:
                    self._lock_seat(f"GET_TABLE_EX slot={self.slot}")
                else:
                    self._seated = True
                    self._stop_hunt_activity()

            move_count = r.u8()
            for _ in range(move_count): r.i8(); r.i32()

            width = r.u8(); height = r.u8(); self.board.resize(width, height)
            r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()

            r.u8(); r.u8(); n = r.u8()
            for _ in range(n): r.read_ascii(); r.read_utf()

            has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players.keys())

            self.is_playing = is_playing
            log.info(f"[TABLE] Slot={self.slot} Playing={is_playing} Turn=slot{current_player} seated={self._seated}")

            # Tránh family chỉ khi create mode, hoặc hunt + HUNT_AVOID_FAMILY=1
            if not is_playing and has_opponent and (not HUNT_MODE or HUNT_AVOID_FAMILY):
                for sid, p in list(self.players.items()):
                    if sid == self.slot or sid < 0:
                        continue
                    opp_name = (p or {}).get("name") or ""
                    if self.is_family_bot(opp_name):
                        await self._avoid_family_and_remake(opp_name)
                        return

            if has_opponent:
                self._sit_alone_since = None
            elif self._seated and not is_playing and self.slot >= 0:
                if self._sit_alone_since is None:
                    self._sit_alone_since = time.time()

            if is_playing and current_player == self.slot:
                if not self._moving and not self.pending_move:
                    self.pending_move = True; await self.do_move()
            elif not is_playing and self.slot >= 0:
                if has_opponent:
                    if not self.ready:
                        log.info("[BOT] Đối thủ đã vào ghế → SET_READY! (ở lại bàn, không hunt)")
                        self.ready = True; await self.send(self.make_ready())
                else:
                    if self.ready:
                        log.info("[BOT] Không có đối thủ → Hủy Ready (vẫn ngồi chờ).")
                    self.ready = False
            elif not is_playing and self.slot < 0:
                # Chưa gán ghế (vừa vào / đang xem) — KHÔNG leave, chỉ đợi
                if HUNT_MODE and (self._seated or self._joining_table or self.table_id):
                    log.info("[HUNT] slot<0 nhưng đang giữ bàn — chờ ghế, không rời")
                    self.in_table = True
                    self._seated = True
                    return
                self.in_table = False; self.table_id = None; self._seated = False
                await asyncio.sleep(1)
                if HUNT_MODE:
                    await self.leave_to_lobby_and_hunt("slot<0")
                else:
                    await self.send(self.make_list_bet_amt())

            self._rejoining = False
        except Exception as e: log.error(f"Table error: {e}")

    async def handle_start(self, r: BinaryReader):
        self.total_games += 1; self.is_playing = True; self.ready = False; self.pending_move = False
        self.in_table = True
        self._seated = True
        self._sit_alone_since = None
        self._stop_hunt_activity("START_MATCH")
        self._moving = False; self._last_move_xy = None
        self._pending_opponent_moves = []
        self.opponent_gone_at = None
        self._embryo_reinit_attempts = 0
        self._embryo_reinit_cooldown_until = 0.0

        # Timer tracker: ván mới
        self.timer_tracker.on_new_game()

        player_count = r.u8()
        for i in range(player_count):
            r.i8(); r.i32()

        width = r.u8(); height = r.u8(); self.board.resize(width, height)
        r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()

        log.info(f"=== GAME {self.total_games} === Me={'X' if self.my_symbol == CROSS else 'O'}")

        if self.engine is None:
            self.init_engine()
        else:
            self.engine.start_game(my_symbol=self.my_symbol)

        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8(); turn_timeout = r.i16(); acc_timeout = r.i16()

        # === TIMER TRACKING ===
        context = "game" if self.is_playing else "lobby"
        result = self.timer_tracker.on_set_turn(
            sid, turn_timeout, acc_timeout, context=context
        )

        # Log timer state
        if result["is_repeat"]:
            log.info(f"  ❌ SET_TURN LẶP: slot={sid} gap={result['entry']['idx']} "
                     f"progress {result['old_progress']*100:.1f}%→0% "
                     f"(mất {result['old_remaining']:.1f}s)")

        if self.slot < 0: return
        if sid == self.slot and self.is_playing and self.running:
            if not self.pending_move and not self._moving:
                self.pending_move = True; await asyncio.sleep(0.5); await self.do_move()

    async def handle_move(self, r: BinaryReader):
        pos = r.i16(); symbol = r.i8()
        x, y = self.board.pos_to_xy(pos)

        current = self.board.get(x, y)
        if current == symbol:
            if symbol == self.my_symbol and self._last_move_xy is not None:
                self._last_move_xy = None
        elif current != EMPTY and current != symbol:
            self.my_symbol = symbol
            self.opponent_symbol = CROSS if symbol == CIRCLE else CIRCLE
            self.board.undo(x, y); self.board.put(x, y, symbol)
        else:
            self.board.put(x, y, symbol)

        if self._moving:
            self._pending_opponent_moves.append((x, y, symbol))
            log.info(f"[MOVE] Queued opponent move ({x},{y}) while engine thinking")

    async def handle_play(self, r: BinaryReader):
        status = r.i8()
        if status != 0:
            log.warning(f"PLAY error {status}")
            self.pending_move = False
            if self._last_move_xy:
                self.board.undo(*self._last_move_xy)
                self._last_move_xy = None
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_gameover(self, r: BinaryReader):
        self.is_playing = False; self.pending_move = False
        self.opponent_gone_at = None

        # Timer tracker: gameover
        self.timer_tracker.on_gameover()
        self.timer_tracker.print_game_summary(self.total_games)

        player_count = r.u8(); my_result = None
        results = {}
        for _ in range(player_count):
            sid = r.i8(); result = r.i8(); r.i64()
            results[sid] = result
            if sid == self.slot: my_result = result

        bot_lost = my_result in (2, 4, 12)
        if my_result in (1, 11): self.wins += 1; log.info(">>> WIN! <<<")
        elif bot_lost: self.losses += 1; log.info(">>> LOSE! <<<")
        else: self.draws += 1; log.info(">>> DRAW! <<<")

        r.read_utf()
        self.save_stats()

        if self._table_lost_at is not None:
            self._table_lost_at = None
            await asyncio.sleep(1)
            if HUNT_MODE and (HUNT_LEAVE_AFTER or HUNT_RENAME_AFTER):
                await self.rename_after_game_and_rehunt("table lost")
            elif HUNT_MODE:
                await self.leave_to_lobby_and_hunt("table lost @gameover", force=True)
            else:
                await self.create_new_table()
            return

        # arena11 hunt: hết ván → (kick nếu thua) → rời bàn → đổi tên no-dot → hunt lại
        if HUNT_MODE and (HUNT_LEAVE_AFTER or HUNT_RENAME_AFTER):
            if bot_lost:
                winner_sid = next((sid for sid, result in results.items()
                                   if sid != self.slot and sid >= 0 and result in (1, 11)), None)
                if winner_sid is None:
                    winner_sid = next((sid for sid in self.players
                                       if sid != self.slot and sid >= 0), None)
                winner = self.players.get(winner_sid) if winner_sid is not None else None
                winner_id = winner.get('id') if winner else None
                if winner_id is not None:
                    log.info(f"[BOT] Bot thua; kick {winner.get('name', winner_id)} rồi đổi tên + hunt...")
                    asyncio.create_task(self._delay_kick(winner_id, 3.0))
                    await asyncio.sleep(5.0)
                else:
                    await asyncio.sleep(1.5)
            else:
                await asyncio.sleep(1.5)
            if not self.is_playing:
                await self.rename_after_game_and_rehunt("gameover")
            return

        # create-mode / stay-mode cũ
        if bot_lost:
            winner_sid = next((sid for sid, result in results.items()
                               if sid != self.slot and sid >= 0 and result in (1, 11)), None)
            if winner_sid is None:
                winner_sid = next((sid for sid in self.players
                                   if sid != self.slot and sid >= 0), None)
            winner = self.players.get(winner_sid) if winner_sid is not None else None
            winner_id = winner.get('id') if winner else None
            if winner_id is not None:
                log.info(f"[BOT] Bot thua; kick người thắng {winner.get('name', winner_id)} sau 5 giây...")
                asyncio.create_task(self._delay_kick(winner_id, 5.0))
                self._seated = True
                self.in_table = True
                self._stop_hunt_activity("lose stay")
                return
            log.warning("[BOT] Bot thua nhưng không tìm thấy playerId người thắng; chuyển sang sẵn sàng")

        log.info("[BOT] Ở lại bàn, ready sau 5s")
        self._seated = True
        self.in_table = True
        self._stop_hunt_activity("gameover stay")
        asyncio.create_task(self._delay_ready(5.0))

    async def handle_kick(self, r: BinaryReader):
        status = r.i8(); content = r.read_utf()
        if self._pending_kick_player_id is not None:
            player_id = self._pending_kick_player_id
            self._pending_kick_player_id = None
            if status == 0:
                log.info(f"[BOT] Kick playerId={player_id} thành công: {content}")
            else:
                log.warning(f"[BOT] Kick playerId={player_id} thất bại ({status}): {content}")
            await asyncio.sleep(1)
            if self.in_table: await self.send(self.make_get_table())
            return
        log.warning(f"[BOT] Bot bị kick khỏi bàn: {content}")
        self.is_playing = False; self.in_table = False; self.pending_move = False
        self.table_id = None
        self._unlock_seat("kicked")
        self._join_lock_until = 0.0
        await asyncio.sleep(1)
        if HUNT_MODE:
            await self.leave_to_lobby_and_hunt("bị kick")
        else:
            await self.create_new_table()

    async def _delay_kick(self, player_id: int, delay: float):
        await asyncio.sleep(delay)
        if self.is_playing or not self.in_table: return
        if not any(sid != self.slot and sid >= 0 and player.get('id') == player_id
                   for sid, player in self.players.items()):
            log.info(f"[BOT] Bỏ kick playerId={player_id}: người chơi không còn ở bàn")
            return
        self.ready = False
        self._pending_kick_player_id = player_id
        log.info(f"[BOT] Gửi KICK_PLAYER playerId={player_id}")
        await self.send(self.make_kick_player(player_id))
        await asyncio.sleep(3)
        if self._pending_kick_player_id == player_id:
            self._pending_kick_player_id = None
            log.warning(f"[BOT] KICK_PLAYER playerId={player_id} không có response sau 3 giây")
            if self.in_table: await self.send(self.make_get_table())

    async def _delay_ready(self, delay: float):
        await asyncio.sleep(delay)
        if not self.is_playing and self.in_table:
            await self.send(self.make_get_table())
            if not self.is_playing and self.in_table:
                self.ready = True
                await self.send(self.make_ready())


    async def handle_player_enter(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64(); name = r.read_utf()
        if r.remaining() >= 36:
            r.i64(); r.i64(); r.read_ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()

        if place_level < 4: return
        log.info(f"[BOT] {name} vào bàn (lv={place_level}) → cập nhật")
        # ROOT CAUSE FIX: hunt mặc định KHÔNG avoid theo '.' (A.n false-positive → loop vào/ra)
        should_avoid = (
            (not self.is_playing)
            and self.is_family_bot(name)
            and ((not HUNT_MODE) or HUNT_AVOID_FAMILY)
        )
        if should_avoid:
            await self._avoid_family_and_remake(name)
            return
        await self.send(self.make_get_table())

    async def handle_player_exit(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64() if r.remaining() >= 8 else -1
        if place_level < 4: return

        slot = self.player_slot_by_id.get(pid) if pid >= 0 else None
        if pid >= 0: self.player_slot_by_id.pop(pid, None)
        if slot is not None: self.players.pop(slot, None)

        if slot is not None and slot == self.slot:
            if self.is_playing:
                self.in_table = False; self._table_lost_at = time.time()
            else:
                self.in_table = False
                self._unlock_seat("self exit")
                await asyncio.sleep(1)
                if HUNT_MODE:
                    await self.leave_to_lobby_and_hunt("self exit")
                else:
                    await self.create_new_table()
        elif self.is_playing:
            if self.opponent_gone_at is None:
                self.opponent_gone_at = time.time()
                log.info("[BOT] Đối thủ rời giữa ván → chờ GAMEOVER")
        elif self.in_table:
            log.info("[BOT] Có người rời bàn → cập nhật trạng thái...")
            await self.send(self.make_get_table())

    # ======================== WATCHDOG ========================
    async def watchdog(self):
        while self.running:
            try: await asyncio.sleep(10)
            except asyncio.CancelledError: return
            if not self.running: return

            if self.start_time and time.time() - self.start_time > RUNTIME:
                self.save_stats(); self.stop(); return

            if not self.ws or self.ws.close_code is not None: continue

            try:
                if (self.opponent_gone_at is not None and self.is_playing
                    and time.time() - self.opponent_gone_at > 15):
                    self.opponent_gone_at = None
                    await self.send(self.make_get_table())

                if (self._table_lost_at is not None
                    and time.time() - self._table_lost_at > 8):
                    self._table_lost_at = None; self.table_id = None
                    if HUNT_MODE:
                        await self.leave_to_lobby_and_hunt("table_lost timeout")
                    else:
                        await self.create_new_table()

                now = time.time()
                if HUNT_MODE:
                    # Đang ngồi bàn → chỉ kiểm tra bàn trống quá lâu (đối thủ không vào)
                    if self._seated or self.in_table or self.is_playing or self._joining_table:
                        if (self._seated and not self.is_playing and self.slot >= 0
                                and self._sit_alone_since
                                and now - self._sit_alone_since >= HUNT_EMPTY_LEAVE_S
                                and now >= self._join_lock_until):
                            # Một mình quá lâu → mới về sảnh dò tiếp
                            log.info(f"[HUNT] Ngồi 1 mình >{HUNT_EMPTY_LEAVE_S:.0f}s → về sảnh dò bàn khác")
                            self._unlock_seat("empty table")
                            await self.leave_to_lobby_and_hunt("empty table")
                        continue

                    # Timeout 1 bước quét (chỉ khi CHƯA ngồi)
                    if (self._scan_state and self._scan_step_at
                            and now - self._scan_step_at > HUNT_STEP_TIMEOUT
                            and not self.is_playing and not self.in_table and not self._seated
                            and not self._joining_table):
                        log.info(f"[HUNT] ⏳ Timeout bước '{self._scan_state}' → quét lại")
                        self._scan_state = None
                        self._scan_next_at = 0.0
                        await self._scan_begin()
                    elif (not self.is_playing and not self.in_table and not self._seated
                          and not self._joining_table and not self._join_is_scan
                          and not self._rejoining and self._bet_amts_loaded
                          and self._scan_state is None and now >= self._scan_next_at):
                        await self._scan_begin()
                else:
                    if (not self.is_playing and not self.in_table and not self._joining_table
                        and not self._rejoining and self._bet_amts_loaded):
                        await self.send(self.make_create_rule())
            except Exception: pass

    # ======================== HTTP LOGIN & IDENTITY ========================
    @staticmethod
    def _html_attr(tag: str, name: str) -> str:
        m = re.search(rf'\b{name}\s*=\s*(["\'])(.*?)\1', tag, re.I | re.S)
        return html_lib.unescape(m.group(2)) if m else ""

    def _read_profile_form(self, page_text: str, page_url: str):
        form_match = re.search(
            r'(?is)<form\b[^>]*name=["\']InputForm0["\'][^>]*>.*?</form>',
            page_text)
        if not form_match:
            return None, None
        form = form_match.group(0)
        open_tag = re.search(r'(?is)<form\b[^>]*>', form).group(0)
        action = urljoin(page_url, self._html_attr(open_tag, 'action'))
        data = {}

        for tag in re.findall(r'(?is)<input\b[^>]*>', form):
            name = self._html_attr(tag, 'name')
            input_type = self._html_attr(tag, 'type').lower()
            if not name:
                continue
            # Giữ SAVE (submit); bỏ button/image/file/reset khác
            if input_type in ('button', 'image', 'file', 'reset'):
                continue
            if input_type == 'submit' and name != 'SAVE':
                continue
            if input_type in ('checkbox', 'radio') and not re.search(r'\bchecked\b', tag, re.I):
                continue
            data[name] = self._html_attr(tag, 'value')

        for match in re.finditer(r'(?is)<select\b([^>]*)>(.*?)</select>', form):
            name = self._html_attr('<select ' + match.group(1) + '>', 'name')
            if not name: continue
            selected = re.search(
                r'(?is)<option\b([^>]*\bselected\b[^>]*)>(.*?)</option>',
                match.group(2))
            if selected:
                data[name] = self._html_attr('<option ' + selected.group(1) + '>', 'value')

        for match in re.finditer(r'(?is)<textarea\b([^>]*)>(.*?)</textarea>', form):
            name = self._html_attr('<textarea ' + match.group(1) + '>', 'name')
            if name:
                data[name] = html_lib.unescape(match.group(2)).strip()
        return action, data


    def is_family_bot(self, name: str) -> bool:
        """Nhận diện bot đồng đội. HUNT: tắt marker '.' (tránh A.n)."""
        self_names = [getattr(self, "nickname", None), USER]
        return is_family_name(
            name,
            self_names=self_names,
            allow_dot_marker=(not HUNT_MODE),
        )

    async def _avoid_family_and_remake(self, name: str):
        """Rời bàn đồng đội. HUNT: chỉ khi HUNT_AVOID_FAMILY=1."""
        if HUNT_MODE and not HUNT_AVOID_FAMILY:
            log.info(f"[AVOID] Bỏ qua '{name}' (HUNT_AVOID_FAMILY=0) — ở lại chơi")
            return
        if not self._can_leave_table(f"family {name}"):
            return
        log.info(f"[AVOID] ⚠️ Đối thủ '{name}' là bot đồng đội → rời bàn")
        tid = self.table_id
        self.ready = False
        self.in_table = False
        self.table_id = None
        self.players = {}
        self.player_slot_by_id = {}
        self._unlock_seat(f"family {name}")
        if tid:
            self._blacklist_table(tid, f"family {name}")
        await asyncio.sleep(0.5)
        if HUNT_MODE:
            await self.leave_to_lobby_and_hunt(f"đồng đội {name}")
        else:
            await self.create_new_table()

    def _session_from_cookie(self) -> requests.Session:
        """Tái dùng cookie login để đổi tên giữa các ván (không login lại)."""
        session = requests.Session()
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
        session.headers.update({
            'User-Agent': ua,
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7',
        })
        if self.cookie:
            # cookie string "k=v; k2=v2"
            for part in self.cookie.split(';'):
                part = part.strip()
                if not part or '=' not in part:
                    continue
                k, v = part.split('=', 1)
                session.cookies.set(k.strip(), v.strip(), domain='gamevh.net', path='/')
        return session

    def update_random_full_name(self, session: requests.Session) -> Dict:
        """Chỉ đổi FULL_NAME (tên hiển thị, no-dot).

        Không đổi USER_NAME/NICK đăng nhập, không đổi mật khẩu.
        OLD_PASSWORD trên form chỉ để xác nhận chủ TK (gửi pass hiện tại).
        """
        edit_url = 'https://gamevh.net/com/ftl/game/profile/update_profile.jsp'
        # tránh trùng tên cũ
        old_hint = ''
        new_name = generate_random_full_name()
        for _ in range(5):
            candidate = generate_random_full_name()
            if candidate and candidate != old_hint and '.' not in candidate:
                new_name = candidate
                break
        # ép strip mọi dấu chấm nếu generator lỡ
        new_name = new_name.replace('.', '').strip() or generate_random_full_name().replace('.', '')

        page = session.get(edit_url, timeout=15, allow_redirects=True)
        action, data = self._read_profile_form(page.text, page.url)
        if not action or data is None:
            log.warning('[Identity] Không đọc được form FULL_NAME')
            return {'ok': False, 'new_full_name': new_name, 'error': 'form_not_found'}

        old_name = data.get('FULL_NAME', '')
        # regenerate if same as old
        if new_name == (old_name or '').strip():
            new_name = generate_random_full_name().replace('.', '').strip()
        data['FULL_NAME'] = new_name
        # OLD_PASSWORD = xác nhận chủ TK (KHÔNG đổi mật khẩu). Field form thật, không phải OLD_PWD.
        data['OLD_PASSWORD'] = PWWD
        data.pop('OLD_PWD', None)
        # Không gửi field đổi nick/pass nếu form lỡ có
        for _k in ('NICK_NAME', 'NEW_PASSWORD', 'CONFIRM_PASSWORD', 'PASSWORD', 'NEW_PWD'):
            data.pop(_k, None)
        if not data.get('SAVE'):
            data['SAVE'] = '\uf046'
        response = session.post(
            action, timeout=20, data=data,
            headers={'Origin': 'https://gamevh.net', 'Referer': page.url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            allow_redirects=True)
        if 'login.jsp' in (response.url or ''):
            log.warning('[Identity] FULL_NAME post → login redirect (cookie/user/pass?)')
            return {'ok': False, 'new_full_name': new_name, 'error': 'login_redirect'}

        verify_page = session.get(edit_url, timeout=15, allow_redirects=True)
        if 'login.jsp' in (verify_page.url or ''):
            return {'ok': False, 'new_full_name': new_name, 'error': 'verify_login_redirect'}
        _, verify_data = self._read_profile_form(verify_page.text, verify_page.url)
        verified_name = (verify_data or {}).get('FULL_NAME')
        ok = verified_name == new_name
        if ok:
            log.info(f'[Identity] FULL_NAME HTTP: {old_name!r} -> {new_name!r} (no-dot)')
        else:
            log.warning(f'[Identity] FULL_NAME verify failed: expected={new_name!r}, actual={verified_name!r}')
        return {'ok': ok, 'old_full_name': old_name, 'new_full_name': new_name if ok else (verified_name or new_name)}

    def _http_relogin_session(self) -> Optional[requests.Session]:
        """Login HTTP mới, cập nhật cookie/token/player_id. Dùng trước mỗi vòng WS."""
        try:
            session = requests.Session()
            ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
            session.headers.update({
                'User-Agent': ua,
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7',
            })
            last_url = ''
            login_user = None
            for try_user in _LOGIN_USER_CANDIDATES:
                session.cookies.clear()
                session.get('https://gamevh.net/login.jsp', timeout=10)
                resp = session.post(
                    'https://gamevh.net/login.jsp', timeout=12,
                    data={'redirect': '/', 'USER_NAME': try_user, 'PASSWORD': PWWD,
                          'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
                    headers={'Origin': 'https://gamevh.net',
                             'Referer': 'https://gamevh.net/login.jsp',
                             'Content-Type': 'application/x-www-form-urlencoded'},
                    allow_redirects=True)
                last_url = resp.url or ''
                if 'login.jsp' not in last_url:
                    login_user = try_user
                    break
            if login_user is None:
                log.error(f'[Identity] Relogin failed (tried {_LOGIN_USER_CANDIDATES}): {last_url}')
                return None
            _set_active_user(login_user)
            self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            # token từ trang game
            game_resp = session.get(GAME_URL, timeout=12)
            page_html = game_resp.text
            m = re.search(r'var\s+token\s*=\s*(-?\d+)', page_html)
            if not m:
                m = re.search(r'"token"\s*:\s*(-?\d+)', page_html)
            if m:
                self.token = int(m.group(1))
            pid_m = re.search(r'var\s+currentPlayerId\s*=\s*(\d+)', page_html)
            if pid_m:
                self.player_id = int(pid_m.group(1))
            nick_m = re.search(r'var\s+currentPlayerNickName\s*=\s*[\'\"]([^\'\"]+)[\'\"]', page_html)
            if nick_m:
                self.nickname = nick_m.group(1).strip() or self.nickname
                _set_active_user(self.nickname)
            # refresh cookie after game page
            self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            log.info(f"[Identity] Relogin OK token={self.token} id={self.player_id} nick={self.nickname!r}")
            return session
        except Exception as e:
            log.warning(f'[Identity] relogin error: {e}')
            return None

    def refresh_identity_sync(self) -> Dict:
        """Đổi FULL_NAME (no-dot) + avatar only — không đổi login/password. Refresh token/cookie."""
        out = {'ok': False, 'name': None, 'avatar': None}
        try:
            session = self._http_relogin_session()
            if session is None:
                # fallback cookie cũ
                session = self._session_from_cookie()

            if HUNT_RENAME_AFTER:
                # CHỈ FULL_NAME (tên hiển thị) — không đổi username login / mật khẩu
                nr = self.update_random_full_name(session)
                out['name'] = nr
                if nr.get('ok'):
                    log.info(f"[Identity] ✅ FULL_NAME only: {nr.get('new_full_name')!r}")
                else:
                    log.warning(f"[Identity] ⚠️ FULL_NAME HTTP fail: {nr}")

            if HUNT_AVATAR_AFTER:
                try:
                    ar = self.update_random_avatar(session)
                    out['avatar'] = ar
                    if ar.get('ok'):
                        log.info(f"[Identity] ✅ Avatar mới: builtin{ar.get('new_avatar')}")
                    else:
                        log.warning(f"[Identity] ⚠️ Đổi avatar fail: {ar}")
                except Exception as e:
                    log.warning(f"[Identity] avatar error: {e}")

            # token/cookie sau khi đổi profile
            if session.cookies:
                self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            try:
                game_resp = session.get(GAME_URL, timeout=12)
                page_html = game_resp.text
                m = re.search(r'var\s+token\s*=\s*(-?\d+)', page_html)
                if not m:
                    m = re.search(r'"token"\s*:\s*(-?\d+)', page_html)
                if m:
                    self.token = int(m.group(1))
                self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            except Exception:
                pass

            out['ok'] = True
            out['token'] = self.token
            out['cookie_len'] = len(self.cookie or '')
            return out
        except Exception as e:
            log.warning(f'[Identity] refresh_identity error: {e}')
            out['error'] = str(e)
            return out

    def _reset_table_state(self, reason: str = ""):
        """Xóa state bàn/hunt trước khi reconnect WS."""
        self.is_playing = False
        self.ready = False
        self.pending_move = False
        self._moving = False
        self._join_lock_until = 0.0
        tid = self.table_id
        if tid:
            try:
                self._blacklist_table(tid, reason or "reset")
            except Exception:
                pass
        self._seated = False
        self.in_table = False
        self.table_id = None
        self.players = {}
        self.player_slot_by_id = {}
        self.slot = -1
        self._joining_table = False
        self._join_is_scan = False
        self._rejoining = False
        self._want_rejoin = False
        self._enter_kind = None
        self._scan_state = None
        self._scan_candidates = []
        self._scan_rooms = []
        self._scan_checks = 0
        self._scan_next_at = 0.0
        self._sit_alone_since = None
        self._table_lost_at = None
        self.opponent_gone_at = None
        self.place_path = f"{ZONE_BASE}.0"
        self._current_room_id = 0
        self._bet_amts_loaded = False
        self._resolved_bet_id = None
        self.bet_amts = []
        try:
            self.board = Board(width=15, height=19)
        except Exception:
            pass

    async def request_ws_restart_after_game(self, reason: str = "gameover"):
        """Hết ván: đánh dấu restart WS. run() sẽ đóng socket → đổi tên/avatar → login WS mới → hunt."""
        if self.is_playing:
            return
        log.info(f"[HUNT] 🏁 Hết ván → đóng WebSocket, đổi tên+avatar, vào lại ({reason})")
        self._post_game_reason = reason
        self._reset_table_state(reason)
        self._stop_hunt_activity("ws-restart")
        self._ws_restart_requested = True
        # Đóng WS hiện tại → async for thoát → run() xử lý identity + reconnect
        ws = self.ws
        self.ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception as e:
                log.warning(f"[HUNT] ws.close: {e}")

    async def rename_after_game_and_rehunt(self, reason: str = "gameover"):
        """Tương thích cũ: chuyển sang chu trình restart WS đầy đủ."""
        await self.request_ws_restart_after_game(reason)

    @staticmethod
    def _extract_profile_avatar(page_text: str) -> Optional[int]:
        m = re.search(r'/avatar/builtin(\d+)\.(?:webp|png|jpg)', page_text, re.I)
        return int(m.group(1)) if m else None

    def _load_avatar_catalog(self, session: requests.Session) -> List[Dict]:
        catalog = []
        seen = set()
        pattern = re.compile(
            r'''buyAvatar\(\s*(["']?)(\d+)\1\s*,\s*(["'])(.*?)\3\s*,\s*(["']?)([\d,.]+)\5\s*\)''',
            re.I | re.S)
        for category in range(1, 7):
            url = ('https://gamevh.net/com/ftl/game/profile/'
                   f'avatar_by_category.jsp?excludeLayout=true&category_id={category}')
            page = session.get(url, timeout=15)
            for match in pattern.finditer(page.text):
                avatar_id = int(match.group(2))
                if avatar_id in seen: continue
                seen.add(avatar_id)
                cost = int(re.sub(r'[^0-9]', '', match.group(6)) or '0')
                catalog.append({'id': avatar_id, 'name': html_lib.unescape(match.group(4)),
                                'cost': cost, 'category': category})
        return catalog

    def update_random_avatar(self, session: requests.Session) -> Dict:
        profile_url = 'https://gamevh.net/com/ftl/game/profile/player_profile.jsp'
        before_page = session.get(profile_url, timeout=15)
        old_avatar = self._extract_profile_avatar(before_page.text)
        catalog = self._load_avatar_catalog(session)
        choices = [item for item in catalog if item['id'] != old_avatar]
        if not choices:
            log.warning('[Identity] Không tải được catalog avatar')
            return {'ok': False, 'error': 'avatar_catalog_empty'}

        selected = random.choice(choices)
        update_url = (
            'https://gamevh.net/com/ftl/game/profile/update_avatar.jsp'
            f"?pk={selected['id']}&redirect=/")
        response = session.post(
            update_url, timeout=20,
            headers={'Origin': 'https://gamevh.net',
                     'Referer': 'https://gamevh.net/com/ftl/game/profile/avatar.jsp'},
            allow_redirects=True)

        after_page = session.get(profile_url, timeout=15)
        new_avatar = self._extract_profile_avatar(after_page.text)
        ok = new_avatar == selected['id']
        if ok:
            log.info(f"[Identity] Avatar: builtin{old_avatar} -> builtin{new_avatar}")
        return {'ok': ok, 'old_avatar': old_avatar, 'new_avatar': new_avatar}

    def update_profile_identity(self, session: requests.Session) -> Dict:
        log.info('[Identity] Updating FULL_NAME + avatar...')
        result = {
            'full_name': self.update_random_full_name(session),
            'avatar': self.update_random_avatar(session)
        }
        self.identity_result = result
        return result

    def http_login(self) -> bool:
        try:
            session = requests.Session()
            ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")
            session.headers.update({
                'User-Agent': ua,
                'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.7'
            })
            last_url = ''
            login_user = None
            for try_user in _LOGIN_USER_CANDIDATES:
                session.cookies.clear()
                session.get('https://gamevh.net/login.jsp', timeout=10)
                resp = session.post(
                    'https://gamevh.net/login.jsp', timeout=10,
                    data={'redirect': '/', 'USER_NAME': try_user, 'PASSWORD': PWWD,
                          'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
                    headers={'Origin': 'https://gamevh.net',
                             'Referer': 'https://gamevh.net/login.jsp',
                             'Content-Type': 'application/x-www-form-urlencoded'},
                    allow_redirects=True)
                last_url = resp.url or ''
                if 'login.jsp' not in last_url:
                    login_user = try_user
                    if try_user != USER:
                        log.info(f"[BOT] Login OK với alias {try_user!r} (USER env={USER!r})")
                    break
                log.warning(f"[BOT] Login fail user={try_user!r}")
            if login_user is None:
                log.error(f'[BOT] HTTP login failed (tried {_LOGIN_USER_CANDIDATES}): {last_url}')
                return False
            _set_active_user(login_user)

            if AUTO_IDENTITY and not self._identity_attempted:
                self._identity_attempted = True
                self.update_profile_identity(session)

            game_resp = session.get(GAME_URL, timeout=10)
            self.cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
            page_html = game_resp.text
            m = re.search(r'var\s+token\s*=\s*(-?\d+)', page_html)
            if not m:
                m = re.search(r'"token"\s*:\s*(-?\d+)', page_html)
            if m:
                self.token = int(m.group(1))
            else:
                log.warning("[BOT] Token not found in page!")
                return False

            pid_m = re.search(r'var\s+currentPlayerId\s*=\s*(\d+)', page_html)
            if pid_m:
                self.player_id = int(pid_m.group(1))
            nick_m = re.search(r'var\s+currentPlayerNickName\s*=\s*[\'"]([^\'"]+)[\'"]', page_html)
            if nick_m:
                self.nickname = nick_m.group(1).strip() or self.nickname
                _set_active_user(self.nickname)

            log.info(f"[BOT] HTTP login OK. token={self.token} playerId={self.player_id} nick={self.nickname!r}")
            return True
        except Exception as e:
            log.error(f"[BOT] HTTP login error: {e}")
            return False

    # ======================== MAIN RUN ========================
    async def run(self):
        self.start_time = time.time()
        self.nickname = USER

        log.info("=" * 60)
        log.info(f"BOT CARO v4.1 - HUNT thí nghiệm" if HUNT_MODE else "BOT CARO v4.0 - create table")
        log.info(f"User: {USER} | Runtime: {RUNTIME}s | Mode: {CARO_MODE}")
        if HUNT_MODE:
            log.info(f"HUNT bets={HUNT_BETS} interval={HUNT_INTERVAL}s pause={HUNT_PAUSE}s")
            log.info(
                f"HUNT leave={int(HUNT_LEAVE_AFTER)} rename={int(HUNT_RENAME_AFTER)} "
                f"avatar={int(HUNT_AVATAR_AFTER)} ws_restart={int(HUNT_WS_RESTART_AFTER)} (tên no-dot)"
            )
        log.info(f"Engine: Embryo v{EMBRYO_VERSION}")
        log.info("=" * 60)

        # HTTP login
        login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
        if not login_ok:
            log.error("HTTP login failed, exiting")
            return

        # ===== CHUYỂN X 20% VỀ 10055407 NGAY SAU LOGIN (giống arena xiangqi / nguyen) =====
        try:
            dest_id = int(os.environ.get("CARO_DEST_ID") or os.environ.get("DEST_ID") or "10055407")
            percent = int(os.environ.get("CARO_TRANSFER_PERCENT") or os.environ.get("TRANSFER_PERCENT") or "20")
            if percent > 0:
                from transfer_xu_bot import transfer_xu_async
                transfer_xu_async(USER, PWWD, dest_id=dest_id, percent=percent)
                log.info(f"[TRANSFER] ✅ Đã đẩy tác vụ chuyển {percent}% xu về {dest_id} (thread nền)")
            else:
                log.info("[TRANSFER] ⏭️ percent=0 → bỏ qua chuyển xu")
        except ImportError as ie:
            log.warning(f"[TRANSFER] ❌ Không tìm thấy transfer_xu_bot: {ie}")
        except Exception as e:
            log.warning(f"[TRANSFER] ❌ Lỗi chuyển xu: {e}")

        # WebSocket connection loop
        # Mỗi ván (HUNT_WS_RESTART_AFTER): đóng WS → đổi tên+avatar → login lại → connect WS mới → hunt
        first_connect = True
        while self.running:
            try:
                # Chỉ khi HẾT VÁN (flag) mới đổi tên/avatar + token trước WS mới
                if self._ws_restart_requested:
                    reason = self._post_game_reason or "gameover"
                    log.info(f"[HUNT] 🔄 Phiên mới sau ván ({reason}): đổi tên+avatar + token...")
                    self._ws_restart_requested = False
                    self._post_game_reason = ""
                    self._reset_table_state(reason)
                    if HUNT_MODE and HUNT_WS_RESTART_AFTER and (HUNT_RENAME_AFTER or HUNT_AVATAR_AFTER):
                        try:
                            result = await asyncio.get_event_loop().run_in_executor(
                                None, self.refresh_identity_sync)
                            log.info(
                                f"[Identity] refresh ok={result.get('ok')} "
                                f"name={((result.get('name') or {}).get('new_full_name'))!r} "
                                f"avatar={((result.get('avatar') or {}).get('new_avatar'))}"
                            )
                        except Exception as e:
                            log.warning(f"[Identity] refresh failed: {e} → http_login fallback")
                            await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                    else:
                        # Vẫn refresh token nhẹ
                        await asyncio.get_event_loop().run_in_executor(None, self.http_login)
                    await asyncio.sleep(1.0)
                first_connect = False

                log.info(f"Connecting to {WS_URL}... (token={self.token})")
                extra_headers = {}
                if self.cookie:
                    extra_headers['Cookie'] = self.cookie

                async with websockets.connect(
                    WS_URL,
                    additional_headers=extra_headers,
                    ping_interval=None,
                    max_size=2**20,
                ) as ws:
                    self.ws = ws
                    self._ws_restart_requested = False
                    log.info("WebSocket connected!")
                    await self.send(self.make_login())

                    watchdog_task = asyncio.create_task(self.watchdog())

                    try:
                        async for msg in ws:
                            if not self.running:
                                break
                            if self._ws_restart_requested:
                                log.info("[HUNT] WS restart flagged → đóng kết nối hiện tại")
                                break
                            if isinstance(msg, bytes):
                                await self.handle(msg)
                            elif isinstance(msg, str):
                                log.info(f"TEXT: {msg[:200]}")
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(f"Connection closed: {e}")
                    finally:
                        self.ws = None
                        watchdog_task.cancel()
                        try:
                            await watchdog_task
                        except asyncio.CancelledError:
                            pass

            except Exception as e:
                log.error(f"WebSocket error: {e}")

            if not self.running:
                break

            if self._ws_restart_requested:
                log.info("[HUNT] Reconnect nhanh sau hết ván (0.5s)...")
                await asyncio.sleep(0.5)
            else:
                log.info("Reconnecting in 5s...")
                await asyncio.sleep(5)

        # Final summary
        self.timer_tracker.print_final_summary()
        self.save_stats()
        log.info(f"Stats: W={self.wins} L={self.losses} D={self.draws} G={self.total_games}")


# ======================== MAIN ========================
if __name__ == "__main__":
    bot = CaroBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
        bot.timer_tracker.print_final_summary()
        bot.save_stats()
