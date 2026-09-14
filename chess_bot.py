#!/usr/bin/env python3
"""
chess_bot.py — Bot Cờ Vua cho gamevh.net — Engine: Stockfish 19

Chế độ:
1. TẠO BÀN: tạo bàn mức cược X, chờ người vào
2. TÌM BÀN: scan sảnh tìm bàn có người, vào chơi
3. MIXED (mặc định): tìm bàn trước, không có thì tạo bàn

Cấu hình qua env:
  CHESS_USER / CHESS_PWWD        tài khoản
  CHESS_BET_XU                   mức cược (mặc định 1000, test=50)
  CHESS_MODE                      create / find / mixed (mặc định mixed)
  CHESS_RUNTIME_HOURS             thời gian chạy (mặc định 5.7)
  CHESS_ACC_FILE / CHESS_ACC_INDEX  lấy acc từ file
"""
import os, sys, struct, time, re, asyncio, subprocess, threading, json, random, signal, atexit
import urllib.request, urllib.parse, http.cookiejar
from typing import List, Tuple, Dict, Optional
from pathlib import Path

try:
    import websockets
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "websockets", "-q", "--break-system-packages"],
                   stderr=subprocess.DEVNULL)
    import websockets

# ==================== LOGGING ====================
import logging
log = logging.getLogger("chess")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(h)

# ==================== CONFIG ====================
DEFAULT_ACC_PWWD = "nhat123456"

def _resolve_base_dir():
    try:
        return Path(__file__).resolve().parent
    except:
        return Path.cwd()

def load_account_from_file(acc_file, index):
    base = _resolve_base_dir()
    path = Path(acc_file)
    if not path.is_absolute():
        path = base / acc_file
    with open(path) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if index >= len(lines):
        raise IndexError(f"Index {index} out of range ({len(lines)} lines)")
    return lines[index], DEFAULT_ACC_PWWD

def _resolve_account():
    direct_user = (os.environ.get("CHESS_USER") or "").strip()
    if direct_user:
        passwd = os.environ.get("CHESS_PWWD") or DEFAULT_ACC_PWWD
        return direct_user, passwd
    acc_file = (os.environ.get("CHESS_ACC_FILE") or "acc_valid_1.txt").strip()
    try:
        acc_index = int(os.environ.get("CHESS_ACC_INDEX") or "0")
    except:
        acc_index = 0
    try:
        return load_account_from_file(acc_file, acc_index)
    except:
        return "abul1", DEFAULT_ACC_PWWD

USER, PWWD = _resolve_account()

WS_URL = "wss://gamevh.net/ws/gameServer"
LOGIN_URL = "https://gamevh.net/login.jsp"
GAME_URL = "https://gamevh.net/play/chess/0"
PROFILE_URL = "https://gamevh.net/com/ftl/game/profile/player_profile.jsp"
VERSION = "5.0.2"
GAME_ID = "chess"
PLACE_PATH = "Lobby.chess.0"

RUNTIME = int(float(os.environ.get("CHESS_RUNTIME_HOURS", "5.7")) * 3600)
BOT_BET_XU = int(os.environ.get("CHESS_BET_XU") or "1000")
BOT_MODE = os.environ.get("CHESS_MODE") or "mixed"  # create / find / mixed
BOT_MATCH_DURATION = '1800'
BOT_TURN_DURATION = '60'
BOT_ACC_DURATION = '2'

# Bet amt IDs (sniffed from server):
# 0=50, 1=100, 2=250, 3=500, 4=1000, 5=2500, 6=5000, 7=10000, ...
BET_AMT_IDS = {50:0, 100:1, 250:2, 500:3, 1000:4, 2500:5, 5000:6, 10000:7, 25000:8, 50000:9, 100000:10}

# ==================== STOCKFISH ENGINE ====================
ENGINE_DIR = _resolve_base_dir() / "stockfish-engine"
SF_BINARY_NAMES = ["stockfish-linux-x86-64-universal", "stockfish", "stockfish-x86-64"]

def detect_stockfish():
    """Tìm Stockfish binary."""
    if ENGINE_DIR.exists():
        for name in SF_BINARY_NAMES:
            p = ENGINE_DIR / "stockfish" / name
            if p.exists():
                p.chmod(0o755)
                return str(p)
            p2 = ENGINE_DIR / name
            if p2.exists():
                p2.chmod(0o755)
                return str(p2)
        for f in ENGINE_DIR.glob("**/stockfish*"):
            if f.is_file():
                f.chmod(0o755)
                return str(f)
    return None

def auto_download_stockfish():
    """Download Stockfish nếu chưa có."""
    binary = detect_stockfish()
    if binary:
        return binary
    log.info("[SF] Downloading Stockfish 19...")
    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import tarfile, io
        url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(ENGINE_DIR)
        os.unlink(tmp_path)
        binary = detect_stockfish()
        if binary:
            log.info(f"[SF] Stockfish installed: {binary}")
            return binary
    except Exception as e:
        log.error(f"[SF] Download failed: {e}")
    return None

class StockfishEngine:
    """UCI protocol wrapper cho Stockfish."""
    def __init__(self, binary_path, depth=15, movetime=3000):
        self.binary = binary_path
        self.depth = depth
        self.movetime = movetime
        self.proc = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return True
            try:
                self.proc = subprocess.Popen(
                    [self.binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=str(ENGINE_DIR)
                )
                self._send("uci")
                for _ in range(20):
                    line = self._read_line(timeout=2)
                    if line and "uciok" in line:
                        break
                self._send("setoption name Threads value 2")
                self._send("setoption name Hash value 64")
                self._send("isready")
                for _ in range(10):
                    line = self._read_line(timeout=2)
                    if line and "readyok" in line:
                        break
                log.info(f"[SF] Stockfish started OK")
                return True
            except Exception as e:
                log.error(f"[SF] Start error: {e}")
                return False

    def _send(self, cmd):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write((cmd + "\n").encode())
                self.proc.stdin.flush()
            except:
                pass

    def _read_line(self, timeout=5):
        if not self.proc or self.proc.poll() is not None:
            return ""
        import selectors
        try:
            sel = selectors.DefaultSelector()
            sel.register(self.proc.stdout, selectors.EVENT_READ)
            ready = sel.select(timeout=timeout)
            sel.close()
            if ready:
                line = self.proc.stdout.readline()
                return line.decode("utf-8", errors="replace").strip()
        except:
            pass
        return ""

    def get_bestmove(self, fen, my_color="w"):
        """Trả về (source_pos, target_pos, promotion) hoặc None."""
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                if not self.start():
                    return None
            self._send(f"position fen {fen}")
            self._send(f"go depth {self.depth}")
            deadline = time.time() + self.movetime / 1000 + 10
            while time.time() < deadline:
                line = self._read_line(timeout=2)
                if not line:
                    continue
                if line.startswith("bestmove"):
                    parts = line.split()
                    if len(parts) >= 2:
                        uci_move = parts[1]
                        return self._uci_to_positions(uci_move)
                    break
            return None

    def _uci_to_positions(self, uci_move):
        """Convert UCI move (e.g. 'e2e4', 'e7e8q') to game positions."""
        if len(uci_move) < 4:
            return None
        src_file = ord(uci_move[0]) - ord('a')
        src_rank = int(uci_move[1]) - 1
        tgt_file = ord(uci_move[2]) - ord('a')
        tgt_rank = int(uci_move[3]) - 1
        # Game position: 0-63, pos = rank * 8 + file
        src_pos = src_rank * 8 + src_file
        tgt_pos = tgt_rank * 8 + tgt_file
        promotion = None
        if len(uci_move) >= 5:
            promo_map = {'q': 1, 'r': 2, 'b': 3, 'n': 4, 'Q': 7, 'R': 8, 'B': 9, 'N': 10}
            promotion = promo_map.get(uci_move[4])
        return (src_pos, tgt_pos, promotion)

    def stop(self):
        with self.lock:
            if self.proc:
                try:
                    self._send("quit")
                    self.proc.wait(3)
                except:
                    try: self.proc.kill()
                    except: pass
                self.proc = None

# ==================== BINARY PROTOCOL ====================
CMD_MAP = {
    300:"PONG",301:"PING",302:"LOGIN",303:"ALERT",311:"BROADCAST",
    401:"ENTER_PLACE",402:"ENTER_CHILD_PLACE",405:"CREATE_RULE",
    406:"PLAYER_ENTERED",407:"PLAYER_EXITED",410:"KICK_PLAYER",
    413:"LIST_BET_AMT",414:"GET_TABLE_DATA",417:"START_MATCH",
    418:"GAMEOVER",419:"ENTER_STATE",420:"SET_TURN",
    421:"SET_PLAYER_STATUS",422:"SET_PLAYER_POINT",423:"SET_PLAYER_ATTR",
    431:"BALANCE_CHANGED",432:"OWNER_CHANGED",433:"GET_TABLE_DATA_EX",
    434:"SET_READY",501:"BET",502:"PLAY",505:"CHAT",518:"HIGHLIGHT",
    529:"MOVE",
}

class BinaryReader:
    def __init__(self, data): self.d = data; self.o = 0
    def rem(self): return len(self.d) - self.o
    def i8(self):
        v = struct.unpack_from(">b", self.d, self.o)[0]; self.o += 1; return v
    def u8(self):
        v = self.d[self.o]; self.o += 1; return v
    def i16(self):
        v = struct.unpack_from(">h", self.d, self.o)[0]; self.o += 2; return v
    def u16(self):
        v = struct.unpack_from(">H", self.d, self.o)[0]; self.o += 2; return v
    def i32(self):
        v = struct.unpack_from(">i", self.d, self.o)[0]; self.o += 4; return v
    def i64(self):
        v = struct.unpack_from(">q", self.d, self.o)[0]; self.o += 8; return v
    def ascii(self):
        n = self.u8()
        s = self.d[self.o:self.o+n].decode("ascii","replace"); self.o += n; return s
    def utf16(self):
        n = self.i16() if self.rem() >= 2 else 0
        s = self.d[self.o:self.o+n*2].decode("utf-16-be","replace"); self.o += n*2; return s
    def read_command(self):
        first = self.i8()
        if first < 0:
            n = -first
            s = self.d[self.o:self.o+n].decode("ascii","replace"); self.o += n
            return s
        second = self.u8()
        cmd_id = (first << 8) | second
        return CMD_MAP.get(cmd_id, f"CMD_{cmd_id}")

class BinaryWriter:
    def __init__(self): self.parts = []
    def u8(self, v): self.parts.append(struct.pack(">B", v))
    def i8(self, v): self.parts.append(struct.pack(">b", v))
    def i16(self, v): self.parts.append(struct.pack(">h", v))
    def i32(self, v): self.parts.append(struct.pack(">i", v))
    def i64(self, v): self.parts.append(struct.pack(">q", v))
    def write_ascii(self, s):
        b = s.encode("ascii","replace"); self.u8(len(b)); self.parts.append(b)
    def write_utf(self, s):
        b = s.encode("utf-16-be"); self.i16(len(b)//2); self.parts.append(b)
    def write_command(self, cmd):
        cmd_id = next((k for k,v in CMD_MAP.items() if v == cmd), None)
        if cmd_id: self.parts.append(struct.pack(">H", cmd_id))
        else:
            b = cmd.encode("ascii"); self.i8(-len(b)); self.parts.append(b)
    def build(self): return b"".join(self.parts)

# ==================== CHESS BOARD ====================
# Piece encoding (from JS decodePieceId):
# White: K=0, Q=1, R=2, B=3, N=4, P=5
# Black: K=6, Q=7, R=8, B=9, N=10, P=11
PIECE_CHARS = {0:"K",1:"Q",2:"R",3:"B",4:"N",5:"P",6:"k",7:"q",8:"r",9:"b",10:"n",11:"p"}
CHAR_TO_PIECE = {v:k for k,v in PIECE_CHARS.items()}

class ChessBoard:
    """8x8 chess board, tracks pieces for FEN generation."""
    def __init__(self):
        self.squares = [None] * 64  # each: piece_id (0-11) or None
        self.white_to_move = True
        self.castling = "KQkq"
        self.en_passant = "-"
        self.halfmove = 0
        self.fullmove = 1
        self.last_move_src = None
        self.last_move_tgt = None

    def clear(self):
        self.squares = [None] * 64
        self.white_to_move = True
        self.castling = "KQkq"
        self.en_passant = "-"

    def setup_standard(self):
        """Setup standard starting position."""
        self.clear()
        # Black pieces (rank 8 = positions 56-63)
        self.squares[56] = 8  # r
        self.squares[57] = 9  # b
        self.squares[58] = 10  # n
        self.squares[59] = 7  # q
        self.squares[60] = 6  # k
        self.squares[61] = 9  # b
        self.squares[62] = 10  # n
        self.squares[63] = 8  # r
        for i in range(48, 56):
            self.squares[i] = 11  # p (black pawns)
        for i in range(8, 16):
            self.squares[i] = 5  # P (white pawns)
        # White pieces (rank 1 = positions 0-7)
        self.squares[0] = 2  # R
        self.squares[1] = 3  # B
        self.squares[2] = 4  # N
        self.squares[3] = 1  # Q
        self.squares[4] = 0  # K
        self.squares[5] = 3  # B
        self.squares[6] = 4  # N
        self.squares[7] = 2  # R

    def set_from_pieces(self, pieces):
        """Set board from list of (piece_id, position)."""
        self.squares = [None] * 64
        for pid, pos in pieces:
            if 0 <= pos < 64:
                self.squares[pos] = pid

    def make_move(self, src, tgt, promotion=None):
        """Apply a move to the board."""
        piece = self.squares[src]
        if piece is None:
            return
        # Handle promotion
        if promotion is not None:
            self.squares[tgt] = promotion
        else:
            self.squares[tgt] = piece
        self.squares[src] = None
        # Handle en passant (pawn diagonal capture to empty square)
        if piece in (5, 11) and src % 8 != tgt % 8 and self.squares[tgt] is None:
            # En passant capture
            cap_pos = (src // 8) * 8 + (tgt % 8)
            self.squares[cap_pos] = None
        # Handle castling (king moves 2 squares)
        if piece in (0, 6) and abs(tgt - src) == 2:
            if tgt > src:
                # Kingside: move rook
                rook_src = (src // 8) * 8 + 7
                rook_tgt = (src // 8) * 8 + 5
            else:
                # Queenside
                rook_src = (src // 8) * 8 + 0
                rook_tgt = (src // 8) * 8 + 3
            if 0 <= rook_src < 64 and 0 <= rook_tgt < 64:
                self.squares[rook_tgt] = self.squares[rook_src]
                self.squares[rook_src] = None
        self.last_move_src = src
        self.last_move_tgt = tgt
        self.white_to_move = not self.white_to_move

    def to_fen(self):
        """Generate FEN string for Stockfish."""
        rows = []
        for rank in range(7, -1, -1):
            row = ""
            empty = 0
            for file in range(8):
                pos = rank * 8 + file
                piece = self.squares[pos]
                if piece is None:
                    empty += 1
                else:
                    if empty > 0:
                        row += str(empty)
                        empty = 0
                    row += PIECE_CHARS.get(piece, "?")
            if empty > 0:
                row += str(empty)
            rows.append(row)
        fen = "/".join(rows)
        fen += f" {'w' if self.white_to_move else 'b'}"
        fen += f" {self.castling if self.castling else '-'}"
        fen += f" {self.en_passant}"
        fen += f" {self.halfmove} {self.fullmove}"
        return fen

    def pos_to_square(self, pos):
        """Convert 0-63 to algebraic (e.g. 0 -> a1, 63 -> h8)."""
        file = pos % 8
        rank = pos // 8
        return chr(ord('a') + file) + str(rank + 1)

# ==================== CHESS BOT ====================
class ChessBot:
    def __init__(self):
        self.ws = None
        self.board = ChessBoard()
        self.engine = None
        self.sf_binary = detect_stockfish() or auto_download_stockfish()
        self.slot = -1
        self.is_playing = False
        self.in_table = False
        self.ready = False
        self.players = {}
        self.nickname = ""
        self.token = 0
        self.cookie = ""
        self.place_path = PLACE_PATH
        self.table_id = None
        self.running = True
        self.start_time = None
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.total_games = 0
        self.pending_move = False
        self._moving = False
        self.bet_amts = []
        self._bet_amts_loaded = False
        self._resolved_bet_id = None
        self._joining_table = False
        self._lobby_idle_since = None
        self._last_create_at = 0
        self._scan_state = None  # find table mode
        self.player_slot_by_id = {}
        self.opponent_gone_at = None
        self._table_lost_at = None
        self._want_rejoin = False
        self._rejoining = False
        self._rejoin_attempts = 0
        self.balance = None
        self._create_fail_count = 0

    def init_engine(self):
        if self.engine is not None:
            return True
        if not self.sf_binary:
            log.error("[SF] No Stockfish binary!")
            return False
        try:
            self.engine = StockfishEngine(self.sf_binary, depth=15, movetime=5000)
            if self.engine.start():
                log.info(f"[SF] Stockfish ready")
                return True
            return False
        except Exception as e:
            log.error(f"[SF] Init error: {e}")
            return False

    @property
    def running_prop(self): return self.running

    def stop(self):
        self.running = False
        if self.engine:
            self.engine.stop()
            self.engine = None

    # ==================== PACKET BUILDERS ====================
    def make_login(self):
        w = BinaryWriter(); w.write_command("LOGIN"); w.write_ascii(self.nickname)
        w.i32(self.token); w.write_ascii(VERSION); w.write_ascii("")
        w.write_ascii(GAME_ID); w.i8(1); return w.build()

    def make_enter(self, path, pw="", mode=1):
        w = BinaryWriter(); w.write_command("ENTER_PLACE"); w.write_ascii(path)
        w.write_utf(pw); w.i8(mode); return w.build()

    def make_create_rule(self):
        bet_id = BET_AMT_IDS.get(BOT_BET_XU, 4)
        args = [("matchDuration", BOT_MATCH_DURATION), ("turnDuration", BOT_TURN_DURATION),
                ("accDuration", BOT_ACC_DURATION), ("blockSoftware", "0")]
        w = BinaryWriter(); w.write_command("CREATE_RULE"); w.i8(bet_id); w.i8(len(args))
        for name, val in args: w.write_ascii(name); w.write_utf(val)
        return w.build()

    def make_get_table(self):
        w = BinaryWriter(); w.write_command("GET_TABLE_DATA_EX"); w.write_ascii(""); return w.build()

    def make_play(self, src_pos, tgt_pos, promotion=None):
        w = BinaryWriter(); w.write_command("PLAY")
        w.i8(src_pos); w.i8(tgt_pos)
        if promotion is not None:
            w.i8(promotion)
        return w.build()

    def make_pong(self):
        w = BinaryWriter(); w.write_command("PONG"); return w.build()

    def make_ready(self):
        if self.is_playing: return b""
        w = BinaryWriter(); w.write_command("SET_READY"); return w.build()

    def make_kick(self, player_id):
        w = BinaryWriter(); w.write_command("KICK_PLAYER"); w.i64(player_id); return w.build()

    async def send(self, data):
        if self.ws and data:
            try: await self.ws.send(data)
            except: pass

    async def create_new_table(self):
        """Tạo bàn mới."""
        bet_id = BET_AMT_IDS.get(BOT_BET_XU, 4)
        log.info(f"[CREATE] Tạo bàn {BOT_BET_XU} xu (id={bet_id})")
        await self.send(self.make_create_rule())

    # ==================== PACKET HANDLERS ====================
    async def handle(self, raw):
        r = BinaryReader(raw)
        cmd = r.read_command()
        if cmd != "PING": log.info(f"RECV {cmd}")
        try:
            if cmd == "PING": await self.send(self.make_pong())
            elif cmd == "LOGIN": await self.handle_login(r)
            elif cmd == "ENTER_PLACE": await self.handle_enter(r)
            elif cmd == "LIST_BET_AMT": await self.handle_list_bet(r)
            elif cmd == "CREATE_RULE": await self.handle_create(r)
            elif cmd == "GET_TABLE_DATA_EX": await self.handle_table(r)
            elif cmd == "START_MATCH": await self.handle_start(r)
            elif cmd == "SET_TURN": await self.handle_turn(r)
            elif cmd == "MOVE": await self.handle_move(r)
            elif cmd == "PLAY": await self.handle_play(r)
            elif cmd == "GAMEOVER": await self.handle_gameover(r)
            elif cmd == "PLAYER_ENTERED": await self.handle_player_enter(r)
            elif cmd == "PLAYER_EXITED": await self.handle_player_exit(r)
            elif cmd == "BALANCE_CHANGED": await self.handle_balance(r)
            elif cmd == "KICK_PLAYER": await self.handle_kick(r)
        except Exception as e:
            log.error(f"Error {cmd}: {e}", exc_info=True)

    async def handle_login(self, r):
        status = r.i8()
        if status == 0:
            path = r.utf16()
            if path == "REFRESH":
                log.warning("[LOGIN] REFRESH — re-login needed")
                return
            if r.rem() > 0:
                lock_key = r.ascii()
            await self.send(self.make_enter(self.place_path))
        else:
            log.error("[LOGIN] Failed")

    async def handle_enter(self, r):
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
                    await self.send(self.make_enter(path))
                else:
                    # Vào sảnh OK → tạo bàn
                    await asyncio.sleep(0.3)
                    await self.create_new_table()
        else:
            if self._joining_table:
                self._joining_table = False
                if self._rejoining:
                    self._rejoining = False; self._rejoin_attempts += 1; self.table_id = None
                    await asyncio.sleep(1); await self.create_new_table()
                else:
                    await asyncio.sleep(1); await self.create_new_table()
            else:
                log.warning(f"[ENTER] Lỗi status={status}")
                await asyncio.sleep(3)
                await self.send(self.make_enter(self.place_path))

    async def handle_list_bet(self, r):
        status = r.i8()
        if status != 0: return
        count = r.u8()
        self.bet_amts = [{"id": i, "value": r.i32()} for i in range(count)]
        log.info(f"[BET] Mức cược: {[(b['id'], b['value']) for b in self.bet_amts[:5]]}")

    async def handle_create(self, r):
        status = r.i8()
        if status == 0:
            table_id = r.ascii()
            self.table_id = table_id
            self._create_fail_count = 0
            log.info(f"[CREATE] Bàn mới! id={table_id} ({BOT_BET_XU} xu)")
            await asyncio.sleep(0.5)
            await self.send(self.make_get_table())
        else:
            self._create_fail_count += 1
            log.warning(f"[CREATE] Thất bại (status={status}) — lần {self._create_fail_count}")
            if self._create_fail_count >= 6:
                self.table_id = None
                await asyncio.sleep(2)
                await self.send(self.make_enter(self.place_path))
            else:
                await asyncio.sleep(5)

    async def handle_table(self, r):
        if self._moving:
            log.info("[TABLE] Engine đang tính, skip")
            return
        try:
            first = r.i8()
            if first != 0:
                msg = r.utf16()
                if "not in table" in msg.lower():
                    self.in_table = False; self.table_id = None
                    await self.create_new_table()
                return
            # Parse table data
            seat_count = r.u8()
            for _ in range(seat_count):
                r.u8(); r.ascii(); r.u8(); child_count = r.u8()
                for _ in range(child_count):
                    r.u8(); r.ascii(); r.utf16(); r.u8(); r.u8()
            r.u8(); self.slot = r.i8(); is_playing = r.u8() == 1
            player_count = r.u8(); self.players = {}; self.player_slot_by_id = {}
            for _ in range(player_count):
                sid = r.i8(); pid = r.i64(); name = r.utf16()
                r.u16(); r.ascii(); r.i8(); r.i64(); r.i64(); r.i64(); r.u8(); r.u8()
                self.players[sid] = {"id": pid, "name": name}
                self.player_slot_by_id[pid] = sid
            current_player = r.i8(); r.i16(); r.i16(); r.u8()
            self.in_table = True
            # Read move list (chess-specific)
            move_count = r.u8()
            for _ in range(move_count):
                r.i8(); r.i32()
            # Read board data (chess pieces)
            # Board width/height not used for chess (always 8x8)
            # The board data format for chess is different from caro
            # For now, just log state
            has_opponent = any(sid >= 0 and sid != self.slot for sid in self.players)
            self.is_playing = is_playing
            log.info(f"[TABLE] Slot={self.slot} Playing={is_playing} Turn=slot{current_player} Players={len(self.players)}")
            if not is_playing and self.slot >= 0 and has_opponent and not self.ready:
                log.info("[BOT] Đối thủ vào → SET_READY!")
                self.ready = True
                await self.send(self.make_ready())
            elif not is_playing and self.slot >= 0 and not has_opponent:
                if self.ready:
                    log.info("[BOT] Không đối thủ → Hủy Ready")
                self.ready = False
            elif not is_playing and self.slot < 0:
                self.in_table = False; self.table_id = None
                await asyncio.sleep(1); await self.create_new_table()
            self._rejoining = False
        except Exception as e:
            log.error(f"[TABLE] Error: {e}")

    async def handle_start(self, r):
        self.total_games += 1; self.is_playing = True
        self.ready = False; self.pending_move = False; self._moving = False
        self.opponent_gone_at = None
        # Parse START_MATCH for chess
        # fillBoardData: count + pieces + lastMove + then firstTurnSlotId + mySlotId
        try:
            piece_count = r.u8()
            pieces = []
            for _ in range(piece_count):
                sid = r.u8(); face = r.u8(); pos = r.u8(); moved = r.u8()
                # Decode piece ID (simplified — may need adjustment)
                piece_id = face  # The 'face' byte encodes piece type
                pieces.append((piece_id, pos))
            last_src = r.u8(); last_tgt = r.u8()
            self.board.set_from_pieces(pieces)
            self.board.last_move_src = last_src
            self.board.last_move_tgt = last_tgt
            first_turn = r.i8()
            my_slot = r.i8()
            if my_slot >= 0: self.slot = my_slot
            # White = slot 0, Black = slot 1 (typically)
            self.board.white_to_move = (first_turn == 0)
            log.info(f"=== GAME {self.total_games} === Slot={self.slot} FirstTurn=slot{first_turn} Pieces={len(pieces)}")
            if self.engine is None:
                self.init_engine()
            else:
                self.engine.start()
            if self.slot < 0:
                await asyncio.sleep(0.5); await self.send(self.make_get_table())
        except Exception as e:
            log.error(f"[START] Parse error: {e}")

    async def handle_turn(self, r):
        sid = r.i8(); turn_timeout = r.i16(); acc_timeout = r.i16()
        if self.slot < 0: return
        if sid == self.slot and self.is_playing and self.running:
            if not self.pending_move and not self._moving:
                self.pending_move = True
                await asyncio.sleep(0.5)
                await self.do_move()
        # SET_READY khi đối thủ vào (lobby countdown)
        if (not self.is_playing and self.in_table and self.slot >= 0
                and not self.ready and sid != self.slot):
            log.info("[BOT] SET_READY (lobby countdown)")
            self.ready = True
            await self.send(self.make_ready())

    async def handle_move(self, r):
        """Server gửi move của đối thủ."""
        src = r.u8(); tgt = r.u8()
        # Optional promotion
        promotion = None
        if r.rem() > 0:
            try:
                promotion = r.u8()
            except:
                pass
        log.info(f"[MOVE] Đối thủ: {self.board.pos_to_square(src)}→{self.board.pos_to_square(tgt)}" +
                 (f" promo={promotion}" if promotion else ""))
        # Update board
        piece = self.board.squares[src]
        if piece is not None:
            self.board.make_move(src, tgt, promotion)

    async def handle_play(self, r):
        status = r.i8()
        if status != 0:
            log.warning(f"[PLAY] Lỗi status={status}")
            self.pending_move = False
            await asyncio.sleep(0.5); await self.send(self.make_get_table())

    async def handle_gameover(self, r):
        self.is_playing = False; self.pending_move = False
        player_count = r.u8()
        my_result = None
        for _ in range(player_count):
            sid = r.i8(); result = r.i8(); r.i64()
            if sid == self.slot: my_result = result
        if my_result in (1, 11): self.wins += 1; log.info(">>> WIN! <<<")
        elif my_result in (2, 4, 12): self.losses += 1; log.info(">>> LOSE! <<<")
        else: self.draws += 1; log.info(">>> DRAW! <<<")
        r.utf16()
        if self._table_lost_at is not None:
            self._table_lost_at = None
            await asyncio.sleep(2); await self.create_new_table()
            return
        log.info("[BOT] Sẵn sàng sau 5s...")
        asyncio.create_task(self._delay_ready(5.0))

    async def handle_player_enter(self, r):
        place_level = r.i8()
        pid = r.i64(); name = r.utf16()
        if r.rem() >= 36:
            r.i64(); r.i64(); r.ascii(); r.i32(); r.i32(); r.i8(); r.i64(); r.i8()
        if place_level < 4: return
        log.info(f"[BOT] {name} vào bàn")
        await self.send(self.make_get_table())
        if self.slot >= 0 and not self.is_playing and not self.ready:
            log.info("[BOT] SET_READY!")
            self.ready = True
            await self.send(self.make_ready())

    async def handle_player_exit(self, r):
        place_level = r.i8()
        pid = r.i64() if r.rem() >= 8 else -1
        if place_level < 4: return
        slot = self.player_slot_by_id.get(pid) if pid >= 0 else None
        if pid >= 0: self.player_slot_by_id.pop(pid, None)
        if slot is not None: self.players.pop(slot, None)
        if slot is not None and slot == self.slot:
            if self.is_playing:
                self.in_table = False; self._table_lost_at = time.time()
            else:
                self.in_table = False
                await asyncio.sleep(1); await self.create_new_table()
        elif self.is_playing:
            if self.opponent_gone_at is None:
                self.opponent_gone_at = time.time()
                log.info("[BOT] Đối thủ rời → chờ GAMEOVER")
        elif self.in_table:
            await self.send(self.make_get_table())

    async def handle_balance(self, r):
        try:
            slot = r.i8(); chip = r.i64()
            if self.slot < 0 or slot == self.slot:
                self.balance = chip
        except: pass

    async def handle_kick(self, r):
        status = r.i8(); content = r.utf16()
        log.warning(f"[KICK] {content}")
        self.is_playing = False; self.in_table = False; self.table_id = None
        await asyncio.sleep(1); await self.create_new_table()

    async def _delay_ready(self, delay):
        await asyncio.sleep(delay)
        if not self.is_playing and self.in_table:
            await self.send(self.make_get_table())
            if not self.is_playing and self.in_table:
                self.ready = True
                await self.send(self.make_ready())

    # ==================== DO MOVE ====================
    async def do_move(self):
        if not self.is_playing or not self.running or self.slot < 0: return
        if self._moving: return
        self._moving = True; self.pending_move = False
        try:
            if not self.sf_binary:
                log.error("[SF] No engine!")
                return
            if self.engine is None:
                self.init_engine()
            if self.engine:
                fen = self.board.to_fen()
                log.info(f"[SF] FEN: {fen[:60]}...")
                move = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: self.engine.get_bestmove(fen, "w" if self.slot == 0 else "b")
                    ), timeout=15
                )
                if move and self.is_playing and self.running:
                    src, tgt, promo = move
                    log.info(f"[MOVE] {self.board.pos_to_square(src)}→{self.board.pos_to_square(tgt)}" +
                             (f" promo={promo}" if promo else ""))
                    await self.send(self.make_play(src, tgt, promo))
                    self.board.make_move(src, tgt, promo)
                else:
                    log.warning("[SF] No move from engine")
        except asyncio.TimeoutError:
            log.warning("[SF] Timeout!")
        except Exception as e:
            log.error(f"[MOVE] Error: {e}")
        finally:
            self._moving = False

    # ==================== HTTP LOGIN ====================
    def http_login(self):
        try:
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            op.addheaders = [("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"),
                             ("Accept-Language", "vi-VN,vi;q=0.9")]
            op.open(LOGIN_URL, timeout=12).read()
            data = urllib.parse.urlencode({
                "redirect": "/", "USER_NAME": USER, "PASSWORD": PWWD,
                "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"
            }).encode()
            op.open(LOGIN_URL, data=data, timeout=12)
            g = op.open(GAME_URL, timeout=12).read().decode("utf-8", "replace")
            m_tok = re.search(r"var\s+token\s*=\s*(-?\d+)", g)
            m_nick = re.search(r"var\s+currentPlayerNickName\s*=\s*[\"']([^\"']+)[\"']", g)
            if not m_tok or not m_nick: return False
            self.cookie = "; ".join(f"{c.name}={c.value}" for c in cj)
            self.nickname = m_nick.group(1)
            self.token = int(m_tok.group(1))
            log.info(f"[HTTP] Login OK: nick={self.nickname}, token={self.token}")
            return True
        except Exception as e:
            log.error(f"[HTTP] Login error: {e}")
            return False

    # ==================== WATCHDOG ====================
    async def watchdog(self):
        while self.running:
            try: await asyncio.sleep(10)
            except: return
            if not self.running: return
            if self.start_time and time.time() - self.start_time > RUNTIME:
                self.stop(); return
            if not self.ws: continue
            # Antistuck
            if not self.is_playing and not self.in_table:
                if self._lobby_idle_since is None:
                    self._lobby_idle_since = time.time()
                elif time.time() - self._lobby_idle_since > 90:
                    log.warning("[ANTISTUCK] >90s sảnh → reset")
                    self._lobby_idle_since = time.time()
                    self._joining_table = False; self._rejoining = False
                    self.table_id = None
                    await self.send(self.make_enter(self.place_path))
            else:
                self._lobby_idle_since = None
            # Create table if standing
            if (not self.is_playing and not self.in_table and not self._joining_table
                    and not self._rejoining and time.time() - self._last_create_at > 15):
                self._last_create_at = time.time()
                await self.create_new_table()

    # ==================== MAIN RUN ====================
    async def run(self):
        self.start_time = time.time()
        self.nickname = USER
        log.info("=" * 60)
        log.info(f"BOT CHESS — Stockfish 19")
        log.info(f"User: {USER} | Bet: {BOT_BET_XU} xu | Mode: {BOT_MODE}")
        log.info(f"Runtime: {RUNTIME}s | Game: {GAME_ID}")
        log.info("=" * 60)

        # HTTP login
        login_ok = await asyncio.get_event_loop().run_in_executor(None, self.http_login)
        if not login_ok:
            log.error("HTTP login failed"); return

        # Init engine
        if not self.init_engine():
            log.error("Stockfish init failed"); return

        # Transfer xu
        try:
            from transfer_xu_bot import transfer_xu_async, KEEP_RESERVE
            _tx_reserve = int(os.environ.get("CHESS_TRANSFER_RESERVE") or KEEP_RESERVE)
            transfer_xu_async(USER, PWWD, dest_id=10055407, reserve=_tx_reserve)
            log.info(f"[TRANSFER] Chuyển xu 1 lần (chừa {_tx_reserve})")
        except: pass

        # WS connection loop
        while self.running:
            try:
                log.info(f"Connecting to {WS_URL}...")
                extra_headers = {}
                if self.cookie:
                    extra_headers["Cookie"] = self.cookie
                async with websockets.connect(WS_URL, additional_headers=extra_headers,
                                              ping_interval=None, max_size=2**20) as ws:
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
                                log.info(f"TEXT: {msg[:100]}")
                    except websockets.exceptions.ConnectionClosed as e:
                        log.warning(f"Connection closed: {e}")
                    finally:
                        watchdog_task.cancel()
                        try: await watchdog_task
                        except: pass
            except Exception as e:
                log.error(f"WS error: {e}")
            if self.running:
                log.info("Reconnect in 5s...")
                await asyncio.sleep(5)
        log.info(f"Stats: W={self.wins} L={self.losses} D={self.draws} G={self.total_games}")

# ==================== MAIN ====================
def main():
    bot = ChessBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        bot.stop()

if __name__ == "__main__":
    main()
