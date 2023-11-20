from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from tinkoff.invest import CandleInterval
from tinkoff.invest.retrying.aio.client import AsyncRetryingClient
from tinkoff.invest.retrying.settings import RetryClientSettings
from tinkoff.invest.utils import now
from datetime import timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import logging
import os

from pandas import DataFrame

from tinkoff.invest import Client, SecurityTradingStatus
from tinkoff.invest.services import InstrumentsService
from tinkoff.invest.utils import quotation_to_decimal

TOKEN = "t.KSwT7xobACNy0ckOlifC8frON0c6g-m-hN2SnScqNaDB6BMyPYfhAWNhv4PHYB925ceVKbg12SmApMgpVF-3dQ"
# Настройка бота
API_TOKEN = '6723923819:AAG40dPtA-WSi-u_JF2nj2jec9wkr21vRZ0'
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Настройка базы данных
DATABASE_URL = "sqlite:///users.db"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()
Base = declarative_base()


# Класс состояний для сбора информации от пользователя
class Form(StatesGroup):
    waiting_for_stock_ticker = State()


logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", level=logging.DEBUG)
logger = logging.getLogger(__name__)


def get_figi_by_ticker(ticker, token):

    with Client(token) as client:
        instruments: InstrumentsService = client.instruments
        tickers = []
        for method in ["shares", "bonds", "etfs", "currencies", "futures"]:
            for item in getattr(instruments, method)().instruments:
                tickers.append(
                    {
                        "name": item.name,
                        "ticker": item.ticker,
                        "class_code": item.class_code,
                        "figi": item.figi,
                        "uid": item.uid,
                        "type": method,
                        "min_price_increment": quotation_to_decimal(
                            item.min_price_increment
                        ),
                        "scale": 9 - len(str(item.min_price_increment.nano)) + 1,
                        "lot": item.lot,
                        "trading_status": str(
                            SecurityTradingStatus(item.trading_status).name
                        ),
                        "api_trade_available_flag": item.api_trade_available_flag,
                        "currency": item.currency,
                        "exchange": item.exchange,
                        "buy_available_flag": item.buy_available_flag,
                        "sell_available_flag": item.sell_available_flag,
                        "short_enabled_flag": item.short_enabled_flag,
                        "klong": quotation_to_decimal(item.klong),
                        "kshort": quotation_to_decimal(item.kshort),
                    }
                )

        tickers_df = DataFrame(tickers)

        ticker_df = tickers_df[tickers_df["ticker"] == ticker]
        if ticker_df.empty:
            logger.error("There is no such ticker: %s", ticker)
            return

        figi = ticker_df["figi"].iloc[0]
        return figi


async def get_stock_candles(figi):
    retry_settings = RetryClientSettings(use_retry=True, max_retry_attempt=2)
    async with AsyncRetryingClient(TOKEN, settings=retry_settings) as client:
        candles = []
        async for candle in client.get_all_candles(
                figi=figi,
                from_=now() - timedelta(days=1),
                interval=CandleInterval.CANDLE_INTERVAL_1_MIN,
        ):
            candles.append(candle)
        return candles[-1] if candles else None


@dp.message_handler(commands=['getstock'], state='*')
async def get_stock_command(message: types.Message):
    await Form.waiting_for_stock_ticker.set()
    await message.reply("Введите тикер акции:")


@dp.message_handler(state=Form.waiting_for_stock_ticker)
async def get_stock(message: types.Message, state: FSMContext):
    ticker = message.text
    figi = get_figi_by_ticker(ticker, 't.KSwT7xobACNy0ckOlifC8frON0c6g-m-hN2SnScqNaDB6BMyPYfhAWNhv4PHYB925ceVKbg12SmApMgpVF-3dQ')
    if figi:
        last_candle = await get_stock_candles(figi)
        if last_candle:
            await message.answer(f"Последняя цена акции {ticker} : {(str(last_candle.close).split(',')[0]).split('=')[1]} руб")
        else:
            await message.answer("Не удалось получить информацию об акции.")
    else:
        await message.answer("Не удалось найти акцию с таким тикером.")
    await state.finish()


# Запуск бота
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String)


class Portfolio(Base):
    __tablename__ = 'portfolios'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    balance = Column(Float)


# Создание таблиц
Base.metadata.create_all(engine)


# Обработчики команд
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Привет! Я твой инвестиционный бот. Вот что я могу делать...")


@dp.message_handler(commands=['help'])
async def send_help(message: types.Message):
    await message.reply("Список команд: /start, /help, /portfolio...")


# Здесь можно добавить больше обработчиков для разных команд

# Функция, которая запускает polling
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
