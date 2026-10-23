from functools import wraps
import os
from datetime import date
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import check_password_hash, generate_password_hash
from med_amina_db import init_db, get_db
from appointment_schedule import ACTIVE_STATUSES, APPOINTMENT_TYPES, available_slots, is_valid_slot

app = Flask(__name__)
is_production = os.environ.get("MED_AMINA_ENV", "").lower() == "production"
secret_key = os.environ.get("MED_AMINA_SECRET_KEY")
if is_production and not secret_key:
    raise RuntimeError("MED_AMINA_SECRET_KEY must be configured in production.")
app.secret_key = secret_key or "med-amina-local-development-only"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_production,
)
init_db()

ROLE_LABELS = {
    "admin":"Әкімшілік", "doctor":"Дәрігер", "nurse":"Медбике/медик",
    "employee":"Қызметкер", "kitchen":"Асхана / диетолог", "cleaner":"Тазалық қызметкері",
    "operating_room":"Операциялық бөлім", "patient":"Пациент", "inpatient":"Стационардағы пациент"
}
WORKER_ROLES = {"admin", "doctor", "nurse", "employee", "kitchen", "cleaner", "operating_room"}

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Алдымен жүйеге кіріңіз.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            user = current_user()
            if user["role"] not in roles:
                abort(403)
            if user["role"] in WORKER_ROLES and not user["approved"]:
                session.clear()
                flash("Аккаунтыңыз әкімшілік растауын күтуде.", "warning")
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper
    return deco

@app.context_processor
def inject_globals():
    user = current_user()
    data = {"me": user, "role_labels": ROLE_LABELS, "today": date.today().isoformat()}
    if user:
        with get_db() as db:
            data["demo_menus"] = db.execute("SELECT * FROM menus ORDER BY menu_date DESC,id DESC LIMIT 20").fetchall()
            data["demo_tasks"] = db.execute("SELECT * FROM cleaning_tasks ORDER BY task_date DESC,id DESC LIMIT 20").fetchall()
            data["demo_rooms"] = db.execute("SELECT * FROM operating_rooms ORDER BY schedule_date DESC,start_time DESC LIMIT 20").fetchall()
            if user["role"] in {"admin", "doctor", "nurse"}:
                data["demo_requests"] = db.execute("SELECT m.*,u.full_name patient_name FROM medication_requests m JOIN users u ON u.id=m.patient_id ORDER BY m.id DESC LIMIT 30").fetchall()
                data["demo_prescriptions"] = db.execute("SELECT p.*,u.full_name patient_name,d.full_name doctor_name FROM prescriptions p JOIN users u ON u.id=p.patient_id JOIN users d ON d.id=p.doctor_id ORDER BY p.id DESC LIMIT 30").fetchall()
            else:
                data["demo_requests"] = db.execute("SELECT m.*,u.full_name patient_name FROM medication_requests m JOIN users u ON u.id=m.patient_id WHERE m.patient_id=? ORDER BY m.id DESC",(user["id"],)).fetchall()
                data["demo_prescriptions"] = db.execute("SELECT p.*,u.full_name patient_name,d.full_name doctor_name FROM prescriptions p JOIN users u ON u.id=p.patient_id JOIN users d ON d.id=p.doctor_id WHERE p.patient_id=? ORDER BY p.id DESC",(user["id"],)).fetchall()
            data["demo_patients"] = db.execute("SELECT id,full_name,email FROM users WHERE role IN ('patient','inpatient') ORDER BY full_name").fetchall()
            data["demo_wards"] = db.execute("SELECT w.*,u.full_name patient_name FROM ward_notes w JOIN users u ON u.id=w.patient_id ORDER BY w.id DESC LIMIT 30").fetchall()
    return data

@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("welcome.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        role_choice = request.form.get("account_type","")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password) and (not role_choice or user["role"] == role_choice):
            if user["role"] in WORKER_ROLES and not user["approved"]:
                flash("Қызметкер аккаунты әлі әкімшілік тарапынан расталмаған.", "warning")
                return render_template("login.html")
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Логин, құпиясөз немесе аккаунт түрі дұрыс емес.", "error")
    return render_template("login.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("full_name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        role = request.form.get("account_type","patient")
        if role not in {"patient","inpatient"}:
            flash("Бұл тіркелу бетінде тек пациенттер тіркеле алады. Қызметкер аккаунтын әкімшілік ашады.", "error")
        elif not name or "@" not in email or len(password) < 8:
            flash("Аты-жөнін, дұрыс email және кемінде 8 таңбалы құпиясөз енгізіңіз.", "error")
        else:
            try:
                with get_db() as db:
                    db.execute("INSERT INTO users(full_name,email,password_hash,role,approved) VALUES(?,?,?,?,1)",
                               (name,email,generate_password_hash(password),role))
                flash("Тіркелу сәтті аяқталды. Енді жүйеге кіре аласыз.", "success")
                return redirect(url_for("login"))
            except Exception:
                flash("Бұл email бұрын тіркелген болуы мүмкін.", "error")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Жүйеден шықтыңыз.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    if user["role"] in WORKER_ROLES and not user["approved"]:
        session.clear()
        flash("Аккаунтыңыз растауды күтуде.", "warning")
        return redirect(url_for("login"))
    with get_db() as db:
        counts = {
            "patients": db.execute("SELECT COUNT(*) FROM users WHERE role IN ('patient','inpatient')").fetchone()[0],
            "doctors": db.execute("SELECT COUNT(*) FROM users WHERE role='doctor' AND approved=1").fetchone()[0],
            "appointments": db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0],
            "labs": db.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0],
        }
        if user["role"] == "admin":
            pending = db.execute("SELECT id,full_name,email,role,department,specialty FROM users WHERE role IN ('doctor','nurse','employee') AND approved=0 ORDER BY id").fetchall()
            recent = db.execute("SELECT a.*,p.full_name patient_name,d.full_name doctor_name FROM appointments a JOIN users p ON p.id=a.patient_id LEFT JOIN users d ON d.id=a.doctor_id ORDER BY a.id DESC LIMIT 8").fetchall()
            return render_template("dashboard.html", user=user, counts=counts, pending=pending, appointments=recent)
        if user["role"] == "patient" or user["role"] == "inpatient":
            appointments = db.execute("SELECT a.*,d.full_name doctor_name,d.specialty FROM appointments a LEFT JOIN users d ON d.id=a.doctor_id WHERE a.patient_id=? ORDER BY a.appointment_date DESC,a.appointment_time DESC", (user["id"],)).fetchall()
            labs = db.execute("SELECT * FROM lab_results WHERE patient_id=? ORDER BY id DESC", (user["id"],)).fetchall()
            doctors = db.execute("SELECT id,full_name,specialty,department FROM users WHERE role='doctor' AND approved=1 ORDER BY full_name").fetchall()
            return render_template("dashboard.html", user=user, counts=counts, appointments=appointments, labs=labs, doctors=doctors)
        if user["role"] == "doctor":
            appointments = db.execute("SELECT a.*,p.full_name patient_name FROM appointments a JOIN users p ON p.id=a.patient_id WHERE a.doctor_id=? ORDER BY a.appointment_date,a.appointment_time", (user["id"],)).fetchall()
            patients = db.execute("SELECT DISTINCT u.id,u.full_name,u.email FROM users u JOIN appointments a ON a.patient_id=u.id WHERE a.doctor_id=?", (user["id"],)).fetchall()
            return render_template("dashboard.html", user=user, counts=counts, appointments=appointments, patients=patients)
        if user["role"] == "nurse":
            appointments = db.execute("SELECT a.*,p.full_name patient_name,d.full_name doctor_name FROM appointments a JOIN users p ON p.id=a.patient_id LEFT JOIN users d ON d.id=a.doctor_id ORDER BY a.appointment_date,a.appointment_time LIMIT 50").fetchall()
            return render_template("dashboard.html", user=user, counts=counts, appointments=appointments)
        return render_template("dashboard.html", user=user, counts=counts)

@app.get("/appointments/availability")
@roles_required("patient", "inpatient")
def appointment_availability():
    appointment_date = request.args.get("date", "")
    appointment_type = request.args.get("type", "Қабылдау")
    doctor_id = request.args.get("doctor_id", type=int)
    if appointment_type == "Қабылдау" and not doctor_id:
        return jsonify(error="Дәрігерді таңдаңыз.", slots=[]), 400
    if appointment_type not in APPOINTMENT_TYPES:
        return jsonify(error="Қабылдау түрі дұрыс емес.", slots=[]), 400
    with get_db() as db:
        if appointment_type == "Қабылдау" and not db.execute(
            "SELECT id FROM users WHERE id=? AND role='doctor' AND approved=1", (doctor_id,)
        ).fetchone():
            return jsonify(error="Таңдалған дәрігер табылмады.", slots=[]), 400
        slots = available_slots(
            db, appointment_date, appointment_type, doctor_id, patient_id=current_user()["id"]
        )
    return jsonify(slots=slots)


@app.route("/appointments", methods=["POST"])
@roles_required("patient", "inpatient")
def create_appointment():
    user = current_user()
    doctor_id = request.form.get("doctor_id", type=int)
    appointment_date = request.form.get("appointment_date", "")
    appointment_time = request.form.get("appointment_time", "")
    appointment_type = request.form.get("appointment_type", "Қабылдау")
    reason = request.form.get("reason", "").strip()

    if appointment_type not in APPOINTMENT_TYPES:
        flash("Қабылдау түрін таңдаңыз.", "error")
        return redirect(url_for("dashboard"))
    if not is_valid_slot(appointment_date, appointment_time):
        flash("Күн мен уақыт жұмыс кестесіне сай емес.", "error")
        return redirect(url_for("dashboard"))
    if appointment_type == "Қабылдау" and not doctor_id:
        flash("Қабылдау үшін дәрігерді таңдаңыз.", "error")
        return redirect(url_for("dashboard"))
    if appointment_type == "Зертхана" and not reason:
        flash("Тапсыратын талдау атауын енгізіңіз.", "error")
        return redirect(url_for("dashboard"))

    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        if appointment_type == "Қабылдау" and not db.execute(
            "SELECT id FROM users WHERE id=? AND role='doctor' AND approved=1", (doctor_id,)
        ).fetchone():
            flash("Таңдалған дәрігер табылмады.", "error")
            return redirect(url_for("dashboard"))
        if appointment_time not in available_slots(
            db, appointment_date, appointment_type, doctor_id, patient_id=user["id"]
        ):
            flash("Бұл уақыт бос емес. Басқа уақытты таңдаңыз.", "error")
            return redirect(url_for("dashboard"))
        db.execute(
            """INSERT INTO appointments
                (patient_id, doctor_id, appointment_date, appointment_time, reason, appointment_type)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (user["id"], doctor_id if appointment_type == "Қабылдау" else None,
             appointment_date, appointment_time, reason, appointment_type),
        )
    flash("Жазылу сұранысы жіберілді.", "success")
    return redirect(url_for("dashboard"))


@app.route("/appointments/<int:appointment_id>/reschedule", methods=["POST"])
@roles_required("patient", "inpatient")
def reschedule_appointment(appointment_id):
    user = current_user()
    appointment_date = request.form.get("new_date", "")
    appointment_time = request.form.get("new_time", "")
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        appointment = db.execute(
            "SELECT * FROM appointments WHERE id=? AND patient_id=?",
            (appointment_id, user["id"]),
        ).fetchone()
        if not appointment:
            abort(404)
        if appointment["status"] not in ACTIVE_STATUSES:
            flash("Бұл жазылымның уақытын өзгерту мүмкін емес.", "error")
            return redirect(url_for("dashboard"))
        if not is_valid_slot(appointment_date, appointment_time):
            flash("Күн мен уақыт жұмыс кестесіне сай емес.", "error")
            return redirect(url_for("dashboard"))
        if appointment_time not in available_slots(
            db, appointment_date, appointment["appointment_type"],
            appointment["doctor_id"], exclude_id=appointment_id, patient_id=user["id"]
        ):
            flash("Бұл уақыт бос емес. Басқа уақытты таңдаңыз.", "error")
            return redirect(url_for("dashboard"))
        db.execute(
            "UPDATE appointments SET appointment_date=?, appointment_time=? WHERE id=?",
            (appointment_date, appointment_time, appointment_id),
        )
    flash("Қабылдау уақыты өзгертілді.", "success")
    return redirect(url_for("dashboard"))


@app.post("/appointments/<int:appointment_id>/cancel")
@roles_required("patient", "inpatient")
def cancel_appointment(appointment_id):
    user = current_user()
    with get_db() as db:
        appointment = db.execute(
            "SELECT status FROM appointments WHERE id=? AND patient_id=?",
            (appointment_id, user["id"]),
        ).fetchone()
        if not appointment:
            abort(404)
        if appointment["status"] not in ACTIVE_STATUSES:
            flash("Бұл жазылымнан бас тарту мүмкін емес.", "error")
        else:
            db.execute(
                "UPDATE appointments SET status='Бас тартылды' WHERE id=?",
                (appointment_id,),
            )
            flash("Қабылдаудан бас тартылды.", "success")
    return redirect(url_for("dashboard"))


@app.post("/appointments/<int:appointment_id>/status")
@roles_required("admin", "doctor")
def update_appointment_status(appointment_id):
    user = current_user()
    status = request.form.get("status", "")
    if status not in {"Расталды", "Аяқталды", "Бас тартылды"}:
        abort(400)
    with get_db() as db:
        appointment = db.execute(
            "SELECT doctor_id, status FROM appointments WHERE id=?", (appointment_id,)
        ).fetchone()
        if not appointment:
            abort(404)
        if user["role"] == "doctor" and appointment["doctor_id"] != user["id"]:
            abort(403)
        if appointment["status"] not in ACTIVE_STATUSES:
            flash("Бұл қабылдаудың мәртебесін өзгерту мүмкін емес.", "error")
            return redirect(url_for("dashboard"))
        db.execute(
            "UPDATE appointments SET status=? WHERE id=?",
            (status, appointment_id),
        )
    flash("Қабылдау мәртебесі жаңартылды.", "success")
    return redirect(url_for("dashboard"))

@app.route("/lab", methods=["POST"])
@roles_required("doctor","nurse")
def add_lab():
    patient_email = request.form.get("patient_email","").strip().lower()
    test_name = request.form.get("test_name","").strip()
    result = request.form.get("result_text","").strip()
    with get_db() as db:
        patient = db.execute("SELECT id FROM users WHERE email=? AND role IN ('patient','inpatient')",(patient_email,)).fetchone()
        if not patient or not test_name:
            flash("Пациент email-і мен талдау атауын дұрыс енгізіңіз.", "error")
        else:
            db.execute("INSERT INTO lab_results(patient_id,test_name,result_text,status) VALUES(?,?,?,?)",
                       (patient["id"],test_name,result,"Нәтиже дайын" if result else "Тағайындалды"))
            flash("Зертхана жазбасы сақталды.", "success")
    return redirect(url_for("dashboard"))

@app.route("/admin/approve/<int:user_id>", methods=["POST"])
@roles_required("admin")
def approve_worker(user_id):
    with get_db() as db:
        db.execute("UPDATE users SET approved=1 WHERE id=? AND role IN ('doctor','nurse','employee')",(user_id,))
    flash("Қызметкер аккаунты расталды.", "success")
    return redirect(url_for("dashboard"))

@app.route("/admin/create-worker", methods=["POST"])
@roles_required("admin")
def create_worker():
    name=request.form.get("full_name","").strip()
    email=request.form.get("email","").strip().lower()
    password=request.form.get("password","")
    role=request.form.get("role","employee")
    specialty=request.form.get("specialty","").strip()
    department=request.form.get("department","").strip()
    if role not in {"admin","doctor","nurse","employee","kitchen","cleaner","operating_room"} or not name or "@" not in email or len(password)<8:
        flash("Қызметкер деректерін тексеріңіз. Құпиясөз кемінде 8 таңба болуы керек.", "error")
    else:
        try:
            with get_db() as db:
                db.execute("INSERT INTO users(full_name,email,password_hash,role,specialty,department,approved) VALUES(?,?,?,?,?,?,1)",
                    (name,email,generate_password_hash(password),role,specialty,department))
            flash("Қызметкер аккаунты құрылды.", "success")
        except Exception:
            flash("Бұл email тіркелген немесе деректер жарамсыз.", "error")
    return redirect(url_for("dashboard"))


@app.route("/medication-request", methods=["POST"])
@roles_required("patient", "inpatient")
def medication_request():
    name=request.form.get("medicine_name","").strip(); note=request.form.get("note","").strip()
    if name:
        with get_db() as db: db.execute("INSERT INTO medication_requests(patient_id,medicine_name,note) VALUES(?,?,?)",(current_user()["id"],name,note))
        flash("Ұсыныс дәрігердің қарауына жіберілді. Бұл нақты рецепт емес.","success")
    else: flash("Дәрі атауын енгізіңіз.","error")
    return redirect(url_for("dashboard"))

@app.route("/prescription", methods=["POST"])
@roles_required("doctor")
def add_prescription():
    patient_id=request.form.get("patient_id", type=int); name=request.form.get("medicine_name","").strip(); instructions=request.form.get("instructions","").strip()
    with get_db() as db:
        patient=db.execute("SELECT id FROM users WHERE id=? AND role IN ('patient','inpatient')",(patient_id,)).fetchone()
        if patient and name:
            db.execute("INSERT INTO prescriptions(patient_id,doctor_id,medicine_name,instructions) VALUES(?,?,?,?)",(patient_id,current_user()["id"],name,instructions)); flash("Демо тағайындау жазылды.","success")
        else: flash("Пациент пен дәрі атауын таңдаңыз.","error")
    return redirect(url_for("dashboard"))

@app.route("/medication-request/<int:req_id>/review", methods=["POST"])
@roles_required("doctor", "admin")
def review_medication_request(req_id):
    status=request.form.get("status","Қабылданбады")
    if status not in {"Қабылданды — дәрігер тексерді", "Қабылданбады"}: status="Қабылданбады"
    with get_db() as db: db.execute("UPDATE medication_requests SET status=? WHERE id=?",(status,req_id))
    flash("Ұсыныс мәртебесі жаңартылды.","success"); return redirect(url_for("dashboard"))

@app.route("/menu", methods=["POST"])
@roles_required("admin", "employee", "kitchen")
def add_menu():
    date=request.form.get("menu_date",""); meal=request.form.get("meal","").strip(); desc=request.form.get("description","").strip(); diet=request.form.get("diet_note","").strip()
    if date and meal and desc:
        with get_db() as db: db.execute("INSERT INTO menus(menu_date,meal,description,diet_note,created_by) VALUES(?,?,?,?,?)",(date,meal,desc,diet,current_user()["id"]))
        flash("Демо ас мәзірі қосылды.","success")
    else: flash("Күн, ас түрі және мәзірді толтырыңыз.","error")
    return redirect(url_for("dashboard"))

@app.route("/cleaning-task", methods=["POST"])
@roles_required("admin", "employee", "cleaner")
def add_cleaning_task():
    area=request.form.get("area","").strip(); person=request.form.get("assigned_to","").strip(); date=request.form.get("task_date",""); note=request.form.get("note","").strip()
    if area and date:
        with get_db() as db: db.execute("INSERT INTO cleaning_tasks(area,assigned_to,task_date,note) VALUES(?,?,?,?)",(area,person,date,note))
        flash("Тазалау тапсырмасы қосылды.","success")
    else: flash("Аймақ пен күнді енгізіңіз.","error")
    return redirect(url_for("dashboard"))

@app.route("/cleaning-task/<int:task_id>/done", methods=["POST"])
@roles_required("admin", "employee", "cleaner")
def complete_cleaning_task(task_id):
    with get_db() as db: db.execute("UPDATE cleaning_tasks SET status='Орындалды' WHERE id=?",(task_id,))
    flash("Тапсырма орындалды деп белгіленді.","success"); return redirect(url_for("dashboard"))

@app.route("/operating-room", methods=["POST"])
@roles_required("admin", "employee", "operating_room")
def add_operating_room():
    room=request.form.get("room_name","").strip(); date=request.form.get("schedule_date",""); start=request.form.get("start_time",""); end=request.form.get("end_time",""); proc=request.form.get("procedure_name","").strip()
    if room and date and start and end:
        with get_db() as db: db.execute("INSERT INTO operating_rooms(room_name,schedule_date,start_time,end_time,procedure_name) VALUES(?,?,?,?,?)",(room,date,start,end,proc))
        flash("Операциялық бөлме кестесі қосылды.","success")
    else: flash("Бөлме, күн және уақыттарды енгізіңіз.","error")
    return redirect(url_for("dashboard"))

@app.route("/ward-note", methods=["POST"])
@roles_required("admin", "nurse", "doctor")
def add_ward_note():
    patient_id=request.form.get("patient_id",type=int); room=request.form.get("room_number","").strip(); diet=request.form.get("diet","").strip(); allergies=request.form.get("allergies","").strip(); note=request.form.get("note","").strip()
    with get_db() as db:
        patient=db.execute("SELECT id FROM users WHERE id=? AND role='inpatient'",(patient_id,)).fetchone()
        if patient:
            db.execute("INSERT INTO ward_notes(patient_id,room_number,diet,allergies,note) VALUES(?,?,?,?,?)",(patient_id,room,diet,allergies,note)); flash("Демо палата ақпараты сақталды.","success")
        else: flash("Стационардағы пациентті таңдаңыз.","error")
    return redirect(url_for("dashboard"))

# Әр бөлім жеке бетте ашылады, шатаспау үшін бәрін бір экранға тықпалаған жоқпын.
@app.route('/profile', methods=['GET','POST'])
@roles_required('patient','inpatient')
def profile():
    user=current_user()
    with get_db() as db:
        if request.method == 'POST':
            try:
                weight=float(request.form.get('weight') or 0) or None
                height=float(request.form.get('height') or 0) or None
            except ValueError:
                flash('Салмақ пен бойды санмен жазшы.', 'error'); return redirect(url_for('profile'))
            db.execute("""INSERT INTO patient_profiles(user_id,blood_type,weight,height,allergies,updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET blood_type=excluded.blood_type,
            weight=excluded.weight,height=excluded.height,allergies=excluded.allergies,updated_at=CURRENT_TIMESTAMP""",
            (user['id'],request.form.get('blood_type','').strip(),weight,height,request.form.get('allergies','').strip()))
            flash('Медициналық профиль сақталды.', 'success'); return redirect(url_for('profile'))
        profile=db.execute('SELECT * FROM patient_profiles WHERE user_id=?',(user['id'],)).fetchone()
    return render_template('section.html',page='profile',user=user,profile=profile)

@app.route('/sos', methods=['GET','POST'])
@roles_required('patient','inpatient')
def sos():
    if request.method == 'POST':
        try:
            lat=float(request.form.get('latitude','')); lon=float(request.form.get('longitude',''))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError()
        except ValueError:
            flash('GPS координаттары дұрыс емес. Геолокацияға рұқсат беріп көрші.', 'error'); return redirect(url_for('sos'))
        with get_db() as db: db.execute('INSERT INTO sos_alerts(patient_id,latitude,longitude) VALUES(?,?,?)',(current_user()['id'],lat,lon))
        flash('SOS демо-хабарламасы сақталды. Бұл жүйе шынайы жедел жәрдем шақырмайды!', 'success'); return redirect(url_for('sos'))
    with get_db() as db: alerts=db.execute('SELECT * FROM sos_alerts WHERE patient_id=? ORDER BY id DESC LIMIT 10',(current_user()['id'],)).fetchall()
    return render_template('section.html',page='sos',user=current_user(),alerts=alerts)

@app.route('/medic-sos')
@roles_required('admin','doctor','nurse')
def medic_sos():
    with get_db() as db: alerts=db.execute('SELECT s.*,u.full_name,u.email FROM sos_alerts s JOIN users u ON u.id=s.patient_id ORDER BY s.id DESC LIMIT 100').fetchall()
    return render_template('section.html',page='medic_sos',user=current_user(),alerts=alerts)

@app.route('/announcements', methods=['GET','POST'])
@login_required
def announcements():
    user=current_user()
    if request.method=='POST':
        if user['role'] not in {'admin','doctor'}: abort(403)
        title=request.form.get('title','').strip(); body=request.form.get('body','').strip()
        if title and body:
            with get_db() as db: db.execute('INSERT INTO announcements(title,body) VALUES(?,?)',(title,body))
            flash('Хабарландыру жарияланды.', 'success')
        else: flash('Тақырып пен мәтінді толтыршы.', 'error')
        return redirect(url_for('announcements'))
    with get_db() as db: items=db.execute('SELECT * FROM announcements ORDER BY id DESC LIMIT 100').fetchall()
    return render_template('section.html',page='announcements',user=user,announcements=items)

@app.route('/inventory', methods=['GET','POST'])
@roles_required('admin','employee','kitchen')
def inventory():
    user=current_user()
    if request.method=='POST':
        name=request.form.get('item_name','').strip(); unit=request.form.get('unit','дана').strip() or 'дана'
        try: qty=float(request.form.get('quantity','')); minimum=float(request.form.get('minimum','0') or 0)
        except ValueError: qty=-1; minimum=0
        if name and qty>=0 and minimum>=0:
            with get_db() as db: db.execute('INSERT INTO inventory_items(item_name,quantity,unit,minimum,updated_by) VALUES(?,?,?,?,?)',(name,qty,unit,minimum,user['id']))
            flash('Өнім қоймаға қосылды.', 'success')
        else: flash('Өнім мен дұрыс санын жазшы.', 'error')
        return redirect(url_for('inventory'))
    with get_db() as db: items=db.execute('SELECT * FROM inventory_items ORDER BY item_name').fetchall()
    return render_template('section.html',page='inventory',user=user,items=items)

@app.route('/inventory/<int:item_id>/update',methods=['POST'])
@roles_required('admin','employee','kitchen')
def update_inventory(item_id):
    try: qty=float(request.form.get('quantity',''))
    except ValueError: qty=-1
    if qty < 0: flash('Саны теріс болмауы керек.', 'error')
    else:
        with get_db() as db: db.execute('UPDATE inventory_items SET quantity=?,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?',(qty,current_user()['id'],item_id))
        flash('Қоймадағы сан жаңартылды.', 'success')
    return redirect(url_for('inventory'))

@app.route('/search')
@login_required
def search():
    q=request.args.get('q','').strip(); user=current_user(); results=[]
    if q:
        pattern='%'+q+'%'
        with get_db() as db:
            if user['role']=='admin':
                rows=db.execute("SELECT full_name AS title,email AS detail,'/dashboard' AS href FROM users WHERE full_name LIKE ? OR email LIKE ? LIMIT 30",(pattern,pattern)).fetchall(); results.extend(dict(r) for r in rows)
            elif user['role'] in {'doctor','nurse'}:
                rows=db.execute("SELECT full_name AS title,email AS detail,'/dashboard' AS href FROM users WHERE role IN ('patient','inpatient') AND (full_name LIKE ? OR email LIKE ?) LIMIT 30",(pattern,pattern)).fetchall(); results.extend(dict(r) for r in rows)
            rows=db.execute("SELECT title,body AS detail,'/announcements' AS href FROM announcements WHERE title LIKE ? OR body LIKE ? LIMIT 20",(pattern,pattern)).fetchall(); results.extend(dict(r) for r in rows)
            if user['role'] in {'admin','employee','kitchen'}:
                rows=db.execute("SELECT item_name AS title,CAST(quantity AS TEXT)||' '||unit AS detail,'/inventory' AS href FROM inventory_items WHERE item_name LIKE ? LIMIT 20",(pattern,)).fetchall(); results.extend(dict(r) for r in rows)
    return render_template('section.html',page='search',user=user,q=q,results=results)

@app.route('/admin/accounts')
@roles_required('admin')
def admin_accounts():
    with get_db() as db: accounts=db.execute('SELECT id,full_name,email,role,department,specialty,approved,created_at FROM users ORDER BY id DESC').fetchall()
    return render_template('section.html',page='accounts',user=current_user(),accounts=accounts)

@app.route('/admin/reset-password/<int:user_id>',methods=['POST'])
@roles_required('admin')
def reset_password(user_id):
    # Парольды ашық көрсетпейміз: базаға қауіпсіз хэш қана сақталады. Әкімші жаңасын қоя алады.
    new_password=request.form.get('new_password','')
    if len(new_password)<8: flash('Жаңа құпиясөз кемінде 8 таңба болсын.', 'error')
    else:
        with get_db() as db: db.execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(new_password),user_id))
        flash('Құпиясөз жаңартылды. Ескі құпиясөз көрсетілмейді.', 'success')
    return redirect(url_for('admin_accounts'))

@app.route('/back')
@login_required
def back():
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/kitchen', methods=['GET','POST'])
@roles_required('admin','employee','kitchen')
def kitchen():
    if request.method=='POST':
        date=request.form.get('menu_date',''); meal=request.form.get('meal','').strip(); desc=request.form.get('description','').strip(); diet=request.form.get('diet_note','').strip()
        if date and meal and desc:
            with get_db() as db: db.execute('INSERT INTO menus(menu_date,meal,description,diet_note,created_by) VALUES(?,?,?,?,?)',(date,meal,desc,diet,current_user()['id']))
            flash('Ас мәзірі сақталды.', 'success')
        else: flash('Күн, ас түрі және тағамдарды толтыршы.', 'error')
        return redirect(url_for('kitchen'))
    with get_db() as db: menus=db.execute('SELECT * FROM menus ORDER BY menu_date DESC,id DESC LIMIT 100').fetchall()
    return render_template('section.html',page='kitchen',user=current_user(),menus=menus)

@app.route('/cleaning', methods=['GET','POST'])
@login_required
def cleaning():
    # Кез келген кірген адам өтінім береді, ал тазалық тобы тапсырманы орындайды.
    user=current_user()
    if request.method=='POST':
        area=request.form.get('area','').strip()
        date=request.form.get('task_date','')
        note=request.form.get('note','').strip()
        priority=request.form.get('priority','Қалыпты')
        if priority not in {'Төмен','Қалыпты','Шұғыл'}:
            priority='Қалыпты'
        if area and date:
            with get_db() as db:
                db.execute("""INSERT INTO cleaning_tasks
                    (area,assigned_to,task_date,note,status,requester_id,priority)
                    VALUES(?,?,?,?,?,?,?)""",
                    (area,'',date,note,'Жаңа',user['id'],priority))
            flash('Тазалыққа өтінім жіберілді. Қызметкерлердің панелінде көрінеді.','success')
        else:
            flash('Орын мен күнді толтыршы.','error')
        return redirect(url_for('cleaning'))
    with get_db() as db:
        tasks=db.execute("""SELECT t.*,u.full_name AS requester_name
            FROM cleaning_tasks t LEFT JOIN users u ON u.id=t.requester_id
            ORDER BY CASE t.priority WHEN 'Шұғыл' THEN 0 WHEN 'Қалыпты' THEN 1 ELSE 2 END,
            t.task_date DESC,t.id DESC LIMIT 200""").fetchall()
    return render_template('section.html',page='cleaning',user=user,tasks=tasks)

@app.route('/cleaning/<int:task_id>/claim', methods=['POST'])
@roles_required('admin','cleaner')
def cleaning_claim(task_id):
    user=current_user()
    with get_db() as db:
        task=db.execute('SELECT * FROM cleaning_tasks WHERE id=?',(task_id,)).fetchone()
        if not task:
            abort(404)
        if task['status']=='Орындалды':
            flash('Бұл тапсырма аяқталып қойған.','warning')
        elif task['assigned_to'] and task['assigned_to'] != user['full_name'] and user['role']!='admin':
            flash('Бұл тапсырманы басқа қызметкер қабылдап қойған.','warning')
        else:
            db.execute("UPDATE cleaning_tasks SET assigned_to=?,status='Қабылданды' WHERE id=?",(user['full_name'],task_id))
            flash('Тапсырманы қабылдадың.','success')
    return redirect(url_for('cleaning'))

@app.route('/cleaning/<int:task_id>/status', methods=['POST'])
@roles_required('admin','cleaner')
def cleaning_status(task_id):
    user=current_user(); status=request.form.get('status','')
    if status not in {'Орындалып жатыр','Орындалды'}:
        abort(400)
    with get_db() as db:
        task=db.execute('SELECT * FROM cleaning_tasks WHERE id=?',(task_id,)).fetchone()
        if not task:
            abort(404)
        if user['role']=='cleaner' and task['assigned_to'] != user['full_name']:
            abort(403)
        db.execute('UPDATE cleaning_tasks SET status=? WHERE id=?',(status,task_id))
    flash('Тапсырма мәртебесі жаңартылды.','success')
    return redirect(url_for('cleaning'))

@app.route('/operating-rooms', methods=['GET','POST'])
@roles_required('admin','employee','operating_room')
def operating_rooms():
    if request.method=='POST':
        room=request.form.get('room_name','').strip(); date=request.form.get('schedule_date',''); start=request.form.get('start_time',''); end=request.form.get('end_time',''); proc=request.form.get('procedure_name','').strip()
        if room and date and start and end:
            with get_db() as db: db.execute('INSERT INTO operating_rooms(room_name,schedule_date,start_time,end_time,procedure_name) VALUES(?,?,?,?,?)',(room,date,start,end,proc))
            flash('Операциялық бөлме кестесі сақталды.', 'success')
        else: flash('Бөлме, күн және уақытты толтыршы.', 'error')
        return redirect(url_for('operating_rooms'))
    with get_db() as db: rooms=db.execute('SELECT * FROM operating_rooms ORDER BY schedule_date DESC,start_time DESC LIMIT 100').fetchall()
    return render_template('section.html',page='operating_rooms',user=current_user(),rooms=rooms)

@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="Бұл бөлімге рұқсатыңыз жоқ."), 403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
