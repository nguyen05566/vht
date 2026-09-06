#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CHIA LÔ + QUAY + CHUYỂN XU TỪ FILE acc*.txt (TRỰC TIẾP, KHÔNG ĐỐI CHIẾU, KHÔNG LƯU CSV)
======================================================================================
1) Đọc tài khoản từ pattern file (acc*.txt)
2) Chia lô trực tiếp (không pre-scan, không đối chiếu file cũ)
3) Chạy quay & chuyển xu từng lô (acc lỗi sẽ tự động bỏ qua)
"""
import argparse
import glob
import os
import subprocess
import sys
import time


def load_all_accounts(pattern="acc*.txt"):
    """Gom tất cả username từ các file acc*.txt."""
    seen = set()
    users = []
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"Không tìm thấy file nào khớp '{pattern}'")
        return users, files
    for fp in files:
        count = 0
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    name = line.split("\t")[0].strip()
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        users.append(name)
                        count += 1
        except Exception as e:
            print(f"  Lỗi đọc {fp}: {e}")
        print(f"  {fp}: {count} tk")
    return users, files


def write_batch(users, prefix="batch_scanned", size=750):
    """Chia danh sách thành các file lô. Trả danh sách file đã tạo."""
    files = []
    for i, start in enumerate(range(0, len(users), size)):
        chunk = users[start:start + size]
        path = f"{prefix}_{i:02d}.txt"
        with open(path, "w", encoding="utf-8") as f:
            for u in chunk:
                f.write(f"{u}\n")
        files.append(path)
        print(f"  {path}: {len(chunk)} tk")
    return files


def main():
    ap = argparse.ArgumentParser(
        description="Chia lô + quay + chuyển xu từ acc*.txt (trực tiếp)")
    ap.add_argument("--pattern", default="acc*.txt",
                    help="Pattern file chứa tk (default: acc*.txt)")
    ap.add_argument("--password", "--pwd", required=True,
                    help="MK chung các tk")
    ap.add_argument("--dest", type=int, default=65692738,
                    help="PlayerId nhận xu")
    ap.add_argument("--batch-size", type=int, default=750,
                    help="Tk mỗi lô (default: 750)")
    ap.add_argument("--workers", type=int, default=30,
                    help="Số luồng quay/chuyển (default: 30)")
    ap.add_argument("--batch-pause", type=int, default=10,
                    help="Nghỉ giữa các lô (giây)")
    ap.add_argument("--phase-gap", type=int, default=15,
                    help="Nghỉ giữa quay và chuyển (giây)")
    args = ap.parse_args()

    t_start = time.time()

    # ===== BƯỚC 1: GOM TÀI KHOẢN =====
    print("=" * 70)
    print("BƯỚC 1: Đọc tài khoản từ", args.pattern)
    print("=" * 70)
    users, files = load_all_accounts(args.pattern)
    print(f"\nTổng: {len(users)} tk duy nhất từ {len(files)} file")
    if not users:
        print("Không có tk nào để xử lý.")
        return

    # ===== BƯỚC 2: CHIA LÔ =====
    print(f"\n{'=' * 70}")
    print(f"BƯỚC 2: Chia {len(users)} tk thành các lô ({args.batch_size} tk/lô)")
    print("=" * 70)
    batch_files = write_batch(users, prefix="batch_run", size=args.batch_size)
    print(f"\nTổng {len(batch_files)} lô")

    # ===== BƯỚC 3: QUAY + CHUYỂN XU TỪNG LÔ =====
    print(f"\n{'=' * 70}")
    print(f"BƯỚC 3: Quay + Chuyển xu ({len(batch_files)} lô, {args.workers} luồng)")
    print("=" * 70)

    for i, bf in enumerate(batch_files, 1):
        print(f"\n{'🔶' * 20}")
        print(f"LÔ {i}/{len(batch_files)} — {bf}")
        print(f"{'🔶' * 20}")

        cmd = [
            sys.executable, "spin_and_transfer.py",
            "--list", bf,
            "--password", args.password,
            "--dest", str(args.dest),
            "--execute", "--phase", "all",
            "--pipeline",
            "--batch-size", str(args.batch_size),
            "--batch-pause", str(args.batch_pause),
            "--phase-gap", str(args.phase_gap),
            "--workers", str(args.workers)
        ]

        result = subprocess.run(cmd, timeout=1800)
        if result.returncode != 0:
            print(f"  Lô {i} kết thúc (code={result.returncode}), tiếp tục lô tiếp")

        # Dọn file lô tạm
        try:
            if os.path.exists(bf):
                os.remove(bf)
        except Exception:
            pass

        if i < len(batch_files):
            print(f"Nghỉ {args.batch_pause}s giữa các lô...")
            time.sleep(args.batch_pause)

    wall = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"HOÀN TẤT — {len(users)} tk, {len(batch_files)} lô, {wall/60:.1f} phút")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
