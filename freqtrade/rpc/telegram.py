# pragma pylint: disable=unused-argument, unused-variable, protected-access, invalid-name

"""
This module manage Telegram communication
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable, Coroutine, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial, wraps
from html import escape
from itertools import chain
from math import isnan
from threading import Thread
from typing import Any, Literal

from tabulate import tabulate
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import MessageLimit, ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import Application, CallbackContext, CallbackQueryHandler, CommandHandler
from telegram.helpers import escape_markdown

from freqtrade.__init__ import __version__
from freqtrade.constants import DUST_PER_COIN, Config
from freqtrade.enums import MarketDirection, RPCMessageType, SignalDirection, TradingMode
from freqtrade.exceptions import OperationalException
from freqtrade.misc import chunks, plural
from freqtrade.persistence import Trade
from freqtrade.rpc import RPC, RPCException, RPCHandler
from freqtrade.rpc.rpc_types import RPCEntryMsg, RPCExitMsg, RPCOrderMsg, RPCSendMsg
from freqtrade.util import (
    dt_from_ts,
    dt_humanize_delta,
    fmt_coin,
    fmt_coin2,
    format_date,
    format_pct,
    round_value,
)


MAX_MESSAGE_LENGTH = MessageLimit.MAX_TEXT_LENGTH

# /smc không tham số: báo cáo nhanh 4 cặp cố định (xem `_smc`).
# Khác /analysis — lệnh đó phân tích 1 cặp trên nhiều khung.
SMC_QUICK_BASES = ("BTC", "ETH", "XAU", "SOL")
# Khung dự phòng khi không đọc được khung giao dịch của bot (xem `_smc_quick_timeframes`).
SMC_QUICK_TIMEFRAME = "4h"

# /scalp: cùng 4 cặp nhưng khung ngắn, cho lệnh trong ngày.
# THỨ TỰ THẤP -> CAO LÀ BẮT BUỘC: `_smc_signal_report` lấy avail[0] làm khung dựng kế hoạch
# Entry/SL/TP và avail[-1] làm khung bias. Đảo thành ("15m", "5m") sẽ ra bias 5m + kế hoạch
# 15m — ngược hẳn ý đồ scalping.
SMC_SCALP_TIMEFRAMES = ("5m", "15m")


logger = logging.getLogger(__name__)

logger.debug("Included module rpc.telegram ...")


def safe_async_db(func: Callable[..., Any]):
    """
    Decorator to safely handle sessions when switching async context
    :param func: function to decorate
    :return: decorated function
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        """Decorator logic"""
        try:
            return func(*args, **kwargs)
        finally:
            Trade.session.remove()

    return wrapper


@dataclass
class TimeunitMappings:
    header: str
    message: str
    message2: str
    callback: str
    default: int
    dateformat: str


def authorized_only(command_handler: Callable[..., Coroutine[Any, Any, None]]):
    """
    Decorator to check if the message comes from the correct chat_id
    can only be used with Telegram Class to decorate instance methods.
    :param command_handler: Telegram CommandHandler
    :return: decorated function
    """

    @wraps(command_handler)
    async def wrapper(self, *args, **kwargs) -> None:
        """Decorator logic"""
        update = kwargs.get("update") or args[0]

        # Reject unauthorized messages
        message: Message = (
            update.message if update.callback_query is None else update.callback_query.message
        )
        cchat_id: int = int(message.chat_id)
        ctopic_id: int | None = message.message_thread_id
        from_user_id: str = str(update.effective_user.id if update.effective_user else "")

        chat_id = int(self._config["telegram"]["chat_id"])
        if cchat_id != chat_id:
            logger.info(f"Rejected unauthorized message from: {cchat_id}")
            return None
        if (topic_id := self._config["telegram"].get("topic_id")) is not None:
            if str(ctopic_id) != topic_id:
                # This can be quite common in multi-topic environments.
                logger.debug(f"Rejected message from wrong channel: {cchat_id}, {ctopic_id}")
                return None

        authorized = self._config["telegram"].get("authorized_users", None)
        if authorized is not None and from_user_id not in authorized:
            logger.info(f"Unauthorized user tried to control the bot: {from_user_id}")
            return None
        # Rollback session to avoid getting data stored in a transaction.
        Trade.rollback()
        logger.debug("Executing handler: %s for chat_id: %s", command_handler.__name__, chat_id)
        try:
            return await command_handler(self, *args, **kwargs)
        except RPCException as e:
            await self._send_msg(str(e))
        except BaseException:
            logger.exception("Exception occurred within Telegram module")
        finally:
            Trade.session.remove()

    return wrapper


class Telegram(RPCHandler):
    """This class handles all telegram communication"""

    def __init__(self, rpc: RPC, config: Config) -> None:
        """
        Init the Telegram call, and init the super class RPCHandler
        :param rpc: instance of RPC Helper class
        :param config: Configuration object
        :return: None
        """
        super().__init__(rpc, config)

        self._app: Application
        self._loop: asyncio.AbstractEventLoop
        self._shutdown_event: asyncio.Event
        self._init_keyboard()
        self._start_thread()

    def _start_thread(self):
        """
        Creates and starts the polling thread
        """
        self._thread = Thread(target=self._init, name="FTTelegram")
        self._thread.start()

    def _init_keyboard(self) -> None:
        """
        Validates the keyboard configuration from telegram config
        section.
        """
        self._keyboard: list[list[str | KeyboardButton]] = [
            ["/daily", "/profit", "/balance"],
            ["/status", "/status table"],
            ["/start", "/stop", "/help"],
        ]
        # do not allow commands with mandatory arguments and critical cmds
        # TODO: DRY! - its not good to list all valid cmds here. But otherwise
        #       this needs refactoring of the whole telegram module (same
        #       problem in _help()).
        valid_keys: list[str] = [
            r"/start$",
            r"/pause$",
            r"/stop$",
            r"/status$",
            r"/status table$",
            r"/trades$",
            r"/buys",
            r"/entries",
            r"/sells",
            r"/exits",
            r"/mix_tags",
            r"/daily$",
            r"/daily \d+$",
            r"/analysis$",
            r"/analysis [\w/]+$",
            r"/smc$",
            r"/scalp$",
            r"/profit([_ ]long|[_ ]short)?$",
            r"/profit([_ ]long|[_ ]short)? \d+$",
            r"/stats$",
            r"/locks$",
            r"/balance$",
            r"/stopbuy$",
            r"/stopentry$",
            r"/reload_config$",
            r"/show_config$",
            r"/logs$",
            r"/whitelist$",
            r"/whitelist(\ssorted|\sbaseonly)+$",
            r"/blacklist$",
            r"/bl_delete$",
            r"/weekly$",
            r"/weekly \d+$",
            r"/monthly$",
            r"/monthly \d+$",
            r"/forcebuy$",
            r"/forcelong$",
            r"/forceshort$",
            r"/forcesell$",
            r"/forceexit$",
            r"/health$",
            r"/help$",
            r"/version$",
            r"/marketdir (long|short|even|none)$",
            r"/marketdir$",
        ]
        # Create keys for generation
        valid_keys_print = [k.replace("$", "") for k in valid_keys]

        # custom keyboard specified in config.json
        cust_keyboard = self._config["telegram"].get("keyboard", [])
        if cust_keyboard:
            combined = "(" + ")|(".join(valid_keys) + ")"
            # check for valid shortcuts
            invalid_keys = [
                b for b in chain.from_iterable(cust_keyboard) if not re.match(combined, b)
            ]
            if len(invalid_keys):
                err_msg = (
                    "config.telegram.keyboard: Invalid commands for "
                    f"custom Telegram keyboard: {invalid_keys}"
                    f"\nvalid commands are: {valid_keys_print}"
                )
                raise OperationalException(err_msg)
            else:
                self._keyboard = cust_keyboard
                logger.info(f"using custom keyboard from config.json: {self._keyboard}")

    def _init_telegram_app(self):
        return Application.builder().token(self._config["telegram"]["token"]).build()

    def _init(self) -> None:
        """
        Initializes this module with the given config,
        registers all known command handlers
        and starts polling for message updates
        Runs in a separate thread.
        """
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        # Tín hiệu dừng polling — set từ cleanup() (thread khác) qua loop này.
        self._shutdown_event = asyncio.Event()

        self._app = self._init_telegram_app()

        # Register command handler and start telegram message polling
        handles = [
            CommandHandler("status", self._status),
            CommandHandler("profit", self._profit),
            CommandHandler("balance", self._balance),
            CommandHandler("start", self._start),
            CommandHandler("stop", self._stop),
            CommandHandler(["forcesell", "forceexit", "fx"], self._force_exit),
            CommandHandler(
                ["forcebuy", "forcelong"],
                partial(self._force_enter, order_side=SignalDirection.LONG),
            ),
            CommandHandler(
                "forceshort", partial(self._force_enter, order_side=SignalDirection.SHORT)
            ),
            CommandHandler("reload_trade", self._reload_trade_from_exchange),
            CommandHandler("trades", self._trades),
            CommandHandler("delete", self._delete_trade),
            CommandHandler(["coo", "cancel_open_order"], self._cancel_open_order),
            CommandHandler(["buys", "entries"], self._enter_tag_performance),
            CommandHandler(["sells", "exits"], self._exit_reason_performance),
            CommandHandler("mix_tags", self._mix_tag_performance),
            CommandHandler("stats", self._stats),
            CommandHandler("daily", self._daily),
            CommandHandler("analysis", self._analysis),
            CommandHandler("smc", self._smc),
            CommandHandler("scalp", self._scalp),
            CommandHandler("weekly", self._weekly),
            CommandHandler("monthly", self._monthly),
            CommandHandler("locks", self._locks),
            CommandHandler(["unlock", "delete_locks"], self._delete_locks),
            CommandHandler(["reload_config", "reload_conf"], self._reload_config),
            CommandHandler(["show_config", "show_conf"], self._show_config),
            CommandHandler(["stopbuy", "stopentry", "pause"], self._pause),
            CommandHandler("whitelist", self._whitelist),
            CommandHandler("blacklist", self._blacklist),
            CommandHandler(["blacklist_delete", "bl_delete"], self._blacklist_delete),
            CommandHandler("logs", self._logs),
            CommandHandler("health", self._health),
            CommandHandler("help", self._help),
            CommandHandler("version", self._version),
            CommandHandler("marketdir", self._changemarketdir),
            CommandHandler("order", self._order),
            CommandHandler("list_custom_data", self._list_custom_data),
            CommandHandler("tg_info", self._tg_info),
            CommandHandler("profit_long", self._profit_long),
            CommandHandler("profit_short", self._profit_short),
        ]
        callbacks = [
            CallbackQueryHandler(self._status_table, pattern="update_status_table"),
            CallbackQueryHandler(self._daily, pattern="update_daily"),
            CallbackQueryHandler(self._weekly, pattern="update_weekly"),
            CallbackQueryHandler(self._monthly, pattern="update_monthly"),
            CallbackQueryHandler(self._profit_long, pattern="update_profit_long"),
            CallbackQueryHandler(self._profit_short, pattern="update_profit_short"),
            CallbackQueryHandler(self._profit, pattern=r"update_profit$"),
            CallbackQueryHandler(self._balance, pattern="update_balance"),
            CallbackQueryHandler(
                self._enter_tag_performance, pattern="update_enter_tag_performance"
            ),
            CallbackQueryHandler(
                self._exit_reason_performance, pattern="update_exit_reason_performance"
            ),
            CallbackQueryHandler(self._mix_tag_performance, pattern="update_mix_tag_performance"),
            CallbackQueryHandler(self._force_exit_inline, pattern=r"force_exit__\S+"),
            CallbackQueryHandler(self._force_enter_inline, pattern=r"force_enter__\S+"),
        ]
        for handle in handles:
            self._app.add_handler(handle)

        for callback in callbacks:
            self._app.add_handler(callback)

        logger.info(
            "rpc.telegram is listening for following commands: %s",
            [[x for x in sorted(h.commands)] for h in handles],
        )
        self._loop.run_until_complete(self._startup_telegram())

    async def _startup_telegram(self) -> None:
        retries = 3
        attempt = 0
        while attempt < retries:
            try:
                await self._app.initialize()
                await self._app.start()
                break
            except Exception as ex:
                logger.error(
                    "Error starting Telegram bot (attempt %d/%d): %s", attempt + 1, retries, ex
                )
                attempt += 1
                if attempt == retries:
                    logger.warning("Telegram init failed.")
                    return
                await asyncio.sleep(2)
        if self._app.updater:
            await self._app.updater.start_polling(
                bootstrap_retries=10,
                timeout=20,
                drop_pending_updates=True,
            )
            # Task nền: tự động gửi /analysis theo lịch cố định (nếu bật config).
            sched_task = asyncio.create_task(self._analysis_schedule_loop())
            # Chờ tín hiệu dừng (cleanup) HOẶC updater tự dừng (polling lỗi).
            while not self._shutdown_event.is_set() and self._app.updater.running:
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=10)
                except TimeoutError:
                    continue
            sched_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sched_task
            # Teardown TRỌN VẸN trong loop này -> hoàn tất stop/shutdown trước
            # khi loop dừng, tránh "cannot schedule new futures after shutdown".
            await self._cleanup_telegram()

    async def _cleanup_telegram(self) -> None:
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()

    def cleanup(self) -> None:
        """
        Stops all running telegram threads.
        :return: None
        """
        # Báo cho vòng lặp polling dừng; teardown chạy trong _startup_telegram.
        # This can take up to `timeout` from the call to `start_polling`.
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._shutdown_event.set)
        self._thread.join()

    def _exchange_from_msg(self, msg: RPCOrderMsg) -> str:
        """
        Extracts the exchange name from the given message.
        :param msg: The message to extract the exchange name from.
        :return: The exchange name.
        """
        return f"{msg['exchange']}{' (dry)' if self._config['dry_run'] else ''}"

    def _add_analyzed_candle(self, pair: str) -> str:
        candle_val = (
            self._config["telegram"].get("notification_settings", {}).get("show_candle", "off")
        )
        if candle_val != "off":
            if candle_val == "ohlc":
                analyzed_df, _ = self._rpc._freqtrade.dataprovider.get_analyzed_dataframe(
                    pair, self._config["timeframe"]
                )
                candle = analyzed_df.iloc[-1].squeeze() if len(analyzed_df) > 0 else None
                if candle is not None:
                    return (
                        f"*Candle OHLC*: `{candle['open']}, {candle['high']}, "
                        f"{candle['low']}, {candle['close']}`\n"
                    )

        return ""

    def _format_entry_msg(self, msg: RPCEntryMsg) -> str:
        is_fill = msg["type"] in [RPCMessageType.ENTRY_FILL]
        emoji = "\N{CHECK MARK}" if is_fill else "\N{LARGE BLUE CIRCLE}"

        terminology = {
            "1_enter": "New Trade",
            "1_entered": "New Trade filled",
            "x_enter": "Increasing position",
            "x_entered": "Position increase filled",
        }

        key = f"{'x' if msg['sub_trade'] else '1'}_{'entered' if is_fill else 'enter'}"
        wording = terminology[key]

        message = (
            f"{emoji} *{self._exchange_from_msg(msg)}:*"
            f" {wording} (#{msg['trade_id']})\n"
            f"*Pair:* `{msg['pair']}`\n"
        )
        message += self._add_analyzed_candle(msg["pair"])
        message += f"*Enter Tag:* `{msg['enter_tag']}`\n" if msg.get("enter_tag") else ""
        message += f"*Amount:* `{round_value(msg['amount'], 8)}`\n"
        message += f"*Direction:* `{msg['direction']}"
        if msg.get("leverage") and msg.get("leverage", 1.0) != 1.0:
            message += f" ({msg['leverage']:.3g}x)"
        message += "`\n"
        message += f"*Open Rate:* `{fmt_coin2(msg['open_rate'], msg['quote_currency'])}`\n"
        if msg["type"] == RPCMessageType.ENTRY and msg["current_rate"]:
            message += (
                f"*Current Rate:* `{fmt_coin2(msg['current_rate'], msg['quote_currency'])}`\n"
            )

        profit_fiat_extra = self.__format_profit_fiat(msg, "stake_amount")  # type: ignore
        total = fmt_coin(msg["stake_amount"], msg["quote_currency"])

        message += f"*{'New ' if msg['sub_trade'] else ''}Total:* `{total}{profit_fiat_extra}`"

        return message

    def _format_exit_msg(self, msg: RPCExitMsg) -> str:
        duration = msg["close_date"].replace(microsecond=0) - msg["open_date"].replace(
            microsecond=0
        )
        duration_min = duration.total_seconds() / 60

        leverage_text = (
            f" ({msg['leverage']:.3g}x)"
            if msg.get("leverage") and msg.get("leverage", 1.0) != 1.0
            else ""
        )

        profit_fiat_extra = self.__format_profit_fiat(msg, "profit_amount")

        profit_extra = (
            f" ({msg['gain']}: {fmt_coin(msg['profit_amount'], msg['quote_currency'])}"
            f"{profit_fiat_extra})"
        )

        is_fill = msg["type"] == RPCMessageType.EXIT_FILL
        is_sub_trade = msg.get("sub_trade")
        is_sub_profit = msg["profit_amount"] != msg.get("cumulative_profit")
        is_final_exit = msg.get("is_final_exit", False) and is_sub_profit
        profit_prefix = "Sub " if is_sub_trade else ""
        cp_extra = ""
        exit_wording = "Exited" if is_fill else "Exiting"
        if is_sub_trade or is_final_exit:
            cp_fiat = self.__format_profit_fiat(msg, "cumulative_profit")

            if is_final_exit:
                profit_prefix = "Sub "
                cp_extra = (
                    f"*Final Profit:* `{format_pct(msg['final_profit_ratio'])} "
                    f"({fmt_coin(msg['cumulative_profit'], msg['stake_currency'])}{cp_fiat})`\n"
                )
            else:
                exit_wording = f"Partially {exit_wording.lower()}"
                if msg["cumulative_profit"]:
                    cp_extra = (
                        f"*Cumulative Profit:* `"
                        f"{fmt_coin(msg['cumulative_profit'], msg['stake_currency'])}{cp_fiat}`\n"
                    )
        enter_tag = f"*Enter Tag:* `{msg['enter_tag']}`\n" if msg.get("enter_tag") else ""
        message = (
            f"{self._get_exit_emoji(msg)} *{self._exchange_from_msg(msg)}:* "
            f"{exit_wording} {msg['pair']} (#{msg['trade_id']})\n"
            f"{self._add_analyzed_candle(msg['pair'])}"
            f"*{f'{profit_prefix}Profit' if is_fill else f'Unrealized {profit_prefix}Profit'}:* "
            f"`{format_pct(msg['profit_ratio'])}{profit_extra}`\n"
            f"{cp_extra}"
            f"{enter_tag}"
            f"*Exit Reason:* `{msg['exit_reason']}`\n"
            f"*Direction:* `{msg['direction']}"
            f"{leverage_text}`\n"
            f"*Amount:* `{round_value(msg['amount'], 8)}`\n"
            f"*Open Rate:* `{fmt_coin2(msg['open_rate'], msg['quote_currency'])}`\n"
        )
        if msg["type"] == RPCMessageType.EXIT and msg["current_rate"]:
            message += (
                f"*Current Rate:* `{fmt_coin2(msg['current_rate'], msg['quote_currency'])}`\n"
            )
            if msg["order_rate"]:
                message += f"*Exit Rate:* `{fmt_coin2(msg['order_rate'], msg['quote_currency'])}`"
        elif msg["type"] == RPCMessageType.EXIT_FILL:
            message += f"*Exit Rate:* `{fmt_coin2(msg['close_rate'], msg['quote_currency'])}`"

        if is_sub_trade:
            stake_amount_fiat = self.__format_profit_fiat(msg, "stake_amount")

            rem = fmt_coin(msg["stake_amount"], msg["quote_currency"])
            message += f"\n*Remaining:* `{rem}{stake_amount_fiat}`"
        else:
            message += f"\n*Duration:* `{duration} ({duration_min:.1f} min)`"
        return message

    def __format_profit_fiat(
        self, msg: RPCExitMsg, key: Literal["stake_amount", "profit_amount", "cumulative_profit"]
    ) -> str:
        """
        Format Fiat currency to append to regular profit output
        """
        profit_fiat_extra = ""
        if self._rpc._fiat_converter and (fiat_currency := msg.get("fiat_currency")):
            profit_fiat = self._rpc._fiat_converter.convert_amount(
                msg[key], msg["stake_currency"], fiat_currency
            )
            profit_fiat_extra = f" / {profit_fiat:.3f} {fiat_currency}"
        return profit_fiat_extra

    def compose_message(self, msg: RPCSendMsg) -> str | None:
        if msg["type"] == RPCMessageType.ENTRY or msg["type"] == RPCMessageType.ENTRY_FILL:
            message = self._format_entry_msg(msg)

        elif msg["type"] == RPCMessageType.EXIT or msg["type"] == RPCMessageType.EXIT_FILL:
            message = self._format_exit_msg(msg)

        elif (
            msg["type"] == RPCMessageType.ENTRY_CANCEL or msg["type"] == RPCMessageType.EXIT_CANCEL
        ):
            message_side = "enter" if msg["type"] == RPCMessageType.ENTRY_CANCEL else "exit"
            message = (
                f"\N{WARNING SIGN} *{self._exchange_from_msg(msg)}:* "
                f"Cancelling {'partial ' if msg.get('sub_trade') else ''}"
                f"{message_side} Order for {msg['pair']} "
                f"(#{msg['trade_id']}). Reason: {msg['reason']}."
            )

        elif msg["type"] == RPCMessageType.PROTECTION_TRIGGER:
            message = (
                f"*Protection* triggered due to {msg['reason']}. "
                f"`{msg['pair']}` will be locked until `{msg['lock_end_time']}`."
            )

        elif msg["type"] == RPCMessageType.PROTECTION_TRIGGER_GLOBAL:
            message = (
                f"*Protection* triggered due to {msg['reason']}. "
                f"*All pairs* will be locked until `{msg['lock_end_time']}`."
            )

        elif msg["type"] == RPCMessageType.STATUS:
            message = f"*Status:* `{msg['status']}`"

        elif msg["type"] == RPCMessageType.WARNING:
            message = f"\N{WARNING SIGN} *Warning:* `{msg['status']}`"
        elif msg["type"] == RPCMessageType.EXCEPTION:
            # Errors will contain exceptions, which are wrapped in triple ticks.
            message = f"\N{WARNING SIGN} *ERROR:* \n {msg['status']}"

        elif msg["type"] == RPCMessageType.STARTUP:
            message = f"{msg['status']}"
        elif msg["type"] == RPCMessageType.STRATEGY_MSG:
            message = f"{msg['msg']}"
        else:
            logger.debug("Unknown message type: %s", msg["type"])
            return None
        return message

    def _message_loudness(self, msg: RPCSendMsg) -> str:
        """Determine the loudness of the message - on, off or silent"""
        default_noti = "on"

        msg_type = msg["type"]
        noti = ""
        if msg["type"] == RPCMessageType.EXIT or msg["type"] == RPCMessageType.EXIT_FILL:
            sell_noti = (
                self._config["telegram"].get("notification_settings", {}).get(str(msg_type), {})
            )

            # For backward compatibility sell still can be string
            if isinstance(sell_noti, str):
                noti = sell_noti
            else:
                default_noti = sell_noti.get("*", default_noti)
                noti = sell_noti.get(str(msg["exit_reason"]), default_noti)
        else:
            noti = (
                self._config["telegram"]
                .get("notification_settings", {})
                .get(str(msg_type), default_noti)
            )

        return noti

    def send_msg(self, msg: RPCSendMsg) -> None:
        """Send a message to telegram channel"""
        noti = self._message_loudness(msg)

        if noti == "off":
            logger.info(f"Notification '{msg['type']}' not sent.")
            # Notification disabled
            return

        message = self.compose_message(deepcopy(msg))
        if message:
            asyncio.run_coroutine_threadsafe(
                self._send_msg(message, disable_notification=(noti == "silent")), self._loop
            )

    def _get_exit_emoji(self, msg):
        """
        Get emoji for exit-messages
        """

        if float(msg["profit_ratio"]) >= 0.05:
            return "\N{ROCKET}"
        elif float(msg["profit_ratio"]) >= 0.0:
            return "\N{EIGHT SPOKED ASTERISK}"
        elif msg["exit_reason"] == "stop_loss":
            return "\N{WARNING SIGN}"
        else:
            return "\N{CROSS MARK}"

    def _prepare_order_details(self, filled_orders: list, quote_currency: str, is_open: bool):
        """
        Prepare details of trade with entry adjustment enabled
        """
        lines_detail: list[str] = []
        if len(filled_orders) > 0:
            first_avg = filled_orders[0]["safe_price"]
        order_nr = 0
        for order in filled_orders:
            lines: list[str] = []
            if order["is_open"] is True:
                continue
            order_nr += 1
            wording = "Entry" if order["ft_is_entry"] else "Exit"

            cur_entry_amount = order["filled"] or order["amount"]
            cur_entry_average = order["safe_price"]
            lines.append("  ")
            lines.append(f"*{wording} #{order_nr}:*")
            if order_nr == 1:
                lines.append(
                    f"*Amount:* {round_value(cur_entry_amount, 8)} "
                    f"({fmt_coin(order['cost'], quote_currency)})"
                )
                lines.append(f"*Average Price:* {round_value(cur_entry_average, 8)}")
            else:
                # TODO: This calculation ignores fees.
                price_to_1st_entry = (cur_entry_average - first_avg) / first_avg
                if is_open:
                    lines.append(f"({dt_humanize_delta(order['order_filled_date'])})")
                lines.append(
                    f"*Amount:* {round_value(cur_entry_amount, 8)} "
                    f"({fmt_coin(order['cost'], quote_currency)})"
                )
                lines.append(
                    f"*Average {wording} Price:* {round_value(cur_entry_average, 8)} "
                    f"({format_pct(price_to_1st_entry)} from 1st entry rate)"
                )
                lines.append(f"*Order Filled:* {order['order_filled_date']}")

            lines_detail.append("\n".join(lines))

        return lines_detail

    @authorized_only
    async def _order(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /order.
        Returns the orders of the trade
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        trade_ids = []
        if context.args and len(context.args) > 0:
            trade_ids = [int(i) for i in context.args if i.isnumeric()]

        results = self._rpc._rpc_trade_status(trade_ids=trade_ids)
        for r in results:
            lines = [f"*Order List for Trade #*`{r['trade_id']}`"]

            lines_detail = self._prepare_order_details(
                r["orders"], r["quote_currency"], r["is_open"]
            )
            lines.extend(lines_detail if lines_detail else "")
            await self.__send_order_msg(lines, r)

    async def __send_order_msg(self, lines: list[str], r: dict[str, Any]) -> None:
        """
        Send status message.
        """
        msg = ""

        for line in lines:
            if line:
                if (len(msg) + len(line) + 1) < MAX_MESSAGE_LENGTH:
                    msg += line + "\n"
                else:
                    await self._send_msg(msg)
                    msg = f"*Order List for Trade #*`{r['trade_id']}` - continued\n" + line + "\n"

        await self._send_msg(msg)

    @authorized_only
    async def _status(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status.
        Returns the current TradeThread status
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        if context.args and "table" in context.args:
            await self._status_table(update, context)
            return
        else:
            await self._status_msg(update, context)

    async def _status_msg(self, update: Update, context: CallbackContext) -> None:
        """
        handler for `/status` and `/status <id>`.

        """
        # Check if there's at least one numerical ID provided.
        # If so, try to get only these trades.
        trade_ids = []
        if context.args and len(context.args) > 0:
            trade_ids = [int(i) for i in context.args if i.isnumeric()]

        results = self._rpc._rpc_trade_status(trade_ids=trade_ids)
        position_adjust = self._config.get("position_adjustment_enable", False)
        max_entries = self._config.get("max_entry_position_adjustment", -1)
        for r in results:
            r["open_date_hum"] = dt_humanize_delta(r["open_date"])

            r["stake_amount_r"] = fmt_coin(r["stake_amount"], r["quote_currency"])
            r["max_stake_amount_r"] = fmt_coin(
                r["max_stake_amount"] or r["stake_amount"], r["quote_currency"]
            )
            r["profit_abs_r"] = fmt_coin(r["profit_abs"], r["quote_currency"])
            r["realized_profit_r"] = fmt_coin(r["realized_profit"], r["quote_currency"])
            r["total_profit_abs_r"] = fmt_coin(r["total_profit_abs"], r["quote_currency"])
            lines = [
                f"*Trade ID:* `{r['trade_id']}`"
                + (f" `(since {r['open_date_hum']})`" if r["is_open"] else ""),
                f"*Current Pair:* {r['pair']}",
                (
                    f"*Direction:* {'`Short`' if r.get('is_short') else '`Long`'}"
                    + (f" ` ({r['leverage']}x)`" if r.get("leverage") else "")
                ),
                f"*Amount:* `{r['amount']} ({r['stake_amount_r']})`",
                f"*Total invested:* `{r['max_stake_amount_r']}`" if position_adjust else "",
                f"*Enter Tag:* `{r['enter_tag']}`" if r["enter_tag"] else "",
                f"*Exit Reason:* `{r['exit_reason']}`" if r.get("exit_reason") else "",
            ]

            if position_adjust:
                max_buy_str = f"/{max_entries + 1}" if (max_entries > 0) else ""
                lines.extend(
                    [
                        f"*Number of Entries:* `{r['nr_of_successful_entries']}{max_buy_str}`",
                        f"*Number of Exits:* `{r['nr_of_successful_exits']}`",
                    ]
                )

            lines.extend(
                [
                    f"*Open Rate:* `{round_value(r['open_rate'], 8)}`",
                    f"*Close Rate:* `{round_value(r['close_rate'], 8)}`" if r["close_rate"] else "",
                    f"*Open Date:* `{r['open_date']}`",
                    f"*Close Date:* `{r['close_date']}`" if r["close_date"] else "",
                    (
                        f" \n*Current Rate:* `{round_value(r['current_rate'], 8)}`"
                        if r["is_open"]
                        else ""
                    ),
                    ("*Unrealized Profit:* " if r["is_open"] else "*Close Profit: *")
                    + f"`{format_pct(r['profit_ratio'])}` `({r['profit_abs_r']})`",
                ]
            )

            if r["is_open"]:
                if (
                    r.get("realized_profit") is not None
                    and r.get("realized_profit_ratio") is not None
                ):
                    lines.append(
                        f"*Realized Profit:* `{format_pct(r['realized_profit_ratio'])} "
                        f"({r['realized_profit_r']})`"
                    )
                if r.get("total_profit_ratio") is not None:
                    lines.append(
                        f"*Total Profit:* `{format_pct(r['total_profit_ratio'])} "
                        f"({r['total_profit_abs_r']})`"
                    )

                # Append empty line to improve readability
                lines.append(" ")
                # Adding liquidation only if it is not None
                if liquidation := r.get("liquidation_price"):
                    lines.append(f"*Liquidation:* `{round_value(liquidation, 8)}`")

                if (
                    r["stop_loss_abs"] != r["initial_stop_loss_abs"]
                    and r["initial_stop_loss_ratio"] is not None
                ):
                    # Adding initial stoploss only if it is different from stoploss
                    lines.append(
                        f"*Initial Stoploss:* `{round_value(r['initial_stop_loss_abs'], 8)}` "
                        f"`({format_pct(r['initial_stop_loss_ratio'])})`"
                    )

                # Adding stoploss and stoploss percentage only if it is not None
                lines.append(
                    f"*Stoploss:* `{round_value(r['stop_loss_abs'], 8)}` "
                    + (f"`({format_pct(r['stop_loss_ratio'])})`" if r["stop_loss_ratio"] else "")
                )
                lines.append(
                    f"*Stoploss distance:* `{round_value(r['stoploss_current_dist'], 8)}` "
                    f"`({format_pct(r['stoploss_current_dist_ratio'])})`"
                )
                if open_orders := r.get("open_orders"):
                    lines.append(
                        f"*Open Order:* `{open_orders}`"
                        + (f"- `{r['exit_order_status']}`" if r["exit_order_status"] else "")
                    )

            await self.__send_status_msg(lines, r)

    async def __send_status_msg(self, lines: list[str], r: dict[str, Any]) -> None:
        """
        Send status message.
        """
        msg = ""

        for line in lines:
            if line:
                if (len(msg) + len(line) + 1) < MAX_MESSAGE_LENGTH:
                    msg += line + "\n"
                else:
                    await self._send_msg(msg)
                    msg = f"*Trade ID:* `{r['trade_id']}` - continued\n" + line + "\n"

        await self._send_msg(msg)

    @authorized_only
    async def _status_table(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status table.
        Returns the current TradeThread status in table format
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        fiat_currency = self._config.get("fiat_display_currency", "")
        statlist, head, fiat_profit_sum, fiat_total_profit_sum = self._rpc._rpc_status_table(
            self._config["stake_currency"], fiat_currency
        )

        show_total = not isnan(fiat_profit_sum) and len(statlist) > 1
        show_total_realized = (
            not isnan(fiat_total_profit_sum) and len(statlist) > 1 and fiat_profit_sum
        ) != fiat_total_profit_sum
        max_trades_per_msg = 50
        """
        Calculate the number of messages of 50 trades per message
        0.99 is used to make sure that there are no extra (empty) messages
        As an example with 50 trades, there will be int(50/50 + 0.99) = 1 message
        """
        messages_count = max(int(len(statlist) / max_trades_per_msg + 0.99), 1)
        for i in range(0, messages_count):
            trades = statlist[i * max_trades_per_msg : (i + 1) * max_trades_per_msg]
            if show_total and i == messages_count - 1:
                # append total line
                trades.append(["Total", "", "", f"{fiat_profit_sum:.2f} {fiat_currency}"])
                if show_total_realized:
                    trades.append(
                        [
                            "Total",
                            "(incl. realized Profits)",
                            "",
                            f"{fiat_total_profit_sum:.2f} {fiat_currency}",
                        ]
                    )

            message = tabulate(trades, headers=head, tablefmt="simple")
            if show_total and i == messages_count - 1:
                # insert separators line between Total
                lines = message.split("\n")
                offset = 2 if show_total_realized else 1
                message = "\n".join(lines[:-offset] + [lines[1]] + lines[-offset:])
            await self._send_msg(
                f"<pre>{message}</pre>",
                parse_mode=ParseMode.HTML,
                reload_able=True,
                callback_path="update_status_table",
                query=update.callback_query,
            )

    async def _timeunit_stats(self, update: Update, context: CallbackContext, unit: str) -> None:
        """
        Handler for /daily <n>
        Returns a daily profit (in BTC) over the last n days.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        vals = {
            "days": TimeunitMappings("Day", "Daily", "days", "update_daily", 7, "%Y-%m-%d"),
            "weeks": TimeunitMappings(
                "Monday", "Weekly", "weeks (starting from Monday)", "update_weekly", 8, "%Y-%m-%d"
            ),
            "months": TimeunitMappings("Month", "Monthly", "months", "update_monthly", 6, "%Y-%m"),
        }
        val = vals[unit]

        stake_cur = self._config["stake_currency"]
        fiat_disp_cur = self._config.get("fiat_display_currency", "")
        try:
            timescale = int(context.args[0]) if context.args else val.default
        except (TypeError, ValueError, IndexError):
            timescale = val.default
        stats = self._rpc._rpc_timeunit_profit(timescale, stake_cur, fiat_disp_cur, unit)
        stats_tab = tabulate(
            [
                [
                    f"{period['date']:{val.dateformat}} ({period['trade_count']})",
                    f"{fmt_coin(period['abs_profit'], stats['stake_currency'])}",
                    f"{period['fiat_value']:.2f} {stats['fiat_display_currency']}",
                    f"{format_pct(period['rel_profit'])}",
                ]
                for period in stats["data"]
            ],
            headers=[
                f"{val.header} (count)",
                f"{stake_cur}",
                f"{fiat_disp_cur}",
                "Profit %",
                "Trades",
            ],
            tablefmt="simple",
        )
        message = (
            f"<b>{val.message} Profit over the last {timescale} {val.message2}</b>:\n"
            f"<pre>{stats_tab}</pre>"
        )
        await self._send_msg(
            message,
            parse_mode=ParseMode.HTML,
            reload_able=True,
            callback_path=val.callback,
            query=update.callback_query,
        )

    @authorized_only
    async def _daily(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /daily <n>
        Returns a daily profit (in BTC) over the last n days.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "days")

    @staticmethod
    def _smc_parse_args(
        args: list[str], whitelist: list[str], stake: str, default_tfs: Sequence[str] | None = None
    ):
        """Tách `/smc [pair] [tf ...]` -> (pair, danh sách khung không trùng)."""
        tf_re = re.compile(r"^\d+[mhdwM]$")
        pair: str | None = None
        tfs: list[str] = []
        for a in args or []:
            if tf_re.match(a):
                tfs.append(a)
            elif pair is None:
                pair = a.upper()
        if pair is None:
            pair = whitelist[0] if whitelist else f"BTC/{stake}"
        if "/" not in pair:
            pair = f"{pair}/{stake}"
        if not tfs:
            tfs = list(default_tfs or ["15m", "1h", "4h", "1d"])
        # dict.fromkeys: khử trùng lặp NHƯNG giữ nguyên thứ tự khung user gõ.
        return pair, list(dict.fromkeys(tfs))

    @staticmethod
    def _smc_indicator_line(r, price: float, val) -> str:
        """Dòng chỉ báo THAM KHẢO (RSI/EMA/MA/MACD) cho 1 khung.

        Chỉ hiển thị, không ảnh hưởng tín hiệu vào lệnh. Trả "" nếu strategy
        chưa có các cột chỉ báo (vd bot chạy bản cũ chưa reload).
        """
        rsi = val(r.get("rsi"))
        if rsi is None:
            return ""
        parts = []
        # RSI: 🟢 quá bán (<=30), 🔴 quá mua (>=70), ⚪ trung tính.
        r_emoji = "🟢" if rsi <= 30 else "🔴" if rsi >= 70 else "⚪"
        parts.append(f"RSI {rsi:.0f}{r_emoji}")
        # EMA: giá so với EMA50 và EMA200 (xu hướng).
        e50, e200 = val(r.get("ema50")), val(r.get("ema200"))
        if e50 is not None and e200 is not None:
            l50 = "🟢" if price >= e50 else "🔴"
            l200 = "🟢" if price >= e200 else "🔴"
            parts.append(f"EMA {l50}{l200}")
        # MA20: giá so với SMA20.
        sma = val(r.get("sma20"))
        if sma is not None:
            parts.append(f"MA20 {'🟢' if price >= sma else '🔴'}")
        # MACD: đường MACD so với signal.
        macd, sigl = val(r.get("macd")), val(r.get("macdsignal"))
        if macd is not None and sigl is not None:
            parts.append(f"MACD {'🟢' if macd >= sigl else '🔴'}")
        return " · ".join(parts)

    @staticmethod
    def _smc_drop_unclosed(df, tf: str):
        """Bỏ nến đang hình thành -> chỉ phân tích trên NẾN ĐÃ ĐÓNG gần nhất."""
        import time

        from freqtrade.exchange import timeframe_to_seconds

        if df is None or len(df) == 0 or "date" not in df.columns:
            return df
        last_open = df["date"].iloc[-1].timestamp()
        if last_open + timeframe_to_seconds(tf) > time.time():
            return df.iloc[:-1]
        return df

    def _smc_live_price(self, ft, pair: str):
        """Giá khớp gần nhất từ ticker CÔNG KHAI (không cần API key)."""
        try:
            t = ft.exchange.fetch_ticker(pair)
            v = t.get("last") or t.get("close")
            return float(v) if v else None
        except Exception as e:
            logger.warning(f"/smc ticker {pair}: {type(e).__name__}: {e}")
            return None

    def _smc_tf_line(self, ft, pair: str, tf: str, candle_type):
        """Phân tích 1 khung trên nến đóng gần nhất -> (dòng, điểm, giá, levels).

        Luôn fetch OHLCV mới tại thời điểm gọi để dùng nến gần nhất của khung
        (tránh dataframe cũ của bot); lỗi thì lùi về dataframe đã phân tích.
        Entry & vị trí (discount/premium, in-OB) tính theo GIÁ ĐÓNG của nến đã
        đóng — KHÔNG theo giá hiện tại (giá live chỉ hiển thị ở tiêu đề).
        """
        import time

        from freqtrade.exchange import timeframe_to_seconds

        def val(v):
            return None if v is None or (isinstance(v, float) and v != v) else v

        def lab(b):
            b = val(b) or 0
            return "🟢" if b > 0 else "🔴" if b < 0 else "⚪"

        try:
            need = (ft.strategy.startup_candle_count or 300) + 50
            since_ms = int((time.time() - need * timeframe_to_seconds(tf)) * 1000)
            df = ft.exchange.get_historic_ohlcv(
                pair=pair, timeframe=tf, since_ms=since_ms, candle_type=candle_type
            )
            if df is not None and len(df):
                df = self._smc_drop_unclosed(df, tf)
                meta = {"pair": pair}
                df = ft.strategy.populate_entry_trend(
                    ft.strategy.populate_indicators(df, meta), meta
                )
            else:
                # Fallback: dataframe đã phân tích của bot (có thể cũ hơn).
                df, _ = ft.dataprovider.get_analyzed_dataframe(pair, tf)
            if df is None or len(df) == 0:
                return f"`{tf:>3}` ⚠️ chưa có dữ liệu", None, None, {}
        except Exception as e:
            logger.warning(f"/smc {pair} {tf}: {type(e).__name__}: {e}")
            return f"`{tf:>3}` ⚠️ lỗi ({type(e).__name__})", None, None, {}

        r = df.iloc[-1]
        # Entry & vị trí tính theo NẾN ĐÓNG (không theo giá hiện tại).
        price = float(r["close"])
        sw = val(r.get("swing_trend")) or 0
        zone = ""
        sh, sl = val(r.get("swing_high")), val(r.get("swing_low"))
        if sh and sl:
            zone = " · DISCOUNT" if price < (sh + sl) / 2 else " · PREMIUM"
        bt, bb = val(r.get("bull_ob_top")), val(r.get("bull_ob_bot"))
        in_ob = " · trong Bull OB" if (bt and bb and bb <= price <= bt) else ""
        sig = " ✅LONG" if val(r.get("enter_long")) else ""
        line = f"`{tf:>3}` Swing {lab(sw)} Internal {lab(r.get('internal_trend'))}"
        line += f"{zone}{in_ob}{sig}"
        ind = self._smc_indicator_line(r, price, val)
        if ind:
            line += f"\n     {ind}"
        # Các mức SMC để dựng kế hoạch Entry-Target.
        levels = {
            "internal_trend": val(r.get("internal_trend")) or 0,
            "swing_trend": sw,
            "enter_long": bool(val(r.get("enter_long"))),
            "enter_short": bool(val(r.get("enter_short"))),
            "bull_ob_top": bt,
            "bull_ob_bot": bb,
            "sw_bull_ob_top": val(r.get("sw_bull_ob_top")),
            "sw_bull_ob_bot": val(r.get("sw_bull_ob_bot")),
            "fvg_top": val(r.get("fvg_top")),
            "fvg_bot": val(r.get("fvg_bot")),
            "swing_high": sh,
            "swing_low": sl,
            # ATR dùng dựng dải entry khi không có OB/FVG nào (thay vì trả một điểm).
            "atr": val(r.get("atr")),
            "equilibrium": val(r.get("equilibrium")),
            "premium_level": val(r.get("premium_level")),
            "bear_ob_top": val(r.get("bear_ob_top")),
            "bear_ob_bot": val(r.get("bear_ob_bot")),
            # Volume Profile (smcv2.md mục F) — POC là magnet giá, VAH/VAL là biên
            # value area 70%. Dùng làm mốc chốt lời có cơ sở khối lượng thật.
            "vp_poc": val(r.get("vp_poc")),
            "vp_vah": val(r.get("vp_vah")),
            "vp_val": val(r.get("vp_val")),
            # Chuỗi pivot gần nhất để ước lượng sóng Elliott.
            "pivots": self._smc_extract_pivots(df),
            # Chỉ báo tham khảo (cho phần Bằng chứng).
            "rsi": val(r.get("rsi")),
            "ema50": val(r.get("ema50")),
            "ema200": val(r.get("ema200")),
            "sma20": val(r.get("sma20")),
            "macd": val(r.get("macd")),
            "macdsignal": val(r.get("macdsignal")),
        }
        return line, (1 if sw > 0 else -1 if sw < 0 else 0), price, levels

    @staticmethod
    def _smc_extract_pivots(df, n: int = 7) -> list[dict]:
        """Trích chuỗi pivot swing (H/L) gần nhất từ cột swing_high/swing_low.

        Mỗi lần giá trị forward-fill đổi = một pivot mới được xác nhận.
        """
        piv: list[tuple[int, str, float]] = []
        for col, typ in (("swing_high", "H"), ("swing_low", "L")):
            if col not in df.columns:
                continue
            prev = None
            for pos, v in enumerate(df[col].to_numpy()):
                if v is not None and v == v and v != prev:  # v==v: loại NaN
                    piv.append((pos, typ, float(v)))
                    prev = v
        piv.sort(key=lambda x: x[0])
        return [{"type": t, "price": p} for _, t, p in piv[-n:]]

    @classmethod
    def _smc_wave_estimate(cls, pivots, swing_trend) -> str | None:
        """Ước lượng vị trí sóng Elliott từ số chân sóng sau pivot cực trị.

        Heuristic: đếm số chân sóng kể từ đáy (uptrend) / đỉnh (downtrend) gần
        nhất. 1-5 = sóng đẩy (lẻ=đẩy, chẵn=hồi); >5 = điều chỉnh A-B-C.
        """
        if not pivots or len(pivots) < 2 or not swing_trend:
            return None
        up = swing_trend > 0
        prices = [p["price"] for p in pivots]
        base_i = prices.index(min(prices)) if up else prices.index(max(prices))
        legs = len(pivots) - 1 - base_i
        if legs <= 0:
            return None
        trend = "TĂNG" if up else "GIẢM"
        if legs <= 5:
            kind = "đẩy" if legs % 2 == 1 else "hồi"
            extra = " — sóng đẩy cuối, cảnh giác đảo chiều" if legs == 5 else ""
            return f"ước lượng đang ở SÓNG {legs} ({kind}) của nhịp {trend}{extra}."
        abc = ("A", "B", "C")[min(legs - 6, 2)]
        return f"đã qua 5 sóng đẩy {trend} — ước lượng đang ở SÓNG {abc} của điều chỉnh A-B-C."

    @staticmethod
    def _smc_fmt_price(x: float) -> str:
        """Định dạng giá theo độ lớn (tránh ký hiệu khoa học cho coin giá cao/thấp)."""
        ax = abs(x)
        if ax >= 1000:
            return f"{x:,.0f}"
        if ax >= 1:
            return f"{x:.2f}"
        if ax >= 0.01:
            return f"{x:.4f}"
        return f"{x:.8f}".rstrip("0").rstrip(".")

    @classmethod
    def _smc_entry_zone(
        cls, lv: dict, price: float, direction: str = "long"
    ) -> tuple[float, float, str, float | None]:
        """Chọn VÙNG entry — luôn trả một KHOẢNG a-b, không bao giờ một điểm.

        LONG: Bull OB nội bộ → Swing OB → FVG → dải quanh giá.
        SHORT: Bear OB → dải quanh giá.
        Khi không có mốc cấu trúc nào, dựng dải ±0.25 ATR quanh giá thay vì trả
        "thị trường" — vào lệnh cần một khoảng để đặt limit, không phải một điểm.
        :return: (lo, hi, nhãn vùng, mức vô hiệu hóa)
                 Mức vô hiệu hóa = biên OB phía rủi ro (đáy OB cho LONG, đỉnh OB
                 cho SHORT) — nơi đặt SL; None khi chỉ là dải quanh giá.
        """
        if direction == "long":
            zones = [
                ("Bull OB", lv.get("bull_ob_bot"), lv.get("bull_ob_top")),
                ("Swing OB", lv.get("sw_bull_ob_bot"), lv.get("sw_bull_ob_top")),
                ("FVG", lv.get("fvg_bot"), lv.get("fvg_top")),
            ]
        else:
            # Chỉ có Bear OB: strategy chưa xuất swing-bear-OB và FVG giảm.
            zones = [("Bear OB", lv.get("bear_ob_bot"), lv.get("bear_ob_top"))]
        for name, bot, top in zones:
            if bot and top and bot <= top:
                inval = bot if direction == "long" else top
                return bot, top, name, inval

        atr = lv.get("atr")
        if atr and atr > 0:
            half, note = 0.25 * atr, "dải ±0.25 ATR quanh giá TT"
        else:
            half, note = 0.0015 * price, "dải ±0.15% quanh giá TT"
        return price - half, price + half, note, None

    @staticmethod
    def _smc_price_vs_zone(cur: float, lo: float, hi: float) -> str:
        """Vị trí giá hiện tại so với vùng entry — vào được ngay hay còn phải chờ."""
        if lo <= cur <= hi:
            return "✅ ĐANG trong vùng entry"
        if cur > hi:
            return f"trên vùng {(cur / hi - 1) * 100:.1f}% — chờ giá hồi xuống"
        return f"dưới vùng {(lo / cur - 1) * 100:.1f}% — chờ giá hồi lên"

    @classmethod
    def _smc_break_note(cls, direction: str, price, live_price, stop, lv: dict) -> str | None:
        """Phân biệt QUÉT thanh khoản (sweep/wick) vs GÃY cấu trúc (nến đóng).

        SMC: chỉ coi setup mất hiệu lực khi NẾN ĐÓNG vượt SL. Giá live/wick vượt
        SL rồi về lại OB là sweep -> setup VẪN hợp lệ (thường là điểm vào đẹp).
        :param price: giá ĐÓNG nến gần nhất; :param live_price: giá khớp hiện tại.
        """
        if not stop:
            return None
        long = direction == "long"
        fmt = cls._smc_fmt_price
        closed_break = price <= stop if long else price >= stop
        live_poke = bool(live_price) and (live_price <= stop if long else live_price >= stop)

        if closed_break:
            if long:
                return (
                    f"❌ *Nến đã ĐÓNG dưới SL {fmt(stop)}* — cấu trúc tăng GÃY (BOS giảm).\n"
                    "👉 Nhận xét: setup LONG mất hiệu lực, nghiêng GIẢM — đứng ngoài / canh SHORT."
                )
            return (
                f"❌ *Nến đã ĐÓNG trên SL {fmt(stop)}* — cấu trúc giảm GÃY (BOS tăng).\n"
                "👉 Nhận xét: setup SHORT mất hiệu lực, nghiêng TĂNG — đứng ngoài / canh LONG."
            )

        if not live_poke:
            return None

        # Live/wick vượt SL nhưng nến CHƯA đóng phá -> có thể là sweep thanh khoản.
        if long:
            bt, bb = lv.get("bull_ob_top"), lv.get("bull_ob_bot")
        else:
            bt, bb = lv.get("bear_ob_top"), lv.get("bear_ob_bot")
        reclaim = bool(bt and bb and bb <= live_price <= bt)
        tail = (
            " Giá đã về lại trong OB → khả năng SWEEP cao (bẫy giá); nếu nến ĐÓNG giữ "
            "trong OB thì setup VẪN HỢP LỆ — thường là điểm vào đẹp sau quét."
            if reclaim
            else " Chờ NẾN ĐÓNG: đóng phá SL = gãy; quét rồi đóng lại trong OB = sweep (hợp lệ)."
        )
        side = "dưới" if long else "trên"
        return (
            f"⚠️ *Giá live {fmt(live_price)} đang QUÉT {side} SL {fmt(stop)}* — "
            f"CHƯA xác nhận gãy (mới là wick).{tail}"
        )

    @staticmethod
    def _smc_trend_word(v) -> str:
        v = v or 0
        return "Tăng" if v > 0 else "Giảm" if v < 0 else "Đi ngang"

    @classmethod
    def _smc_liquidity_targets(
        cls, lv: dict, entry_ref: float, direction: str
    ) -> list[tuple[float, str]]:
        """Mốc chốt lời theo THANH KHOẢN / CẤU TRÚC — đúng chuẩn SMC.

        `docs/ict/ICT_M15_M5_Entry_Guide.txt:11-12` chỉ định TP tại Buy/Sell-side
        Liquidity kế tiếp; `docs/smcv2.md:164` nêu "vùng thanh khoản đối diện
        (đỉnh/đáy cũ, EQH/EQL)". Khác hẳn Fib extension: mỗi mốc ở đây là nơi có
        LÝ DO để giá phản ứng (lệnh chờ, cụm stop, mất cân bằng chưa lấp, khối
        lượng tích tụ) — không phải kết quả của một phép nhân dải swing.
        :return: [(giá, lý do)] — chỉ những mốc nằm PHÍA TRƯỚC theo hướng lệnh.
        """
        long = direction == "long"
        out: list[tuple[float, str]] = []

        def add(level, label: str) -> None:
            if not level or level <= 0:
                return
            if (level > entry_ref) if long else (level < entry_ref):
                out.append((float(level), label))

        # 1) Đỉnh/đáy cũ = BSL/SSL — mục tiêu SMC kinh điển (từ chuỗi pivot thật).
        want = "H" if long else "L"
        for p in lv.get("pivots") or []:
            if p.get("type") == want:
                add(p.get("price"), "đỉnh cũ (BSL)" if long else "đáy cũ (SSL)")

        # 2) OB ĐỐI DIỆN — nơi phe ngược chiều đặt lệnh chờ.
        if long:
            add(lv.get("bear_ob_bot"), "biên Bear OB (cung)")
        else:
            add(lv.get("bull_ob_top"), "biên Bull OB (cầu)")

        # 3) FVG chưa lấp — lấp đầy mất cân bằng (biên xa = lấp trọn).
        add(lv.get("fvg_top") if long else lv.get("fvg_bot"), "lấp trọn FVG")

        # 4) Volume Profile — POC là magnet, VAH/VAL là biên value area 70%.
        add(lv.get("vp_poc"), "POC (magnet khối lượng)")
        if long:
            add(lv.get("vp_vah"), "VAH (biên value area)")
        else:
            add(lv.get("vp_val"), "VAL (biên value area)")

        # 5) Equilibrium 50% dải swing — mốc cân bằng premium/discount.
        add(lv.get("equilibrium"), "equilibrium 50%")

        # 6) Đỉnh/đáy SWING của dải hiện tại — cũng là BSL/SSL thật, chỉ khác nguồn
        # với chuỗi pivot ở (1). Đây là mốc Fib 1.0 cũ: giữ lại vì nó là mức giá
        # CÓ THẬT trên biểu đồ, khác hẳn 1.272/1.618 vốn chỉ là phép nhân dải swing.
        add(
            lv.get("swing_high") if long else lv.get("swing_low"),
            "đỉnh swing (BSL)" if long else "đáy swing (SSL)",
        )
        return out

    @classmethod
    def _smc_tp_ladder(
        cls,
        lv: dict,
        entry_ref: float,
        entry_worst: float,
        rr_risk: float | None,
        direction: str,
        max_rows: int = 6,
        max_r: float = 8.0,
        max_pct: float = 0.25,
        min_r: float = 0.5,
    ) -> tuple[list[tuple[float, str]], list[tuple[float, str]], list[tuple[float, str]]]:
        """Thang TP CHỈ gồm mốc thanh khoản/cấu trúc CÓ THẬT trên biểu đồ.

        Đã bỏ hoàn toàn hai nguồn ngoại suy từng có ở đây: Fib extension
        1.272/1.618 và mốc tổng hợp "R:R 1:2". Không có lệnh chờ, cụm stop hay
        mất cân bằng nào nằm ở những mức đó — đưa vào thang chỉ khiến R:R trông
        đẹp hơn thực tế. Khi cấu trúc thật không với tới `min_rr`R thì kết luận
        đúng là BỎ LỆNH (`_smc_plan_notes`), không phải vẽ thêm mốc cho đủ.

        MỐC SO SÁNH LÀ `entry_worst`, KHÔNG phải `entry_ref`: mốc nằm giữa mép
        xấu nhất và tâm vùng trông như "phía trước" nếu đo từ tâm, nhưng khi khớp
        ở mép xấu nhất thì nó nằm SAU LƯNG — thực đo SOL/USDT ra TP1 `74.10` cho
        SHORT có mép bán 73.88, tức lệnh LỖ mà bảng vẫn ghi "0.0R" vì lấy trị
        tuyệt đối. Lọc theo mép xấu nhất thì mọi mốc còn lại chắc chắn có lời.

        SÀN `min_r`: mốc dưới `min_r`R không tương xứng với SL — chốt ở đó ăn vài
        phần mười R trong khi vẫn ôm rủi ro 1R. TRẦN `max_r` (hoặc `max_pct` khi
        chưa có SL): mốc xa hơn tuy vẫn đúng cấu trúc nhưng vô dụng để lập kế
        hoạch — thực đo BTC/USDT 4h ra đỉnh cũ ở +41.5% (13R). Cả hai nhóm bị
        loại đều KHÔNG bị giấu: trả về ở phần tử 2 & 3 để ghi chú nói rõ.
        :return: (thang TP [(giá, lý do)] sắp gần→xa, mốc bỏ vì quá xa,
                 mốc bỏ vì quá gần so với SL)
        """
        long = direction == "long"
        # entry_worst: mốc duy nhất đảm bảo "có lời" dù khớp ở đâu trong vùng.
        targets = list(cls._smc_liquidity_targets(lv, entry_worst, direction))

        # Trần khoảng cách. Đo bằng bội số R khi đã có SL, không thì bằng % giá.
        if rr_risk:
            over = lambda p: abs(p - entry_worst) / rr_risk > max_r  # noqa: E731
        else:
            over = lambda p: abs(p / entry_ref - 1) > max_pct  # noqa: E731
        dropped = sorted(
            (t for t in targets if over(t[0])),
            key=lambda x: abs(x[0] - entry_worst),
        )
        targets = [t for t in targets if not over(t[0])]

        # Sàn: chỉ áp được khi đã biết SL, vì "nhỏ" là nhỏ SO VỚI rủi ro.
        too_close: list[tuple[float, str]] = []
        if rr_risk:
            under = lambda p: abs(p - entry_worst) / rr_risk < min_r  # noqa: E731
            too_close = sorted(
                (t for t in targets if under(t[0])),
                key=lambda x: -abs(x[0] - entry_worst),
            )
            targets = [t for t in targets if not under(t[0])]

        # Sắp gần→xa rồi gộp mốc trùng (<0.15%): nhiều nguồn cùng trỏ một giá
        # = mốc MẠNH hơn, nên nối lý do lại thay vì hiện hai dòng.
        merged: list[tuple[float, str]] = []
        for t, name in sorted(targets, key=lambda x: x[0], reverse=not long):
            if merged and abs(t / merged[-1][0] - 1) < 0.0015:
                merged[-1] = (merged[-1][0], f"{merged[-1][1]} + {name}")
                continue
            merged.append((t, name))

        # Mốc cấu trúc XA NHẤT nằm cuối thang, nên cắt thô merged[:max_rows] sẽ
        # giấu mất nó — đúng mục tiêu tốt nhất, và là mốc quyết định R:R xa.
        far = merged[-1][0] if merged else None
        return cls._smc_trim_ladder(merged, max_rows, [far]), dropped, too_close

    @staticmethod
    def _smc_trim_ladder(
        merged: list[tuple[float, str]], max_rows: int, must: list[float | None]
    ) -> list[tuple[float, str]]:
        """Cắt thang TP xuống `max_rows` nhưng LUÔN giữ các mốc trong `must`.

        Phần còn lại lấy các mốc GẦN entry nhất (đã sắp gần→xa từ trước).
        """
        if len(merged) <= max_rows:
            return merged
        wanted = [m for m in must if m]
        keep = [
            i for i, (p, _) in enumerate(merged) if any(abs(p / m - 1) < 0.0015 for m in wanted)
        ]
        rest = [i for i in range(len(merged)) if i not in keep]
        chosen = set(keep[:max_rows]) | set(rest[: max(0, max_rows - len(keep))])
        return [merged[i] for i in sorted(chosen)]

    @classmethod
    def _smc_trade_decision(cls, strategy, lv: dict, price: float, direction: str | None) -> dict:
        """Quyết định giao dịch dạng DỮ LIỆU: vùng entry, SL, thang TP, và có vào lệnh hay không.

        Tách khỏi `_smc_plan_block` để báo cáo gửi đi và lệnh vào tự động
        (`_auto_entry_from_analysis`) dùng CHUNG một phép tính. Nếu hai bên tự tính riêng, sẽ
        có ngày tin nhắn ghi "BỎ LỆNH" mà bot vẫn vào lệnh — kiểu sai lệch đó không có gì
        trong log báo cho ai biết.

        `reject` khác None nghĩa là KHÔNG được vào lệnh, kèm lý do đọc được cho người dùng.

        :param strategy: strategy đang chạy (lấy `sl_buffer_pct`)
        :param lv: các mốc SMC của khung dựng kế hoạch
        :param price: giá nến đóng của khung đó
        :param direction: "long" | "short" | None (các khung xung đột)
        :return: dict quyết định; `reject=None` là được phép vào lệnh
        """
        min_rr = 2.0
        base_out: dict = {
            "min_rr": min_rr,
            "direction": direction,
            "lo": 0.0,
            "hi": 0.0,
            "entry_note": "",
            "inval": None,
            "entry_ref": 0.0,
            "entry_worst": 0.0,
            "stop": None,
            "sl_note": "",
            "rr_risk": None,
            "targets": [],
            "dropped": [],
            "too_close": [],
            "rr_first": None,
            "reject": None,
        }
        if not direction or not price:
            return {**base_out, "reject": "các khung xung đột / thiếu dữ liệu"}

        long = direction == "long"
        lo, hi, entry_note, inval = cls._smc_entry_zone(lv, price, direction)
        entry_ref = (lo + hi) / 2
        # Mép xấu nhất: LONG mua ở đỉnh vùng, SHORT bán ở đáy vùng.
        entry_worst = hi if long else lo

        try:
            buf = float(strategy.sl_buffer_pct.value)
        except Exception:
            buf = 0.5

        # SL đặt ngay ngoài BIÊN OB entry (vô hiệu hóa setup), KHÔNG theo đỉnh/đáy
        # swing xa -> tránh SL quá dài. Fallback swing khi chỉ có dải quanh giá.
        rr_risk = None
        stop = None
        base = inval if inval else lv.get("swing_low" if long else "swing_high")
        sl_note = (
            ("đáy OB" if inval else "swing low") if long else ("đỉnh OB" if inval else "swing high")
        )
        if base and base > 0:
            stop = base * (1 - buf / 100) if long else base * (1 + buf / 100)
            risk = entry_worst - stop if long else stop - entry_worst
            if risk > 0:
                rr_risk = risk

        merged, dropped, too_close = cls._smc_tp_ladder(
            lv, entry_ref, entry_worst, rr_risk, direction
        )
        # Mốc đầu tiên ĐẠT chuẩn R:R, tính trên giá thật thay vì mốc min_rr tổng hợp.
        hit = None
        if rr_risk:
            hit = next(
                (
                    i
                    for i, (t, _) in enumerate(merged, 1)
                    if abs(t - entry_worst) / rr_risk >= min_rr
                ),
                None,
            )

        # Thứ tự kiểm tra = thứ tự nghiêm trọng. Không SL đứng trước mọi thứ: kế hoạch không
        # có điểm dừng lỗ thì không phải kế hoạch, dù thang TP có đẹp tới đâu.
        reject = None
        if rr_risk is None:
            reject = "không dựng được SL từ cấu trúc (không có biên OB / swing hợp lệ)"
        elif not merged:
            reject = "không có mốc thanh khoản/cấu trúc nào phía trước để làm TP"
        elif hit is None:
            reject = (
                f"mốc xa nhất chỉ đạt 1:{abs(merged[-1][0] - entry_worst) / rr_risk:.1f}, "
                f"dưới ngưỡng 1:{min_rr:.0f}"
            )

        return {
            **base_out,
            "lo": lo,
            "hi": hi,
            "entry_note": entry_note,
            "inval": inval,
            "entry_ref": entry_ref,
            "entry_worst": entry_worst,
            "stop": stop,
            "sl_note": sl_note,
            "rr_risk": rr_risk,
            "targets": merged,
            "dropped": dropped,
            "too_close": too_close,
            "rr_first": (abs(merged[0][0] - entry_worst) / rr_risk)
            if (merged and rr_risk)
            else None,
            "reject": reject,
        }

    @classmethod
    def _smc_plan_block(
        cls, strategy, tf: str, lv: dict, price: float, direction: str, live_price=None
    ) -> list[str]:
        """Khối 'Kịch bản chính' (mục 6): bảng Giá TT/Entry/SL/TP/R:R canh cột
        (monospace) + trigger + điểm vô hiệu hóa + nhận xét sweep/gãy.

        Mọi phép tính rủi ro lấy ở MÉP XẤU NHẤT của vùng entry (mua đắt nhất cho
        LONG / bán rẻ nhất cho SHORT), nên tỉ lệ công bố vẫn đúng dù lệnh khớp ở
        bất kỳ đâu trong vùng.

        R:R công bố là số ĐO ĐƯỢC trên mốc cấu trúc thật, KHÔNG phải ngưỡng
        `min_rr` mặc định — thang TP không còn mốc tổng hợp nào để bấu víu. Cấu
        trúc không với tới `min_rr` → `_smc_plan_notes` đề nghị BỎ LỆNH.
        """
        long = direction == "long"
        fmt = cls._smc_fmt_price

        d = cls._smc_trade_decision(strategy, lv, price, direction)
        min_rr = d["min_rr"]
        lo, hi, entry_note = d["lo"], d["hi"], d["entry_note"]
        entry_ref, entry_worst = d["entry_ref"], d["entry_worst"]
        stop, rr_risk, sl_note = d["stop"], d["rr_risk"], d["sl_note"]
        merged, dropped, too_close = d["targets"], d["dropped"], d["too_close"]

        def pct(level: float) -> str:
            # % so với ENTRY (khoảng cách SL/TP từ điểm vào) — đúng hướng lời/lỗ.
            return f"{(level / entry_ref - 1) * 100:+.1f}%"

        cur = live_price or price
        rows = [
            ("Giá TT", fmt(cur), cls._smc_price_vs_zone(cur, lo, hi)),
            ("Entry", f"{fmt(lo)}-{fmt(hi)}", entry_note),
        ]
        if stop:
            rows.append(("SL", fmt(stop), f"{pct(stop)} · {'dưới' if long else 'trên'} {sl_note}"))
        else:
            rows.append(("SL", "n/a", "chưa có mốc cấu trúc"))
        for i, (t, name) in enumerate(merged, 1):
            mult = f" · {abs(t - entry_worst) / rr_risk:.1f}R" if rr_risk else ""
            rows.append((f"TP{i}", fmt(t), f"{pct(t)} · {name}{mult}"))
        if not merged:
            rows.append(("TP", "n/a", "chưa có mốc thanh khoản/cấu trúc nào phía trước"))

        # R:R đo trên mốc THẬT gần nhất và xa nhất — không có số mặc định 1:2.
        if rr_risk and merged:
            rows.append(
                (
                    "R:R TP1",
                    f"1 : {abs(merged[0][0] - entry_worst) / rr_risk:.1f}",
                    f"tính ở mép {fmt(entry_worst)}",
                )
            )
            rows.append(
                (
                    "R:R xa",
                    f"1 : {abs(merged[-1][0] - entry_worst) / rr_risk:.1f}",
                    f"nếu giữ tới TP{len(merged)}",
                )
            )

        # Bảng canh cột trong block monospace.
        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        table = [f"{a.ljust(w1)}  {b.rjust(w2)}  {c}".rstrip() for a, b, c in rows]

        out = [f"🎯 *KỊCH BẢN CHÍNH: {'LONG' if long else 'SHORT'}* (`{tf}`)", "```", *table, "```"]
        out += cls._smc_plan_notes(
            long, fmt, lo, hi, stop, rr_risk, merged, entry_worst, min_rr, dropped, too_close
        )
        note = cls._smc_break_note(direction, price, live_price, stop, lv)
        if note:
            out.insert(1, note)
        return out

    @classmethod
    def _smc_plan_notes(
        cls, long, fmt, lo, hi, stop, rr_risk, merged, entry_worst, min_rr, dropped, too_close
    ) -> list[str]:
        """Các dòng ghi chú dưới bảng: cách đặt lệnh, trigger, kỷ luật chốt lời,
        mốc bị bỏ vì quá xa, và phán quyết BỎ LỆNH khi cấu trúc không với tới
        `min_rr`.

        Mọi mốc trong `merged` đều có thanh khoản/cấu trúc đỡ nên không còn dòng
        chú thích phân loại mốc mạnh/yếu — cái gì hiện trong bảng là mục tiêu
        thật, cái gì không đủ tiêu chuẩn thì không được hiện.
        """
        out = [
            f"• Đặt lệnh: limit trong vùng `{fmt(lo)}-{fmt(hi)}`, KHÔNG đuổi giá ngoài vùng.",
            "• Trigger: chờ phản ứng giá / CHoCH khung 15m-1H tại vùng Entry.",
        ]
        # Mốc đầu tiên ĐẠT chuẩn R:R, tính trên giá thật thay vì mốc min_rr tổng hợp.
        hit = None
        if rr_risk:
            hit = next(
                (
                    i
                    for i, (t, _) in enumerate(merged, 1)
                    if abs(t - entry_worst) / rr_risk >= min_rr
                ),
                None,
            )
        if hit:
            out.append(
                f"• Chốt lời: TP{hit} là mốc thật đầu tiên đạt 1:{min_rr:.0f} — giữ tối thiểu tới "
                f"đó; các mốc trước nó chỉ chốt MỘT PHẦN."
            )
        # Cắt bớt phải NÓI RA: mốc bị bỏ vẫn là mốc thật, chỉ là quá xa để dùng.
        if dropped:
            near = dropped[0]
            extra = f" (gần nhất: `{fmt(near[0])}` — {near[1]}"
            extra += f", {abs(near[0] - entry_worst) / rr_risk:.1f}R)" if rr_risk else ")"
            out.append(f"• Đã bỏ {len(dropped)} mốc quá xa để lập kế hoạch{extra}.")
        # Mốc quá gần cũng phải nói ra, nếu không bảng trông như "chỉ có bấy nhiêu".
        if too_close and rr_risk:
            far = too_close[0]
            out.append(
                f"• Đã bỏ {len(too_close)} mốc quá gần, không tương xứng với SL "
                f"(xa nhất trong nhóm: `{fmt(far[0])}` — {far[1]}, "
                f"{abs(far[0] - entry_worst) / rr_risk:.1f}R)."
            )
        # Cấu trúc không với tới min_rr -> BỎ LỆNH. Rủi ro lớn + lời nhỏ là setup
        # phải từ chối, không phải setup cần thêm mốc ngoại suy cho R:R đẹp.
        #
        # Nhánh "không có SL" phải đứng đầu và phải NÓI RA: trước đây bảng chỉ ghi "SL n/a"
        # rồi im lặng, đọc như một kế hoạch bình thường thiếu một ô. Kế hoạch không có điểm
        # dừng lỗ thì không phải kế hoạch — và `_smc_trade_decision` cũng từ chối ở đúng
        # điều kiện này, nên báo cáo phải nói cùng một điều.
        if not rr_risk:
            out.append(
                "❌ *BỎ LỆNH:* không dựng được SL từ cấu trúc (không có biên OB / swing hợp lệ) "
                "— không có điểm dừng lỗ thì không có kế hoạch."
            )
        elif rr_risk and not merged:
            out.append(
                f"❌ *BỎ LỆNH:* không có mốc thanh khoản/cấu trúc nào phía trước để làm TP — "
                f"không có cách nào biết lời tới đâu, trong khi SL đã mất `{fmt(rr_risk)}`."
            )
        elif rr_risk and hit is None:
            far_r = abs(merged[-1][0] - entry_worst) / rr_risk
            out.append(
                f"❌ *BỎ LỆNH:* mốc cấu trúc xa nhất chỉ đạt `1 : {far_r:.1f}`, dưới ngưỡng "
                f"1:{min_rr:.0f} — rủi ro lớn mà lợi nhuận nhỏ. Chỉ vào lệnh nếu siết được SL "
                "sát biên OB hơn để tỉ lệ đạt chuẩn."
            )
        if stop:
            out.append(
                f"• Vô hiệu hóa: nến ĐÓNG qua `{fmt(stop)}` → cấu trúc "
                f"{'tăng' if long else 'giảm'} gãy."
            )
        return out

    @classmethod
    def _smc_sec_bias(cls, htf, avail, sw, bull, bear, lv_by) -> list[str]:
        """Mục 1 — Bức tranh đa khung (HTF bias)."""
        word = cls._smc_trend_word
        n = len(avail)
        mtf = " ".join(f"{t}{'🟢' if sw[t] > 0 else '🔴' if sw[t] < 0 else '⚪'}" for t in avail)
        if bull > bear:
            concl = f"đồng thuận TĂNG ({bull}/{n}) → ưu tiên LONG thuận xu hướng"
        elif bear > bull:
            concl = f"đồng thuận GIẢM ({bear}/{n}) → ưu tiên SHORT thuận xu hướng"
        else:
            concl = "xung đột giữa các khung → đứng ngoài, chờ rõ"
        out = [
            "",
            "*1️⃣ Bức tranh đa khung (HTF Bias)*",
            f"• Chính ({htf}): {word(lv_by[htf].get('swing_trend'))}",
        ]
        if "4h" in avail:
            out.append(
                f"• Phụ (4H): swing {word(lv_by['4h'].get('swing_trend'))}, "
                f"internal {word(lv_by['4h'].get('internal_trend'))}"
            )
        out.append(f"• MTF: {mtf} — {concl}")
        return out

    @classmethod
    def _smc_sec_structure(cls, stf, lv) -> list[str]:
        """Mục 2 — Cấu trúc & SMC (OB / FVG)."""
        fmt = cls._smc_fmt_price
        word = cls._smc_trend_word
        out = [
            "",
            f"*2️⃣ Cấu trúc & SMC* (`{stf}`)",
            f"• Cấu trúc: Internal {word(lv.get('internal_trend'))} · "
            f"Swing {word(lv.get('swing_trend'))}",
        ]
        a, b = lv.get("bear_ob_bot"), lv.get("bear_ob_top")
        if a and b:
            out.append(f"• Vùng Cung (Bear OB): `{fmt(a)}-{fmt(b)}`")
        c, d = lv.get("bull_ob_bot"), lv.get("bull_ob_top")
        if c and d:
            out.append(f"• Vùng Cầu (Bull OB): `{fmt(c)}-{fmt(d)}`")
        e, f = lv.get("fvg_bot"), lv.get("fvg_top")
        if e and f:
            out.append(f"• FVG (imbalance): `{fmt(e)}-{fmt(f)}`")
        return out

    @classmethod
    def _smc_sec_liquidity(cls, lv, live_price) -> list[str]:
        """Mục 3 — Thanh khoản (BSL/SSL) & nhận định sweep."""
        fmt = cls._smc_fmt_price
        bsl, ssl = lv.get("swing_high"), lv.get("swing_low")
        out = ["", "*3️⃣ Thanh khoản & Mức quan trọng*"]
        if bsl:
            out.append(f"• BSL (đỉnh cũ/EQH): `{fmt(bsl)}` — nơi phe Short đặt SL")
        if ssl:
            out.append(f"• SSL (đáy cũ/EQL): `{fmt(ssl)}` — nơi phe Long đặt SL")
        if live_price and ssl and live_price < ssl:
            note = f"giá đã quét SSL (đáy `{fmt(ssl)}`) → canh phản ứng tăng (sweep)."
        elif live_price and bsl and live_price > bsl:
            note = f"giá đã quét BSL (đỉnh `{fmt(bsl)}`) → canh phản ứng giảm (sweep)."
        else:
            note = "chưa quét đỉnh/đáy gần nhất; giá trong range."
        out.append(f"• Sweep: {note}")
        return out

    @classmethod
    def _smc_fib_line(cls, lv, direction) -> str | None:
        """Golden pocket Fib 0.618-0.786 từ swing high/low, theo hướng vào lệnh.

        LONG: thoái lui từ đỉnh (discount, mua). SHORT: từ đáy (premium, bán).
        """
        sh, sl = lv.get("swing_high"), lv.get("swing_low")
        if not (sh and sl and sh > sl):
            return None
        fmt = cls._smc_fmt_price
        rng = sh - sl
        if direction == "short":
            f618, f786, zone = sl + 0.618 * rng, sl + 0.786 * rng, "premium (bán)"
        else:
            f618, f786, zone = sh - 0.618 * rng, sh - 0.786 * rng, "discount (mua)"
        lo, hi = sorted((f618, f786))
        return f"• Fib golden pocket: `{fmt(lo)}-{fmt(hi)}` (0.618-0.786, {zone})"

    @classmethod
    def _smc_elliott_note(cls, lv, cp) -> list[str]:
        """Ước lượng bối cảnh sóng Elliott (heuristic từ trend + vị trí giá).

        KHÔNG phải đếm sóng chính xác — chỉ gợi ý phase, cần xác nhận thủ công.
        """
        sw = lv.get("swing_trend") or 0
        it = lv.get("internal_trend") or 0
        eq = lv.get("equilibrium")
        in_disc = (cp < eq) if (cp and eq) else None
        if sw > 0:  # xu hướng tăng
            if it < 0 or in_disc:
                guess = (
                    "Hồi quy trong xu hướng TĂNG — khả năng đang ở sóng điều chỉnh "
                    "(2 hoặc 4 / ABC), chờ kết thúc để vào sóng đẩy tăng tiếp."
                )
            else:
                guess = "Đang trong sóng ĐẨY TĂNG (impulse 3/5) — thuận đà."
        elif sw < 0:  # xu hướng giảm
            if it > 0 or (in_disc is False):
                guess = (
                    "Hồi quy trong xu hướng GIẢM — khả năng sóng điều chỉnh, "
                    "chờ kết thúc để vào sóng đẩy giảm tiếp."
                )
            else:
                guess = "Đang trong sóng ĐẨY GIẢM (impulse) — thuận đà."
        else:
            guess = "Đi ngang — có thể sóng điều chỉnh phẳng/tam giác, chờ phá vỡ."
        out = ["", "*4️⃣ Sóng Elliott* (ước lượng)"]
        wave = cls._smc_wave_estimate(lv.get("pivots") or [], sw)
        if wave:
            out.append(f"• Vị trí: {wave}")
        out.append(f"• Bối cảnh: {guess}")
        out.append("• _Heuristic từ pivot/cấu trúc — cần xác nhận đếm sóng thủ công._")
        return out

    @classmethod
    def _smc_sec_confluence(cls, stf, lv, cp, direction) -> list[str]:
        """Mục 5 — Hợp lưu kỹ thuật (RSI / EMA / MACD / Fib)."""
        out = ["", f"*5️⃣ Hợp lưu kỹ thuật* (`{stf}`)"]
        rsi = lv.get("rsi")
        if rsi is not None:
            st = "quá bán <30" if rsi <= 30 else "quá mua >70" if rsi >= 70 else "trung tính"
            out.append(f"• RSI: {rsi:.0f} — {st}")
        e200, e50 = lv.get("ema200"), lv.get("ema50")
        if cp and e200:
            s = f"giá {'trên' if cp >= e200 else 'dưới'} EMA200"
            if e50:
                s += f", {'trên' if cp >= e50 else 'dưới'} EMA50"
            out.append(f"• EMA: {s}")
        macd, sig = lv.get("macd"), lv.get("macdsignal")
        if macd is not None and sig is not None:
            out.append(f"• MACD: {'cắt lên (> signal)' if macd >= sig else 'cắt xuống (< signal)'}")
        fib = cls._smc_fib_line(lv, direction)
        out.append(fib if fib else "• Fib: chưa đủ swing để tính.")
        out.append("• Volume profile: chưa tính tự động.")
        return out

    @classmethod
    def _smc_sec_plan(cls, strategy, stf, lv, price, direction, live_price) -> list[str]:
        """Mục 6 — Kế hoạch giao dịch (kịch bản chính + phụ)."""
        fmt = cls._smc_fmt_price
        out = ["", "*6️⃣ Kế hoạch giao dịch*"]
        if not direction or not price:
            out.append(
                "⚪ Đứng ngoài — các khung xung đột / thiếu dữ liệu, chưa có kịch bản xác suất cao."
            )
            return out
        out += cls._smc_plan_block(strategy, stf, lv, price, direction, live_price)
        if direction == "long" and lv.get("swing_low"):
            out.append(
                f"🔄 *Kịch bản phụ*: nếu nến ĐÓNG dưới SSL `{fmt(lv['swing_low'])}` → hủy LONG, "
                "chờ retest để SHORT theo hướng phá vỡ."
            )
        elif direction == "short" and lv.get("swing_high"):
            out.append(
                f"🔄 *Kịch bản phụ*: nếu nến ĐÓNG trên BSL `{fmt(lv['swing_high'])}` → hủy SHORT, "
                "chờ retest để LONG theo hướng phá vỡ."
            )
        return out

    @classmethod
    def _smc_sec_risk(cls, stf, lv, direction) -> list[str]:
        """Mục 7 — Quản trị rủi ro & lưu ý."""
        out = ["", "*7️⃣ Quản trị rủi ro*", "• Risk/lệnh: tối đa 1-2% tài khoản."]
        it = lv.get("internal_trend") or 0
        if direction and it != 0 and (it > 0) != (direction == "long"):
            out.append(
                f"• Ghi chú: internal `{stf}` đang {cls._smc_trend_word(it).lower()} ngược bias "
                "→ đây là nhịp hồi; nếu vào lệnh ngược bias phải giảm nửa volume."
            )
        out.append(
            "⚠️ _Phân tích kỹ thuật dựa trên cấu trúc/thanh khoản/xác suất — không phải lời "
            "khuyên đầu tư (NFA). Tự chịu trách nhiệm và tuân thủ dừng lỗ._"
        )
        return out

    @classmethod
    def _smc_report_context(cls, strategy, tfs, levels_by_tf) -> tuple | None:
        """Khung dựng kế hoạch + hướng vào lệnh — dùng chung cho báo cáo và lệnh tự động.

        Tách ra vì `_auto_entry_from_analysis` phải quyết định trên ĐÚNG khung và ĐÚNG hướng
        mà báo cáo vừa gửi đi. Tính lại ở hai nơi là mở đường cho bot vào lệnh theo một khung
        khác với khung nó vừa báo.

        :return: (avail, htf, stf, direction, sw, bull, bear) hoặc None khi không có khung nào
        """
        avail = [t for t in tfs if t in levels_by_tf]
        if not avail:
            return None
        htf = next((t for t in ("1w", "1d") if t in avail), avail[-1])
        stf = (
            strategy.timeframe
            if strategy.timeframe in avail
            else ("4h" if "4h" in avail else avail[0])
        )
        sw = {t: (levels_by_tf[t].get("swing_trend") or 0) for t in avail}
        bull = sum(1 for v in sw.values() if v > 0)
        bear = sum(1 for v in sw.values() if v < 0)
        direction = "long" if bull > bear else "short" if bear > bull else None
        return avail, htf, stf, direction, sw, bull, bear

    @classmethod
    def _smc_signal_report(
        cls, strategy, pair, tfs, levels_by_tf, price_by_tf, live_price, now_str
    ) -> list[str]:
        """Ghép báo cáo 7 mục theo template-signal.md từ levels đa khung."""
        ctx = cls._smc_report_context(strategy, tfs, levels_by_tf)
        if not ctx:
            return [f"📊 *{pair}* — ⚠️ Không lấy được dữ liệu khung nào."]
        avail, htf, stf, direction, sw, bull, bear = ctx
        fmt = cls._smc_fmt_price
        lv = levels_by_tf[stf]
        cp = price_by_tf.get(stf) or (live_price or 0.0)
        pos = "LONG" if bull > bear else "SHORT" if bear > bull else "ĐỨNG NGOÀI"
        # Nhãn theo khung DỰNG KẾ HOẠCH, không theo khung bias: kế hoạch trên 5m là scalping
        # kể cả khi bias lấy từ 4h.
        if stf in ("4h", "1d", "1w"):
            setup = "Swing Trade"
        elif stf in ("1m", "3m", "5m"):
            setup = "Scalping"
        else:
            setup = "Day Trade"
        shown = live_price if live_price is not None else price_by_tf.get(stf)
        tag = "live" if live_price is not None else "nến đóng"

        out = [f"📊 *Phân Tích & Kế Hoạch: {pair}*"]
        if shown is not None:
            out.append(f"Giá: `{fmt(shown)}` ({tag}) | Cập nhật: `{now_str}`")
        out.append(f"Vị thế ưu tiên: *{pos}* | Loại: {setup}")
        out += cls._smc_sec_bias(htf, avail, sw, bull, bear, levels_by_tf)
        out += cls._smc_sec_structure(stf, lv)
        out += cls._smc_sec_liquidity(lv, live_price)
        out += cls._smc_elliott_note(lv, cp)
        out += cls._smc_sec_confluence(stf, lv, cp, direction)
        out += cls._smc_sec_plan(strategy, stf, lv, price_by_tf.get(stf, cp), direction, live_price)
        out += cls._smc_sec_risk(stf, lv, direction)
        return out

    @authorized_only
    async def _analysis(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /analysis [pair] [tf ...]
        Phân tích SMC ĐA KHUNG THỜI GIAN một cặp theo SmcElliottStrategy.
        - Giá hiển thị: ticker khớp mới nhất (công khai, không cần API key).
        - Mỗi khung: fetch OHLCV mới tại thời điểm gọi, bỏ nến đang hình thành,
          phân tích trên nến ĐÃ ĐÓNG gần nhất (tránh repaint & dataframe cũ).
        Ví dụ:  /analysis            -> BTC, khung mặc định 15m 1h 4h 1d
                /analysis ETH 4h 1d  -> ETH, chỉ 4h và 1d
        """
        ft = self._rpc._freqtrade
        pair, tfs = self._smc_parse_args(
            context.args or [], ft.active_pair_whitelist, ft.config["stake_currency"]
        )
        report = await self._build_analysis_report(ft, pair, tfs)
        await self._send_msg(report)

    @staticmethod
    def _smc_quick_pairs(whitelist: list[str], stake: str) -> list[str]:
        """4 cặp cố định của `/smc` (BTC, ETH, XAU, SOL), đúng format bot đang chạy.

        Cặp có trong whitelist thì lấy nguyên chuỗi — whitelist đã đúng
        `BASE/QUOTE:SETTLE` khi chạy futures. Cặp thiếu thì dựng theo hậu tố của
        cặp đầu whitelist; hardcode format spot ở đây sẽ sai khi bot chạy futures.
        """
        by_base = {p.split("/")[0].upper(): p for p in whitelist or []}
        sample = whitelist[0] if whitelist else ""
        suffix = sample.split("/", 1)[1] if "/" in sample else stake
        return [by_base.get(base, f"{base}/{suffix}") for base in SMC_QUICK_BASES]

    @staticmethod
    def _smc_quick_timeframes(ft) -> list[str]:
        """Khung mặc định của `/smc` — bám theo khung giao dịch của chính bot đang chạy.

        Trước đây cứng là `SMC_QUICK_TIMEFRAME` (4h), nên bot scalping 5m (`./run_smc_5.sh`)
        bấm /smc lại nhận kế hoạch swing 4h: một báo cáo không liên quan gì tới lệnh mà bot
        đó thực sự vào, và không có dấu hiệu nào cho thấy nó sai khung.

        Bot khung ngắn (< 1h) trả về cả bộ `SMC_SCALP_TIMEFRAMES` chứ không riêng khung của nó:
        `_smc_signal_report` lấy `avail[0]` dựng kế hoạch Entry/SL/TP và `avail[-1]` lấy bias,
        nên danh sách một phần tử sẽ lấy bias ngay trên khung vào lệnh — đúng thứ mà bias đa
        khung sinh ra để tránh.

        :param ft: FreqtradeBot đang chạy
        :return: danh sách khung, thứ tự thấp -> cao
        """
        from freqtrade.exchange import timeframe_to_minutes

        tf = getattr(ft.strategy, "timeframe", None) or ft.config.get("timeframe")
        # isinstance chứ không phải truthy: /help cũng gọi hàm này, và ở đó `ft` có thể là
        # mock (test) — `getattr` trên mock trả về mock, đưa thẳng vào timeframe_to_minutes
        # sẽ ném lỗi và giết luôn lệnh /help.
        if not isinstance(tf, str):
            return [SMC_QUICK_TIMEFRAME]
        return list(SMC_SCALP_TIMEFRAMES) if timeframe_to_minutes(tf) < 60 else [tf]

    async def _send_pair_reports(self, ft, pairs: list[str], tfs: list[str], header: str) -> None:
        """Gửi header rồi mỗi cặp một báo cáo.

        Lỗi một cặp (sàn không niêm yết, fetch hụt) không được chặn các cặp còn lại — và
        phải báo ra Telegram, im lặng ở đây đọc y như "cặp đó không có tín hiệu".
        """
        await self._send_msg(header)
        for pair in pairs:
            try:
                await self._send_msg(await self._build_analysis_report(ft, pair, tfs))
            except Exception as e:
                logger.warning("Báo cáo %s lỗi: %s: %s", pair, type(e).__name__, e)
                await self._send_msg(f"⚠️ `{pair}`: không dựng được báo cáo ({type(e).__name__}).")

    @authorized_only
    async def _smc(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /smc [pair] [tf ...]
        Không tham số -> báo cáo 4 cặp cố định (BTC, ETH, XAU, SOL) trên khung của chính bot
        đang chạy (`_smc_quick_timeframes`), mỗi cặp một tin nhắn.
        Có tham số -> hành xử y hệt /analysis.
        Ví dụ:  /smc            -> bot 4h: BTC, ETH, XAU, SOL trên 4h
                                   bot 5m: cùng 4 cặp trên 5m + 15m (giống /scalp)
                /smc ETH 4h 1d  -> ETH, chỉ 4h và 1d
        """
        if context.args:
            await self._analysis(update, context)
            return
        ft = self._rpc._freqtrade
        pairs = self._smc_quick_pairs(ft.active_pair_whitelist, ft.config["stake_currency"])
        tfs = self._smc_quick_timeframes(ft)
        await self._send_pair_reports(
            ft,
            pairs,
            tfs,
            f"⚡ *Phân tích nhanh {' + '.join(tfs)}* — {len(pairs)} cặp",
        )

    @authorized_only
    async def _scalp(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /scalp [pair] [tf ...]
        Bản khung ngắn của /smc: cùng 4 cặp nhưng chạy 5m + 15m để tìm lệnh trong ngày.
        Bias lấy từ 15m, kế hoạch Entry/SL/TP dựng trên 5m (xem SMC_SCALP_TIMEFRAMES).
        Ví dụ:  /scalp             -> BTC, ETH, XAU, SOL trên 5m + 15m
                /scalp SOL 5m 15m 1h -> SOL, thêm 1h làm khung bias
        """
        ft = self._rpc._freqtrade
        tfs = list(SMC_SCALP_TIMEFRAMES)
        if context.args:
            pair, tfs = self._smc_parse_args(
                context.args, ft.active_pair_whitelist, ft.config["stake_currency"], tfs
            )
            pairs = [pair]
        else:
            pairs = self._smc_quick_pairs(ft.active_pair_whitelist, ft.config["stake_currency"])
        await self._send_pair_reports(
            ft, pairs, tfs, f"⏱ *Scalping {'+'.join(tfs)}* — {len(pairs)} cặp"
        )

    async def _build_analysis_report(self, ft, pair: str, tfs: list[str]) -> str:
        """Dựng báo cáo /analysis (7 mục) cho 1 cặp — dùng chung cho lệnh & lịch."""
        text, _decision = await self._build_analysis(ft, pair, tfs)
        return text

    async def _build_analysis(self, ft, pair: str, tfs: list[str]) -> tuple[str, dict | None]:
        """Như `_build_analysis_report` nhưng trả kèm quyết định giao dịch có cấu trúc.

        Lịch /analysis dùng bản này để vào lệnh theo ĐÚNG kế hoạch vừa gửi đi
        (`_auto_entry_from_analysis`). Các lệnh gõ tay vẫn đi qua `_build_analysis_report`
        và không bao giờ tự vào lệnh — gõ /analysis để XEM thì không được biến thành lệnh thật.

        :return: (text báo cáo, decision) — decision là None khi không dựng được kế hoạch
        """
        from freqtrade.enums import CandleType
        from freqtrade.util import dt_now

        candle_type = ft.config.get("candle_type_def", CandleType.SPOT)
        # Giá khớp mới nhất (ticker công khai) — dùng cho hiển thị & nhận định sweep.
        live_price = await asyncio.to_thread(self._smc_live_price, ft, pair)

        levels_by_tf: dict[str, dict] = {}
        price_by_tf: dict[str, float] = {}
        for tf in tfs:
            # Fetch + phân tích trên nến đã đóng (chạy trong thread riêng vì blocking).
            _line, _score, price, levels = await asyncio.to_thread(
                self._smc_tf_line, ft, pair, tf, candle_type
            )
            if price is not None:
                price_by_tf[tf] = price
            if levels:
                levels_by_tf[tf] = levels

        now_str = dt_now().strftime("%Y-%m-%d %H:%M UTC")
        text = "\n".join(
            self._smc_signal_report(
                ft.strategy, pair, tfs, levels_by_tf, price_by_tf, live_price, now_str
            )
        )

        decision = None
        ctx = self._smc_report_context(ft.strategy, tfs, levels_by_tf)
        if ctx:
            _avail, _htf, stf, direction, *_ = ctx
            cp = price_by_tf.get(stf) or (live_price or 0.0)
            decision = self._smc_trade_decision(
                ft.strategy, levels_by_tf[stf], price_by_tf.get(stf, cp), direction
            )
            decision["timeframe"] = stf
            decision["live_price"] = live_price
        return text, decision

    @staticmethod
    def _next_analysis_slot(now: datetime, interval_hours: int, day_start_hour: int) -> datetime:
        """Thời điểm chạy /analysis kế tiếp: các mốc cách nhau `interval_hours`,
        neo tại `day_start_hour` (vd 4h neo 7h -> 7,11,15,19,23,3)."""
        interval = max(1, int(interval_hours))
        day_start = int(day_start_hour) % 24
        slots = sorted({(day_start + k * interval) % 24 for k in range(max(1, 24 // interval))})
        base = now.replace(minute=0, second=0, microsecond=0)
        for h in slots:
            cand = base.replace(hour=h)
            if cand > now:
                return cand
        return (base + timedelta(days=1)).replace(hour=slots[0])

    def _auto_entry_from_analysis(self, ft, pair: str, decision: dict) -> str | None:
        """Vào lệnh thật theo kế hoạch mà lịch /analysis vừa gửi. Chạy trong thread riêng.

        Đây là đường vào lệnh THỨ HAI của bot, song song với `populate_entry_trend`. Nó CỐ Ý
        bỏ qua các cổng của strategy (G1 bull_1d, G2 discount, score) — đó là điều được yêu
        cầu, và cũng là rủi ro chính: các cổng đó nằm trong phần edge đã đo được trên 197 cặp,
        còn luật này thì chưa từng được backtest.

        Cái KHÔNG bỏ qua, vì `_rpc_force_entry` tự kiểm:
          · `max_open_trades` của bot (rpc.py: "Maximum number of trades is reached")
          · `stake_amount` + `available_capital` (qua `wallets.get_trade_stake_amount`)
          · cặp đã có lệnh mở, bot không ở trạng thái RUNNING, cặp không hợp lệ

        :return: dòng kết quả để gửi về Telegram, hoặc None khi không làm gì
        """
        from freqtrade.enums import SignalDirection
        from freqtrade.rpc import RPCException

        fmt = self._smc_fmt_price
        if decision.get("reject"):
            return None
        # Long-only: SmcElliottStrategy đặt can_short=False và không có nhánh short nào.
        # Vào short ở đây sẽ tạo vị thế mà mọi callback thoát lệnh của strategy đều không
        # hiểu — tệ hơn nhiều so với bỏ lỡ.
        if decision.get("direction") != "long":
            return None

        # Limit đặt tại MÉP XẤU NHẤT của vùng — đúng con số mà R:R trong báo cáo được tính
        # trên đó. Đặt ở mép đẹp hơn thì R:R thực tế sẽ khác với R:R vừa công bố.
        price: float | None = decision.get("entry_worst") or None
        stop: float | None = decision.get("stop")
        # Không rơi về giá thị trường khi thiếu entry/SL: lệnh vào ở giá khác kế hoạch là
        # một lệnh KHÁC với lệnh vừa báo cáo, không phải cùng lệnh đặt hơi lệch.
        if price is None or stop is None:
            return f"⚠️ `{pair}`: kế hoạch thiếu giá entry hoặc SL → bỏ qua."
        try:
            trade = self._rpc._rpc_force_entry(
                pair,
                price,
                order_type="limit",
                order_side=SignalDirection.LONG,
                enter_tag="analysis_auto",
            )
        except RPCException as e:
            # Hết slot / đã có lệnh / bot đang stop: đây là hoạt động BÌNH THƯỜNG của hạn
            # mức, không phải lỗi — nhưng vẫn phải nói ra, im lặng thì không ai biết vì sao
            # báo cáo có kế hoạch mà tài khoản không có lệnh.
            return f"⛔ `{pair}`: không vào được — {e}"
        if trade is None:
            return f"⚠️ `{pair}`: lệnh không được tạo (xem log)."
        rr = decision.get("rr_first")
        return (
            f"✅ `{pair}`: đã đặt LONG limit `{fmt(price)}` "
            f"(SL `{fmt(stop)}`"
            + (f", R:R TP1 1:{rr:.1f}" if rr else "")
            + f", khung `{decision.get('timeframe')}`)"
        )

    async def _run_scheduled_analysis(self, cfg: dict) -> None:
        """Gửi báo cáo /analysis cho các cặp đã cấu hình (hoặc whitelist).

        Bật `auto_entry.enabled` thì mỗi kế hoạch KHÔNG bị "BỎ LỆNH" sẽ được vào lệnh thật
        ngay sau khi báo cáo của cặp đó gửi đi. Xem `_auto_entry_from_analysis`.
        """
        ft = self._rpc._freqtrade
        tfs = cfg.get("timeframes") or ["15m", "1h", "4h", "1d"]
        pairs = cfg.get("pairs") or (ft.active_pair_whitelist or [])[: int(cfg.get("max_pairs", 3))]
        if not pairs:
            return
        auto = cfg.get("auto_entry") or {}
        auto_on = bool(auto.get("enabled"))
        # Trần riêng cho mỗi lượt chạy, KHÁC max_open_trades: nó chặn việc một lượt phân tích
        # xấu quét sạch mọi slot cùng lúc. max_open_trades vẫn là trần tuyệt đối phía dưới.
        left = int(auto.get("max_per_run", 2)) if auto_on else 0
        if auto_on and not ft.config.get("force_entry_enable", False):
            # Không tự bật hộ: force_entry_enable cũng mở /forcebuy cho bất kỳ ai vào được
            # chat, nên đó phải là quyết định tường minh trong config.
            await self._send_msg(
                "⚠️ `auto_entry.enabled` đang bật nhưng `force_entry_enable` là false → "
                'KHÔNG vào lệnh nào. Đặt `"force_entry_enable": true` trong config.'
            )
            auto_on = False

        await self._send_msg("🕗 *Báo cáo phân tích định kỳ*")
        results: list[str] = []
        for pair in pairs:
            try:
                text, decision = await self._build_analysis(ft, pair, tfs)
                await self._send_msg(text)
            except Exception as e:
                logger.warning("Lịch /analysis %s lỗi: %s: %s", pair, type(e).__name__, e)
                continue
            if not auto_on or not decision or left <= 0:
                continue
            try:
                line = await asyncio.to_thread(self._auto_entry_from_analysis, ft, pair, decision)
            except Exception as e:
                logger.exception("Auto-entry %s lỗi: %s: %s", pair, type(e).__name__, e)
                line = f"⚠️ `{pair}`: auto-entry lỗi ({type(e).__name__})."
            if line:
                results.append(line)
                if line.startswith("✅"):
                    left -= 1
        if results:
            await self._send_msg("🤖 *Vào lệnh tự động theo lịch*\n" + "\n".join(results))

    async def _analysis_schedule_loop(self) -> None:
        """Vòng lặp lịch: tự động chạy /analysis theo giờ cố định (nếu bật config).

        Cấu hình dưới `telegram.analysis_schedule`:
          enabled(bool), interval_hours(=4), day_start_hour(=7),
          timezone(='Asia/Ho_Chi_Minh'), pairs(=whitelist), timeframes, max_pairs(=3).
        """
        cfg = (self._config.get("telegram") or {}).get("analysis_schedule") or {}
        if not cfg.get("enabled"):
            return
        from zoneinfo import ZoneInfo

        try:
            tz = ZoneInfo(cfg.get("timezone", "Asia/Ho_Chi_Minh"))
        except Exception:
            tz = ZoneInfo("UTC")
        interval = int(cfg.get("interval_hours", 4))
        day_start = int(cfg.get("day_start_hour", 7))
        # Trễ sau khi nến đóng để chắc chắn sàn đã chốt nến (tránh fetch hụt).
        offset = max(0, int(cfg.get("offset_seconds", 0)))
        logger.info(
            "Lịch /analysis bật: mỗi %dh, neo %02d:00 (%s), trễ %ds.",
            interval,
            day_start,
            tz,
            offset,
        )
        while not self._shutdown_event.is_set():
            now = datetime.now(tz)
            nxt = self._next_analysis_slot(now, interval, day_start)
            wait = max(1.0, (nxt - now).total_seconds() + offset)
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=wait)
                break  # nhận tín hiệu dừng
            except TimeoutError:
                pass
            try:
                await self._run_scheduled_analysis(cfg)
            except Exception as e:
                logger.warning("Lịch /analysis lỗi: %s: %s", type(e).__name__, e)

    @authorized_only
    async def _weekly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /weekly <n>
        Returns a weekly profit (in BTC) over the last n weeks.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "weeks")

    @authorized_only
    async def _monthly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /monthly <n>
        Returns a monthly profit (in BTC) over the last n months.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._timeunit_stats(update, context, "months")

    def _format_profit_message(
        self,
        stats: dict,
        stake_cur: str,
        fiat_disp_cur: str,
        timescale: int | None = None,
        direction: str | None = None,
    ) -> str:
        """
        Format profit statistics message for telegram.

        :param stats: Trade statistics dictionary
        :param stake_cur: Stake currency
        :param fiat_disp_cur: Fiat display currency
        :param timescale: Optional timescale filter
        :param direction: Optional direction filter ('long', 'short', or None for all)
        :return: Formatted markdown message
        """
        # Extract common variables
        profit_closed_coin = stats["profit_closed_coin"]
        profit_closed_ratio_mean = stats["profit_closed_ratio_mean"]
        profit_closed_percent = stats["profit_closed_percent"]
        profit_closed_fiat = stats["profit_closed_fiat"]
        profit_all_coin = stats["profit_all_coin"]
        profit_all_ratio_mean = stats["profit_all_ratio_mean"]
        profit_all_percent = stats["profit_all_percent"]
        profit_all_fiat = stats["profit_all_fiat"]
        trade_count = stats["trade_count"]
        first_trade_date = f"{stats['first_trade_humanized']} ({stats['first_trade_date']})"
        latest_trade_date = f"{stats['latest_trade_humanized']} ({stats['latest_trade_date']})"
        avg_duration = stats["avg_duration"]
        best_pair = stats["best_pair"]
        best_pair_profit_ratio = stats["best_pair_profit_ratio"]
        best_pair_profit_abs = fmt_coin(stats["best_pair_profit_abs"], stake_cur)
        winrate = stats["winrate"]
        expectancy = stats["expectancy"]
        expectancy_ratio = stats["expectancy_ratio"]

        # Direction-specific labels
        direction_label = f" {direction}" if direction else ""
        no_trades_msg = (
            f"No{direction_label} trades yet.\n*Bot started:* `{stats['bot_start_date']}`"
        )
        no_closed_msg = f"`No closed{direction_label} trade` \n"
        closed_roi_label = f"*ROI:* Closed{direction_label} trades"
        all_roi_label = f"*ROI:* All{direction_label} trades"

        if stats["trade_count"] == 0:
            return no_trades_msg

        # Build message
        if stats["closed_trade_count"] > 0:
            fiat_closed_trades = (
                f"∙ `{fmt_coin(profit_closed_fiat, fiat_disp_cur)}`\n" if fiat_disp_cur else ""
            )
            markdown_msg = (
                f"{closed_roi_label}\n"
                f"∙ `{fmt_coin(profit_closed_coin, stake_cur)} "
                f"({format_pct(profit_closed_ratio_mean)}) "
                f"({profit_closed_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
                f"{fiat_closed_trades}"
            )
        else:
            markdown_msg = no_closed_msg

        fiat_all_trades = (
            f"∙ `{fmt_coin(profit_all_fiat, fiat_disp_cur)}`\n" if fiat_disp_cur else ""
        )
        markdown_msg += (
            f"{all_roi_label}\n"
            f"∙ `{fmt_coin(profit_all_coin, stake_cur)} "
            f"({format_pct(profit_all_ratio_mean)}) "
            f"({profit_all_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
            f"{fiat_all_trades}"
            f"*Total Trade Count:* `{trade_count}`\n"
            f"*Bot started:* `{stats['bot_start_date']}`\n"
            f"*{'First Trade opened' if not timescale else 'Showing Profit since'}:* "
            f"`{first_trade_date}`\n"
            f"*Latest Trade opened:* `{latest_trade_date}`\n"
            f"*Win / Loss:* `{stats['winning_trades']} / {stats['losing_trades']}`\n"
            f"*Winrate:* `{format_pct(winrate)}`\n"
            f"*Expectancy (Ratio):* `{expectancy:.2f} ({expectancy_ratio:.2f})`"
        )

        if stats["closed_trade_count"] > 0:
            markdown_msg += (
                f"\n*Avg. Duration:* `{avg_duration}`\n"
                f"*Best Performing:* `{best_pair}: {best_pair_profit_abs} "
                f"({format_pct(best_pair_profit_ratio)})`\n"
                f"*Trading volume:* `{fmt_coin(stats['trading_volume'], stake_cur)}`\n"
                f"*Profit factor:* `{stats['profit_factor']:.2f}`\n"
                f"*Max Drawdown:* `{format_pct(stats['max_drawdown'])} "
                f"({fmt_coin(stats['max_drawdown_abs'], stake_cur)})`\n"
                f"    from `{stats['max_drawdown_start']} "
                f"({fmt_coin(stats['drawdown_high'], stake_cur)})`\n"
                f"    to `{stats['max_drawdown_end']} "
                f"({fmt_coin(stats['drawdown_low'], stake_cur)})`\n"
                f"*Current Drawdown:* `{format_pct(stats['current_drawdown'])} "
                f"({fmt_coin(stats['current_drawdown_abs'], stake_cur)})`\n"
                f"    from `{stats['current_drawdown_start']} "
                f"({fmt_coin(stats['current_drawdown_high'], stake_cur)})`\n"
            )

        return markdown_msg

    async def _profit_handler(
        self,
        update: Update,
        context: CallbackContext,
        direction: str | None = None,
    ) -> None:
        """
        Common handler for profit commands.

        :param update: Telegram update
        :param context: Callback context
        :param direction: Trade direction filter ('long', 'short', or None)
        :param callback_path: Callback path for message updates
        """
        stake_cur = self._config["stake_currency"]
        fiat_disp_cur = self._config.get("fiat_display_currency", "")

        start_date = datetime.fromtimestamp(0)
        timescale = None
        try:
            if context.args:
                if not direction:
                    arg = context.args[0].lower()
                    if arg in ("short", "long"):
                        direction = arg
                        context.args.pop(0)  # Remove direction from args
                timescale = int(context.args[0]) - 1
                today_start = datetime.combine(date.today(), datetime.min.time())
                start_date = today_start - timedelta(days=timescale)
        except (TypeError, ValueError, IndexError):
            pass

        # Get stats with optional direction filter
        stats_kwargs = {
            "stake_currency": stake_cur,
            "fiat_display_currency": fiat_disp_cur,
            "start_date": start_date,
        }
        if direction:
            stats_kwargs["direction"] = direction

        stats = self._rpc._rpc_trade_statistics(**stats_kwargs)
        markdown_msg = self._format_profit_message(
            stats, stake_cur, fiat_disp_cur, timescale, direction
        )

        await self._send_msg(
            markdown_msg,
            reload_able=True,
            callback_path="update_profit" if not direction else f"update_profit_{direction}",
            query=update.callback_query,
        )

    @authorized_only
    async def _profit(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit.
        Returns a cumulative profit statistics.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        await self._profit_handler(update, context)

    @authorized_only
    async def _profit_long(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit_long.
        Returns cumulative profit statistics for long trades.
        """
        await self._profit_handler(update, context, direction="long")

    @authorized_only
    async def _profit_short(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit_short.
        Returns cumulative profit statistics for short trades.
        """
        await self._profit_handler(update, context, direction="short")

    @authorized_only
    async def _stats(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stats
        Show stats of recent trades
        """
        stats = self._rpc._rpc_stats()

        reason_map = {
            "roi": "ROI",
            "stop_loss": "Stoploss",
            "trailing_stop_loss": "Trail. Stop",
            "stoploss_on_exchange": "Stoploss",
            "exit_signal": "Exit Signal",
            "force_exit": "Force Exit",
            "emergency_exit": "Emergency Exit",
        }
        exit_reasons_tabulate = [
            [reason_map.get(reason, reason), sum(count.values()), count["wins"], count["losses"]]
            for reason, count in stats["exit_reasons"].items()
        ]
        exit_reasons_msg = "No trades yet."
        for reason in chunks(exit_reasons_tabulate, 25):
            exit_reasons_msg = tabulate(reason, headers=["Exit Reason", "Exits", "Wins", "Losses"])
            if len(exit_reasons_tabulate) > 25:
                await self._send_msg(f"```\n{exit_reasons_msg}```", ParseMode.MARKDOWN)
                exit_reasons_msg = ""

        durations = stats["durations"]
        duration_msg = tabulate(
            [
                [
                    "Wins",
                    (
                        str(timedelta(seconds=durations["wins"]))
                        if durations["wins"] is not None
                        else "N/A"
                    ),
                ],
                [
                    "Losses",
                    (
                        str(timedelta(seconds=durations["losses"]))
                        if durations["losses"] is not None
                        else "N/A"
                    ),
                ],
            ],
            headers=["", "Avg. Duration"],
        )
        msg = f"""```\n{exit_reasons_msg}```\n```\n{duration_msg}```"""

        await self._send_msg(msg, ParseMode.MARKDOWN)

    @authorized_only
    async def _balance(self, update: Update, context: CallbackContext) -> None:
        """Handler for /balance"""
        full_result = context.args and "full" in context.args
        result = self._rpc._rpc_balance(
            self._config["stake_currency"], self._config.get("fiat_display_currency", "")
        )

        balance_dust_level = self._config["telegram"].get("balance_dust_level", 0.0)
        if not balance_dust_level:
            balance_dust_level = DUST_PER_COIN.get(self._config["stake_currency"], 1.0)

        output = ""
        if self._config["dry_run"]:
            output += "*Warning:* Simulated balances in Dry Mode.\n"
        starting_cap = fmt_coin(result["starting_capital"], self._config["stake_currency"])
        output += f"Starting capital: `{starting_cap}`"
        starting_cap_fiat = (
            fmt_coin(result["starting_capital_fiat"], self._config["fiat_display_currency"])
            if result["starting_capital_fiat"] > 0
            else ""
        )
        output += (f" `, {starting_cap_fiat}`.\n") if result["starting_capital_fiat"] > 0 else ".\n"

        total_dust_balance = 0
        total_dust_currencies = 0
        for curr in result["currencies"]:
            curr_output = ""
            if (curr["is_position"] or curr["est_stake"] > balance_dust_level) and (
                full_result or curr["is_bot_managed"]
            ):
                if curr["is_position"]:
                    curr_output = (
                        f"*{curr['currency']}:*\n"
                        f"\t`{curr['side']}: {round_value(curr['position'], 8)}`\n"
                        f"\t`Est. {curr['stake']}: "
                        f"{fmt_coin(curr['est_stake'], curr['stake'], False)}`\n"
                    )
                else:
                    est_stake = fmt_coin(
                        curr["est_stake" if full_result else "est_stake_bot"], curr["stake"], False
                    )

                    curr_output = (
                        f"*{curr['currency']}:*\n"
                        f"\t`Available: {fmt_coin(curr['free'], curr['currency'], False)}`\n"
                        f"\t`Balance: {fmt_coin(curr['balance'], curr['currency'], False)}`\n"
                        f"\t`Pending: {fmt_coin(curr['used'], curr['currency'], False)}`\n"
                        f"\t`Bot Owned: {fmt_coin(curr['bot_owned'], curr['currency'], False)}`\n"
                        f"\t`Est. {curr['stake']}: {est_stake}`\n"
                    )

            elif curr["est_stake"] <= balance_dust_level:
                total_dust_balance += curr["est_stake"]
                total_dust_currencies += 1

            # Handle overflowing message length
            if len(output + curr_output) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output)
                output = curr_output
            else:
                output += curr_output

        if total_dust_balance > 0:
            output += (
                f"*{total_dust_currencies} Other "
                f"{plural(total_dust_currencies, 'Currency', 'Currencies')} "
                f"(< {balance_dust_level} {result['stake']}):*\n"
                f"\t`Est. {result['stake']}: "
                f"{fmt_coin(total_dust_balance, result['stake'], False)}`\n"
            )
        tc = result["trade_count"] > 0
        stake_improve = f" `({result['starting_capital_ratio']:.2%})`" if tc else ""
        fiat_val = f" `({result['starting_capital_fiat_ratio']:.2%})`" if tc else ""
        value = fmt_coin(result["value" if full_result else "value_bot"], result["symbol"], False)
        total_stake = fmt_coin(
            result["total" if full_result else "total_bot"], result["stake"], False
        )
        fiat_estimated_value = (
            f"\t`{result['symbol']}: {value}`{fiat_val}\n" if result["symbol"] else ""
        )
        output += (
            f"\n*Estimated Value{' (Bot managed assets only)' if not full_result else ''}*:\n"
            f"\t`{result['stake']}: {total_stake}`{stake_improve}\n"
            f"{fiat_estimated_value}"
        )
        await self._send_msg(
            output, reload_able=True, callback_path="update_balance", query=update.callback_query
        )

    @authorized_only
    async def _start(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /start.
        Starts TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_start()
        await self._send_msg(f"Status: `{msg['status']}`")

    @authorized_only
    async def _stop(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop.
        Stops TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_stop()
        await self._send_msg(f"Status: `{msg['status']}`")

    @authorized_only
    async def _reload_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /reload_config.
        Triggers a config file reload
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_reload_config()
        await self._send_msg(f"Status: `{msg['status']}`")

    @authorized_only
    async def _pause(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop_buy /stop_entry and /pause.
        Sets bot state to paused
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_pause()
        await self._send_msg(f"Status: `{msg['status']}`")

    @authorized_only
    async def _reload_trade_from_exchange(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /reload_trade <tradeid>.
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        msg = self._rpc._rpc_reload_trade_from_exchange(trade_id)
        await self._send_msg(f"Status: `{msg['status']}`")

    @authorized_only
    async def _force_exit(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /forceexit <id>.
        Sells the given trade at current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        if context.args:
            trade_id = context.args[0]
            await self._force_exit_action(trade_id)
        else:
            fiat_currency = self._config.get("fiat_display_currency", "")
            try:
                statlist, _, _, _ = self._rpc._rpc_status_table(
                    self._config["stake_currency"], fiat_currency
                )
            except RPCException:
                await self._send_msg(msg="No open trade found.")
                return
            trades = []
            for trade in statlist:
                trades.append((trade[0], f"{trade[0]} {trade[1]} {trade[2]} {trade[3]}"))

            trade_buttons = [
                InlineKeyboardButton(text=trade[1], callback_data=f"force_exit__{trade[0]}")
                for trade in trades
            ]
            buttons_aligned = self._layout_inline_keyboard(trade_buttons, cols=1)

            buttons_aligned.append(
                [InlineKeyboardButton(text="Cancel", callback_data="force_exit__cancel")]
            )
            await self._send_msg(msg="Which trade?", keyboard=buttons_aligned)

    async def _force_exit_action(self, trade_id: str):
        if trade_id != "cancel":
            try:
                loop = asyncio.get_running_loop()
                # Workaround to avoid nested loops
                await loop.run_in_executor(None, safe_async_db(self._rpc._rpc_force_exit), trade_id)
            except RPCException as e:
                await self._send_msg(str(e))

    async def _force_exit_inline(self, update: Update, _: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            if query.data and "__" in query.data:
                # Input data is "force_exit__<tradid|cancel>"
                trade_id = query.data.split("__")[1].split(" ")[0]
                if trade_id == "cancel":
                    await query.answer()
                    await query.edit_message_text(text="Force exit canceled.")
                    return
                trade: Trade | None = (
                    Trade.get_trades(trade_filter=Trade.id == int(trade_id)).first()
                    if trade_id.isdigit()
                    else None
                )
                await query.answer()
                if trade:
                    await query.edit_message_text(
                        text=f"Manually exiting Trade #{trade_id}, {trade.pair}"
                    )
                    await self._force_exit_action(trade_id)
                else:
                    await query.edit_message_text(text=f"Trade {trade_id} not found.")

    async def _force_enter_action(self, pair, price: float | None, order_side: SignalDirection):
        if pair != "cancel":
            try:

                @safe_async_db
                def _force_enter():
                    self._rpc._rpc_force_entry(pair, price, order_side=order_side)

                loop = asyncio.get_running_loop()
                # Workaround to avoid nested loops
                await loop.run_in_executor(None, _force_enter)
            except RPCException as e:
                logger.exception("Forcebuy error!")
                await self._send_msg(str(e), ParseMode.HTML)

    async def _force_enter_inline(self, update: Update, _: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            if query.data and "__" in query.data:
                # Input data is "force_enter__<pair|cancel>_<side>"
                payload = query.data.split("__")[1]
                if payload == "cancel":
                    await query.answer()
                    await query.edit_message_text(text="Force enter canceled.")
                    return
                if payload and "_||_" in payload:
                    pair, side = payload.split("_||_")
                    order_side = SignalDirection(side)
                    await query.answer()
                    await query.edit_message_text(text=f"Manually entering {order_side} for {pair}")
                    await self._force_enter_action(pair, None, order_side)

    @staticmethod
    def _layout_inline_keyboard(
        buttons: list[InlineKeyboardButton], cols=3
    ) -> list[list[InlineKeyboardButton]]:
        return [buttons[i : i + cols] for i in range(0, len(buttons), cols)]

    @authorized_only
    async def _force_enter(
        self, update: Update, context: CallbackContext, order_side: SignalDirection
    ) -> None:
        """
        Handler for /forcelong <asset> <price> and `/forceshort <asset> <price>
        Buys a pair trade at the given or current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if context.args:
            pair = context.args[0]
            price = float(context.args[1]) if len(context.args) > 1 else None
            await self._force_enter_action(pair, price, order_side)
        else:
            whitelist = self._rpc._rpc_whitelist()["whitelist"]
            pair_buttons = [
                InlineKeyboardButton(
                    text=pair, callback_data=f"force_enter__{pair}_||_{order_side}"
                )
                for pair in sorted(whitelist)
            ]
            buttons_aligned = self._layout_inline_keyboard(pair_buttons)

            buttons_aligned.append(
                [InlineKeyboardButton(text="Cancel", callback_data="force_enter__cancel")]
            )
            await self._send_msg(
                msg="Which pair?", keyboard=buttons_aligned, query=update.callback_query
            )

    @authorized_only
    async def _trades(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /trades <n>
        Returns last n recent trades.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config["stake_currency"]
        try:
            nrecent = int(context.args[0]) if context.args else 10
        except (TypeError, ValueError, IndexError):
            nrecent = 10
        nonspot = self._config.get("trading_mode", TradingMode.SPOT) != TradingMode.SPOT
        trades = self._rpc._rpc_trade_history(nrecent)
        trades_tab = tabulate(
            [
                [
                    dt_humanize_delta(dt_from_ts(trade["close_timestamp"])),
                    f"{trade['pair']} (#{trade['trade_id']}"
                    f"{(' ' + ('S' if trade['is_short'] else 'L')) if nonspot else ''})",
                    f"{format_pct(trade['close_profit'])} ({trade['close_profit_abs']})",
                ]
                for trade in trades["trades"]
            ],
            headers=[
                "Close Date",
                "Pair (ID L/S)" if nonspot else "Pair (ID)",
                f"Profit ({stake_cur})",
            ],
            tablefmt="simple",
        )
        message = f"<b>{min(trades['trades_count'], nrecent)} recent trades</b>:\n" + (
            f"<pre>{trades_tab}</pre>" if trades["trades_count"] > 0 else ""
        )
        await self._send_msg(message, parse_mode=ParseMode.HTML)

    @authorized_only
    async def _delete_trade(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete <id>.
        Delete the given trade
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        msg = self._rpc._rpc_delete(trade_id)
        await self._send_msg(
            f"{msg['result_msg']}\n"
            "Please make sure to take care of this asset on the exchange manually."
        )

    @authorized_only
    async def _cancel_open_order(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /cancel_open_order <id>.
        Cancel open order for tradeid
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if not context.args or len(context.args) == 0:
            raise RPCException("Trade-id not set.")
        trade_id = int(context.args[0])
        self._rpc._rpc_cancel_open_order(trade_id)
        await self._send_msg("Open order canceled.")

    @authorized_only
    async def _enter_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /entries PAIR .
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_enter_tag_performance(pair)
        output = "*Entry Tag Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['enter_tag']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({format_pct(trade['profit_ratio'])}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_enter_tag_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _exit_reason_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /exits.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_exit_reason_performance(pair)
        output = "*Exit Reason Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['exit_reason']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({format_pct(trade['profit_ratio'])}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_exit_reason_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _mix_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /mix_tags.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        pair = None
        if context.args and isinstance(context.args[0], str):
            pair = context.args[0]

        trades = self._rpc._rpc_mix_tag_performance(pair)
        output = "*Mix Tag Performance:*\n"
        for i, trade in enumerate(trades):
            stat_line = (
                f"{i + 1}.\t `{trade['mix_tag']}\t"
                f"{fmt_coin(trade['profit_abs'], self._config['stake_currency'])} "
                f"({format_pct(trade['profit_ratio'])}) "
                f"({trade['count']})`\n"
            )

            if len(output + stat_line) >= MAX_MESSAGE_LENGTH:
                await self._send_msg(output, parse_mode=ParseMode.MARKDOWN)
                output = stat_line
            else:
                output += stat_line

        await self._send_msg(
            output,
            parse_mode=ParseMode.MARKDOWN,
            reload_able=True,
            callback_path="update_mix_tag_performance",
            query=update.callback_query,
        )

    @authorized_only
    async def _locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /locks.
        Returns the currently active locks
        """
        rpc_locks = self._rpc._rpc_locks()
        if not rpc_locks["locks"]:
            await self._send_msg("No active locks.", parse_mode=ParseMode.HTML)

        for locks in chunks(rpc_locks["locks"], 25):
            message = tabulate(
                [
                    [lock["id"], lock["pair"], lock["lock_end_time"], lock["reason"]]
                    for lock in locks
                ],
                headers=["ID", "Pair", "Until", "Reason"],
                tablefmt="simple",
            )
            message = f"<pre>{escape(message)}</pre>"
            logger.debug(message)
            await self._send_msg(message, parse_mode=ParseMode.HTML)

    @authorized_only
    async def _delete_locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete_locks.
        Returns the currently active locks
        """
        arg = context.args[0] if context.args and len(context.args) > 0 else None
        lockid = None
        pair = None
        if arg:
            try:
                lockid = int(arg)
            except ValueError:
                pair = arg

        self._rpc._rpc_delete_lock(lockid=lockid, pair=pair)
        await self._locks(update, context)

    @authorized_only
    async def _whitelist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /whitelist
        Shows the currently active whitelist
        """
        whitelist = self._rpc._rpc_whitelist()

        if context.args:
            if "sorted" in context.args:
                whitelist["whitelist"] = sorted(whitelist["whitelist"])
            if "baseonly" in context.args:
                whitelist["whitelist"] = [pair.split("/")[0] for pair in whitelist["whitelist"]]

        message = f"Using whitelist `{whitelist['method']}` with {whitelist['length']} pairs\n"
        message += f"`{', '.join(whitelist['whitelist'])}`"

        logger.debug(message)
        await self._send_msg(message)

    @authorized_only
    async def _blacklist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /blacklist
        Shows the currently active blacklist
        """
        await self.send_blacklist_msg(self._rpc._rpc_blacklist(context.args))

    async def send_blacklist_msg(self, blacklist: dict):
        errmsgs = []
        for _, error in blacklist["errors"].items():
            errmsgs.append(f"Error: {error['error_msg']}")
        if errmsgs:
            await self._send_msg("\n".join(errmsgs))

        message = f"Blacklist contains {blacklist['length']} pairs\n"
        message += f"`{', '.join(blacklist['blacklist'])}`"

        logger.debug(message)
        await self._send_msg(message)

    @authorized_only
    async def _blacklist_delete(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /bl_delete
        Deletes pair(s) from current blacklist
        """
        await self.send_blacklist_msg(self._rpc._rpc_blacklist_delete(context.args or []))

    @authorized_only
    async def _logs(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /logs
        Shows the latest logs
        """
        try:
            limit = int(context.args[0]) if context.args else 10
        except (TypeError, ValueError, IndexError):
            limit = 10
        logs = RPC._rpc_get_logs(limit)["logs"]
        msgs = ""
        msg_template = "*{}* {}: {} \\- `{}`"
        for logrec in logs:
            msg = msg_template.format(
                escape_markdown(logrec[0], version=2),
                escape_markdown(logrec[2], version=2),
                escape_markdown(logrec[3], version=2),
                escape_markdown(logrec[4], version=2),
            )
            if len(msgs + msg) + 10 >= MAX_MESSAGE_LENGTH:
                # Send message immediately if it would become too long
                await self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)
                msgs = msg + "\n"
            else:
                # Append message to messages to send
                msgs += msg + "\n"

        if msgs:
            await self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)

    @authorized_only
    async def _help(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /help.
        Show commands of the bot
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        force_enter_text = (
            "*/forcelong <pair> [<rate>]:* `Instantly buys the given pair. "
            "Optionally takes a rate at which to buy "
            "(only applies to limit orders).` \n"
        )
        if self._rpc._freqtrade.trading_mode != TradingMode.SPOT:
            force_enter_text += (
                "*/forceshort <pair> [<rate>]:* `Instantly shorts the given pair. "
                "Optionally takes a rate at which to sell "
                "(only applies to limit orders).` \n"
            )
        message = (
            "_Bot Control_\n"
            "------------\n"
            "*/start:* `Starts the trader`\n"
            "*/pause:* `Pause the new entries for trader, but handles open trades gracefully`\n"
            "*/stop:* `Stops the trader`\n"
            "*/stopentry:* `Stops entering, but handles open trades gracefully` \n"
            "*/forceexit <trade_id>|all:* `Instantly exits the given trade or all trades, "
            "regardless of profit`\n"
            "*/fx <trade_id>|all:* `Alias to /forceexit`\n"
            f"{force_enter_text if self._config.get('force_entry_enable', False) else ''}"
            "*/delete <trade_id>:* `Instantly delete the given trade in the database`\n"
            "*/reload_trade <trade_id>:* `Reload trade from exchange Orders`\n"
            "*/cancel_open_order <trade_id>:* `Cancels open orders for trade. "
            "Only valid when the trade has open orders.`\n"
            "*/coo <trade_id>|all:* `Alias to /cancel_open_order`\n"
            "*/whitelist [sorted] [baseonly]:* `Show current whitelist. Optionally in "
            "order and/or only displaying the base currency of each pairing.`\n"
            "*/blacklist [pair]:* `Show current blacklist, or adds one or more pairs "
            "to the blacklist.` \n"
            "*/blacklist_delete [pairs]| /bl_delete [pairs]:* "
            "`Delete pair / pattern from blacklist. Will reset on reload_conf.` \n"
            "*/reload_config:* `Reload configuration file` \n"
            "*/unlock <pair|id>:* `Unlock this Pair (or this lock id if it's numeric)`\n"
            "_Current state_\n"
            "------------\n"
            "*/show_config:* `Show running configuration` \n"
            "*/locks:* `Show currently locked pairs`\n"
            "*/balance:* `Show bot managed balance per currency`\n"
            "*/balance total:* `Show account balance per currency`\n"
            "*/logs [limit]:* `Show latest logs - defaults to 10` \n"
            "*/health* `Show latest process timestamp - defaults to 1970-01-01 00:00:00` \n"
            "*/marketdir [long | short | even | none]:* `Updates the user managed variable "
            "that represents the current market direction. If no direction is provided `"
            "`the currently set market direction will be output.` \n"
            "*/list_custom_data <trade_id> <key>:* `List custom_data for Trade ID & Key combo.`\n"
            "`If no Key is supplied it will list all key-value pairs found for that Trade ID.`\n"
            "_Statistics_\n"
            "------------\n"
            "*/status <trade_id>|[table]:* `Lists all open trades`\n"
            "         *<trade_id> :* `Lists one or more specific trades.`\n"
            "                        `Separate multiple <trade_id> with a blank space.`\n"
            "         *table :* `will display trades in a table`\n"
            "                `pending buy orders are marked with an asterisk (*)`\n"
            "                `pending sell orders are marked with a double asterisk (**)`\n"
            "*/entries <pair|none>:* `Shows the enter_tag performance`\n"
            "*/exits <pair|none>:* `Shows the exit reason performance`\n"
            "*/mix_tags <pair|none>:* `Shows combined entry tag + exit reason performance`\n"
            "*/trades [limit]:* `Lists last closed trades (limited to 10 by default)`\n"
            "*/profit [<n>]:* `Lists cumulative profit from all finished trades, "
            "over the last n days`\n"
            "*/profit_long [<n>]:* `Lists cumulative profit from all finished long trades, "
            "over the last n days`\n"
            "*/profit_short [<n>]:* `Lists cumulative profit from all finished short trades, "
            "over the last n days`\n"
            "*/daily <n>:* `Shows profit or loss per day, over the last n days`\n"
            "*/analysis [pair] [tf...]:* `Báo cáo tín hiệu SMC đầy đủ cho MỘT cặp "
            "(bias đa khung, cấu trúc/OB/FVG, thanh khoản, hợp lưu, kế hoạch "
            "Entry/SL/TP, quản trị rủi ro) (mặc định 15m 1h 4h 1d)`\n"
            "*/smc [pair] [tf...]:* `Không tham số: báo cáo khung "
            f"{' + '.join(self._smc_quick_timeframes(self._rpc._freqtrade))} "
            f"(theo khung của bot này) cho {', '.join(SMC_QUICK_BASES)}. "
            "Có tham số: giống /analysis`\n"
            "*/scalp [pair] [tf...]:* `Lệnh trong ngày — "
            f"{' + '.join(SMC_SCALP_TIMEFRAMES)} (bias {SMC_SCALP_TIMEFRAMES[-1]}, "
            f"kế hoạch {SMC_SCALP_TIMEFRAMES[0]}). Không tham số: "
            f"{', '.join(SMC_QUICK_BASES)}`\n"
            "*/weekly <n>:* `Shows statistics per week, over the last n weeks`\n"
            "*/monthly <n>:* `Shows statistics per month, over the last n months`\n"
            "*/stats:* `Shows Wins / losses by Sell reason as well as "
            "Avg. holding durations for buys and sells.`\n"
            "*/help:* `This help message`\n"
            "*/version:* `Show version`\n"
        )

        await self._send_msg(message, parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    async def _health(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /health
        Shows the last process timestamp
        """
        health = self._rpc.health()
        message = f"Last process: `{health['last_process_loc']}`\n"
        message += f"Initial bot start: `{health['bot_start_loc']}`\n"
        message += f"Last bot restart: `{health['bot_startup_loc']}`"
        await self._send_msg(message)

    @authorized_only
    async def _version(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /version.
        Show version information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        strategy_version = self._rpc._freqtrade.strategy.version()
        version_string = f"*Version:* `{__version__}`"
        if strategy_version is not None:
            version_string += f"\n*Strategy version: * `{strategy_version}`"

        await self._send_msg(version_string)

    @authorized_only
    async def _show_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /show_config.
        Show config information information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        val = RPC._rpc_show_config(self._config, self._rpc._freqtrade.state)

        if val["trailing_stop"]:
            sl_info = (
                f"*Initial Stoploss:* `{val['stoploss']}`\n"
                f"*Trailing stop positive:* `{val['trailing_stop_positive']}`\n"
                f"*Trailing stop offset:* `{val['trailing_stop_positive_offset']}`\n"
                f"*Only trail above offset:* `{val['trailing_only_offset_is_reached']}`\n"
            )

        else:
            sl_info = f"*Stoploss:* `{val['stoploss']}`\n"

        if val["position_adjustment_enable"]:
            pa_info = (
                f"*Position adjustment:* On\n"
                f"*Max enter position adjustment:* `{val['max_entry_position_adjustment']}`\n"
            )
        else:
            pa_info = "*Position adjustment:* Off\n"

        await self._send_msg(
            f"*Mode:* `{'Dry-run' if val['dry_run'] else 'Live'}`\n"
            f"*Exchange:* `{val['exchange']}{' (Demo)' if val['demo_trading'] else ''}`\n"
            f"*Market: * `{val['trading_mode']}`\n"
            f"*Stake per trade:* `{val['stake_amount']} {val['stake_currency']}`\n"
            f"*Max open Trades:* `{val['max_open_trades']}`\n"
            f"*Minimum ROI:* `{val['minimal_roi']}`\n"
            f"*Entry strategy:* ```\n{json.dumps(val['entry_pricing'])}```\n"
            f"*Exit strategy:* ```\n{json.dumps(val['exit_pricing'])}```\n"
            f"{sl_info}"
            f"{pa_info}"
            f"*Timeframe:* `{val['timeframe']}`\n"
            f"*Strategy:* `{val['strategy']}`\n"
            f"*Current state:* `{val['state']}`"
        )

    @authorized_only
    async def _list_custom_data(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /list_custom_data <id> <key>.
        List custom_data for specified trade (and key if supplied).
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            if not context.args or len(context.args) == 0:
                raise RPCException("Trade-id not set.")
            trade_id = int(context.args[0])
            key = None if len(context.args) < 2 else str(context.args[1])

            results = self._rpc._rpc_list_custom_data(trade_id, key)
            messages = []
            if len(results) > 0:
                trade_custom_data = results[0]["custom_data"]
                messages.append(
                    "Found custom-data entr" + ("ies: " if len(trade_custom_data) > 1 else "y: ")
                )
                for custom_data in trade_custom_data:
                    lines = [
                        f"*Key:* `{custom_data['key']}`",
                        f"*Type:* `{custom_data['type']}`",
                        f"*Value:* `{custom_data['value']}`",
                        f"*Create Date:* `{format_date(custom_data['created_at'])}`",
                        f"*Update Date:* `{format_date(custom_data['updated_at'])}`",
                    ]
                    # Filter empty lines using list-comprehension
                    messages.append("\n".join([line for line in lines if line]))
                for msg in messages:
                    if len(msg) > MAX_MESSAGE_LENGTH:
                        msg = "Message dropped because length exceeds "
                        msg += f"maximum allowed characters: {MAX_MESSAGE_LENGTH}"
                        logger.warning(msg)
                    await self._send_msg(msg)
            else:
                message = f"Didn't find any custom-data entries for Trade ID: `{trade_id}`"
                message += f" and Key: `{key}`." if key is not None else ""
                await self._send_msg(message)

        except RPCException as e:
            await self._send_msg(str(e))

    async def _update_msg(
        self,
        query: CallbackQuery,
        msg: str,
        callback_path: str = "",
        reload_able: bool = False,
        parse_mode: str = ParseMode.MARKDOWN,
    ) -> None:
        if reload_able:
            reply_markup = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Refresh", callback_data=callback_path)],
                ]
            )
        else:
            reply_markup = InlineKeyboardMarkup([[]])
        msg += f"\nUpdated: {datetime.now().ctime()}"
        if not query.message:
            return

        try:
            await query.edit_message_text(
                text=msg, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as e:
            if "not modified" in e.message.lower():
                pass
            else:
                logger.warning("TelegramError: %s", e.message)
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)

    async def _send_msg(
        self,
        msg: str,
        parse_mode: str = ParseMode.MARKDOWN,
        disable_notification: bool = False,
        keyboard: list[list[InlineKeyboardButton]] | None = None,
        callback_path: str = "",
        reload_able: bool = False,
        query: CallbackQuery | None = None,
    ) -> None:
        """
        Send given markdown message
        :param msg: message
        :param bot: alternative bot
        :param parse_mode: telegram parse mode
        :return: None
        """
        reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup
        if query:
            await self._update_msg(
                query=query,
                msg=msg,
                parse_mode=parse_mode,
                callback_path=callback_path,
                reload_able=reload_able,
            )
            return
        if reload_able and self._config["telegram"].get("reload", True):
            reply_markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Refresh", callback_data=callback_path)]]
            )
        else:
            if keyboard is not None:
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = ReplyKeyboardMarkup(self._keyboard, resize_keyboard=True)
        try:
            try:
                await self._app.bot.send_message(
                    self._config["telegram"]["chat_id"],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                    message_thread_id=self._config["telegram"].get("topic_id"),
                )
            except NetworkError as network_err:
                # Sometimes the telegram server resets the current connection,
                # if this is the case we send the message again.
                logger.warning(
                    "Telegram NetworkError: %s! Trying one more time.", network_err.message
                )
                await self._app.bot.send_message(
                    self._config["telegram"]["chat_id"],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                    message_thread_id=self._config["telegram"].get("topic_id"),
                )
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)

    @authorized_only
    async def _changemarketdir(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /marketdir.
        Updates the bot's market_direction
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if context.args and len(context.args) == 1:
            new_market_dir_arg = context.args[0]
            old_market_dir = self._rpc._get_market_direction()
            new_market_dir = None
            if new_market_dir_arg == "long":
                new_market_dir = MarketDirection.LONG
            elif new_market_dir_arg == "short":
                new_market_dir = MarketDirection.SHORT
            elif new_market_dir_arg == "even":
                new_market_dir = MarketDirection.EVEN
            elif new_market_dir_arg == "none":
                new_market_dir = MarketDirection.NONE

            if new_market_dir is not None:
                self._rpc._update_market_direction(new_market_dir)
                await self._send_msg(
                    "Successfully updated market direction"
                    f" from *{old_market_dir}* to *{new_market_dir}*."
                )
            else:
                raise RPCException(
                    "Invalid market direction provided. \n"
                    "Valid market directions: *long, short, even, none*"
                )
        elif context.args is not None and len(context.args) == 0:
            old_market_dir = self._rpc._get_market_direction()
            await self._send_msg(f"Currently set market direction: *{old_market_dir}*")
        else:
            raise RPCException(
                "Invalid usage of command /marketdir. \n"
                "Usage: */marketdir [short | long | even | none]*"
            )

    async def _tg_info(self, update: Update, context: CallbackContext) -> None:
        """
        Intentionally unauthenticated Handler for /tg_info.
        Returns information about the current telegram chat - even if chat_id does not
        correspond to this chat.

        :param update: message update
        :return: None
        """
        if not update.message:
            return
        chat_id = update.message.chat_id
        topic_id = update.message.message_thread_id
        user_id = (
            update.effective_user.id if topic_id is not None and update.effective_user else None
        )

        msg = f"""Freqtrade Bot Info:
        ```json
            {{
                "enabled": true,
                "token": "********",
                "chat_id": "{chat_id}",
                {f'"topic_id": "{topic_id}",' if topic_id else ""}
                {f'//"authorized_users": ["{user_id}"]' if topic_id and user_id else ""}
            }}
        ```
        """
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN_V2,
                message_thread_id=topic_id,
            )
        except TelegramError as telegram_err:
            logger.warning("TelegramError: %s! Giving up on that message.", telegram_err.message)
