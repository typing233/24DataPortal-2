"""Generate test data for dataportal demo."""
import csv
import sqlite3
import os
from pathlib import Path


def create_test_data(output_dir: str = "tests/test_data"):
    os.makedirs(output_dir, exist_ok=True)

    # SQLite database
    db_path = os.path.join(output_dir, "sample.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            age INTEGER,
            city TEXT,
            registered_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            product TEXT NOT NULL,
            amount REAL,
            quantity INTEGER,
            order_date TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_city ON users(city)")

    conn.execute("""
        CREATE VIEW IF NOT EXISTS user_order_summary AS
        SELECT u.name, u.city, COUNT(o.id) as order_count, SUM(o.amount) as total_spent
        FROM users u LEFT JOIN orders o ON u.id = o.user_id
        GROUP BY u.id
    """)

    import random
    random.seed(42)

    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京"]
    products = ["笔记本电脑", "手机", "平板", "耳机", "键盘", "显示器", "鼠标", "充电器"]

    users = []
    for i in range(1, 201):
        name = f"用户{i:03d}"
        email = f"user{i}@example.com"
        age = random.randint(18, 65)
        city = random.choice(cities)
        date = f"2023-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        users.append((i, name, email, age, city, date))

    conn.executemany("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?)", users)

    orders = []
    for i in range(1, 501):
        user_id = random.randint(1, 200)
        product = random.choice(products)
        amount = round(random.uniform(50, 5000), 2)
        qty = random.randint(1, 5)
        date = f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
        orders.append((i, user_id, product, amount, qty, date))

    conn.executemany("INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?)", orders)
    conn.commit()
    conn.close()

    # CSV file
    csv_path = os.path.join(output_dir, "products.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "category", "price", "stock", "rating"])
        categories = ["电子产品", "办公用品", "家居", "运动", "食品"]
        for i in range(1, 101):
            writer.writerow([
                i,
                f"产品{i:03d}",
                random.choice(categories),
                round(random.uniform(10, 2000), 2),
                random.randint(0, 500),
                round(random.uniform(1, 5), 1),
            ])

    # CSV with encoding edge case (GBK)
    csv_gbk_path = os.path.join(output_dir, "sales_gbk.csv")
    with open(csv_gbk_path, "w", newline="", encoding="gbk") as f:
        writer = csv.writer(f)
        writer.writerow(["月份", "销售额", "数量", "地区"])
        for i in range(1, 13):
            writer.writerow([
                f"2024-{i:02d}",
                round(random.uniform(100000, 500000), 2),
                random.randint(100, 1000),
                random.choice(cities),
            ])

    print(f"Test data created in {output_dir}/")
    print(f"  - {db_path} (SQLite: users, orders, user_order_summary)")
    print(f"  - {csv_path} (UTF-8 CSV: products)")
    print(f"  - {csv_gbk_path} (GBK CSV: sales)")


if __name__ == "__main__":
    create_test_data()
