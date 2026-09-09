#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DRAIN TO BAN_XU — Gom xu từ TOÀN BỘ acc_valid_*.txt trên GitHub, chuyển 100%
số dư (+ xu quay vòng may mắn được) về acc Facebook "ban_xu" (jav jp).
================================================================================
- Tự tải acc_valid_1..8.txt từ GitHub raw (dùng file local nếu đã có)
- Gộp + dedupe toàn bộ account (~15.311 tk), chia lô 750 tk
- Mỗi lô gọi spin_and_transfer.py --execute --phase all --pipeline:
    login HTTP -> login WS -> quay hết lượt vòng quay -> TRANSFER 100% số dư
- Account chết / sai mật khẩu / số dư <= 200 xu: TỰ ĐỘNG BỎ QUA
- Log đầy đủ vào drain_logs/, tổng kết tổng xu đã chuyển

Cách chạy:
    python3 drain_to_banxu.py                 # quét TẤT CẢ 8 file, chạy thật
    python3 drain_to_banxu.py --only 1,3      # chỉ dùng acc_valid_1.txt + _3.txt
    python3 drain_to_banxu.py --limit 20      # chạy thử 20 tk đầu tiên
    python3 drain_to_banxu.py --dry           # chỉ liệt kê, không chuyển
    python3 drain_to_banxu.py --password XXX  # nếu acc dùng mật khẩu khác
"""
import argparse
import glob
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

# ==================== CONFIG ====================
# Token GitHub: lấy từ env GITHUB_TOKEN hoặc file .gh_token cùng thư mục
# (KHÔNG hardcode token vào file để tránh bị GitHub push protection chặn)
def _load_token():
    import os as _os
    tok = _os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    for p in (_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".gh_token"),
              ".gh_token"):
        try:
            if _os.path.exists(p):
                t = open(p).read().strip()
                if t:
                    return t
        except Exception:
            pass
    return ""

GITHUB_TOKEN = _load_token()
REPO_RAW = "https://raw.githubusercontent.com/nguyen05566/vht/main"
ACC_FILES = [f"acc_valid_{i}.txt" for i in range(1, 9)]
ACC_DIR = "acc_files"

DEST_ID = 10055407        # acc Facebook "ban_xu" (jav jp) — đích nhận xu
DEST_NAME = "ban_xu"
DEFAULT_PASS = "nhat123456"   # mật khẩu chung (arena11 / acc_valid_2 đã xác nhận)

WORKER = "spin_and_transfer.py"   # worker có sẵn: quay vòng + chuyển 100%
LOG_DIR = "drain_logs"
# ================================================


def download_acc_files(only):
    """Tải acc_valid_*.txt từ GitHub vào ACC_DIR. Trả về danh sách file local."""
    os.makedirs(ACC_DIR, exist_ok=True)
    files = []
    for name in ACC_FILES:
        if only and not any(f"_{n}." in name for n in only):
            continue
        local = os.path.join(ACC_DIR, name)
        if not os.path.exists(local):
            url = f"{REPO_RAW}/{name}"
            req = urllib.request.Request(url, headers={"Authorization": f"token {GITHUB_TOKEN}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r, open(local, "wb") as f:
                    f.write(r.read())
                print(f"  ⬇ {name}: {os.path.getsize(local):,} bytes")
            except Exception as e:
                print(f"  ✗ {name}: {e}")
                continue
        else:
            print(f"  ✓ {name} (đã có local)")
        files.append(local)
    return files


def load_users(files):
    """Gom + dedupe username từ các file (không phân biệt hoa thường)."""
    seen, users = set(), []
    for fp in files:
        n = 0
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    name = line.strip().split("\t")[0].strip()
                    if name and not name.startswith("#") and name.lower() not in seen:
                        seen.add(name.lower())
                        users.append(name)
                        n += 1
        except Exception as e:
            print(f"  Lỗi đọc {fp}: {e}")
        print(f"  {os.path.basename(fp)}: {n} tk")
    return users


def write_batches(users, prefix, size):
    paths = []
    for i, start in enumerate(range(0, len(users), size)):
        p = f"{prefix}_{i:02d}.txt"
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(users[start:start + size]) + "\n")
        paths.append(p)
    return paths


def run_batch(batch_file, args, idx, total):
    """Chạy worker cho 1 lô. Trả (ok_count, xu, fail_count)."""
    log_path = os.path.join(LOG_DIR, f"drain_{time.strftime('%Y%m%d_%H%M%S')}_{idx}.log")
    cmd = [sys.executable, WORKER,
           "--list", batch_file,
           "--password", args.password,
           "--dest", str(args.dest),
           "--execute", "--phase", "all", "--pipeline",
           "--batch-size", str(args.batch_size),
           "--batch-pause", str(args.batch_pause),
           "--phase-gap", str(args.phase_gap),
           "--delay-min", str(args.delay_min),
           "--delay-max", str(args.delay_max),
           "--workers", str(args.workers)]
    print(f"\n{'=' * 66}\nLÔ {idx}/{total} — {batch_file} → log: {log_path}\n{'=' * 66}")
    ok = xu = fail = 0
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
        with open(log_path, "w", encoding="utf-8") as lf:
            for line in proc.stdout:
                lf.write(line)
                if re.search(r"TRANSFER [\d,]+ x ->", line):
                    ok += 1
                    m = re.search(r"TRANSFER ([\d,]+) x", line)
                    if m:
                        xu += int(m.group(1).replace(",", ""))
                elif "❌" in line:
                    fail += 1
                if "✅" in line or "❌" in line or "⏭️" in line:
                    print("  " + line.rstrip())
        proc.wait(timeout=60)
    except Exception as e:
        print(f"  Lỗi lô {idx}: {e}")
    return ok, xu, fail


def main():
    ap = argparse.ArgumentParser(description="Gom toàn bộ xu acc_valid về ban_xu")
    ap.add_argument("--only", default="", help="chỉ dùng file số này, vd: 1,3")
    ap.add_argument("--limit", type=int, default=0, help="giới hạn tổng số tk (0=hết)")
    ap.add_argument("--password", "--pwd", default=DEFAULT_PASS)
    ap.add_argument("--dest", type=int, default=DEST_ID, help="playerId nhận xu")
    ap.add_argument("--workers", type=int, default=8,
                    help="số luồng (default 8 — ít luồng cho giống người)")
    ap.add_argument("--batch-size", type=int, default=120)
    ap.add_argument("--batch-pause", type=int, default=30,
                    help="nghỉ giữa các lô (ngẫu nhiên x1-x3)")
    ap.add_argument("--delay-min", type=float, default=8.0,
                    help="delay ngẫu nhiên tối thiểu trước mỗi acc (giây)")
    ap.add_argument("--delay-max", type=float, default=25.0,
                    help="delay ngẫu nhiên tối đa trước mỗi acc (giây)")
    ap.add_argument("--phase-gap", type=int, default=15)
    ap.add_argument("--no-shuffle", action="store_true",
                    help="không xáo trộn thứ tự account")
    ap.add_argument("--exclude", default="",
                    help="file chứa tên acc cần BỎ QUA (giữ làm vốn) — cách nhau bằng dòng mới")
    ap.add_argument("--dry", action="store_true", help="chỉ liệt kê, không chạy")
    args = ap.parse_args()

    only = [x for x in args.only.split(",") if x.strip()]
    t0 = time.time()

    print("=" * 66)
    print(f"DRAIN TO {DEST_NAME} (id={args.dest}) — quay vòng + chuyển 100% số dư")
    print("=" * 66)

    if not os.path.exists(WORKER):
        print(f"✗ Thiếu worker {WORKER} — phải nằm cùng thư mục!")
        return 1
    os.makedirs(LOG_DIR, exist_ok=True)

    print("\n[1/3] Tải danh sách account từ GitHub:")
    files = download_acc_files(only)
    if not files:
        print("✗ Không có file account nào!")
        return 1

    print(f"\n[2/3] Đọc + dedupe account:")
    users = load_users(files)
    if args.exclude:
        try:
            ex = set()
            with open(args.exclude, encoding="utf-8") as ef:
                for line in ef:
                    n = line.strip().split("\t")[0].strip().lower()
                    if n:
                        ex.add(n)
            before = len(users)
            users = [u for u in users if u.lower() not in ex]
            print(f"  loại {before - len(users)} tk theo --exclude ({args.exclude})")
        except Exception as e:
            print(f"  ⚠️ không đọc được --exclude: {e}")
    if args.no_shuffle:
        print("  (giữ thứ tự gốc)")
    else:
        random.shuffle(users)   # xáo trộn để không đi tuần tự a->z như máy móc
        print("  đã xáo trộn thứ tự account (giống người)")
    if args.limit > 0:
        users = users[:args.limit]
    print(f"  → TỔNG: {len(users)} tk duy nhất | mật khẩu: {args.password}")

    # Lưu danh sách.tk lần này sẽ chạy — để quản lý vốn dự phòng, lần sau --exclude file này
    os.makedirs(LOG_DIR, exist_ok=True)
    sel_path = os.path.join(LOG_DIR, f"selected_{time.strftime('%Y%m%d_%H%M%S')}_{len(users)}.txt")
    with open(sel_path, "w", encoding="utf-8") as sf:
        sf.write("\n".join(users) + "\n")
    print(f"  📋 danh sách.tk lần này: {sel_path} (lần sau dùng --exclude {sel_path} để giữ vốn)")
    print(f"  ⚙️  CHẾ ĐỘ GIỐNG NGƯỜI: {args.workers} luồng | delay ngẫu nhiên "
          f"{args.delay_min:.0f}-{args.delay_max:.0f}s/acc | lô {args.batch_size} | "
          f"nghỉ lô {args.batch_pause}-{args.batch_pause * 3}s")

    if args.dry:
        print("\n[DRY] Không chạy. Lệnh thật sẽ là:")
        print(f"  spin_and_transfer.py --list <lô> --password {args.password} "
              f"--dest {args.dest} --execute --phase all --pipeline --workers {args.workers}")
        return 0

    print(f"\n[3/3] Chia lô {args.batch_size} tk + chạy {args.workers} luồng:")
    batches = write_batches(users, "batch_bx", args.batch_size)
    print(f"  → {len(batches)} lô")

    tot_ok = tot_xu = tot_fail = 0
    for i, bf in enumerate(batches, 1):
        ok, xu, fail = run_batch(bf, args, i, len(batches))
        tot_ok += ok; tot_xu += xu; tot_fail += fail
        print(f"  ▸ Lô {i}: +{ok} tk chuyển thành công | +{xu:,} xu | {fail} lỗi")
        try:
            os.remove(bf)
        except Exception:
            pass
        if i < len(batches):
            bp = random.uniform(args.batch_pause, args.batch_pause * 3)
            print(f"  Nghỉ {bp:.0f}s…")
            time.sleep(bp)

    mins = (time.time() - t0) / 60
    print(f"\n{'=' * 66}")
    print(f"HOÀN TẤT: {tot_ok}/{len(users)} tk sống | tổng chuyển {tot_xu:,} xu "
          f"→ {DEST_NAME} (id={args.dest}) | {tot_fail} lỗi | {mins:.1f} phút")
    print(f"Log chi tiết: {LOG_DIR}/")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
