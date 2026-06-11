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

DEPARTMENTS = [
    "Management", "QA & QC", "R&D", "Dry Mix", "TSD", "Admin & IR",
    "Production", "PPC", "BSR & Logistics", "Stores", "Maintenance",
    "Accounts & Finance", "Commercial", "Sales", "Export",
    "Digital Marketing", "Traded Goods", "Reclamation Services",
    "Purchase", "Projects", "IT", "HR",
]

# one color per department — used for card accents, avatar rings, legend
DEPT_COLORS = {
    "Management":           "#C8102E",
    "QA & QC":              "#2F9E44",
    "R&D":                  "#6741D9",
    "Dry Mix":              "#E8590C",
    "TSD":                  "#A86E0F",
    "Admin & IR":           "#D6336C",
    "Production":           "#F76707",
    "PPC":                  "#E67700",
    "BSR & Logistics":      "#94751A",
    "Stores":               "#5C7A1F",
    "Maintenance":          "#3B7A57",
    "Accounts & Finance":   "#1971C2",
    "Commercial":           "#0B7285",
    "Sales":                "#087F5B",
    "Export":               "#2B8A3E",
    "Digital Marketing":    "#862E9C",
    "Traded Goods":         "#5C4033",
    "Reclamation Services": "#364FC7",
    "Purchase":             "#C92A2A",
    "Projects":             "#5F3DC4",
    "IT":                   "#1864AB",
    "HR":                   "#9C36B5",
}
_FALLBACK_COLORS = ["#C8102E", "#0B7285", "#E8590C", "#6741D9", "#2F9E44",
                    "#1971C2", "#D6336C", "#A86E0F", "#364FC7", "#087F5B"]


def dept_color(dept):
    if dept in DEPT_COLORS:
        return DEPT_COLORS[dept]
    return _FALLBACK_COLORS[abs(hash(dept)) % len(_FALLBACK_COLORS)]


app.jinja_env.globals["dept_color"] = dept_color


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
    total = Employee.query.filter_by(is_active=True).count()
    departments = (db.session.query(Employee.department)
                   .filter_by(is_active=True).distinct().count())
    return render_template("chart.html", total=total, departments=departments)


@app.route("/api/people")
def api_people():
    """Flat employee list consumed by d3-org-chart on the chart page."""
    people = Employee.query.filter_by(is_active=True).all()
    promo_counts = {}
    for ev in RoleEvent.query.filter_by(event_type="Promotion"):
        promo_counts[ev.employee_id] = promo_counts.get(ev.employee_id, 0) + 1
    return {
        "people": [{
            "id": e.id,
            "parentId": e.manager_id,
            "name": e.name,
            "title": e.title,
            "department": e.department,
            "location": e.location or "",
            "photoUrl": e.photo_url or "",
            "initials": e.initials,
            "tenure": e.tenure_years,
            "promotions": promo_counts.get(e.id, 0),
        } for e in people]
    }


@app.route("/api/org")
def api_org():
    """Flat node list for d3-org-chart. A virtual company node sits at the
    root so the chart always has a single root even with several
    manager-less people."""
    from flask import jsonify
    emps = Employee.query.filter_by(is_active=True).all()
    active_ids = {e.id for e in emps}

    nodes = [{
        "id": "company",
        "parentId": None,
        "isCompany": True,
        "name": "D&H Sécheron",
        "title": "Electrodes Pvt. Ltd. · since 1966",
    }]
    for e in emps:
        parent = (f"e{e.manager_id}"
                  if e.manager_id in active_ids else "company")
        reports = sum(1 for r in e.reports if r.is_active)
        nodes.append({
            "id": f"e{e.id}",
            "parentId": parent,
            "empId": e.id,
            "name": e.name,
            "title": e.title,
            "department": e.department,
            "color": dept_color(e.department),
            "photo": e.photo_url or "",
            "initials": e.initials,
            "location": e.location or "",
            "reports": reports,
        })
    return jsonify(nodes)


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
    """Seed the real D&H Secheron org structure (Unit 1 & 3) on first run."""
    if Employee.query.count():
        return

    def add(name, title, dept, manager=None, joined=None, location="Indore"):
        emp = Employee(name=name, title=title, department=dept,
                       manager_id=manager.id if manager else None,
                       joined_on=joined or date(2015, 1, 1),
                       location=location)
        db.session.add(emp)
        db.session.flush()
        db.session.add(RoleEvent(employee_id=emp.id, event_type="Hired",
                                 title=title, department=dept,
                                 start_date=emp.joined_on))
        return emp

    # Board / top level
    vc  = add("Arvind Maheshwari", "Vice Chairman", "Management",
              joined=date(1990, 1, 1), location="Mumbai")
    add("Arnav Maheshwari", "Executive Director", "Management", vc,
        date(2005, 6, 1), "Mumbai")
    jmd = add("Dr. TJP Rao", "Joint Managing Director", "Management", vc,
              date(2000, 4, 1), "Indore")

    # Direct reports to JMD
    add("Prashant Tilak",        "AGM - QA & QC",             "QA & QC",           jmd, date(2010, 3, 1))
    add("Dr. Suresh Telu",       "VP - R&D",                  "R&D",               jmd, date(2008, 7, 1))
    add("Narendra Udasi",        "AGM - Dry Mix",             "Dry Mix",           jmd, date(2012, 5, 1))
    add("Rajesh Kumar",          "DGM - TSD",                 "TSD",               jmd, date(2011, 9, 1))
    add("Brijbhan Singh Rajput", "AGM - Admin & IR",          "Admin & IR",        jmd, date(2009, 2, 1))
    prod = add("Ripal Naik",     "AVP - Production",          "Production",        jmd, date(2013, 6, 1))
    add("L.R. Golani",           "GM - Accounts & Finance",   "Accounts & Finance",jmd, date(2007, 4, 1))
    add("Shivi Chaturvedi",      "GM - Commercial",           "Commercial",        jmd, date(2014, 1, 1))
    sales= add("V. Ganesh Kumar","Vice President - Sales",    "Sales",             jmd, date(2006, 8, 1), "Mumbai")
    add("Nilesh Paul",           "AGM - Purchase",            "Purchase",          jmd, date(2015, 3, 1))
    add("Riyush Godha",          "Sr. Manager - Projects",    "Projects",          jmd, date(2018, 7, 1))
    add("Kedar Joshi",           "DGM - IT & EDP",            "IT",                jmd, date(2010, 11, 1))
    add("Rahul Singh",           "DGM - HR",                  "HR",                jmd, date(2012, 4, 1))

    # Under Production
    bsr = add("Dushyant Joshi",  "Manager - BSR & Logistics", "BSR & Logistics",   prod, date(2017, 8, 1))
    add("Sachin Jaiswal",        "Senior Manager",            "BSR & Logistics",   bsr,  date(2019, 3, 1))
    add("Devendra Gehlot",       "Senior Manager - PPC",      "PPC",               prod, date(2016, 2, 1))
    add("Arvind Bhadoriya",      "Manager - Stores",          "Stores",            prod, date(2016, 6, 1))
    add("Neeraj Sharma",         "AGM - Maintenance",         "Maintenance",       prod, date(2014, 9, 1))

    # Under Sales
    exp  = add("Sankha Das",     "General Manager - Export",  "Export",            sales, date(2011, 5, 1), "Mumbai")
    add("Rajiv Singh Parihar",   "AGM - Export",              "Export",            exp,   date(2015, 9, 1), "Mumbai")
    srid = add("G. Sridhar",     "General Manager",           "Sales",             sales, date(2009, 3, 1), "Hyderabad")
    add("Srinivas Kanche",       "DGM - Sales",               "Sales",             srid,  date(2013, 7, 1), "Hyderabad")
    add("Anjali Lalappan",       "Sr. Executive - Digital Marketing", "Digital Marketing", sales, date(2020, 6, 1))
    add("Atul Mishra",           "Asst. Manager - Traded Goods",      "Traded Goods",      sales, date(2019, 11, 1))
    add("Manoj Sao",             "DGM - Reclamation Services",        "Reclamation Services", sales, date(2012, 8, 1))

    db.session.commit()


with app.app_context():
    db.create_all()
    seed_if_empty()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
