import os
import json
import datetime
import sqlite3
import logging
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary key for development fallback
_DEV_KEY = None

def get_fernet() -> Fernet:
    global _DEV_KEY
    key_str = os.environ.get("ENCRYPTION_KEY")
    if not key_str:
        if _DEV_KEY is None:
            _DEV_KEY = Fernet.generate_key()
            logger.warning("ENCRYPTION_KEY not set. Generated a temporary key for this run.")
        return Fernet(_DEV_KEY)
    try:
        return Fernet(key_str.encode())
    except Exception as e:
        logger.error(f"Failed to load ENCRYPTION_KEY: {e}. Generating a temporary one.")
        if _DEV_KEY is None:
            _DEV_KEY = Fernet.generate_key()
        return Fernet(_DEV_KEY)

def encrypt_data(data: str) -> str:
    f = get_fernet()
    return f.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    f = get_fernet()
    return f.decrypt(encrypted_data.encode()).decode()

class DBClient:
    def __init__(self):
        self.use_sqlite = False
        self.datastore_client = None
        
        # Check if we should force local SQLite database
        if os.environ.get("USE_LOCAL_MOCK_DB", "").lower() == "true":
            self.use_sqlite = True
            logger.info("USE_LOCAL_MOCK_DB is True. Forcing local SQLite database.")
        else:
            try:
                from google.cloud import datastore
                # This will succeed if credentials/project are configured
                self.datastore_client = datastore.Client()
                logger.info("Successfully initialized GCP Datastore client.")
            except Exception as e:
                logger.warning(f"Could not initialize GCP Datastore client: {e}. Falling back to SQLite.")
                self.use_sqlite = True

        if self.use_sqlite:
            self.db_path = os.environ.get("SQLITE_DB_PATH", "local_database.db")
            self._init_sqlite()

    def _init_sqlite(self):
        """Initialize SQLite tables for token caching, auth flows, and requests."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Token caches table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_caches (
                home_account_id TEXT PRIMARY KEY,
                token_cache TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Auth flows table (for stateful OAuth in stateless environments)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_flows (
                state TEXT PRIMARY KEY,
                flow TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Booking requests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS booking_requests (
                request_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                attendees TEXT NOT NULL,
                room_email TEXT NOT NULL,
                room_name TEXT NOT NULL,
                requester_id TEXT NOT NULL,
                requester_email TEXT NOT NULL,
                requester_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TEXT,
                approver_email TEXT,
                expires_at TEXT
            )
        """)
        # Migration: add expires_at column to existing databases that don't have it
        try:
            cursor.execute("ALTER TABLE booking_requests ADD COLUMN expires_at TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists, safe to ignore
        conn.commit()
        conn.close()

    # --- Token Cache Persistence ---
    def get_token_cache(self, home_account_id: str) -> Optional[str]:
        """Loads and decrypts the serialized token cache from the database."""
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT token_cache FROM token_caches WHERE home_account_id = ?", (home_account_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                try:
                    return decrypt_data(row[0])
                except Exception as e:
                    logger.error(f"Failed to decrypt token cache for {home_account_id}: {e}")
                    return None
            return None
        else:
            try:
                key = self.datastore_client.key('TokenCache', home_account_id)
                entity = self.datastore_client.get(key)
                if entity and 'token_cache' in entity:
                    return decrypt_data(entity['token_cache'])
            except Exception as e:
                logger.error(f"Error fetching token cache from Datastore: {e}")
            return None

    def save_token_cache(self, home_account_id: str, serialized_cache: str) -> None:
        """Encrypts and saves the serialized token cache to the database."""
        encrypted_cache = encrypt_data(serialized_cache)
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO token_caches (home_account_id, token_cache, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (home_account_id, encrypted_cache))
            conn.commit()
            conn.close()
        else:
            try:
                from google.cloud import datastore
                key = self.datastore_client.key('TokenCache', home_account_id)
                # exclude_from_indexes=True is REQUIRED: token cache JSON exceeds 1500-byte index limit
                entity = datastore.Entity(key=key, exclude_from_indexes=['token_cache'])
                entity.update({
                    'token_cache': encrypted_cache,
                    'updated_at': datetime.datetime.now(datetime.timezone.utc)
                })
                self.datastore_client.put(entity)
            except Exception as e:
                logger.error(f"Error saving token cache to Datastore: {e}")

    # --- Auth Flow Persistence ---
    def get_auth_flow(self, state: str) -> Optional[Dict]:
        """Retrieves and deserializes the auth flow using state as the key."""
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT flow FROM auth_flows WHERE state = ?", (state,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
            return None
        else:
            try:
                key = self.datastore_client.key('AuthFlow', state)
                entity = self.datastore_client.get(key)
                if entity and 'flow' in entity:
                    return json.loads(entity['flow'])
            except Exception as e:
                logger.error(f"Error fetching auth flow from Datastore: {e}")
            return None

    def save_auth_flow(self, state: str, flow_dict: dict) -> None:
        """Saves the auth flow object linked with state parameter."""
        flow_json = json.dumps(flow_dict)
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO auth_flows (state, flow, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (state, flow_json))
            conn.commit()
            conn.close()
        else:
            try:
                from google.cloud import datastore
                key = self.datastore_client.key('AuthFlow', state)
                # exclude_from_indexes=True: flow JSON may exceed the 1500-byte indexed limit
                entity = datastore.Entity(key=key, exclude_from_indexes=['flow'])
                entity.update({
                    'flow': flow_json,
                    'created_at': datetime.datetime.now(datetime.timezone.utc)
                })
                self.datastore_client.put(entity)
            except Exception as e:
                logger.error(f"Error saving auth flow to Datastore: {e}")

    def delete_auth_flow(self, state: str) -> None:
        """Deletes the auth flow from database after code exchange completes."""
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM auth_flows WHERE state = ?", (state,))
            conn.commit()
            conn.close()
        else:
            try:
                key = self.datastore_client.key('AuthFlow', state)
                self.datastore_client.delete(key)
            except Exception as e:
                logger.error(f"Error deleting auth flow from Datastore: {e}")

    # --- Booking Request Persistence ---
    def save_booking_request(self, request_id: str, request_data: dict) -> None:
        """Saves a new booking request in Firestore/SQLite."""
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # expires_at: 7 days from now (security: approval links expire)
            expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)).isoformat()
            cursor.execute("""
                INSERT OR REPLACE INTO booking_requests 
                (request_id, subject, start_time, end_time, attendees, room_email, room_name, requester_id, requester_email, requester_name, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """, (
                request_id,
                request_data['subject'],
                request_data['start_time'],
                request_data['end_time'],
                ",".join(request_data['attendees']),
                request_data['room_email'],
                request_data['room_name'],
                request_data['requester_id'],
                request_data['requester_email'],
                request_data['requester_name'],
                request_data.get('status', 'pending'),
                expires_at
            ))
            conn.commit()
            conn.close()
        else:
            try:
                from google.cloud import datastore
                expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
                key = self.datastore_client.key('BookingRequest', request_id)
                # exclude_from_indexes for large text fields
                entity = datastore.Entity(key=key, exclude_from_indexes=['subject'])
                entity.update({
                    'subject': request_data['subject'],
                    'start_time': request_data['start_time'],
                    'end_time': request_data['end_time'],
                    'attendees': request_data['attendees'],
                    'room_email': request_data['room_email'],
                    'room_name': request_data['room_name'],
                    'requester_id': request_data['requester_id'],
                    'requester_email': request_data['requester_email'],
                    'requester_name': request_data['requester_name'],
                    'status': request_data.get('status', 'pending'),
                    'created_at': datetime.datetime.now(datetime.timezone.utc),
                    'expires_at': expires_at
                })
                self.datastore_client.put(entity)
            except Exception as e:
                logger.error(f"Error saving booking request to Datastore: {e}")

    def get_booking_request(self, request_id: str) -> Optional[Dict]:
        """Retrieves booking request details by ID."""
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM booking_requests WHERE request_id = ?", (request_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    'request_id': row[0],
                    'subject': row[1],
                    'start_time': row[2],
                    'end_time': row[3],
                    'attendees': row[4].split(",") if row[4] else [],
                    'room_email': row[5],
                    'room_name': row[6],
                    'requester_id': row[7],
                    'requester_email': row[8],
                    'requester_name': row[9],
                    'status': row[10],
                    'created_at': row[11],
                    'processed_at': row[12],
                    'approver_email': row[13],
                    'expires_at': row[14] if len(row) > 14 else None
                }
            return None
        else:
            try:
                key = self.datastore_client.key('BookingRequest', request_id)
                entity = self.datastore_client.get(key)
                if entity:
                    return {
                        'request_id': request_id,
                        'subject': entity.get('subject'),
                        'start_time': entity.get('start_time'),
                        'end_time': entity.get('end_time'),
                        'attendees': entity.get('attendees', []),
                        'room_email': entity.get('room_email'),
                        'room_name': entity.get('room_name'),
                        'requester_id': entity.get('requester_id'),
                        'requester_email': entity.get('requester_email'),
                        'requester_name': entity.get('requester_name'),
                        'status': entity.get('status'),
                        'created_at': entity.get('created_at').isoformat() if hasattr(entity.get('created_at'), 'isoformat') else entity.get('created_at'),
                        'processed_at': entity.get('processed_at').isoformat() if hasattr(entity.get('processed_at'), 'isoformat') else entity.get('processed_at'),
                        'approver_email': entity.get('approver_email'),
                        'expires_at': entity.get('expires_at').isoformat() if hasattr(entity.get('expires_at'), 'isoformat') else entity.get('expires_at')
                    }
            except Exception as e:
                logger.error(f"Error fetching booking request from Datastore: {e}")
            return None

    def update_booking_request_status(self, request_id: str, status: str, approver_email: Optional[str] = None) -> None:
        """Updates the status of a booking request."""
        processed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if self.use_sqlite:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE booking_requests 
                SET status = ?, processed_at = ?, approver_email = ? 
                WHERE request_id = ?
            """, (status, processed_at, approver_email, request_id))
            conn.commit()
            conn.close()
        else:
            try:
                key = self.datastore_client.key('BookingRequest', request_id)
                entity = self.datastore_client.get(key)
                if entity:
                    entity.update({
                        'status': status,
                        'processed_at': datetime.datetime.now(datetime.timezone.utc),
                        'approver_email': approver_email
                    })
                    self.datastore_client.put(entity)
            except Exception as e:
                logger.error(f"Error updating booking request in Datastore: {e}")
