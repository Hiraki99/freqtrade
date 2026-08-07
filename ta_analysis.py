#!/usr/bin/env python3
"""
Bot phân tích SMC trên Telegram — RULE-BASED, KHÔNG cần API (miễn phí).

Gõ /analysis BTC/USDT  -> bot tính cấu trúc SMC (BOS/CHoCH), order block, FVG,
premium/discount, RSI từ dữ liệu ccxt và trả về báo cáo. Dùng đúng logic của
SmcElliottStrategy.

Biến môi trường (nạp bằng `set -a; source .env; set +a` hoặc run_ta_analysis.sh):
  FREQTRADE__TELEGRAM__CHAT_ID
  ANALYSIS_TELEGRAM_TOKEN     (khuyến nghị: bot RIÊNG, tránh xung đột 409 với bot trade)
  FREQTRADE__TELEGRAM__TOKEN  (fallback — ĐỪNG chạy cùng lúc bot trade nếu dùng chung)
"""

from __future__ import annotations

import logging
import os
import time

import ccxt
import numpy as np
import pandas as pd
import requests

EXCHANGE = "binance"
PAIRS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["1h", "4h"]          # phân tích đa khung
INTERNAL_LEN = 5
SWING_LEN = 50
OB_ATR_MULT = 2.0
FVG_THRESH_MULT = 2.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("ta_analysis")
TG = "https://api.telegram.org"
BULLISH, BEARISH = 1, -1


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Thiếu biến môi trường {name}.")
    return v


def tg(token, method, **payload):
    try:
        return requests.post(f"{TG}/bot{token}/{method}", json=payload, timeout=40).json()
    except requests.RequestException as exc:
        log.warning("Telegram %s lỗi: %s", method, exc)
        return {}


def send(token, chat_id, text):
    for i in range(0, len(text), 4000):
        tg(token, "sendMessage", chat_id=chat_id, text=text[i:i + 4000],
           disable_web_page_preview=True)


# ----------------------------- SMC core ----------------------------------- #
def atr(df, period=200):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def rsi(s, period=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    ll = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + g / ll.replace(0, 1e-12))


def _leg(df, size):
    highest = df["high"].rolling(size).max().values
    lowest = df["low"].rolling(size).min().values
    hs = df["high"].shift(size).values
    ls = df["low"].shift(size).values
    n = len(df); leg = np.zeros(n, dtype=np.int8); cur = 0
    for i in range(n):
        if i >= size and hs[i] > highest[i]:
            cur = 0
        elif i >= size and ls[i] < lowest[i]:
            cur = 1
        leg[i] = cur
    return leg


def structure(df, size):
    n = len(df)
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    ph, pl = df["parsed_high"].values, df["parsed_low"].values
    leg = _leg(df, size)
    sh = np.nan; shi = -1; shx = True
    sl = np.nan; sli = -1; slx = True
    bias = 0
    bull, bear = [], []
    for i in range(n):
        if i > 0 and leg[i] != leg[i - 1] and i >= size:
            if leg[i] == 0:
                sh = high[i - size]; shi = i - size; shx = False
            else:
                sl = low[i - size]; sli = i - size; slx = False
        if not np.isnan(sh) and not shx and close[i] > sh and close[i - 1] <= sh:
            bias = BULLISH; shx = True
            if shi >= 0:
                seg = pl[shi:i + 1]
                if seg.size:
                    j = shi + int(np.argmin(seg)); bull.append((ph[j], pl[j]))
        if not np.isnan(sl) and not slx and close[i] < sl and close[i - 1] >= sl:
            bias = BEARISH; slx = True
            if sli >= 0:
                seg = ph[sli:i + 1]
                if seg.size:
                    j = sli + int(np.argmax(seg)); bear.append((ph[j], pl[j]))
        bull = [o for o in bull if low[i] >= o[1]]
        bear = [o for o in bear if high[i] <= o[0]]
    return {"bias": bias, "last_sh": sh, "last_sl": sl,
            "bull_ob": bull[-1] if bull else None,
            "bear_ob": bear[-1] if bear else None}


def fvg(df, mult):
    n = len(df)
    high, low = df["high"].values, df["low"].values
    op, cl = df["open"].values, df["close"].values
    delta = np.zeros(n)
    for i in range(1, n):
        if op[i - 1]:
            delta[i] = (cl[i - 1] - op[i - 1]) / op[i - 1]
    thr = np.cumsum(np.abs(delta)) / np.maximum(np.arange(1, n + 1), 1) * mult
    top = bot = np.nan
    for i in range(2, n):
        if low[i] > high[i - 2] and cl[i - 1] > high[i - 2] and delta[i] > thr[i]:
            top, bot = low[i], high[i - 2]
        if not np.isnan(bot) and low[i] < bot:
            top = bot = np.nan
    return (bot, top) if not np.isnan(bot) else None


def analyze_tf(exchange, pair, tf):
    raw = exchange.fetch_ohlcv(pair, timeframe=tf, limit=400)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    a = atr(df, 200)
    hv = (df["high"] - df["low"]) >= OB_ATR_MULT * a
    df["parsed_high"] = np.where(hv, df["low"], df["high"])
    df["parsed_low"] = np.where(hv, df["high"], df["low"])
    intr = structure(df, INTERNAL_LEN)
    swg = structure(df, SWING_LEN)
    fv = fvg(df, FVG_THRESH_MULT)
    price = df["close"].iloc[-1]
    r = rsi(df["close"]).iloc[-1]

    def lab(b):
        return "🟢 TĂNG" if b > 0 else "🔴 GIẢM" if b < 0 else "⚪ chưa rõ"

    lines = [f"⏱ {tf}: giá {price:.2f} | RSI {r:.0f}",
             f"   Swing (HTF): {lab(swg['bias'])} | Internal: {lab(intr['bias'])}"]
    if intr["bull_ob"]:
        t, b = intr["bull_ob"]
        lines.append(f"   🟦 Bull OB (demand): {b:.2f}–{t:.2f}"
                     + ("  ← giá đang trong vùng" if b <= price <= t else ""))
    if intr["bear_ob"]:
        t, b = intr["bear_ob"]
        lines.append(f"   🟥 Bear OB (supply): {b:.2f}–{t:.2f}")
    if fv:
        b, t = fv
        lines.append(f"   ▫️ Bull FVG: {b:.2f}–{t:.2f}")
    if not np.isnan(swg["last_sh"]) and not np.isnan(swg["last_sl"]):
        eq = (swg["last_sh"] + swg["last_sl"]) / 2
        zone = "PREMIUM (đỉnh range)" if price > eq else "DISCOUNT (đáy range)"
        lines.append(f"   📐 Range {swg['last_sl']:.2f}–{swg['last_sh']:.2f} | "
                     f"eq {eq:.2f} → giá ở {zone}")
    return "\n".join(lines), intr, swg


def analyze(exchange, pair):
    blocks = []
    biases = []
    for tf in TIMEFRAMES:
        try:
            txt, intr, swg = analyze_tf(exchange, pair, tf)
            blocks.append(txt)
            biases.append(swg["bias"])
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"⏱ {tf}: lỗi {exc}")
    # Kết luận
    if biases and all(b > 0 for b in biases):
        verdict = "✅ Đa khung ĐỒNG THUẬN TĂNG — ưu tiên tìm LONG ở vùng discount/Bull OB."
    elif biases and all(b < 0 for b in biases):
        verdict = "⛔ Đa khung ĐỒNG THUẬN GIẢM — tránh long, chờ cấu trúc đảo."
    else:
        verdict = "⚠️ Khung xung đột — đứng ngoài hoặc chờ xác nhận thêm."
    head = f"📊 SMC ANALYSIS {pair} — {EXCHANGE}\n" + "─" * 26
    return head + "\n" + "\n\n".join(blocks) + "\n\n" + verdict + \
        "\n\n⚠️ Phân tích kỹ thuật, không phải lời khuyên đầu tư."


HELP = ("📈 SMC Analysis bot (rule-based, miễn phí)\n"
        "/analysis [cặp] — phân tích SMC đa khung (vd /analysis BTC/USDT)\n"
        "/pairs — danh sách cặp\n/help — trợ giúp\n"
        "Bỏ trống [cặp] = phân tích tất cả.")


def handle(token, chat_id, exchange, text):
    p = text.strip().split()
    cmd = p[0].lstrip("/").split("@")[0].lower()
    arg = p[1].upper() if len(p) > 1 else None
    if cmd in ("analysis", "analyse", "smc"):
        targets = [arg] if arg else PAIRS
        send(token, chat_id, f"⏳ Đang phân tích {', '.join(targets)}…")
        for pair in targets:
            try:
                send(token, chat_id, analyze(exchange, pair))
            except Exception as exc:  # noqa: BLE001
                send(token, chat_id, f"⚠️ Lỗi {pair}: {exc}")
    elif cmd == "pairs":
        send(token, chat_id, "Cặp: " + ", ".join(PAIRS))
    else:
        send(token, chat_id, HELP)


def main():
    chat_id = _req("FREQTRADE__TELEGRAM__CHAT_ID")
    token = os.environ.get("ANALYSIS_TELEGRAM_TOKEN") or _req("FREQTRADE__TELEGRAM__TOKEN")
    if not os.environ.get("ANALYSIS_TELEGRAM_TOKEN"):
        log.warning("Dùng CHUNG token bot trade — ĐỪNG chạy bot freqtrade cùng lúc (409). "
                    "Khuyến nghị tạo bot riêng -> ANALYSIS_TELEGRAM_TOKEN.")

    exchange = getattr(ccxt, EXCHANGE)({"enableRateLimit": True})
    tg(token, "setMyCommands", commands=[
        {"command": "analysis", "description": "Phân tích SMC — /analysis [cặp]"},
        {"command": "pairs", "description": "Danh sách cặp"},
        {"command": "help", "description": "Hướng dẫn"},
    ])
    send(token, chat_id, "📈 SMC Analysis bot online (miễn phí, rule-based).\n" + HELP)
    log.info("Analysis bot online — pairs=%s tf=%s", PAIRS, TIMEFRAMES)

    offset = None
    while True:
        res = tg(token, "getUpdates", offset=offset, timeout=25)
        if res.get("ok"):
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != str(chat_id):
                    continue
                t = msg.get("text", "")
                if t.startswith("/"):
                    log.info("Lệnh: %s", t)
                    handle(token, chat_id, exchange, t)
        elif res.get("error_code") == 409:
            log.error("Xung đột 409 — token đang bị bot khác poll. Dừng bot trade "
                      "hoặc đặt ANALYSIS_TELEGRAM_TOKEN riêng.")
            time.sleep(15)


if __name__ == "__main__":
    main()
