import os, time, json, datetime, subprocess, requests
from zoneinfo import ZoneInfo

API_KEY = os.environ["FINNHUB_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "210"))
MODE = os.environ.get("MODE", "") or "watch"
INTERVAL_SEC = 15
NY = ZoneInfo("America/New_York")
OPEN, CLOSE = datetime.time(9, 30), datetime.time(16, 0)
STATE_FILE = "state.json"

# To exit a position yourself, add "sold": <price> to its line
POSITIONS = [
    {"symbol": "CPRT", "buy": 32.01, "target": 36.81, "exit_to": "2026-11-18"},
    {"symbol": "PSIG", "buy": 5.02, "target": 6.53, "exit_to": "2026-11-12"},
    {"symbol": "SOFI", "buy": 16.27, "target": 17.90, "exit_to": "2026-11-19"},
]


def quote(symbol):
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()["c"]


def notify(msg):
    print(msg)
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"), timeout=10)
    except Exception as e:
        print(f"notify error {e}")


def ny_now():
    return datetime.datetime.now(NY)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"closed": {}, "last_summary": ""}


def save_state(state, msg):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    try:
        subprocess.run(["git", "add", STATE_FILE], check=True)
        subprocess.run(["git", "commit", "-qm", msg], check=True)
        subprocess.run(["git", "pull", "-q", "--rebase"], check=True)
        subprocess.run(["git", "push", "-q"], check=True)
    except Exception as e:
        print(f"state save error {e}")


def closed_info(p, state):
    if "sold" in p:
        return {"price": p["sold"], "reason": "manual"}
    return state["closed"].get(p["symbol"])


def pct(p, price):
    return (price - p["buy"]) / p["buy"] * 100


def trading_days_left(today, exit_to):
    d, n = today, 0
    while d < exit_to:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def monitor(state):
    end = time.time() + RUN_MINUTES * 60
    while time.time() < end:
        now = ny_now()
        if now.weekday() >= 5 or now.time() >= CLOSE:
            break
        if now.time() < OPEN:
            time.sleep(30)
            continue
        for p in POSITIONS:
            if closed_info(p, state):
                continue
            try:
                price = quote(p["symbol"])
                if not price:
                    print(f"{p['symbol']}: no data")
                    continue
                print(f"{now:%H:%M:%S} NY {p['symbol']}: ${price:.2f} ({pct(p, price):+.2f}%)")
                if price >= p["target"]:
                    state["closed"][p["symbol"]] = {
                        "price": round(price, 2), "date": now.date().isoformat(), "reason": "target"}
                    save_state(state, f"{p['symbol']} hit target")
                    notify(f"🎯 {p['symbol']} הגיעה ליעד! נמכרה ב-${price:.2f} ({pct(p, price):+.1f}%)")
            except Exception as e:
                print(f"{p['symbol']}: error {e}")
        time.sleep(INTERVAL_SEC)


def summary(state):
    today = ny_now().date()
    lines = [f"📊 סיכום יום {today:%d/%m}"]
    for p in POSITIONS:
        s = p["symbol"]
        c = closed_info(p, state)
        if c:
            lines.append(f"✅ {s}: סגורה, נמכרה ב-${c['price']:.2f} ({pct(p, c['price']):+.1f}%)")
            continue
        try:
            price = quote(s)
        except Exception as e:
            lines.append(f"{s}: שגיאה בשליפת מחיר")
            continue
        diff, pc = price - p["buy"], pct(p, price)
        exit_to = datetime.date.fromisoformat(p["exit_to"])
        if today < exit_to:
            left = trading_days_left(today, exit_to)
            lines.append(f"{s}: ${price:.2f} ({pc:+.1f}%) | יעד ${p['target']} | נותרו {left} ימי מסחר")
        elif diff >= 0:
            state["closed"][s] = {"price": round(price, 2), "date": today.isoformat(), "reason": "time"}
            lines.append(f"⏰ {s}: הזמן נגמר, נמכרה ב-${price:.2f} (רווח ${diff:.2f} למניה, {pc:+.1f}%)")
        else:
            lines.append(f"⚠️ {s}: הזמן נגמר ואתה בהפסד של ${-diff:.2f} למניה ({pc:.1f}%). לצאת? תגיד לClaude")
    state["last_summary"] = today.isoformat()
    save_state(state, f"summary {today}")
    notify("\n".join(lines))


if __name__ == "__main__":
    state = load_state()
    if MODE == "summary":
        summary(state)
    else:
        monitor(state)
        now = ny_now()
        if now.weekday() < 5 and now.time() >= CLOSE and state.get("last_summary") != now.date().isoformat():
            summary(state)
        else:
            print("Done (no summary needed now)")
