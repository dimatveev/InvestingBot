from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Настройка бота
API_TOKEN = '6723923819:AAG40dPtA-WSi-u_JF2nj2jec9wkr21vRZ0'
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Настройка базы данных
DATABASE_URL = "sqlite:///users.db"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()
Base = declarative_base()

# Определение моделей SQLAlchemy
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

# Обработчик команды start
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Привет!")
