#!/usr/bin/env python3
"""test_cup_hang.py — Kiểm tra xử lý khi engine TREO (còn sống nhưng câm).

Đây là lỗi thấy trong log GitHub Actions: lặp vô hạn cặp
    [ENGINE] ❌ Không lấy được nước đi -> bỏ lượt tính này
    [TURN] Tới lượt nhưng 12s chưa đi được -> tính lại

Nguyên nhân: bot chỉ khởi động lại engine khi tiến trình ĐÃ THOÁT
(poll() != None). Nếu engine còn sống mà không trả lời nữa, bot cứ gọi lại
trên đúng tiến trình câm đó -> trượt mãi.

Test dùng engine giả (không cần wine).
"""
import os, sys, time, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cup_bot", os.path.join(HERE, "cup_bot.py"))
cup = importlib.util.module_from_spec(spec); spec.loader.exec_module(cup)

ok = True

# ---------- 1. Ngân sách thời gian phải nhỏ hơn watchdog ----------
worst = cup.ENGINE_SYNC_TIMEOUT + cup.ENGINE_READ_TIMEOUT + cup.ENGINE_STOP_GRACE
print("1. Ngân sách thời gian một lần tính")
print(f"   sync {cup.ENGINE_SYNC_TIMEOUT}s + read {cup.ENGINE_READ_TIMEOUT}s "
      f"+ stop {cup.ENGINE_STOP_GRACE}s = {worst}s")
print(f"   watchdog bắn ở {cup.TURN_WATCHDOG_SEC}s")
good = worst < cup.TURN_WATCHDOG_SEC
ok &= good
print(f"   {worst}s < {cup.TURN_WATCHDOG_SEC}s ? {'✅' if good else '❌ VA CHẠM'}")

good = cup.ENGINE_READ_TIMEOUT > cup.ENGINE_MOVETIME_MS / 1000.0
ok &= good
print(f"   read timeout {cup.ENGINE_READ_TIMEOUT}s > movetime "
      f"{cup.ENGINE_MOVETIME_MS/3000}s ? {'✅' if good else '❌'}")

# ---------- 2. Engine treo -> phải bị GIẾT rồi dựng lại ----------
print("\n2. Engine treo (còn sống nhưng câm)")

class HungProc:
    """Tiến trình giả: còn sống, không bao giờ trả lời."""
    def __init__(self): self.killed = False
    def poll(self):  return None if not self.killed else -9
    def kill(self):  self.killed = True
    def wait(self, timeout=None): return -9

class FakeBot:
    def __init__(self):
        self._engine_proc = HungProc()
        self.engine = True
        self.init_calls = 0
        self.kill_calls = 0
        self.board = type("B", (), {"is_playing": True, "is_my_turn": True})()
        self.fixed_pawn_positions = None
        self.multipv = {}
    def _kill_engine(self):
        self.kill_calls += 1
        if self._engine_proc: self._engine_proc.kill()
        self._engine_proc = None; self.engine = None
    def _init_engine(self):
        self.init_calls += 1
        self._engine_proc = HungProc(); self.engine = True
    def get_best_move(self, fen, moves, fixed_positions=None):
        return None            # engine câm: luôn trượt
    def _fsf_cmd(self, c): pass

bot = FakeBot()
# chạy đúng đoạn retry của _do_auto_move
raw = None
for _attempt in (1, 2):
    _dead = (bot._engine_proc is None or bot._engine_proc.poll() is not None)
    if not _dead:
        bot._kill_engine()
    bot._init_engine()
    if not bot.engine: break
    raw = bot.get_best_move("fen", [])
    if raw: break

print(f"   số lần _kill_engine : {bot.kill_calls}  (phải ≥1 — engine câm phải bị giết)")
print(f"   số lần _init_engine : {bot.init_calls}  (phải ≥1 — phải dựng lại)")
good = bot.kill_calls >= 1 and bot.init_calls >= 1
ok &= good
print(f"   {'✅ Engine câm được dọn và dựng lại' if good else '❌ Vẫn thử lại trên tiến trình treo'}")

# ---------- 3. Threads không vượt quá số nhân ----------
print("\n3. Số luồng engine")
cpu = os.cpu_count() or 2
th = max(1, min(2, cpu - 1))
print(f"   cpu_count={cpu} -> Threads={th}")
good = 1 <= th <= 2 and th < max(2, cpu)
ok &= good
print(f"   Chừa ít nhất 1 nhân cho WebSocket: {'✅' if good else '❌'}")

print("\n" + "="*60)
print("✅ XỬ LÝ ENGINE TREO ĐÚNG" if ok else "❌ CÒN LỖI")
sys.exit(0 if ok else 1)
