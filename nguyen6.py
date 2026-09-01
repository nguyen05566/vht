#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  BOT CARO ALPHAGOMOKU (MK) - FULL NAME + AVATAR v5.0             ║
║  Engine: AlphaGomoku MK 2026 – #1 Caro Gomocup                   ║
║  Luật: INFO rule 8 = CARO6                            ║
║  Bàn server 15x19 → khung 15x15 region-first                     ║
║  Pipeline: đọc bàn → region → RESTART → BOARD map → nhận nước    ║
╚══════════════════════════════════════════════════════════════════╝
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
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q", "--break-system-packages"], stderr=subprocess.DEVNULL)
        importlib.import_module(pkg)

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
    """Tạo tên người Việt Nam tự nhiên (chỉ gồm 1 từ: tên có dấu hoặc không dấu), không họ/đệm, không số."""
    has_accent = random.choice([True, False])
    name_list = VN_TEN_DAU if has_accent else VN_TEN_KHONG_DAU
    return random.choice(name_list)


# ======================== ALPHAGOMOKU CONFIG ========================
try:
    _BASE_DIR = Path(__file__).parent
except NameError:
    _BASE_DIR = Path.cwd()

ENGINE_DIR = _BASE_DIR / "alphagomoku-engine"
# Binary AlphaGomoku MK (pbrain)
AG_BINARY_PRIMARY = "pbrain-AlphaGomoku"
AG_BINARY_NAMES = [
    # Bản Linux native (ưu tiên, không cần wine)
    "pbrain-AlphaGomoku",
    "pbrain-AlphaGomoku_opencl",
    "pbrain-AlphaGomoku_cuda",
    "pbrain-alphagomoku",
    # Fallback: bản Windows .exe (chạy qua wine)
    "pbrain-AlphaGomoku.exe",
    "pbrain-alphagomoku.exe",
    "pbrain-AlphaGomoku*.exe",
    "pbrain-alphagomoku*.exe",
    "AlphaGomoku.exe",
    "alphagomoku.exe",
    "pbrain-*.exe",
]
# Gomocup 2026 – #1 Caro
AG_DOWNLOAD_URL = "https://github.com/MaciejKozarzewski/AlphaGomoku/releases/download/v5.9.3/AlphaGomoku_linux.zip"
# INFO rule 8 = CARO6 (caro); rule 9 = CARO5; timeout 4s/nước; match server 1800s
AG_RULE = 8
AG_TIMEOUT = 2000           # ms / nước (2s)
AG_MATCH_TIMEOUT = 1800000  # ms / ván = 30 phút (khớp BOT_MATCH_DURATION)
AG_MOVE_TIMEOUT = 15.0      # giây – timeout cứng cho toàn bộ khâu tính nước

# Alias giữ tương thích tên cũ trong file
KG_BINARY_PRIMARY = AG_BINARY_PRIMARY
KG_BINARY_NAMES = AG_BINARY_NAMES
KG_DOWNLOAD_URLS = [AG_DOWNLOAD_URL]

def _find_file(directory: Path, names: List[str]) -> Optional[Path]:
    if not directory.exists():
        return None
    for name in names:
        if "*" in name:
            matches = list(directory.glob(name))
            if matches:
                return matches[0]
        else:
            p = directory / name
            if p.exists():
                return p
    # Tìm đệ quy 1 cấp
    for sub in directory.iterdir():
        if sub.is_dir():
            for name in names:
                if "*" in name:
                    matches = list(sub.glob(name))
                    if matches:
                        return matches[0]
                else:
                    p = sub / name
                    if p.exists():
                        return p
    return None

def optimize_alphagomoku_config():
    """Tự động tối ưu config.json cho AlphaGomoku trên môi trường 2 vCPU (GitHub Actions)."""
    config_file = ENGINE_DIR / "config.json"
    if not config_file.exists():
        return
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # 1. Tận dụng tối đa 2 vCPU
        cfg["search_threads"] = 2

        # 2. Tăng độ rộng MCTS tối ưu (64)
        if "search_config" in cfg and "mcts_config" in cfg["search_config"]:
            cfg["search_config"]["mcts_config"]["max_children"] = 64

        # 3. Mở rộng bộ nhớ Threat Space Search (TSS) & Hash table
        if "search_config" in cfg and "tss_config" in cfg["search_config"]:
            cfg["search_config"]["tss_config"]["max_positions"] = 500
            cfg["search_config"]["tss_config"]["hash_table_size"] = 8388608

        # 4. Tăng bộ nhớ Node Cache
        if "search_config" in cfg and "tree_config" in cfg["search_config"]:
            cfg["search_config"]["tree_config"]["initial_node_cache_size"] = 131072
            cfg["search_config"]["tree_config"]["edge_bucket_size"] = 400000
            cfg["search_config"]["tree_config"]["node_bucket_size"] = 20000

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        log.info("[AG] Đã tối ưu hóa config.json thành công (threads=2, max_children=64, max_positions=500, hash_table=8MB)")
    except Exception as e:
        log.warning(f"[AG] Không thể tối ưu config.json: {e}")

def auto_download_alphagomoku() -> Optional[str]:
    """Tải AlphaGomoku Linux (native) từ GitHub nếu chưa có binary."""
    primary = ENGINE_DIR / AG_BINARY_PRIMARY
    if primary.exists():
        try:
            primary.chmod(0o755)
        except Exception:
            pass
        optimize_alphagomoku_config()
        log.info(f"[AG] Binary đã có: {primary}")
        return str(primary)

    binary = _find_file(ENGINE_DIR, AG_BINARY_NAMES)
    if binary:
        try:
            binary.chmod(0o755)
        except Exception:
            pass
        optimize_alphagomoku_config()
        log.info(f"[AG] Binary đã có: {binary}")
        return str(binary)

    log.info("[AG] Đang tải AlphaGomoku Linux (native) từ GitHub release...")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import zipfile
        archive = Path("/tmp/alphagomoku_linux.zip")
        req = urllib.request.Request(AG_DOWNLOAD_URL, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=300) as resp:
            archive.write_bytes(resp.read())
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(str(ENGINE_DIR))
        archive.unlink(missing_ok=True)

        optimize_alphagomoku_config()

        binary = _find_file(ENGINE_DIR, AG_BINARY_NAMES)
        if binary:
            try:
                binary.chmod(0o755)
            except Exception:
                pass
            log.info(f"[AG] Tải thành công: {binary}")
            return str(binary)

        for pat in ("pbrain-AlphaGomoku", "*.exe"):
            for f in ENGINE_DIR.rglob(pat):
                if f.is_file():
                    try:
                        f.chmod(0o755)
                    except Exception:
                        pass
                    log.info(f"[AG] Tìm thấy binary: {f}")
                    return str(f)
        log.warning("[AG] Giải nén xong nhưng không tìm thấy binary")
        return None
    except Exception as e:
        log.error(f"[AG] Download failed: {e}")
        return None

def detect_ag_binary() -> Optional[str]:
    def _make_exec(path_str):
        try:
            os.chmod(path_str, 0o755)
        except Exception:
            pass
        return path_str

    primary = ENGINE_DIR / AG_BINARY_PRIMARY
    if primary.exists():
        return _make_exec(str(primary))
    p = _find_file(ENGINE_DIR, AG_BINARY_NAMES)
    if p:
        return _make_exec(str(p))
    if ENGINE_DIR.exists():
        # Ưu tiên binary Linux native (không đuôi .exe)
        for f in ENGINE_DIR.rglob("pbrain-AlphaGomoku"):
            return _make_exec(str(f))
        for f in ENGINE_DIR.rglob("*AlphaGomoku*.exe"):
            return _make_exec(str(f))
        for f in ENGINE_DIR.rglob("*alphagomoku*.exe"):
            return _make_exec(str(f))
        for f in ENGINE_DIR.rglob("pbrain-*.exe"):
            return _make_exec(str(f))
        for f in ENGINE_DIR.rglob("*.exe"):
            return _make_exec(str(f))
    return None

# Alias tương thích
auto_download_katagomo = auto_download_alphagomoku
detect_kg_binary = detect_ag_binary

# ======================== ALPHAGOMOKU PBRAIN ENGINE ========================
class AlphaGomokuEngine:
    """
    Wrapper pbrain cho AlphaGomoku (MK) 2026 – #1 Caro Gomocup.

    Quy trình BẮT BUỘC mỗi nước:
      1. Đọc board_history (bàn server 15x19)
      2. Phân tích → chọn khung 15x15 (origin)
      3. Khung đổi → RESTART (xoá state cũ)
      4. Map quân trong khung → BOARD (tọa độ 0..14)
      5. Nhận nước → map ngược bàn thật
      6. Lỗi → thử origin khác + RESTART (không fallback liền)

    Luật: INFO rule 8 = CARO6 (chuẩn Gomocup Caro; rule 9 = CARO5).
    """
    ENGINE_SIZE = 15

    def __init__(self, timeout_turn=None, board_size=15, board_height=19,
                 match_timeout_ms=None):
        self.binary = detect_ag_binary()
        # timeout_turn: giây (nội bộ); match: ms
        tt_ms = timeout_turn if timeout_turn is not None else AG_TIMEOUT
        if tt_ms > 100:  # đã là ms
            self.timeout_turn_ms = int(tt_ms)
            self.timeout_turn = tt_ms / 1000.0
        else:
            self.timeout_turn = float(tt_ms)
            self.timeout_turn_ms = int(tt_ms * 1000)
        self.match_timeout_ms = int(
            match_timeout_ms if match_timeout_ms is not None else AG_MATCH_TIMEOUT
        )
        self.time_left_ms = self.match_timeout_ms
        self._match_start_mono = None
        self.board_width = board_size
        self.board_height = board_height
        self.proc = None
        self.lock = threading.Lock()
        self._buffer = bytearray()
        self.my_side = 1
        self._initialized = False
        self._selector = None
        self._origin_x = 0
        self._origin_y = 2
        self._committed_ox = None
        self._committed_oy = None
        self._engine_has_board = False
        self.rule = AG_RULE  # 8 = CARO6 (rule 9 = CARO5)

    def _send_time_infos(self):
        """Gửi timeout_turn / timeout_match / time_left cho engine."""
        # Tránh time_left về 0 (engine có thể panic / đánh bừa)
        left = self.match_timeout_ms  # ĐỀU SỨC CẢ VÁN: luôn báo time_left lớn -> Embryo nghĩ đúng ~timeout_turn mỗi nước (depth 13-20), không bị yếu khi đồng hồ ván giảm
        self._send(f"INFO timeout_turn {self.timeout_turn_ms}")
        self._send(f"INFO timeout_match {self.match_timeout_ms}")
        self._send(f"INFO time_left {left}")

    # ---------- I/O ----------
    def _init_selector(self):
        self._close_selector()
        if self.proc and self.proc.stdout:
            try:
                self._selector = selectors.DefaultSelector()
                self._selector.register(self.proc.stdout, selectors.EVENT_READ)
            except Exception as e:
                log.warning(f"[AG] Selector register error: {e}")
                self._selector = None

    def _close_selector(self):
        if self._selector:
            try:
                self._selector.close()
            except Exception:
                pass
            self._selector = None

    def _send(self, cmd: str):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except Exception:
                pass

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
                    ready = self._selector.select(timeout=min(remaining, 1.0))
                else:
                    sel = selectors.DefaultSelector()
                    sel.register(self.proc.stdout, selectors.EVENT_READ)
                    ready = sel.select(timeout=min(remaining, 1.0))
                    sel.close()
                if ready:
                    chunk = os.read(self.proc.stdout.fileno(), 4096)
                    if not chunk:
                        return ""
                    self._buffer.extend(chunk)
            except Exception:
                return ""

    def _drain_output(self):
        while self._read_line(timeout=0.02):
            pass

    # ---------- Region analysis (KHÔNG đụng engine) ----------
    def _compute_origin(self, board_history: list) -> Tuple[int, int]:
        """
        Chọn khung 15x15 dựa trên các nước gần đây.
        Không phân tích threat/pattern và không can thiệp vào việc đánh giá
        hay tính nước của AlphaGomoku.
        """
        max_oy = max(0, self.board_height - self.ENGINE_SIZE)
        if not board_history:
            return 0, max_oy // 2

        recent = board_history[-8:]
        total_w = 0.0
        weighted_y = 0.0
        for i, (_x, y, _sym) in enumerate(recent):
            w = 1.8 ** i
            weighted_y += y * w
            total_w += w

        center_y = weighted_y / total_w if total_w else board_history[-1][1]
        oy = int(round(center_y - self.ENGINE_SIZE / 2))
        oy = max(0, min(max_oy, oy))

        last_y = board_history[-1][1]
        if last_y <= 2:
            oy = 0
        elif last_y >= self.board_height - 3:
            oy = max_oy

        # Hysteresis: chỉ đổi khung khi thật sự cần.
        current = self._committed_oy
        if current is not None and 0 <= current <= max_oy:
            last_rel = last_y - current
            if 2 <= last_rel <= self.ENGINE_SIZE - 3:
                oy = current

        return 0, oy

    def _count_outside(self, board_history: list, ox: int, oy: int) -> int:
        es = self.ENGINE_SIZE
        n = 0
        for x, y, _ in board_history:
            if not (ox <= x < ox + es and oy <= y < oy + es):
                n += 1
        return n

    def _analyze_region(self, board_history: list) -> Tuple[int, int, int, int]:
        """
        Bước 1+2: đọc history, xác định khung tối ưu.
        Trả về (ox, oy, n_inside, n_outside).
        """
        ox, oy = self._compute_origin(board_history)
        outside = self._count_outside(board_history, ox, oy)
        inside = len(board_history) - outside
        return ox, oy, inside, outside

    def _to_engine(self, x: int, y: int, ox: int = None, oy: int = None) -> Optional[Tuple[int, int]]:
        if ox is None:
            ox = self._origin_x
        if oy is None:
            oy = self._origin_y
        ex, ey = x - ox, y - oy
        if 0 <= ex < self.ENGINE_SIZE and 0 <= ey < self.ENGINE_SIZE:
            return ex, ey
        return None

    def _from_engine(self, ex: int, ey: int) -> Tuple[int, int]:
        return ex + self._origin_x, ey + self._origin_y

    def _map_history_to_engine(self, board_history: list, ox: int, oy: int) -> list:
        """Chỉ quân trong khung, tọa độ đã map 0..14. Bỏ quân ngoài khung."""
        out = []
        for x, y, sym in board_history:
            m = self._to_engine(x, y, ox, oy)
            if m is not None:
                out.append((m[0], m[1], sym))
        return out

    def _soft_restart(self) -> bool:
        """RESTART engine để xoá state cũ trước khi BOARD khung mới."""
        if not self.proc or self.proc.poll() is not None:
            return False
        self._drain_output()
        self._send("RESTART")
        ok = False
        for _ in range(6):
            line = self._read_line(timeout=1.0)
            if line.upper() == "OK":
                ok = True
                break
        self._engine_has_board = False
        self._committed_ox = None
        self._committed_oy = None
        if ok:
            self._send_time_infos()
            self._send("INFO ponder 1")
            self._send(f"INFO rule {self.rule}")
            time.sleep(0.05)
            self._drain_output()
        return ok

    def _commit_region(self, ox: int, oy: int, need_restart: bool) -> bool:
        """
        Gán origin mới. Nếu khung đổi hoặc engine chưa có board hợp lệ
        → RESTART để không mang state cũ (tránh engine hiểu bàn trống/sai).
        """
        region_changed = (
            self._committed_ox is None
            or self._committed_oy is None
            or ox != self._committed_ox
            or oy != self._committed_oy
            or not self._engine_has_board
            or (need_restart and (ox != self._committed_ox or oy != self._committed_oy))
        )
        self._origin_x, self._origin_y = ox, oy
        if region_changed:
            log.info(
                f"[AG] Commit region origin=({ox},{oy}) "
                f"cover y[{oy}:{oy + self.ENGINE_SIZE}) "
                f"(restart={region_changed})"
            )
            if not self._soft_restart():
                self._send("START 15")
                for _ in range(5):
                    if self._read_line(timeout=0.8).upper() == "OK":
                        break
                self._send_time_infos()
                self._send(f"INFO rule {self.rule}")
                self._drain_output()
                self._engine_has_board = False
            return True
        return False

    # ---------- Lifecycle ----------
    def _wine_cmd(self, binary: str) -> list:
        """Chọn wine / wine64 tùy môi trường."""
        candidates = [
            "/usr/lib/wine/wine64",
            "/usr/bin/wine64",
            "wine64",
            "wine",
        ]
        wine_bin = "wine"
        for c in candidates:
            if c.startswith("/") and Path(c).exists():
                wine_bin = c
                break
            elif not c.startswith("/"):
                # which
                p = shutil.which(c)
                if p:
                    wine_bin = p
                    break
        env_loader = os.environ.get("WINELOADER")
        if env_loader and Path(env_loader).exists():
            return [env_loader, binary]
        return [wine_bin, binary]

    def start_game(self, my_symbol=1) -> bool:
        with self.lock:
            self._stop_unlocked()
            if not self.binary:
                self.binary = detect_ag_binary()
            if not self.binary:
                log.warning("[AG] Không tìm thấy binary AlphaGomoku")
                return False

            optimize_alphagomoku_config()
            try:
                is_exe = self.binary.lower().endswith(".exe")
                cmd = self._wine_cmd(self.binary) if is_exe else [self.binary]

                env = os.environ.copy()
                env.setdefault("WINEDEBUG", "-all")
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=str(ENGINE_DIR),
                    env=env,
                )
                self._buffer = bytearray()
                self._init_selector()
                self.my_side = my_symbol
                self._origin_x = 0
                self._origin_y = max(0, (self.board_height - self.ENGINE_SIZE) // 2)
                self._committed_ox = None
                self._committed_oy = None
                self._engine_has_board = False

                # AlphaGomoku: START 15 (vuông), không RECTSTART
                self._send("START 15")
                ok = False
                for _ in range(12):
                    line = self._read_line(timeout=1.0)
                    if line.upper() == "OK":
                        ok = True
                        break
                if not ok:
                    log.warning("[AG] START 15 không nhận OK, vẫn tiếp tục")

                # Reset đồng hồ ván 30 phút
                self.time_left_ms = self.match_timeout_ms
                self._match_start_mono = time.monotonic()
                self._send_time_infos()
                self._send("INFO ponder 1")
                # rule 8 = CARO6, rule 9 = CARO5 – chuẩn Gomocup Caro
                self._send(f"INFO rule {self.rule}")
                time.sleep(0.2)
                self._drain_output()

                self._initialized = True
                log.info(
                    f"[AG] AlphaGomoku started (rule={self.rule}, "
                    f"turn={self.timeout_turn_ms}ms, match={self.match_timeout_ms}ms=30m) "
                    f"binary={Path(self.binary).name} my_side={my_symbol}"
                )
                return True
            except Exception as e:
                log.error(f"[AG] Start error: {e}")
                self._initialized = False
                return False

    def restart_game(self) -> bool:
        with self.lock:
            if not self._initialized or not self.proc or self.proc.poll() is not None:
                return False
            self.time_left_ms = self.match_timeout_ms
            self._match_start_mono = time.monotonic()
            ok = self._soft_restart()
            self._origin_x = 0
            self._origin_y = max(0, (self.board_height - self.ENGINE_SIZE) // 2)
            self._committed_ox = None
            self._committed_oy = None
            self._engine_has_board = False
            return ok

    def get_move(self, board_history: list, my_side: int) -> Optional[Tuple[int, int]]:
        with self.lock:
            try:
                if not self._initialized or not self.proc or self.proc.poll() is not None:
                    return None
                return self._get_move_region_first(board_history, my_side)
            except Exception as e:
                log.warning(f"[AG] get_move error: {e}")
                self._engine_has_board = False
                return None

    def _get_move_region_first(self, board_history: list, my_side: int) -> Optional[Tuple[int, int]]:
        """
        Pipeline bắt buộc:
          đọc history → xác định region → (RESTART nếu đổi) → BOARD map → nhận nước → map ngược
        Không bao giờ gửi tọa độ tuyệt đối / nước ngoài khung lên engine.
        """
        # KHÔNG drain output ở đây — giữ kết quả ponder trong buffer!
        occupied = {(x, y) for x, y, _ in board_history}

        # --- Bước 1+2: phân tích region TRƯỚC khi đụng engine ---
        ox, oy, n_in, n_out = self._analyze_region(board_history)
        log.info(
            f"[AG] Analyze region origin=({ox},{oy}) "
            f"inside={n_in} outside={n_out} total={len(board_history)}"
        )
        if n_out > 0:
            log.info(
                f"[AG] {n_out} quân ngoài khung sẽ bị cắt (không gửi engine) "
                f"— khung bám mặt trận gần nhất"
            )

        # Candidate windows chỉ dựa trên vị trí, không chấm threat/pattern.
        max_oy = max(0, self.board_height - self.ENGINE_SIZE)
        candidate_origins = [(ox, oy)]

        if board_history:
            last_y = board_history[-1][1]
            follow_oy = max(0, min(max_oy, last_y - self.ENGINE_SIZE // 2))
            candidate_origins.append((0, follow_oy))

        candidate_origins.extend([(0, 0), (0, max_oy)])
        candidate_origins = list(dict.fromkeys(candidate_origins))

        for attempt, (cox, coy) in enumerate(candidate_origins):
            # --- Bước 3: commit region + RESTART nếu khung đổi ---
            self._commit_region(cox, coy, need_restart=False)

            # --- Bước 4: map + BOARD (chỉ quân trong khung, tọa độ 0..14) ---
            mapped = self._map_history_to_engine(board_history, cox, coy)
            # KHÔNG drain output ở đây — giữ kết quả ponder trong buffer!
            # Cập nhật time_left theo đồng hồ thật (không để engine tưởng hết giờ)
            if self._match_start_mono is not None:
                elapsed_ms = int((time.monotonic() - self._match_start_mono) * 1000)
                self.time_left_ms = max(
                    self.match_timeout_ms - elapsed_ms,
                    self.timeout_turn_ms,
                )
            self._send_time_infos()
            t0 = time.monotonic()
            self._send("BOARD")
            for (ex, ey, sym) in mapped:
                c = 1 if sym == self.my_side else 2
                self._send(f"{ex},{ey},{c}")
            self._send("DONE")

            raw = self._read_engine_move()
            think_ms = int((time.monotonic() - t0) * 1000)
            if raw is None:
                log.warning(f"[AG] attempt={attempt} origin=({cox},{coy}): không có response")
                self._engine_has_board = False
                continue

            ex, ey = raw
            if not (0 <= ex < self.ENGINE_SIZE and 0 <= ey < self.ENGINE_SIZE):
                log.warning(f"[AG] attempt={attempt}: engine trả ({ex},{ey}) ngoài 0..14")
                self._engine_has_board = False
                continue

            # Đánh dấu engine đã nhận BOARD hợp lệ
            self._committed_ox, self._committed_oy = cox, coy
            self._engine_has_board = True
            self._origin_x, self._origin_y = cox, coy

            abs_x, abs_y = self._from_engine(ex, ey)

            if not (0 <= abs_x < self.board_width and 0 <= abs_y < self.board_height):
                log.warning(
                    f"[AG] attempt={attempt}: map abs=({abs_x},{abs_y}) ngoài bàn "
                    f"— thử origin khác (không fallback)"
                )
                self._engine_has_board = False
                continue

            if (abs_x, abs_y) in occupied:
                log.warning(
                    f"[AG] attempt={attempt}: ({abs_x},{abs_y}) đã có quân "
                    f"— thử origin khác (không fallback)"
                )
                self._engine_has_board = False
                continue

            # Trừ thời gian đã nghĩ khỏi time_left
            self.time_left_ms = max(self.time_left_ms - think_ms, self.timeout_turn_ms)
            log.info(
                f"[AG] OK engine=({ex},{ey}) → abs=({abs_x},{abs_y}) "
                f"origin=({cox},{coy}) mapped={len(mapped)}/{len(board_history)} "
                f"think={think_ms}ms time_left={self.time_left_ms}ms"
            )
            return abs_x, abs_y

        # Hết origin ứng viên — vẫn không fallback ở đây; để do_move soft-retry
        log.warning("[AG] Hết candidate origin, engine chưa cho nước hợp lệ")
        self._engine_has_board = False
        return None

    def _read_engine_move(self) -> Optional[Tuple[int, int]]:
        deadline = time.monotonic() + self.timeout_turn + 5.0
        while time.monotonic() < deadline:
            line = self._read_line(timeout=0.8)
            if not line:
                continue
            up = line.upper()
            if up.startswith(("MESSAGE", "ERROR", "DEBUG", "OK", "UNKNOWN")):
                if up.startswith("ERROR"):
                    log.warning(f"[AG] engine ERROR: {line}")
                continue
            # Regex lọc cực chặt: chỉ chấp nhận dòng chứa duy nhất "X,Y" (bỏ qua rác "eval X,Y" hay "X,Y score")
            match = re.match(r"^\s*(\d+)\s*,\s*(\d+)\s*$", line)
            if match:
                return int(match.group(1)), int(match.group(2))
        return None

    def _stop_unlocked(self):
        if self.proc:
            try:
                self._send("END")
            except Exception:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
            self._initialized = False
        self._close_selector()

    def stop(self):
        with self.lock:
            self._stop_unlocked()

# Alias để tương thích code cũ
# Alias tương thích code cũ
KataGomokuEngine = AlphaGomokuEngine
KataGomoEngine = AlphaGomokuEngine

# ======================== CONSTANTS & CONFIG ========================
WS_URL = "wss://gamevh.net/ws/gameServer"
GAME_URL = "https://gamevh.net/play/caro/0"
# === CẤU HÌNH TRỰC TIẾP - KHÔNG CẦN SECRETS ===
CARO_USER_DIRECT = "nguyen6"
CARO_PWWD_DIRECT = ""
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
BOT_BET_XU = 1000
BOT_MATCH_DURATION = '1800'  # 1800s trên server GameVH
BOT_TURN_DURATION = '60'     # 60s/nước trên server
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
        self.pos += 8; return (hi << 32) + lo
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

    def get_strategic_empty_move(self, my_symbol: int, opponent_symbol: int) -> Tuple[int, int]:
        """
        Chọn nước đi chiến thuật thông minh khi fallback:
        - Ưu tiên bắt đòn 4 thắng ngay hoặc chặn đối thủ 4.
        - Mở đòn 3 / chặn 3.
        - Ưu tiên vùng thoáng ít quân (tránh bị kẹt trong đám đông quân chết).
        - Gần mặt trận đang đánh.
        """
        if not self.history:
            return self.get_empty_near_center()

        last_x, last_y = self.history[-1][0], self.history[-1][1]
        directions = [(1, 0), (0, 1), (1, 1), (1, -1)]

        # Tìm các ô trống quanh các nước gần nhất (bán kính 1..3)
        k = min(len(self.history), 8)
        recent_stones = [(x, y) for x, y, _ in self.history[-k:]]

        candidates = set()
        for sx, sy in recent_stones:
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    nx, ny = sx + dx, sy + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == EMPTY:
                        candidates.add((nx, ny))

        if not candidates:
            return self.get_empty_near(last_x, last_y)

        best_score = -float('inf')
        best_move = (last_x, last_y)

        for x, y in candidates:
            score = 0

            # Khoảng cách tới nước vừa đánh
            dist_last = max(abs(x - last_x), abs(y - last_y))
            score -= dist_last * 12

            # Đếm số ô trống xung quanh (độ thoáng / ít quân)
            open_count = 0
            crowded_count = 0
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.width and 0 <= ny < self.height:
                        if self.grid[ny][nx] == EMPTY:
                            open_count += 1
                        else:
                            crowded_count += 1
            # Thưởng điểm cho vùng thoáng ít quân
            score += open_count * 20
            # Phạt nặng nếu chui vào ổ đông quân đã bị bít kín
            if crowded_count >= 6:
                score -= 150

            # Đánh giá thế cờ các hướng
            for dx, dy in directions:
                f_count, f_open = self._count_consecutive(x, y, dx, dy, my_symbol)
                o_count, o_open = self._count_consecutive(x, y, dx, dy, opponent_symbol)

                # Thắng ngay (5 con)
                if f_count >= 4:
                    score += 1000000
                # Chặn đối thủ thắng (4 con)
                elif o_count >= 4:
                    score += 600000
                # Đòn 3 thoáng (sắp thành 4)
                elif f_count == 3 and f_open >= 1:
                    score += 50000
                # Chặn đối thủ đòn 3
                elif o_count == 3 and o_open >= 1:
                    score += 40000
                # Nước 2 thoáng
                elif f_count == 2 and f_open == 2:
                    score += 5000
                elif o_count == 2 and o_open == 2:
                    score += 3000

            if score > best_score:
                best_score = score
                best_move = (x, y)

        return best_move

    def _count_consecutive(self, x: int, y: int, dx: int, dy: int, sym: int) -> Tuple[int, int]:
        count = 0
        open_ends = 0

        # Tiến
        nx, ny = x + dx, y + dy
        while 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == sym:
            count += 1
            nx += dx
            ny += dy
        if 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == EMPTY:
            open_ends += 1

        # Lùi
        nx, ny = x - dx, y - dy
        while 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == sym:
            count += 1
            nx -= dx
            ny -= dy
        if 0 <= nx < self.width and 0 <= ny < self.height and self.grid[ny][nx] == EMPTY:
            open_ends += 1

        return count, open_ends

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
        
        self.ag = None; self.ag_available = False
        self.ag_moves = 0; self.ag_errors = 0; self.ag_fallback_count = 0
        self._moving = False; self._last_move_xy = None
        self._pending_opponent_moves = []  # Queue nước đối thủ khi bot đang tính
        self._ag_reinit_attempts = 0
        self._ag_reinit_cooldown_until = 0.0
        
        self.table_id = None
        self.player_slot_by_id = {}
        self._pending_kick_player_id = None
        self.opponent_gone_at = None
        self._table_lost_at = None
        self._want_rejoin = False; self._rejoining = False; self._rejoin_attempts = 0

        self._identity_attempted = False
        self.identity_result = {}

    def init_ag(self):
        if self.ag is not None:
            return self.ag_available
        binary = detect_ag_binary()
        if not binary:
            binary = auto_download_alphagomoku()
        if not binary:
            log.warning("[AG] No AlphaGomoku binary found!")
            self.ag_available = False
            return False
        try:
            self.ag = AlphaGomokuEngine(timeout_turn=AG_TIMEOUT, board_size=15, board_height=19)
            self.ag.binary = binary
            ok = self.ag.start_game(my_symbol=self.my_symbol)
            if ok:
                self.ag_available = True
                log.info(f"[AG] AlphaGomoku OK! binary={os.path.basename(binary)} rule={AG_RULE}")
            else:
                self.ag_available = False
                log.warning("[AG] Start failed!")
            return self.ag_available
        except Exception as e:
            log.error(f"[AG] Init error: {e}")
            self.ag_available = False
            return False

    def _hard_reset_engine(self, reason: str = ""):
        """
        Kill hẳn process wine + xóa tham chiếu, để lượt sau init_ag() tạo lại
        process mới hoàn toàn sạch. Dùng khi fallback xảy ra để tránh tái dùng
        process hỏng/treo ngầm dẫn tới fallback lặp vô hạn.
        """
        try:
            if self.ag is not None:
                self.ag.stop()
        except Exception as e:
            log.warning(f"[AG] _hard_reset_engine stop error: {e}")
        finally:
            self.ag = None
            self.ag_available = False
        log.warning(f"[AG] HARD-RESET engine (reason={reason}) → lượt sau sẽ init_ag() lại")

    def _try_reinit_ag(self) -> bool:
        """
        Tái khởi tạo engine NGAY TRONG VÁN khi ag_available == False.
        Có giới hạn số lần thử (MAX_REINIT) và cooldown giữa các lần để tránh
        bão restart khi wine chưa kịp sẵn sàng.
        """
        MAX_REINIT = 3
        COOLDOWN = 15.0  # giây
        now = time.time()
        if now < self._ag_reinit_cooldown_until:
            return False
        if self._ag_reinit_attempts >= MAX_REINIT:
            log.warning(
                f"[AG] Đã thử reinit {self._ag_reinit_attempts}x, tạm ngưng."
                f" Sẽ thử lại ở trận sau.")
            return False
        self._ag_reinit_attempts += 1
        self._ag_reinit_cooldown_until = now + COOLDOWN
        log.warning(
            f"[AG] Thử tái khởi tạo engine ngay trong ván "
            f"(lần {self._ag_reinit_attempts}/{MAX_REINIT})...")
        self._hard_reset_engine("reinit-mid-match")
        ok = self.init_ag()
        if ok:
            log.info("[AG] Engine đã phục hồi ngay trong ván")
            self._ag_reinit_attempts = 0
        return ok

    @property
    def running(self) -> bool: return self._running

    def stop(self):
        self._running = False
        if self.ag: self.ag.stop(); self.ag = None; self.ag_available = False

    def save_stats(self):
        try:
            with open("/tmp/caro_ag_stats.json", "w") as f:
                json.dump({'W': self.wins, 'L': self.losses, 'D': self.draws, 'G': self.total_games}, f)
        except Exception: pass

    def update_symbols(self):
        self.my_symbol = CIRCLE if self.slot == 0 else CROSS
        self.opponent_symbol = CROSS if self.my_symbol == CIRCLE else CIRCLE
        log.info(f"Slot={self.slot} Me={'X' if self.my_symbol == CROSS else 'O'}")

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

    def make_play(self, pos: int) -> bytes:
        w = BinaryWriter(); w.write_command("PLAY"); w.i16(pos); return w.build()

    def make_pong(self) -> bytes:
        w = BinaryWriter(); w.write_command("PONG"); return w.build()

    def make_ready(self) -> bytes:
        if self.is_playing: return b''
        w = BinaryWriter(); w.write_command("SET_READY"); return w.build()

    def make_kick_player(self, player_id: int) -> bytes:
        # Web client protocol: command 410 followed by signed int64 playerId.
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
            
            # Flush nước đối thủ bị queue TRƯỚC khi capture history
            if self._pending_opponent_moves:
                log.info(f"[AG] Flushing {len(self._pending_opponent_moves)} queued opponent moves")
                self._pending_opponent_moves.clear()
            
            history = list(self.board.history)

            # Nếu engine đang tắt, thử khôi phục ngay trong ván trước khi fallback
            if not self.ag_available:
                self._try_reinit_ag()

            if self.ag_available:
                try:
                    # HARD TIMEOUT: nếu engine/wine treo, không để bot đứng hình
                    move = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.ag.get_move(history, self.my_symbol)
                        ),
                        timeout=AG_MOVE_TIMEOUT
                    )

                    if (move and 0 <= move[0] < self.board.width and 0 <= move[1] < self.board.height
                            and self.board.get(*move) == EMPTY):
                        x, y = move
                        self.ag_moves += 1
                    else:
                        self.ag_errors += 1
                        log.warning(
                            f"[AG] Nước chưa hợp lệ sau region-retry: {move}. "
                            f"Soft RESTART rồi thử lại 1 lần..."
                        )
                        try:
                            self.ag.restart_game()
                            move2 = await asyncio.wait_for(
                                asyncio.get_event_loop().run_in_executor(
                                    None,
                                    lambda: self.ag.get_move(history, self.my_symbol)
                                ),
                                timeout=AG_MOVE_TIMEOUT
                            )
                            if (move2 and 0 <= move2[0] < self.board.width
                                    and 0 <= move2[1] < self.board.height
                                    and self.board.get(*move2) == EMPTY):
                                x, y = move2
                                self.ag_moves += 1
                                log.info(f"[AG] Retry sau RESTART OK: {move2}")
                            else:
                                raise RuntimeError(f"retry still invalid: {move2}")
                        except Exception as e2:
                            log.warning(f"[AG] Soft retry fail ({e2}) → fallback chiến thuật thoáng quân")
                            x, y = self.board.get_strategic_empty_move(self.my_symbol, self.opponent_symbol)
                            self.ag_fallback_count += 1
                            # HARD-RESET: fallback → kill engine; thử phục hồi ngay trong ván
                            self._hard_reset_engine("soft-retry fail")
                            self._try_reinit_ag()
                except asyncio.TimeoutError:
                    # Wine/engine treo -> buộc kill, thử phục hồi ngay trong ván
                    self.ag_errors += 1
                    log.warning(f"[AG] TIMEOUT nước >{AG_MOVE_TIMEOUT}s → engine treo, reset")
                    self._hard_reset_engine("timeout")
                    self._try_reinit_ag()
                    x, y = self.board.get_strategic_empty_move(self.my_symbol, self.opponent_symbol)
                    self.ag_fallback_count += 1
                except Exception as e:
                    self.ag_errors += 1
                    log.warning(f"[AG] Error: {e}")
                    x, y = self.board.get_strategic_empty_move(self.my_symbol, self.opponent_symbol)
                    self.ag_fallback_count += 1
                    # HARD-RESET: bất kỳ lỗi nào (kể cả treo ngầm) → kill engine, tái tạo sạch
                    self._hard_reset_engine("exception")
                    self._try_reinit_ag()
            else:
                x, y = self.board.get_strategic_empty_move(self.my_symbol, self.opponent_symbol)

            elapsed = time.time() - start
            pos = self.board.xy_to_pos(x, y)
            log.info(f"MOVE ({x},{y}) took {elapsed:.2f}s [AG]")
            await self.send(self.make_play(pos))
            self._last_move_xy = (x, y)
            self.board.put(x, y, self.my_symbol)
        finally:
            self._moving = False

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

    async def handle_list_bet_amt(self, r: BinaryReader):
        status = r.i8()
        if status != 0: return
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
            log.info(f"[CREATE_RULE] Bàn mới! id={table_id}")
            await asyncio.sleep(0.5); self._joining_table = False
            await self.send(self.make_get_table())
        else:
            self._joining_table = False

    async def handle_table(self, r: BinaryReader):
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
                        log.info("[BOT] Phát hiện đối thủ thực sự đã ngồi vào ghế. Bấm Sẵn sàng!")
                        self.ready = True; await self.send(self.make_ready())
                else:
                    if self.ready:
                        log.info("[BOT] Không có đối thủ ngồi ở ghế đối diện (chỉ có người xem hoặc bàn trống). Hủy Sẵn sàng.")
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
        # Reset bộ đếm reinit cho mỗi trận mới
        self._ag_reinit_attempts = 0
        self._ag_reinit_cooldown_until = 0.0
        
        player_count = r.u8()
        for i in range(player_count):
            r.i8(); r.i32()
        
        width = r.u8(); height = r.u8(); self.board.resize(width, height)
        r.i16(); self.board.load_rle(r.read_bytes()); self.update_symbols()
        
        log.info(f"=== GAME {self.total_games} === Me={'X' if self.my_symbol == CROSS else 'O'}")
        
        if self.ag is None:
            self.init_ag()
        else:
            self.ag.start_game(my_symbol=self.my_symbol)
        
        if self.slot < 0:
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_turn(self, r: BinaryReader):
        sid = r.i8(); r.i16(); r.i16()
        if self.slot < 0: return
        if sid == self.slot and self.is_playing and self.running:
            if not self.pending_move and not self._moving:
                self.pending_move = True; await asyncio.sleep(2); await self.do_move()

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
        
        # Nếu engine đang tính → queue nước đi để sync sau
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
            # Ưu tiên đúng slot được GAMEOVER đánh dấu thắng; fallback sang đối thủ còn lại.
            winner_sid = next((sid for sid, result in results.items()
                               if sid != self.slot and sid >= 0 and result in (1, 11)), None)
            if winner_sid is None:
                winner_sid = next((sid for sid in self.players
                                   if sid != self.slot and sid >= 0), None)
            winner = self.players.get(winner_sid) if winner_sid is not None else None
            winner_id = winner.get('id') if winner else None
            if winner_id is not None:
                log.info(f"[BOT] Bot thua; sẽ kick người thắng {winner.get('name', winner_id)} sau 5 giây...")
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
        # Chỉ kick nếu đúng playerId vẫn đang ở slot đối phương.
        if not any(sid != self.slot and sid >= 0 and player.get('id') == player_id
                   for sid, player in self.players.items()):
            log.info(f"[BOT] Bỏ kick playerId={player_id}: người chơi không còn ở bàn")
            return
        self.ready = False
        self._pending_kick_player_id = player_id
        log.info(f"[BOT] Gửi KICK_PLAYER playerId={player_id}")
        await self.send(self.make_kick_player(player_id))
        # Không để response bị treo làm nhầm một thông báo kick về sau.
        await asyncio.sleep(3)
        if self._pending_kick_player_id == player_id:
            self._pending_kick_player_id = None
            log.warning(f"[BOT] KICK_PLAYER playerId={player_id} không có response sau 3 giây")
            if self.in_table: await self.send(self.make_get_table())

    async def _delay_ready(self, delay: float):
        await asyncio.sleep(delay)
        if not self.is_playing and self.in_table:
            await self.send(self.make_get_table())

    async def handle_player_enter(self, r: BinaryReader):
        place_level = r.i8()
        pid = r.i64(); name = r.read_utf()
        if r.remaining() >= 36:
            r.i64(); r.i64(); r.read_ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()
            
        if place_level < 4: return
        log.info(f"[BOT] Phát hiện {name} vào bàn cờ. Đang cập nhật trạng thái bàn...")
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
                self.in_table = False; await asyncio.sleep(1); await self.create_new_table()
        elif self.is_playing:
            if self.opponent_gone_at is None:
                self.opponent_gone_at = time.time()
                log.info("[BOT] Đối thủ rời giữa ván -> ở lại bàn, chờ GAMEOVER")
        elif self.in_table:
            log.info("[BOT] Phát hiện có người rời bàn. Đang cập nhật lại trạng thái...")
            await self.send(self.make_get_table())

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
                    await self.create_new_table()
                
                if (not self.is_playing and not self.in_table and not self._joining_table
                    and not self._rejoining and self._bet_amts_loaded):
                    await self.send(self.make_create_rule())
            except Exception: pass

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
            if not name:
                continue
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
            log.warning(
                f'[Identity] FULL_NAME verify failed: expected={new_name!r}, '
                f'actual={verified_name!r}, HTTP={response.status_code}')
        return {
            'ok': ok, 'old_full_name': old_name, 'new_full_name': new_name,
            'verified_full_name': verified_name, 'http_status': response.status_code
        }

    @staticmethod
    def _extract_profile_balance(page_text: str) -> Optional[int]:
        m = re.search(
            r'(?is)<div\s+class=["\'][^"\']*\bchipBalance\b[^"\']*["\'][^>]*>(.*?)</div>',
            page_text)
        if not m:
            return None
        digits = re.sub(r'[^0-9-]', '', html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1))))
        return int(digits) if digits and digits != '-' else None

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
                if avatar_id in seen:
                    continue
                seen.add(avatar_id)
                cost = int(re.sub(r'[^0-9]', '', match.group(6)) or '0')
                catalog.append({
                    'id': avatar_id,
                    'name': html_lib.unescape(match.group(4)),
                    'cost': cost,
                    'category': category
                })
        return catalog

    def update_random_avatar(self, session: requests.Session) -> Dict:
        profile_url = 'https://gamevh.net/com/ftl/game/profile/player_profile.jsp'
        before_page = session.get(profile_url, timeout=15)
        old_avatar = self._extract_profile_avatar(before_page.text)
        balance_before = self._extract_profile_balance(before_page.text)
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
        balance_after = self._extract_profile_balance(after_page.text)
        ok = new_avatar == selected['id']
        if ok:
            log.info(
                f"[Identity] Avatar: builtin{old_avatar} -> builtin{new_avatar}; "
                f"giá niêm yết={selected['cost']} x; số dư={balance_before}->{balance_after}")
        else:
            log.warning(
                f"[Identity] Avatar verify failed: expected=builtin{selected['id']}, "
                f"actual=builtin{new_avatar}, HTTP={response.status_code}")
        return {
            'ok': ok, 'old_avatar': old_avatar, 'new_avatar': new_avatar,
            'selected_avatar': selected, 'balance_before': balance_before,
            'balance_after': balance_after, 'http_status': response.status_code
        }

    def update_profile_identity(self, session: requests.Session) -> Dict:
        log.info('[Identity] Updating FULL_NAME + avatar (không đổi tên đăng nhập)...')
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
                data={'redirect': '/', 'USER_NAME': USER, 'PWD': PWWD,
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

            tm = re.search(r'var\s+token\s*=\s*(-?\d+)', page_html)
            if not tm:
                log.error('[BOT] Token not found')
                return False
            self.token = int(tm.group(1))

            nm = re.search(r"var\s+currentPlayerNickName\s*=\s*'([^']+)'", page_html)
            if not nm:
                log.error('[BOT] currentPlayerNickName not found')
                return False
            self.nickname = nm.group(1)

            pm = re.search(r'var\s+placePath\s*=\s*\"([^\"]+)\"', page_html)
            if pm:
                self.place_path = pm.group(1)

            if self.nickname == USER:
                log.info(f'[Identity] Tên đăng nhập giữ nguyên: {self.nickname}')
            else:
                log.warning(
                    f'[Identity] Server nickname={self.nickname!r} khác CARO_USER={USER!r}')
            log.info(f'[BOT] Login OK: {self.nickname}')
            return True
        except Exception as e:
            log.error(f'[BOT] Login error: {e}', exc_info=True)
            return False

    async def connect_ws(self) -> bool:
        try:
            self.ws = await websockets.connect(WS_URL,
                additional_headers={"Cookie": self.cookie, "Origin": "https://gamevh.net",
                                    "User-Agent": "Mozilla/5.0"},
                max_size=2**20, ping_interval=None)
            return True
        except Exception as e: log.error(f"[BOT] WS connect error: {e}"); return False

    async def run_ws(self):
        if not await self.connect_ws(): return
        await self.send(self.make_login())
        wd_task = asyncio.create_task(self.watchdog())
        try:
            async for raw in self.ws:
                if not self.running: break
                if isinstance(raw, bytes): await self.handle(raw)
        except websockets.exceptions.ConnectionClosed as e:
            log.warning(f"[BOT] WS closed: {e.code}")
        except Exception as e: log.error(f"[BOT] WS error: {e}")
        finally:
            wd_task.cancel()
            try: await wd_task
            except Exception: pass
            self.save_stats()
            if self.ws and self.ws.close_code is None:
                try: await self.ws.close()
                except Exception: pass

    async def run(self):
        self.start_time = time.time(); self._running = True
        log.info(f"{'='*50}")

        # ===== CHUYỂN X 50% NGAY KHI KHỞI ĐỘNG (TRƯỚC KHI VÀO BÀN) =====
        log.info("[TRANSFER] 🔄 Chuyển 50% x cho xxxx trước khi vào bàn...")
        try:
            import asyncio
            from transfer_xu_bot import transfer_xu_async
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: asyncio.run(
                transfer_xu_async(USER, PWWD, dest_id=65692738, percent=50)
            ))
            if result:
                log.info("[TRANSFER] ✅ Chuyển x thành công!")
            else:
                log.info("[TRANSFER] ⚠️ Chuyển x thất bại, tiếp tục chạy bot...")
        except ImportError as ie:
            log.error(f"[TRANSFER] ❌ Không tìm thấy transfer_xu_bot: {ie}")
        except Exception as e:
            log.error(f"[TRANSFER] ❌ Lỗi chuyển x: {e}")
        log.info("[TRANSFER] ✅ Hoàn tất, bắt đầu vào bàn chơi...")
        # ===== END CHUYỂN X =====
        log.info("BOT CARO ALPHAGOMOKU (MK) v5.0 – rule 8 = CARO6, region-first")
        log.info(f"{'='*50}")
        
        retry_count = 0
        while self.running:
            if time.time() - self.start_time > RUNTIME: break
            
            was_in_table = self.in_table or self.is_playing
            self._want_rejoin = (was_in_table and self.table_id is not None and self._rejoin_attempts < 2)
            
            self.is_playing = False; self.pending_move = False
            self.in_table = False; self.ready = False
            self.board = Board(width=15, height=19); self.players.clear()
            self.bet_amts = []; self._resolved_bet_id = None
            self._bet_amts_loaded = False; self._joining_table = False
            self.opponent_gone_at = None; self._table_lost_at = None
            
            if self.ag: self.ag.stop(); self.ag = None; self.ag_available = False
            
            login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
            if not login_ok:
                retry_count += 1
                retry_delay = min(30 * (2 ** (retry_count - 1)), 300)
                remaining = RUNTIME - (time.time() - self.start_time)
                if remaining <= 0:
                    break
                retry_delay = min(retry_delay, remaining)
                log.warning(f'[BOT] Login thất bại; thử lại sau {retry_delay:.0f}s')
                await asyncio.sleep(retry_delay)
                continue

            retry_count = 0
            if IDENTITY_TEST_ONLY:
                remaining = RUNTIME - (time.time() - self.start_time)
                log.info(
                    f'[TEST] Identity test only; không chạy game. '
                    f'Chờ hết {max(0, remaining):.1f}s...')
                if remaining > 0:
                    await asyncio.sleep(remaining)
                self.stop()
                break

            await self.run_ws()
            
            if not (self.in_table or self.is_playing):
                self.table_id = None
            
            self.save_stats()
            if self.ag: self.ag.stop(); self.ag = None

def main():
    bin_path = auto_download_alphagomoku()
    if bin_path:
        print(f"[SETUP] AlphaGomoku ready: {os.path.basename(bin_path)}")
        print(f"[SETUP] Rule={AG_RULE} (CARO6), board window 15x15 on 15x19")
    else:
        print("[SETUP] Không tìm thấy AlphaGomoku binary – bot sẽ chơi gần trung tâm")
        print(f"[SETUP] Tải: {AG_DOWNLOAD_URL}")
        print(f"[SETUP] Giải nén vào thư mục: {ENGINE_DIR}/")

    try:
        asyncio.get_running_loop()
        loop = asyncio.get_running_loop()
        loop.create_task(_run_bot())
    except RuntimeError:
        asyncio.run(_run_bot())

async def _run_bot():
    try:
        bot = CaroBot()
        await bot.run()
    except KeyboardInterrupt:
        log.info("[BOT] Stopped by user")
    except Exception as e:
        log.error(f"[BOT] Error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
elif 'ipykernel' in sys.modules or 'google.colab' in sys.modules:
    main()
