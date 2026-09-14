import sqlite3
from datetime import datetime

DB_NAME = "nestima_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица отчетов агентов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            report_text TEXT,
            date TEXT
        )
    """)
    
    # Таблица сделок
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            realtor_username TEXT,
            address TEXT,
            deal_price REAL,
            commission REAL,
            telegraph_link TEXT,
            date TEXT
        )
    """)
    
    # Таблица расходов (Бухгалтерия)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT,
            amount_usd REAL,
            date TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def save_report(user_id: int, username: str, full_name: str, text: str):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO reports (user_id, username, full_name, report_text, date) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, full_name, text, today)
    )
    conn.commit()
    conn.close()

def get_today_reports():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT username, full_name, report_text FROM reports WHERE date = ?", (today,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def save_deal(realtor: str, address: str, price: float, commission: float, telegraph_link: str):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO deals (realtor_username, address, deal_price, commission, telegraph_link, date) VALUES (?, ?, ?, ?, ?, ?)",
        (realtor, address, price, commission, telegraph_link, today)
    )
    conn.commit()
    conn.close()

def save_expense(description: str, amount: float):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO expenses (description, amount_usd, date) VALUES (?, ?, ?)",
        (description, amount, today)
    )
    conn.commit()
    conn.close()

def get_financial_summary():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(commission) FROM deals")
    total_income = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(amount_usd) FROM expenses")
    total_expenses = cursor.fetchone()[0] or 0.0
    conn.close()
    
    return total_income, total_expenses
