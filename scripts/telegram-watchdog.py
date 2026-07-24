#!/usr/bin/env python3
import hashlib
import os
import queue
import re
import signal
import platform
import sys
import threading
import time
import urllib.parse
import urllib.request


DEFAULT_LOGS = [
    ("node", os.path.expanduser("~/midstate-node.log")),
    ("pool", os.path.expanduser("~/midstate-pool.log")),
    ("solo", os.path.expanduser("~/midstate-solo.log")),
]

MATCH_RE = re.compile(
    os.environ.get(
        "WATCHDOG_MATCH",
        r"(?i)\b(error|fatal|critical|panic|rejected|validation timed out|failed)\b",
    )
)
COOLDOWN_SECS = int(os.environ.get("WATCHDOG_COOLDOWN_SECS", "300"))
POLL_SECS = float(os.environ.get("WATCHDOG_POLL_SECS", "1"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
START_FROM_END = os.environ.get("WATCHDOG_START_FROM_END", "1").strip() not in {"0", "false", "False"}

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.", file=sys.stderr)
    sys.exit(2)


def parse_logs():
    raw = os.environ.get("WATCHDOG_LOGS", "").strip()
    if not raw:
        return DEFAULT_LOGS

    logs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            label, path = item.split(":", 1)
            logs.append((label.strip() or os.path.basename(path.strip()), os.path.expanduser(path.strip())))
        else:
            path = os.path.expanduser(item)
            logs.append((os.path.basename(path), path))
    return logs or DEFAULT_LOGS


def telegram_send(message):
    payload = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message[:3900],
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def fingerprint(source, line):
    digest = hashlib.sha1(f"{source}\n{line}".encode("utf-8")).hexdigest()
    return digest


def format_alert(source, line):
    hostname = os.environ.get("HOSTNAME") or platform.node()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    return f"[{hostname}] {source} {timestamp}\n{line.strip()}"


def open_log(path):
    while not stop_event.is_set():
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
            if START_FROM_END:
                handle.seek(0, os.SEEK_END)
            return handle
        except FileNotFoundError:
            time.sleep(2)
    return None


def watch_log(source, path, outbox):
    handle = None
    last_size = 0

    while not stop_event.is_set():
        if handle is None:
            handle = open_log(path)
            if handle is None:
                return
            try:
                last_size = os.path.getsize(path)
            except OSError:
                last_size = 0

        line = handle.readline()
        if line:
            outbox.put((source, line))
            continue

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

        if size < last_size:
            handle.close()
            handle = None
            last_size = 0
            continue

        last_size = size
        time.sleep(POLL_SECS)


def main():
    logs = parse_logs()
    outbox = queue.Queue()
    sent = {}

    for source, path in logs:
        thread = threading.Thread(target=watch_log, args=(source, path, outbox), daemon=True)
        thread.start()

    try:
        telegram_send(f"[{os.environ.get('HOSTNAME') or platform.node()}] watchdog armed for {', '.join(src for src, _ in logs)}")
    except Exception as exc:
        print(f"startup telegram failed: {exc}", file=sys.stderr)

    while not stop_event.is_set():
        try:
            source, line = outbox.get(timeout=1)
        except queue.Empty:
            continue

        if not MATCH_RE.search(line):
            continue

        key = fingerprint(source, line)
        now = time.time()
        last = sent.get(key, 0)
        if now - last < COOLDOWN_SECS:
            continue

        try:
            telegram_send(format_alert(source, line))
            sent[key] = now
        except Exception as exc:
            print(f"telegram send failed: {exc}", file=sys.stderr)


def shutdown(*_args):
    stop_event.set()


stop_event = threading.Event()
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

if __name__ == "__main__":
    main()
