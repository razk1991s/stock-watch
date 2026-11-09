import os, time, datetime, requests

API_KEY = os.environ["FINNHUB_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "210"))
INTERVAL_SEC = 15

POSITIONS = [
    {"symbol": "CPRT", "buy": 32.01, "target": 36.81, "exit_to": "2026-11-18"},
    {"symbol": "PSIG", "buy": 5.02, "target": 6.53, "exit_to": "2026-11-12"},
]

def quote(symbol):
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()["c"]

def notify(msg):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"), timeout=10)
    except Exception as e:
        print(f"notify error {e}")

alerted = set()
end = time.time() + RUN_MINUTES * 60
notify("Stock watcher started (cloud)")

while time.time() < end:
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%H:%M:%S")
    for p in POSITIONS:
        try:
            price = quote(p["symbol"])
            if not price:
                print(f"{now} {p['symbol']}: no data")
                continue
            pct = (price - p["buy"]) / p["buy"] * 100
            print(f"{now} {p['symbol']}: ${price:.2f} ({pct:+.2f}%)")
            if price >= p["target"] and (p["symbol"], "hit") not in alerted:
                notify(f"{p['symbol']} hit target: ${price:.2f} ({pct:+.1f}%)")
                alerted.add((p["symbol"], "hit"))
            late_day = datetime.date.fromisoformat(p["exit_to"]) + datetime.timedelta(days=1)
            if today == late_day and (p["symbol"], "late") not in alerted:
                notify(f"{p['symbol']} exit window ended at ${price:.2f} ({pct:+.1f}%)")
                alerted.add((p["symbol"], "late"))
        except Exception as e:
            print(f"{now} {p['symbol']}: error {e}")
    time.sleep(INTERVAL_SEC)
