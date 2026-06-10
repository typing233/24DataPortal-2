"""CLI entry point for dataportal."""
import click
import uvicorn

from dataportal.cli_plugin import plugin
from dataportal.cli_publish import publish


@click.group()
def main():
    """DataPortal - Interactive data exploration portal with plugin system."""
    pass


main.add_command(plugin)
main.add_command(publish)


@main.command()
@click.argument("sources", nargs=-1, required=False)
@click.option("--port", "-p", default=8001, help="Port to serve on")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")
@click.option("--config", "-c", default=None, help="Path to config JSON file")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
@click.option("--demo", is_flag=True, help="Start with built-in demo data")
def serve(sources, port, host, config, reload, demo):
    """Start the data portal web server.

    SOURCES: SQLite files, CSV files, or directories to serve.
    If no sources given, starts with built-in demo data automatically.
    """
    import os
    import sys
    from pathlib import Path

    if not sources and not demo:
        demo = True

    if demo:
        demo_dir = Path(__file__).parent / "_demo_data"
        demo_dir.mkdir(exist_ok=True)
        _generate_demo_data(str(demo_dir))
        sources = (str(demo_dir),)
        click.echo(f"Starting with demo data in {demo_dir}")

    os.environ["DATAPORTAL_SOURCES"] = "|".join(sources)
    if config:
        os.environ["DATAPORTAL_CONFIG"] = config

    click.echo(f"DataPortal starting on http://{host}:{port}")
    uvicorn.run(
        "dataportal.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


def _generate_demo_data(output_dir: str):
    """Generate demo data if not already present."""
    import csv
    import sqlite3
    import random
    from pathlib import Path

    db_path = Path(output_dir) / "sample.sqlite"
    csv_path = Path(output_dir) / "products.csv"

    if db_path.exists() and csv_path.exists():
        return

    random.seed(42)
    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京"]
    products = ["笔记本电脑", "手机", "平板", "耳机", "键盘", "显示器", "鼠标", "充电器"]

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            email TEXT UNIQUE, age INTEGER, city TEXT, registered_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
            product TEXT NOT NULL, amount REAL, quantity INTEGER, order_date TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_city ON users(city)")
    conn.execute("""
        CREATE VIEW IF NOT EXISTS user_order_summary AS
        SELECT u.name, u.city, COUNT(o.id) as order_count, SUM(o.amount) as total_spent
        FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id
    """)

    users = []
    for i in range(1, 201):
        users.append((i, f"用户{i:03d}", f"user{i}@example.com",
                      random.randint(18, 65), random.choice(cities),
                      f"2023-{random.randint(1,12):02d}-{random.randint(1,28):02d}"))
    conn.executemany("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?,?)", users)

    orders = []
    for i in range(1, 501):
        orders.append((i, random.randint(1, 200), random.choice(products),
                       round(random.uniform(50, 5000), 2), random.randint(1, 5),
                       f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}"))
    conn.executemany("INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?)", orders)
    conn.commit()
    conn.close()

    with open(str(csv_path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "category", "price", "stock", "rating"])
        categories = ["电子产品", "办公用品", "家居", "运动", "食品"]
        for i in range(1, 101):
            writer.writerow([i, f"产品{i:03d}", random.choice(categories),
                             round(random.uniform(10, 2000), 2),
                             random.randint(0, 500), round(random.uniform(1, 5), 1)])


if __name__ == "__main__":
    main()
