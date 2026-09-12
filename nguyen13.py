#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  BOT CARO - SET_TURN Timer Monitor v4.0                            ║
║  Engine: Embryo Caro6 v1.2.3 (Linux Native)                        ║
║  Mục đích:                                                       ║
║  - Chơi Caro tự động trên gamevh.net                             ║
║  - Monitor SET_TURN packets, phát hiện timer reset bug           ║
║  - Ghi nhận chi tiết timer countdown khi gameover, người vào/ra  ║
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
    has_accent = random.choice([True, False])
    name_list = VN_TEN_DAU if has_accent else VN_TEN_KHONG_DAU
    return random.choice(name_list)


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

CARO_USER_DIRECT = "ngan10"
CARO_PWWD_DIRECT = "nhat123456"

def _clean_env(val: Optional[str], default: str) -> str:
    if val and str(val).strip(): return str(val).strip()
    return default

USER = _clean_env(os.environ.get("CARO_USER1") or os.environ.get("CARO_USER"), CARO_USER_DIRECT)
PWWD = _clean_env(os.environ.get("CARO_PWWD1") or os.environ.get("CARO_PWWD"), CARO_PWWD_DIRECT)

VERSION = "5.0.2"
GAME_ID = "caro"
RUNTIME = int(os.environ.get("CARO_RUNTIME_SECONDS") or
              float(os.environ.get("CARO_RUNTIME_HOURS", "5.9")) * 3600)
AUTO_IDENTITY = os.environ.get("CARO_AUTO_IDENTITY", "1") == "1"
IDENTITY_TEST_ONLY = os.environ.get("CARO_IDENTITY_TEST_ONLY", "0") == "1"
BOT_BET_XU = 400
BOT_MATCH_DURATION = '1800'
BOT_TURN_DURATION = '60'
# Đếm ngược server khi đối phương VÀO BÀN (accDuration):
#   "0" = mặc định server = 10s  →  đổi sang "2" để countdown 2s, bot vào trận ngay
BOT_ACC_DURATION = '2'
EMPTY = -1
CIRCLE = 0
CROSS = 1

CMD_MAP = {
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
        self._lobby_idle_since = None   # ANTISTUCK: mốc thời gian đứng sảnh
        self._ghost_table_since = None  # ANTISTUCK: mốc ngồi bàn không chơi
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
        self.balance = None            # số dư xu (từ BALANCE_CHANGED) — chọn mức cược phù hợp
        self._create_fail_count = 0    # đếm CREATE_RULE thất bại liên tiếp -> thang hồi phục

        self._identity_attempted = False
        self.identity_result = {}

        # === SET_TURN TIMER TRACKER ===
        self.timer_tracker = SetTurnTracker()

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
                ("accDuration", BOT_ACC_DURATION), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_amt_id); w.i8(len(args))
        for name, val in args: w.write_ascii(name); w.write_utf(val)
        return w.build()

    def make_get_table(self) -> bytes:
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA_EX"); w.write_ascii(""); return w.build()

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
        if not self._bet_amts_loaded:
            self._bet_amts_loaded = False
            await self.send(self.make_list_bet_amt())
        else:
            await self.send(self.make_create_rule())

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
            elif cmd == "GET_TABLE_DATA_EX": await self.handle_table(r)
            elif cmd == "START_MATCH": await self.handle_start(r)
            elif cmd == "SET_TURN": await self.handle_turn(r)
            elif cmd == "MOVE": await self.handle_move(r)
            elif cmd == "GAMEOVER": await self.handle_gameover(r)
            elif cmd == "PLAY": await self.handle_play(r)
            elif cmd == "KICK_PLAYER": await self.handle_kick(r)
            elif cmd == "PLAYER_ENTERED": await self.handle_player_enter(r)
            elif cmd == "PLAYER_EXITED": await self.handle_player_exit(r)
            elif cmd == "BALANCE_CHANGED": await self.handle_balance(r)
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
            log.error(f"LOGIN failed")

    async def handle_enter(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            if self._joining_table:
                self._joining_table = False; self._rejoining = False
                self.in_table = True
                await asyncio.sleep(0.3); await self.send(self.make_get_table())
            elif not self.in_table:
                if self._want_rejoin and self.table_id:
                    self._want_rejoin = False; self._rejoining = True; self._joining_table = True
                    path = f"{self.place_path}.{self.table_id}"
                    log.info(f"[BOT] Thử vào lại bàn cũ: {path}")
                    await self.send(self.make_enter(path))
                else:
                    self._bet_amts_loaded = False; self._resolved_bet_id = None
                    await self.send(self.make_list_bet_amt())
        else:
            if self._joining_table:
                self._joining_table = False
                if self._rejoining:
                    self._rejoining = False; self._rejoin_attempts += 1; self.table_id = None
                    await asyncio.sleep(1); await self.send(self.make_list_bet_amt())
                else:
                    await asyncio.sleep(1); await self.send(self.make_create_rule())
            else:
                log.warning(f"[ENTER] Vào sảnh lỗi (status={status}) -> thử lại sau 3s")
                await asyncio.sleep(3)
                await self.send(self.make_enter(self.place_path))

    async def handle_list_bet_amt(self, r: BinaryReader):
        status = r.i8()
        if status != 0:
            log.warning(f"[BET] Lấy mức cược lỗi (status={status}) -> thử lại sau 3s")
            await asyncio.sleep(3)
            await self.send(self.make_list_bet_amt())
            return
        count = r.i8()
        self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
        self._resolved_bet_id = self.resolve_bet_amt_id()
        self._bet_amts_loaded = True
        await self.send(self.make_create_rule())

    async def handle_create_rule(self, r: BinaryReader):
        status = r.i8()
        if status == 0:
            table_id = r.read_ascii()
            self.table_id = table_id; self._rejoin_attempts = 0
            self._create_fail_count = 0
            log.info(f"[CREATE_RULE] Bàn mới! id={table_id}")
            await asyncio.sleep(0.5); self._joining_table = False
            await self.send(self.make_get_table())
        else:
            self._joining_table = False
            self._create_fail_count += 1
            log.warning(f"[CREATE_RULE] ❌ Tạo bàn thất bại (status={status}) — lần {self._create_fail_count}")
            await self._recover_create_fail()

    async def handle_balance(self, r: BinaryReader):
        """BALANCE_CHANGED: slot i8, chip i64, star i64, ch i64, st i64 — theo dõi số dư."""
        try:
            slot = r.i8(); chip = r.i64()
            if self.slot is None or self.slot < 0 or slot == self.slot:
                self.balance = chip
        except Exception:
            pass

    async def _recover_create_fail(self):
        """Thang hồi phục khi không tạo được bàn (vd hết xu dưới mức cược):
        x3: hạ mức cược theo số dư | x6: hard reset về sảnh nạp lại mức cược
        | x10: đóng WS để reconnect lấy phiên mới."""
        fail = self._create_fail_count
        if fail >= 10:
            self._create_fail_count = 0
            log.error("[CREATE] Thất bại 10 lần liên tiếp -> đóng WS để reconnect lấy phiên mới")
            try:
                if self.ws: await self.ws.close()
            except Exception:
                pass
            return
        if fail >= 6:
            log.warning("[CREATE] Thất bại 6 lần -> hard reset: về sảnh, nạp lại mức cược")
            self.table_id = None
            self._bet_amts_loaded = False
            self._resolved_bet_id = None
            await asyncio.sleep(2)
            await self.send(self.make_enter(self.place_path))
            return
        if fail >= 3 and self.bet_amts:
            if self.balance is not None:
                afford = [ba for ba in self.bet_amts if 0 < ba['value'] <= int(self.balance)]
                if afford:
                    pick = max(afford, key=lambda x: x['value'])['id']
                    reason = f"theo số dư {int(self.balance):,} xu"
                else:
                    pick = min(self.bet_amts, key=lambda x: x['value'])['id']
                    reason = f"số dư {int(self.balance):,} xu — lấy mức thấp nhất"
            else:
                pick = min(self.bet_amts, key=lambda x: x['value'])['id']
                reason = "chưa rõ số dư — lấy mức thấp nhất"
            if pick != self._resolved_bet_id:
                log.warning(f"[CREATE] Hạ mức cược: bet_id={pick} ({reason})")
                self._resolved_bet_id = pick

    async def handle_table(self, r: BinaryReader):
        if self._moving:
            log.info("[TABLE] Engine đang tính, bỏ qua board reload")
            return
        try:
            first_byte = r.i8()
            if first_byte != 0:
                if "not in table" in r.read_utf().lower():
                    self.in_table = False; self.table_id = None
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

            move_count = r.u8()
            for _ in range(move_count): r.i8(); r.i32()

            width = r.u8(); height = r.u8(); self.board.resize(width, height)
            r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()

            r.u8(); r.u8(); n = r.u8()
            for _ in range(n): r.read_ascii(); r.read_utf()

            has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players.keys())

            self.is_playing = is_playing
            log.info(f"[TABLE] Slot={self.slot} Playing={is_playing} Turn=slot{current_player}")

            if is_playing and current_player == self.slot:
                if not self._moving and not self.pending_move:
                    self.pending_move = True; await self.do_move()
            elif not is_playing and self.slot >= 0:
                if has_opponent:
                    if not self.ready:
                        log.info("[BOT] Đối thủ đã vào ghế → SET_READY!")
                        self.ready = True; await self.send(self.make_ready())
                else:
                    if self.ready:
                        log.info("[BOT] Không có đối thủ → Hủy Ready.")
                    self.ready = False
            elif not is_playing and self.slot < 0:
                self.in_table = False; self.table_id = None
                await asyncio.sleep(1); await self.send(self.make_list_bet_amt())

            self._rejoining = False
        except Exception as e: log.error(f"Table error: {e}")

    async def handle_start(self, r: BinaryReader):
        self.total_games += 1; self.is_playing = True; self.ready = False; self.pending_move = False
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

        # === FIX COUNTDOWN 2s: SET_TURN lobby = server đang đếm ngược
        # (đối phương vừa vào bàn). Chưa READY thì READY ngay để countdown
        # hết (accDuration=2s) là vào trận, không phải chờ 10s như trước.
        if (context == "lobby" and self.in_table and self.slot >= 0
                and not self.is_playing and not self.ready):
            log.info(f"[BOT] SET_TURN lobby countdown={turn_timeout}s → SET_READY ngay!")
            self.ready = True
            await self.send(self.make_ready())

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
            await asyncio.sleep(2); await self.create_new_table()
            return

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
                return
            log.warning("[BOT] Bot thua nhưng không tìm thấy playerId người thắng; chuyển sang sẵn sàng")

        log.info("[BOT] Ở lại bàn, sẽ sẵn sàng sau 5 giây...")
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
        await asyncio.sleep(1); await self.create_new_table()

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
        log.info(f"[BOT] {name} vào bàn → cập nhật trạng thái...")
        await self.send(self.make_get_table())
        # === FIX COUNTDOWN 2s: đối phương vào bàn → SET_READY NGAY ===
        # Server đang đếm ngược accDuration (BOT_ACC_DURATION=2s) từ lúc này;
        # bot phải READY trước khi countdown hết để vào trận ngay lập tức.
        if self.slot >= 0 and not self.is_playing and not self.ready:
            log.info(f"[BOT] SET_READY ngay (countdown {BOT_ACC_DURATION}s) → vào trận ngay!")
            self.ready = True
            await self.send(self.make_ready())

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
                self.in_table = False; await asyncio.sleep(1); await self.create_new_table()
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

            # ===== ANTISTUCK: đứng sảnh quá 90s không có bàn -> reset cờ kẹt, nạp lại cược =====
            try:
                if not self.is_playing and not self.in_table:
                    if self._lobby_idle_since is None:
                        self._lobby_idle_since = time.time()
                    elif time.time() - self._lobby_idle_since > 90:
                        log.warning("[ANTISTUCK] >90s đứng sảnh không bàn -> reset cờ (_joining/_rejoining/_bet_amts), ENTER lại sảnh")
                        self._lobby_idle_since = time.time()
                        self._joining_table = False
                        self._rejoining = False
                        self._want_rejoin = False
                        self.table_id = None
                        self._bet_amts_loaded = False
                        self._resolved_bet_id = None
                        await self.send(self.make_enter(self.place_path))
                else:
                    self._lobby_idle_since = None
            except Exception: pass

            # ===== GHOST TABLE: ngồi bàn >5 phút không chơi -> hỏi server xác nhận =====
            try:
                if self.in_table and not self.is_playing:
                    if self._ghost_table_since is None:
                        self._ghost_table_since = time.time()
                    elif time.time() - self._ghost_table_since > 300:
                        log.info("[ANTISTUCK] Ngồi bàn >300s không chơi -> GET_TABLE xác nhận với server")
                        self._ghost_table_since = time.time()
                        await self.send(self.make_get_table())
                else:
                    self._ghost_table_since = None
            except Exception: pass

            try:
                if (self.opponent_gone_at is not None and self.is_playing
                    and time.time() - self.opponent_gone_at > 15):
                    self.opponent_gone_at = None
                    await self.send(self.make_get_table())

                if (self._table_lost_at is not None
                    and time.time() - self._table_lost_at > 8):
                    self._table_lost_at = None; self.table_id = None
                    await self.create_new_table()

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
            if not name or input_type in ('submit', 'button', 'image', 'file', 'reset'):
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

    def update_random_full_name(self, session: requests.Session) -> Dict:
        edit_url = 'https://gamevh.net/com/ftl/game/profile/update_profile.jsp'
        new_name = generate_random_full_name()
        page = session.get(edit_url, timeout=15, allow_redirects=True)
        action, data = self._read_profile_form(page.text, page.url)
        if not action or data is None:
            log.warning('[Identity] Không đọc được form FULL_NAME')
            return {'ok': False, 'new_full_name': new_name, 'error': 'form_not_found'}

        old_name = data.get('FULL_NAME', '')
        data['FULL_NAME'] = new_name
        data['OLD_PWD'] = PWWD
        data['SAVE'] = '\uf046'
        response = session.post(
            action, timeout=20, data=data,
            headers={'Origin': 'https://gamevh.net', 'Referer': page.url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            allow_redirects=True)

        verify_page = session.get(edit_url, timeout=15, allow_redirects=True)
        _, verify_data = self._read_profile_form(verify_page.text, verify_page.url)
        verified_name = (verify_data or {}).get('FULL_NAME')
        ok = verified_name == new_name
        if ok:
            log.info(f'[Identity] FULL_NAME: {old_name!r} -> {new_name!r}')
        else:
            log.warning(f'[Identity] FULL_NAME verify failed: expected={new_name!r}, actual={verified_name!r}')
        return {'ok': ok, 'old_full_name': old_name, 'new_full_name': new_name}

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
            session.get('https://gamevh.net/login.jsp', timeout=10)
            resp = session.post(
                'https://gamevh.net/login.jsp', timeout=10,
                data={'redirect': '/', 'USER_NAME': USER, 'PASSWORD': PWWD,
                      'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
                headers={'Origin': 'https://gamevh.net',
                         'Referer': 'https://gamevh.net/login.jsp',
                         'Content-Type': 'application/x-www-form-urlencoded'},
                allow_redirects=True)
            if 'login.jsp' in resp.url:
                log.error(f'[BOT] HTTP login failed: {resp.url}')
                return False

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

            log.info(f"[BOT] HTTP login OK. token={self.token}")
            return True
        except Exception as e:
            log.error(f"[BOT] HTTP login error: {e}")
            return False

    # ======================== MAIN RUN ========================
    async def run(self):
        self.start_time = time.time()
        self.nickname = USER

        log.info("=" * 60)
        log.info(f"BOT CARO v4.0 - SET_TURN Timer Monitor")
        log.info(f"User: {USER} | Runtime: {RUNTIME}s")
        log.info(f"Engine: Embryo v{EMBRYO_VERSION}")
        log.info("=" * 60)

        # HTTP login
        login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
        if not login_ok:
            log.error("HTTP login failed, exiting")
            return

        # ===== CHUYỂN X ĐỊNH KỲ VỀ 10055407 (lần đầu ngay sau login, sau đó mỗi CARO_TRANSFER_INTERVAL giây) =====
        try:
            from transfer_xu_bot import start_periodic_transfer
            _tx_int = int(os.environ.get("CARO_TRANSFER_INTERVAL") or 9000)
            _tx_pct = int(os.environ.get("CARO_TRANSFER_PERCENT") or 20)
            start_periodic_transfer(USER, PWWD, dest_id=10055407, percent=_tx_pct, interval=_tx_int)
            log.info(f"[TRANSFER] ✅ Bật chuyển xu định kỳ {_tx_pct}% mỗi {_tx_int}s về 10055407 (thread nền)")
        except ImportError as ie:
            log.warning(f"[TRANSFER] ❌ Không tìm thấy transfer_xu_bot: {ie}")
        except Exception as e:
            log.warning(f"[TRANSFER] ❌ Lỗi chuyển x: {e}")

        # WebSocket connection loop
        while self.running:
            try:
                log.info(f"Connecting to {WS_URL}...")
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
                    log.info("WebSocket connected!")
                    await self.send(self.make_login())

                    watchdog_task = asyncio.create_task(self.watchdog())

                    try:
                        async for msg in ws:
                            if not self.running: break
                            if isinstance(msg, bytes):
                                await self.handle(msg)
                            elif isinstance(msg, str):
                                log.info(f"TEXT: {msg[:200]}")
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(f"Connection closed: {e}")
                    finally:
                        watchdog_task.cancel()
                        try: await watchdog_task
                        except asyncio.CancelledError: pass

            except Exception as e:
                log.error(f"WebSocket error: {e}")

            if self.running:
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
