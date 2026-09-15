#!/usr/bin/env python3
"""test_cup_engine.py — Kiểm tra end-to-end: PKJQ.exe (wine) + FEN cờ úp + dịch nước đi.

Chạy engine thật, nạp thế cờ úp, lấy bestmove, dịch ngược về ô server và kiểm tra:
  - Nước đi xuất phát từ quân CỦA BÊN ĐI (không ra nước hộ đối thủ)
  - Ô server nằm đúng nửa bàn của bên đi
  - MultiPV = 1 (mặc định) và KHÔNG có log RAM-LEARN thay thế nước đi
"""
import os, sys, importlib.util, subprocess, time, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cup_bot", os.path.join(HERE, "cup_bot.py"))
cup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cup)


def find(paths):
    return next((p for p in paths if os.path.isfile(p)), None)


wine = find(["/usr/lib/wine/wine64", "/usr/bin/wine64", shutil.which("wine64") or "",
             shutil.which("wine") or ""])
pkjq = find([os.path.join(HERE, "Jieqibox/Engines/PikaJieQi0111/PKJQ.exe"),
             os.path.join(HERE, "jieqibox/Jieqibox/Engines/PikaJieQi0111/PKJQ.exe")])
if not wine or not pkjq:
    print(f"❌ Thiếu wine ({wine}) hoặc PKJQ.exe ({pkjq})")
    sys.exit(1)

env = os.environ.copy()
env.update(WINEPREFIX=os.path.expanduser("~/.winepfx"), WINEDEBUG="-all", DISPLAY="")
env.setdefault("XDG_RUNTIME_DIR", tempfile.gettempdir())


def run_engine(cmds):
    p = subprocess.run([wine, pkjq], input="\n".join(cmds) + "\n",
                       capture_output=True, text=True, env=env,
                       cwd=os.path.dirname(pkjq), timeout=120)
    return p.stdout.replace("\r", "")


BAG = "A2B2N2R2C2P5a2b2n2r2c2p5"
BOARD = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX"

print("=== 1. Engine hiểu FEN cờ úp ('x'/'X') ===")
out = run_engine(["uci", "setoption name EvalFile value pikafish.nnue", "isready",
                  f"position fen {BOARD} {BAG} w - - 0 1", "d", "quit"])
fen_echo = next((l for l in out.splitlines() if l.startswith("Fen:")), "")
print("  " + (fen_echo or "❌ engine không echo FEN"))
assert BOARD in fen_echo, "engine không nạp đúng thế cờ úp"
print("  ✅ PKJQ nạp đúng thế cờ úp")

print("\n=== 2. MultiPV mặc định của bot ===")
print(f"  ENGINE_MULTIPV = {cup.ENGINE_MULTIPV} "
      f"(chỉ tạm nâng lên {cup.ENGINE_MULTIPV_FALLBACK} khi phải né chốt khóa)")
assert cup.ENGINE_MULTIPV == 1
assert not hasattr(cup, "TrendAnalyzer"), "TrendAnalyzer (RAM-learn) vẫn còn!"
src = open(os.path.join(HERE, "cup_bot.py")).read()
assert "select_best_trend_move" not in src, "vẫn còn hàm thay thế nước đi RAM-learn"
print("  ✅ Đã gỡ RAM-learn: bot dùng thẳng bestmove của engine")

print("\n=== 3. Bestmove cho từng bên + dịch về ô server ===")
Tracker = cup.XiangqiBoardTracker
allok = True
for side, side_name in (("w", "ĐỎ"), ("b", "ĐEN")):
    out = run_engine(["uci", "setoption name EvalFile value pikafish.nnue", "isready",
                      f"position fen {BOARD} {BAG} {side} - - 0 1",
                      "go movetime 4000", "quit"])
    bm = next((l.split()[1] for l in out.splitlines() if l.startswith("bestmove")), None)
    assert bm and bm not in ("(none)", "0000"), f"engine không ra nước cho bên {side}"
    src_rank = int(bm[1])
    # bên ĐỎ đi từ rank thấp, bên ĐEN đi từ rank cao
    own = (src_rank <= 4) if side == "w" else (src_rank >= 5)

    for flip in (True, False):
        b = Tracker()
        b.flip = flip
        b.is_red = (side == "w")
        s, t = b.engine_move_to_pos(bm)
        back = b.pos_to_engine_move(s, t)
        ok = (back == bm) and 0 <= s < 90 and 0 <= t < 90
        allok &= ok and own
        print(f"  {side_name}: bestmove {bm} | flip={flip} -> server pos {s}->{t} "
              f"| dịch ngược '{back}' {'✅' if ok else '❌'}"
              f"{'' if own else '  ❌ ra nước hộ đối thủ!'}")

print("\n" + "=" * 60)
print("✅ ENGINE + FLIP + MULTIPV ĐỀU ĐẠT" if allok else "❌ CÒN LỖI")
sys.exit(0 if allok else 1)
