"""Create a repeatable local demo without external database dependencies."""

import sqlite3

with sqlite3.connect("demo.db") as db:
    db.execute("CREATE TABLE IF NOT EXISTS customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER "
        "REFERENCES customers(id), total NUMERIC NOT NULL)"
    )
    db.executemany("INSERT OR IGNORE INTO customers VALUES (?, ?)", [(1, "Ada"), (2, "Grace")])
    db.executemany("INSERT OR IGNORE INTO orders VALUES (?, ?, ?)", [(1, 1, 42.50), (2, 2, 80)])
print("Demo ready: demo.db")
