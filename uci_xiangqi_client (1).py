#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uci_xiangqi_client.py — Ví dụ cách "gọi" engine cờ Tướng (Pikafish / PikaJieQi)
trong bộ Jieqibox để cho engine sinh nước đi và tự chơi.

Cả 3 engine trong bộ cài (pikafish-avx2.exe, pikafish-modern.exe, PKJQ.exe)
đều là chương trình CONSOLE nói giao thức UCI qua stdin/stdout:

    GUI  --lệnh文本-->  stdin  --> ENGINE
    GUI  <--kết quả--  stdout <-- ENGINE

Cách Jieqibox (app Tauri/Rust) làm:
    - lệnh `spawn_engine`  : khởi động tiến trình engine
    - lệnh `send_to_engine`: ghi một dòng lệnh UCI vào stdin
    - sự kiện `engine-output`: đẩy từng dòng stdout về giao diện web

Cách viết bằng Python (file này): dùng subprocess + thread đọc stdout.

Chạy thử trên Windows:
    python uci_xiangqi_client.py "Engines\\pikafish-modern\\pikafish-modern.exe"
    python uci_xiangqi_client.py "Engines\\PikaJieQi0111\\PKJQ.exe"
"""

import subprocess
import threading
import queue
import time
import sys

# FEN thế trận khai cuộc cờ Tướng (chuẩn Pikafish/XQ)
# CỜ ÚP (揭棋, Jieqi): x/X = quân úp ẩn (ô mã đi như mã, ô pháo như pháo...),
# k/K = 2 tướng lộ sẵn. Đuôi "A2B2N2R2C2P5a2b2n2r2c2p5" = túi quân chưa lật
# mỗi bên (2 sỹ + 2 tượng + 2 mã + 2 xe + 2 pháo + 5 tốt = 15 quân úp).
START_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX A2B2N2R2C2P5a2b2n2r2c2p5 w - - 0 1"


class UciEngine:
    """Vỏ bọc giao thức UCI cho một tiến trình engine cờ ÚP (Jieqi)."""

    def __init__(self, path: str, name: str = "engine"):
        # KHỞI ĐỘNG TIẾN TRÌNH ENGINE (như spawn_engine của Jieqibox)
        self.proc = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,               # theo dòng (line-buffered)
            encoding="utf-8",
            errors="replace",
            cwd=str(__import__("pathlib").Path(path).parent),  # để engine tìm file .nnue
        )
        self.name = name
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.info_lines = []          # các dòng "info ..." gần nhất
        self.bestmove = None          # nước đi trả về cuối cùng
        self.id_info = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---------------- đọc stdout của engine ----------------
    def _read_loop(self):
        for line in self.proc.stdout:          # mỗi dòng UCI là một thông điệp
            line = line.rstrip("\n").strip()
            if not line:
                continue
            self.lines.put(line)
            if line.startswith("id "):
                key, _, val = line[3:].partition(" ")
                self.id_info[key] = val
            elif line.startswith("info "):
                self.info_lines.append(line)
                self.info_lines = self.info_lines[-30:]   # giữ 30 dòng gần nhất
            elif line.startswith("bestmove"):
                self.bestmove = line.split()[1]           # ví dụ: bestmove h2e2
            # (ta cứ đẩy mọi dòng vào queue để hàm wait_for dùng)

    # ---------------- ghi lệnh vào stdin ----------------
    def send(self, cmd: str):
        """Gửi một dòng lệnh UCI, ví dụ 'go movetime 1000'."""
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def wait_for(self, prefix: str, timeout: float = 10.0) -> str:
        """Chờ cho tới khi engine trả về dòng bắt đầu bằng `prefix`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line
        raise TimeoutError(f"Engine {self.name} không trả lời '{prefix}' trong {timeout}s")

    # ---------------- các bước bắt tay UCI ----------------
    def handshake(self):
        """uci -> uciok ; isready -> readyok. Bắt buộc trước khi dùng engine."""
        self.send("uci")
        while True:
            line = self.wait_for("uciok", timeout=10)
            break
        self.send("isready")
        self.wait_for("readyok")
        return self.id_info          # {'name': 'Pikafish ...', 'author': ...}

    def new_game(self):
        """Báo engine ván mới (xóa_history/HT)."""
        self.send("ucinewgame")
        self.send("isready")
        self.wait_for("readyok")

    def set_option(self, name: str, value):
        self.send(f"setoption name {name} value {value}")

    # ---------------- đặt thế trận ----------------
    def position(self, fen: str = None, moves=None):
        """
        Nói cho engine biết thế cờ hiện tại.
          - fen=None  -> position startpos
          - moves     -> danh sách nước đi dạng ICCS, ví dụ ['h2e2', 'h9g7']
        Nước đi cờ (kể cả lật quân úp): cột a..i (9 cột), hàng 0..9, ví dụ
        'h2e2' = pháo đầu vào giữa (nếu quân úp ở h2 đi, nó sẽ được LẬT thành
        quân ngẫu nhiên — engine/GUI cập nhật FEN mới sau khi lộ).
        """
        moves = moves or []
        base = f"position fen {fen}" if fen else "position startpos"
        if moves:
            base += " moves " + " ".join(moves)
        self.send(base)

    # ---------------- bảo engine nghĩ ----------------
    def go(self, movetime_ms: int = None, depth: int = None,
           infinite: bool = False, wtime=None, btime=None, winc=0, binc=0):
        """
        Ra lệnh tìm nước đi. Các dạng hay dùng:
          go movetime 1000        -> nghĩ đúng 1 giây
          go depth 12             -> tìm sâu 12 nước
          go wtime 60000 btime 60000 winc 1000 binc 1000  -> đánh theo giờ
          go infinite             -> phân tích mãi (dừng bằng lệnh 'stop')
        """
        self.bestmove = None
        self.info_lines.clear()
        cmd = "go"
        if movetime_ms is not None:
            cmd += f" movetime {movetime_ms}"
        if depth is not None:
            cmd += f" depth {depth}"
        if wtime is not None:
            cmd += f" wtime {wtime} btime {btime} winc {winc} binc {binc}"
        if infinite:
            cmd += " infinite"
        self.send(cmd)

    def best_move(self, timeout: float = 60.0) -> str:
        """Chờ engine tính xong, lấy nước tốt nhất (bestmove)."""
        self.wait_for("bestmove", timeout=timeout)
        return self.bestmove

    def last_analysis(self):
        """Trả về (độ sâu, điểm số centipawn, chuỗi biến chính PV) từ info cuối."""
        depth, score, pv = None, None, ""
        for line in reversed(self.info_lines):
            parts = line.split()
            if "pv" in parts:
                pv = " ".join(parts[parts.index("pv") + 1:])
            if "score cp" in line:
                score = int(parts[parts.index("score") + 2])
            if "depth" in parts:
                depth = int(parts[parts.index("depth") + 1])
            if depth and score:
                break
        return depth, score, pv

    # ---------------- kết thúc ----------------
    def stop(self):
        self.send("stop")            # dừng 'go infinite'

    def quit(self):
        try:
            self.send("quit")        # lệnh thoát chuẩn của UCI
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


# ===================== DEMO 1: bảo engine đi một nước =====================
def demo_single_move(path):
    print("=" * 60)
    print(f"DEMO 1 — Gọi engine: {path}")
    eng = UciEngine(path)
    info = eng.handshake()
    print(f"  Engine nhận dạng : {info.get('name', '?')}")
    print(f"  Tác giả          : {info.get('author', '?')}")

    eng.new_game()
    # Ví dụ 1: thế trận khai cuộc, Trắng đi trước, nghĩ 1 giây
    eng.position(fen=START_FEN)
    eng.go(movetime_ms=1000)
    mv = eng.best_move()
    depth, score, pv = eng.last_analysis()
    print(f"  Nước tốt nhất    : {mv}")
    print(f"  Độ sâu {depth}, điểm {score} cp, biến chính: {pv}")

    # Ví dụ 2: tính theo chuỗi nước đã đi (startpos + moves)
    eng.position(moves=["h2e2", "h9g7", "h0g2"])
    eng.go(depth=10)
    mv2 = eng.best_move()
    print(f"  Sau 3 nước, engine chọn: {mv2}")

    eng.quit()
    print("  -> Hoàn tất: engine ĐÁNH ĐƯỢC, giao tiếp bằng văn bản UCI.\n")


# ============ DEMO 2: hai engine (hoặc engine với chính nó) tự đánh ============
def demo_self_play(path_a, path_b=None, max_moves=60, movetime_ms=200):
    print("=" * 60)
    print(f"DEMO 2 — Tự chơi: {path_a} vs {path_b or path_a}")
    red = UciEngine(path_a, "ĐỎ")
    black = UciEngine(path_b or path_a, "ĐEN")
    red.handshake(); black.handshake()
    red.new_game(); black.new_game()

    moves, fen = [], START_FEN
    for ply in range(max_moves):
        side = red if ply % 2 == 0 else black
        # cả hai engine phải biết cùng một thế cờ
        for e in (red, black):
            e.position(fen=fen)
            e.go(movetime_ms=movetime_ms)
        mv = red.best_move()
        if not mv or mv == "(none)":
            print(f"  ĐỎ hết nước (thua/bị chiếu chết) sau {ply} nước.")
            break
        mv_b = black.best_move()
        if mv_b != mv:
            # nếu hai engine khác nhau, lấy nước của bên đi
            mv = mv if ply % 2 == 0 else mv_b
        moves.append(mv)
        depth, score, pv = side.last_analysis()
        print(f"  Nước {ply//2+1:2d} [{side.name:3s}]: {mv}   (sâu {depth}, điểm {score})")
        if mv == "(none)":
            print(f"  {side.name} thua.")
            break
        # Cập nhật FEN đơn giản: chuyển lượt đi (w<->b) — thực tế nên dùng
        # thư viện cờ Tướng (ví dụ python-xiangqi) để sinh FEN chính xác.
        fen = fen.replace(" w ", " b ") if ply % 2 == 0 else fen.replace(" b ", " w ")

    red.quit(); black.quit()
    print("  Chuỗi nước đi (ICCS):", " ".join(moves), "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    demo_single_move(sys.argv[1])
    if len(sys.argv) >= 3:
        demo_self_play(sys.argv[1], sys.argv[2])
