import sqlite3
from datetime import datetime

DB_NAME = "database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Таблица отчетов (22:00 в группе)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            report_text TEXT,
            date TEXT
        )
    ''')
    
    # Таблица сделок (для темы "Сделки")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            realtor TEXT,
            address TEXT,
            price REAL,
            commission REAL,
            telegraph_link TEXT,
            date TEXT
        )
    ''')
    
    # Таблица расходов (для бухгалтерии)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT,
            amount REAL,
            date TEXT
        )
    ''')
    
    # Таблица объектов/архива с таймерами
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS objects (
            id TEXT PRIMARY KEY,
            realtor_id INTEGER,
            address TEXT,
            status TEXT, -- 'active', 'archive', 'rented_until'
            return_date TEXT,
            data_json TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

def save_report(user_id, username, full_name, text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "INSERT INTO reports (user_id, username, full_name, report_text, date) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, full_name, text, today)
    )
    conn.commit()
    conn.close()

def get_today_reports():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT username, full_name, report_text FROM reports WHERE date = ?", (today,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def save_deal(realtor, address, price, commission, telegraph_link):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO deals (realtor, address, price, commission, telegraph_link, date) VALUES (?, ?, ?, ?, ?, ?)",
        (realtor, address, price, commission, telegraph_link, date)
    )
    conn.commit()
    conn.close()

def save_expense(description, amount):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute(
        "INSERT INTO expenses (description, amount, date) VALUES (?, ?, ?)",
        (description, amount, date)
    )
    conn.commit()
    conn.close()

def get_financial_summary():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT SUM(commission) FROM deals")
    income = cursor.fetchone()[0] or 0.0
    
    cursor.execute("SELECT SUM(amount) FROM expenses")
    expenses = cursor.fetchone()[0] or 0.0
    
    conn.close()
    return income, expenses
