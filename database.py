import sqlite3
import os
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('LIFELINK_DB', os.path.join(BASE_DIR, 'lifelink.db'))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    """Initialize the database with all tables and sample data."""
    conn = get_db()
    cursor = conn.cursor()

    # Hospitals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hospitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_name TEXT NOT NULL UNIQUE,
            license_number TEXT NOT NULL UNIQUE,
            location_city TEXT NOT NULL,
            location_state TEXT NOT NULL,
            hospital_type TEXT NOT NULL,
            admin_name TEXT NOT NULL,
            admin_designation TEXT NOT NULL,
            contact_phone TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Donors table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS donors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            dob DATE NOT NULL,
            gender TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            contact TEXT NOT NULL,
            location TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            height_cm REAL NOT NULL,
            organ_type TEXT NOT NULL,
            organ_metrics TEXT,
            medical_history TEXT,
            death_date DATE,
            hospital_id INTEGER NOT NULL,
            doctor_assigned TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE
        )
    ''')

    # Ensure new death_date column exists for existing databases
    donor_columns = cursor.execute('PRAGMA table_info(donors)').fetchall()
    if donor_columns:
        column_names = {col['name'] for col in donor_columns}
        if 'death_date' not in column_names:
            cursor.execute('ALTER TABLE donors ADD COLUMN death_date DATE')

    # Patients table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            dob DATE NOT NULL,
            gender TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            contact TEXT NOT NULL,
            location TEXT NOT NULL,
            weight_kg REAL NOT NULL,
            height_cm REAL NOT NULL,
            organ_needed TEXT NOT NULL,
            organ_metrics TEXT,
            medical_history TEXT,
            urgency_score INTEGER NOT NULL,
            hospital_id INTEGER NOT NULL,
            doctor_assigned TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE
        )
    ''')

    # Matches table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            donor_id TEXT NOT NULL,
            score INTEGER NOT NULL,
            reasoning TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE,
            FOREIGN KEY (donor_id) REFERENCES donors(donor_id) ON DELETE CASCADE
        )
    ''')

    # Audit logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            changes TEXT,
            user_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hospital_id) REFERENCES hospitals(id) ON DELETE CASCADE
        )
    ''')

    # Helpful indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donors_hospital ON donors(hospital_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patients_hospital ON patients(hospital_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_score ON matches(score DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_hospital ON audit_logs(hospital_id)')

    # Seed hospitals once
    existing = cursor.execute('SELECT COUNT(*) as count FROM hospitals').fetchone()['count']
    if existing == 0:
        sample_hospitals = [
            ('Apollo Hospital Mumbai', 'MH123456', 'Mumbai', 'Maharashtra', 'Private',
             'Dr. Rajesh Kumar', 'Medical Director', '9876543210', 'admin@apollomumbai.in',
             'apollo_mumbai', generate_password_hash('apollo123')),
            
            ('Lilavati Hospital Mumbai', 'MH234567', 'Mumbai', 'Maharashtra', 'Private',
             'Dr. Priya Sharma', 'CEO', '9876543211', 'admin@lilavati.in',
             'lilavati', generate_password_hash('lilavati123')),
            
            ('AIIMS Delhi', 'DL123456', 'New Delhi', 'Delhi', 'Government',
             'Dr. Amit Singh', 'Director', '9876543212', 'admin@aiims.in',
             'aiims_delhi', generate_password_hash('aiims123')),
            
            ('Manipal Hospital Bangalore', 'KA123456', 'Bangalore', 'Karnataka', 'Private',
             'Dr. Sunita Reddy', 'Medical Director', '9876543213', 'admin@manipal.in',
             'manipal_blr', generate_password_hash('manipal123')),
            
            ('Fortis Hospital Delhi', 'DL234567', 'New Delhi', 'Delhi', 'Private',
             'Dr. Vikram Mehta', 'CEO', '9876543214', 'admin@fortis.in',
             'fortis_delhi', generate_password_hash('fortis123'))
        ]
        
        cursor.executemany('''
            INSERT INTO hospitals (hospital_name, license_number, location_city, location_state,
                                 hospital_type, admin_name, admin_designation, contact_phone,
                                 contact_email, username, password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', sample_hospitals)

    conn.commit()
    conn.close()
    print("✓ Database initialized successfully")

# Approved hospitals list for signup validation
APPROVED_HOSPITALS = {
    "MH123456": "Apollo Hospital Mumbai",
    "MH234567": "Lilavati Hospital Mumbai",
    "MH345678": "Breach Candy Hospital Mumbai",
    "DL123456": "AIIMS Delhi",
    "DL234567": "Fortis Hospital Delhi",
    "DL345678": "Max Hospital Delhi",
    "KA123456": "Manipal Hospital Bangalore",
    "KA234567": "Apollo Hospital Bangalore",
    "KA345678": "Narayana Health Bangalore",
    "TN123456": "Apollo Hospital Chennai",
    "TN234567": "Fortis Malar Chennai",
    "GJ123456": "Sterling Hospital Ahmedabad",
    "RJ123456": "Fortis Hospital Jaipur",
    "UP123456": "Max Hospital Noida",
    "HR123456": "Medanta Gurgaon",
    "WB123456": "AMRI Hospital Kolkata",
    "TG123456": "Apollo Hospital Hyderabad",
    "PB123456": "Fortis Hospital Mohali",
    "MP123456": "CHL Hospital Indore",
    "BR123456": "AIIMS Patna"
}

if __name__ == '__main__':
    init_db()