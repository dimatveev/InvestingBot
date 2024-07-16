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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

TOKEN = "" #введите свой тинькофф апи
API_TOKEN = '' #введите свой telegram api
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
    favorites = relationship("FavoriteStock", back_populates="user")


class Portfolio(Base):
    __tablename__ = 'portfolios'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False)
    user = relationship('User', back_populates='portfolios')
    favorites = relationship('FavoriteStock', back_populates='portfolio')


class FavoriteStock(Base):
    __tablename__ = 'favorites'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=True)
    ticker = Column(String, nullable=False)
    figi = Column(String, nullable=False)
    portfolio = relationship('Portfolio', back_populates='favorites')
    user = relationship('User', back_populates='favorites')


DATABASE_URL = "sqlite:///db/users.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
session = SessionLocal()

Base.metadata.create_all(engine)


class Form(StatesGroup):
    waiting_for_stock_ticker = State()
    waiting_for_favorite_stock_ticker = State()
    waiting_for_favorite_stock_to_delete_ticker = State()


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
    await message.reply("🔍 Введите тикер акции, чтобы узнать текущую цену:")


@dp.message_handler(state=Form.waiting_for_stock_ticker)
async def get_stock(message: types.Message, state: FSMContext):
    ticker = message.text
    figi = get_figi_by_ticker(ticker,
                              't.KSwT7xobACNy0ckOlifC8frON0c6g-m-hN2SnScqNaDB6BMyPYfhAWNhv4PHYB925ceVKbg12SmApMgpVF-3dQ')
    if figi:
        last_candle = await get_stock_candles(figi)
        if last_candle:
            await message.answer(
                f"📈 Последняя цена акции {ticker}: {(str(last_candle.close).split(',')[0]).split('=')[1]} руб")
        else:
            await message.answer("😕 Не удалось получить информацию об акции.")
    else:
        await message.answer("😢 Не удалось найти акцию с таким тикером.")
    await state.finish()


@dp.message_handler(commands=['addfavorite'], state='*')
async def add_favorite_command(message: types.Message):
    await Form.waiting_for_favorite_stock_ticker.set()
    await message.reply("💖 Введите тикер акции для добавления в избранное:")


@dp.message_handler(state=Form.waiting_for_favorite_stock_ticker)
async def add_stock_to_favorites(message: types.Message, state: FSMContext):
    ticker = message.text.upper()
    figi = get_figi_by_ticker(ticker, TOKEN)
    if figi is None:
        await message.answer(f"🤔 Не удалось найти акцию с тикером: {ticker}.")
        await state.finish()
        return

    try:
        user_id = message.from_user.id
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            user = User(id=user_id, username=message.from_user.username)
            session.add(user)
            session.commit()

        new_favorite = FavoriteStock(user_id=user.id, ticker=ticker, figi=figi)
        session.add(new_favorite)
        session.commit()
        await message.answer(f"✅ Акция {ticker} добавлена в избранное.")
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении акции в избранное: {e}")
        await message.answer("🚫 Произошла ошибка при добавлении акции в избранное.")

    await state.finish()


@dp.message_handler(commands=['deletefavorite'], state='*')
async def delete_favorite_command(message: types.Message):
    await Form.waiting_for_favorite_stock_to_delete_ticker.set()
    await message.reply("🗑️ Введите тикер акции для удаления из избранного:")


@dp.message_handler(state=Form.waiting_for_favorite_stock_to_delete_ticker)
async def delete_stock_from_favorites(message: types.Message, state: FSMContext):
    ticker = message.text.upper()
    user_id = message.from_user.id

    try:
        favorite_stock = session.query(FavoriteStock).filter(
            FavoriteStock.user_id == user_id,
            FavoriteStock.ticker == ticker
        ).first()

        if favorite_stock:
            session.delete(favorite_stock)
            session.commit()
            await message.answer(f"🗑️ Акция {ticker} удалена из избранного.")
        else:
            await message.answer(f"🤷‍♂️ Акция {ticker} не найдена в вашем списке избранного.")
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении акции из избранного: {e}")
        await message.answer("🚫 Произошла ошибка при удалении акции из избранного.")

    await state.finish()


@dp.message_handler(commands=['myfavorites'])
async def show_favorites(message: types.Message):
    user_id = message.from_user.id
    user_favorites = session.query(FavoriteStock).filter(FavoriteStock.user_id == user_id).all()
    if not user_favorites:
        await message.answer("💔 В вашем списке избранного пока нет акций.")
        return

    response = "💖 Ваш список избранных акций:\n"
    for favorite in user_favorites:
        last_candle = await get_stock_candles(favorite.figi)
        if last_candle:
            price = (str(last_candle.close).split(',')[0]).split('=')[1]
            response += f"- {favorite.ticker}: {price} руб\n"
        else:
            response += f"- {favorite.ticker}: Нет данных о цене\n"

    await message.answer(response)


@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.answer(
        "Привет! Я твой инвестиционный бот 📈. Вот что я могу делать:\n"
        "🔍 Получить котировки - узнать актуальные котировки акций\n"
        "💖 Добавить в избранное - добавить акцию в список избранных\n"
        "🗑️ Удалить из избранного - удалить акцию из избранного\n"
        "💔 Показать избранное - посмотреть список избранных акций\n"
        "ℹ️ Помощь - получить информацию о работе бота\n\n"
        "Выберите нужное действие из меню ниже:",
        reply_markup=get_main_keyboard()
    )


@dp.message_handler(commands=['help'])
async def send_help(message: types.Message):
    help_text = (
        "Привет! Я твой инвестиционный помощник-бот. Вот что я могу делать:\n\n"
        "/start - начать работу с ботом и получить приветственное сообщение.\n"
        "/menu - вызов удобной клавиатуры чтобы легче оперировать командами. \n"
        "/help - получить информацию о доступных командах и их использовании.\n"
        "/getstock - получить текущую цену акции. После ввода команды, отправьте тикер акции, например 'YNDX' для Yandex.\n"
        "/addfavorite - добавить акцию в список избранных. После ввода команды, отправьте тикер акции, которую хотите добавить.\n"
        "/myfavorites - просмотреть ваш список избранных акций и их текущие цены.\n"
        "/deletefavorite - удалить акцию из списка избранных. После ввода команды, отправьте тикер акции для удаления.\n\n"
        "Если у вас возникнут вопросы или предложения, не стесняйтесь обращаться!"
    )

    await message.reply(help_text)


@dp.message_handler(commands=['menu'])
async def show_menu(message: types.Message):
    await message.answer(
        "Выберите действие из меню ниже:",
        reply_markup=get_main_keyboard()
    )


@dp.message_handler(
    lambda message: message.text in ["🔍 Получить котировки", "💖 Добавить в избранное", "🗑️ Удалить из избранного",
                                     "💔 Показать избранное", "ℹ️ Помощь"])
async def handle_keyboard_commands(message: types.Message):
    if message.text == "🔍 Получить котировки":
        await get_stock_command(message)
    elif message.text == "💖 Добавить в избранное":
        await add_favorite_command(message)
    elif message.text == "🗑️ Удалить из избранного":
        await delete_favorite_command(message)
    elif message.text == "💔 Показать избранное":
        await show_favorites(message)
    elif message.text == "ℹ️ Помощь":
        await send_help(message)


def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("🔍 Получить котировки"))
    keyboard.add(KeyboardButton("💖 Добавить в избранное"))
    keyboard.add(KeyboardButton("🗑️ Удалить из избранного"))
    keyboard.add(KeyboardButton("💔 Показать избранное"))
    keyboard.add(KeyboardButton("ℹ️ Помощь"))
    return keyboard


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
