import sqlite3
import random
import sys
from datetime import datetime, timedelta


def generate_large_sqlite(filename, num_rows):
    """
    Generates a SQLite database with sample data for testing.

    Args:
        filename (str): The name of the .db file to create.
        num_rows (int): The number of data rows to generate.
    """
    first_names = ["John", "Jane", "Peter", "Susan", "Michael", "Linda", "Robert", "Patricia", "James", "Mary"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

    start_time = datetime.now()
    print(f"Starting SQLite generation for {num_rows:,} rows...")

    try:
        conn = sqlite3.connect(filename)
        c = conn.cursor()

        c.execute("DROP TABLE IF EXISTS transactions")
        c.execute("""CREATE TABLE transactions (
            ID INTEGER PRIMARY KEY,
            FirstName TEXT,
            LastName TEXT,
            Email TEXT,
            TransactionAmount REAL,
            TransactionDate TEXT
        )""")

        c.execute("DROP TABLE IF EXISTS categories")
        c.execute("""CREATE TABLE categories (
            ID INTEGER PRIMARY KEY,
            Name TEXT,
            Description TEXT
        )""")
        categories = [
            (1, "Electronics", "Phones, laptops, and accessories"),
            (2, "Clothing", "Shirts, pants, and shoes"),
            (3, "Food", "Groceries and dining"),
            (4, "Travel", "Flights, hotels, and car rentals"),
            (5, "Entertainment", "Movies, games, and concerts"),
        ]
        c.executemany("INSERT INTO categories VALUES (?, ?, ?)", categories)

        start_date = datetime(2020, 1, 1)
        batch = []
        for i in range(1, num_rows + 1):
            first = random.choice(first_names)
            last = random.choice(last_names)
            email = f"{first.lower()}.{last.lower()}{i}@example.com"
            amount = round(random.uniform(5.0, 5000.0), 2)
            date = start_date + timedelta(days=random.randint(0, 1825), hours=random.randint(0, 23))
            batch.append((i, first, last, email, amount, date.strftime('%Y-%m-%d %H:%M:%S')))

            if len(batch) >= 10000:
                c.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)", batch)
                batch = []
                print(f"  ...wrote {i:,} rows")

        if batch:
            c.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)", batch)

        conn.commit()
        conn.close()

        end_time = datetime.now()
        print(f"\nSuccessfully created '{filename}' with {num_rows:,} rows (transactions) + 5 rows (categories).")
        print(f"Total time taken: {end_time - start_time}")

    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_test_sqlite.py <output_filename.db> <number_of_rows>")
        sys.exit(1)

    output_file = sys.argv[1]
    try:
        rows = int(sys.argv[2])
        if rows <= 0:
            raise ValueError
    except ValueError:
        print("Error: Please provide a positive integer for the number of rows.")
        sys.exit(1)

    generate_large_sqlite(output_file, rows)
