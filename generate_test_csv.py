import csv
import random
import sys
from datetime import datetime, timedelta

def generate_large_csv(filename, num_rows):
    """
    Generates a large CSV file with sample data for testing.

    Args:
        filename (str): The name of the CSV file to create.
        num_rows (int): The number of data rows to generate.
    """
    headers = ["ID", "FirstName", "LastName", "Email", "TransactionAmount", "TransactionDate"]
    first_names = ["John", "Jane", "Peter", "Susan", "Michael", "Linda", "Robert", "Patricia", "James", "Mary"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

    start_time = datetime.now()
    print(f"Starting CSV generation for {num_rows:,} rows...")

    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            start_date = datetime(2020, 1, 1)

            for i in range(1, num_rows + 1):
                first = random.choice(first_names)
                last = random.choice(last_names)
                email = f"{first.lower()}.{last.lower()}{i}@example.com"
                amount = round(random.uniform(5.0, 5000.0), 2)
                date = start_date + timedelta(days=random.randint(0, 1825), hours=random.randint(0,23))
                
                writer.writerow([i, first, last, email, amount, date.strftime('%Y-%m-%d %H:%M:%S')])

                # Print progress update
                if i % 100000 == 0:
                    print(f"  ...wrote {i:,} rows")

        end_time = datetime.now()
        print(f"\nSuccessfully created '{filename}' with {num_rows:,} rows.")
        print(f"Total time taken: {end_time - start_time}")

    except IOError as e:
        print(f"Error: Could not write to file {filename}. {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_test_csv.py <output_filename.csv> <number_of_rows>")
        sys.exit(1)

    output_file = sys.argv[1]
    try:
        rows = int(sys.argv[2])
        if rows <= 0:
            raise ValueError
    except ValueError:
        print("Error: Please provide a positive integer for the number of rows.")
        sys.exit(1)

    generate_large_csv(output_file, rows)
