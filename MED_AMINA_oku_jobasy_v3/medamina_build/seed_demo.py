import secrets
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from med_amina_db import get_db, init_db


DEMO_USERS = [
    ("Жүйе әкімшісі", "admin@medamina.test", "admin", "Әкімшілік", ""),
    ("Айдана Дәрігер", "doctor.one@medamina.test", "doctor", "Терапия", "Терапевт"),
    ("Ерлан Дәрігер", "doctor.two@medamina.test", "doctor", "Кардиология", "Кардиолог"),
    ("Аружан Пациент", "patient.one@medamina.test", "patient", "", ""),
    ("Мирас Пациент", "patient.two@medamina.test", "patient", "", ""),
]


def next_open_day():
    appointment_date = date.today() + timedelta(days=1)
    while appointment_date.weekday() == 6:
        appointment_date += timedelta(days=1)
    return appointment_date.isoformat()


def seed_demo_data():
    init_db()
    credentials = []
    with get_db() as db:
        user_ids = {}
        for full_name, email, role, department, specialty in DEMO_USERS:
            existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                user_ids[email] = existing["id"]
                continue

            password = secrets.token_urlsafe(12)
            cursor = db.execute(
                """INSERT INTO users
                    (full_name, email, password_hash, role, department, specialty, approved)
                    VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (full_name, email, generate_password_hash(password), role, department, specialty),
            )
            user_ids[email] = cursor.lastrowid
            credentials.append((email, password))

        appointment_date = next_open_day()
        demo_appointments = [
            (user_ids["patient.one@medamina.test"], user_ids["doctor.one@medamina.test"],
             appointment_date, "08:00", "Жалпы қабылдау", "Қабылдау"),
            (user_ids["patient.two@medamina.test"], None,
             appointment_date, "09:00", "Жалпы қан талдауы", "Зертхана"),
        ]
        for patient_id, doctor_id, appointment_date, appointment_time, reason, appointment_type in demo_appointments:
            exists = db.execute(
                """SELECT id FROM appointments
                   WHERE patient_id=? AND reason=? AND appointment_type=?""",
                (patient_id, reason, appointment_type),
            ).fetchone()
            if not exists:
                db.execute(
                    """INSERT INTO appointments
                        (patient_id, doctor_id, appointment_date, appointment_time, reason, appointment_type)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                    (patient_id, doctor_id, appointment_date, appointment_time, reason, appointment_type),
                )

    if credentials:
        print("New demo accounts (passwords are shown only once):")
        for email, password in credentials:
            print(f"{email}  |  {password}")
    else:
        print("Demo accounts already exist; passwords were not changed.")
    print("Fake demo data and appointments are ready.")


if __name__ == "__main__":
    seed_demo_data()
