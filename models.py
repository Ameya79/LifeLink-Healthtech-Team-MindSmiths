import sqlite3
import json
import os
from datetime import datetime
from math import radians, cos, sin, asin, sqrt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.path.dirname(BASE_DIR), 'lifelink.db')

# Approximate coordinates for major Indian cities (lat, lon)
CITY_COORDINATES = {
    'mumbai': (19.0760, 72.8777),
    'new delhi': (28.6139, 77.2090),
    'delhi': (28.7041, 77.1025),
    'bangalore': (12.9716, 77.5946),
    'bengaluru': (12.9716, 77.5946),
    'chennai': (13.0827, 80.2707),
    'hyderabad': (17.3850, 78.4867),
    'pune': (18.5204, 73.8567),
    'kolkata': (22.5726, 88.3639),
    'ahmedabad': (23.0225, 72.5714),
    'jaipur': (26.9124, 75.7873),
    'indore': (22.7196, 75.8577),
    'kochi': (9.9312, 76.2673),
    'coimbatore': (11.0168, 76.9558)
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def generate_unique_id(prefix, hospital_id):
    """Generate unique ID like PT-001-2024-001 or DN-001-2024-001"""
    conn = get_db()
    year = datetime.now().year
    
    if prefix == 'PT':
        count = conn.execute('SELECT COUNT(*) as count FROM patients WHERE hospital_id = ?', (hospital_id,)).fetchone()['count']
    else:
        count = conn.execute('SELECT COUNT(*) as count FROM donors WHERE hospital_id = ?', (hospital_id,)).fetchone()['count']
    
    conn.close()
    return f"{prefix}-{hospital_id:03d}-{year}-{count+1:03d}"

def calculate_age(dob_str):
    """Calculate age from date of birth"""
    if not dob_str or not isinstance(dob_str, str):
        return 0
    try:
        dob = datetime.strptime(dob_str.strip(), '%Y-%m-%d')
        today = datetime.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except (ValueError, TypeError):
        return 0

def calculate_bmi(weight_kg, height_cm):
    """Calculate BMI"""
    try:
        height_m = height_cm / 100
        if height_m <= 0:
            return 0
        return round(weight_kg / (height_m ** 2), 1)
    except (TypeError, ZeroDivisionError):
        return 0

# ====================
# DONOR FUNCTIONS
# ====================

def add_donor(data, hospital_id):
    """Add new donor to database"""
    conn = get_db()
    donor_id = generate_unique_id('DN', hospital_id)
    
    organ_metrics = json.dumps(data.get('organ_metrics', {}))
    medical_history = json.dumps(data.get('medical_history', []))
    
    conn.execute('''
        INSERT INTO donors (donor_id, name, dob, gender, blood_group, contact, location,
                          weight_kg, height_cm, organ_type, organ_metrics, medical_history,
                          death_date, hospital_id, doctor_assigned)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (donor_id, data['name'], data['dob'], data['gender'], data['blood_group'],
          data['contact'], data['location'], data['weight_kg'], data['height_cm'],
          data['organ_type'], organ_metrics, medical_history, data.get('death_date'),
          hospital_id, data['doctor_assigned']))
    
    conn.commit()
    conn.close()
    return donor_id

def get_all_donors():
    """Get all donors with hospital info"""
    conn = get_db()
    donors = conn.execute('''
        SELECT d.*, h.hospital_name, h.location_city
        FROM donors d
        JOIN hospitals h ON d.hospital_id = h.id
        WHERE d.status = 'active'
        ORDER BY d.created_at DESC
    ''').fetchall()
    conn.close()
    return donors

def get_donors_by_hospital(hospital_id):
    """Get donors for specific hospital"""
    conn = get_db()
    donors = conn.execute('''
        SELECT d.*, h.hospital_name
        FROM donors d
        JOIN hospitals h ON d.hospital_id = h.id
        WHERE d.hospital_id = ? AND d.status = 'active'
        ORDER BY d.created_at DESC
    ''', (hospital_id,)).fetchall()
    conn.close()
    return donors

def get_donor_by_id(donor_id):
    """Get single donor details"""
    conn = get_db()
    donor = conn.execute('''
        SELECT d.*, h.hospital_name, h.location_city, h.contact_phone
        FROM donors d
        JOIN hospitals h ON d.hospital_id = h.id
        WHERE d.donor_id = ?
    ''', (donor_id,)).fetchone()
    conn.close()
    return donor

def update_donor(donor_id, data, hospital_id):
    """Update existing donor"""
    conn = get_db()
    organ_metrics = json.dumps(data.get('organ_metrics', {}))
    medical_history = json.dumps(data.get('medical_history', []))
    
    conn.execute('''
        UPDATE donors 
        SET name = ?, dob = ?, gender = ?, blood_group = ?, contact = ?, location = ?,
            weight_kg = ?, height_cm = ?, organ_type = ?, organ_metrics = ?, 
            medical_history = ?, death_date = ?, doctor_assigned = ?
        WHERE donor_id = ? AND hospital_id = ?
    ''', (data['name'], data['dob'], data['gender'], data['blood_group'], 
          data['contact'], data['location'], data['weight_kg'], data['height_cm'],
          data['organ_type'], organ_metrics, medical_history, data.get('death_date'),
          data['doctor_assigned'],
          donor_id, hospital_id))
    
    conn.commit()
    conn.close()
    return donor_id

def delete_donor(donor_id, hospital_id, status='inactive'):
    """Soft delete donor (set status)"""
    conn = get_db()
    conn.execute('''
        UPDATE donors SET status = ? WHERE donor_id = ? AND hospital_id = ?
    ''', (status, donor_id, hospital_id))
    conn.commit()
    conn.close()
    return True

# ====================
# PATIENT FUNCTIONS
# ====================

def add_patient(data, hospital_id):
    """Add new patient to database"""
    conn = get_db()
    patient_id = generate_unique_id('PT', hospital_id)
    
    organ_metrics = json.dumps(data.get('organ_metrics', {}))
    medical_history = json.dumps(data.get('medical_history', []))
    
    conn.execute('''
        INSERT INTO patients (patient_id, name, dob, gender, blood_group, contact, location,
                            weight_kg, height_cm, organ_needed, organ_metrics, medical_history,
                            urgency_score, hospital_id, doctor_assigned)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (patient_id, data['name'], data['dob'], data['gender'], data['blood_group'],
          data['contact'], data['location'], data['weight_kg'], data['height_cm'],
          data['organ_needed'], organ_metrics, medical_history, data['urgency_score'],
          hospital_id, data['doctor_assigned']))
    
    conn.commit()
    conn.close()
    return patient_id

def get_all_patients():
    """Get all patients with hospital info"""
    conn = get_db()
    patients = conn.execute('''
        SELECT p.*, h.hospital_name, h.location_city
        FROM patients p
        JOIN hospitals h ON p.hospital_id = h.id
        WHERE p.status = 'active'
        ORDER BY p.urgency_score DESC, p.created_at DESC
    ''').fetchall()
    conn.close()
    return patients

def get_patients_by_hospital(hospital_id):
    """Get patients for specific hospital"""
    conn = get_db()
    patients = conn.execute('''
        SELECT p.*, h.hospital_name
        FROM patients p
        JOIN hospitals h ON p.hospital_id = h.id
        WHERE p.hospital_id = ? AND p.status = 'active'
        ORDER BY p.urgency_score DESC, p.created_at DESC
    ''', (hospital_id,)).fetchall()
    conn.close()
    return patients

def get_patient_by_id(patient_id):
    """Get single patient details"""
    conn = get_db()
    patient = conn.execute('''
        SELECT p.*, h.hospital_name, h.location_city, h.contact_phone
        FROM patients p
        JOIN hospitals h ON p.hospital_id = h.id
        WHERE p.patient_id = ?
    ''', (patient_id,)).fetchone()
    conn.close()
    return patient

def update_patient(patient_id, data, hospital_id):
    """Update existing patient"""
    conn = get_db()
    organ_metrics = json.dumps(data.get('organ_metrics', {}))
    medical_history = json.dumps(data.get('medical_history', []))
    
    conn.execute('''
        UPDATE patients 
        SET name = ?, dob = ?, gender = ?, blood_group = ?, contact = ?, location = ?,
            weight_kg = ?, height_cm = ?, organ_needed = ?, organ_metrics = ?, 
            medical_history = ?, urgency_score = ?, doctor_assigned = ?
        WHERE patient_id = ? AND hospital_id = ?
    ''', (data['name'], data['dob'], data['gender'], data['blood_group'], 
          data['contact'], data['location'], data['weight_kg'], data['height_cm'],
          data['organ_needed'], organ_metrics, medical_history, data['urgency_score'],
          data['doctor_assigned'], patient_id, hospital_id))
    
    conn.commit()
    conn.close()
    return patient_id

def delete_patient(patient_id, hospital_id, status='inactive'):
    """Soft delete patient (set status)"""
    conn = get_db()
    conn.execute('''
        UPDATE patients SET status = ? WHERE patient_id = ? AND hospital_id = ?
    ''', (status, patient_id, hospital_id))
    conn.commit()
    conn.close()
    return True

def log_audit(hospital_id, action_type, entity_type, entity_id, changes=None, user_info=None):
    """Log audit trail"""
    conn = get_db()
    changes_json = json.dumps(changes) if changes else None
    user_json = json.dumps(user_info) if user_info else None
    
    conn.execute('''
        INSERT INTO audit_logs (hospital_id, action_type, entity_type, entity_id, changes, user_info)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (hospital_id, action_type, entity_type, entity_id, changes_json, user_json))
    conn.commit()
    conn.close()

# ====================
# ADVANCED MATCHING ALGORITHM
# ====================

def calculate_distance(loc1, loc2):
    """Distance calculation using Haversine where data is available."""
    if not loc1 or not loc2:
        return 999

    city1 = loc1.strip().lower()
    city2 = loc2.strip().lower()

    if city1 == city2:
        return 0

    coords1 = CITY_COORDINATES.get(city1)
    coords2 = CITY_COORDINATES.get(city2)

    if coords1 and coords2:
        lat1, lon1 = map(radians, coords1)
        lat2, lon2 = map(radians, coords2)

        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        earth_radius_km = 6371
        return round(c * earth_radius_km)

    # Fall back to heuristic if coords missing
    return 250 if city1.split()[-1] == city2.split()[-1] else 900

def calculate_hla_match(patient_hla, donor_hla):
    """Calculate HLA compatibility (0-6 matches)"""
    if not patient_hla or not donor_hla:
        return 0
    
    matches = 0
    for marker in ['hla_a', 'hla_b', 'hla_dr']:
        if marker in patient_hla and marker in donor_hla:
            patient_set = {value for value in patient_hla[marker] if value}
            donor_set = {value for value in donor_hla[marker] if value}
            matches += len(patient_set & donor_set)
    
    return min(matches, 6)

def check_blood_compatibility(patient_blood, donor_blood):
    """Check blood group compatibility"""
    compatible = {
        'A+': ['A+', 'A-', 'O+', 'O-'],
        'A-': ['A-', 'O-'],
        'B+': ['B+', 'B-', 'O+', 'O-'],
        'B-': ['B-', 'O-'],
        'AB+': ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'],
        'AB-': ['A-', 'B-', 'AB-', 'O-'],
        'O+': ['O+', 'O-'],
        'O-': ['O-']
    }
    
    return donor_blood in compatible.get(patient_blood, [])

def calculate_match_score(patient, donor):
    """Advanced matching algorithm"""
    score = 0
    reasons = []
    
    # Parse metrics
    try:
        patient_metrics = json.loads(patient['organ_metrics']) if patient['organ_metrics'] else {}
        donor_metrics = json.loads(donor['organ_metrics']) if donor['organ_metrics'] else {}
    except:
        patient_metrics = {}
        donor_metrics = {}
    
    # 1. Blood Compatibility (Critical - 30 points)
    if not check_blood_compatibility(patient['blood_group'], donor['blood_group']):
        return 0, ["❌ Blood type incompatible"]
    
    if patient['blood_group'] == donor['blood_group']:
        score += 30
        reasons.append("✓ Perfect blood match")
    elif donor['blood_group'] == 'O-':
        score += 28
        reasons.append("✓ Universal donor")
    else:
        score += 24
        reasons.append("✓ Compatible blood type")
    
    # 2. Organ Match (Critical - 25 points)
    if patient['organ_needed'] != donor['organ_type']:
        return 0, ["❌ Organ type mismatch"]
    
    score += 25
    organ = patient['organ_needed']
    
    # 3. Organ-Specific Scoring
    if organ == 'Kidney':
        # HLA Matching (up to 15 points)
        hla_matches = calculate_hla_match(
            patient_metrics.get('hla_typing'),
            donor_metrics.get('hla_typing')
        )
        hla_score = hla_matches * 2.5  # 6 matches = 15 points
        score += hla_score
        reasons.append(f"✓ HLA match: {hla_matches}/6 markers")
        
        # Dialysis duration priority
        dialysis_months = patient_metrics.get('dialysis_duration_months', 0)
        if dialysis_months > 36:
            score += 8
            reasons.append("✓ Long-term dialysis priority")
    
    elif organ == 'Liver':
        # MELD Score Priority (up to 18 points)
        meld = patient_metrics.get('meld_score', 10)
        if meld >= 35:
            score += 18
            reasons.append("🔴 Critical MELD score (35+)")
        elif meld >= 25:
            score += 12
            reasons.append("⚠️ High MELD score (25-34)")
        elif meld >= 15:
            score += 6
            reasons.append("✓ Moderate MELD score (15-24)")
    
    elif organ == 'Pancreas':
        # C-peptide and diabetes matching (up to 18 points)
        donor_cpeptide = donor_metrics.get('c_peptide_level', 0)
        patient_diabetes_type = patient_metrics.get('diabetes_type', '')
        insulin_duration = patient_metrics.get('insulin_dependency_years', 0)
        
        if donor_cpeptide > 0.5:  # Good islet cell function
            score += 12
            reasons.append("✓ Good C-peptide levels")
        
        if patient_diabetes_type == 'Type 1' and insulin_duration > 5:
            score += 6
            reasons.append("✓ Long-term Type 1 diabetes - high priority")
        
        # HbA1c compatibility
        patient_hba1c = patient_metrics.get('hba1c_level', 0)
        if patient_hba1c > 8.0:
            score += 3
            reasons.append("✓ Poor glycemic control - transplant priority")
    
    elif organ == 'Lung':
        # FEV1 and size matching (critical for lung)
        patient_bmi = calculate_bmi(patient['weight_kg'], patient['height_cm'])
        donor_bmi = calculate_bmi(donor['weight_kg'], donor['height_cm'])
        bmi_diff = abs(patient_bmi - donor_bmi)
        
        # Size matching
        if bmi_diff <= 3:
            score += 12
            reasons.append("✓ Excellent size match")
        elif bmi_diff <= 5:
            score += 8
            reasons.append("✓ Good size match")
        else:
            score -= 10
            reasons.append("⚠️ Size mismatch concern")
        
        # FEV1 compatibility
        donor_fev1 = donor_metrics.get('fev1_score', 0)
        if donor_fev1 >= 80:
            score += 8
            reasons.append("✓ Excellent donor FEV1 (≥80%)")
        elif donor_fev1 >= 70:
            score += 4
            reasons.append("✓ Good donor FEV1 (70-79%)")
        
        # Patient diagnosis priority
        patient_diagnosis = patient_metrics.get('diagnosis', '').lower()
        if 'ipf' in patient_diagnosis or 'pulmonary fibrosis' in patient_diagnosis:
            score += 5
            reasons.append("✓ IPF diagnosis - high priority")
    
    elif organ == 'Heart':
        # Size Matching (critical for heart)
        patient_bmi = calculate_bmi(patient['weight_kg'], patient['height_cm'])
        donor_bmi = calculate_bmi(donor['weight_kg'], donor['height_cm'])
        bmi_diff = abs(patient_bmi - donor_bmi)
        
        if bmi_diff <= 3:
            score += 10
            reasons.append("✓ Excellent size match")
        elif bmi_diff <= 5:
            score += 7
            reasons.append("✓ Good size match")
        else:
            score -= 10
            reasons.append("⚠️ Size mismatch concern")
    
    # 4. Distance & Cold Ischemia Time (8 points max)
    distance = calculate_distance(patient['location'], donor['location'])
    
    if organ in ['Heart', 'Lung']:
        # Critical: <4 hours transport
        if distance > 500:
            score -= 30
            reasons.append("❌ Distance too far for organ viability")
        elif distance < 100:
            score += 8
            reasons.append("✓ Excellent proximity (same region)")
        else:
            score += 4
            reasons.append("✓ Acceptable distance")
    elif organ == 'Pancreas':
        # Pancreas less time-sensitive than heart/lung
        if distance == 0:
            score += 6
            reasons.append("✓ Same city - minimal transport time")
        elif distance <= 300:
            score += 4
            reasons.append("✓ Regional match")
        else:
            score += 2
            reasons.append("⚠️ Longer transport, still feasible")
    else:
        # Kidney/Liver less time-sensitive
        if distance == 0:
            score += 6
            reasons.append("✓ Same city - minimal transport time")
        elif distance <= 300:
            score += 5
            reasons.append("✓ Regional match")
        elif distance <= 600:
            score += 2
            reasons.append("⚠️ Longer transport, still feasible")
        else:
            score -= 3
            reasons.append("⚠️ Extended transport window - monitor viability")
    
    # 5. Urgency Weighting (15 points max)
    urgency = patient['urgency_score']
    urgency_points = min(15, urgency * 0.15)
    score += urgency_points
    
    if urgency >= 90:
        reasons.append("🔴 CRITICAL urgency")
    elif urgency >= 70:
        reasons.append("⚠️ High urgency")
    elif urgency >= 50:
        reasons.append("✓ Moderate urgency")
    
    # 6. Medical Contraindications Check
    patient_history_str = patient['medical_history'] if patient['medical_history'] else '[]'
    donor_history_str = donor['medical_history'] if donor['medical_history'] else '[]'
    
    try:
        patient_history = json.loads(patient_history_str) if isinstance(patient_history_str, str) else (patient_history_str if isinstance(patient_history_str, list) else [])
        donor_history = json.loads(donor_history_str) if isinstance(donor_history_str, str) else (donor_history_str if isinstance(donor_history_str, list) else [])
    except:
        patient_history = []
        donor_history = []
    
    # Check for active cancer in donor
    if 'Active Cancer' in donor_history or 'Malignancy' in donor_history:
        score -= 50
        reasons.append("❌ Donor has active cancer - contraindication")
    
    # Check for incompatible medical histories
    if 'Active Infection' in donor_history:
        score -= 20
        reasons.append("⚠️ Donor has active infection - review required")
    
    # 7. Age Matching Bonus (4 points max)
    patient_age = calculate_age(patient['dob'])
    donor_age = calculate_age(donor['dob'])
    age_diff = abs(patient_age - donor_age)
    
    if age_diff <= 10:
        score += 4
        reasons.append("✓ Similar age range")
    elif age_diff <= 20:
        score += 2
        reasons.append("✓ Acceptable age difference")
    
    # Penalize incomplete clinical data to avoid perfect scores without depth
    if not patient_metrics or not donor_metrics:
        score -= 5
        reasons.append("ℹ️ Limited clinical markers supplied")
    
    score = max(0, min(int(round(score)), 100))
    return score, reasons

# ====================
# MATCHING FUNCTIONS
# ====================

def get_matches():
    """Get all potential matches with scores"""
    conn = get_db()
    patients = conn.execute('SELECT * FROM patients WHERE status = "active"').fetchall()
    donors = conn.execute('SELECT * FROM donors WHERE status = "active"').fetchall()
    conn.close()
    
    matches = []
    
    for patient in patients:
        best_donor = None
        best_score = 0
        best_reasons = []
        best_distance = None
        
        for donor in donors:
            score, reasons = calculate_match_score(patient, donor)
            
            if score > best_score:
                best_score = score
                best_donor = donor
                best_reasons = reasons
                best_distance = calculate_distance(patient['location'], donor['location'])
        
        if best_donor and best_score > 0:
            matches.append({
                'patient': patient,
                'donor': best_donor,
                'score': best_score,
                'reasons': best_reasons,
                'distance_km': best_distance
            })
    
    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches

def search_by_id(search_id):
    """Search for patient or donor by ID"""
    search_id = search_id.upper()
    conn = get_db()
    
    # Try patient first
    result = conn.execute('''
        SELECT p.*, h.hospital_name, 'patient' as type
        FROM patients p
        JOIN hospitals h ON p.hospital_id = h.id
        WHERE p.patient_id = ?
    ''', (search_id,)).fetchone()
    
    if result:
        conn.close()
        return result
    
    # Try donor
    result = conn.execute('''
        SELECT d.*, h.hospital_name, 'donor' as type
        FROM donors d
        JOIN hospitals h ON d.hospital_id = h.id
        WHERE d.donor_id = ?
    ''', (search_id,)).fetchone()
    
    conn.close()
    return result