import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "med_amina_data.sqlite3"

@contextmanager
def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            specialty TEXT DEFAULT '',
            department TEXT DEFAULT '',
            approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER,
            appointment_date TEXT NOT NULL,
            appointment_time TEXT NOT NULL,
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'Күтілуде',
            appointment_type TEXT NOT NULL DEFAULT 'Қабылдау',
            FOREIGN KEY(patient_id) REFERENCES users(id),
            FOREIGN KEY(doctor_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS lab_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            test_name TEXT NOT NULL,
            result_text TEXT DEFAULT '',
            status TEXT DEFAULT 'Тағайындалды',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(patient_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS medication_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL, note TEXT DEFAULT '', status TEXT DEFAULT 'Дәрігер қарауын күтуде',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(patient_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS prescriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, doctor_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL, instructions TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(patient_id) REFERENCES users(id), FOREIGN KEY(doctor_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT, menu_date TEXT NOT NULL, meal TEXT NOT NULL,
            description TEXT NOT NULL, diet_note TEXT DEFAULT '', created_by INTEGER,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS cleaning_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, area TEXT NOT NULL, assigned_to TEXT DEFAULT '',
            task_date TEXT NOT NULL, status TEXT DEFAULT 'Жаңа', note TEXT DEFAULT '',
            requester_id INTEGER, priority TEXT DEFAULT 'Қалыпты',
            FOREIGN KEY(requester_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS operating_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room_name TEXT NOT NULL, schedule_date TEXT NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT NOT NULL, procedure_name TEXT DEFAULT '', status TEXT DEFAULT 'Жоспарланған'
        );
        CREATE TABLE IF NOT EXISTS ward_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL, room_number TEXT DEFAULT '',
            diet TEXT DEFAULT '', allergies TEXT DEFAULT '', note TEXT DEFAULT '',
            FOREIGN KEY(patient_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS patient_profiles (
            user_id INTEGER PRIMARY KEY, blood_type TEXT DEFAULT '', weight REAL, height REAL,
            allergies TEXT DEFAULT '', updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS sos_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id INTEGER NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'Жаңа', FOREIGN KEY(patient_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0, unit TEXT DEFAULT 'дана', minimum REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_by INTEGER,
            FOREIGN KEY(updated_by) REFERENCES users(id)
        );
        """)
        # Бұрынғы оқу базасы болса, жаңа бағандарды қауіпсіз қосып аламыз.
        columns = {row[1] for row in db.execute("PRAGMA table_info(cleaning_tasks)").fetchall()}
        if "requester_id" not in columns:
            db.execute("ALTER TABLE cleaning_tasks ADD COLUMN requester_id INTEGER REFERENCES users(id)")
        if "priority" not in columns:
            db.execute("ALTER TABLE cleaning_tasks ADD COLUMN priority TEXT DEFAULT 'Қалыпты'")
        appointment_columns = {
            row[1] for row in db.execute("PRAGMA table_info(appointments)").fetchall()
        }
        if "appointment_type" not in appointment_columns:
            db.execute(
                "ALTER TABLE appointments ADD COLUMN appointment_type TEXT NOT NULL DEFAULT 'Қабылдау'"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_appointments_schedule "
            "ON appointments(appointment_date, appointment_time, appointment_type, doctor_id, status)"
        )
