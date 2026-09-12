#!/usr/bin/env python3
"""
nguyen_multi — chạy 9 bot Caro nguyen* trong 1 job duy nhất (tiết kiệm slot Actions).
Kiến trúc: 1 process cha spawn 9 process con (python nguyenN.py), mỗi con 1 thread
đọc log + 1 thread giám sát. Bot con crash -> tự restart (có giới hạn). Hết phiên
(ManyToOne: hết MULTI_RUNTIME_SECONDS / nhận SIGTERM từ handoff) -> dừng tất cả.

Env:
  CARO_RUNTIME_HOURS   = 5.7   (thời gian chạy MỖI BOT CON / phiên — truyền xuống)
  MULTI_RUNTIME_SECONDS= (rỗng) (giới hạn tổng phiên của process cha; test ngắn dùng cái này)
  MULTI_STAGGER        = 20    (giãn cách khởi động giữa các bot, giây)
  MULTI_MAX_RESTARTS   = 5     (số lần restart tối đa mỗi bot con trong 1 phiên)
  MULTI_RESTART_DELAY  = 30    (chờ bao lâu trước khi restart)
  BOT_PASSWD           = nhat123456 (đẩy xuống bot con qua CARO_PWWD — cho bot thiếu pw cứng)
"""
import os, sys, time, signal, subprocess, threading

BOTS = ["nguyen1", "nguyen4", "nguyen5", "nguyen6", "nguyen7",
        "nguyen13", "nguyen14", "nguyen15", "nguyen16"]

RUNTIME_HOURS   = os.environ.get("CARO_RUNTIME_HOURS") or "5.7"
SESSION_CAP_S   = int(os.environ.get("MULTI_RUNTIME_SECONDS") or 0)  # 0 = không giới hạn (chạy tới khi bị kill)
STAGGER_S       = int(os.environ.get("MULTI_STAGGER") or "20")
MAX_RESTARTS    = int(os.environ.get("MULTI_MAX_RESTARTS") or "5")
RESTART_DELAY_S = int(os.environ.get("MULTI_RESTART_DELAY") or "30")
MIN_START_WINDOW = int(os.environ.get("MULTI_MIN_START_WINDOW") or "60")  # còn < Ns phiên: không khởi động bot mới

START_TS   = time.time()
STOP       = threading.Event()
procs      = {}                     # name -> subprocess.Popen
procs_lock = threading.Lock()


def log(msg):
    print(f"[multi {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def child_env():
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["CARO_RUNTIME_HOURS"] = RUNTIME_HOURS   # bot con ưu tiên CARO_RUNTIME_SECONDS nếu có sẵn
    if not env.get("CARO_PWWD") and not env.get("CARO_PWWD1"):
        env["CARO_PWWD"] = os.environ.get("BOT_PASSWD") or "nhat123456"
    return env


def pump(name, proc):
    """Thread đọc stdout bot con, in ra với tiền tố [name]."""
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()
    except Exception:
        pass


def bot_worker(name, delay_s):
    """Thread quản lý 1 bot: chờ delay -> chạy -> restart khi thoát sớm -> dừng khi hết phiên."""
    if delay_s:
        if STOP.wait(delay_s):
            return
    attempts = 0
    while not STOP.is_set():
        if SESSION_CAP_S and (SESSION_CAP_S - (time.time() - START_TS)) < MIN_START_WINDOW:
            return  # còn quá ít thời gian phiên: không khởi động thêm
        attempts += 1
        env = child_env()
        log(f"start {name}.py (lan {attempts}, runtime {RUNTIME_HOURS}h)")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", f"{name}.py"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env)
        except Exception as e:
            log(f"LOI spawn {name}: {e}")
            if attempts > MAX_RESTARTS or STOP.wait(RESTART_DELAY_S):
                return
            continue
        with procs_lock:
            procs[name] = proc
        t = threading.Thread(target=pump, args=(name, proc), daemon=True)
        t.start()
        # Chờ process thoát / hết phiên / nhận lệnh dừng
        while proc.poll() is None:
            if STOP.is_set():
                break
            if SESSION_CAP_S and time.time() - START_TS >= SESSION_CAP_S:
                break
            time.sleep(5)
        # Dọn process còn treo
        if proc.poll() is None:
            log(f"dung {name} (het phien)")
            try:
                proc.terminate(); proc.wait(timeout=15)
            except Exception:
                try: proc.kill()
                except Exception: pass
            return
        rc = proc.returncode
        if STOP.is_set():
            return
        if SESSION_CAP_S and time.time() - START_TS >= SESSION_CAP_S:
            return
        log(f"{name} thoat som code={rc} — restart sau {RESTART_DELAY_S}s (lan {attempts}/{MAX_RESTARTS})")
        if attempts > MAX_RESTARTS:
            log(f"{name} vuot {MAX_RESTARTS} lan restart — bo theo doi")
            return
        if STOP.wait(RESTART_DELAY_S):
            return


def shutdown(signum, frame):
    if STOP.is_set():
        return
    log(f"nhan tin hieu {signum} — dung tat ca bot con...")
    STOP.set()
    with procs_lock:
        plist = list(procs.items())
    for name, proc in plist:
        if proc.poll() is None:
            try: proc.terminate()
            except Exception: pass
    deadline = time.time() + 20
    for name, proc in plist:
        try:
            proc.wait(timeout=max(1, deadline - time.time()))
        except Exception:
            try: proc.kill()
            except Exception: pass
    log("tat ca bot con da dung — thoat")


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log(f"nguyen-multi: {len(BOTS)} bot = {', '.join(BOTS)}")
    log(f"runtime/bot={RUNTIME_HOURS}h | stagger={STAGGER_S}s | cap_phien={SESSION_CAP_S or 'khong'}s | restarts<={MAX_RESTARTS}")
    threads = []
    for i, name in enumerate(BOTS):
        t = threading.Thread(target=bot_worker, args=(name, i * STAGGER_S), daemon=True)
        t.start()
        threads.append(t)
    # Thread chính: chờ hết phiên (nếu có cap) hoặc bị kill bởi handoff
    while not STOP.is_set():
        if SESSION_CAP_S and time.time() - START_TS >= SESSION_CAP_S:
            log("het gio phien (MULTI_RUNTIME_SECONDS)")
            shutdown("SESSION_END", None)
            break
        time.sleep(5)
    for t in threads:
        t.join(timeout=30)
    log("nguyen-multi ket thuc")


if __name__ == "__main__":
    main()
