import sqlite3

employees = [
    ("1001", "Jan Jansen"),
    ("1002", "Piet de Vries"),
    ("1003", "Sara Bakker")
]

conn = sqlite3.connect("timeclock.db")
cursor = conn.cursor()

for code, name in employees:
    try:
        cursor.execute(
            "INSERT INTO employees (code, name) VALUES (?, ?)",
            (code, name)
        )
    except sqlite3.IntegrityError:
        pass

conn.commit()
conn.close()

print("Medewerkers toegevoegd.")