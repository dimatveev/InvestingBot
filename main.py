from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from tinkoff.invest import CandleInterval
from tinkoff.invest.retrying.aio.client import AsyncRetryingClient
from tinkoff.invest.retrying.settings import RetryClientSettings
from tinkoff.invest.utils import now
from datetime import timedelta
from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import logging
from pandas import DataFrame
from tinkoff.invest import Client, SecurityTradingStatus
from tinkoff.invest.services import InstrumentsService
from tinkoff.invest.utils import quotation_to_decimal
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

TOKEN = "t.KSwT7xobACNy0ckOlifC8frON0c6g-m-hN2SnScqNaDB6BMyPYfhAWNhv4PHYB925ceVKbg12SmApMgpVF-3dQ"
API_TOKEN = '6723923819:AAG40dPtA-WSi-u_JF2nj2jec9wkr21vRZ0'
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    portfolios = relationship('Portfolio', back_populates='user')
    favourites = relationship("FavouriteStock", back_populates="user")


class Portfolio(Base):
    __tablename__ = 'portfolios'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False)
    user = relationship('User', back_populates='portfolios')
    favourites = relationship('FavouriteStock', back_populates='portfolio')


class FavouriteStock(Base):
    __tablename__ = 'favourites'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True)
    ticker = Column(String, nullable=False)
    figi = Column(String, nullable=False)
    portfolio = relationship('Portfolio', back_populates='favourites')
    user = relationship('User', back_populates='favourites')


DATABASE_URL = "sqlite:///db/users.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
session = SessionLocal()



Base.metadata.create_all(engine)




class Form(StatesGroup):
    waiting_for_stock_ticker = State()
    waiting_for_favorite_stock_ticker = State()


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
    figi = get_figi_by_ticker(ticker,
                              't.KSwT7xobACNy0ckOlifC8frON0c6g-m-hN2SnScqNaDB6BMyPYfhAWNhv4PHYB925ceVKbg12SmApMgpVF-3dQ')
    if figi:
        last_candle = await get_stock_candles(figi)
        if last_candle:
            await message.answer(
                f"Последняя цена акции {ticker} : {(str(last_candle.close).split(',')[0]).split('=')[1]} руб")
        else:
            await message.answer("Не удалось получить информацию об акции.")
    else:
        await message.answer("Не удалось найти акцию с таким тикером.")
    await state.finish()


@dp.message_handler(commands=['addfavourite'], state='*')
async def add_favourite_command(message: types.Message):
    await Form.waiting_for_favorite_stock_ticker.set()
    await message.reply("Введите тикер акции, которую хотите добавить в избранное:")


@dp.message_handler(state=Form.waiting_for_favorite_stock_ticker)
async def add_stock_to_favourites(message: types.Message, state: FSMContext):
    ticker = message.text.upper()
    figi = get_figi_by_ticker(ticker, TOKEN)
    if figi is None:
        await message.answer(f"Не удалось найти акцию с тикером: {ticker}.")
        await state.finish()
        return

    try:
        user_id = message.from_user.id
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            user = User(id=user_id, username=message.from_user.username)
            session.add(user)
            session.commit()

        new_favourite = FavouriteStock(user_id=user.id, ticker=ticker, figi=figi)
        session.add(new_favourite)
        session.commit()
        await message.answer(f"Акция {ticker} добавлена в избранное.")
    except Exception as e:
        logger.error(f"Ошибка при добавлении акции в избранное: {e}")
        await message.answer("Произошла ошибка при добавлении акции в избранное.")

    await state.finish()


@dp.message_handler(commands=['myfavourites'])
async def show_favourites(message: types.Message):
    user_id = message.from_user.id
    user_favourites = session.query(FavouriteStock).filter(FavouriteStock.user_id == user_id).all()
    if not user_favourites:
        await message.answer("В вашем списке избранного пока нет акций.")
        return

    for favourite in user_favourites:
        last_candle = await get_stock_candles(favourite.figi)
        if last_candle:
            price = (str(last_candle.close).split(',')[0]).split('=')[1]
            await message.answer(f"{favourite.ticker}: {price} руб")
        else:
            await message.answer(f"Не удалось получить информацию об акции {favourite.ticker}.")


@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Привет! Я твой инвестиционный бот. Вот что я могу делать...")


@dp.message_handler(commands=['help'])
async def send_help(message: types.Message):
    await message.reply("Список команд: /start, /help, /portfolio...")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
