#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DÒ TÀI KHOẢN TỪ acc*.txt + CHIA LÔ + QUAY + CHUYỂN XU
============================================================
1) Gom tất cả acc*.txt trong repo, deduplicate
2) Dò nhanh (HTTP-only) xem tk nào còn hợp lệ → acc_scanned_valid.txt
3) Chia lô → chạy spin_and_transfer.py cho từng lô

Dùng riêng, không đăng ký mới.
"""
import argparse
import glob
import os
import subprocess
import sys
import time


def load_all_accounts(pattern="acc*.txt"):
    """Gom tất cả username từ các file acc*.txt, deduplicate, giữ thứ tự."""
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
                    # acc*.txt có thể là tab-separated: username\tdate\trun_id
                    name = line.split("\t")[0].strip()
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        users.append(name)
                        count += 1
        except Exception as e:
            print(f"  Lỗi đọc {fp}: {e}")
        print(f"  {fp}: {count} tk mới")
    return users, files


def write_batch(users, prefix="batch_scanned", size=750):
    """Chia danh sách thành các file lô. Trả danh sách file đã tạo."""
    files = []
    for i, start in enumerate(range(0, len(users), size)):
        chunk = users[start:start + size]
        path = f"{prefix}_{i:02d}"
        with open(path, "w", encoding="utf-8") as f:
            for u in chunk:
                f.write(f"{u}\n")
        files.append(path)
        print(f"  {path}: {len(chunk)} tk")
    return files


def main():
    ap = argparse.ArgumentParser(
        description="Dò tk từ acc*.txt + chia lô + quay + chuyển xu")
    ap.add_argument("--pattern", default="acc*.txt",
                    help="Pattern file chứa tk (default: acc*.txt)")
    ap.add_argument("--password", "--pwd", required=True,
                    help="MK chung các tk")
    ap.add_argument("--dest", type=int, default=65692738,
                    help="PlayerId nhận xu")
    ap.add_argument("--batch-size", type=int, default=750,
                    help="Tk mỗi lô (default: 750)")
    ap.add_argument("--workers", type=int, default=12,
                    help="Số luồng quay/chuyển (default: 12)")
    ap.add_argument("--scan-workers", type=int, default=80,
                    help="Số luồng dò tk (default: 80)")
    ap.add_argument("--batch-pause", type=int, default=20,
                    help="Nghỉ giữa các lô (giây)")
    ap.add_argument("--phase-gap", type=int, default=60,
                    help="Nghỉ giữa quay và chuyển (giây)")
    ap.add_argument("--skip-scan", action="store_true",
                    help="Bỏ qua bước dò, dùng thẳng acc*.txt")
    ap.add_argument("--skip-done", action="store_true",
                    help="Bỏ qua tk đã xử lý trong phase2_transfer.csv")
    args = ap.parse_args()

    t_start = time.time()

    # ===== BƯỚC 1: GOM TÀI KHOẢN =====
    print("=" * 70)
    print("BƯỚC 1: Gom tài khoản từ acc*.txt")
    print("=" * 70)
    users, files = load_all_accounts(args.pattern)
    print(f"\nTổng: {len(users)} tk duy nhất từ {len(files)} file")
    if not users:
        print("Không có tk nào để xử lý.")
        return

    # ===== BƯỚC 2: DÒ HỢP LỆ =====
    valid_file = "acc_scanned_valid.txt"
    if not args.skip_scan:
        print(f"\n{'=' * 70}")
        print(f"BƯỚC 2: Dò {len(users)} tk ({args.scan_workers} luồng HTTP)")
        print("=" * 70)

        # Ghi tạm danh sách gộp để dò
        merged_file = "_merged_all.txt"
        with open(merged_file, "w", encoding="utf-8") as f:
            for u in users:
                f.write(f"{u}\n")

        # Dùng discover_accounts ở chế độ --fast --no-ws (chỉ HTTP, không đọc balance)
        # nhưng cần sửa: discover_accounts.py dùng --prefix/start/end, không dùng --list
        # Nên dùng spin_and_transfer --prune (tự dò + lọc)
        # HOẶC chạy HTTP check trực tiếp
        print("Đang dò nhanh (HTTP login check)...")
        result = subprocess.run(
            [sys.executable, "discover_accounts.py",
             "--prefix", "_dummy_scan",
             "--start", "1", "--end", "1",
             "--password", args.password,
             "--workers", str(args.scan_workers),
             "--no-ws", "--fast",
             "--out-valid", valid_file],
            capture_output=True, text=True, timeout=600
        )
        # discover_accounts không hỗ trợ --list, nên dò bằng cách khác
        # → dùng spin_and_transfer.py --prune (nó có prune_invalid dùng HTTP check)
        # Ghi danh sách gộp vào 1 file rồi dùng --list

        # Thực ra cách nhanh nhất: dùng --prune của spin_and_transfer
        # Nhưng trước tiên cần dò bằng script đơn giản
        os.remove(merged_file)

        # Dò trực tiếp bằng ThreadPoolExecutor
        import requests as _req
        from concurrent.futures import ThreadPoolExecutor, as_completed
        LOGIN_URL = "https://gamevh.net/login.jsp"
        UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/139.0 Safari/537.36")

        def http_check(user):
            try:
                s = _req.Session()
                s.headers.update({"User-Agent": UA})
                s.get(LOGIN_URL, timeout=10)
                r = s.post(LOGIN_URL, timeout=10,
                           data={"redirect": "/", "USER_NAME": user,
                                 "PASSWORD": args.password,
                                 "AUTO_LOGIN": "true", "LOGIN": "Đăng nhập"},
                           headers={"Origin": "https://gamevh.net",
                                    "Referer": LOGIN_URL},
                           allow_redirects=True)
                return user, "login.jsp" not in r.url
            except Exception:
                return user, False

        valid = []
        invalid = 0
        with ThreadPoolExecutor(max_workers=args.scan_workers) as ex:
            futs = {ex.submit(http_check, u): u for u in users}
            for i, f in enumerate(as_completed(futs), 1):
                user, ok = f.result()
                if ok:
                    valid.append(user)
                else:
                    invalid += 1
                if i % 200 == 0 or i == len(users):
                    print(f"  [{i}/{len(users)}] hợp lệ: {len(valid)}, loại: {invalid}")

        with open(valid_file, "w", encoding="utf-8") as f:
            for u in sorted(valid):
                f.write(f"{u}\n")
        print(f"\nDò xong: {len(valid)} hợp lệ / {invalid} loại / {len(users)} tổng")
        print(f"Ghi {valid_file}")
        users = valid

    if not users:
        print("Không có tk hợp lệ nào.")
        return

    # Loại trừ đã xử lý
    if args.skip_done:
        done = set()
        try:
            import csv as _csv
            with open("phase2_transfer.csv", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("status") in ("OK", "BALANCE_TOO_LOW"):
                        done.add(row["account"])
        except FileNotFoundError:
            pass
        if done:
            before = len(users)
            users = [u for u in users if u not in done]
            print(f"Bỏ qua {before - len(users)} tk đã xử lý, còn {len(users)}")

    # ===== BƯỚC 3: CHIA LÔ =====
    print(f"\n{'=' * 70}")
    print(f"BƯỚC 3: Chia {len(users)} tk thành lô ({args.batch_size} tk/lô)")
    print("=" * 70)
    batch_files = write_batch(users, prefix="batch_scanned", size=args.batch_size)
    print(f"\nTổng {len(batch_files)} lô")

    # ===== BƯỚC 4: QUAY + CHUYỂN XU TỪNG LÔ =====
    print(f"\n{'=' * 70}")
    print(f"BƯỚC 4: Quay + Chuyển xu ({len(batch_files)} lô, {args.workers} luồng)")
    print("=" * 70)

    for i, bf in enumerate(batch_files, 1):
        print(f"\n{'🔶' * 20}")
        print(f"LÔ {i}/{len(batch_files)} — {bf}")
        print(f"{'🔶' * 20}")

        result = subprocess.run(
            [sys.executable, "spin_and_transfer.py",
             "--list", bf,
             "--password", args.password,
             "--dest", str(args.dest),
             "--execute", "--phase", "all",
             "--batch-size", str(args.batch_size),
             "--batch-pause", str(args.batch_pause),
             "--phase-gap", str(args.phase_gap),
             "--workers", str(args.workers),
             "--append", "--skip-done"],
            timeout=1800  # 30 phút max mỗi lô
        )
        if result.returncode != 0:
            print(f"  Lô {i} lỗi (code={result.returncode}), tiếp lô tiếp")

        # Nghỉ giữa các lô (trừ lô cuối)
        if i < len(batch_files):
            print(f"Nghỉ {args.batch_pause}s giữa lô...")
            time.sleep(args.batch_pause)

    wall = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"HOÀN TẤT — {len(users)} tk, {len(batch_files)} lô, {wall/60:.1f} phút")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
