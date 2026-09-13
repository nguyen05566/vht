#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recover_acc.py — Khôi phục tk đã đăng ký THÀNH CÔNG nhưng chưa
được push lên repo (do workflow register* bị fail / cancel / lỗi thời gian).

Cách hoạt động:
  1. Liệt kê tất cả workflow runs "Register Accounts*" qua GitHub API.
  2. Tải log từng run, trích các dòng "✅ THÀNH CÔNG: <user>" / "[REGISTER] OK <user>".
  3. Lấy nội dung các ledger hiện tại trên main (acc*.txt).
  4. Tên nào CÓ trong log nhưng KHÔNG có trong ledger -> là tk bị "mất".
  5. (tùy chọn) Loại các tk đã xử lý trước đó (acc_valid.txt).
  6. Ghi ra acc_recovered.txt (format ledger: user\tcreated_at\tsource_run_id).

Cách chạy:
  export GH_PAT=ghp_xxx          # hoặc để trống nếu git remote đã có token
  python3 recover_acc.py [--keep-processed] [--out acc_recovered.txt]
"""
import argparse
import io
import os
import re
import sys
import zipfile

import requests

REPO = "javjp607-blip/nguyen055"
API = "https://api.github.com/repos/%s" % REPO
LEDGER_FILES = ["acc.txt"] + [f"acc{i}.txt" for i in range(1, 16)] + ["acc_legacy.txt"]


def get_token():
    t = os.environ.get("GH_PAT", "").strip()
    if t:
        return t
    # thử đọc từ git remote (https://oauth2:TOKEN@github.com/...)
    try:
        for l in open(".git/config", encoding="utf-8"):
            if "url =" in l:
                m = re.search(r"(?:oauth2:|ghp_)[^@\s/]+", l)
                if m:
                    return m.group(0).split(":", 1)[-1]
    except Exception:
        pass
    return None


def api(path, token, params=None):
    r = requests.get(API + path, headers={"Authorization": "token %s" % token,
                                          "Accept": "application/vnd.github+json"},
                     params=params, timeout=60)
    if r.status_code != 200:
        print("! API", path, r.status_code, r.text[:200])
        return None
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-processed", action="store_true",
                    help="KHÔNG loại các tk đã xử lý (acc_valid.txt)")
    ap.add_argument("--processed-file", default="acc_valid.txt",
                    help="file tk đã quay+chuyển trước đó (đặt cạnh --out để loại trừ)")
    ap.add_argument("--out", default="acc_recovered.txt")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    token = get_token()
    if not token:
        print("! Không tìm thấy GH_PAT (env) hoặc token trong git remote"); sys.exit(1)

    # 1) lấy tất cả runs (3 trang)
    runs_, page = [], 1
    while page <= 3:
        d = api("/actions/runs", token, {"per_page": 100, "page": page})
        if not d:
            break
        runs_ += d.get("workflow_runs", [])
        if len(runs_) >= d.get("total_count", 0):
            break
        page += 1
    reg = [r for r in runs_ if (r["name"] + r.get("path", "")).lower().find("register") >= 0]
    print("Run register* tìm thấy:", len(reg))

    # 2) tải log + trích tên
    name_runs = {}
    for r in reg:
        rid = r["id"]
        if r["status"] != "completed":
            print(f"  run {rid}: {r['status']} — bỏ qua (chưa có log)")
            continue
        dl = requests.get("%s/actions/runs/%s/logs" % (API, rid),
                          headers={"Authorization": "token %s" % token},
                          timeout=180, allow_redirects=True)
        if dl.status_code != 200:
            print(f"  run {rid}: {dl.status_code} — không tải được log")
            continue
        txt = ""
        try:
            z = zipfile.ZipFile(io.BytesIO(dl.content))
            for n in z.namelist():
                txt += z.read(n).decode("utf-8", "replace")
        except zipfile.BadZipFile:
            txt = dl.text
        ns = set(re.findall(r"✅ THÀNH CÔNG: (\S+)", txt)) | \
             set(re.findall(r"\[REGISTER\] OK (\S+)", txt))
        base = re.search(r"Tên cơ sở\s*: (\S+)", txt)
        created_at = r["created_at"][:19].replace("T", " ") + "Z"
        print(f"  run {rid} ({base.group(1) if base else '?'}): {len(ns)} tk")
        for n in ns:
            name_runs.setdefault(n, []).append((rid, created_at))

    # 3) ledger hiện tại trên main
    led = set()
    for f in LEDGER_FILES:
        d = api("/contents/" + f, token, {"ref": "main"})
        if not d or "content" not in d:
            continue
        import base64
        for l in base64.b64decode(d["content"]).decode("utf-8", "replace").splitlines():
            l = l.strip()
            if l and not l.startswith("#"):
                led.add(l.split("\t")[0].strip())

    # 4) tên bị mất
    rec = {n for n in name_runs if n not in led}
    if not args.keep_processed:
        pf = args.processed_file
        if os.path.exists(pf):
            val = {l.strip().split("\t")[0] for l in open(pf, encoding="utf-8")
                   if l.strip() and not l.startswith("#")}
            dup = rec & val
            rec -= val
            print(f"Loại {len(dup)} tk đã xử lý trước ({pf})")
        else:
            print(f"! Không tìm thấy {pf} — KHÔNG loại các tk đã xử lý")
            print("  (copy acc_valid.txt về đây, hoặc dùng --processed-file <đường dẫn>)")

    rec = sorted(rec)
    print(f">>> Tk tạo được nhưng chưa có trong repo: {len(rec)}")

    # 5) ghi file
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# Username ledger (recovered từ workflow logs — tạo thành công nhưng chưa đẩy lên repo)\n")
        f.write("# username\tcreated_at_utc\tsource_run_id\n")
        for n in rec:
            rid, at = name_runs[n][0]
            f.write(f"{n}\t{at}\t{rid}\n")
    print("Đã ghi", args.out, "→", len(rec), "tk")


if __name__ == "__main__":
    main()
