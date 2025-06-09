import logging
import re
from flask import session
from passlib.hash import bcrypt
from sqlalchemy import text
from utils import get_db_connection

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Auth:
    """Handle user authentication and role management"""
    
    @staticmethod
    def init_db():
        """Initialize MySQL database with auth table if it doesn't exist"""
        engine = get_db_connection()
        if not engine:
            logger.error("Failed to initialize database: No connection")
            raise Exception("Database connection failed")
        
        try:
            with engine.connect() as connection:
                # Create auth table if it doesn't exist
                connection.execute(text('''
                    CREATE TABLE IF NOT EXISTS auth (
                        email VARCHAR(255) PRIMARY KEY,
                        password VARCHAR(255) NOT NULL,
                        role VARCHAR(50) NOT NULL,
                        code VARCHAR(50) NOT NULL
                    )
                '''))
                # Check if default users exist
                result = connection.execute(text("SELECT COUNT(*) FROM auth"))
                if result.fetchone()[0] == 0:
                    default_users = [
                        ("avinash@megaserve.tech", bcrypt.hash("admin123"), "admin", "2004"),
                        ("rajat@megaserve.tech", bcrypt.hash("user123"), "admin", "2005"),
                        ("yash@megaserve.tech", bcrypt.hash("user123"), "user", "2006"),
                        ("vinod@megaserve.tech", bcrypt.hash("user123"), "user", "2007"),
                        ("bansi@megaserve.tech", bcrypt.hash("user123"), "user", "2008")
                    ]
                    connection.execute(
                        text("INSERT INTO auth (email, password, role, code) VALUES (:email, :password, :role, :code)"),
                        [{"email": email, "password": password, "role": role, "code": code} for email, password, role, code in default_users]
                    )
                    connection.commit()
                    logger.info("Initialized auth table with default users")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise Exception(f"Failed to initialize database: {e}")

    @staticmethod
    def is_valid_email(email):
        """Validate email format and domain"""
        pattern = r'^[a-zA-Z0-9._%+-]+@megaserve\.tech$'
        return bool(re.match(pattern, email))

    @staticmethod
    def authenticate(email, password):
        """Authenticate user with hashed password"""
        engine = get_db_connection()
        if not engine:
            logger.error("Authentication failed: No database connection")
            return None
        
        try:
            with engine.connect() as connection:
                email = email.strip().lower() if email else ""
                password = password.strip() if password else ""
                
                result = connection.execute(
                    text("SELECT password, role FROM auth WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                
                if result and bcrypt.verify(password, result[0]):
                    logger.info(f"Authentication successful for {email}")
                    return {
                        "email": email,
                        "role": result[1],
                        "authenticated": True
                    }
                logger.warning(f"Authentication failed for {email}")
                return None
        except Exception as e:
            logger.error(f"Database error during authentication: {e}")
            return None

    @staticmethod
    def verify_code(email, code):
        """Verify security code for password reset"""
        engine = get_db_connection()
        if not engine:
            logger.error("Code verification failed: No database connection")
            return False
        
        try:
            with engine.connect() as connection:
                email = email.strip().lower() if email else ""
                result = connection.execute(
                    text("SELECT code FROM auth WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                return result and result[0] == code
        except Exception as e:
            logger.error(f"Database error during code verification: {e}")
            return False

    @staticmethod
    def reset_password(email, new_password):
        """Update user password in database"""
        engine = get_db_connection()
        if not engine:
            logger.error("Password reset failed: No database connection")
            return False
        
        try:
            with engine.connect() as connection:
                email = email.strip().lower() if email else ""
                result = connection.execute(
                    text("SELECT email FROM auth WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                if result:
                    hashed_password = bcrypt.hash(new_password)
                    connection.execute(
                        text("UPDATE auth SET password = :password WHERE email = :email"),
                        {"password": hashed_password, "email": email}
                    )
                    connection.commit()
                    logger.info(f"Password reset for {email}")
                    return True
                logger.warning(f"Password reset failed: {email} not found")
                return False
        except Exception as e:
            logger.error(f"Database error during password reset: {e}")
            return False

    @staticmethod
    def add_user(email, password, code, role="user"):
        """Add new user to database"""
        engine = get_db_connection()
        if not engine:
            logger.error("User addition failed: No database connection")
            return False
        
        try:
            with engine.connect() as connection:
                email = email.strip().lower() if email else ""
                if not Auth.is_valid_email(email):
                    logger.warning(f"Invalid email format: {email}")
                    return False
                result = connection.execute(
                    text("SELECT email FROM auth WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                if result:
                    logger.warning(f"User {email} already exists")
                    return False
                hashed_password = bcrypt.hash(password)
                connection.execute(
                    text("INSERT INTO auth (email, password, role, code) VALUES (:email, :password, :role, :code)"),
                    {"email": email, "password": hashed_password, "role": role, "code": code}
                )
                connection.commit()
                logger.info(f"Added new user: {email}")
                return True
        except Exception as e:
            logger.error(f"Database error during user addition: {e}")
            return False

    @staticmethod
    def get_user_role(email):
        """Get user role"""
        engine = get_db_connection()
        if not engine:
            logger.error("Role retrieval failed: No database connection")
            return None
        
        try:
            with engine.connect() as connection:
                email = email.strip().lower() if email else ""
                result = connection.execute(
                    text("SELECT role FROM auth WHERE email = :email"),
                    {"email": email}
                ).fetchone()
                return result[0] if result else None
        except Exception as e:
            logger.error(f"Database error during role retrieval: {e}")
            return None

    @staticmethod
    def logout():
        """Clear session"""
        session.clear()
        logger.info("User logged out")