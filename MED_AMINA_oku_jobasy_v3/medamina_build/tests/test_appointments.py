import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import med_amina_db

_database_dir = tempfile.TemporaryDirectory()
med_amina_db.DB_PATH = Path(_database_dir.name) / "test.sqlite3"

import med_amina_server
from med_amina_db import get_db
from seed_demo import seed_demo_data
from werkzeug.security import generate_password_hash


app = med_amina_server.app
app.config.update(TESTING=True, SECRET_KEY="test-secret")


def next_open_date(days_ahead=1):
    value = date.today() + timedelta(days=days_ahead)
    while value.weekday() == 6:
        value += timedelta(days=1)
    return value.isoformat()


class AppointmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with get_db() as db:
            cls.patient_id = db.execute(
                """INSERT INTO users(full_name,email,password_hash,role,approved)
                   VALUES(?,?,?,?,1)""",
                ("Тест пациент", "patient@test.local", generate_password_hash("test-password"), "patient"),
            ).lastrowid
            cls.second_patient_id = db.execute(
                """INSERT INTO users(full_name,email,password_hash,role,approved)
                   VALUES(?,?,?,?,1)""",
                ("Екінші пациент", "patient2@test.local", generate_password_hash("test-password"), "patient"),
            ).lastrowid
            cls.doctor_id = db.execute(
                """INSERT INTO users(full_name,email,password_hash,role,approved)
                   VALUES(?,?,?,?,1)""",
                ("Тест дәрігер", "doctor@test.local", generate_password_hash("test-password"), "doctor"),
            ).lastrowid

    def setUp(self):
        with get_db() as db:
            db.execute("DELETE FROM appointments")
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = self.patient_id
        self.appointment_date = next_open_date()

    def appointment_payload(self, **overrides):
        payload = {
            "appointment_type": "Қабылдау",
            "doctor_id": str(self.doctor_id),
            "appointment_date": self.appointment_date,
            "appointment_time": "08:00",
            "reason": "Жалпы қабылдау",
        }
        payload.update(overrides)
        return payload

    def test_availability_returns_half_hour_slots(self):
        response = self.client.get(
            "/appointments/availability",
            query_string={
                "date": self.appointment_date,
                "type": "Қабылдау",
                "doctor_id": self.doctor_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        slots = response.get_json()["slots"]
        self.assertEqual(slots[0], "07:00")
        self.assertEqual(slots[-1], "18:30")
        self.assertEqual(len(slots), 24)

    def test_dashboard_renders_scheduling_controls(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Қабылдауға немесе талдауға жазылу".encode(), response.data)
        self.assertIn(b"data-appointment-scheduler", response.data)

    def test_appointment_booking_and_duplicate_slot_are_handled(self):
        response = self.client.post("/appointments", data=self.appointment_payload())
        self.assertEqual(response.status_code, 302)
        duplicate = self.client.post("/appointments", data=self.appointment_payload())
        self.assertEqual(duplicate.status_code, 302)
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM appointments WHERE appointment_date=? AND appointment_time='08:00'",
                (self.appointment_date,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_lab_booking_uses_shared_slot_pool(self):
        self.client.post("/appointments", data=self.appointment_payload())
        with self.client.session_transaction() as session:
            session["user_id"] = self.second_patient_id
        response = self.client.post(
            "/appointments",
            data=self.appointment_payload(
                appointment_type="Зертхана",
                doctor_id="",
                reason="Жалпы қан талдауы",
            ),
        )
        self.assertEqual(response.status_code, 302)
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM appointments WHERE appointment_date=? AND appointment_time='08:00'",
                (self.appointment_date,),
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_patient_cannot_book_overlapping_services(self):
        self.client.post("/appointments", data=self.appointment_payload())
        self.client.post(
            "/appointments",
            data=self.appointment_payload(
                appointment_type="Зертхана",
                doctor_id="",
                reason="Жалпы қан талдауы",
            ),
        )
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM appointments WHERE patient_id=? AND appointment_time='08:00'",
                (self.patient_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_cancelled_slot_becomes_available_again(self):
        self.client.post("/appointments", data=self.appointment_payload())
        with get_db() as db:
            appointment_id = db.execute(
                "SELECT id FROM appointments WHERE patient_id=?", (self.patient_id,)
            ).fetchone()["id"]
        self.client.post(f"/appointments/{appointment_id}/cancel")
        response = self.client.get(
            "/appointments/availability",
            query_string={
                "date": self.appointment_date,
                "type": "Қабылдау",
                "doctor_id": self.doctor_id,
            },
        )
        self.assertIn("08:00", response.get_json()["slots"])

    def test_sunday_is_not_bookable(self):
        sunday = date.today() + timedelta(days=(6 - date.today().weekday()) % 7 or 7)
        response = self.client.get(
            "/appointments/availability",
            query_string={
                "date": sunday.isoformat(),
                "type": "Зертхана",
            },
        )
        self.assertEqual(response.get_json()["slots"], [])

    def test_reschedule_updates_date_and_time(self):
        self.client.post("/appointments", data=self.appointment_payload())
        with get_db() as db:
            appointment_id = db.execute(
                "SELECT id FROM appointments WHERE patient_id=?", (self.patient_id,)
            ).fetchone()["id"]
        new_date = next_open_date(2)
        response = self.client.post(
            f"/appointments/{appointment_id}/reschedule",
            data={"new_date": new_date, "new_time": "09:30"},
        )
        self.assertEqual(response.status_code, 302)
        with get_db() as db:
            appointment = db.execute(
                "SELECT appointment_date, appointment_time FROM appointments WHERE id=?",
                (appointment_id,),
            ).fetchone()
        self.assertEqual((appointment["appointment_date"], appointment["appointment_time"]), (new_date, "09:30"))

    def test_assigned_doctor_can_confirm_appointment(self):
        self.client.post("/appointments", data=self.appointment_payload())
        with get_db() as db:
            appointment_id = db.execute(
                "SELECT id FROM appointments WHERE patient_id=?", (self.patient_id,)
            ).fetchone()["id"]
        with self.client.session_transaction() as session:
            session["user_id"] = self.doctor_id
        response = self.client.post(
            f"/appointments/{appointment_id}/status",
            data={"status": "Расталды"},
        )
        self.assertEqual(response.status_code, 302)
        with get_db() as db:
            status = db.execute(
                "SELECT status FROM appointments WHERE id=?", (appointment_id,)
            ).fetchone()["status"]
        self.assertEqual(status, "Расталды")

    def test_demo_seed_is_repeatable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            seed_demo_data()
            seed_demo_data()
        with get_db() as db:
            demo_users = db.execute(
                "SELECT COUNT(*) FROM users WHERE email LIKE '%@medamina.test'"
            ).fetchone()[0]
            demo_appointments = db.execute(
                """SELECT COUNT(*) FROM appointments
                   WHERE reason IN ('Жалпы қабылдау', 'Жалпы қан талдауы')"""
            ).fetchone()[0]
        self.assertEqual(demo_users, 5)
        self.assertEqual(demo_appointments, 2)

    def test_existing_database_gets_appointment_type_column(self):
        old_database = Path(_database_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(old_database)
        connection.execute(
            """CREATE TABLE appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                doctor_id INTEGER,
                appointment_date TEXT NOT NULL,
                appointment_time TEXT NOT NULL,
                reason TEXT DEFAULT '',
                status TEXT DEFAULT 'Күтілуде'
            )"""
        )
        connection.commit()
        connection.close()

        test_database = med_amina_db.DB_PATH
        try:
            med_amina_db.DB_PATH = old_database
            med_amina_db.init_db()
            with get_db() as db:
                columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(appointments)").fetchall()
                }
            self.assertIn("appointment_type", columns)
        finally:
            med_amina_db.DB_PATH = test_database


if __name__ == "__main__":
    unittest.main()
