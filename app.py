from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime
from functools import wraps
import json
import os
import urllib.request
import urllib.error

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from database import init_db, get_db, APPROVED_HOSPITALS
from models import (
    add_donor as create_donor_record,
    add_patient as create_patient_record,
    update_donor, update_patient,
    delete_donor, delete_patient,
    log_audit,
    get_all_donors, get_all_patients,
    get_donors_by_hospital, get_patients_by_hospital,
    get_donor_by_id, get_patient_by_id, get_matches, search_by_id,
    calculate_age, calculate_bmi, calculate_distance, calculate_match_score
)

# Load environment variables if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lifelink_default_secret_key_2024')

# Gemini AI configuration - Securely read from environment
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'models/gemini-2.0-flash-lite')
GEMINI_API_URL = f'https://generativelanguage.googleapis.com/v1beta/{GEMINI_MODEL}:generateContent'

# Initialize database on first run
try:
    init_db()
except Exception as db_error:
    print(f"[LifeLink] Skipping DB init: {db_error}")


def login_required(view_fn):
    """Route decorator to enforce authenticated access."""
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        if 'hospital_id' not in session:
            flash('Please login to continue.', 'warning')
            return redirect(url_for('login'))
        return view_fn(*args, **kwargs)
    return wrapper


@app.before_request
def persist_session():
    """Ensure session lasts across the browsing session."""
    session.permanent = True


def sanitize(value: str) -> str:
    return value.strip() if isinstance(value, str) else ''


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def post_json(url, payload, timeout=10):
    """Send JSON POST with requests if available, else urllib."""
    data = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    
    if REQUESTS_AVAILABLE:
        resp = requests.post(url, data=data, headers=headers, timeout=timeout)
        return resp.status_code, resp.text
    
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, body
    except urllib.error.HTTPError as http_err:
        body = http_err.read().decode('utf-8', errors='ignore')
        return http_err.code, body


def humanize_timestamp(value):
    """Convert database timestamps to human-readable deltas."""
    if not value:
        return 'Just now'
    if isinstance(value, datetime):
        event_time = value
    else:
        try:
            event_time = datetime.fromisoformat(str(value))
        except ValueError:
            try:
                event_time = datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return str(value)
    delta = datetime.utcnow() - event_time
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return 'Just now'
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr ago"
    if seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days > 1 else ''} ago"
    return event_time.strftime('%d %b %Y')


def format_activity_entry(row):
    """Map audit log rows to UI-friendly activity cards."""
    change_data = {}
    if row.get('changes'):
        try:
            change_data = json.loads(row['changes'])
        except (json.JSONDecodeError, TypeError):
            change_data = {}
    entity = row.get('entity_type', '').lower()
    action = row.get('action_type', '').upper()
    entity_label = entity.capitalize() if entity else 'Record'
    
    title_lookup = {
        'CREATE': f'New {entity_label} added',
        'UPDATE': f'{entity_label} updated',
        'DELETE': f'{entity_label} status changed'
    }
    icon_lookup = {
        'patient': 'fas fa-user-injured',
        'donor': 'fas fa-hand-holding-heart',
        'match': 'fas fa-link'
    }
    variant_lookup = {
        'CREATE': 'success',
        'UPDATE': 'info',
        'DELETE': 'warning'
    }
    
    details = []
    for key in ('name', 'organ_type', 'organ_needed', 'status'):
        value = change_data.get(key)
        if value:
            details.append(str(value))
    subtitle = ' • '.join(details) if details else f"Reference: {row.get('entity_id', 'N/A')}"
    
    return {
        'title': title_lookup.get(action, 'Activity recorded'),
        'subtitle': subtitle,
        'meta': humanize_timestamp(row.get('created_at')),
        'icon': icon_lookup.get(entity, 'fas fa-info-circle'),
        'variant': variant_lookup.get(action, 'neutral')
    }

# ==================== LANDING & AUTH ====================

@app.route('/')
def index():
    if 'hospital_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html', current_year=date.today().year)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if 'hospital_id' in session:
        return redirect(url_for('dashboard'))

    context = {'approved_hospitals': APPROVED_HOSPITALS}

    if request.method == 'POST':
        license_number = request.form.get('license_number', '').strip().upper()
        user_hospital_name = sanitize(request.form.get('hospital_name'))
        mapped_name = APPROVED_HOSPITALS.get(license_number)
        hospital_name = mapped_name or user_hospital_name

        if not hospital_name:
            flash('Please provide your hospital name.', 'danger')
            return render_template('signup.html', license_number=license_number, **context)

        # Get all form data
        data = {
            'hospital_name': hospital_name,
            'license_number': license_number,
            'location_city': sanitize(request.form.get('location_city')),
            'location_state': sanitize(request.form.get('location_state')),
            'hospital_type': sanitize(request.form.get('hospital_type')),
            'admin_name': sanitize(request.form.get('admin_name')),
            'admin_designation': sanitize(request.form.get('admin_designation')),
            'contact_phone': sanitize(request.form.get('contact_phone')),
            'contact_email': sanitize(request.form.get('contact_email')),
            'username': sanitize(request.form.get('username')),
            'password': request.form.get('password', '')
        }
        
        # Validate email domain
        email_domain = data['contact_email'].split('@')[-1]
        public_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
        if email_domain in public_domains:
            flash('Please use official hospital email (not personal email)', 'danger')
            return render_template('signup.html', **data, **context)
        
        # Check username uniqueness
        conn = get_db()
        existing = conn.execute('SELECT id FROM hospitals WHERE username = ?', 
                               (data['username'],)).fetchone()
        if existing:
            conn.close()
            flash('Username already taken', 'danger')
            return render_template('signup.html', **data, **context)
        
        # Create hospital account
        try:
            password_hash = generate_password_hash(data['password'])
            conn.execute('''
                INSERT INTO hospitals (hospital_name, license_number, location_city, 
                                     location_state, hospital_type, admin_name, 
                                     admin_designation, contact_phone, contact_email, 
                                     username, password)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data['hospital_name'], data['license_number'], data['location_city'],
                  data['location_state'], data['hospital_type'], data['admin_name'],
                  data['admin_designation'], data['contact_phone'], data['contact_email'],
                  data['username'], password_hash))
            conn.commit()
            conn.close()
            
            success_message = 'Hospital registered successfully! Please login.'
            if not mapped_name:
                success_message = 'Hospital submitted for onboarding. Our team will verify the license and notify you shortly.'
            flash(success_message, 'success')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Registration failed: {str(e)}', 'danger')
            return render_template('signup.html', **data, **context)
    
    return render_template('signup.html', **context)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'hospital_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Please enter both username and password', 'danger')
            return render_template('login.html')
        
        conn = get_db()
        hospital = conn.execute(
            'SELECT * FROM hospitals WHERE username = ?', (username,)
        ).fetchone()
        conn.close()
        
        if hospital and check_password_hash(hospital['password'], password):
            session['hospital_id'] = hospital['id']
            session['hospital_name'] = hospital['hospital_name']
            session['location_city'] = hospital['location_city']
            flash(f'Welcome back, {hospital["hospital_name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    # Clear theme preference to ensure landing page is always light
    flash('Logged out successfully', 'info')
    response = redirect(url_for('index'))
    # Set a cookie to clear theme on landing page
    response.set_cookie('clear_theme', 'true', max_age=1)
    return response

# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    
    # Get statistics
    my_donors = conn.execute(
        'SELECT COUNT(*) as count FROM donors WHERE hospital_id = ? AND status = "active"',
        (session['hospital_id'],)
    ).fetchone()['count']
    
    my_patients = conn.execute(
        'SELECT COUNT(*) as count FROM patients WHERE hospital_id = ? AND status = "active"',
        (session['hospital_id'],)
    ).fetchone()['count']
    
    network_donors = conn.execute(
        'SELECT COUNT(*) as count FROM donors WHERE hospital_id != ? AND status = "active"',
        (session['hospital_id'],)
    ).fetchone()['count']
    
    network_patients = conn.execute(
        'SELECT COUNT(*) as count FROM patients WHERE hospital_id != ? AND status = "active"',
        (session['hospital_id'],)
    ).fetchone()['count']
    
    # Get urgent patients
    urgent_patients = conn.execute('''
        SELECT patient_id, name, organ_needed, urgency_score
        FROM patients
        WHERE hospital_id = ? AND status = "active" AND urgency_score >= 80
        ORDER BY urgency_score DESC
        LIMIT 5
    ''', (session['hospital_id'],)).fetchall()
    
    # Analytics: Organ distribution
    organ_distribution = conn.execute('''
        SELECT organ_needed, COUNT(*) as count
        FROM patients
        WHERE hospital_id = ? AND status = "active"
        GROUP BY organ_needed
        ORDER BY count DESC
    ''', (session['hospital_id'],)).fetchall()
    
    # Analytics: Critical patients count
    critical_patients = conn.execute('''
        SELECT COUNT(*) as count
        FROM patients
        WHERE hospital_id = ? AND status = "active" AND urgency_score >= 90
    ''', (session['hospital_id'],)).fetchone()['count']

    # AI Match insights (computed on the fly)
    all_matches = get_matches()
    hospital_matches = [m for m in all_matches if m['patient']['hospital_id'] == session['hospital_id']]
    total_matches = len(hospital_matches)
    avg_score = round(sum(match['score'] for match in hospital_matches) / total_matches, 1) if total_matches else 0
    recent_matches = hospital_matches[:3]

    # Recent activity feed sourced from audit logs
    activity_rows = conn.execute('''
        SELECT action_type, entity_type, entity_id, changes, created_at
        FROM audit_logs
        WHERE hospital_id = ?
        ORDER BY created_at DESC
        LIMIT 8
    ''', (session['hospital_id'],)).fetchall()
    
    recent_activity = [format_activity_entry(dict(row)) for row in activity_rows]
    
    if urgent_patients:
        urgent = urgent_patients[0]
        recent_activity.append({
            'title': 'Urgent patient flagged',
            'subtitle': f"{urgent['patient_id']} • {urgent['name']} • Urgency {urgent['urgency_score']}%",
            'meta': 'Live',
            'icon': 'fas fa-bell',
            'variant': 'danger'
        })
    
    recent_activity.append({
        'title': 'Matching engine refreshed',
        'subtitle': f"{total_matches} total matches • Avg score {avg_score}%",
        'meta': 'Just now',
        'icon': 'fas fa-sync',
        'variant': 'primary'
    })
    
    recent_activity.append({
        'title': 'Dashboard updated',
        'subtitle': f"{my_patients} patients • {my_donors} donors in your care",
        'meta': 'Just now',
        'icon': 'fas fa-chart-line',
        'variant': 'neutral'
    })
    
    recent_activity = recent_activity[:5]
    
    conn.close()
    
    return render_template('dashboard.html',
                         my_donors=my_donors,
                         my_patients=my_patients,
                         network_donors=network_donors,
                         network_patients=network_patients,
                         urgent_patients=urgent_patients,
                         organ_distribution=organ_distribution,
                         avg_match_score=avg_score,
                         total_matches=total_matches,
                         critical_patients=critical_patients,
                         recent_matches=recent_matches,
                         recent_activity=recent_activity)

# ==================== MY DONORS ====================

@app.route('/my-donors')
@login_required
def my_donors():
    donors = get_donors_by_hospital(session['hospital_id'])
    return render_template('my_donors.html', donors=donors)

@app.route('/add-donor', methods=['GET', 'POST'])
@login_required
def add_donor():
    if request.method == 'POST':
        try:
            # Basic info
            data = {
                'name': sanitize(request.form.get('name')),
                'dob': request.form.get('dob'),
                'gender': sanitize(request.form.get('gender')),
                'blood_group': sanitize(request.form.get('blood_group')),
                'contact': sanitize(request.form.get('contact')),
                'location': sanitize(request.form.get('location')),
                'weight_kg': safe_float(request.form.get('weight_kg')),
                'height_cm': safe_float(request.form.get('height_cm')),
                'organ_type': sanitize(request.form.get('organ_type')),
                'doctor_assigned': sanitize(request.form.get('doctor_assigned')),
                'medical_history': request.form.getlist('medical_history'),
                'death_date': request.form.get('death_date') or None
            }
            
            # Organ-specific metrics
            organ_metrics = {}
            organ_type = data['organ_type']
            if organ_type != 'Heart':
                data['death_date'] = None
            
            if organ_type == 'Kidney':
                organ_metrics = {
                    'hla_typing': {
                        'hla_a': [request.form.get('hla_a1'), request.form.get('hla_a2')],
                        'hla_b': [request.form.get('hla_b1'), request.form.get('hla_b2')],
                        'hla_dr': [request.form.get('hla_dr1'), request.form.get('hla_dr2')]
                    },
                    'serum_creatinine': safe_float(request.form.get('serum_creatinine')),
                    'kidney_function': safe_int(request.form.get('kidney_function'))
                }
            elif organ_type == 'Liver':
                organ_metrics = {
                    'alt': safe_int(request.form.get('alt')),
                    'ast': safe_int(request.form.get('ast')),
                    'liver_condition': sanitize(request.form.get('liver_condition'))
                }
            elif organ_type == 'Heart':
                organ_metrics = {
                    'ejection_fraction': safe_int(request.form.get('ejection_fraction')),
                    'heart_condition': sanitize(request.form.get('heart_condition'))
                }
            elif organ_type == 'Pancreas':
                organ_metrics = {
                    'pancreas_function': safe_int(request.form.get('pancreas_function'), 0),
                    'c_peptide_level': safe_float(request.form.get('c_peptide_level'), 0),
                    'islet_cell_viability': safe_int(request.form.get('islet_cell_viability'), 0)
                }
            elif organ_type == 'Lung':
                organ_metrics = {
                    'fev1_score': safe_int(request.form.get('fev1_score'), 0),
                    'smoking_history': sanitize(request.form.get('smoking_history')),
                    'chest_xray_status': sanitize(request.form.get('chest_xray_status'))
                }
            
            data['organ_metrics'] = organ_metrics
            
            donor_id = create_donor_record(data, session['hospital_id'])
            log_audit(session['hospital_id'], 'CREATE', 'donor', donor_id, 
                     {'name': data['name'], 'organ_type': data['organ_type']},
                     {'hospital': session.get('hospital_name')})
            flash(f'Donor registered successfully! ID: {donor_id}', 'success')
            return redirect(url_for('my_donors'))
        except Exception as e:
            flash(f'Error adding donor: {str(e)}', 'danger')
    
    return render_template('add_donor.html', today=date.today().isoformat())

@app.route('/donor/<donor_id>')
@login_required
def donor_detail(donor_id):
    donor = get_donor_by_id(donor_id)
    if not donor:
        flash('Donor not found', 'danger')
        return redirect(url_for('my_donors'))
    
    # Parse JSON fields
    try:
        organ_metrics = json.loads(donor['organ_metrics']) if donor['organ_metrics'] else {}
        medical_history = json.loads(donor['medical_history']) if donor['medical_history'] else []
    except:
        organ_metrics = {}
        medical_history = []
    
    age = calculate_age(donor['dob'])
    bmi = calculate_bmi(donor['weight_kg'], donor['height_cm'])
    
    return render_template('donor_detail.html',
                         donor=donor,
                         organ_metrics=organ_metrics,
                         medical_history=medical_history,
                         age=age,
                         bmi=bmi)

@app.route('/edit-donor/<donor_id>', methods=['GET', 'POST'])
@login_required
def edit_donor(donor_id):
    donor = get_donor_by_id(donor_id)
    if not donor or donor['hospital_id'] != session['hospital_id']:
        flash('Donor not found or access denied', 'danger')
        return redirect(url_for('my_donors'))
    
    if request.method == 'POST':
        try:
            data = {
                'name': sanitize(request.form.get('name')),
                'dob': request.form.get('dob'),
                'gender': sanitize(request.form.get('gender')),
                'blood_group': sanitize(request.form.get('blood_group')),
                'contact': sanitize(request.form.get('contact')),
                'location': sanitize(request.form.get('location')),
                'weight_kg': safe_float(request.form.get('weight_kg')),
                'height_cm': safe_float(request.form.get('height_cm')),
                'organ_type': sanitize(request.form.get('organ_type')),
                'doctor_assigned': sanitize(request.form.get('doctor_assigned')),
                'medical_history': request.form.getlist('medical_history'),
                'death_date': request.form.get('death_date') or None
            }
            
            organ_metrics = {}
            organ_type = data['organ_type']
            if organ_type != 'Heart':
                data['death_date'] = None
            
            if organ_type == 'Kidney':
                organ_metrics = {
                    'hla_typing': {
                        'hla_a': [request.form.get('hla_a1'), request.form.get('hla_a2')],
                        'hla_b': [request.form.get('hla_b1'), request.form.get('hla_b2')],
                        'hla_dr': [request.form.get('hla_dr1'), request.form.get('hla_dr2')]
                    },
                    'serum_creatinine': safe_float(request.form.get('serum_creatinine')),
                    'kidney_function': safe_int(request.form.get('kidney_function'))
                }
            elif organ_type == 'Liver':
                organ_metrics = {
                    'alt': safe_int(request.form.get('alt')),
                    'ast': safe_int(request.form.get('ast')),
                    'liver_condition': sanitize(request.form.get('liver_condition'))
                }
            elif organ_type == 'Heart':
                organ_metrics = {
                    'ejection_fraction': safe_int(request.form.get('ejection_fraction')),
                    'heart_condition': sanitize(request.form.get('heart_condition'))
                }
            elif organ_type == 'Pancreas':
                organ_metrics = {
                    'pancreas_function': safe_int(request.form.get('pancreas_function'), 0),
                    'c_peptide_level': safe_float(request.form.get('c_peptide_level'), 0),
                    'islet_cell_viability': safe_int(request.form.get('islet_cell_viability'), 0)
                }
            elif organ_type == 'Lung':
                organ_metrics = {
                    'fev1_score': safe_int(request.form.get('fev1_score'), 0),
                    'smoking_history': sanitize(request.form.get('smoking_history')),
                    'chest_xray_status': sanitize(request.form.get('chest_xray_status'))
                }
            
            data['organ_metrics'] = organ_metrics
            update_donor(donor_id, data, session['hospital_id'])
            log_audit(session['hospital_id'], 'UPDATE', 'donor', donor_id,
                     {'changes': 'Donor information updated'}, {'hospital': session.get('hospital_name')})
            flash('Donor updated successfully!', 'success')
            return redirect(url_for('donor_detail', donor_id=donor_id))
        except Exception as e:
            flash(f'Error updating donor: {str(e)}', 'danger')
    
    try:
        organ_metrics = json.loads(donor['organ_metrics']) if donor['organ_metrics'] else {}
        medical_history = json.loads(donor['medical_history']) if donor['medical_history'] else []
    except:
        organ_metrics = {}
        medical_history = []
    
    return render_template('edit_donor.html', donor=donor, organ_metrics=organ_metrics,
                         medical_history=medical_history, today=date.today().isoformat())

@app.route('/delete-donor/<donor_id>', methods=['POST'])
@login_required
def delete_donor_route(donor_id):
    donor = get_donor_by_id(donor_id)
    if not donor or donor['hospital_id'] != session['hospital_id']:
        flash('Donor not found or access denied', 'danger')
        return redirect(url_for('my_donors'))
    
    status = request.form.get('status', 'inactive')
    delete_donor(donor_id, session['hospital_id'], status)
    log_audit(session['hospital_id'], 'DELETE', 'donor', donor_id,
             {'status': status}, {'hospital': session.get('hospital_name')})
    flash(f'Donor marked as {status}', 'success')
    return redirect(url_for('my_donors'))

# ==================== MY PATIENTS ====================

@app.route('/my-patients')
@login_required
def my_patients():
    patients = get_patients_by_hospital(session['hospital_id'])
    return render_template('my_patients.html', patients=patients)

@app.route('/add-patient', methods=['GET', 'POST'])
@login_required
def add_patient():
    if request.method == 'POST':
        try:
            data = {
                'name': sanitize(request.form.get('name')),
                'dob': request.form.get('dob'),
                'gender': sanitize(request.form.get('gender')),
                'blood_group': sanitize(request.form.get('blood_group')),
                'contact': sanitize(request.form.get('contact')),
                'location': sanitize(request.form.get('location')),
                'weight_kg': safe_float(request.form.get('weight_kg')),
                'height_cm': safe_float(request.form.get('height_cm')),
                'organ_needed': sanitize(request.form.get('organ_needed')),
                'urgency_score': safe_int(request.form.get('urgency_score'), 0),
                'doctor_assigned': sanitize(request.form.get('doctor_assigned')),
                'medical_history': request.form.getlist('medical_history')
            }
            
            # Organ-specific metrics
            organ_metrics = {}
            organ_type = data['organ_needed']
            
            if organ_type == 'Kidney':
                organ_metrics = {
                    'hla_typing': {
                        'hla_a': [request.form.get('hla_a1'), request.form.get('hla_a2')],
                        'hla_b': [request.form.get('hla_b1'), request.form.get('hla_b2')],
                        'hla_dr': [request.form.get('hla_dr1'), request.form.get('hla_dr2')]
                    },
                    'dialysis_status': sanitize(request.form.get('dialysis_status')),
                    'dialysis_duration_months': safe_int(request.form.get('dialysis_duration_months'), 0)
                }
            elif organ_type == 'Liver':
                organ_metrics = {
                    'meld_score': safe_int(request.form.get('meld_score'), 10),
                    'diagnosis': sanitize(request.form.get('diagnosis'))
                }
            elif organ_type == 'Heart':
                organ_metrics = {
                    'ejection_fraction': safe_int(request.form.get('ejection_fraction')),
                    'unos_status': sanitize(request.form.get('unos_status'))
                }
            elif organ_type == 'Pancreas':
                organ_metrics = {
                    'diabetes_type': sanitize(request.form.get('diabetes_type')),
                    'insulin_dependency_years': safe_int(request.form.get('insulin_dependency_years'), 0),
                    'hba1c_level': safe_float(request.form.get('hba1c_level'), 0)
                }
            elif organ_type == 'Lung':
                organ_metrics = {
                    'diagnosis': sanitize(request.form.get('diagnosis')),
                    'oxygen_dependency': sanitize(request.form.get('oxygen_dependency')),
                    'six_minute_walk_test': safe_int(request.form.get('six_minute_walk_test'), 0)
                }
            
            data['organ_metrics'] = organ_metrics
            
            patient_id = create_patient_record(data, session['hospital_id'])
            log_audit(session['hospital_id'], 'CREATE', 'patient', patient_id,
                     {'name': data['name'], 'organ_needed': data['organ_needed']},
                     {'hospital': session.get('hospital_name')})
            flash(f'Patient registered successfully! ID: {patient_id}', 'success')
            return redirect(url_for('my_patients'))
        except Exception as e:
            flash(f'Error adding patient: {str(e)}', 'danger')
    
    return render_template('add_patient.html', today=date.today().isoformat())

@app.route('/patient/<patient_id>')
@login_required
def patient_detail(patient_id):
    patient = get_patient_by_id(patient_id)
    if not patient:
        flash('Patient not found', 'danger')
        return redirect(url_for('my_patients'))
    
    try:
        organ_metrics = json.loads(patient['organ_metrics']) if patient['organ_metrics'] else {}
        medical_history = json.loads(patient['medical_history']) if patient['medical_history'] else []
    except:
        organ_metrics = {}
        medical_history = []
    
    age = calculate_age(patient['dob'])
    bmi = calculate_bmi(patient['weight_kg'], patient['height_cm'])
    
    return render_template('patient_detail.html',
                         patient=patient,
                         organ_metrics=organ_metrics,
                         medical_history=medical_history,
                         age=age,
                         bmi=bmi)

@app.route('/edit-patient/<patient_id>', methods=['GET', 'POST'])
@login_required
def edit_patient(patient_id):
    patient = get_patient_by_id(patient_id)
    if not patient or patient['hospital_id'] != session['hospital_id']:
        flash('Patient not found or access denied', 'danger')
        return redirect(url_for('my_patients'))
    
    if request.method == 'POST':
        try:
            data = {
                'name': sanitize(request.form.get('name')),
                'dob': request.form.get('dob'),
                'gender': sanitize(request.form.get('gender')),
                'blood_group': sanitize(request.form.get('blood_group')),
                'contact': sanitize(request.form.get('contact')),
                'location': sanitize(request.form.get('location')),
                'weight_kg': safe_float(request.form.get('weight_kg')),
                'height_cm': safe_float(request.form.get('height_cm')),
                'organ_needed': sanitize(request.form.get('organ_needed')),
                'urgency_score': safe_int(request.form.get('urgency_score'), 0),
                'doctor_assigned': sanitize(request.form.get('doctor_assigned')),
                'medical_history': request.form.getlist('medical_history')
            }
            
            organ_metrics = {}
            organ_type = data['organ_needed']
            
            if organ_type == 'Kidney':
                organ_metrics = {
                    'hla_typing': {
                        'hla_a': [request.form.get('hla_a1'), request.form.get('hla_a2')],
                        'hla_b': [request.form.get('hla_b1'), request.form.get('hla_b2')],
                        'hla_dr': [request.form.get('hla_dr1'), request.form.get('hla_dr2')]
                    },
                    'dialysis_status': sanitize(request.form.get('dialysis_status')),
                    'dialysis_duration_months': safe_int(request.form.get('dialysis_duration_months'), 0)
                }
            elif organ_type == 'Liver':
                organ_metrics = {
                    'meld_score': safe_int(request.form.get('meld_score'), 10),
                    'diagnosis': sanitize(request.form.get('diagnosis'))
                }
            elif organ_type == 'Heart':
                organ_metrics = {
                    'ejection_fraction': safe_int(request.form.get('ejection_fraction')),
                    'unos_status': sanitize(request.form.get('unos_status'))
                }
            elif organ_type == 'Pancreas':
                organ_metrics = {
                    'diabetes_type': sanitize(request.form.get('diabetes_type')),
                    'insulin_dependency_years': safe_int(request.form.get('insulin_dependency_years'), 0),
                    'hba1c_level': safe_float(request.form.get('hba1c_level'), 0)
                }
            elif organ_type == 'Lung':
                organ_metrics = {
                    'diagnosis': sanitize(request.form.get('diagnosis')),
                    'oxygen_dependency': sanitize(request.form.get('oxygen_dependency')),
                    'six_minute_walk_test': safe_int(request.form.get('six_minute_walk_test'), 0)
                }
            
            data['organ_metrics'] = organ_metrics
            update_patient(patient_id, data, session['hospital_id'])
            log_audit(session['hospital_id'], 'UPDATE', 'patient', patient_id,
                     {'changes': 'Patient information updated'}, {'hospital': session.get('hospital_name')})
            flash('Patient updated successfully!', 'success')
            return redirect(url_for('patient_detail', patient_id=patient_id))
        except Exception as e:
            flash(f'Error updating patient: {str(e)}', 'danger')
    
    try:
        organ_metrics = json.loads(patient['organ_metrics']) if patient['organ_metrics'] else {}
        medical_history = json.loads(patient['medical_history']) if patient['medical_history'] else []
    except:
        organ_metrics = {}
        medical_history = []
    
    return render_template('edit_patient.html', patient=patient, organ_metrics=organ_metrics,
                         medical_history=medical_history, today=date.today().isoformat())

@app.route('/delete-patient/<patient_id>', methods=['POST'])
@login_required
def delete_patient_route(patient_id):
    patient = get_patient_by_id(patient_id)
    if not patient or patient['hospital_id'] != session['hospital_id']:
        flash('Patient not found or access denied', 'danger')
        return redirect(url_for('my_patients'))
    
    status = request.form.get('status', 'inactive')
    delete_patient(patient_id, session['hospital_id'], status)
    log_audit(session['hospital_id'], 'DELETE', 'patient', patient_id,
             {'status': status}, {'hospital': session.get('hospital_name')})
    flash(f'Patient marked as {status}', 'success')
    return redirect(url_for('my_patients'))

# ==================== NETWORK VIEWS ====================

@app.route('/all-donors')
@login_required
def all_donors():
    donors = get_all_donors()
    return render_template('all_donors.html', donors=donors, my_hospital_id=session['hospital_id'])

@app.route('/all-patients')
@login_required
def all_patients():
    patients = get_all_patients()
    return render_template('all_patients.html', patients=patients, my_hospital_id=session['hospital_id'])

# ==================== MATCHES ====================

@app.route('/matches')
@login_required
def matches():
    all_matches = get_matches()
    return render_template('matches.html', matches=all_matches)

@app.route('/match/<patient_id>/<donor_id>')
@login_required
def match_detail(patient_id, donor_id):
    """Display detailed match analysis between a patient and donor"""
    patient = get_patient_by_id(patient_id)
    donor = get_donor_by_id(donor_id)
    
    if not patient or not donor:
        flash('Match not found', 'danger')
        return redirect(url_for('matches'))
    
    score, reasons = calculate_match_score(patient, donor)
    
    patient_age = calculate_age(patient['dob'])
    patient_bmi = calculate_bmi(patient['weight_kg'], patient['height_cm'])
    donor_age = calculate_age(donor['dob'])
    donor_bmi = calculate_bmi(donor['weight_kg'], donor['height_cm'])
    
    # Calculate distance
    distance_km = calculate_distance(patient['location'], donor['location'])
    
    match_data = {
        'score': score,
        'reasons': reasons,
        'distance_km': distance_km
    }
    
    return render_template('match_detail.html',
                         patient=patient,
                         donor=donor,
                         match=match_data,
                         age=patient_age,
                         bmi=patient_bmi,
                         donor_age=donor_age,
                         donor_bmi=donor_bmi)

# ==================== SEARCH ====================

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip().upper()
    
    if not query:
        flash('Please enter a Patient ID or Donor ID', 'warning')
        return redirect(url_for('dashboard'))
    
    result = search_by_id(query)
    
    if not result:
        flash(f'No record found for ID: {query}', 'warning')
        return redirect(url_for('dashboard'))
    
    if result['type'] == 'patient':
        return redirect(url_for('patient_detail', patient_id=query))
    else:
        return redirect(url_for('donor_detail', donor_id=query))

@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/api/chat', methods=['POST'])
@login_required
def chat_with_rem():
    """REM chatbot endpoint - processes natural language queries with Gemini AI"""
    try:
        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({'response': 'Please enter a message.', 'error': True}), 400
        
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return jsonify({'response': 'Please enter a question.', 'error': True}), 400
        
        conn = get_db()
        
        # Get database context for Gemini
        db_context = get_database_context(conn, session['hospital_id'])
        
        # Try Gemini AI first if available, fallback to pattern matching
        if GEMINI_API_KEY:
            try:
                response = query_with_gemini(user_message, db_context)
            except Exception as ai_error:
                # Fallback to pattern matching
                response = process_rem_query(user_message.lower(), conn, session['hospital_id'])
        else:
            # Use pattern matching if Gemini not configured
            response = process_rem_query(user_message.lower(), conn, session['hospital_id'])
        
        conn.close()
        
        return jsonify({'response': response, 'error': False}), 200
    
    except Exception as e:
        return jsonify({'response': f'Error: {str(e)}', 'error': True}), 500

def get_database_context(conn, hospital_id):
    """Get database context for Gemini AI"""
    stats = {
        'my_donors': conn.execute(
            'SELECT COUNT(*) as count FROM donors WHERE hospital_id = ? AND status = "active"',
            (hospital_id,)
        ).fetchone()['count'],
        'my_patients': conn.execute(
            'SELECT COUNT(*) as count FROM patients WHERE hospital_id = ? AND status = "active"',
            (hospital_id,)
        ).fetchone()['count'],
        'critical_patients': conn.execute(
            'SELECT COUNT(*) as count FROM patients WHERE hospital_id = ? AND urgency_score >= 80 AND status = "active"',
            (hospital_id,)
        ).fetchone()['count'],
    }
    
    urgent_patients = conn.execute('''
        SELECT patient_id, name, organ_needed, blood_group, location, urgency_score
        FROM patients
        WHERE hospital_id = ? AND status = "active"
        ORDER BY urgency_score DESC
        LIMIT 5
    ''', (hospital_id,)).fetchall()
    
    recent_donors = conn.execute('''
        SELECT donor_id, name, organ_type, blood_group, location
        FROM donors
        WHERE hospital_id = ? AND status = "active"
        ORDER BY created_at DESC
        LIMIT 5
    ''', (hospital_id,)).fetchall()
    
    stats['urgent_patients_detail'] = [dict(row) for row in urgent_patients]
    stats['recent_donors'] = [dict(row) for row in recent_donors]
    
    all_matches = get_matches()
    hospital_matches = [m for m in all_matches if m['patient']['hospital_id'] == hospital_id]
    stats['total_matches'] = len(hospital_matches)
    stats['avg_match_score'] = round(sum(match['score'] for match in hospital_matches) / stats['total_matches'], 1) if stats['total_matches'] else 0
    stats['recent_matches'] = [{
        'patient_id': match['patient']['patient_id'],
        'patient_name': match['patient']['name'],
        'donor_id': match['donor']['donor_id'],
        'donor_name': match['donor']['name'],
        'score': match['score'],
        'organ': match['patient']['organ_needed']
    } for match in hospital_matches[:5]]
    
    return json.dumps(stats, default=str)

def query_with_gemini(user_message, db_context):
    """Query Gemini AI with database context"""
    prompt = f"""You are REM (Resource & Emergency Matching), an AI assistant for LifeLink organ matching platform.

Database Context:
{db_context}

User Query: {user_message}

Provide a helpful, concise response about the organ matching database. Always prioritize directly answering the user’s specific question with evidence from the context before giving any general summary. Reference actual patient/donor IDs and metrics when relevant.

Keep tone professional and medical-appropriate.

Formatting rules (strict):
- Start each section on a new line using plain text labels ending with a colon (e.g., `Overview:`).
- Use simple `-` bullet points; do not use *, bold, italics, or emojis.
- Order sections as: Overview, Critical Patients, Donors, Matches, Next Actions (skip sections without data).
- Bullet sentences must be compact and avoid repeating data in multiple sections.
- If a question cannot be answered from the provided context, explicitly state that in the Overview section."""
    
    try:
        status_code, raw_body = post_json(
            f'{GEMINI_API_URL}?key={GEMINI_API_KEY}',
            {
                'contents': [{
                    'parts': [{'text': prompt}]
                }]
            },
            timeout=10
        )
        
        if status_code != 200:
            raise RuntimeError(f'Gemini HTTP {status_code}: {raw_body[:200]}')
        
        result = json.loads(raw_body)
        candidates = result.get('candidates') or []
        first = candidates[0] if candidates else None
        parts = first.get('content', {}).get('parts', []) if first else []
        text = parts[0].get('text') if parts else None
        if not text:
            raise RuntimeError('Gemini returned empty response')
        return text
    
    except Exception as e:
        raise Exception(f"Gemini API error: {str(e)}")

def process_rem_query(message, conn, hospital_id):
    """Process REM query with improved pattern matching"""
    
    def get_critical_patients(conn, message):
        """Get critical patients, optionally filtered by location"""
        location_keywords = ['mumbai', 'delhi', 'bangalore', 'chennai', 'kolkata', 'hyderabad', 'pune', 'ahmedabad']
        location_filter = None
        for loc in location_keywords:
            if loc in message:
                location_filter = loc.capitalize()
                break
        
        if location_filter:
            patients = conn.execute('''
                SELECT name, patient_id, urgency_score, organ_needed, location
                FROM patients
                WHERE location LIKE ? AND urgency_score >= 80 AND status = "active"
                ORDER BY urgency_score DESC
                LIMIT 5
            ''', (f'%{location_filter}%',)).fetchall()
            if patients:
                response = f"Found {len(patients)} critical patients in {location_filter}:\n"
                for p in patients:
                    response += f"• {p['name']} ({p['patient_id']}) - {p['organ_needed']} - Urgency: {p['urgency_score']}%\n"
                return response
            else:
                return f"No critical patients found in {location_filter} currently."
        else:
            patients = conn.execute('''
                SELECT COUNT(*) as count FROM patients
                WHERE urgency_score >= 80 AND status = "active"
            ''').fetchone()
            return f"There are {patients['count']} critical patients (urgency ≥ 80) in the network."
    
    def get_donor_info(conn, message):
        """Get donor information based on organ type, blood group, or location"""
        organ_types = ['kidney', 'liver', 'heart', 'lung', 'pancreas']
        blood_groups = ['o+', 'o-', 'a+', 'a-', 'b+', 'b-', 'ab+', 'ab-']
        location_keywords = ['mumbai', 'delhi', 'bangalore', 'maharashtra', 'karnataka', 'tamil nadu']
        
        organ_filter = None
        blood_filter = None
        location_filter = None
        
        for org in organ_types:
            if org in message:
                organ_filter = org.capitalize()
                break
        
        for bg in blood_groups:
            if bg in message:
                blood_filter = bg.upper()
                break
        
        for loc in location_keywords:
            if loc in message:
                location_filter = loc
                break
        
        query = 'SELECT name, donor_id, blood_group, organ_type, location FROM donors WHERE status = "active"'
        params = []
        
        if organ_filter:
            query += ' AND organ_type = ?'
            params.append(organ_filter)
        
        if blood_filter:
            query += ' AND blood_group = ?'
            params.append(blood_filter)
        
        if location_filter:
            query += ' AND location LIKE ?'
            params.append(f'%{location_filter}%')
        
        query += ' LIMIT 5'
        
        donors = conn.execute(query, tuple(params)).fetchall()
        if donors:
            response = f"Found {len(donors)} donor(s):\n"
            for d in donors:
                response += f"• {d['name']} ({d['donor_id']}) - {d['blood_group']} {d['organ_type']} - {d['location']}\n"
            return response
        else:
            filters = []
            if organ_filter:
                filters.append(f"organ type {organ_filter}")
            if blood_filter:
                filters.append(f"blood group {blood_filter}")
            if location_filter:
                filters.append(f"location {location_filter}")
            filter_str = " with " + " and ".join(filters) if filters else ""
            return f"No donors found{filter_str} currently."
    
    def get_match_stats(conn, hospital_id):
        """Get match statistics"""
        result = conn.execute('''
            SELECT AVG(score) as avg_score, COUNT(*) as count
            FROM matches m
            JOIN patients p ON m.patient_id = p.patient_id
            WHERE p.hospital_id = ?
        ''', (hospital_id,)).fetchone()
        
        if result and result['avg_score']:
            return f"Your hospital has {result['count']} matches with an average score of {result['avg_score']:.1f}%."
        else:
            return "No matches found yet. Add patients and donors to generate matches."
    
    def get_organ_donors(conn, organ_type, message):
        """Get donors for specific organ type"""
        donors = conn.execute('''
            SELECT name, donor_id, blood_group, location
            FROM donors
            WHERE organ_type = ? AND status = "active"
            LIMIT 5
        ''', (organ_type,)).fetchall()
        
        if donors:
            response = f"Found {len(donors)} {organ_type} donor(s):\n"
            for d in donors:
                response += f"• {d['name']} ({d['donor_id']}) - {d['blood_group']} - {d['location']}\n"
            return response
        else:
            return f"No {organ_type} donors available currently."
    
    def get_help_text():
        """Return help text"""
        return """I can help you with:
• Finding critical/urgent patients (e.g., "Show critical patients in Mumbai")
• Searching for donors by organ type, blood group, or location (e.g., "Find kidney donors with O+ blood")
• Checking match scores and statistics (e.g., "What's my match statistics?")
• Answering questions about the database

Try asking:
- "Show critical patients"
- "Find kidney donors"
- "Match statistics"
- "Help"
"""
    
    # Pattern matching
    patterns = {
        'critical': lambda: get_critical_patients(conn, message),
        'urgent': lambda: get_critical_patients(conn, message),
        'donor': lambda: get_donor_info(conn, message),
        'match': lambda: get_match_stats(conn, hospital_id),
        'help': lambda: get_help_text(),
        'kidney': lambda: get_organ_donors(conn, 'Kidney', message),
        'liver': lambda: get_organ_donors(conn, 'Liver', message),
        'heart': lambda: get_organ_donors(conn, 'Heart', message),
        'lung': lambda: get_organ_donors(conn, 'Lung', message),
        'pancreas': lambda: get_organ_donors(conn, 'Pancreas', message),
    }
    
    for keyword, handler in patterns.items():
        if keyword in message:
            return handler()
    
    return f"I understand you're asking about '{message}'. Try: 'Show critical patients', 'Find kidney donors', 'Match statistics', or type 'help'."

# ==================== EXPORT ====================

@app.route('/export/<data_type>')
@login_required
def export_data(data_type):
    """Export data as CSV"""
    import csv
    from io import StringIO
    from datetime import datetime
    
    conn = get_db()
    
    try:
        if data_type == 'donors':
            donors = conn.execute('''
                SELECT donor_id, name, dob, gender, blood_group, organ_type, 
                       location, weight_kg, height_cm, status, created_at
                FROM donors WHERE hospital_id = ?
            ''', (session['hospital_id'],)).fetchall()
            
            si = StringIO()
            writer = csv.writer(si)
            writer.writerow(['Donor ID', 'Name', 'DOB', 'Gender', 'Blood Group', 
                            'Organ Type', 'Location', 'Weight (kg)', 'Height (cm)', 
                            'Status', 'Created At'])
            for d in donors:
                writer.writerow([d['donor_id'], d['name'], d['dob'], d['gender'], 
                               d['blood_group'], d['organ_type'], d['location'],
                               d['weight_kg'], d['height_cm'], d['status'], d['created_at']])
            
            output = make_response(si.getvalue())
            output.headers["Content-Disposition"] = f"attachment; filename=donors_export_{datetime.now().strftime('%Y%m%d')}.csv"
            output.headers["Content-type"] = "text/csv"
            conn.close()
            return output
        
        elif data_type == 'patients':
            patients = conn.execute('''
                SELECT patient_id, name, dob, gender, blood_group, organ_needed, 
                       location, weight_kg, height_cm, urgency_score, status, created_at
                FROM patients WHERE hospital_id = ?
            ''', (session['hospital_id'],)).fetchall()
            
            si = StringIO()
            writer = csv.writer(si)
            writer.writerow(['Patient ID', 'Name', 'DOB', 'Gender', 'Blood Group', 
                            'Organ Needed', 'Location', 'Weight (kg)', 'Height (cm)', 
                            'Urgency Score', 'Status', 'Created At'])
            for p in patients:
                writer.writerow([p['patient_id'], p['name'], p['dob'], p['gender'], 
                               p['blood_group'], p['organ_needed'], p['location'],
                               p['weight_kg'], p['height_cm'], p['urgency_score'], 
                               p['status'], p['created_at']])
            
            output = make_response(si.getvalue())
            output.headers["Content-Disposition"] = f"attachment; filename=patients_export_{datetime.now().strftime('%Y%m%d')}.csv"
            output.headers["Content-type"] = "text/csv"
            conn.close()
            return output
        
        elif data_type == 'matches':
            matches = conn.execute('''
                SELECT m.score, p.patient_id, p.name as patient_name, p.organ_needed,
                       d.donor_id, d.name as donor_name, d.organ_type, d.location as donor_location
                FROM matches m
                JOIN patients p ON m.patient_id = p.patient_id
                JOIN donors d ON m.donor_id = d.donor_id
                WHERE p.hospital_id = ?
                ORDER BY m.score DESC
            ''', (session['hospital_id'],)).fetchall()
            
            si = StringIO()
            writer = csv.writer(si)
            writer.writerow(['Match Score', 'Patient ID', 'Patient Name', 'Organ Needed',
                            'Donor ID', 'Donor Name', 'Organ Type', 'Donor Location'])
            for m in matches:
                writer.writerow([m['score'], m['patient_id'], m['patient_name'], 
                               m['organ_needed'], m['donor_id'], m['donor_name'],
                               m['organ_type'], m['donor_location']])
            
            output = make_response(si.getvalue())
            output.headers["Content-Disposition"] = f"attachment; filename=matches_export_{datetime.now().strftime('%Y%m%d')}.csv"
            output.headers["Content-type"] = "text/csv"
            conn.close()
            return output
        
        elif data_type == 'all':
            # Export all data types in a zip-like structure (simplified: combined CSV)
            donors = conn.execute('''
                SELECT 'DONOR' as type, donor_id as id, name, organ_type as organ, 
                       blood_group, location, status, created_at
                FROM donors WHERE hospital_id = ?
            ''', (session['hospital_id'],)).fetchall()
            
            patients = conn.execute('''
                SELECT 'PATIENT' as type, patient_id as id, name, organ_needed as organ,
                       blood_group, location, status, created_at
                FROM patients WHERE hospital_id = ?
            ''', (session['hospital_id'],)).fetchall()
            
            si = StringIO()
            writer = csv.writer(si)
            writer.writerow(['Type', 'ID', 'Name', 'Organ', 'Blood Group', 'Location', 'Status', 'Created At'])
            for row in donors + patients:
                writer.writerow([row['type'], row['id'], row['name'], row['organ'],
                               row['blood_group'], row['location'], row['status'], row['created_at']])
            
            output = make_response(si.getvalue())
            output.headers["Content-Disposition"] = f"attachment; filename=all_data_export_{datetime.now().strftime('%Y%m%d')}.csv"
            output.headers["Content-type"] = "text/csv"
            conn.close()
            return output
        
        conn.close()
        return jsonify({'error': 'Invalid export type'}), 400
    
    except Exception as e:
        conn.close()
        return jsonify({'error': f'Export failed: {str(e)}'}), 500

# ==================== NOTIFICATIONS ====================

@app.route('/api/notifications')
@login_required
def get_notifications():
    """Get notifications for the current hospital"""
    conn = get_db()
    notifications = []
    
    try:
        # Check for high-score matches
        matches = get_matches()
        for m in matches[:5]:  # Top 5
            if m['score'] >= 80:
                # Check if this match involves the current hospital
                if m['patient']['hospital_id'] == session['hospital_id'] or \
                   (m.get('donor') and m['donor'].get('hospital_id') == session['hospital_id']):
                    notifications.append({
                        'id': f"match-{m['patient']['patient_id']}-{m['donor']['donor_id']}",
                        'title': f'High match found: {m["patient"]["name"]} ← {m["donor"]["name"]}',
                        'time': 'Just now',
                        'link': f'/match/{m["patient"]["patient_id"]}/{m["donor"]["donor_id"]}'
                    })
        
        # Check for critical patients in network
        critical = conn.execute('''
            SELECT patient_id, name, urgency_score 
            FROM patients 
            WHERE hospital_id != ? AND urgency_score >= 80 AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 3
        ''', (session['hospital_id'],)).fetchall()
        
        for p in critical:
            notifications.append({
                'id': f'critical-{p["patient_id"]}',
                'title': f'Critical patient added: {p["name"]} (Urgency: {p["urgency_score"]}%)',
                'time': '2 hours ago',
                'link': f'/patient/{p["patient_id"]}'
            })
        
        conn.close()
        return jsonify({'notifications': notifications})
    
    except Exception as e:
        conn.close()
        return jsonify({'notifications': [], 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
