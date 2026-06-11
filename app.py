"""
D&H Sécheron — Organization Chart
---------------------------------
A small Flask app that renders the company org chart, lets an admin
add / edit / move employees, and automatically keeps a career history
(promotions, transfers, title changes) for every employee.

Run locally:
    pip install -r requirements.txt
    python app.py          ->  http://localhost:5000

Deploy on Render:  see README.md
"""

import os
from datetime import date, datetime
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from flask_sqlalchemy import SQLAlchemy

# --------------------------------------------------------------------------
# App / DB setup
# --------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

db_url = os.environ.get("DATABASE_URL", "sqlite:///orgchart.db")
# Render's Postgres URLs start with postgres:// which SQLAlchemy rejects
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

db = SQLAlchemy(app)

EVENT_TYPES = ["Hired", "Promotion", "Lateral Move", "Department Transfer",
               "Title Change", "Re-joined"]

DEPARTMENTS = ["Management", "Sales & Marketing", "Production", "R&D",
               "Quality Assurance", "Finance & Accounts", "Human Resources",
               "Supply Chain", "IT", "Exports"]


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    location = db.Column(db.String(80))
    photo_url = db.Column(db.String(300))
    joined_on = db.Column(db.Date, default=date.today)
    is_active = db.Column(db.Boolean, default=True)

    # current role (history is kept separately in RoleEvent)
    title = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey("employee.id"))

    manager = db.relationship("Employee", remote_side=[id],
                              backref="reports")
    events = db.relationship("RoleEvent", backref="employee",
                             order_by="RoleEvent.start_date",
                             cascade="all, delete-orphan")

    @property
    def tenure_years(self):
        if not self.joined_on:
            return 0
        return round((date.today() - self.joined_on).days / 365.25, 1)

    @property
    def initials(self):
        parts = self.name.split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


class RoleEvent(db.Model):
    """One row per role an employee has held. The open row (end_date is
    NULL) is the current role. Editing a role through the 'Record movement'
    form closes the open row and opens a new one — that is the whole
    promotion-tracking mechanism."""
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employee.id"),
                            nullable=False)
    event_type = db.Column(db.String(40), nullable=False)   # Hired/Promotion/…
    title = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80), nullable=False)
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date)                            # NULL = current
    notes = db.Column(db.Text)

    @property
    def duration_label(self):
        end = self.end_date or date.today()
        months = (end.year - self.start_date.year) * 12 + \
                 (end.month - self.start_date.month)
        years, months = divmod(max(months, 0), 12)
        bits = []
        if years:
            bits.append(f"{years} yr" + ("s" if years > 1 else ""))
        if months:
            bits.append(f"{months} mo")
        return " ".join(bits) or "< 1 mo"


# --------------------------------------------------------------------------
# Auth helper (single shared admin password, set via ADMIN_PASSWORD env var)
# --------------------------------------------------------------------------
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        flash("Wrong password. Try again.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("chart"))


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------
@app.route("/")
def chart():
    roots = (Employee.query
             .filter_by(manager_id=None, is_active=True)
             .order_by(Employee.name).all())
    total = Employee.query.filter_by(is_active=True).count()
    departments = (db.session.query(Employee.department)
                   .filter_by(is_active=True).distinct().count())
    return render_template("chart.html", roots=roots, total=total,
                           departments=departments)


@app.route("/employee/<int:emp_id>")
def employee(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    history = sorted(emp.events, key=lambda e: e.start_date, reverse=True)
    return render_template("employee.html", emp=emp, history=history)


@app.route("/directory")
def directory():
    q = request.args.get("q", "").strip()
    dept = request.args.get("dept", "")
    query = Employee.query.filter_by(is_active=True)
    if q:
        query = query.filter(Employee.name.ilike(f"%{q}%") |
                             Employee.title.ilike(f"%{q}%"))
    if dept:
        query = query.filter_by(department=dept)
    employees = query.order_by(Employee.department, Employee.name).all()
    depts = [d[0] for d in db.session.query(Employee.department)
             .filter_by(is_active=True).distinct().order_by(Employee.department)]
    return render_template("directory.html", employees=employees,
                           depts=depts, q=q, dept=dept)


# --------------------------------------------------------------------------
# Admin: manage people
# --------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin():
    employees = Employee.query.order_by(Employee.is_active.desc(),
                                        Employee.name).all()
    return render_template("admin.html", employees=employees)


def _manager_choices(exclude_id=None):
    q = Employee.query.filter_by(is_active=True).order_by(Employee.name)
    return [e for e in q if e.id != exclude_id]


@app.route("/admin/employee/new", methods=["GET", "POST"])
@admin_required
def employee_new():
    if request.method == "POST":
        f = request.form
        emp = Employee(
            name=f["name"].strip(),
            email=f.get("email", "").strip() or None,
            phone=f.get("phone", "").strip() or None,
            location=f.get("location", "").strip() or None,
            photo_url=f.get("photo_url", "").strip() or None,
            title=f["title"].strip(),
            department=f["department"],
            manager_id=int(f["manager_id"]) if f.get("manager_id") else None,
            joined_on=_parse_date(f.get("joined_on")) or date.today(),
        )
        db.session.add(emp)
        db.session.flush()
        db.session.add(RoleEvent(
            employee_id=emp.id, event_type="Hired",
            title=emp.title, department=emp.department,
            start_date=emp.joined_on,
            notes=f.get("notes", "").strip() or None,
        ))
        db.session.commit()
        flash(f"{emp.name} added to the chart.", "ok")
        return redirect(url_for("admin"))
    return render_template("employee_form.html", emp=None,
                           managers=_manager_choices(),
                           departments=DEPARTMENTS)


@app.route("/admin/employee/<int:emp_id>/edit", methods=["GET", "POST"])
@admin_required
def employee_edit(emp_id):
    """Edits contact details only. Role / title / department changes go
    through 'Record movement' so the history stays truthful."""
    emp = Employee.query.get_or_404(emp_id)
    if request.method == "POST":
        f = request.form
        emp.name = f["name"].strip()
        emp.email = f.get("email", "").strip() or None
        emp.phone = f.get("phone", "").strip() or None
        emp.location = f.get("location", "").strip() or None
        emp.photo_url = f.get("photo_url", "").strip() or None
        if f.get("joined_on"):
            emp.joined_on = _parse_date(f["joined_on"])
        db.session.commit()
        flash("Details saved.", "ok")
        return redirect(url_for("employee", emp_id=emp.id))
    return render_template("employee_form.html", emp=emp,
                           managers=_manager_choices(emp.id),
                           departments=DEPARTMENTS)


@app.route("/admin/employee/<int:emp_id>/move", methods=["GET", "POST"])
@admin_required
def employee_move(emp_id):
    """The promotion / transfer form. Closes the current RoleEvent and
    opens a new one, then updates the employee's current role."""
    emp = Employee.query.get_or_404(emp_id)
    if request.method == "POST":
        f = request.form
        effective = _parse_date(f.get("effective_date")) or date.today()

        open_event = next((e for e in emp.events if e.end_date is None), None)
        if open_event:
            open_event.end_date = effective

        emp.title = f["title"].strip()
        emp.department = f["department"]
        emp.manager_id = int(f["manager_id"]) if f.get("manager_id") else None

        db.session.add(RoleEvent(
            employee_id=emp.id,
            event_type=f["event_type"],
            title=emp.title,
            department=emp.department,
            start_date=effective,
            notes=f.get("notes", "").strip() or None,
        ))
        db.session.commit()
        flash(f"Movement recorded for {emp.name}.", "ok")
        return redirect(url_for("employee", emp_id=emp.id))
    return render_template("move_form.html", emp=emp,
                           managers=_manager_choices(emp.id),
                           departments=DEPARTMENTS,
                           event_types=[t for t in EVENT_TYPES
                                        if t not in ("Hired",)])


@app.route("/admin/employee/<int:emp_id>/toggle", methods=["POST"])
@admin_required
def employee_toggle(emp_id):
    """Mark someone as exited (or re-activate). Their reports are moved up
    to their manager so the chart never breaks."""
    emp = Employee.query.get_or_404(emp_id)
    emp.is_active = not emp.is_active
    if not emp.is_active:
        open_event = next((e for e in emp.events if e.end_date is None), None)
        if open_event:
            open_event.end_date = date.today()
        for report in list(emp.reports):
            report.manager_id = emp.manager_id
        emp.manager_id = None
    db.session.commit()
    flash(("Marked as exited. Their team now reports to their manager."
           if not emp.is_active else "Re-activated."), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/employee/<int:emp_id>/delete", methods=["POST"])
@admin_required
def employee_delete(emp_id):
    emp = Employee.query.get_or_404(emp_id)
    for report in list(emp.reports):
        report.manager_id = emp.manager_id
    name = emp.name
    db.session.delete(emp)
    db.session.commit()
    flash(f"{name} deleted permanently (history included).", "ok")
    return redirect(url_for("admin"))


# --------------------------------------------------------------------------
# Helpers / bootstrapping
# --------------------------------------------------------------------------
def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


@app.template_filter("nice_date")
def nice_date(d):
    return d.strftime("%b %Y") if d else "Present"


def seed_if_empty():
    """Load a small sample hierarchy on first run so the chart isn't blank.
    Delete these people from the Manage screen and add your real ones."""
    if Employee.query.count():
        return

    def add(name, title, dept, manager=None, joined=None, location="Indore"):
        emp = Employee(name=name, title=title, department=dept,
                       manager_id=manager.id if manager else None,
                       joined_on=joined or date(2018, 4, 1),
                       location=location)
        db.session.add(emp)
        db.session.flush()
        db.session.add(RoleEvent(employee_id=emp.id, event_type="Hired",
                                 title=title, department=dept,
                                 start_date=emp.joined_on))
        return emp

    md = add("A. Sharma", "Managing Director", "Management",
             joined=date(2005, 6, 1), location="Mumbai")
    coo = add("R. Verma", "Chief Operating Officer", "Management", md,
              date(2010, 2, 1), "Mumbai")
    sales = add("P. Iyer", "VP — Sales & Marketing", "Sales & Marketing", md,
                date(2012, 8, 1), "Mumbai")
    plant = add("S. Kulkarni", "Plant Head", "Production", coo,
                date(2014, 1, 15))
    rnd = add("Dr. N. Joshi", "Head of R&D", "R&D", coo, date(2016, 7, 1))
    add("M. Patel", "Regional Sales Manager — West", "Sales & Marketing",
        sales, date(2019, 3, 1), "Mumbai")
    add("K. Singh", "Regional Sales Manager — North", "Sales & Marketing",
        sales, date(2020, 11, 1), "Delhi")
    qa = add("V. Rao", "QA Manager", "Quality Assurance", plant,
             date(2017, 5, 1))
    add("T. Mishra", "Production Supervisor", "Production", plant,
        date(2021, 9, 1))
    add("A. Khan", "Welding Metallurgist", "R&D", rnd, date(2022, 2, 1))

    # give one person a visible promotion history for the demo
    promo_date = date(2023, 4, 1)
    open_ev = next(e for e in qa.events if e.end_date is None)
    open_ev.end_date = promo_date
    open_ev.title, open_ev.department = "QA Engineer", "Quality Assurance"
    qa.title = "QA Manager"
    db.session.add(RoleEvent(employee_id=qa.id, event_type="Promotion",
                             title="QA Manager",
                             department="Quality Assurance",
                             start_date=promo_date,
                             notes="Promoted after NABL audit success."))
    db.session.commit()


with app.app_context():
    db.create_all()
    seed_if_empty()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
