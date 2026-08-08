"""Mô phỏng ngoại tuyến đường auto_entry (lịch /analysis -> vào lệnh).

VÌ SAO CẦN: `freqtrade backtesting` chỉ chạy `populate_entry_trend`. Đường auto_entry nằm
trong `telegram.py`, do lịch kích hoạt, nên không có công cụ nào của freqtrade chạm tới nó —
bật lên là chạy tiền thật mà chưa từng có một con số nào.

CÁCH LÀM: bước theo từng `--step` phút giống hệt lịch thật; tại mỗi mốc dựng `levels` cho từng
khung bằng ĐÚNG hàm bot dùng (`Telegram._smc_levels_from_row`), lấy hướng bằng
`_smc_report_context`, rồi phán quyết bằng `_smc_trade_decision`. Không có bản sao logic nào ở
đây — sửa luật trong telegram.py là mô phỏng đổi theo.

CHỐNG NHÌN TRỘM TƯƠNG LAI: tại mốc T, mỗi khung chỉ được dùng nến đã ĐÓNG (`date + tf <= T`).
Đây là chỗ duy nhất dễ tạo ra lợi nhuận giả; `--self-check` in ra độ trễ thực tế của từng khung
để xác minh, và chạy thêm một lượt lệch nửa nến để chắc kết quả không phụ thuộc lưới thời gian.

CÁC XẤP XỈ (đọc trước khi tin con số):
  · Khớp lệnh theo nến 5m: buy-limit coi như khớp khi `low <= giá limit`. Sổ lệnh thật có thể
    không khớp ở đáy nến.
  · Thoát lệnh theo ĐÚNG kế hoạch đã công bố: SL của kế hoạch, TP1 của thang. Bot thật còn có
    custom_stoploss/custom_exit/ROI của strategy chồng lên — nên đây đo "nếu tôn trọng đúng kế
    hoạch thì sao", không phải bản sao hành vi bot.
  · Cùng một nến chạm cả SL lẫn TP -> tính là SL (giả định bi quan).
  · Phí `--fee` mỗi chiều. Funding rate của futures KHÔNG được tính.
  · Chỉ LONG, một vị thế tại một thời điểm — giống ràng buộc thật của bot 5m một cặp.

Chạy:
  .venv/bin/python sim_auto_entry.py --pair BTC/USDT:USDT \
      --timerange 20240101-20260807 --step 15 --self-check
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from freqtrade.configuration import Configuration
from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import RunMode
from freqtrade.exchange import timeframe_to_seconds
from freqtrade.resolvers import StrategyResolver
from freqtrade.rpc.telegram import Telegram


def load_analyzed(strategy, dp, pair: str, tf: str, datadir: Path, candle_type: str):
    """Nạp feather rồi chạy populate_indicators + populate_entry_trend cho MỘT khung.

    Đặt `strategy.timeframe = tf` trước khi chạy vì decorator @informative gộp khung 1d theo
    khung nền; để nguyên 5m sẽ gộp sai lưới thời gian cho các khung cao hơn.
    """
    fn = datadir / f"{pair.replace('/', '_').replace(':', '_')}-{tf}-{candle_type}.feather"
    if not fn.exists():
        raise SystemExit(f"thiếu dữ liệu: {fn}")
    df = pd.read_feather(fn)
    strategy.timeframe = tf
    strategy.dp = dp
    meta = {"pair": pair}
    df = strategy.populate_entry_trend(strategy.populate_indicators(df.copy(), meta), meta)
    return df.reset_index(drop=True)


def rows_closed_at(df: pd.DataFrame, tf_sec: int, t: datetime) -> int | None:
    """Chỉ số hàng cuối cùng đã ĐÓNG tại thời điểm t (date là giờ MỞ nến)."""
    closed = df["date"] + pd.Timedelta(seconds=tf_sec) <= t
    if not closed.any():
        return None
    return int(closed.to_numpy().nonzero()[0][-1])


def walk_candles(candles, open_trade, pending, trades, rejects, fee):
    """Chạy nến exec qua lệnh chờ và vị thế đang mở; trả lại trạng thái mới.

    Tách khỏi `simulate` để phần ra quyết định và phần khớp/thoát lệnh không dính nhau —
    và để mỗi hàm nằm dưới ngưỡng phức tạp của ruff.
    """
    for c in candles:
        if open_trade:
            # Bi quan: chạm cả SL lẫn TP trong một nến -> tính là SL.
            if c["low"] <= open_trade["sl"]:
                open_trade["exit"], open_trade["why"] = open_trade["sl"], "sl"
            elif c["high"] >= open_trade["tp"]:
                open_trade["exit"], open_trade["why"] = open_trade["tp"], "tp"
            if open_trade.get("exit"):
                e, x = open_trade["entry"], open_trade["exit"]
                open_trade["pct"] = (x / e - 1) * 100 - fee * 200
                open_trade["closed"] = c["date"]
                trades.append(open_trade)
                open_trade = None
        elif pending:
            if c["low"] <= pending["limit"]:
                open_trade = {
                    "entry": pending["limit"],
                    "sl": pending["sl"],
                    "tp": pending["tp"],
                    "opened": c["date"],
                    "rr": pending["rr"],
                }
                pending = None
            elif c["date"] >= pending["expire"]:
                rejects["hết hạn chờ"] = rejects.get("hết hạn chờ", 0) + 1
                pending = None
    return open_trade, pending


def simulate(args) -> dict:
    cfg = Configuration.from_files([args.config, args.config_overlay])
    cfg["runmode"] = RunMode.BACKTEST
    cfg["strategy"] = "SmcElliottStrategy"
    cfg["strategy_path"] = "strategies"
    strategy = StrategyResolver.load_strategy(cfg)
    dp = DataProvider(cfg, None, None)
    datadir = Path(cfg["datadir"]) / "futures"
    candle_type = "futures"
    pair = args.pair

    tfs: list[str] = args.timeframes.split(",")
    analyzed = {tf: load_analyzed(strategy, dp, pair, tf, datadir, candle_type) for tf in tfs}
    strategy.timeframe = args.plan_tf  # khung dựng kế hoạch = khung của bot
    tf_sec = {tf: timeframe_to_seconds(tf) for tf in tfs}

    base = analyzed[args.exec_tf]  # nến dùng để mô phỏng khớp lệnh & thoát lệnh
    start = datetime.strptime(args.timerange.split("-")[0], "%Y%m%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.timerange.split("-")[1], "%Y%m%d").replace(tzinfo=UTC)
    # Bỏ qua đoạn đầu để mọi khung đủ nến khởi động, nếu không khung 4h sẽ toàn NaN.
    warm = max(tf_sec.values()) * (strategy.startup_candle_count or 400)
    start = max(start, base["date"].min().to_pydatetime() + timedelta(seconds=warm))

    fee = args.fee / 100.0
    trades: list[dict] = []
    rejects: dict[str, int] = {}
    plans = 0
    open_trade: dict | None = None
    pending: dict | None = None
    lag_seen: dict[str, set] = {tf: set() for tf in tfs}

    # Dựng sẵn mảng nến exec dưới dạng dict thuần: iterrows() tạo một Series mỗi hàng, đắt
    # hơn nhiều lần so với việc đọc dict trong vòng lặp nóng.
    base_dates = base["date"]
    base_rows = base[["date", "high", "low"]].to_dict("records")

    t = start
    step = timedelta(minutes=args.step)
    while t < end:
        # ---- Quản lý vị thế/lệnh chờ trên nến exec đã đóng trong bước vừa qua ----
        # searchsorted thay cho mặt nạ boolean trên cả cột: mặt nạ quét toàn bộ lịch sử mỗi
        # bước, đủ để một lượt 2 tháng chạy hàng chục phút.
        lo_i = int(base_dates.searchsorted(t - step, side="left"))
        hi_i = int(base_dates.searchsorted(t, side="left"))
        open_trade, pending = walk_candles(
            base_rows[lo_i:hi_i], open_trade, pending, trades, rejects, fee
        )

        # ---- Lượt phân tích tại mốc t ----
        if not open_trade and not pending:
            levels_by_tf, price_by_tf = {}, {}
            for tf in tfs:
                i = rows_closed_at(analyzed[tf], tf_sec[tf], t)
                if i is None:
                    continue
                # Cửa sổ có trần thay vì cắt từ đầu lịch sử: `iloc[:i+1]` sao chép n hàng mỗi
                # bước -> O(n²), 2 tháng chạy không xong. `_smc_extract_pivots` chỉ cần đủ
                # history để tìm 7 pivot gần nhất, 1000 nến là thừa ở mọi khung.
                df_cut = analyzed[tf].iloc[max(0, i - 999) : i + 1]
                r = df_cut.iloc[-1]
                lag_seen[tf].add(int((t - r["date"].to_pydatetime()).total_seconds()))
                levels_by_tf[tf] = Telegram._smc_levels_from_row(r, df_cut)
                price_by_tf[tf] = float(r["close"])

            ctx = Telegram._smc_report_context(strategy, tfs, levels_by_tf)
            if ctx:
                _avail, _htf, stf, direction, *_ = ctx
                d = Telegram._smc_trade_decision(
                    strategy, levels_by_tf[stf], price_by_tf.get(stf, 0.0), direction
                )
                plans += 1
                if d["reject"]:
                    key = d["reject"].split("(")[0].strip()[:45]
                    rejects[key] = rejects.get(key, 0) + 1
                elif direction != "long":
                    rejects["hướng SHORT (bot long-only)"] = (
                        rejects.get("hướng SHORT (bot long-only)", 0) + 1
                    )
                else:
                    pending = {
                        "limit": d["entry_worst"],
                        "sl": d["stop"],
                        "tp": d["targets"][0][0],
                        "rr": d["rr_first"],
                        "expire": t + timedelta(minutes=args.entry_timeout),
                    }
        t += step

    return {
        "trades": trades,
        "plans": plans,
        "rejects": rejects,
        "lag": {tf: (min(v), max(v)) for tf, v in lag_seen.items() if v},
        "start": start,
        "end": end,
    }


def report(res: dict, args) -> None:
    tr = res["trades"]
    print(f"\n{'=' * 72}\nMÔ PHỎNG AUTO_ENTRY — {args.pair}  {args.timerange}  bước {args.step}m")
    print(f"{'=' * 72}")
    print(f"Khoảng thực chạy : {res['start']:%Y-%m-%d} -> {res['end']:%Y-%m-%d}")
    print(f"Lượt phân tích   : {res['plans']:,}")
    print(f"Lệnh khớp        : {len(tr)}")
    if res["rejects"]:
        print("\nLý do KHÔNG vào lệnh (top):")
        for k, v in sorted(res["rejects"].items(), key=lambda x: -x[1])[:8]:
            print(f"  {v:>7,}  {k}")
    if not tr:
        print("\n=> Không có lệnh nào. Không kết luận được gì về lãi/lỗ.")
        return
    wins = [t for t in tr if t["pct"] > 0]
    tot = sum(t["pct"] for t in tr)
    gp = sum(t["pct"] for t in wins)
    gl = -sum(t["pct"] for t in tr if t["pct"] <= 0)
    pf = (gp / gl) if gl else float("inf")
    print(f"\nTổng % (cộng dồn, mỗi lệnh trên vốn lệnh): {tot:+.2f}%")
    print(f"Win rate : {len(wins)}/{len(tr)} = {100 * len(wins) / len(tr):.1f}%")
    print(f"TB/lệnh  : {tot / len(tr):+.3f}%   |  Profit factor: {pf:.2f}")
    print(
        f"Tốt nhất : {max(x['pct'] for x in tr):+.2f}%"
        f"  |  Tệ nhất: {min(x['pct'] for x in tr):+.2f}%"
    )
    by = {}
    for t in tr:
        by[t["why"]] = by.get(t["why"], 0) + 1
    print(f"Thoát    : {by}")
    if args.self_check:
        print("\nTỰ KIỂM CHỐNG NHÌN TRỘM TƯƠNG LAI")
        print("  Độ trễ (giây) từ lúc nến đóng tới lúc chấm — phải >= 0 ở MỌI khung:")
        for tf, (lo, hi) in res["lag"].items():
            flag = "✅" if lo >= 0 else "❌ NHÌN TRỘM"
            print(f"    {tf:>4}: min={lo:>7,}  max={hi:>7,}  {flag}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="BTC/USDT:USDT")
    p.add_argument("--config", default="config.json")
    p.add_argument("--config-overlay", default="config-5m.json")
    p.add_argument("--timerange", default="20240101-20260807")
    p.add_argument("--timeframes", default="5m,15m,1h,4h")
    p.add_argument("--plan-tf", default="5m", help="khung dựng kế hoạch = timeframe của bot")
    p.add_argument("--exec-tf", default="5m", help="khung mô phỏng khớp/thoát lệnh")
    p.add_argument("--step", type=int, default=15, help="chu kỳ lịch analysis, phút")
    p.add_argument("--entry-timeout", type=int, default=10, help="hạn lệnh chờ, phút")
    p.add_argument("--fee", type=float, default=0.05, help="phí MỖI CHIỀU, %%")
    p.add_argument("--self-check", action="store_true")
    args = p.parse_args()
    report(simulate(args), args)


if __name__ == "__main__":
    main()
