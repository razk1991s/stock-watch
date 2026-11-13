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

# NYSE full-day holidays (weekends are skipped automatically)
US_HOLIDAYS = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15",
               "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
               "2027-11-25", "2027-12-24"}

# To exit a position yourself, add "sold": <price> to its line
# exit_from / exit_to = the recommended exit window
POSITIONS = [
    {"symbol": "SOFI", "buy": 16.27, "target": 17.90, "exit_from": "2026-11-13", "exit_to": "2026-11-19"},
    {"symbol": "CPRT", "buy": 32.01, "target": 36.81, "exit_from": "2026-11-16", "exit_to": "2026-11-18"},
    {"symbol": "PSIG", "buy": 5.02, "target": 6.53, "exit_from": "2026-11-10", "exit_to": "2026-11-12"},
]


def quote_full(symbol):
    r = requests.get("https://finnhub.io/api/v1/quote",
                     params={"symbol": symbol, "token": API_KEY}, timeout=10)
    r.raise_for_status()
    return r.json()  # c=price, t=unix time of the quote


def quote(symbol):
    return quote_full(symbol)["c"]


def notify(msg, title=None):
    print((title + "\n" if title else "") + msg)
    try:
        # JSON publish so a Hebrew/emoji title works (HTTP headers can't carry it)
        body = {"topic": NTFY_TOPIC, "message": msg}
        if title:
            body["title"] = title
        requests.post("https://ntfy.sh/", json=body, timeout=10)
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


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in US_HOLIDAYS


def trading_days_left(today, exit_to):
    d, n = today, 0
    while d < exit_to:
        d += datetime.timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def fmt_pct(x):
    return f"{x:+.2f}%" if 0 < abs(x) < 0.1 else f"{x:+.1f}%"


def fmt_money(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def closed_lines(p, c):
    price = c["price"]
    diff, pc = price - p["buy"], pct(p, price)
    word = "רווח" if diff >= 0 else "הפסד"
    hit = price >= p["target"]
    icon = "🟩" if hit else "🟥"
    when = f" ב-{datetime.date.fromisoformat(c['date']):%d/%m}" if c.get("date") else ""
    result = "✅ הגיעה ליעד" if hit else f"❌ לא הגיעה ליעד ({fmt_pct(pct(p, p['target']))})"
    return [f"{icon} {p['symbol']}  נמכרה ב-${price:.2f}{when} · {word} {fmt_money(diff)} ({fmt_pct(pc)})",
            f"   {result}"]


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
    blocks = []
    for p in POSITIONS:
        s = p["symbol"]
        c = closed_info(p, state)
        if c:
            blocks.append(closed_lines(p, c))
            continue
        try:
            q = quote_full(s)
            price = q["c"]
            if not price:
                raise ValueError("no price")
        except Exception:
            blocks.append([f"⚪ {s}  ⚠️ לא התקבל מחיר היום"])
            continue
        quote_day = datetime.datetime.fromtimestamp(q.get("t", 0), NY).date()
        stale = quote_day != today
        pc = pct(p, price)
        exit_to = datetime.date.fromisoformat(p["exit_to"])
        exit_from = datetime.date.fromisoformat(p.get("exit_from", p["exit_to"]))

        if today < exit_to or stale:
            left = trading_days_left(today, exit_to)
            need = (p["target"] - price) / price * 100
            if today >= exit_from:
                icon, phase = "🟦", "בחלון היציאה"
            else:
                icon, phase = "🟨", f"חלון נפתח {exit_from:%d/%m}"
            lines = [f"{icon} {s}  ${price:.2f}  ({fmt_pct(pc)} מהקנייה)",
                     f"   יעד ${p['target']:.2f} · חסר {fmt_pct(need)} · {left} ימי מסחר · {phase}"]
            if stale:
                lines.append(f"   ⚠️ מחיר לא עדכני (מ-{quote_day:%d/%m}), לא נסגרה היום")
            blocks.append(lines)
        elif price >= p["buy"]:
            c = {"price": round(price, 2), "date": today.isoformat(), "reason": "time"}
            state["closed"][s] = c
            blocks.append(["⏰ הזמן נגמר"] + closed_lines(p, c))
        else:
            diff = price - p["buy"]
            blocks.append([f"🟧 {s}  ${price:.2f} · הזמן נגמר בהפסד {fmt_money(diff)} למניה ({fmt_pct(pc)})",
                           "   ❓ לצאת? תגיד ל-Claude"])

    closed = [p for p in POSITIONS if closed_info(p, state)]
    hits = sum(closed_info(p, state)["price"] >= p["target"] for p in closed)
    title = f"📊 סיכום {today:%d/%m} · {len(POSITIONS)} מניות"
    header = f"פתוחות: {len(POSITIONS) - len(closed)} · נסגרו: {len(closed)} · הגיעו ליעד: {hits}/{len(closed)}"
    msg = header + "\n\n" + "\n\n".join("\n".join(b) for b in blocks)

    state["last_summary"] = today.isoformat()
    save_state(state, f"summary {today}")
    notify(msg, title)


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
