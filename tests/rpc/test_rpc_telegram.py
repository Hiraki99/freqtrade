# pragma pylint: disable=missing-docstring, C0103
# pragma pylint: disable=protected-access, unused-argument, invalid-name
# pragma pylint: disable=too-many-lines, too-many-arguments

import asyncio
import logging
import re
import threading
from datetime import timedelta
from functools import reduce
from random import choice, randint
from string import ascii_uppercase
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
import time_machine
from pandas import DataFrame
from sqlalchemy import select
from telegram import Chat, Message, ReplyKeyboardMarkup, Update, User
from telegram.error import BadRequest, NetworkError, TelegramError

from freqtrade import __version__
from freqtrade.constants import CANCEL_REASON
from freqtrade.enums import (
    ExitType,
    MarketDirection,
    RPCMessageType,
    RunMode,
    SignalDirection,
    State,
)
from freqtrade.exceptions import OperationalException
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.loggers import setup_logging
from freqtrade.persistence import PairLocks, Trade
from freqtrade.persistence.models import Order
from freqtrade.rpc import RPC
from freqtrade.rpc.rpc import RPCException
from freqtrade.rpc.telegram import Telegram, authorized_only
from freqtrade.util.datetime_helpers import dt_now
from tests.conftest import (
    CURRENT_TEST_STRATEGY,
    EXMS,
    create_mock_trades,
    create_mock_trades_usdt,
    get_patched_freqtradebot,
    log_has,
    log_has_re,
    patch_exchange,
    patch_get_signal,
    patch_whitelist,
)


@pytest.fixture(autouse=True)
def mock_exchange_loop(mocker):
    mocker.patch("freqtrade.exchange.exchange.Exchange._init_async_loop")


@pytest.fixture
def default_conf(default_conf) -> dict:
    # Telegram is enabled by default
    default_conf["telegram"]["enabled"] = True
    return default_conf


@pytest.fixture
def update():
    message = Message(
        0,
        dt_now(),
        Chat(1235, 0),
        from_user=User(5432, "test", is_bot=False),
    )
    _update = Update(0, message=message)

    return _update


def patch_eventloop_threading(telegrambot):
    init_event = threading.Event()

    def thread_fuck():
        telegrambot._loop = asyncio.new_event_loop()
        init_event.set()
        telegrambot._loop.run_forever()

    x = threading.Thread(target=thread_fuck, daemon=True)
    x.start()
    # Wait for thread to be properly initialized with timeout
    if not init_event.wait(timeout=5.0):
        raise RuntimeError("Failed to initialize event loop thread")


class DummyCls(Telegram):
    """
    Dummy class for testing the Telegram @authorized_only decorator
    """

    def __init__(self, rpc: RPC, config) -> None:
        super().__init__(rpc, config)
        self.state = {"called": False}

    def _init(self):
        pass

    @authorized_only
    async def dummy_handler(self, *args, **kwargs) -> None:
        """
        Fake method that only change the state of the object
        """
        self.state["called"] = True

    @authorized_only
    async def dummy_exception(self, *args, **kwargs) -> None:
        """
        Fake method that throw an exception
        """
        raise Exception("test")


def get_telegram_testobject(mocker, default_conf, mock=True, ftbot=None, mock_fiat=True):
    msg_mock = AsyncMock()
    if mock:
        mocker.patch.multiple(
            "freqtrade.rpc.telegram.Telegram",
            _init=MagicMock(),
            _send_msg=msg_mock,
            _start_thread=MagicMock(),
        )
    if not ftbot:
        ftbot = get_patched_freqtradebot(mocker, default_conf)
    rpc = RPC(ftbot)
    if rpc._fiat_converter is not None and mock_fiat:
        mocker.patch.object(rpc._fiat_converter, "get_price", return_value=1.1)

    telegram = Telegram(rpc, default_conf)
    telegram._loop = MagicMock()
    patch_eventloop_threading(telegram)

    return telegram, ftbot, msg_mock


def test_telegram__init__(default_conf, mocker) -> None:
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())

    telegram, _, _ = get_telegram_testobject(mocker, default_conf)
    assert telegram._config == default_conf


def test_telegram_init(default_conf, mocker, caplog) -> None:
    app_mock = MagicMock()
    mocker.patch("freqtrade.rpc.telegram.Telegram._start_thread", MagicMock())
    mocker.patch("freqtrade.rpc.telegram.Telegram._init_telegram_app", return_value=app_mock)
    mocker.patch("freqtrade.rpc.telegram.Telegram._startup_telegram", AsyncMock())

    telegram, _, _ = get_telegram_testobject(mocker, default_conf, mock=False)
    telegram._init()
    assert app_mock.call_count == 0

    # number of handles registered
    assert app_mock.add_handler.call_count > 0
    # assert start_polling.start_polling.call_count == 1

    message_str = (
        "rpc.telegram is listening for following commands: [['status'], ['profit'], "
        "['balance'], ['start'], ['stop'], "
        "['forceexit', 'forcesell', 'fx'], ['forcebuy', 'forcelong'], ['forceshort'], "
        "['reload_trade'], ['trades'], ['delete'], ['cancel_open_order', 'coo'], "
        "['buys', 'entries'], ['exits', 'sells'], ['mix_tags'], "
        "['stats'], ['daily'], ['analysis'], ['smc'], ['scalp'], ['weekly'], ['monthly'], "
        "['locks'], ['delete_locks', 'unlock'], "
        "['reload_conf', 'reload_config'], ['show_conf', 'show_config'], "
        "['pause', 'stopbuy', 'stopentry'], ['whitelist'], ['blacklist'], "
        "['bl_delete', 'blacklist_delete'], "
        "['logs'], ['health'], ['help'], ['version'], ['marketdir'], "
        "['order'], ['list_custom_data'], ['tg_info'], ['profit_long'], ['profit_short']]"
    )

    assert log_has(message_str, caplog)


async def test_telegram_startup(default_conf, mocker, caplog) -> None:
    app_mock = MagicMock()
    app_mock.initialize = AsyncMock()
    app_mock.start = AsyncMock()
    app_mock.stop = AsyncMock()
    app_mock.shutdown = AsyncMock()
    app_mock.updater.start_polling = AsyncMock()
    app_mock.updater.stop = AsyncMock()
    app_mock.updater.running = False

    telegram, _, _ = get_telegram_testobject(mocker, default_conf)
    telegram._app = app_mock
    telegram._shutdown_event = asyncio.Event()
    await telegram._startup_telegram()
    assert app_mock.initialize.call_count == 1
    assert app_mock.start.call_count == 1
    assert app_mock.updater.start_polling.call_count == 1
    # updater.running == False -> thoát vòng chờ và teardown đầy đủ trong loop.
    assert app_mock.shutdown.call_count == 1

    # Test telegram Retries and Exceptions
    app_mock.start = AsyncMock(side_effect=Exception("Test exception"))
    await telegram._startup_telegram()
    assert app_mock.start.call_count == 3
    assert log_has("Telegram init failed.", caplog)


async def test_telegram_cleanup(
    default_conf,
    mocker,
) -> None:
    telegram, _, _ = get_telegram_testobject(mocker, default_conf)
    telegram._loop = asyncio.get_running_loop()
    telegram._thread = MagicMock()
    telegram._shutdown_event = asyncio.Event()

    telegram.cleanup()
    await asyncio.sleep(0.1)
    # cleanup() chỉ báo dừng + join; teardown (stop/shutdown) chạy trong
    # _startup_telegram để hoàn tất trước khi loop dừng.
    assert telegram._shutdown_event.is_set()
    assert telegram._thread.join.call_count == 1


async def test_authorized_only(default_conf, mocker, caplog, update) -> None:
    patch_exchange(mocker)
    caplog.set_level(logging.DEBUG)
    default_conf["telegram"]["enabled"] = False
    bot = FreqtradeBot(default_conf)
    rpc = RPC(bot)
    dummy = DummyCls(rpc, default_conf)

    patch_get_signal(bot)
    await dummy.dummy_handler(update=update, context=MagicMock())
    assert dummy.state["called"] is True
    assert log_has("Executing handler: dummy_handler for chat_id: 1235", caplog)
    assert not log_has("Rejected unauthorized message from: 1235", caplog)
    assert not log_has("Exception occurred within Telegram module", caplog)


async def test_authorized_only_unauthorized(default_conf, mocker, caplog) -> None:
    patch_exchange(mocker)
    caplog.set_level(logging.DEBUG)
    message = Message(
        randint(1, 100),
        dt_now(),
        Chat(0xDEADBEEF, 0),
        from_user=User(5432, "test", is_bot=False),
    )
    update = Update(randint(1, 100), message=message)

    default_conf["telegram"]["enabled"] = False
    bot = FreqtradeBot(default_conf)
    rpc = RPC(bot)
    dummy = DummyCls(rpc, default_conf)

    patch_get_signal(bot)
    await dummy.dummy_handler(update=update, context=MagicMock())
    assert dummy.state["called"] is False
    assert not log_has("Executing handler: dummy_handler for chat_id: 3735928559", caplog)
    assert log_has("Rejected unauthorized message from: 3735928559", caplog)
    assert not log_has("Exception occurred within Telegram module", caplog)


async def test_authorized_users(default_conf, mocker, caplog, update) -> None:
    patch_exchange(mocker)
    caplog.set_level(logging.DEBUG)
    default_conf["telegram"]["enabled"] = False
    default_conf["telegram"]["authorized_users"] = ["5432"]
    bot = FreqtradeBot(default_conf)
    rpc = RPC(bot)
    dummy = DummyCls(rpc, default_conf)

    await dummy.dummy_handler(update=update, context=MagicMock())
    assert dummy.state["called"] is True
    assert log_has("Executing handler: dummy_handler for chat_id: 1235", caplog)
    caplog.clear()
    # Test empty case
    default_conf["telegram"]["authorized_users"] = []
    dummy1 = DummyCls(rpc, default_conf)
    await dummy1.dummy_handler(update=update, context=MagicMock())
    assert dummy1.state["called"] is False
    assert log_has_re(r"Unauthorized user tried to .*5432", caplog)
    caplog.clear()
    # Test wrong user
    default_conf["telegram"]["authorized_users"] = ["1234"]
    dummy1 = DummyCls(rpc, default_conf)
    await dummy1.dummy_handler(update=update, context=MagicMock())
    assert dummy1.state["called"] is False
    assert log_has_re(r"Unauthorized user tried to .*5432", caplog)
    caplog.clear()

    # Test reverse case again
    default_conf["telegram"]["authorized_users"] = ["5432"]
    dummy1 = DummyCls(rpc, default_conf)
    await dummy1.dummy_handler(update=update, context=MagicMock())
    assert dummy1.state["called"] is True
    assert not log_has_re(r"Unauthorized user tried to .*5432", caplog)


async def test_authorized_only_exception(default_conf, mocker, caplog, update) -> None:
    patch_exchange(mocker)

    default_conf["telegram"]["enabled"] = False

    bot = FreqtradeBot(default_conf)
    rpc = RPC(bot)
    dummy = DummyCls(rpc, default_conf)
    patch_get_signal(bot)

    await dummy.dummy_exception(update=update, context=MagicMock())
    assert dummy.state["called"] is False
    assert not log_has("Executing handler: dummy_handler for chat_id: 0", caplog)
    assert not log_has("Rejected unauthorized message from: 0", caplog)
    assert log_has("Exception occurred within Telegram module", caplog)


async def test_telegram_status(default_conf, update, mocker) -> None:
    default_conf["telegram"]["enabled"] = False

    status_table = MagicMock()
    mocker.patch("freqtrade.rpc.telegram.Telegram._status_table", status_table)

    mocker.patch.multiple(
        "freqtrade.rpc.rpc.RPC",
        _rpc_trade_status=MagicMock(
            return_value=[
                {
                    "trade_id": 1,
                    "pair": "ETH/BTC",
                    "base_currency": "ETH",
                    "quote_currency": "BTC",
                    "open_date": dt_now(),
                    "close_date": None,
                    "open_rate": 1.099e-05,
                    "close_rate": None,
                    "current_rate": 1.098e-05,
                    "amount": 90.99181074,
                    "stake_amount": 90.99181074,
                    "max_stake_amount": 90.99181074,
                    "buy_tag": None,
                    "enter_tag": None,
                    "close_profit_ratio": None,
                    "profit": -0.0059,
                    "profit_ratio": -0.0059,
                    "profit_abs": -0.225,
                    "realized_profit": 0.0,
                    "total_profit_abs": -0.225,
                    "initial_stop_loss_abs": 1.098e-05,
                    "stop_loss_abs": 1.099e-05,
                    "exit_order_status": None,
                    "initial_stop_loss_ratio": -0.0005,
                    "stoploss_current_dist": 1e-08,
                    "stoploss_current_dist_ratio": -0.0002,
                    "stop_loss_ratio": -0.0001,
                    "open_order": "(limit buy rem=0.00000000)",
                    "is_open": True,
                    "is_short": False,
                    "filled_entry_orders": [],
                    "orders": [],
                }
            ]
        ),
    )

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._status(update=update, context=MagicMock())
    assert msg_mock.call_count == 1

    context = MagicMock()
    # /status table
    context.args = ["table"]
    await telegram._status(update=update, context=context)
    assert status_table.call_count == 1


@pytest.mark.usefixtures("init_persistence")
async def test_telegram_status_multi_entry(default_conf, update, mocker, fee) -> None:
    default_conf["telegram"]["enabled"] = False
    default_conf["position_adjustment_enable"] = True
    mocker.patch.multiple(
        EXMS,
        fetch_order=MagicMock(return_value=None),
        get_rate=MagicMock(return_value=0.22),
    )

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    create_mock_trades(fee)
    trades = Trade.get_open_trades()
    trade = trades[3]
    # Average may be empty on some exchanges
    trade.orders[0].average = 0
    trade.orders.append(
        Order(
            order_id="5412vbb",
            ft_order_side="buy",
            ft_pair=trade.pair,
            ft_is_open=False,
            ft_amount=trade.amount,
            ft_price=trade.open_rate,
            status="closed",
            symbol=trade.pair,
            order_type="market",
            side="buy",
            price=trade.open_rate * 0.95,
            average=0,
            filled=trade.amount,
            remaining=0,
            cost=trade.amount,
            order_date=trade.open_date,
            order_filled_date=trade.open_date,
        )
    )
    trade.recalc_trade_from_orders()
    Trade.commit()

    await telegram._status(update=update, context=MagicMock())
    assert msg_mock.call_count == 4
    msg = msg_mock.call_args_list[3][0][0]
    assert re.search(r"Number of Entries.*2", msg)
    # Exit order is still open, hence not a successful exit
    assert re.search(r"Number of Exits.*0", msg)
    assert re.search(r"Close Date:", msg) is None
    assert re.search(r"Close Profit:", msg) is None


@pytest.mark.usefixtures("init_persistence")
async def test_telegram_status_closed_trade(default_conf, update, mocker, fee) -> None:
    default_conf["position_adjustment_enable"] = True
    mocker.patch.multiple(
        EXMS,
        fetch_order=MagicMock(return_value=None),
        get_rate=MagicMock(return_value=0.22),
    )

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    create_mock_trades(fee)
    trade = Trade.get_trades([Trade.is_open.is_(False)]).first()
    context = MagicMock()
    context.args = [str(trade.id)]
    await telegram._status(update=update, context=context)
    assert msg_mock.call_count == 1
    msg = msg_mock.call_args_list[0][0][0]
    assert re.search(r"Close Date:", msg)
    assert re.search(r"Close Profit:", msg)


async def test_order_handle(default_conf, update, ticker, fee, mocker) -> None:
    default_conf["max_open_trades"] = 3
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )
    status_table = MagicMock()
    mocker.patch.multiple(
        "freqtrade.rpc.telegram.Telegram",
        _status_table=status_table,
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    patch_get_signal(freqtradebot)

    freqtradebot.state = State.RUNNING
    msg_mock.reset_mock()

    # Create some test data
    freqtradebot.enter_positions()

    mocker.patch("freqtrade.rpc.telegram.MAX_MESSAGE_LENGTH", 500)

    msg_mock.reset_mock()
    context = MagicMock()
    context.args = ["2"]
    await telegram._order(update=update, context=context)

    assert msg_mock.call_count == 1

    msg1 = msg_mock.call_args_list[0][0][0]

    assert "Order List for Trade #*`2`" in msg1

    msg_mock.reset_mock()
    mocker.patch("freqtrade.rpc.telegram.MAX_MESSAGE_LENGTH", 50)
    context = MagicMock()
    context.args = ["2"]
    await telegram._order(update=update, context=context)

    assert msg_mock.call_count == 2

    msg1 = msg_mock.call_args_list[0][0][0]
    msg2 = msg_mock.call_args_list[1][0][0]

    assert "Order List for Trade #*`2`" in msg1
    assert "*Order List for Trade #*`2` - continued" in msg2


@pytest.mark.usefixtures("init_persistence")
async def test_telegram_order_multi_entry(default_conf, update, mocker, fee) -> None:
    default_conf["telegram"]["enabled"] = False
    default_conf["position_adjustment_enable"] = True
    mocker.patch.multiple(
        EXMS,
        fetch_order=MagicMock(return_value=None),
        get_rate=MagicMock(return_value=0.22),
    )

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    create_mock_trades(fee)
    trades = Trade.get_open_trades()
    trade = trades[3]
    # Average may be empty on some exchanges
    trade.orders[0].average = 0
    trade.orders.append(
        Order(
            order_id="5412vbb",
            ft_order_side="buy",
            ft_pair=trade.pair,
            ft_is_open=False,
            ft_amount=trade.amount,
            ft_price=trade.open_rate,
            status="closed",
            symbol=trade.pair,
            order_type="market",
            side="buy",
            price=trade.open_rate * 0.95,
            average=0,
            filled=trade.amount,
            remaining=0,
            cost=trade.amount,
            order_date=trade.open_date,
            order_filled_date=trade.open_date,
        )
    )
    trade.recalc_trade_from_orders()
    Trade.commit()

    await telegram._order(update=update, context=MagicMock())
    assert msg_mock.call_count == 4
    msg = msg_mock.call_args_list[3][0][0]
    assert re.search(r"from 1st entry rate", msg)
    assert re.search(r"Order Filled", msg)


async def test_status_handle(default_conf, update, ticker, fee, mocker) -> None:
    default_conf["max_open_trades"] = 3
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )
    status_table = MagicMock()
    mocker.patch.multiple(
        "freqtrade.rpc.telegram.Telegram",
        _status_table=status_table,
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    patch_get_signal(freqtradebot)

    freqtradebot.state = State.STOPPED
    # Status is also enabled when stopped
    await telegram._status(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "no active trade" in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    freqtradebot.state = State.RUNNING
    await telegram._status(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "no active trade" in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Create some test data
    freqtradebot.enter_positions()
    # Trigger status while we have a fulfilled order for the open trade
    await telegram._status(update=update, context=MagicMock())

    # close_rate should not be included in the message as the trade is not closed
    # and no line should be empty
    lines = msg_mock.call_args_list[0][0][0].split("\n")
    assert "" not in lines[:-1]
    assert "Close Rate" not in "".join(lines)
    assert "Close Profit" not in "".join(lines)

    assert msg_mock.call_count == 3
    assert "ETH/BTC" in msg_mock.call_args_list[0][0][0]
    assert "LTC/BTC" in msg_mock.call_args_list[1][0][0]

    msg_mock.reset_mock()
    context = MagicMock()
    context.args = ["2", "3"]

    await telegram._status(update=update, context=context)

    lines = msg_mock.call_args_list[0][0][0].split("\n")
    assert "" not in lines[:-1]
    assert "Close Rate" not in "".join(lines)
    assert "Close Profit" not in "".join(lines)

    assert msg_mock.call_count == 2
    assert "LTC/BTC" in msg_mock.call_args_list[0][0][0]

    mocker.patch("freqtrade.rpc.telegram.MAX_MESSAGE_LENGTH", 500)

    msg_mock.reset_mock()
    context = MagicMock()
    context.args = ["2"]
    await telegram._status(update=update, context=context)

    assert msg_mock.call_count == 1

    msg1 = msg_mock.call_args_list[0][0][0]

    assert "Close Rate" not in msg1
    assert "Trade ID:* `2`" in msg1


async def test_status_table_handle(default_conf, update, ticker, fee, mocker) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )

    default_conf["stake_amount"] = 15.0

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    patch_get_signal(freqtradebot)

    freqtradebot.state = State.STOPPED
    # Status table is also enabled when stopped
    await telegram._status_table(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "no active trade" in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    freqtradebot.state = State.RUNNING
    await telegram._status_table(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "no active trade" in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Create some test data
    freqtradebot.enter_positions()

    await telegram._status_table(update=update, context=MagicMock())

    text = re.sub("</?pre>", "", msg_mock.call_args_list[-1][0][0])
    line = text.split("\n")
    fields = re.sub("[ ]+", " ", line[2].strip()).split(" ")

    assert int(fields[0]) == 1
    # assert 'L' in fields[1]
    assert "ETH/BTC" in fields[1]
    assert msg_mock.call_count == 1


async def test_daily_handle(default_conf_usdt, update, ticker, fee, mocker, time_machine) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )

    telegram, _freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)

    # Move date to within day
    time_machine.move_to("2022-06-11 08:00:00+00:00")
    # Create some test data
    create_mock_trades_usdt(fee)

    # Try valid data
    # /daily 2
    context = MagicMock()
    context.args = ["2"]
    await telegram._daily(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Daily Profit over the last 2 days</b>:" in msg_mock.call_args_list[0][0][0]
    assert "Day " in msg_mock.call_args_list[0][0][0]
    assert str(dt_now().date()) in msg_mock.call_args_list[0][0][0]
    assert "  6.83 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  7.51 USD" in msg_mock.call_args_list[0][0][0]
    assert "(2)" in msg_mock.call_args_list[0][0][0]
    assert "(2)  6.83 USDT  7.51 USD  0.64%" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Reset msg_mock
    msg_mock.reset_mock()
    context.args = []
    await telegram._daily(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Daily Profit over the last 7 days</b>:" in msg_mock.call_args_list[0][0][0]
    assert str(dt_now().date()) in msg_mock.call_args_list[0][0][0]
    assert str((dt_now() - timedelta(days=5)).date()) in msg_mock.call_args_list[0][0][0]
    assert "  6.83 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  7.51 USD" in msg_mock.call_args_list[0][0][0]
    assert "(2)" in msg_mock.call_args_list[0][0][0]
    assert "(1)" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Reset msg_mock
    msg_mock.reset_mock()

    # /daily 1
    context = MagicMock()
    context.args = ["1"]
    await telegram._daily(update=update, context=context)
    assert "  6.83 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  7.51 USD" in msg_mock.call_args_list[0][0][0]
    assert "(2)" in msg_mock.call_args_list[0][0][0]


async def test_daily_wrong_input(default_conf, update, ticker, mocker) -> None:
    mocker.patch.multiple(EXMS, fetch_ticker=ticker)

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /daily -2
    context = MagicMock()
    context.args = ["-2"]
    await telegram._daily(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "must be an integer greater than 0" in msg_mock.call_args_list[0][0][0]

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /daily today
    context = MagicMock()
    context.args = ["today"]
    await telegram._daily(update=update, context=context)
    assert "Daily Profit over the last 7 days</b>:" in msg_mock.call_args_list[0][0][0]


async def test_weekly_handle(default_conf_usdt, update, ticker, fee, mocker, time_machine) -> None:
    default_conf_usdt["max_open_trades"] = 1
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    # Move to saturday - so all trades are within that week
    time_machine.move_to("2022-06-11")
    create_mock_trades_usdt(fee)

    # Try valid data
    # /weekly 2
    context = MagicMock()
    context.args = ["2"]
    await telegram._weekly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert (
        "Weekly Profit over the last 2 weeks (starting from Monday)</b>:"
        in msg_mock.call_args_list[0][0][0]
    )
    assert "Monday " in msg_mock.call_args_list[0][0][0]
    today = dt_now().date()
    first_iso_day_of_current_week = today - timedelta(days=today.weekday())
    assert str(first_iso_day_of_current_week) in msg_mock.call_args_list[0][0][0]
    assert "  2.74 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  3.01 USD" in msg_mock.call_args_list[0][0][0]
    assert "(3)" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Reset msg_mock
    msg_mock.reset_mock()
    context.args = []
    await telegram._weekly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert (
        "Weekly Profit over the last 8 weeks (starting from Monday)</b>:"
        in msg_mock.call_args_list[0][0][0]
    )
    assert "Weekly" in msg_mock.call_args_list[0][0][0]
    assert "  2.74 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  3.01 USD" in msg_mock.call_args_list[0][0][0]
    assert "(3)" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /weekly -3
    context = MagicMock()
    context.args = ["-3"]
    await telegram._weekly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "must be an integer greater than 0" in msg_mock.call_args_list[0][0][0]

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /weekly this week
    context = MagicMock()
    context.args = ["this week"]
    await telegram._weekly(update=update, context=context)
    assert (
        "Weekly Profit over the last 8 weeks (starting from Monday)</b>:"
        in msg_mock.call_args_list[0][0][0]
    )


async def test_monthly_handle(default_conf_usdt, update, ticker, fee, mocker, time_machine) -> None:
    default_conf_usdt["max_open_trades"] = 1
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    # Move to day within the month so all mock trades fall into this week.
    time_machine.move_to("2022-06-11")
    create_mock_trades_usdt(fee)

    # Try valid data
    # /monthly 2
    context = MagicMock()
    context.args = ["2"]
    await telegram._monthly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Monthly Profit over the last 2 months</b>:" in msg_mock.call_args_list[0][0][0]
    assert "Month " in msg_mock.call_args_list[0][0][0]
    today = dt_now().date()
    current_month = f"{today.year}-{today.month:02} "
    assert current_month in msg_mock.call_args_list[0][0][0]
    assert "  2.74 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  3.01 USD" in msg_mock.call_args_list[0][0][0]
    assert "(3)" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Reset msg_mock
    msg_mock.reset_mock()
    context.args = []
    await telegram._monthly(update=update, context=context)
    assert msg_mock.call_count == 1
    # Default to 6 months
    assert "Monthly Profit over the last 6 months</b>:" in msg_mock.call_args_list[0][0][0]
    assert "Month " in msg_mock.call_args_list[0][0][0]
    assert current_month in msg_mock.call_args_list[0][0][0]
    assert "  2.74 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  3.01 USD" in msg_mock.call_args_list[0][0][0]
    assert "(3)" in msg_mock.call_args_list[0][0][0]
    assert "(0)" in msg_mock.call_args_list[0][0][0]

    # Reset msg_mock
    msg_mock.reset_mock()

    # /monthly 12
    context = MagicMock()
    context.args = ["12"]
    await telegram._monthly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Monthly Profit over the last 12 months</b>:" in msg_mock.call_args_list[0][0][0]
    assert "  2.74 USDT" in msg_mock.call_args_list[0][0][0]
    assert "  3.01 USD" in msg_mock.call_args_list[0][0][0]
    assert "(3)" in msg_mock.call_args_list[0][0][0]

    # The one-digit months should contain a zero, Eg: September 2021 = "2021-09"
    # Since we loaded the last 12 months, any month should appear
    assert "-09" in msg_mock.call_args_list[0][0][0]

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /monthly -3
    context = MagicMock()
    context.args = ["-3"]
    await telegram._monthly(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "must be an integer greater than 0" in msg_mock.call_args_list[0][0][0]

    # Try invalid data
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /monthly february
    context = MagicMock()
    context.args = ["february"]
    await telegram._monthly(update=update, context=context)
    assert "Monthly Profit over the last 6 months</b>:" in msg_mock.call_args_list[0][0][0]


async def test_telegram_profit_handle(
    default_conf_usdt, update, ticker_usdt, ticker_sell_up, fee, limit_sell_order_usdt, mocker
) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker_usdt,
        get_fee=fee,
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    patch_get_signal(freqtradebot)

    await telegram._profit(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "No trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Create some test data
    freqtradebot.enter_positions()
    trade = Trade.session.scalars(select(Trade)).first()

    context = MagicMock()
    # Test with invalid 2nd argument (should silently pass)
    context.args = ["aaa"]
    await telegram._profit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "No closed trade" in msg_mock.call_args_list[-1][0][0]
    assert "*ROI:* All trades" in msg_mock.call_args_list[-1][0][0]
    mocker.patch("freqtrade.wallets.Wallets.get_starting_balance", return_value=1000)
    assert (
        "∙ `0.298 USDT (0.50%) (0.03 \N{GREEK CAPITAL LETTER SIGMA}%)`"
        in msg_mock.call_args_list[-1][0][0]
    )
    msg_mock.reset_mock()

    # Update the ticker with a market going up
    mocker.patch(f"{EXMS}.fetch_ticker", ticker_sell_up)
    # Simulate fulfilled LIMIT_SELL order for trade
    trade = Trade.session.scalars(select(Trade)).first()
    oobj = Order.parse_from_ccxt_object(
        limit_sell_order_usdt, limit_sell_order_usdt["symbol"], "sell"
    )
    trade.orders.append(oobj)
    trade.update_trade(oobj)

    trade.close_date = dt_now()
    trade.is_open = False
    Trade.commit()

    context.args = ["3"]
    await telegram._profit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "*ROI:* Closed trades" in msg_mock.call_args_list[-1][0][0]
    assert (
        "∙ `5.685 USDT (9.45%) (0.57 \N{GREEK CAPITAL LETTER SIGMA}%)`"
        in msg_mock.call_args_list[-1][0][0]
    )
    assert "∙ `6.253 USD`" in msg_mock.call_args_list[-1][0][0]
    assert "*ROI:* All trades" in msg_mock.call_args_list[-1][0][0]
    assert (
        "∙ `5.685 USDT (9.45%) (0.57 \N{GREEK CAPITAL LETTER SIGMA}%)`"
        in msg_mock.call_args_list[-1][0][0]
    )
    assert "∙ `6.253 USD`" in msg_mock.call_args_list[-1][0][0]

    assert "*Best Performing:* `ETH/USDT: 5.685 USDT (9.47%)`" in msg_mock.call_args_list[-1][0][0]
    assert "*Max Drawdown:*" in msg_mock.call_args_list[-1][0][0]
    assert "*Profit factor:*" in msg_mock.call_args_list[-1][0][0]
    assert "*Winrate:*" in msg_mock.call_args_list[-1][0][0]
    assert "*Expectancy (Ratio):*" in msg_mock.call_args_list[-1][0][0]
    assert "*Trading volume:* `126 USDT`" in msg_mock.call_args_list[-1][0][0]


@pytest.mark.asyncio
async def test_telegram_profit_long_short_handle(
    default_conf_usdt, update, ticker_usdt, fee, mocker
):
    """
    Test the /profit_long and /profit_short commands to ensure the output content
    is consistent with /profit, covering both no trades and trades present cases.
    """

    mocker.patch.multiple(EXMS, fetch_ticker=ticker_usdt, get_fee=fee)
    telegram, _freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)

    # When there are no trades
    await telegram._profit_long(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "No long trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Test support with "/profit long"
    context = MagicMock()
    context.args = ["long"]
    await telegram._profit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "No long trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    await telegram._profit_short(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "No short trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Test support with "/profit short"
    context = MagicMock()
    context.args = ["short"]
    await telegram._profit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "No short trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # When there are trades
    create_mock_trades_usdt(fee)

    # Keep only long trades
    for t in Trade.get_trades_proxy():
        t.is_short = False
    Trade.commit()
    await telegram._profit_long(update=update, context=MagicMock())
    msg = msg_mock.call_args_list[0][0][0]
    assert "*ROI:* Closed long trades" in msg
    assert "*ROI:* All long trades" in msg
    assert "*Total Trade Count:*" in msg
    assert "*Winrate:*" in msg
    assert "*Expectancy (Ratio):*" in msg
    assert "*Best Performing:*" in msg
    assert "*Profit factor:*" in msg
    assert "*Max Drawdown:*" in msg
    assert "*Current Drawdown:*" in msg
    msg_mock.reset_mock()

    # Keep only short trades
    for t in Trade.get_trades_proxy():
        t.is_short = True
    Trade.commit()
    await telegram._profit_short(update=update, context=MagicMock())
    msg = msg_mock.call_args_list[0][0][0]
    assert "*ROI:* Closed short trades" in msg
    assert "*ROI:* All short trades" in msg
    assert "*Total Trade Count:*" in msg
    assert "*Winrate:*" in msg
    assert "*Expectancy (Ratio):*" in msg
    assert "*Best Performing:*" in msg
    assert "*Profit factor:*" in msg
    assert "*Max Drawdown:*" in msg
    assert "*Current Drawdown:*" in msg
    msg_mock.reset_mock()

    # Test parameter passing
    context = MagicMock()
    context.args = ["2"]
    await telegram._profit_long(update=update, context=context)
    assert msg_mock.call_count == 1
    await telegram._profit_short(update=update, context=context)
    assert msg_mock.call_count == 2


@pytest.mark.parametrize("is_short", [True, False])
async def test_telegram_stats(default_conf, update, ticker, fee, mocker, is_short) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    await telegram._stats(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "No trades yet." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Create some test data
    create_mock_trades(fee, is_short=is_short)

    await telegram._stats(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "Exit Reason" in msg_mock.call_args_list[-1][0][0]
    assert "ROI" in msg_mock.call_args_list[-1][0][0]
    assert "Avg. Duration" in msg_mock.call_args_list[-1][0][0]
    # Duration is not only N/A
    assert "0:19:00" in msg_mock.call_args_list[-1][0][0]
    assert "N/A" in msg_mock.call_args_list[-1][0][0]
    msg_mock.reset_mock()


async def test_telegram_balance_handle(default_conf, update, mocker, rpc_balance, tickers) -> None:
    default_conf["dry_run"] = False
    mocker.patch(f"{EXMS}.get_balances", return_value=rpc_balance)
    mocker.patch(f"{EXMS}.get_tickers", tickers)
    mocker.patch(f"{EXMS}.get_valid_pair_combination", side_effect=lambda a, b: [f"{a}/{b}"])

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    await telegram._balance(update=update, context=MagicMock())
    context = MagicMock()
    context.args = ["full"]
    await telegram._balance(update=update, context=context)
    result = msg_mock.call_args_list[0][0][0]
    result_full = msg_mock.call_args_list[1][0][0]
    assert msg_mock.call_count == 2
    assert "*BTC:*" in result
    assert "*ETH:*" not in result
    assert "*USDT:*" not in result
    assert "*EUR:*" not in result
    assert "*LTC:*" not in result

    assert "*LTC:*" in result_full
    assert "*XRP:*" not in result
    assert "Balance:" in result
    assert "Est. BTC:" in result
    assert "BTC: 11" in result
    assert "BTC: 12" in result_full
    assert "*3 Other Currencies (< 0.0001 BTC):*" in result
    assert "BTC: 0.00000309" in result
    assert "*Estimated Value*:" in result_full
    assert "*Estimated Value (Bot managed assets only)*:" in result


async def test_telegram_balance_handle_futures(
    default_conf, update, rpc_balance, mocker, tickers
) -> None:
    default_conf.update(
        {
            "dry_run": False,
            "trading_mode": "futures",
            "margin_mode": "isolated",
        }
    )
    mock_pos = [
        {
            "symbol": "ETH/USDT:USDT",
            "timestamp": None,
            "datetime": None,
            "initialMargin": 0.0,
            "initialMarginPercentage": None,
            "maintenanceMargin": 0.0,
            "maintenanceMarginPercentage": 0.005,
            "entryPrice": 0.0,
            "notional": 10.0,
            "leverage": 5.0,
            "unrealizedPnl": 0.0,
            "contracts": 1.0,
            "contractSize": 1,
            "marginRatio": None,
            "liquidationPrice": 0.0,
            "markPrice": 2896.41,
            "collateral": 20,
            "marginType": "isolated",
            "side": "short",
            "percentage": None,
        },
        {
            "symbol": "ADA/USDT:USDT",
            "timestamp": None,
            "datetime": None,
            "initialMargin": 0.0,
            "initialMarginPercentage": None,
            "maintenanceMargin": 0.0,
            "maintenanceMarginPercentage": 0.005,
            "entryPrice": 0.0,
            "notional": 10.0,
            "leverage": None,
            "unrealizedPnl": 0.0,
            "contracts": 1.0,
            "contractSize": 1,
            "marginRatio": None,
            "liquidationPrice": 0.0,
            "markPrice": 2896.41,
            "collateral": 20,
            "marginType": "isolated",
            "side": "short",
            "percentage": None,
        },
    ]
    mocker.patch(f"{EXMS}.get_balances", return_value=rpc_balance)
    mocker.patch(f"{EXMS}.fetch_positions", return_value=mock_pos)
    mocker.patch(f"{EXMS}.get_tickers", tickers)
    mocker.patch(f"{EXMS}.get_valid_pair_combination", side_effect=lambda a, b: [f"{a}/{b}"])
    mocker.patch(f"{EXMS}.get_conversion_rate", return_value=3200)

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)
    mocker.patch(
        "freqtrade.persistence.trade_model.Trade.get_open_trades",
        return_value=[
            MagicMock(pair="ETH/USDT:USDT", safe_base_currency="ETH"),
            MagicMock(pair="ADA/USDT:USDT", safe_base_currency="ADA"),
        ],
    )

    await telegram._balance(update=update, context=MagicMock())
    result = msg_mock.call_args_list[0][0][0]
    assert msg_mock.call_count == 1

    assert "ETH/USDT:USDT" in result
    assert "`short: 10" in result
    assert "ADA/USDT:USDT" in result


async def test_balance_handle_empty_response(default_conf, update, mocker) -> None:
    default_conf["dry_run"] = False
    mocker.patch(f"{EXMS}.get_balances", return_value={})

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    freqtradebot.config["dry_run"] = False
    await telegram._balance(update=update, context=MagicMock())
    result = msg_mock.call_args_list[0][0][0]
    assert msg_mock.call_count == 1
    assert "Starting capital: `0 BTC" in result


async def test_balance_handle_empty_response_dry(default_conf, update, mocker) -> None:
    mocker.patch(f"{EXMS}.get_balances", return_value={})

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    await telegram._balance(update=update, context=MagicMock())
    result = msg_mock.call_args_list[0][0][0]
    assert msg_mock.call_count == 1
    assert "*Warning:* Simulated balances in Dry Mode." in result
    assert "Starting capital: `990 BTC`" in result


async def test_balance_handle_too_large_response(default_conf, update, mocker) -> None:
    balances = []
    for i in range(100):
        curr = choice(ascii_uppercase) + choice(ascii_uppercase) + choice(ascii_uppercase)
        balances.append(
            {
                "currency": curr,
                "free": 1.0,
                "used": 0.5,
                "balance": i,
                "bot_owned": 0.5,
                "est_stake": 1,
                "est_stake_bot": 1,
                "stake": "BTC",
                "is_position": False,
                "leverage": 1.0,
                "position": 0.0,
                "side": "long",
                "is_bot_managed": True,
            }
        )
    mocker.patch(
        "freqtrade.rpc.rpc.RPC._rpc_balance",
        return_value={
            "currencies": balances,
            "total": 100.0,
            "total_bot": 100.0,
            "symbol": 100.0,
            "value": 1000.0,
            "value_bot": 1000.0,
            "starting_capital": 1000,
            "starting_capital_fiat": 1000,
        },
    )

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    await telegram._balance(update=update, context=MagicMock())
    assert msg_mock.call_count > 1
    # Test if wrap happens around 4000 -
    # and each single currency-output is around 120 characters long so we need
    # an offset to avoid random test failures
    assert len(msg_mock.call_args_list[0][0][0]) < 4096
    assert len(msg_mock.call_args_list[0][0][0]) > (4096 - 120)


async def test_start_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    freqtradebot.state = State.STOPPED
    assert freqtradebot.state == State.STOPPED
    await telegram._start(update=update, context=MagicMock())
    assert freqtradebot.state == State.RUNNING
    assert msg_mock.call_count == 1


async def test_start_handle_already_running(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    freqtradebot.state = State.RUNNING
    assert freqtradebot.state == State.RUNNING
    await telegram._start(update=update, context=MagicMock())
    assert freqtradebot.state == State.RUNNING
    assert msg_mock.call_count == 1
    assert "already running" in msg_mock.call_args_list[0][0][0]


async def test_stop_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    freqtradebot.state = State.RUNNING
    assert freqtradebot.state == State.RUNNING
    await telegram._stop(update=update, context=MagicMock())
    assert freqtradebot.state == State.STOPPED
    assert msg_mock.call_count == 1
    assert "stopping trader" in msg_mock.call_args_list[0][0][0]


async def test_stop_handle_already_stopped(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    freqtradebot.state = State.STOPPED
    assert freqtradebot.state == State.STOPPED
    await telegram._stop(update=update, context=MagicMock())
    assert freqtradebot.state == State.STOPPED
    assert msg_mock.call_count == 1
    assert "already stopped" in msg_mock.call_args_list[0][0][0]


async def test_pause_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    assert freqtradebot.state == State.RUNNING
    await telegram._pause(update=update, context=MagicMock())
    assert freqtradebot.state == State.PAUSED
    assert msg_mock.call_count == 1
    assert (
        "paused, no more entries will occur from now. Run /start to enable entries."
        in msg_mock.call_args_list[0][0][0]
    )


async def test_reload_config_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    freqtradebot.state = State.RUNNING
    assert freqtradebot.state == State.RUNNING
    await telegram._reload_config(update=update, context=MagicMock())
    assert freqtradebot.state == State.RELOAD_CONFIG
    assert msg_mock.call_count == 1
    assert "Reloading config" in msg_mock.call_args_list[0][0][0]


async def test_telegram_forceexit_handle(
    default_conf, update, ticker, fee, ticker_sell_up, mocker
) -> None:
    msg_mock = mocker.patch("freqtrade.rpc.telegram.Telegram.send_msg", MagicMock())
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    patch_exchange(mocker)
    patch_whitelist(mocker, default_conf)
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )

    freqtradebot = FreqtradeBot(default_conf)
    rpc = RPC(freqtradebot)
    telegram = Telegram(rpc, default_conf)
    patch_get_signal(freqtradebot)

    # Create some test data
    freqtradebot.enter_positions()

    trade = Trade.session.scalars(select(Trade)).first()
    assert trade

    # Increase the price and sell it
    mocker.patch(f"{EXMS}.fetch_ticker", ticker_sell_up)

    # /forceexit 1
    context = MagicMock()
    context.args = ["1"]
    await telegram._force_exit(update=update, context=context)

    assert msg_mock.call_count == 4
    last_msg = msg_mock.call_args_list[-2][0][0]
    assert {
        "type": RPCMessageType.EXIT,
        "trade_id": 1,
        "exchange": "Binance",
        "pair": "ETH/BTC",
        "gain": "profit",
        "leverage": 1.0,
        "limit": 1.173e-05,
        "order_rate": 1.173e-05,
        "amount": 91.07468123,
        "order_type": "limit",
        "open_rate": 1.098e-05,
        "current_rate": 1.173e-05,
        "direction": "Long",
        "profit_amount": 6.314e-05,
        "profit_ratio": 0.0629778,
        "stake_currency": "BTC",
        "quote_currency": "BTC",
        "base_currency": "ETH",
        "fiat_currency": "USD",
        "buy_tag": ANY,
        "enter_tag": ANY,
        "exit_reason": ExitType.FORCE_EXIT.value,
        "open_date": ANY,
        "close_date": ANY,
        "close_rate": ANY,
        "stake_amount": 0.0009999999999054,
        "sub_trade": False,
        "cumulative_profit": 0.0,
        "is_final_exit": False,
        "final_profit_ratio": None,
    } == last_msg


async def test_telegram_force_exit_down_handle(
    default_conf, update, ticker, fee, ticker_sell_down, mocker
) -> None:
    msg_mock = mocker.patch("freqtrade.rpc.telegram.Telegram.send_msg", MagicMock())
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    patch_exchange(mocker)
    patch_whitelist(mocker, default_conf)

    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )

    freqtradebot = FreqtradeBot(default_conf)
    rpc = RPC(freqtradebot)
    telegram = Telegram(rpc, default_conf)
    patch_get_signal(freqtradebot)

    # Create some test data
    freqtradebot.enter_positions()

    # Decrease the price and sell it
    mocker.patch.multiple(EXMS, fetch_ticker=ticker_sell_down)

    trade = Trade.session.scalars(select(Trade)).first()
    assert trade

    # /forceexit 1
    context = MagicMock()
    context.args = ["1"]
    await telegram._force_exit(update=update, context=context)

    assert msg_mock.call_count == 4

    last_msg = msg_mock.call_args_list[-2][0][0]
    assert {
        "type": RPCMessageType.EXIT,
        "trade_id": 1,
        "exchange": "Binance",
        "pair": "ETH/BTC",
        "gain": "loss",
        "leverage": 1.0,
        "limit": 1.043e-05,
        "order_rate": 1.043e-05,
        "amount": 91.07468123,
        "order_type": "limit",
        "open_rate": 1.098e-05,
        "current_rate": 1.043e-05,
        "direction": "Long",
        "profit_amount": -5.497e-05,
        "profit_ratio": -0.05482878,
        "stake_currency": "BTC",
        "quote_currency": "BTC",
        "base_currency": "ETH",
        "fiat_currency": "USD",
        "buy_tag": ANY,
        "enter_tag": ANY,
        "exit_reason": ExitType.FORCE_EXIT.value,
        "open_date": ANY,
        "close_date": ANY,
        "close_rate": ANY,
        "stake_amount": 0.0009999999999054,
        "sub_trade": False,
        "cumulative_profit": 0.0,
        "is_final_exit": False,
        "final_profit_ratio": None,
    } == last_msg


async def test_forceexit_all_handle(default_conf, update, ticker, fee, mocker) -> None:
    patch_exchange(mocker)
    msg_mock = mocker.patch("freqtrade.rpc.telegram.Telegram.send_msg", MagicMock())
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    patch_whitelist(mocker, default_conf)
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )
    default_conf["max_open_trades"] = 4
    freqtradebot = FreqtradeBot(default_conf)
    rpc = RPC(freqtradebot)
    telegram = Telegram(rpc, default_conf)
    patch_get_signal(freqtradebot)

    # Create some test data
    freqtradebot.enter_positions()
    msg_mock.reset_mock()

    # /forceexit all
    context = MagicMock()
    context.args = ["all"]
    await telegram._force_exit(update=update, context=context)

    # Called for each trade 2 times
    assert msg_mock.call_count == 8
    msg = msg_mock.call_args_list[0][0][0]
    assert {
        "type": RPCMessageType.EXIT,
        "trade_id": 1,
        "exchange": "Binance",
        "pair": "ETH/BTC",
        "gain": "loss",
        "leverage": 1.0,
        "order_rate": 1.099e-05,
        "limit": 1.099e-05,
        "amount": 91.07468123,
        "order_type": "limit",
        "open_rate": 1.098e-05,
        "current_rate": 1.099e-05,
        "direction": "Long",
        "profit_amount": -4.09e-06,
        "profit_ratio": -0.00408133,
        "stake_currency": "BTC",
        "quote_currency": "BTC",
        "base_currency": "ETH",
        "fiat_currency": "USD",
        "buy_tag": ANY,
        "enter_tag": ANY,
        "exit_reason": ExitType.FORCE_EXIT.value,
        "open_date": ANY,
        "close_date": ANY,
        "close_rate": ANY,
        "stake_amount": 0.0009999999999054,
        "sub_trade": False,
        "cumulative_profit": 0.0,
        "is_final_exit": False,
        "final_profit_ratio": None,
    } == msg


async def test_forceexit_handle_invalid(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    # Trader is not running
    freqtradebot.state = State.STOPPED
    # /forceexit 1
    context = MagicMock()
    context.args = ["1"]
    await telegram._force_exit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "not running" in msg_mock.call_args_list[0][0][0]

    # Invalid argument
    msg_mock.reset_mock()
    freqtradebot.state = State.RUNNING
    # /forceexit 123456
    context = MagicMock()
    context.args = ["123456"]
    await telegram._force_exit(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "invalid argument" in msg_mock.call_args_list[0][0][0]


async def test_force_exit_no_pair(default_conf, update, ticker, fee, mocker) -> None:
    default_conf["max_open_trades"] = 4
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
        _dry_is_price_crossed=MagicMock(return_value=True),
    )
    femock = mocker.patch("freqtrade.rpc.rpc.RPC._rpc_force_exit")
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    patch_get_signal(freqtradebot)

    # /forceexit
    context = MagicMock()
    context.args = []
    await telegram._force_exit(update=update, context=context)
    # No pair
    assert msg_mock.call_args_list[0][1]["msg"] == "No open trade found."

    # Create some test data
    freqtradebot.enter_positions()
    msg_mock.reset_mock()

    # /forceexit
    await telegram._force_exit(update=update, context=context)
    keyboard = msg_mock.call_args_list[0][1]["keyboard"]
    # 4 pairs + cancel
    assert reduce(lambda acc, x: acc + len(x), keyboard, 0) == 5
    assert keyboard[-1][0].text == "Cancel"

    assert keyboard[1][0].callback_data == "force_exit__2 "
    update = MagicMock()
    update.callback_query = AsyncMock()
    update.callback_query.data = keyboard[1][0].callback_data
    await telegram._force_exit_inline(update, None)
    assert update.callback_query.answer.call_count == 1
    assert update.callback_query.edit_message_text.call_count == 1
    assert femock.call_count == 1
    assert femock.call_args_list[0][0][0] == "2"

    # Retry exiting - but cancel instead
    update.callback_query.reset_mock()
    await telegram._force_exit(update=update, context=context)
    # Use cancel button
    update.callback_query.data = keyboard[-1][0].callback_data
    await telegram._force_exit_inline(update, None)
    query = update.callback_query
    assert query.answer.call_count == 1
    assert query.edit_message_text.call_count == 1
    assert query.edit_message_text.call_args_list[-1][1]["text"] == "Force exit canceled."


async def test_force_enter_handle(default_conf, update, mocker) -> None:
    fbuy_mock = MagicMock(return_value=None)
    mocker.patch("freqtrade.rpc.rpc.RPC._rpc_force_entry", fbuy_mock)

    telegram, freqtradebot, _ = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    # /forcelong ETH/BTC
    context = MagicMock()
    context.args = ["ETH/BTC"]
    await telegram._force_enter(update=update, context=context, order_side=SignalDirection.LONG)

    assert fbuy_mock.call_count == 1
    assert fbuy_mock.call_args_list[0][0][0] == "ETH/BTC"
    assert fbuy_mock.call_args_list[0][0][1] is None
    assert fbuy_mock.call_args_list[0][1]["order_side"] == SignalDirection.LONG

    # Reset and retry with specified price
    fbuy_mock = MagicMock(return_value=None)
    mocker.patch("freqtrade.rpc.rpc.RPC._rpc_force_entry", fbuy_mock)
    # /forcelong ETH/BTC 0.055
    context = MagicMock()
    context.args = ["ETH/BTC", "0.055"]
    await telegram._force_enter(update=update, context=context, order_side=SignalDirection.LONG)

    assert fbuy_mock.call_count == 1
    assert fbuy_mock.call_args_list[0][0][0] == "ETH/BTC"
    assert isinstance(fbuy_mock.call_args_list[0][0][1], float)
    assert fbuy_mock.call_args_list[0][0][1] == 0.055


async def test_force_enter_handle_exception(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)

    await telegram._force_enter(update=update, context=MagicMock(), order_side=SignalDirection.LONG)

    assert msg_mock.call_count == 1
    assert msg_mock.call_args_list[0][0][0] == "Force_entry not enabled."


async def test_force_enter_no_pair(default_conf, update, mocker) -> None:
    fbuy_mock = mocker.patch("freqtrade.rpc.rpc.RPC._rpc_force_entry", return_value=None)

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    patch_get_signal(freqtradebot)

    context = MagicMock()
    context.args = []
    await telegram._force_enter(update=update, context=context, order_side=SignalDirection.LONG)

    assert fbuy_mock.call_count == 0
    assert msg_mock.call_count == 1
    assert msg_mock.call_args_list[0][1]["msg"] == "Which pair?"
    # assert msg_mock.call_args_list[0][1]['callback_query_handler'] == 'forcebuy'
    keyboard = msg_mock.call_args_list[0][1]["keyboard"]
    # One additional button - cancel
    assert reduce(lambda acc, x: acc + len(x), keyboard, 0) == 5
    update = MagicMock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "force_enter__XRP/USDT_||_long"
    await telegram._force_enter_inline(update, None)
    assert fbuy_mock.call_count == 1

    fbuy_mock.reset_mock()
    update.callback_query = AsyncMock()
    update.callback_query.data = "force_enter__cancel"
    await telegram._force_enter_inline(update, None)
    assert fbuy_mock.call_count == 0
    query = update.callback_query
    assert query.edit_message_text.call_count == 1
    assert query.edit_message_text.call_args_list[-1][1]["text"] == "Force enter canceled."


async def test_telegram_entry_tag_performance_handle(
    default_conf_usdt, update, ticker, fee, mocker
) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    patch_get_signal(freqtradebot)

    create_mock_trades_usdt(fee)

    context = MagicMock()
    await telegram._enter_tag_performance(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Entry Tag Performance" in msg_mock.call_args_list[0][0][0]
    assert "`TEST1\t3.987 USDT (1.99%) (1)`" in msg_mock.call_args_list[0][0][0]

    context.args = ["XRP/USDT"]
    await telegram._enter_tag_performance(update=update, context=context)
    assert msg_mock.call_count == 2

    msg_mock.reset_mock()
    mocker.patch(
        "freqtrade.rpc.rpc.RPC._rpc_enter_tag_performance", side_effect=RPCException("Error")
    )
    await telegram._enter_tag_performance(update=update, context=MagicMock())

    assert msg_mock.call_count == 1
    assert "Error" in msg_mock.call_args_list[0][0][0]


async def test_telegram_exit_reason_performance_handle(
    default_conf_usdt, update, ticker, fee, mocker
) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    patch_get_signal(freqtradebot)

    create_mock_trades_usdt(fee)

    context = MagicMock()
    await telegram._exit_reason_performance(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Exit Reason Performance" in msg_mock.call_args_list[0][0][0]
    assert "`roi\t2.842 USDT (9.47%) (1)`" in msg_mock.call_args_list[0][0][0]
    context.args = ["XRP/USDT"]

    await telegram._exit_reason_performance(update=update, context=context)
    assert msg_mock.call_count == 2

    msg_mock.reset_mock()
    mocker.patch(
        "freqtrade.rpc.rpc.RPC._rpc_exit_reason_performance", side_effect=RPCException("Error")
    )
    await telegram._exit_reason_performance(update=update, context=MagicMock())

    assert msg_mock.call_count == 1
    assert "Error" in msg_mock.call_args_list[0][0][0]


async def test_telegram_mix_tag_performance_handle(
    default_conf_usdt, update, ticker, fee, mocker
) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)
    patch_get_signal(freqtradebot)

    # Create some test data
    create_mock_trades_usdt(fee)

    context = MagicMock()
    await telegram._mix_tag_performance(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Mix Tag Performance" in msg_mock.call_args_list[0][0][0]
    assert "`TEST3 roi\t2.842 USDT (10.00%) (1)`" in msg_mock.call_args_list[0][0][0]

    context.args = ["XRP/USDT"]
    await telegram._mix_tag_performance(update=update, context=context)
    assert msg_mock.call_count == 2

    msg_mock.reset_mock()
    mocker.patch(
        "freqtrade.rpc.rpc.RPC._rpc_mix_tag_performance", side_effect=RPCException("Error")
    )
    await telegram._mix_tag_performance(update=update, context=MagicMock())

    assert msg_mock.call_count == 1
    assert "Error" in msg_mock.call_args_list[0][0][0]


async def test_telegram_lock_handle(default_conf, update, ticker, fee, mocker) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    patch_get_signal(freqtradebot)
    await telegram._locks(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "No active locks." in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()

    PairLocks.lock_pair("ETH/BTC", dt_now() + timedelta(minutes=4), "randreason")
    PairLocks.lock_pair("XRP/BTC", dt_now() + timedelta(minutes=20), "deadbeef")

    await telegram._locks(update=update, context=MagicMock())

    assert "Pair" in msg_mock.call_args_list[0][0][0]
    assert "Until" in msg_mock.call_args_list[0][0][0]
    assert "Reason\n" in msg_mock.call_args_list[0][0][0]
    assert "ETH/BTC" in msg_mock.call_args_list[0][0][0]
    assert "XRP/BTC" in msg_mock.call_args_list[0][0][0]
    assert "deadbeef" in msg_mock.call_args_list[0][0][0]
    assert "randreason" in msg_mock.call_args_list[0][0][0]

    context = MagicMock()
    context.args = ["XRP/BTC"]
    msg_mock.reset_mock()
    await telegram._delete_locks(update=update, context=context)

    assert "ETH/BTC" in msg_mock.call_args_list[0][0][0]
    assert "randreason" in msg_mock.call_args_list[0][0][0]
    assert "XRP/BTC" not in msg_mock.call_args_list[0][0][0]
    assert "deadbeef" not in msg_mock.call_args_list[0][0][0]


async def test_whitelist_static(default_conf, update, mocker) -> None:
    telegram, _freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._whitelist(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert (
        "Using whitelist `['StaticPairList']` with 4 pairs\n"
        "`ETH/BTC, LTC/BTC, XRP/BTC, NEO/BTC`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["sorted"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['StaticPairList']` with 4 pairs\n"
        "`ETH/BTC, LTC/BTC, NEO/BTC, XRP/BTC`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["baseonly"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['StaticPairList']` with 4 pairs\n"
        "`ETH, LTC, XRP, NEO`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["baseonly", "sorted"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['StaticPairList']` with 4 pairs\n"
        "`ETH, LTC, NEO, XRP`" in msg_mock.call_args_list[0][0][0]
    )


async def test_whitelist_dynamic(default_conf, update, mocker) -> None:
    mocker.patch(f"{EXMS}.exchange_has", return_value=True)
    default_conf["pairlists"] = [{"method": "VolumePairList", "number_assets": 4}]
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._whitelist(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert (
        "Using whitelist `['VolumePairList']` with 4 pairs\n"
        "`ETH/BTC, LTC/BTC, XRP/BTC, NEO/BTC`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["sorted"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['VolumePairList']` with 4 pairs\n"
        "`ETH/BTC, LTC/BTC, NEO/BTC, XRP/BTC`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["baseonly"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['VolumePairList']` with 4 pairs\n"
        "`ETH, LTC, XRP, NEO`" in msg_mock.call_args_list[0][0][0]
    )

    context = MagicMock()
    context.args = ["baseonly", "sorted"]
    msg_mock.reset_mock()
    await telegram._whitelist(update=update, context=context)
    assert (
        "Using whitelist `['VolumePairList']` with 4 pairs\n"
        "`ETH, LTC, NEO, XRP`" in msg_mock.call_args_list[0][0][0]
    )


async def test_blacklist_static(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._blacklist(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "Blacklist contains 2 pairs\n`DOGE/BTC, HOT/BTC`" in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()

    # /blacklist ETH/BTC
    context = MagicMock()
    context.args = ["ETH/BTC"]
    await telegram._blacklist(update=update, context=context)
    assert msg_mock.call_count == 1
    assert (
        "Blacklist contains 3 pairs\n`DOGE/BTC, HOT/BTC, ETH/BTC`"
        in msg_mock.call_args_list[0][0][0]
    )
    assert freqtradebot.pairlists.blacklist == ["DOGE/BTC", "HOT/BTC", "ETH/BTC"]

    msg_mock.reset_mock()
    context = MagicMock()
    context.args = ["XRP/.*"]
    await telegram._blacklist(update=update, context=context)
    assert msg_mock.call_count == 1

    assert (
        "Blacklist contains 4 pairs\n`DOGE/BTC, HOT/BTC, ETH/BTC, XRP/.*`"
        in msg_mock.call_args_list[0][0][0]
    )
    assert freqtradebot.pairlists.blacklist == ["DOGE/BTC", "HOT/BTC", "ETH/BTC", "XRP/.*"]

    msg_mock.reset_mock()
    context.args = ["DOGE/BTC"]
    await telegram._blacklist_delete(update=update, context=context)
    assert msg_mock.call_count == 1
    assert (
        "Blacklist contains 3 pairs\n`HOT/BTC, ETH/BTC, XRP/.*`" in msg_mock.call_args_list[0][0][0]
    )


async def test_telegram_logs(default_conf, update, mocker) -> None:
    mocker.patch.multiple(
        "freqtrade.rpc.telegram.Telegram",
        _init=MagicMock(),
    )
    setup_logging(default_conf)

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    context = MagicMock()
    context.args = []
    await telegram._logs(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "freqtrade\\.rpc\\.telegram" in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    context.args = ["1"]
    await telegram._logs(update=update, context=context)
    assert msg_mock.call_count == 1

    msg_mock.reset_mock()
    # Test with changed MaxMessageLength
    mocker.patch("freqtrade.rpc.telegram.MAX_MESSAGE_LENGTH", 200)
    context = MagicMock()
    context.args = []
    await telegram._logs(update=update, context=context)
    # Called at least 2 times. Exact times will change with unrelated changes to setup messages
    # Therefore we don't test for this explicitly.
    assert msg_mock.call_count >= 2


@pytest.mark.parametrize(
    "is_short,regex_pattern",
    [(True, r"now[ ]*XRP\/BTC \(#3\)  -1.00% \("), (False, r"now[ ]*XRP\/BTC \(#3\)  1.00% \(")],
)
async def test_telegram_trades(mocker, update, default_conf, fee, is_short, regex_pattern):
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    context = MagicMock()
    context.args = []

    await telegram._trades(update=update, context=context)
    assert "<b>0 recent trades</b>:" in msg_mock.call_args_list[0][0][0]
    assert "<pre>" not in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    context.args = ["hello"]
    await telegram._trades(update=update, context=context)
    assert "<b>0 recent trades</b>:" in msg_mock.call_args_list[0][0][0]
    assert "<pre>" not in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    create_mock_trades(fee, is_short=is_short)

    context = MagicMock()
    context.args = [5]
    await telegram._trades(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "2 recent trades</b>:" in msg_mock.call_args_list[0][0][0]
    assert "Profit (" in msg_mock.call_args_list[0][0][0]
    assert "Close Date" in msg_mock.call_args_list[0][0][0]
    assert "<pre>" in msg_mock.call_args_list[0][0][0]
    assert bool(re.search(regex_pattern, msg_mock.call_args_list[0][0][0]))


@pytest.mark.parametrize("is_short", [True, False])
async def test_telegram_delete_trade(mocker, update, default_conf, fee, is_short):
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    context = MagicMock()
    context.args = []

    await telegram._delete_trade(update=update, context=context)
    assert "Trade-id not set." in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    create_mock_trades(fee, is_short=is_short)

    context = MagicMock()
    context.args = [1]
    await telegram._delete_trade(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Deleted trade #1" in msg_mock.call_args_list[0][0][0]
    assert "Please make sure to take care of this asset" in msg_mock.call_args_list[0][0][0]


@pytest.mark.parametrize("is_short", [True, False])
async def test_telegram_reload_trade_from_exchange(mocker, update, default_conf, fee, is_short):
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    context = MagicMock()
    context.args = []

    await telegram._reload_trade_from_exchange(update=update, context=context)
    assert "Trade-id not set." in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    create_mock_trades(fee, is_short=is_short)

    context.args = [5]

    await telegram._reload_trade_from_exchange(update=update, context=context)
    assert "Status: `Reloaded from orders from exchange`" in msg_mock.call_args_list[0][0][0]


@pytest.mark.parametrize("is_short", [True, False])
async def test_telegram_delete_open_order(mocker, update, default_conf, fee, is_short, ticker):
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
    )
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    context = MagicMock()
    context.args = []

    await telegram._cancel_open_order(update=update, context=context)
    assert "Trade-id not set." in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    create_mock_trades(fee, is_short=is_short)

    context = MagicMock()
    context.args = [5]
    await telegram._cancel_open_order(update=update, context=context)
    assert "No open order for trade_id" in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()

    trade = Trade.get_trades([Trade.id == 6]).first()
    mocker.patch(f"{EXMS}.fetch_order", return_value=trade.orders[-1].to_ccxt_object())
    context = MagicMock()
    context.args = [6]
    await telegram._cancel_open_order(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Open order canceled." in msg_mock.call_args_list[0][0][0]


async def test_help_handle(default_conf, update, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._help(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "*/help:* `This help message`" in msg_mock.call_args_list[0][0][0]


async def test_version_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._version(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert f"*Version:* `{__version__}`" in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    freqtradebot.strategy.version = lambda: "1.1.1"

    await telegram._version(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert f"*Version:* `{__version__}`" in msg_mock.call_args_list[0][0][0]
    assert "*Strategy version: * `1.1.1`" in msg_mock.call_args_list[0][0][0]


async def test_show_config_handle(default_conf, update, mocker) -> None:
    default_conf["runmode"] = RunMode.DRY_RUN

    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)

    await telegram._show_config(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "*Mode:* `{}`".format("Dry-run") in msg_mock.call_args_list[0][0][0]
    assert "*Exchange:* `binance`" in msg_mock.call_args_list[0][0][0]
    assert f"*Strategy:* `{CURRENT_TEST_STRATEGY}`" in msg_mock.call_args_list[0][0][0]
    assert "*Stoploss:* `-0.1`" in msg_mock.call_args_list[0][0][0]

    msg_mock.reset_mock()
    freqtradebot.config["trailing_stop"] = True
    await telegram._show_config(update=update, context=MagicMock())
    assert msg_mock.call_count == 1
    assert "*Mode:* `{}`".format("Dry-run") in msg_mock.call_args_list[0][0][0]
    assert "*Exchange:* `binance`" in msg_mock.call_args_list[0][0][0]
    assert f"*Strategy:* `{CURRENT_TEST_STRATEGY}`" in msg_mock.call_args_list[0][0][0]
    assert "*Initial Stoploss:* `-0.1`" in msg_mock.call_args_list[0][0][0]


@pytest.mark.parametrize(
    "message_type,enter,enter_signal,leverage",
    [
        (RPCMessageType.ENTRY, "Long", "long_signal_01", None),
        (RPCMessageType.ENTRY, "Long", "long_signal_01", 1.0),
        (RPCMessageType.ENTRY, "Long", "long_signal_01", 5.0),
        (RPCMessageType.ENTRY, "Short", "short_signal_01", 2.0),
    ],
)
def test_send_msg_enter_notification(
    default_conf, mocker, caplog, message_type, enter, enter_signal, leverage
) -> None:
    default_conf["telegram"]["notification_settings"]["show_candle"] = "ohlc"
    df = DataFrame(
        {
            "open": [1.1],
            "high": [2.2],
            "low": [1.0],
            "close": [1.5],
        }
    )
    mocker.patch(
        "freqtrade.data.dataprovider.DataProvider.get_analyzed_dataframe", return_value=(df, 1)
    )

    msg = {
        "type": message_type,
        "trade_id": 1,
        "enter_tag": enter_signal,
        "exchange": "Binance",
        "pair": "ETH/BTC",
        "leverage": leverage,
        "open_rate": 1.099e-05,
        "order_type": "limit",
        "direction": enter,
        "stake_amount": 0.01465333,
        "stake_amount_fiat": 0.0,
        "stake_currency": "BTC",
        "quote_currency": "BTC",
        "base_currency": "ETH",
        "fiat_currency": "USD",
        "sub_trade": False,
        "current_rate": 1.099e-05,
        "amount": 1333.3333333333335,
        "analyzed_candle": {"open": 1.1, "high": 2.2, "low": 1.0, "close": 1.5},
        "open_date": dt_now() + timedelta(hours=-1),
    }
    telegram, freqtradebot, msg_mock = get_telegram_testobject(
        mocker, default_conf, mock_fiat=False
    )

    telegram.send_msg(msg)
    leverage_text = f" ({leverage:.3g}x)" if leverage and leverage != 1.0 else ""

    assert msg_mock.call_args[0][0] == (
        f"\N{LARGE BLUE CIRCLE} *Binance (dry):* New Trade (#1)\n"
        f"*Pair:* `ETH/BTC`\n"
        "*Candle OHLC*: `1.1, 2.2, 1.0, 1.5`\n"
        f"*Enter Tag:* `{enter_signal}`\n"
        "*Amount:* `1333.33333333`\n"
        f"*Direction:* `{enter}"
        f"{leverage_text}`\n"
        "*Open Rate:* `0.00001099 BTC`\n"
        "*Current Rate:* `0.00001099 BTC`\n"
        "*Total:* `0.01465333 BTC / 180.895 USD`"
    )

    freqtradebot.config["telegram"]["notification_settings"] = {"entry": "off"}
    caplog.clear()
    msg_mock.reset_mock()
    telegram.send_msg(msg)
    assert msg_mock.call_count == 0
    assert log_has("Notification 'entry' not sent.", caplog)

    freqtradebot.config["telegram"]["notification_settings"] = {"entry": "silent"}
    caplog.clear()
    msg_mock.reset_mock()

    telegram.send_msg(msg)
    assert msg_mock.call_count == 1
    assert msg_mock.call_args_list[0][1]["disable_notification"] is True


@pytest.mark.parametrize(
    "message_type,enter_signal",
    [
        (RPCMessageType.ENTRY_CANCEL, "long_signal_01"),
        (RPCMessageType.ENTRY_CANCEL, "short_signal_01"),
    ],
)
def test_send_msg_enter_cancel_notification(
    default_conf, mocker, message_type, enter_signal
) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    telegram.send_msg(
        {
            "type": message_type,
            "enter_tag": enter_signal,
            "trade_id": 1,
            "exchange": "Binance",
            "pair": "ETH/BTC",
            "reason": CANCEL_REASON["TIMEOUT"],
        }
    )
    assert (
        msg_mock.call_args[0][0] == "\N{WARNING SIGN} *Binance (dry):* "
        "Cancelling enter Order for ETH/BTC (#1). "
        "Reason: cancelled due to timeout."
    )


def test_send_msg_protection_notification(default_conf, mocker, time_machine) -> None:
    default_conf["telegram"]["notification_settings"]["protection_trigger"] = "on"

    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    time_machine.move_to("2021-09-01 05:00:00 +00:00")
    lock = PairLocks.lock_pair("ETH/BTC", dt_now() + timedelta(minutes=6), "randreason")
    msg = {
        "type": RPCMessageType.PROTECTION_TRIGGER,
    }
    msg.update(lock.to_json())
    telegram.send_msg(msg)
    assert (
        msg_mock.call_args[0][0] == "*Protection* triggered due to randreason. "
        "`ETH/BTC` will be locked until `2021-09-01 05:10:00`."
    )

    msg_mock.reset_mock()
    # Test global protection

    msg = {
        "type": RPCMessageType.PROTECTION_TRIGGER_GLOBAL,
    }
    lock = PairLocks.lock_pair("*", dt_now() + timedelta(minutes=100), "randreason")
    msg.update(lock.to_json())
    telegram.send_msg(msg)
    assert (
        msg_mock.call_args[0][0] == "*Protection* triggered due to randreason. "
        "*All pairs* will be locked until `2021-09-01 06:45:00`."
    )


@pytest.mark.parametrize(
    "message_type,entered,enter_signal,leverage",
    [
        (RPCMessageType.ENTRY_FILL, "Long", "long_signal_01", 1.0),
        (RPCMessageType.ENTRY_FILL, "Long", "long_signal_02", 2.0),
        (RPCMessageType.ENTRY_FILL, "Short", "short_signal_01", 2.0),
    ],
)
def test_send_msg_entry_fill_notification(
    default_conf, mocker, message_type, entered, enter_signal, leverage
) -> None:
    default_conf["telegram"]["notification_settings"]["entry_fill"] = "on"
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf, mock_fiat=False)

    telegram.send_msg(
        {
            "type": message_type,
            "trade_id": 1,
            "enter_tag": enter_signal,
            "exchange": "Binance",
            "pair": "ETH/BTC",
            "leverage": leverage,
            "stake_amount": 0.01465333,
            "direction": entered,
            "sub_trade": False,
            "stake_currency": "BTC",
            "quote_currency": "BTC",
            "base_currency": "ETH",
            "fiat_currency": "USD",
            "open_rate": 1.099e-05,
            "amount": 1333.3333333333335,
            "open_date": dt_now() - timedelta(hours=1),
        }
    )
    leverage_text = f" ({leverage:.3g}x)" if leverage != 1.0 else ""
    assert msg_mock.call_args[0][0] == (
        f"\N{CHECK MARK} *Binance (dry):* New Trade filled (#1)\n"
        f"*Pair:* `ETH/BTC`\n"
        f"*Enter Tag:* `{enter_signal}`\n"
        "*Amount:* `1333.33333333`\n"
        f"*Direction:* `{entered}"
        f"{leverage_text}`\n"
        "*Open Rate:* `0.00001099 BTC`\n"
        "*Total:* `0.01465333 BTC / 180.895 USD`"
    )

    msg_mock.reset_mock()
    telegram.send_msg(
        {
            "type": message_type,
            "trade_id": 1,
            "enter_tag": enter_signal,
            "exchange": "Binance",
            "pair": "ETH/BTC",
            "leverage": leverage,
            "stake_amount": 0.01465333,
            "sub_trade": True,
            "direction": entered,
            "stake_currency": "BTC",
            "quote_currency": "BTC",
            "base_currency": "ETH",
            "fiat_currency": "USD",
            "open_rate": 1.099e-05,
            "amount": 1333.3333333333335,
            "open_date": dt_now() - timedelta(hours=1),
        }
    )

    assert msg_mock.call_args[0][0] == (
        f"\N{CHECK MARK} *Binance (dry):* Position increase filled (#1)\n"
        f"*Pair:* `ETH/BTC`\n"
        f"*Enter Tag:* `{enter_signal}`\n"
        "*Amount:* `1333.33333333`\n"
        f"*Direction:* `{entered}"
        f"{leverage_text}`\n"
        "*Open Rate:* `0.00001099 BTC`\n"
        "*New Total:* `0.01465333 BTC / 180.895 USD`"
    )


def test_send_msg_exit_notification(default_conf, mocker) -> None:
    with time_machine.travel("2022-09-01 05:00:00 +00:00", tick=False):
        telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

        old_convamount = telegram._rpc._fiat_converter.convert_amount
        telegram._rpc._fiat_converter.convert_amount = lambda a, b, c: -24.812
        telegram.send_msg(
            {
                "type": RPCMessageType.EXIT,
                "trade_id": 1,
                "exchange": "Binance",
                "pair": "KEY/ETH",
                "leverage": 1.0,
                "direction": "Long",
                "gain": "loss",
                "order_rate": 3.201e-04,
                "amount": 1333.3333333333335,
                "order_type": "market",
                "open_rate": 7.5e-04,
                "current_rate": 3.201e-04,
                "profit_amount": -0.05746268,
                "profit_ratio": -0.57405275,
                "stake_currency": "ETH",
                "quote_currency": "ETH",
                "base_currency": "KEY",
                "fiat_currency": "USD",
                "enter_tag": "buy_signal1",
                "exit_reason": ExitType.STOP_LOSS.value,
                "open_date": dt_now() - timedelta(hours=1),
                "close_date": dt_now(),
            }
        )
        assert msg_mock.call_args[0][0] == (
            "\N{WARNING SIGN} *Binance (dry):* Exiting KEY/ETH (#1)\n"
            "*Unrealized Profit:* `-57.41% (loss: -0.05746 ETH / -24.812 USD)`\n"
            "*Enter Tag:* `buy_signal1`\n"
            "*Exit Reason:* `stop_loss`\n"
            "*Direction:* `Long`\n"
            "*Amount:* `1333.33333333`\n"
            "*Open Rate:* `0.00075 ETH`\n"
            "*Current Rate:* `0.0003201 ETH`\n"
            "*Exit Rate:* `0.0003201 ETH`\n"
            "*Duration:* `1:00:00 (60.0 min)`"
        )

        msg_mock.reset_mock()
        telegram.send_msg(
            {
                "type": RPCMessageType.EXIT,
                "trade_id": 1,
                "exchange": "Binance",
                "pair": "KEY/ETH",
                "direction": "Long",
                "gain": "loss",
                "order_rate": 3.201e-04,
                "amount": 1333.3333333333335,
                "order_type": "market",
                "open_rate": 7.5e-04,
                "current_rate": 3.201e-04,
                "cumulative_profit": -0.15746268,
                "profit_amount": -0.05746268,
                "profit_ratio": -0.57405275,
                "stake_currency": "ETH",
                "quote_currency": "ETH",
                "base_currency": "KEY",
                "fiat_currency": "USD",
                "enter_tag": "buy_signal1",
                "exit_reason": ExitType.STOP_LOSS.value,
                "open_date": dt_now() - timedelta(days=1, hours=2, minutes=30),
                "close_date": dt_now(),
                "stake_amount": 0.01,
                "sub_trade": True,
            }
        )
        assert msg_mock.call_args[0][0] == (
            "\N{WARNING SIGN} *Binance (dry):* Partially exiting KEY/ETH (#1)\n"
            "*Unrealized Sub Profit:* `-57.41% (loss: -0.05746 ETH / -24.812 USD)`\n"
            "*Cumulative Profit:* `-0.15746 ETH / -24.812 USD`\n"
            "*Enter Tag:* `buy_signal1`\n"
            "*Exit Reason:* `stop_loss`\n"
            "*Direction:* `Long`\n"
            "*Amount:* `1333.33333333`\n"
            "*Open Rate:* `0.00075 ETH`\n"
            "*Current Rate:* `0.0003201 ETH`\n"
            "*Exit Rate:* `0.0003201 ETH`\n"
            "*Remaining:* `0.01 ETH / -24.812 USD`"
        )

        msg_mock.reset_mock()
        telegram.send_msg(
            {
                "type": RPCMessageType.EXIT,
                "trade_id": 1,
                "exchange": "Binance",
                "pair": "KEY/ETH",
                "direction": "Long",
                "gain": "loss",
                "order_rate": 3.201e-04,
                "amount": 1333.3333333333335,
                "order_type": "market",
                "open_rate": 7.5e-04,
                "current_rate": 3.201e-04,
                "profit_amount": -0.05746268,
                "profit_ratio": -0.57405275,
                "stake_currency": "ETH",
                "quote_currency": "ETH",
                "base_currency": "KEY",
                "fiat_currency": None,
                "enter_tag": "buy_signal1",
                "exit_reason": ExitType.STOP_LOSS.value,
                "open_date": dt_now() - timedelta(days=1, hours=2, minutes=30),
                "close_date": dt_now(),
            }
        )
        assert msg_mock.call_args[0][0] == (
            "\N{WARNING SIGN} *Binance (dry):* Exiting KEY/ETH (#1)\n"
            "*Unrealized Profit:* `-57.41% (loss: -0.05746 ETH)`\n"
            "*Enter Tag:* `buy_signal1`\n"
            "*Exit Reason:* `stop_loss`\n"
            "*Direction:* `Long`\n"
            "*Amount:* `1333.33333333`\n"
            "*Open Rate:* `0.00075 ETH`\n"
            "*Current Rate:* `0.0003201 ETH`\n"
            "*Exit Rate:* `0.0003201 ETH`\n"
            "*Duration:* `1 day, 2:30:00 (1590.0 min)`"
        )
        # Reset singleton function to avoid random breaks
        telegram._rpc._fiat_converter.convert_amount = old_convamount


async def test_send_msg_exit_cancel_notification(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    old_convamount = telegram._rpc._fiat_converter.convert_amount
    telegram._rpc._fiat_converter.convert_amount = lambda a, b, c: -24.812
    telegram.send_msg(
        {
            "type": RPCMessageType.EXIT_CANCEL,
            "trade_id": 1,
            "exchange": "Binance",
            "pair": "KEY/ETH",
            "reason": "Cancelled on exchange",
        }
    )
    assert msg_mock.call_args[0][0] == (
        "\N{WARNING SIGN} *Binance (dry):* Cancelling exit Order for KEY/ETH (#1)."
        " Reason: Cancelled on exchange."
    )

    msg_mock.reset_mock()
    # Test with live mode (no dry appendix)
    telegram._config["dry_run"] = False
    telegram.send_msg(
        {
            "type": RPCMessageType.EXIT_CANCEL,
            "trade_id": 1,
            "exchange": "Binance",
            "pair": "KEY/ETH",
            "reason": "timeout",
        }
    )
    assert msg_mock.call_args[0][0] == (
        "\N{WARNING SIGN} *Binance:* Cancelling exit Order for KEY/ETH (#1). Reason: timeout."
    )
    # Reset singleton function to avoid random breaks
    telegram._rpc._fiat_converter.convert_amount = old_convamount


@pytest.mark.parametrize(
    "direction,enter_signal,leverage",
    [
        ("Long", "long_signal_01", None),
        ("Long", "long_signal_01", 1.0),
        ("Long", "long_signal_01", 5.0),
        ("Short", "short_signal_01", 2.0),
    ],
)
def test_send_msg_exit_fill_notification(
    default_conf, mocker, direction, enter_signal, leverage
) -> None:
    default_conf["telegram"]["notification_settings"]["exit_fill"] = "on"
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    with time_machine.travel("2022-09-01 05:00:00 +00:00", tick=False):
        telegram.send_msg(
            {
                "type": RPCMessageType.EXIT_FILL,
                "trade_id": 1,
                "exchange": "Binance",
                "pair": "KEY/ETH",
                "leverage": leverage,
                "direction": direction,
                "gain": "loss",
                "limit": 3.201e-04,
                "amount": 1333.3333333333335,
                "order_type": "market",
                "open_rate": 7.5e-04,
                "close_rate": 3.201e-04,
                "profit_amount": -0.05746268,
                "profit_ratio": -0.57405275,
                "stake_currency": "ETH",
                "quote_currency": "ETH",
                "base_currency": "KEY",
                "fiat_currency": None,
                "enter_tag": enter_signal,
                "exit_reason": ExitType.STOP_LOSS.value,
                "open_date": dt_now() - timedelta(days=1, hours=2, minutes=30),
                "close_date": dt_now(),
            }
        )

        leverage_text = f" ({leverage:.3g}x)`\n" if leverage and leverage != 1.0 else "`\n"
        assert msg_mock.call_args[0][0] == (
            "\N{WARNING SIGN} *Binance (dry):* Exited KEY/ETH (#1)\n"
            "*Profit:* `-57.41% (loss: -0.05746 ETH)`\n"
            f"*Enter Tag:* `{enter_signal}`\n"
            "*Exit Reason:* `stop_loss`\n"
            f"*Direction:* `{direction}"
            f"{leverage_text}"
            "*Amount:* `1333.33333333`\n"
            "*Open Rate:* `0.00075 ETH`\n"
            "*Exit Rate:* `0.0003201 ETH`\n"
            "*Duration:* `1 day, 2:30:00 (1590.0 min)`"
        )


def test_send_msg_status_notification(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    telegram.send_msg({"type": RPCMessageType.STATUS, "status": "running"})
    assert msg_mock.call_args[0][0] == "*Status:* `running`"


async def test_warning_notification(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    telegram.send_msg({"type": RPCMessageType.WARNING, "status": "message"})
    assert msg_mock.call_args[0][0] == "\N{WARNING SIGN} *Warning:* `message`"


def test_startup_notification(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    telegram.send_msg({"type": RPCMessageType.STARTUP, "status": "*Custom:* `Hello World`"})
    assert msg_mock.call_args[0][0] == "*Custom:* `Hello World`"


def test_send_msg_strategy_msg_notification(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    telegram.send_msg({"type": RPCMessageType.STRATEGY_MSG, "msg": "hello world, Test msg"})
    assert msg_mock.call_args[0][0] == "hello world, Test msg"


def test_send_msg_unknown_type(default_conf, mocker) -> None:
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)
    telegram.send_msg(
        {
            "type": None,
        }
    )
    assert msg_mock.call_count == 0


@pytest.mark.parametrize(
    "message_type,enter,enter_signal,leverage",
    [
        (RPCMessageType.ENTRY, "Long", "long_signal_01", None),
        (RPCMessageType.ENTRY, "Long", "long_signal_01", 2.0),
        (RPCMessageType.ENTRY, "Short", "short_signal_01", 2.0),
    ],
)
def test_send_msg_buy_notification_no_fiat(
    default_conf, mocker, message_type, enter, enter_signal, leverage
) -> None:
    del default_conf["fiat_display_currency"]
    default_conf["dry_run"] = False
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    telegram.send_msg(
        {
            "type": message_type,
            "enter_tag": enter_signal,
            "trade_id": 1,
            "exchange": "Binance",
            "pair": "ETH/BTC",
            "leverage": leverage,
            "open_rate": 1.099e-05,
            "order_type": "limit",
            "direction": enter,
            "sub_trade": False,
            "stake_amount": 0.01465333,
            "stake_amount_fiat": 0.0,
            "stake_currency": "BTC",
            "quote_currency": "BTC",
            "base_currency": "ETH",
            "fiat_currency": None,
            "current_rate": 1.099e-05,
            "amount": 1333.3333333333335,
            "open_date": dt_now() - timedelta(hours=1),
        }
    )

    leverage_text = f" ({leverage:.3g}x)" if leverage and leverage != 1.0 else ""
    assert msg_mock.call_args[0][0] == (
        f"\N{LARGE BLUE CIRCLE} *Binance:* New Trade (#1)\n"
        "*Pair:* `ETH/BTC`\n"
        f"*Enter Tag:* `{enter_signal}`\n"
        "*Amount:* `1333.33333333`\n"
        f"*Direction:* `{enter}"
        f"{leverage_text}`\n"
        "*Open Rate:* `0.00001099 BTC`\n"
        "*Current Rate:* `0.00001099 BTC`\n"
        "*Total:* `0.01465333 BTC`"
    )


@pytest.mark.parametrize(
    "direction,enter_signal,leverage",
    [
        ("Long", "long_signal_01", None),
        ("Long", "long_signal_01", 1.0),
        ("Long", "long_signal_01", 5.0),
        ("Short", "short_signal_01", 2.0),
    ],
)
@pytest.mark.parametrize("fiat", ["", None])
def test_send_msg_exit_notification_no_fiat(
    default_conf, mocker, direction, enter_signal, leverage, time_machine, fiat
) -> None:
    if fiat is None:
        del default_conf["fiat_display_currency"]
    else:
        default_conf["fiat_display_currency"] = fiat
    time_machine.move_to("2022-05-02 00:00:00 +00:00", tick=False)
    telegram, _, msg_mock = get_telegram_testobject(mocker, default_conf)

    telegram.send_msg(
        {
            "type": RPCMessageType.EXIT,
            "trade_id": 1,
            "exchange": "Binance",
            "pair": "KEY/ETH",
            "gain": "loss",
            "leverage": leverage,
            "direction": direction,
            "sub_trade": False,
            "order_rate": 3.201e-04,
            "amount": 1333.3333333333335,
            "order_type": "limit",
            "open_rate": 7.5e-04,
            "current_rate": 3.201e-04,
            "profit_amount": -0.05746268,
            "profit_ratio": -0.57405275,
            "stake_currency": "ETH",
            "quote_currency": "ETH",
            "base_currency": "KEY",
            "fiat_currency": "USD",
            "enter_tag": enter_signal,
            "exit_reason": ExitType.STOP_LOSS.value,
            "open_date": dt_now() - timedelta(hours=2, minutes=35, seconds=3),
            "close_date": dt_now(),
        }
    )

    leverage_text = f" ({leverage:.3g}x)" if leverage and leverage != 1.0 else ""
    assert msg_mock.call_args[0][0] == (
        "\N{WARNING SIGN} *Binance (dry):* Exiting KEY/ETH (#1)\n"
        "*Unrealized Profit:* `-57.41% (loss: -0.05746 ETH)`\n"
        f"*Enter Tag:* `{enter_signal}`\n"
        "*Exit Reason:* `stop_loss`\n"
        f"*Direction:* `{direction}"
        f"{leverage_text}`\n"
        "*Amount:* `1333.33333333`\n"
        "*Open Rate:* `0.00075 ETH`\n"
        "*Current Rate:* `0.0003201 ETH`\n"
        "*Exit Rate:* `0.0003201 ETH`\n"
        "*Duration:* `2:35:03 (155.1 min)`"
    )


@pytest.mark.parametrize(
    "msg,expected",
    [
        ({"profit_ratio": 0.201, "exit_reason": "roi"}, "\N{ROCKET}"),
        ({"profit_ratio": 0.051, "exit_reason": "roi"}, "\N{ROCKET}"),
        ({"profit_ratio": 0.0256, "exit_reason": "roi"}, "\N{EIGHT SPOKED ASTERISK}"),
        ({"profit_ratio": 0.01, "exit_reason": "roi"}, "\N{EIGHT SPOKED ASTERISK}"),
        ({"profit_ratio": 0.0, "exit_reason": "roi"}, "\N{EIGHT SPOKED ASTERISK}"),
        ({"profit_ratio": -0.05, "exit_reason": "stop_loss"}, "\N{WARNING SIGN}"),
        ({"profit_ratio": -0.02, "exit_reason": "sell_signal"}, "\N{CROSS MARK}"),
    ],
)
def test__exit_emoji(default_conf, mocker, msg, expected):
    del default_conf["fiat_display_currency"]

    telegram, _, _ = get_telegram_testobject(mocker, default_conf)

    assert telegram._get_exit_emoji(msg) == expected


async def test_telegram__send_msg(default_conf, mocker, caplog) -> None:
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    telegram, _, _ = get_telegram_testobject(mocker, default_conf, mock=False)
    telegram._app = MagicMock()
    telegram._app.bot = bot

    await telegram._send_msg("test")
    assert len(bot.method_calls) == 1

    # Test update
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    await telegram._send_msg("test", callback_path="DeadBeef", query=query, reload_able=True)
    assert query.edit_message_text.call_count == 1
    assert "Updated: " in query.edit_message_text.call_args_list[0][1]["text"]

    query.edit_message_text = AsyncMock(side_effect=BadRequest("not modified"))
    await telegram._send_msg("test", callback_path="DeadBeef", query=query)
    assert query.edit_message_text.call_count == 1
    assert not log_has_re(r"TelegramError: .*", caplog)

    query.edit_message_text = AsyncMock(side_effect=BadRequest(""))
    await telegram._send_msg("test2", callback_path="DeadBeef", query=query)
    assert query.edit_message_text.call_count == 1
    assert log_has_re(r"TelegramError: .*", caplog)

    query.edit_message_text = AsyncMock(side_effect=TelegramError("DeadBEEF"))
    await telegram._send_msg("test3", callback_path="DeadBeef", query=query)

    assert log_has_re(r"TelegramError: DeadBEEF! Giving up.*", caplog)


async def test__send_msg_network_error(default_conf, mocker, caplog) -> None:
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    bot = MagicMock()
    bot.send_message = MagicMock(side_effect=NetworkError("Oh snap"))
    telegram, _, _ = get_telegram_testobject(mocker, default_conf, mock=False)
    telegram._app = MagicMock()
    telegram._app.bot = bot

    telegram._config["telegram"]["enabled"] = True
    await telegram._send_msg("test")

    # Bot should've tried to send it twice
    assert len(bot.method_calls) == 2
    assert log_has("Telegram NetworkError: Oh snap! Trying one more time.", caplog)


@pytest.mark.filterwarnings("ignore:.*ChatPermissions")
async def test__send_msg_keyboard(default_conf, mocker, caplog) -> None:
    mocker.patch("freqtrade.rpc.telegram.Telegram._init", MagicMock())
    bot = MagicMock()
    bot.send_message = AsyncMock()
    freqtradebot = get_patched_freqtradebot(mocker, default_conf)
    rpc = RPC(freqtradebot)

    invalid_keys_list = [["/not_valid", "/profit"], ["/daily"], ["/alsoinvalid"]]
    default_keys_list = [
        ["/daily", "/profit", "/balance"],
        ["/status", "/status table"],
        ["/start", "/stop", "/help"],
    ]
    default_keyboard = ReplyKeyboardMarkup(default_keys_list)

    custom_keys_list = [
        ["/daily", "/stats", "/balance", "/profit", "/profit 5"],
        ["/analysis", "/start", "/reload_config", "/help"],
    ]
    custom_keyboard = ReplyKeyboardMarkup(custom_keys_list)

    def init_telegram(freqtradebot):
        telegram = Telegram(rpc, default_conf)
        telegram._app = MagicMock()
        telegram._app.bot = bot
        return telegram

    # no keyboard in config -> default keyboard
    freqtradebot.config["telegram"]["enabled"] = True
    telegram = init_telegram(freqtradebot)
    await telegram._send_msg("test")
    used_keyboard = bot.send_message.call_args[1]["reply_markup"]
    assert used_keyboard == default_keyboard

    # invalid keyboard in config -> default keyboard
    freqtradebot.config["telegram"]["enabled"] = True
    freqtradebot.config["telegram"]["keyboard"] = invalid_keys_list
    err_msg = (
        re.escape(
            "config.telegram.keyboard: Invalid commands for custom "
            "Telegram keyboard: ['/not_valid', '/alsoinvalid']"
            "\nvalid commands are: "
        )
        + r"*"
    )
    with pytest.raises(OperationalException, match=err_msg):
        telegram = init_telegram(freqtradebot)

    # valid keyboard in config -> custom keyboard
    freqtradebot.config["telegram"]["enabled"] = True
    freqtradebot.config["telegram"]["keyboard"] = custom_keys_list
    telegram = init_telegram(freqtradebot)
    await telegram._send_msg("test")
    used_keyboard = bot.send_message.call_args[1]["reply_markup"]
    assert used_keyboard == custom_keyboard
    assert log_has(
        "using custom keyboard from config.json: "
        "[['/daily', '/stats', '/balance', '/profit', '/profit 5'], ['/analysis', "
        "'/start', '/reload_config', '/help']]",
        caplog,
    )


async def test_change_market_direction(default_conf, mocker, update) -> None:
    telegram, _, _msg_mock = get_telegram_testobject(mocker, default_conf)
    assert telegram._rpc._freqtrade.strategy.market_direction == MarketDirection.NONE
    context = MagicMock()
    context.args = ["long"]
    await telegram._changemarketdir(update, context)
    assert telegram._rpc._freqtrade.strategy.market_direction == MarketDirection.LONG
    context = MagicMock()
    context.args = ["invalid"]
    await telegram._changemarketdir(update, context)
    assert telegram._rpc._freqtrade.strategy.market_direction == MarketDirection.LONG


async def test_telegram_list_custom_data(default_conf_usdt, update, ticker, fee, mocker) -> None:
    mocker.patch.multiple(
        EXMS,
        fetch_ticker=ticker,
        get_fee=fee,
    )
    telegram, _freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf_usdt)

    # Create some test data
    create_mock_trades_usdt(fee)
    # No trade id
    context = MagicMock()
    await telegram._list_custom_data(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "Trade-id not set." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    #
    context.args = ["1"]
    await telegram._list_custom_data(update=update, context=context)
    assert msg_mock.call_count == 1
    assert "No custom-data found for Trade ID: 1." in msg_mock.call_args_list[0][0][0]
    msg_mock.reset_mock()

    # Add some custom data
    trade1 = Trade.get_trades_proxy()[0]
    trade1.set_custom_data("test_int", 1)
    trade1.set_custom_data("test_dict", {"test": "dict"})
    Trade.commit()
    context.args = [f"{trade1.id}"]
    await telegram._list_custom_data(update=update, context=context)
    assert msg_mock.call_count == 3
    assert "Found custom-data entries: " in msg_mock.call_args_list[0][0][0]
    assert (
        "*Key:* `test_int`\n*Type:* `int`\n*Value:* `1`\n*Create Date:*"
    ) in msg_mock.call_args_list[1][0][0]
    assert (
        "*Key:* `test_dict`\n*Type:* `dict`\n*Value:* `{'test': 'dict'}`\n*Create Date:* `"
    ) in msg_mock.call_args_list[2][0][0]

    msg_mock.reset_mock()


def test_notification_settings(default_conf_usdt, mocker):
    (telegram, _, _) = get_telegram_testobject(mocker, default_conf_usdt)
    telegram._config["telegram"].update(
        {
            "notification_settings": {
                "status": "silent",
                "warning": "on",
                "startup": "off",
                "entry": "silent",
                "entry_fill": "on",
                "entry_cancel": "silent",
                "exit": {
                    "roi": "silent",
                    "emergency_exit": "on",
                    "force_exit": "on",
                    "exit_signal": "silent",
                    "trailing_stop_loss": "on",
                    "stop_loss": "on",
                    "stoploss_on_exchange": "on",
                    "custom_exit": "silent",
                    "partial_exit": "off",
                },
                "exit_fill": {
                    "roi": "silent",
                    "partial_exit": "off",
                    "*": "silent",  # Default to silent
                },
                "exit_cancel": "on",
                "protection_trigger": "off",
                "protection_trigger_global": "on",
                "strategy_msg": "off",
                "show_candle": "off",
            }
        }
    )

    loudness = telegram._message_loudness

    assert loudness({"type": RPCMessageType.ENTRY, "exit_reason": ""}) == "silent"
    assert loudness({"type": RPCMessageType.ENTRY_FILL, "exit_reason": ""}) == "on"
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": ""}) == "on"
    # Default to silent due to "*" definition
    assert loudness({"type": RPCMessageType.EXIT_FILL, "exit_reason": ""}) == "silent"
    assert loudness({"type": RPCMessageType.PROTECTION_TRIGGER, "exit_reason": ""}) == "off"
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "roi"}) == "silent"
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "partial_exit"}) == "off"
    # Not given key defaults to on
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "cust_exit112"}) == "on"

    assert loudness({"type": RPCMessageType.EXIT_FILL, "exit_reason": "roi"}) == "silent"
    assert loudness({"type": RPCMessageType.EXIT_FILL, "exit_reason": "partial_exit"}) == "off"
    # Default to silent due to "*" definition
    assert loudness({"type": RPCMessageType.EXIT_FILL, "exit_reason": "cust_exit112"}) == "silent"

    # Simplified setup for exit
    telegram._config["telegram"].update(
        {
            "notification_settings": {
                "status": "silent",
                "warning": "on",
                "startup": "off",
                "entry": "silent",
                "entry_fill": "on",
                "entry_cancel": "silent",
                "exit": "off",
                "exit_cancel": "on",
                "exit_fill": "on",
                "protection_trigger": "off",
                "protection_trigger_global": "on",
                "strategy_msg": "off",
                "show_candle": "off",
            }
        }
    )

    assert loudness({"type": RPCMessageType.EXIT_FILL, "exit_reason": "roi"}) == "on"
    # All regular exits are off
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "roi"}) == "off"
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "partial_exit"}) == "off"
    assert loudness({"type": RPCMessageType.EXIT, "exit_reason": "cust_exit112"}) == "off"


async def test__tg_info(default_conf_usdt, mocker, update):
    (telegram, _, _) = get_telegram_testobject(mocker, default_conf_usdt)
    context = AsyncMock()

    await telegram._tg_info(update, context)

    assert context.bot.send_message.call_count == 1
    content = context.bot.send_message.call_args[1]["text"]
    assert "Freqtrade Bot Info:\n" in content
    assert '"chat_id": "1235"' in content


@pytest.mark.parametrize(
    "value,expected",
    [
        (60000.0, "60,000"),
        (1234.5, "1,234"),
        (12.345, "12.35"),
        (0.0123, "0.0123"),
        (0.00012345, "0.00012345"),
    ],
)
def test__smc_fmt_price(value, expected):
    assert Telegram._smc_fmt_price(value) == expected


def test__smc_entry_zone_prefers_bull_ob_then_band():
    # Bull OB ưu tiên -> trả (lo, hi, note, invalidation=đáy OB cho LONG)
    lv = {"bull_ob_bot": 100.0, "bull_ob_top": 110.0}
    lo, hi, note, inval = Telegram._smc_entry_zone(lv, 105.0, "long")
    assert (lo, hi) == (100.0, 110.0) and note == "Bull OB" and inval == 100.0

    # SHORT Bear OB -> invalidation = đỉnh OB
    lv2 = {"bear_ob_bot": 100.0, "bear_ob_top": 110.0}
    _, _, note2, inval2 = Telegram._smc_entry_zone(lv2, 105.0, "short")
    assert note2 == "Bear OB" and inval2 == 110.0

    # Không có OB nhưng có ATR -> dải ±0.25 ATR quanh giá (KHÔNG trả một điểm).
    lo, hi, note, inval = Telegram._smc_entry_zone({"atr": 4.0}, 105.0)
    assert (lo, hi) == (104.0, 106.0) and "ATR" in note and inval is None

    # Không có gì -> dải ±0.15% quanh giá, vẫn là một KHOẢNG.
    lo, hi, note, inval = Telegram._smc_entry_zone({}, 200.0)
    assert lo < 200.0 < hi and "±0.15%" in note and inval is None


def test__smc_price_vs_zone():
    assert "trong vùng" in Telegram._smc_price_vs_zone(105.0, 100.0, 110.0)
    assert "trên vùng" in Telegram._smc_price_vs_zone(121.0, 100.0, 110.0)
    assert "hồi xuống" in Telegram._smc_price_vs_zone(121.0, 100.0, 110.0)
    assert "dưới vùng" in Telegram._smc_price_vs_zone(90.0, 100.0, 110.0)
    assert "hồi lên" in Telegram._smc_price_vs_zone(90.0, 100.0, 110.0)


def test__smc_liquidity_targets_sources_and_direction():
    """Mốc TP phải đến từ THANH KHOẢN/CẤU TRÚC, và chỉ lấy mốc phía trước."""
    lv = {
        "pivots": [
            {"type": "L", "price": 58000.0},  # đáy cũ = SSL (phía dưới -> hợp lệ cho SHORT)
            {"type": "L", "price": 70000.0},  # đáy cũ nhưng nằm TRÊN entry -> phải loại
            {"type": "H", "price": 66000.0},  # đỉnh cũ -> chỉ dùng cho LONG
        ],
        "bull_ob_top": 59000.0,
        "fvg_bot": 57000.0,
        "vp_poc": 61000.0,
        "vp_val": 56000.0,
        "equilibrium": 62000.0,
    }
    got = Telegram._smc_liquidity_targets(lv, 64000.0, "short")
    prices = [p for p, _ in got]
    reasons = " ".join(r for _, r in got)
    assert 58000.0 in prices and "đáy cũ (SSL)" in reasons
    assert 70000.0 not in prices  # nằm sau lưng -> loại
    assert 66000.0 not in prices  # đỉnh cũ không phải mục tiêu của SHORT
    assert 59000.0 in prices and "Bull OB" in reasons
    assert 57000.0 in prices and "FVG" in reasons
    assert 61000.0 in prices and "POC" in reasons
    assert 56000.0 in prices and "VAL" in reasons
    assert 62000.0 in prices and "equilibrium" in reasons

    # LONG chỉ lấy ĐỈNH cũ (BSL) phía trên; pivot đáy 70000 tuy nằm trên vẫn bị
    # loại vì buy-side liquidity nằm trên ĐỈNH cũ, không phải trên đáy cũ.
    up = Telegram._smc_liquidity_targets({"pivots": lv["pivots"]}, 64000.0, "long")
    assert [p for p, _ in up] == [66000.0]

    # Đỉnh/đáy SWING là mốc giá CÓ THẬT -> vẫn là nguồn thanh khoản hợp lệ
    # (thay cho mốc "Fib 1.0" cũ). Chỉ lấy đúng phía theo hướng lệnh.
    sw = Telegram._smc_liquidity_targets(
        {"swing_high": 68000.0, "swing_low": 55000.0}, 64000.0, "long"
    )
    assert [p for p, _ in sw] == [68000.0]
    assert "đỉnh swing (BSL)" in sw[0][1]
    sw_dn = Telegram._smc_liquidity_targets(
        {"swing_high": 68000.0, "swing_low": 55000.0}, 64000.0, "short"
    )
    assert [p for p, _ in sw_dn] == [55000.0]


def test__smc_tp_ladder_has_no_synthetic_rung():
    """Thang TP KHÔNG được chứa mốc tổng hợp "R:R 1:2" — chỉ giá có thật.

    Trước đây mốc 2R được chèn thẳng vào thang để R:R luôn nhìn đủ chuẩn. Đó là
    ngoại suy: không có thanh khoản nào ở mức đó. Khi cấu trúc không với tới 2R,
    kết luận đúng là bỏ lệnh, không phải thêm một dòng cho đẹp bảng.
    """
    lv = {  # nhiều mốc gần entry -> đủ lấp đầy max_rows
        "pivots": [{"type": "L", "price": p} for p in (59000.0, 58800.0, 58500.0, 58200.0)],
        "vp_poc": 59500.0,
        "vp_val": 59200.0,
        "bull_ob_top": 58900.0,
        "swing_high": 67000.0,
        "swing_low": 58000.0,
    }
    # risk 700 -> 2R = 60000 - 1400 = 58600, KHÔNG trùng mốc cấu trúc nào.
    merged, _, _ = Telegram._smc_tp_ladder(lv, 60000.0, 60000.0, 700.0, "short", max_rows=6)
    assert len(merged) <= 6
    assert not any("TỐI THIỂU" in name for _, name in merged)
    assert not any(abs(p - 58600.0) < 1 for p, _ in merged), "mốc 2R tổng hợp vẫn còn"
    # Mốc cấu trúc XA NHẤT (đáy swing 58000) vẫn phải sống sót khi cắt bớt.
    assert any(abs(p - 58000.0) < 1 for p, _ in merged)
    assert all("Fib" not in name for _, name in merged)


def test__smc_tp_ladder_keeps_farthest_structural_target():
    """Mốc CẤU TRÚC xa nhất phải hiển thị, không bị các mốc gần đè mất.

    Regression thật (ETH/USDT 4h): có đáy cũ ở 2.7R nhưng bị cắt, bảng chỉ hiện
    tới 0.9R + mốc 2R tổng hợp -> giấu đúng mục tiêu thanh khoản tốt nhất.
    """
    lv = {
        "pivots": [
            {"type": "L", "price": p}
            for p in (2288.0, 2262.0, 2252.0, 2249.0, 2220.0, 1938.0, 1916.0)
        ],
        "vp_poc": 2288.5,
        "swing_high": 2436.0,
        "swing_low": 2220.0,
    }
    merged, _, _ = Telegram._smc_tp_ladder(lv, 2302.0, 2295.0, 141.0, "short")
    prices = [p for p, _ in merged]
    assert any(abs(p - 1916.0) < 1 for p in prices), "mốc cấu trúc xa nhất bị cắt mất"
    assert abs(merged[-1][0] - 1916.0) < 1  # mốc xa nhất = mốc quyết định R:R xa
    assert len(merged) <= 6


def test__smc_tp_ladder_drops_far_targets_but_reports_them():
    """Trần khoảng cách: mốc quá xa bị bỏ khỏi thang NHƯNG phải được nói ra.

    Thực đo BTC/USDT 4h: đỉnh cũ ở +41.5% (13R) — đúng cấu trúc, vô dụng để lập
    kế hoạch. Cắt im lặng sẽ khiến bảng trông như "đã bao hết mốc".
    """
    lv = {
        "pivots": [
            {"type": "H", "price": 62000.0},  # 2.0R  -> giữ
            {"type": "H", "price": 66000.0},  # 6.0R  -> giữ
            {"type": "H", "price": 82850.0},  # 22.8R -> vượt trần 8R, bỏ
        ]
    }
    merged, dropped, _ = Telegram._smc_tp_ladder(lv, 60000.0, 60000.0, 1000.0, "long", max_r=8.0)
    prices = [p for p, _ in merged]
    assert 82850.0 not in prices
    assert [p for p, _ in dropped] == [82850.0]
    # Mốc xa nhất phải tính SAU khi cắt trần, nếu không R:R xa sẽ trỏ mốc đã bỏ.
    assert merged[-1][0] == 66000.0

    # Trần được nêu trong ghi chú của plan block, kèm mốc GẦN NHẤT trong số đã bỏ.
    # Ở đây SL bám đáy OB nên risk chỉ 795 -> cả 66,000 (8.2R) lẫn 82,850 vượt trần.
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    lv2 = dict(lv, bull_ob_bot=59000.0, bull_ob_top=59500.0, swing_low=59000.0)
    out = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv2, 59200.0, "long", 59200.0))
    assert "Đã bỏ 2 mốc quá xa" in out
    assert "66,000" in out and "8.2R" in out  # nêu đích danh mốc gần nhất bị bỏ
    assert "82,850" not in out  # mốc quá xa không còn trong bảng

    # Chưa có SL (rr_risk None) -> trần rơi về % giá.
    _, dropped_pct, _ = Telegram._smc_tp_ladder(lv, 60000.0, 60000.0, None, "long")
    assert [p for p, _ in dropped_pct] == [82850.0]  # +38% > trần 25%


def test__smc_tp_ladder_drops_targets_not_worth_the_sl():
    """Regression SOL/USDT: TP nằm SAU mép entry xấu nhất, và TP quá nhỏ so với SL.

    Bảng thật user gặp: SHORT mép bán 73.88, SL 79.27 (risk 5.39) mà TP1 = 74.10
    — CAO hơn giá bán, tức lệnh LỖ, nhưng hiển thị "0.0R" vì tính bằng trị tuyệt
    đối. TP2 = 73.39 thì chỉ 0.09R: ôm rủi ro 1R để ăn 0.09R.
    """
    lv = {
        "bull_ob_top": 74.10,  # nằm giữa mép xấu nhất (73.88) và tâm vùng -> LỖ
        "pivots": [{"type": "L", "price": p} for p in (73.39, 64.04, 60.13)],
        "vp_val": 73.39,
        "swing_low": 73.39,
    }
    merged, _dropped, too_close = Telegram._smc_tp_ladder(lv, 74.14, 73.88, 5.39, "short")
    prices = [round(p, 2) for p, _ in merged]
    # Mốc sau lưng mép xấu nhất KHÔNG được coi là mục tiêu.
    assert 74.10 not in prices
    assert 74.10 not in [round(p, 2) for p, _ in too_close]
    # Mốc 0.09R bị loại vì không tương xứng với SL, nhưng phải được báo lại.
    assert 73.39 not in prices
    assert 73.39 in [round(p, 2) for p, _ in too_close]
    # Chỉ còn mốc đủ lớn: 1.8R và 2.5R.
    assert prices == [64.04, 60.13]
    assert all(abs(p - 73.88) / 5.39 >= 0.5 for p in prices)


def test__smc_tp_ladder_excludes_fib_extensions():
    """Fib 1.272/1.618 không còn được sinh ra: chỉ là phép nhân dải swing.

    Đáy swing (mốc "Fib 1.0" cũ) vẫn giữ vì đó là mức giá CÓ THẬT trên chart.
    """
    lv = {"swing_high": 66000.0, "swing_low": 60000.0}
    merged, _, _ = Telegram._smc_tp_ladder(lv, 65000.0, 65000.0, 1000.0, "short")
    prices = [round(p) for p, _ in merged]
    assert prices == [60000]  # chỉ đáy swing
    assert "đáy swing (SSL)" in merged[0][1]
    assert 58368 not in prices  # 1.272 ext = 66000 - 1.272*6000
    assert 56292 not in prices  # 1.618 ext


def test__smc_plan_block_reports_real_rr_only():
    """R:R công bố phải ĐO trên mốc cấu trúc thật, tính ở MÉP XẤU NHẤT.

    Không còn dòng "R:R 1 : 2.0 tối thiểu" mặc định lẫn mốc TP 2R tổng hợp — cả
    hai đều đúng bất kể cấu trúc có với tới hay không, nên vô nghĩa.
    """
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    lv = {
        "bear_ob_bot": 64800.0,
        "bear_ob_top": 65400.0,
        "swing_high": 66956.0,
        "swing_low": 57800.0,
    }
    out = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 64026.0, "short", 64026.0))
    # Giá hiện tại hiển thị + vị trí so với vùng.
    assert "Giá TT" in out and "64,026" in out and "dưới vùng" in out
    # Entry là KHOẢNG a-b.
    assert "64,800-65,400" in out
    # SHORT: mép xấu nhất = ĐÁY vùng (bán rẻ nhất) -> R:R tính ở đó.
    assert "tính ở mép 64,800" in out
    # SL = đỉnh OB + đệm 0.5% = 65400*1.005 = 65727 -> risk 927.
    assert "65,727" in out
    # TP duy nhất là đáy swing THẬT 57800 = (64800-57800)/927 = 7.6R.
    assert "57,800" in out and "đáy swing (SSL)" in out
    assert "R:R TP1" in out and "1 : 7.6" in out
    # Không còn mốc/nhãn ngoại suy nào.
    assert "TỐI THIỂU" not in out and "ngoại suy" not in out
    assert "62,946" not in out  # mốc 2R tổng hợp cũ
    assert "BỎ LỆNH" not in out  # 7.6R > 1:2 -> setup hợp lệ


def test__smc_plan_block_says_skip_when_structure_cannot_reach_2r():
    """Cấu trúc thật không với tới 1:2 -> BỎ LỆNH, không bịa mục tiêu cho đủ."""
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    lv = {  # OB rộng -> risk 2330; mốc cấu trúc duy nhất phía trước là đáy swing 63000
        "bear_ob_bot": 64000.0,
        "bear_ob_top": 66000.0,
        "swing_high": 66500.0,
        "swing_low": 63000.0,
    }
    out = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 64500.0, "short", 64500.0))
    assert "BỎ LỆNH" in out
    # mép xấu nhất 64000, SL 66330 -> risk 2330; đáy swing 63000 chỉ cách 1000 = 0.4R
    # -> dưới sàn 0.5R nên bị loại khỏi bảng, NHƯNG phải nói ra là đã loại.
    assert "Đã bỏ 1 mốc quá gần, không tương xứng với SL" in out and "0.4R" in out
    assert "chưa có mốc thanh khoản/cấu trúc nào phía trước" in out
    # Không được có mốc ngoại suy nào kéo R:R lên cho đủ chuẩn.
    assert "ngoại suy" not in out and "TỐI THIỂU" not in out
    assert "62,946" not in out


def test__smc_plan_block_full_long_setup():
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    lv = {
        "bull_ob_bot": 59500.0,
        "bull_ob_top": 60200.0,
        "swing_low": 59000.0,
        "swing_high": 62000.0,
    }
    out = "\n".join(Telegram._smc_plan_block(strategy, "1h", lv, 60000.0, "long"))
    assert "KỊCH BẢN CHÍNH: LONG" in out
    assert "Entry" in out and "59,500-60,200" in out and "Bull OB" in out
    # SL ngay dưới ĐÁY OB (59500): 59500*(1-0.5%) = 59202.5 -> 59,202
    assert "59,202" in out and "đáy OB" in out
    # TP duy nhất = đỉnh swing THẬT 62000; Fib ext 63854 KHÔNG còn được sinh ra.
    assert "62,000" in out and "đỉnh swing (BSL)" in out
    assert "63,854" not in out and "Fib 1." not in out
    # (62000-60200)/997.5 = 1.8R < 2 -> phải khuyên bỏ lệnh thay vì vẽ thêm mốc.
    assert "R:R xa" in out and "1 : 1.8" in out
    assert "BỎ LỆNH" in out
    assert "```" in out  # bảng monospace
    assert "Trigger" in out and "Vô hiệu hóa" in out


def test__smc_indicator_line():
    def val(v):
        from math import isnan

        return None if v is None or (isinstance(v, float) and isnan(v)) else v

    # RSI quá bán, giá trên cả 2 EMA và MA20, MACD > signal -> tất cả 🟢.
    row = {
        "rsi": 25.0,
        "ema50": 59000.0,
        "ema200": 58000.0,
        "sma20": 59500.0,
        "macd": 5.0,
        "macdsignal": 2.0,
    }
    assert (
        Telegram._smc_indicator_line(row, 60000.0, val) == "RSI 25🟢 · EMA 🟢🟢 · MA20 🟢 · MACD 🟢"
    )

    # RSI quá mua, giá dưới EMA200 & MA20, MACD < signal.
    row2 = {
        "rsi": 75.0,
        "ema50": 61000.0,
        "ema200": 62000.0,
        "sma20": 60500.0,
        "macd": 1.0,
        "macdsignal": 4.0,
    }
    assert (
        Telegram._smc_indicator_line(row2, 60000.0, val)
        == "RSI 75🔴 · EMA 🔴🔴 · MA20 🔴 · MACD 🔴"
    )

    # Thiếu cột chỉ báo (strategy chưa reload) -> trả chuỗi rỗng.
    assert Telegram._smc_indicator_line({}, 60000.0, val) == ""


def test__smc_plan_block_short_setup():
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    lv = {
        "bear_ob_bot": 60000.0,
        "bear_ob_top": 60500.0,
        "swing_high": 61000.0,
        "equilibrium": 59000.0,
        "swing_low": 57500.0,
        "bull_ob_top": 58200.0,
    }
    out = "\n".join(Telegram._smc_plan_block(strategy, "1h", lv, 60250.0, "short"))
    assert "KỊCH BẢN CHÍNH: SHORT" in out
    assert "60,000-60,500" in out and "Bear OB" in out
    # SL ngay trên ĐỈNH OB (60500): 60500*(1+0.5%) = 60802.5 -> 60,802
    assert "60,802" in out and "đỉnh OB" in out
    # TP đều là mốc THẬT: equilibrium 59000, biên Bull OB 58200, đáy swing 57500.
    assert "57,500" in out and "đáy swing (SSL)" in out
    assert "59,000" in out and "equilibrium" in out
    assert "R:R" in out and "Fib" not in out


def test__smc_plan_block_sweep_vs_break_around_sl():
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    # swing_low 58894 -> SL ~58600. Nến ĐÓNG 59193 (trên SL).
    lv = {"swing_low": 58894.0, "equilibrium": 63212.0}

    # 1) Live 58541 < SL nhưng nến đóng trên SL -> SWEEP (wick), CHƯA gãy.
    sweep = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 59193.0, "long", 58541.0))
    assert "QUÉT" in sweep
    assert "CHƯA xác nhận gãy" in sweep
    assert "GÃY (BOS" not in sweep  # không kết luận gãy

    # 2) Nến ĐÓNG dưới SL (price 58400) -> cấu trúc GÃY thật.
    brk = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 58400.0, "long", 58300.0))
    assert "ĐÓNG dưới SL" in brk
    assert "GÃY (BOS giảm)" in brk

    # 3) Cả live lẫn nến đóng đều trên SL -> không cảnh báo.
    ok = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 59193.0, "long", 59000.0))
    assert "QUÉT" not in ok and "GÃY" not in ok


def test__smc_break_note_reclaim_into_ob():
    # Live thủng SL nhưng đã về lại TRONG Bull OB -> nhấn mạnh khả năng sweep hợp lệ.
    lv = {"bull_ob_bot": 58500.0, "bull_ob_top": 59500.0}
    note = Telegram._smc_break_note("long", 59193.0, 58550.0, 58600.0, lv)
    assert note is not None
    assert "SWEEP cao" in note and "VẪN HỢP LỆ" in note


def test__smc_plan_block_handles_missing_levels():
    strategy = SimpleNamespace(sl_buffer_pct=SimpleNamespace(value=0.5))
    # Có Bull OB nhưng KHÔNG có mốc thanh khoản nào phía trên -> SL dựng được,
    # TP thì không. Trước đây chỗ này vẫn đẻ ra mốc 2R = 131 từ SL và công bố
    # "R:R 1 : 2.0" dù chẳng có gì ở 131 — đúng kiểu số đẹp mà rỗng.
    lv = {"bull_ob_bot": 100.0, "bull_ob_top": 110.0}
    out = "\n".join(Telegram._smc_plan_block(strategy, "4h", lv, 105.0, "long"))
    assert "đáy OB" in out  # SL bám đáy OB (không n/a)
    assert "chưa có mốc thanh khoản/cấu trúc nào phía trước" in out
    assert "131" not in out and "TỐI THIỂU" not in out
    assert "1 : 2.0" not in out
    assert "BỎ LỆNH" in out  # không biết lời tới đâu -> không vào lệnh

    # Thiếu cả OB lẫn swing -> SL n/a.
    out2 = "\n".join(Telegram._smc_plan_block(strategy, "4h", {}, 105.0, "long"))
    assert "SL" in out2 and "n/a" in out2


def test__smc_signal_report_no_data():
    strategy = SimpleNamespace(timeframe="4h", sl_buffer_pct=SimpleNamespace(value=0.5))
    out = Telegram._smc_signal_report(strategy, "BTC/USDT", ["4h"], {}, {}, None, "now")
    assert len(out) == 1 and "Không lấy được dữ liệu" in out[0]


def test__smc_signal_report_full_template():
    strategy = SimpleNamespace(timeframe="4h", sl_buffer_pct=SimpleNamespace(value=0.5))
    base = {
        "internal_trend": -1,
        "swing_trend": -1,
        "swing_high": 61000.0,
        "swing_low": 58894.0,
        "equilibrium": 60000.0,
        "premium_level": 60800.0,
        "bear_ob_bot": 60500.0,
        "bear_ob_top": 61200.0,
        "bull_ob_bot": 58800.0,
        "bull_ob_top": 59400.0,
        "fvg_bot": 59100.0,
        "fvg_top": 59300.0,
        "rsi": 34.0,
        "ema50": 59800.0,
        "ema200": 60500.0,
        "macd": -2.0,
        "macdsignal": -1.0,
    }
    levels = {tf: dict(base) for tf in ("15m", "1h", "4h", "1d")}
    prices = {tf: 59193.0 for tf in levels}
    out = "\n".join(
        Telegram._smc_signal_report(
            strategy, "BTC/USDT", ["15m", "1h", "4h", "1d"], levels, prices, 58541.0, "2026-06-30"
        )
    )
    # 7 mục theo template
    for header in (
        "Bức tranh đa khung",
        "Cấu trúc & SMC",
        "Thanh khoản",
        "Sóng Elliott",
        "Hợp lưu kỹ thuật",
        "Kế hoạch giao dịch",
        "Quản trị rủi ro",
    ):
        assert header in out
    # Đồng thuận giảm -> ưu tiên SHORT
    assert "Vị thế ưu tiên: *SHORT*" in out
    assert "KỊCH BẢN CHÍNH: SHORT" in out
    # Giá đã quét SSL (live 58541 < swing_low 58894)
    assert "quét SSL" in out
    assert "golden pocket" in out  # Fib retracement (entry)
    assert "Sóng Elliott" in out and "ước lượng" in out  # mục 4 có ước lượng
    # TP là thanh khoản thật (đáy swing / FVG / OB đối diện), không phải Fib ext.
    assert "đáy swing (SSL)" in out
    assert "Fib 1." not in out and "ngoại suy" not in out
    assert "NFA" in out  # disclaimer


def test__smc_fib_line():
    lv = {"swing_high": 61000.0, "swing_low": 59000.0}  # range 2000
    # SHORT: từ đáy lên -> 0.618 = 59000+1236 = 60236 ; 0.786 = 60572
    short = Telegram._smc_fib_line(lv, "short")
    assert short is not None and "golden pocket" in short and "premium" in short
    assert "60,236" in short and "60,572" in short
    # LONG: từ đỉnh xuống -> 0.618 = 61000-1236 = 59764 ; 0.786 = 59428
    long = Telegram._smc_fib_line(lv, "long")
    assert "discount" in long and "59,428" in long and "59,764" in long
    # Thiếu swing -> None
    assert Telegram._smc_fib_line({"swing_high": 100.0}, "long") is None


def test__smc_quick_pairs():
    # Spot: lấy nguyên cặp có trong whitelist, cặp thiếu dựng theo hậu tố cặp đầu.
    got = Telegram._smc_quick_pairs(["ETH/USDT", "BTC/USDT", "SUI/USDT"], "USDT")
    assert got == ["BTC/USDT", "ETH/USDT", "XAU/USDT", "SOL/USDT"]
    # Futures: cặp thiếu (XAU) phải giữ hậu tố :USDT của whitelist, không rớt về spot.
    fut = Telegram._smc_quick_pairs(["BTC/USDT:USDT", "ETH/USDT:USDT"], "USDT")
    assert fut == ["BTC/USDT:USDT", "ETH/USDT:USDT", "XAU/USDT:USDT", "SOL/USDT:USDT"]
    # Whitelist rỗng -> dựng toàn bộ theo stake currency.
    assert Telegram._smc_quick_pairs([], "USDC") == [
        "BTC/USDC",
        "ETH/USDC",
        "XAU/USDC",
        "SOL/USDC",
    ]


def test__smc_quick_timeframes(default_conf, mocker) -> None:
    """/smc bám theo khung của bot, không cứng 4h — nếu không bot 5m trả kế hoạch swing 4h."""
    _telegram, freqtradebot, _msg_mock = get_telegram_testobject(mocker, default_conf)

    # Bot swing: đúng một khung của chính nó.
    freqtradebot.strategy.timeframe = "4h"
    assert Telegram._smc_quick_timeframes(freqtradebot) == ["4h"]
    freqtradebot.strategy.timeframe = "1h"
    assert Telegram._smc_quick_timeframes(freqtradebot) == ["1h"]

    # Bot scalping (< 1h): cả bộ scalp, thấp -> cao. Một khung duy nhất sẽ khiến
    # _smc_signal_report lấy bias ngay trên khung vào lệnh.
    freqtradebot.strategy.timeframe = "5m"
    assert Telegram._smc_quick_timeframes(freqtradebot) == ["5m", "15m"]

    # Không đọc được khung -> dự phòng, không được ném lỗi (/help cũng gọi hàm này).
    freqtradebot.strategy.timeframe = MagicMock()
    assert Telegram._smc_quick_timeframes(freqtradebot) == ["4h"]


async def test_smc_quick_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    freqtradebot.config["stake_currency"] = "USDT"
    freqtradebot.active_pair_whitelist = ["BTC/USDT", "ETH/USDT"]
    freqtradebot.strategy.timeframe = "4h"
    report_mock = mocker.patch.object(
        telegram, "_build_analysis_report", AsyncMock(return_value="report")
    )

    # /smc không tham số -> header + 1 báo cáo 4h cho mỗi cặp cố định
    context = MagicMock()
    context.args = []
    await telegram._smc(update=update, context=context)

    assert msg_mock.call_count == 5
    assert "4h" in msg_mock.call_args_list[0][0][0]
    assert [c[0][1] for c in report_mock.call_args_list] == [
        "BTC/USDT",
        "ETH/USDT",
        "XAU/USDT",
        "SOL/USDT",
    ]
    assert all(c[0][2] == ["4h"] for c in report_mock.call_args_list)

    # Cùng lệnh /smc trên bot 5m -> khung scalp, không phải 4h của bot swing.
    msg_mock.reset_mock()
    report_mock.reset_mock()
    freqtradebot.strategy.timeframe = "5m"
    await telegram._smc(update=update, context=context)
    assert "5m + 15m" in msg_mock.call_args_list[0][0][0]
    assert all(c[0][2] == ["5m", "15m"] for c in report_mock.call_args_list)
    freqtradebot.strategy.timeframe = "4h"

    # Một cặp lỗi không được chặn các cặp còn lại — vẫn đủ 4 tin nhắn sau header.
    msg_mock.reset_mock()
    report_mock.side_effect = [RuntimeError("boom"), "report", "report", "report"]
    await telegram._smc(update=update, context=context)
    assert msg_mock.call_count == 5
    assert "BTC/USDT" in msg_mock.call_args_list[1][0][0]  # cặp lỗi -> cảnh báo, không im lặng
    assert "⚠️" in msg_mock.call_args_list[1][0][0]

    # Có tham số -> ủy quyền cho /analysis (1 cặp, các khung user gõ)
    msg_mock.reset_mock()
    report_mock.reset_mock(side_effect=True)
    context.args = ["ETH", "1d"]
    await telegram._smc(update=update, context=context)
    assert msg_mock.call_count == 1
    assert report_mock.call_args[0][1] == "ETH/USDT"
    assert report_mock.call_args[0][2] == ["1d"]


async def test_smc_scalp_handle(default_conf, update, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    freqtradebot.config["stake_currency"] = "USDT"
    freqtradebot.active_pair_whitelist = ["BTC/USDT", "ETH/USDT"]
    report_mock = mocker.patch.object(
        telegram, "_build_analysis_report", AsyncMock(return_value="report")
    )

    # /scalp không tham số -> 4 cặp cố định, khung 5m + 15m ĐÚNG THỨ TỰ thấp -> cao
    context = MagicMock()
    context.args = []
    await telegram._scalp(update=update, context=context)

    assert msg_mock.call_count == 5
    assert [c[0][1] for c in report_mock.call_args_list] == [
        "BTC/USDT",
        "ETH/USDT",
        "XAU/USDT",
        "SOL/USDT",
    ]
    # Thứ tự quyết định khung nào là bias (cuối) và khung nào dựng kế hoạch (đầu).
    assert all(c[0][2] == ["5m", "15m"] for c in report_mock.call_args_list)

    # Có tham số -> 1 cặp; khung user gõ đè mặc định
    msg_mock.reset_mock()
    report_mock.reset_mock()
    context.args = ["SOL", "5m", "15m", "1h"]
    await telegram._scalp(update=update, context=context)
    assert report_mock.call_count == 1
    assert report_mock.call_args[0][1] == "SOL/USDT"
    assert report_mock.call_args[0][2] == ["5m", "15m", "1h"]

    # Chỉ có cặp, không có khung -> vẫn dùng mặc định scalp (không rơi về 15m/1h/4h/1d)
    report_mock.reset_mock()
    context.args = ["ETH"]
    await telegram._scalp(update=update, context=context)
    assert report_mock.call_args[0][2] == ["5m", "15m"]


def test__smc_signal_report_scalp_timeframes():
    """Khung 5m+15m: bias lấy khung cuối (15m), kế hoạch dựng trên khung đầu (5m)."""
    strategy = SimpleNamespace(timeframe="4h", minimal_roi={}, stoploss=-0.1)
    lv_5m = {"swing_trend": 1, "internal_trend": 1, "swing_high": 101.0, "swing_low": 99.0}
    lv_15m = {"swing_trend": 1, "internal_trend": 1, "swing_high": 102.0, "swing_low": 98.0}
    out = "\n".join(
        Telegram._smc_signal_report(
            strategy,
            "BTC/USDT",
            ["5m", "15m"],
            {"5m": lv_5m, "15m": lv_15m},
            {"5m": 100.0, "15m": 100.0},
            100.0,
            "now",
        )
    )
    assert "Loại: Scalping" in out
    assert "Chính (15m)" in out  # bias = khung cao nhất
    assert "*2️⃣ Cấu trúc & SMC* (`5m`)" in out  # kế hoạch = khung thấp nhất


def test__next_analysis_slot():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    # interval 4h, neo 7h -> slots 3,7,11,15,19,23
    nxt = Telegram._next_analysis_slot(datetime(2026, 7, 1, 8, 30, tzinfo=tz), 4, 7)
    assert nxt.hour == 11 and nxt.day == 1  # 08:30 -> 11:00 cùng ngày
    nxt2 = Telegram._next_analysis_slot(datetime(2026, 7, 1, 23, 30, tzinfo=tz), 4, 7)
    assert nxt2.hour == 3 and nxt2.day == 2  # 23:30 -> 03:00 hôm sau
    nxt3 = Telegram._next_analysis_slot(datetime(2026, 7, 1, 7, 0, tzinfo=tz), 4, 7)
    assert nxt3.hour == 11  # đúng 07:00 -> mốc kế tiếp, không lặp lại chính nó


def _auto_entry_decision(**over) -> dict:
    """Quyết định 'được phép vào lệnh' tối thiểu, ghi đè từng khoá để dựng ca lỗi."""
    return {
        "reject": None,
        "direction": "long",
        "entry_worst": 60000.0,
        "stop": 59000.0,
        "rr_first": 2.5,
        "timeframe": "4h",
        **over,
    }


def test__auto_entry_from_analysis_places_order(default_conf, mocker) -> None:
    telegram, freqtradebot, _msg = get_telegram_testobject(mocker, default_conf)
    fe = mocker.patch.object(telegram._rpc, "_rpc_force_entry", return_value=MagicMock())

    line = telegram._auto_entry_from_analysis(freqtradebot, "BTC/USDT", _auto_entry_decision())

    assert line is not None and line.startswith("✅")
    # Limit tại MÉP XẤU NHẤT của vùng — cùng con số mà R:R trong báo cáo tính trên đó.
    assert fe.call_args[0][1] == 60000.0
    assert fe.call_args[1]["order_type"] == "limit"
    assert fe.call_args[1]["enter_tag"] == "analysis_auto"


def test__auto_entry_from_analysis_skips(default_conf, mocker) -> None:
    """Ba trường hợp KHÔNG được vào lệnh — im lặng, không gọi tới sàn."""
    telegram, freqtradebot, _msg = get_telegram_testobject(mocker, default_conf)
    fe = mocker.patch.object(telegram._rpc, "_rpc_force_entry")

    # Báo cáo nói BỎ LỆNH -> hành động phải khớp với tin nhắn.
    assert (
        telegram._auto_entry_from_analysis(
            freqtradebot, "BTC/USDT", _auto_entry_decision(reject="R:R không đạt")
        )
        is None
    )
    # Short: strategy long-only, vào short sẽ tạo vị thế mà callback thoát lệnh không hiểu.
    assert (
        telegram._auto_entry_from_analysis(
            freqtradebot, "BTC/USDT", _auto_entry_decision(direction="short")
        )
        is None
    )
    assert fe.call_count == 0

    # Thiếu SL -> KHÔNG rơi về giá thị trường, vì đó sẽ là một lệnh khác với lệnh đã báo.
    line = telegram._auto_entry_from_analysis(
        freqtradebot, "BTC/USDT", _auto_entry_decision(stop=None)
    )
    assert line is not None and "thiếu giá entry hoặc SL" in line
    assert fe.call_count == 0


def test__auto_entry_from_analysis_reports_limit_reached(default_conf, mocker) -> None:
    """Hết slot là hoạt động bình thường của hạn mức — nhưng phải NÓI RA, không im lặng."""
    from freqtrade.rpc import RPCException

    telegram, freqtradebot, _msg = get_telegram_testobject(mocker, default_conf)
    mocker.patch.object(
        telegram._rpc,
        "_rpc_force_entry",
        side_effect=RPCException("Maximum number of trades is reached."),
    )

    line = telegram._auto_entry_from_analysis(freqtradebot, "BTC/USDT", _auto_entry_decision())
    assert line is not None and line.startswith("⛔")
    assert "Maximum number of trades" in line


async def test_run_scheduled_analysis_auto_entry(default_conf, mocker) -> None:
    telegram, freqtradebot, msg_mock = get_telegram_testobject(mocker, default_conf)
    freqtradebot.active_pair_whitelist = ["BTC/USDT", "ETH/USDT"]
    mocker.patch.object(
        telegram, "_build_analysis", AsyncMock(return_value=("report", _auto_entry_decision()))
    )
    auto = mocker.patch.object(telegram, "_auto_entry_from_analysis", return_value="✅ ok")

    # auto_entry TẮT -> chỉ gửi báo cáo, không đụng tới lệnh.
    await telegram._run_scheduled_analysis({"max_pairs": 2})
    assert auto.call_count == 0

    # Bật nhưng force_entry_enable=false -> cảnh báo, vẫn không vào lệnh nào.
    msg_mock.reset_mock()
    freqtradebot.config["force_entry_enable"] = False
    await telegram._run_scheduled_analysis({"max_pairs": 2, "auto_entry": {"enabled": True}})
    assert auto.call_count == 0
    assert "force_entry_enable" in msg_mock.call_args_list[0][0][0]

    # Bật đủ -> vào lệnh, nhưng max_per_run chặn ở 1 dù có 2 cặp.
    msg_mock.reset_mock()
    freqtradebot.config["force_entry_enable"] = True
    await telegram._run_scheduled_analysis(
        {"max_pairs": 2, "auto_entry": {"enabled": True, "max_per_run": 1}}
    )
    assert auto.call_count == 1
    assert "Vào lệnh tự động" in msg_mock.call_args_list[-1][0][0]


def test__smc_wave_estimate():
    def piv(*seq):
        return [{"type": "H" if p[0] == "H" else "L", "price": p[1]} for p in seq]

    # Uptrend: đáy(100) H(110) L(105) H(120) -> sau đáy có 3 chân -> SÓNG 3 (đẩy)
    up = Telegram._smc_wave_estimate(piv(("L", 100), ("H", 110), ("L", 105), ("H", 120)), 1)
    assert "SÓNG 3" in up and "đẩy" in up and "TĂNG" in up

    # Sau đáy có 5 chân -> sóng đẩy cuối, cảnh giác đảo chiều
    five = Telegram._smc_wave_estimate(
        piv(("L", 100), ("H", 110), ("L", 105), ("H", 120), ("L", 112), ("H", 130)), 1
    )
    assert "SÓNG 5" in five and "đảo chiều" in five

    # >5 chân -> điều chỉnh A-B-C
    abc = Telegram._smc_wave_estimate(
        piv(("L", 100), ("H", 110), ("L", 105), ("H", 120), ("L", 112), ("H", 130), ("L", 118)), 1
    )
    assert "SÓNG A" in abc and "A-B-C" in abc

    # Không có pivot / không trend -> None
    assert Telegram._smc_wave_estimate([], 1) is None
    assert Telegram._smc_wave_estimate(piv(("L", 100), ("H", 110)), 0) is None


def test__smc_elliott_note():
    # Uptrend + giá discount (dưới eq) -> hồi quy, chờ sóng đẩy
    up_pull = "\n".join(
        Telegram._smc_elliott_note(
            {"swing_trend": 1, "internal_trend": -1, "equilibrium": 100.0}, 95.0
        )
    )
    assert "Sóng Elliott" in up_pull and "điều chỉnh" in up_pull
    # Downtrend thuận đà
    dn = "\n".join(
        Telegram._smc_elliott_note(
            {"swing_trend": -1, "internal_trend": -1, "equilibrium": 100.0}, 95.0
        )
    )
    assert "ĐẨY GIẢM" in dn
    # Luôn có caveat heuristic
    assert "Heuristic" in up_pull
