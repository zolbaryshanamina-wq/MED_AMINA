from datetime import date, datetime, time, timedelta


APPOINTMENT_TYPES = {"Қабылдау", "Зертхана"}
ACTIVE_STATUSES = {"Күтілуде", "Расталды"}
OPENING_TIME = time(7, 0)
CLOSING_TIME = time(19, 0)
SLOT_MINUTES = 30


def is_bookable_date(value):
    try:
        appointment_date = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False

    return appointment_date >= date.today() and appointment_date.weekday() < 6


def available_slots(
    db, appointment_date, appointment_type, doctor_id=None, exclude_id=None, patient_id=None
):
    if not is_bookable_date(appointment_date) or appointment_type not in APPOINTMENT_TYPES:
        return []

    if appointment_type == "Қабылдау" and not doctor_id:
        return []

    query = """
        SELECT appointment_time
        FROM appointments
        WHERE appointment_date = ?
          AND appointment_type = ?
          AND status != 'Бас тартылды'
    """
    params = [appointment_date, appointment_type]

    if appointment_type == "Қабылдау":
        query += " AND doctor_id = ?"
        params.append(doctor_id)
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)

    booked = {row["appointment_time"] for row in db.execute(query, params).fetchall()}
    if patient_id is not None:
        patient_query = """
            SELECT appointment_time
            FROM appointments
            WHERE appointment_date = ?
              AND patient_id = ?
              AND status != 'Бас тартылды'
        """
        patient_params = [appointment_date, patient_id]
        if exclude_id is not None:
            patient_query += " AND id != ?"
            patient_params.append(exclude_id)
        booked.update(
            row["appointment_time"]
            for row in db.execute(patient_query, patient_params).fetchall()
        )
    start = datetime.combine(date.today(), OPENING_TIME)
    end = datetime.combine(date.today(), CLOSING_TIME)
    slots = []
    while start + timedelta(minutes=SLOT_MINUTES) <= end:
        slot = start.strftime("%H:%M")
        if slot not in booked:
            slots.append(slot)
        start += timedelta(minutes=SLOT_MINUTES)

    if appointment_date == date.today().isoformat():
        now = datetime.now().time()
        slots = [slot for slot in slots if time.fromisoformat(slot) > now]

    return slots


def is_valid_slot(appointment_date, appointment_time):
    if not is_bookable_date(appointment_date):
        return False
    try:
        parsed_time = time.fromisoformat(appointment_time)
    except (TypeError, ValueError):
        return False

    if parsed_time.minute not in {0, 30} or parsed_time.second or parsed_time.microsecond:
        return False
    if not OPENING_TIME <= parsed_time < CLOSING_TIME:
        return False
    return appointment_date != date.today().isoformat() or parsed_time > datetime.now().time()
