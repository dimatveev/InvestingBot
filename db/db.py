import sqlite3

# Подключение к базе данных (или создание, если она не существует)
with sqlite3.connect('/Users/dmitriimatveev/PycharmProjects/InvestingBot/db/users.db') as db:
    cursor = db.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE NOT NULL
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS portfolios (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS favourites (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        portfolio_id INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        figi TEXT NOT NULL,
        FOREIGN KEY (portfolio_id) REFERENCES portfolios (id)
    )
    ''')

    db.commit()
