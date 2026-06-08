"""
Database models and operations for the Voting System
"""
import json
import os
import shutil
import sqlite3
from uuid import uuid4
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from modules.security import hash_aadhar

class Database:
    def __init__(self):
        self.db_path = Config.DATABASE_PATH
        self.init_db()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Initialize database with required tables and run migrations"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Run migrations for legacy schemas
        self._migrate_schema(conn, cursor)

        # Polls table (multi-poll support, with per-admin isolation)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                poll_start_time TEXT,
                poll_end_time TEXT,
                is_active INTEGER DEFAULT 0,
                allow_nota INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (admin_id) REFERENCES admin_users(id)
            )
        ''')
        
        # Users table (new schema)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                middle_name TEXT,
                last_name TEXT NOT NULL,
                aadhar_hash TEXT UNIQUE NOT NULL,
                aadhar_last4 TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                profile_image TEXT,
                eid_hash TEXT UNIQUE,
                eid_pdf_path TEXT,
                has_voted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Candidates table (party_symbol nullable)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_name TEXT NOT NULL,
                party_name TEXT NOT NULL,
                party_symbol TEXT,
                poll_id INTEGER,
                age INTEGER,
                manifesto_path TEXT,
                description TEXT,
                vote_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (poll_id) REFERENCES polls(id)
            )
        ''')
        
        # Votes table (no Aadhaar)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER NOT NULL,
                poll_id INTEGER,
                voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (candidate_id) REFERENCES candidates(id),
                FOREIGN KEY (poll_id) REFERENCES polls(id)
            )
        ''')
        
        # Admin users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        
        # Face embeddings table for duplicate detection
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS face_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                face_id TEXT UNIQUE NOT NULL,
                embedding_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Poll settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS poll_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_start_time TEXT,
                poll_end_time TEXT,
                is_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Vote receipts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vote_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                candidate_id INTEGER NOT NULL,
                poll_id INTEGER,
                receipt_hash TEXT UNIQUE NOT NULL,
                pdf_path TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (candidate_id) REFERENCES candidates(id),
                FOREIGN KEY (poll_id) REFERENCES polls(id)
            )
        ''')
        
        # Face recognition logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS face_recognition_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                context TEXT,
                detection_confidence REAL,
                match_similarity REAL,
                success INTEGER DEFAULT 0,
                reason TEXT,
                winner_user_id INTEGER,
                runner_up_user_id INTEGER,
                winner_score REAL,
                runner_up_score REAL,
                score_margin REAL,
                valid_frame_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # User-Poll access control table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_poll_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                poll_id INTEGER NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, poll_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (poll_id) REFERENCES polls(id)
            )
        ''')

        self._ensure_face_log_columns(conn, cursor)
        self._purge_legacy_face_enrollments_once(conn, cursor)

        cursor.execute('SELECT * FROM poll_settings')
        if not cursor.fetchone():
            cursor.execute('INSERT INTO poll_settings (is_active) VALUES (0)')

        # Ensure at least one poll exists
        cursor.execute('SELECT * FROM polls')
        if not cursor.fetchone():
            settings = self.get_poll_settings()
            title = 'Default Poll'
            description = 'Auto-created default poll'
            poll_start = settings.get('poll_start_time') if settings else None
            poll_end = settings.get('poll_end_time') if settings else None
            is_active = settings.get('is_active') if settings else 0
            cursor.execute('''
                INSERT INTO polls (title, description, poll_start_time, poll_end_time, is_active, allow_nota)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (title, description, poll_start, poll_end, is_active))
        
        # Insert default admin if not exists (with hashed password)
        cursor.execute('SELECT * FROM admin_users WHERE email = ?', (Config.ADMIN_EMAIL,))
        existing_admin = cursor.fetchone()
        if not existing_admin:
            hashed_pw = generate_password_hash(Config.ADMIN_PASSWORD)
            cursor.execute('INSERT INTO admin_users (email, password) VALUES (?, ?)',
                         (Config.ADMIN_EMAIL, hashed_pw))
        else:
            # Migrate plaintext password to hashed if it's still plaintext
            raw_pw = existing_admin['password'] if existing_admin else None
            if raw_pw and not raw_pw.startswith('pbkdf2:') and not raw_pw.startswith('scrypt:'):
                hashed_pw = generate_password_hash(raw_pw)
                cursor.execute('UPDATE admin_users SET password = ? WHERE email = ?',
                             (hashed_pw, Config.ADMIN_EMAIL))
        
        conn.commit()
        conn.close()

    def _table_exists(self, cursor, table_name):
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        )
        return cursor.fetchone() is not None

    def _get_table_info(self, cursor, table_name):
        cursor.execute(f"PRAGMA table_info({table_name})")
        return cursor.fetchall()

    def _column_exists(self, cursor, table_name, column_name):
        return any(col['name'] == column_name for col in self._get_table_info(cursor, table_name))

    def _get_meta_value(self, cursor, key):
        cursor.execute('SELECT value FROM app_meta WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row['value'] if row else None

    def _set_meta_value(self, cursor, key, value):
        cursor.execute(
            'INSERT INTO app_meta (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value)
        )

    def _ensure_face_log_columns(self, conn, cursor):
        required_columns = {
            'winner_user_id': 'INTEGER',
            'runner_up_user_id': 'INTEGER',
            'winner_score': 'REAL',
            'runner_up_score': 'REAL',
            'score_margin': 'REAL',
            'valid_frame_count': 'INTEGER',
            'poll_id': 'INTEGER'
        }
        for column_name, definition in required_columns.items():
            if not self._column_exists(cursor, 'face_recognition_logs', column_name):
                cursor.execute(f'ALTER TABLE face_recognition_logs ADD COLUMN {column_name} {definition}')
        conn.commit()

    def _purge_legacy_face_enrollments_once(self, conn, cursor):
        marker_key = 'face_enrollment_schema_version'
        if self._get_meta_value(cursor, marker_key) == '2':
            return

        cursor.execute('DELETE FROM face_embeddings')
        dataset_folder = Config.FACE_DATASET_FOLDER
        if os.path.exists(dataset_folder):
            for entry in os.listdir(dataset_folder):
                path = os.path.join(dataset_folder, entry)
                if os.path.isdir(path):
                    shutil.rmtree(path)
        self._set_meta_value(cursor, marker_key, '2')
        conn.commit()

    def _migrate_schema(self, conn, cursor):
        """
        Migrate legacy schema (Aadhaar plaintext, votes table, candidates constraints).
        Safe to run multiple times.
        """
        # Migrate users/votes/embeddings if legacy Aadhaar columns exist
        if self._table_exists(cursor, 'users') and self._column_exists(cursor, 'users', 'aadhar_id') \
           and not self._column_exists(cursor, 'users', 'aadhar_hash'):
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.commit()

            # Create new users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    middle_name TEXT,
                    last_name TEXT NOT NULL,
                    aadhar_hash TEXT UNIQUE NOT NULL,
                    aadhar_last4 TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    profile_image TEXT,
                    eid_hash TEXT UNIQUE,
                    eid_pdf_path TEXT,
                    has_voted INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Copy users
            cursor.execute('SELECT * FROM users')
            users = cursor.fetchall()
            for user in users:
                raw_aadhar = user['aadhar_id']
                aadhar_hash = hash_aadhar(raw_aadhar)
                aadhar_last4 = str(raw_aadhar)[-4:] if raw_aadhar else '0000'
                cursor.execute('''
                    INSERT INTO users_new (
                        id, first_name, middle_name, last_name,
                        aadhar_hash, aadhar_last4, phone, email,
                        password, profile_image, eid_hash, eid_pdf_path, has_voted, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user['id'], user['first_name'], user['middle_name'], user['last_name'],
                    aadhar_hash, aadhar_last4, user['phone'], user['email'],
                    user['password'],
                    user['profile_image'] if 'profile_image' in user.keys() else None,
                    user['eid_hash'] if 'eid_hash' in user.keys() else None,
                    user['eid_pdf_path'] if 'eid_pdf_path' in user.keys() else None,
                    user['has_voted'], user['created_at']
                ))

            # Create new votes table
            if self._table_exists(cursor, 'votes'):
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS votes_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        candidate_id INTEGER NOT NULL,
                        voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id),
                        FOREIGN KEY (candidate_id) REFERENCES candidates(id)
                    )
                ''')
                cursor.execute('''
                    INSERT INTO votes_new (id, user_id, candidate_id, voted_at)
                    SELECT id, user_id, candidate_id, voted_at FROM votes
                ''')

            # Migrate face embeddings (aadhar_id -> user_id)
            if self._table_exists(cursor, 'face_embeddings'):
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS face_embeddings_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        face_id TEXT UNIQUE NOT NULL,
                        embedding_data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )
                ''')
                cursor.execute('SELECT id, aadhar_id FROM users')
                user_map = {row['aadhar_id']: row['id'] for row in cursor.fetchall()}
                cursor.execute('SELECT * FROM face_embeddings')
                embeddings = cursor.fetchall()
                for emb in embeddings:
                    user_id = user_map.get(emb['aadhar_id'])
                    if not user_id:
                        continue
                    embedding_data = emb['embedding_data']
                    face_id = hash_aadhar(embedding_data)[:32]
                    cursor.execute('''
                        INSERT INTO face_embeddings_new (id, user_id, face_id, embedding_data, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (
                        emb['id'], user_id, face_id, embedding_data, emb['created_at']
                    ))

            # Swap tables
            cursor.execute('DROP TABLE IF EXISTS users')
            cursor.execute('ALTER TABLE users_new RENAME TO users')

            if self._table_exists(cursor, 'votes_new'):
                cursor.execute('DROP TABLE IF EXISTS votes')
                cursor.execute('ALTER TABLE votes_new RENAME TO votes')

            if self._table_exists(cursor, 'face_embeddings_new'):
                cursor.execute('DROP TABLE IF EXISTS face_embeddings')
                cursor.execute('ALTER TABLE face_embeddings_new RENAME TO face_embeddings')

            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()

        # Migrate candidates table to allow nullable party_symbol and new columns
        if self._table_exists(cursor, 'candidates'):
            columns = self._get_table_info(cursor, 'candidates')
            column_names = {col['name']: col for col in columns}
            party_symbol_info = column_names.get('party_symbol')
            party_symbol_notnull = party_symbol_info['notnull'] == 1 if party_symbol_info else False
            missing_cols = any(name not in column_names for name in ['age', 'manifesto_path', 'description', 'poll_id'])

            if party_symbol_notnull or missing_cols:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.commit()

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS candidates_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_name TEXT NOT NULL,
                        party_name TEXT NOT NULL,
                        party_symbol TEXT,
                        poll_id INTEGER,
                        age INTEGER,
                        manifesto_path TEXT,
                        description TEXT,
                        vote_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (poll_id) REFERENCES polls(id)
                    )
                ''')

                cursor.execute('SELECT * FROM candidates')
                rows = cursor.fetchall()
                for row in rows:
                    cursor.execute('''
                        INSERT INTO candidates_new (
                            id, candidate_name, party_name, party_symbol,
                            poll_id, age, manifesto_path, description, vote_count, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        row['id'],
                        row['candidate_name'],
                        row['party_name'],
                        row['party_symbol'] if 'party_symbol' in row.keys() else None,
                        row['poll_id'] if 'poll_id' in row.keys() else None,
                        row['age'] if 'age' in row.keys() else None,
                        row['manifesto_path'] if 'manifesto_path' in row.keys() else None,
                        row['description'] if 'description' in row.keys() else None,
                        row['vote_count'] if 'vote_count' in row.keys() else 0,
                        row['created_at'] if 'created_at' in row.keys() else None
                    ))

                cursor.execute('DROP TABLE IF EXISTS candidates')
                cursor.execute('ALTER TABLE candidates_new RENAME TO candidates')

                conn.execute("PRAGMA foreign_keys=ON")
                conn.commit()

        # Add allow_nota column to polls if missing
        if self._table_exists(cursor, 'polls') and not self._column_exists(cursor, 'polls', 'allow_nota'):
            cursor.execute('ALTER TABLE polls ADD COLUMN allow_nota INTEGER DEFAULT 0')
            conn.commit()

        # Add admin_id column to polls if missing (data isolation migration)
        if self._table_exists(cursor, 'polls') and not self._column_exists(cursor, 'polls', 'admin_id'):
            cursor.execute('ALTER TABLE polls ADD COLUMN admin_id INTEGER')
            conn.commit()

        # Add profile_image/eid_hash/eid_pdf_path columns to users if missing
        if self._table_exists(cursor, 'users'):
            if not self._column_exists(cursor, 'users', 'profile_image'):
                cursor.execute('ALTER TABLE users ADD COLUMN profile_image TEXT')
            if not self._column_exists(cursor, 'users', 'eid_hash'):
                cursor.execute('ALTER TABLE users ADD COLUMN eid_hash TEXT')
            if not self._column_exists(cursor, 'users', 'eid_pdf_path'):
                cursor.execute('ALTER TABLE users ADD COLUMN eid_pdf_path TEXT')
            conn.commit()

        # Ensure polls table exists for migrations
        if not self._table_exists(cursor, 'polls'):
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    title TEXT NOT NULL,
                    description TEXT,
                    poll_start_time TEXT,
                    poll_end_time TEXT,
                    is_active INTEGER DEFAULT 0,
                    allow_nota INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (admin_id) REFERENCES admin_users(id)
                )
            ''')
            conn.commit()

        # Seed default poll if none exists
        cursor.execute('SELECT id FROM polls ORDER BY id LIMIT 1')
        poll_row = cursor.fetchone()
        if not poll_row:
            cursor.execute('''
                INSERT INTO polls (title, description, is_active)
                VALUES (?, ?, ?)
            ''', ('Default Poll', 'Auto-created default poll', 0))
            conn.commit()
            cursor.execute('SELECT id FROM polls ORDER BY id LIMIT 1')
            poll_row = cursor.fetchone()

        default_poll_id = poll_row['id'] if poll_row else None

        # Add poll_id to votes/receipts if missing
        if self._table_exists(cursor, 'votes') and not self._column_exists(cursor, 'votes', 'poll_id'):
            cursor.execute('ALTER TABLE votes ADD COLUMN poll_id INTEGER')
            conn.commit()
        if self._table_exists(cursor, 'vote_receipts') and not self._column_exists(cursor, 'vote_receipts', 'poll_id'):
            cursor.execute('ALTER TABLE vote_receipts ADD COLUMN poll_id INTEGER')
            conn.commit()
        if self._table_exists(cursor, 'candidates') and not self._column_exists(cursor, 'candidates', 'poll_id'):
            cursor.execute('ALTER TABLE candidates ADD COLUMN poll_id INTEGER')
            conn.commit()

        # Backfill poll_id for existing rows
        if default_poll_id is not None:
            if self._table_exists(cursor, 'candidates'):
                cursor.execute('UPDATE candidates SET poll_id = ? WHERE poll_id IS NULL', (default_poll_id,))
            if self._table_exists(cursor, 'votes'):
                cursor.execute('UPDATE votes SET poll_id = ? WHERE poll_id IS NULL', (default_poll_id,))
            if self._table_exists(cursor, 'vote_receipts'):
                cursor.execute('UPDATE vote_receipts SET poll_id = ? WHERE poll_id IS NULL', (default_poll_id,))
            conn.commit()
    
    # User operations
    def create_user(self, first_name, middle_name, last_name, aadhar_hash, aadhar_last4, phone, email, password):
        """Create a new user"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            hashed_password = generate_password_hash(password)
            
            cursor.execute('''
                INSERT INTO users (first_name, middle_name, last_name, aadhar_hash, aadhar_last4, phone, email, password)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (first_name, middle_name, last_name, aadhar_hash, aadhar_last4, phone, email, hashed_password))
            
            conn.commit()
            user_id = cursor.lastrowid
            conn.close()
            return {'success': True, 'user_id': user_id}
        except sqlite3.IntegrityError as e:
            return {'success': False, 'error': 'Email or Aadhaar already registered'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_user_by_email(self, email):
        """Get user by email"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    def get_users_by_phone(self, phone):
        """Get users by phone number."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE phone = ? ORDER BY id', (phone,))
        users = cursor.fetchall()
        conn.close()
        return [dict(user) for user in users]

    def get_user_by_eid_hash(self, eid_hash):
        """Get user by E-ID hash"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE eid_hash = ?', (eid_hash,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None
    
    def get_user_by_aadhar_hash(self, aadhar_hash):
        """Get user by Aadhaar hash"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE aadhar_hash = ?', (aadhar_hash,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    def get_user_by_id(self, user_id):
        """Get user by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return dict(user) if user else None

    def get_all_users(self):
        """Get all registered voters ordered by name"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, first_name, last_name, email, phone, aadhar_last4 '
            'FROM users ORDER BY first_name, last_name'
        )
        users = cursor.fetchall()
        conn.close()
        return [dict(u) for u in users]
    
    def verify_user_password(self, email, password):
        """Verify user password"""
        user = self.get_user_by_email(email)
        if user and check_password_hash(user['password'], password):
            return user
        return None
    
    def update_user(self, user_id, **kwargs):
        """Update user details"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Build dynamic update query
        fields = []
        values = []
        for key, value in kwargs.items():
            if key == 'password':
                value = generate_password_hash(value)
            fields.append(f"{key} = ?")
            values.append(value)
        
        values.append(user_id)
        query = f"UPDATE users SET {', '.join(fields)} WHERE id = ?"
        
        cursor.execute(query, values)
        conn.commit()
        conn.close()
        return {'success': True}

    def delete_user(self, user_id):
        """Delete user and related data (for duplicate face rejection)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM face_embeddings WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM vote_receipts WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM votes WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        return {'success': True}
    
    def mark_user_voted(self, user_id):
        """Mark user as voted"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET has_voted = 1 WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def has_user_voted(self, user_id, poll_id=None):
        """Check if user has voted (optionally within a poll)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if poll_id is not None:
            cursor.execute('SELECT 1 FROM votes WHERE user_id = ? AND poll_id = ? LIMIT 1', (user_id, poll_id))
        else:
            cursor.execute('SELECT 1 FROM votes WHERE user_id = ? LIMIT 1', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    # Candidate operations
    def add_candidate(self, candidate_name, party_name, party_symbol=None, poll_id=None):
        """Add a new candidate"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO candidates (candidate_name, party_name, party_symbol, poll_id)
                VALUES (?, ?, ?, ?)
            ''', (candidate_name, party_name, party_symbol, poll_id))
            
            conn.commit()
            candidate_id = cursor.lastrowid
            conn.close()
            return {'success': True, 'candidate_id': candidate_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_all_candidates(self, admin_id=None):
        """Get all candidates, optionally filtered to a specific admin's polls"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if admin_id is not None:
            cursor.execute('''
                SELECT c.* FROM candidates c
                JOIN polls p ON c.poll_id = p.id
                WHERE p.admin_id = ?
                ORDER BY c.id
            ''', (admin_id,))
        else:
            cursor.execute('SELECT * FROM candidates ORDER BY id')
        candidates = cursor.fetchall()
        conn.close()
        return [dict(candidate) for candidate in candidates]

    def get_candidates_by_poll(self, poll_id, include_nota=True):
        """Get candidates for a specific poll"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM candidates WHERE poll_id = ? ORDER BY id', (poll_id,))
        candidates = cursor.fetchall()
        conn.close()
        result = [dict(candidate) for candidate in candidates]
        if not include_nota:
            result = [c for c in result if str(c.get('candidate_name', '')).upper() != 'NOTA']
        return result

    def ensure_nota_candidate(self, poll_id):
        """Ensure a NOTA candidate exists for a poll"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM candidates
            WHERE poll_id = ? AND UPPER(candidate_name) = 'NOTA'
            LIMIT 1
        ''', (poll_id,))
        exists = cursor.fetchone()
        if not exists:
            cursor.execute('''
                INSERT INTO candidates (candidate_name, party_name, party_symbol, poll_id, age, manifesto_path, description)
                VALUES ('NOTA', 'NOTA', NULL, ?, NULL, NULL, 'None of the above')
            ''', (poll_id,))
            conn.commit()
        conn.close()

    def get_poll_status_info(self, poll):
        """Compute poll status and open state for UI"""
        from datetime import datetime
        if not poll:
            return {'status': 'unknown', 'is_open': False}
        start_time = poll.get('poll_start_time')
        end_time = poll.get('poll_end_time')
        now = datetime.now()
        if start_time and end_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(end_time)
            except Exception:
                return {'status': 'invalid', 'is_open': False}
            if now < start_dt:
                return {'status': 'upcoming', 'is_open': False}
            if now > end_dt:
                return {'status': 'closed', 'is_open': False}
            return {'status': 'active', 'is_open': True}
        return {'status': 'active' if poll.get('is_active') == 1 else 'inactive', 'is_open': poll.get('is_active') == 1}
    
    def get_candidate_by_id(self, candidate_id):
        """Get candidate by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM candidates WHERE id = ?', (candidate_id,))
        candidate = cursor.fetchone()
        conn.close()
        return dict(candidate) if candidate else None
    
    # Voting operations
    def cast_vote(self, user_id, candidate_id, poll_id=None):
        """Cast a vote"""
        try:
            # Check if user already voted
            if self.has_user_voted(user_id, poll_id):
                return {'success': False, 'error': 'You have already voted'}
            
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Insert vote
            cursor.execute('''
                INSERT INTO votes (user_id, candidate_id, poll_id)
                VALUES (?, ?, ?)
            ''', (user_id, candidate_id, poll_id))
            
            # Increment candidate vote count
            cursor.execute('''
                UPDATE candidates SET vote_count = vote_count + 1 WHERE id = ?
            ''', (candidate_id,))
            
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    # Admin operations
    def verify_admin(self, email, password):
        """Verify admin credentials using hashed passwords"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM admin_users WHERE email = ?', (email,))
        admin = cursor.fetchone()
        conn.close()
        if admin and check_password_hash(admin['password'], password):
            return dict(admin)
        return None

    def create_admin(self, email, password):
        """Create a new admin account with hashed password"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            hashed_password = generate_password_hash(password)
            cursor.execute(
                'INSERT INTO admin_users (email, password) VALUES (?, ?)',
                (email, hashed_password)
            )
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': 'Email already registered as admin' if 'UNIQUE' in str(e) else str(e)}
    
    # User-Poll access control operations
    def assign_users_to_poll(self, poll_id, user_ids):
        """Assign a list of users to a poll (replace existing assignments)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            # Remove all existing assignments for this poll
            cursor.execute('DELETE FROM user_poll_access WHERE poll_id = ?', (poll_id,))
            # Insert new assignments
            for uid in user_ids:
                cursor.execute(
                    'INSERT OR IGNORE INTO user_poll_access (user_id, poll_id) VALUES (?, ?)',
                    (uid, poll_id)
                )
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def add_users_to_poll(self, poll_id, user_ids):
        """Add users to a poll without removing existing assignments."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            inserted = 0
            for uid in user_ids:
                cursor.execute(
                    'INSERT OR IGNORE INTO user_poll_access (user_id, poll_id) VALUES (?, ?)',
                    (uid, poll_id)
                )
                inserted += cursor.rowcount
            conn.commit()
            conn.close()
            return {'success': True, 'assigned': inserted}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def remove_user_from_poll(self, poll_id, user_id):
        """Remove a single user from a poll"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM user_poll_access WHERE poll_id = ? AND user_id = ?',
                (poll_id, user_id)
            )
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_eligible_users_for_poll(self, poll_id):
        """Return all users assigned to a poll"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.id, u.first_name, u.last_name, u.email
            FROM users u
            JOIN user_poll_access upa ON u.id = upa.user_id
            WHERE upa.poll_id = ?
            ORDER BY u.first_name, u.last_name
        ''', (poll_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_polls_for_user(self, user_id):
        """Return only the polls this user is assigned to (with status info)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*
            FROM polls p
            JOIN user_poll_access upa ON p.id = upa.poll_id
            WHERE upa.user_id = ?
            ORDER BY p.id DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def user_has_poll_access(self, user_id, poll_id):
        """Check whether a user is assigned to a specific poll"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT 1 FROM user_poll_access WHERE user_id = ? AND poll_id = ?',
            (user_id, poll_id)
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None

    # Results operations
    def get_results(self, poll_id=None, admin_id=None):
        """Get election results, optionally scoped by poll and/or admin"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if poll_id:
            cursor.execute('''
                SELECT candidate_name, party_name, party_symbol, vote_count
                FROM candidates
                WHERE poll_id = ?
                ORDER BY vote_count DESC
            ''', (poll_id,))
        elif admin_id is not None:
            cursor.execute('''
                SELECT c.candidate_name, c.party_name, c.party_symbol, c.vote_count
                FROM candidates c
                JOIN polls p ON c.poll_id = p.id
                WHERE p.admin_id = ?
                ORDER BY c.vote_count DESC
            ''', (admin_id,))
        else:
            cursor.execute('''
                SELECT candidate_name, party_name, party_symbol, vote_count
                FROM candidates
                ORDER BY vote_count DESC
            ''')
        results = cursor.fetchall()
        conn.close()
        return [dict(result) for result in results]
    
    # Face embeddings operations
    def store_face_embedding(self, user_id, face_id, embedding_data):
        """Store a single face embedding for compatibility with older code."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            embedding_str = json.dumps(embedding_data.tolist() if hasattr(embedding_data, 'tolist') else embedding_data)
            cursor.execute('''
                INSERT INTO face_embeddings (user_id, face_id, embedding_data)
                VALUES (?, ?, ?)
            ''', (user_id, face_id, embedding_str))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def replace_face_embeddings(self, user_id, embeddings):
        """Replace a user's face enrollment with multiple embedding samples."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM face_embeddings WHERE user_id = ?', (user_id,))
            stored_count = 0
            for index, embedding_data in enumerate(embeddings):
                embedding_str = json.dumps(embedding_data.tolist() if hasattr(embedding_data, 'tolist') else embedding_data)
                face_id = f"user_{user_id}_{index}_{uuid4().hex}"
                cursor.execute('''
                    INSERT INTO face_embeddings (user_id, face_id, embedding_data)
                    VALUES (?, ?, ?)
                ''', (user_id, face_id, embedding_str))
                stored_count += 1
            conn.commit()
            conn.close()
            return {'success': True, 'stored_count': stored_count}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def delete_face_embeddings(self, user_id):
        """Delete all enrolled face embeddings for a user."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM face_embeddings WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return {'success': True}

    def get_all_face_embeddings(self):
        """Get all face embeddings for comparison."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, user_id, face_id, embedding_data, created_at FROM face_embeddings ORDER BY user_id, id')
        embeddings = cursor.fetchall()
        conn.close()
        return [dict(emb) for emb in embeddings]

    def get_user_face_embeddings(self, user_id):
        """Get all face embeddings enrolled for a specific user."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, user_id, face_id, embedding_data, created_at FROM face_embeddings WHERE user_id = ? ORDER BY id', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_face_embeddings_grouped(self):
        """Return face embeddings grouped by user_id."""
        grouped = {}
        for embedding in self.get_all_face_embeddings():
            grouped.setdefault(embedding['user_id'], []).append(embedding)
        return grouped

    def check_face_exists(self, user_id):
        """Check if any face embeddings exist for the user."""
        return self.user_has_face_enrollment(user_id)

    def user_has_face_enrollment(self, user_id):
        """Check whether a user has at least one enrolled face embedding."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM face_embeddings WHERE user_id = ? LIMIT 1', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    # Poll settings operations (legacy)
    def get_poll_settings(self):
        """Get current poll settings (legacy)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM poll_settings ORDER BY id DESC LIMIT 1')
        settings = cursor.fetchone()
        conn.close()
        return dict(settings) if settings else None

    # Poll operations (multi-poll, admin-isolated)
    def create_poll(self, title, description=None, poll_start_time=None, poll_end_time=None, allow_nota=0, admin_id=None):
        """Create a new poll owned by admin_id"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO polls (admin_id, title, description, poll_start_time, poll_end_time, is_active, allow_nota)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            ''', (admin_id, title, description, poll_start_time, poll_end_time, int(allow_nota)))
            conn.commit()
            poll_id = cursor.lastrowid
            conn.close()
            if int(allow_nota) == 1:
                self.ensure_nota_candidate(poll_id)
            return {'success': True, 'poll_id': poll_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def delete_poll(self, poll_id, admin_id=None):
        """Delete a poll and all its related data (ownership enforced if admin_id provided)"""
        try:
            poll = self.get_poll_by_id(poll_id)
            if not poll:
                return {'success': False, 'error': 'Poll not found'}
            if admin_id is not None and poll.get('admin_id') != admin_id:
                return {'success': False, 'error': 'Access denied'}

            conn = self.get_connection()
            cursor = conn.cursor()
            # Delete in safe order (children first)
            cursor.execute('DELETE FROM vote_receipts WHERE poll_id = ?', (poll_id,))
            cursor.execute('DELETE FROM votes WHERE poll_id = ?', (poll_id,))
            cursor.execute('DELETE FROM candidates WHERE poll_id = ?', (poll_id,))
            cursor.execute('DELETE FROM user_poll_access WHERE poll_id = ?', (poll_id,))
            cursor.execute('DELETE FROM polls WHERE id = ?', (poll_id,))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}



    def get_polls(self, admin_id=None):
        """Get polls, optionally filtered to a specific admin"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if admin_id is not None:
            cursor.execute('SELECT * FROM polls WHERE admin_id = ? ORDER BY id DESC', (admin_id,))
        else:
            cursor.execute('SELECT * FROM polls ORDER BY id DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_poll_by_id(self, poll_id):
        """Get poll by ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM polls WHERE id = ?', (poll_id,))
        poll = cursor.fetchone()
        conn.close()
        return dict(poll) if poll else None

    def update_poll(self, poll_id, title=None, description=None, poll_start_time=None, poll_end_time=None, allow_nota=None, admin_id=None):
        """Update poll details (admin_id enforces ownership if provided)"""
        try:
            # Ownership check
            if admin_id is not None:
                poll = self.get_poll_by_id(poll_id)
                if not poll or poll.get('admin_id') != admin_id:
                    return {'success': False, 'error': 'Poll not found or access denied'}

            fields = []
            values = []
            if title is not None:
                fields.append("title = ?")
                values.append(title)
            if description is not None:
                fields.append("description = ?")
                values.append(description)
            if poll_start_time is not None:
                fields.append("poll_start_time = ?")
                values.append(poll_start_time)
            if poll_end_time is not None:
                fields.append("poll_end_time = ?")
                values.append(poll_end_time)
            if allow_nota is not None:
                fields.append("allow_nota = ?")
                values.append(int(allow_nota))
            fields.append("updated_at = CURRENT_TIMESTAMP")

            conn = self.get_connection()
            cursor = conn.cursor()
            values.append(poll_id)
            query = f"UPDATE polls SET {', '.join(fields)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()
            conn.close()
            if allow_nota is not None and int(allow_nota) == 1:
                self.ensure_nota_candidate(poll_id)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def toggle_poll_status(self, poll_id, is_active, deactivate_others=False, admin_id=None):
        """Activate or deactivate a poll (admin_id enforces ownership if provided)"""
        try:
            # Ownership check
            if admin_id is not None:
                poll = self.get_poll_by_id(poll_id)
                if not poll or poll.get('admin_id') != admin_id:
                    return {'success': False, 'error': 'Poll not found or access denied'}

            conn = self.get_connection()
            cursor = conn.cursor()
            if is_active and deactivate_others:
                if admin_id is not None:
                    # Only deactivate this admin's polls
                    cursor.execute('UPDATE polls SET is_active = 0 WHERE admin_id = ?', (admin_id,))
                else:
                    cursor.execute('UPDATE polls SET is_active = 0')
            cursor.execute('''
                UPDATE polls
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (is_active, poll_id))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_active_poll(self, admin_id=None):
        """Get one active poll (latest), optionally scoped to an admin"""
        polls = self.get_active_polls(admin_id=admin_id)
        return polls[0] if polls else None

    def get_active_polls(self, admin_id=None):
        """Get all currently active polls, optionally scoped to an admin"""
        from datetime import datetime
        polls = self.get_polls(admin_id=admin_id)
        active = []
        now = datetime.now()
        for poll in polls:
            start_time = poll.get('poll_start_time')
            end_time = poll.get('poll_end_time')
            if start_time and end_time:
                try:
                    start_dt = datetime.fromisoformat(start_time)
                    end_dt = datetime.fromisoformat(end_time)
                except Exception:
                    continue
                if now > end_dt:
                    if poll.get('is_active') == 1:
                        self.toggle_poll_status(poll['id'], 0, deactivate_others=False)
                    continue
                if now < start_dt:
                    continue
                active.append(poll)
            else:
                if poll.get('is_active') == 1:
                    active.append(poll)
        return active

    def is_poll_active(self, poll_id=None):
        """Check if a poll (or any poll) is active based on schedule or manual toggle"""
        from datetime import datetime
        now = datetime.now()

        if poll_id is None:
            return len(self.get_active_polls()) > 0

        poll = self.get_poll_by_id(poll_id)
        if not poll:
            return False

        start_time = poll.get('poll_start_time')
        end_time = poll.get('poll_end_time')
        if start_time and end_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(end_time)
            except Exception:
                return False
            if now > end_dt:
                if poll.get('is_active') == 1:
                    self.toggle_poll_status(poll['id'], 0, deactivate_others=False)
                return False
            if now < start_dt:
                return False
            return True
        return poll.get('is_active') == 1
    
    # Vote receipts operations
    def store_receipt(self, user_id, candidate_id, poll_id, receipt_hash, pdf_path):
        """Store vote receipt information"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO vote_receipts (user_id, candidate_id, poll_id, receipt_hash, pdf_path)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, candidate_id, poll_id, receipt_hash, pdf_path))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_receipt_by_hash(self, receipt_hash):
        """Get receipt by hash"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vote_receipts WHERE receipt_hash = ?', (receipt_hash,))
        receipt = cursor.fetchone()
        conn.close()
        return dict(receipt) if receipt else None

    def get_latest_receipt_for_user(self, user_id):
        """Get latest receipt for a user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM vote_receipts
            WHERE user_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
        ''', (user_id,))
        receipt = cursor.fetchone()
        conn.close()
        return dict(receipt) if receipt else None

    def get_receipt_for_user_poll(self, user_id, poll_id):
        """Get latest receipt for a user within a poll"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM vote_receipts
            WHERE user_id = ? AND poll_id = ?
            ORDER BY generated_at DESC
            LIMIT 1
        ''', (user_id, poll_id))
        receipt = cursor.fetchone()
        conn.close()
        return dict(receipt) if receipt else None

    # Face recognition logs
    def log_face_attempt(self, user_id=None, context=None, detection_confidence=None, match_similarity=None, success=0, reason=None, winner_user_id=None, runner_up_user_id=None, winner_score=None, runner_up_score=None, score_margin=None, valid_frame_count=None, poll_id=None):
        """Log face recognition attempt for analytics and debugging."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO face_recognition_logs (
                    user_id, context, detection_confidence, match_similarity, success, reason,
                    winner_user_id, runner_up_user_id, winner_score, runner_up_score,
                    score_margin, valid_frame_count, poll_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, context, detection_confidence, match_similarity, success, reason,
                winner_user_id, runner_up_user_id, winner_score, runner_up_score,
                score_margin, valid_frame_count, poll_id
            ))
            conn.commit()
            conn.close()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_failed_attempts_count(self):
        """Get total failed face recognition attempts during voting"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM face_recognition_logs WHERE success = 0 AND context = 'vote'")
        count = cursor.fetchone()['count']
        conn.close()
        return count
    
    # Dashboard statistics operations
    def get_vote_statistics(self, poll_id=None, admin_id=None):
        """Get voting statistics scoped by poll and/or admin"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Build candidate scope clause
        if poll_id:
            cand_where = 'WHERE c.poll_id = ?'
            cand_params = (poll_id,)
            cursor.execute(f'SELECT SUM(vote_count) as total_votes FROM candidates WHERE poll_id = ?', (poll_id,))
        elif admin_id is not None:
            cand_where = 'JOIN polls p ON c.poll_id = p.id WHERE p.admin_id = ?'
            cand_params = (admin_id,)
            cursor.execute('''
                SELECT SUM(c.vote_count) as total_votes
                FROM candidates c JOIN polls p ON c.poll_id = p.id
                WHERE p.admin_id = ?
            ''', (admin_id,))
        else:
            cand_where = ''
            cand_params = ()
            cursor.execute('SELECT SUM(vote_count) as total_votes FROM candidates')

        total_votes_result = cursor.fetchone()
        total_votes = total_votes_result['total_votes'] if total_votes_result['total_votes'] else 0

        # Get total eligible voters scoped to the poll / admin
        if poll_id:
            cursor.execute(
                'SELECT COUNT(*) as total_voters FROM user_poll_access WHERE poll_id = ?',
                (poll_id,)
            )
        elif admin_id is not None:
            cursor.execute(
                '''SELECT COUNT(DISTINCT upa.user_id) as total_voters
                   FROM user_poll_access upa
                   JOIN polls p ON upa.poll_id = p.id
                   WHERE p.admin_id = ?''',
                (admin_id,)
            )
        else:
            cursor.execute('SELECT COUNT(*) as total_voters FROM users')
        total_voters = cursor.fetchone()['total_voters']

        turnout = (total_votes / total_voters * 100) if total_voters > 0 else 0

        # Candidate-wise chart data
        if cand_where:
            cursor.execute(f'''
                SELECT c.candidate_name, c.party_name, c.vote_count
                FROM candidates c {cand_where}
                ORDER BY c.vote_count DESC
            ''', cand_params)
        else:
            cursor.execute('SELECT candidate_name, party_name, vote_count FROM candidates ORDER BY vote_count DESC')
        candidates_data = cursor.fetchall()

        # Party-wise totals
        if cand_where:
            cursor.execute(f'''
                SELECT c.party_name, SUM(c.vote_count) as votes
                FROM candidates c {cand_where}
                GROUP BY c.party_name ORDER BY votes DESC
            ''', cand_params)
        else:
            cursor.execute('SELECT party_name, SUM(vote_count) as votes FROM candidates GROUP BY party_name ORDER BY votes DESC')
        parties_data = cursor.fetchall()

        # Failed face verification attempts scoped to poll/admin
        if poll_id:
            cursor.execute(
                "SELECT COUNT(*) as count FROM face_recognition_logs WHERE success = 0 AND context = 'vote' AND poll_id = ?",
                (poll_id,)
            )
        elif admin_id is not None:
            cursor.execute('''
                SELECT COUNT(*) as count
                FROM face_recognition_logs frl
                JOIN polls p ON frl.poll_id = p.id
                WHERE frl.success = 0 AND frl.context = 'vote' AND p.admin_id = ?
            ''', (admin_id,))
        else:
            cursor.execute("SELECT COUNT(*) as count FROM face_recognition_logs WHERE success = 0 AND context = 'vote'")
        failed_attempts = cursor.fetchone()['count']

        conn.close()
        return {
            'total_votes': total_votes,
            'total_voters': total_voters,
            'turnout_percentage': round(turnout, 2),
            'failed_attempts': failed_attempts,
            'candidates': [dict(c) for c in candidates_data],
            'votes_by_party': [dict(p) for p in parties_data]
        }
    
    def get_hourly_turnout(self, poll_id=None, admin_id=None):
        """Get hourly voting turnout data, optionally scoped by poll or admin"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if poll_id:
            cursor.execute('''
                SELECT strftime('%Y-%m-%d %H:00:00', voted_at) as hour, COUNT(*) as count
                FROM votes WHERE poll_id = ?
                GROUP BY hour ORDER BY hour
            ''', (poll_id,))
        elif admin_id is not None:
            cursor.execute('''
                SELECT strftime('%Y-%m-%d %H:00:00', v.voted_at) as hour, COUNT(*) as count
                FROM votes v JOIN polls p ON v.poll_id = p.id
                WHERE p.admin_id = ?
                GROUP BY hour ORDER BY hour
            ''', (admin_id,))
        else:
            cursor.execute('''
                SELECT strftime('%Y-%m-%d %H:00:00', voted_at) as hour, COUNT(*) as count
                FROM votes GROUP BY hour ORDER BY hour
            ''')
        hourly_data = cursor.fetchall()
        conn.close()
        return [dict(h) for h in hourly_data]
    
    # Updated candidate operations to include new fields
    def add_candidate_enhanced(self, candidate_name, party_name, party_symbol=None, poll_id=None, age=None, manifesto_path=None, description=None):
        """Add a new candidate with enhanced fields"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO candidates (candidate_name, party_name, party_symbol, poll_id, age, manifesto_path, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (candidate_name, party_name, party_symbol, poll_id, age, manifesto_path, description))
            
            conn.commit()
            candidate_id = cursor.lastrowid
            conn.close()
            return {'success': True, 'candidate_id': candidate_id}
        except Exception as e:
            return {'success': False, 'error': str(e)}

