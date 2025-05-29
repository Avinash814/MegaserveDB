from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import logging
import re
import json
import os

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

login_bp = Blueprint('login', __name__, template_folder='templates')

# Path to store users data
USERS_FILE = 'users.json'

# Initialize users dictionary
def load_users():
    """Load users from JSON file or initialize with default users"""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                users = json.load(f)
                logger.info(f"Loaded users from {USERS_FILE}: {users.keys()}")
                return users
        except Exception as e:
            logger.error(f"Error loading users file: {e}")
            # Fallback to default users if file is corrupted
    default_users = {
        "avinash@megaserve.tech": {
            "password": "admin123",  # Initial password, can be changed by user
            "role": "admin",
            "code": "2004"
        },
        "rajat@megaserve.tech": {
            "password": "user123",  # Initial password, can be changed by user
            "role": "user",
            "code": "2005"
        },
        "yash@megaserve.tech": {
            "password": "user123",  # Initial password, can be changed by user
            "role": "user",
            "code": "2006"
        },
        "vinod@megaserve.tech": {
            "password": "user123",  # Initial password, can be changed by user
            "role": "user",
            "code": "2007"
        },
        "bansi@megaserve.tech": {
            "password": "user123",  # Initial password, can be changed by user
            "role": "user",
            "code": "2008"
        }
    }
    logger.info("Using default users")
    save_users(default_users)  # Save defaults to file if file doesn't exist
    return default_users

def save_users(users):
    """Save users to JSON file with plain text passwords"""
    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=4)
        logger.info(f"Users data saved to {USERS_FILE}")
    except Exception as e:
        logger.error(f"Error saving users file: {e}")
        raise Exception(f"Failed to save users file: {e}")

# Load users at startup
users = load_users()

def is_valid_email(email):
    """Validate email format and domain"""
    pattern = r'^[a-zA-Z0-9._%+-]+@megaserve\.tech$'
    return re.match(pattern, email) is not None

@login_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login with plain text password comparison"""
    # Redirect authenticated users to their respective home pages
    if 'authenticated' in session and session['authenticated']:
        logger.debug(f"User already authenticated, redirecting based on role: {session.get('role')}")
        if session.get('role') == 'admin':
            return redirect(url_for('admin.admin_home'))
        elif session.get('role') == 'user':
            return redirect(url_for('user.user_home'))
        else:
            session.clear()  # Clear invalid role
            flash("Invalid role detected, please log in again", "error")

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        logger.debug(f"Login attempt - Email: '{email}', Password: '{password}'")
        if email:
            email = email.strip().lower()
        if password:
            password = password.strip()

        if not is_valid_email(email):
            logger.warning(f"Invalid email format or domain: {email}")
            flash("Email must be a valid @megaserve.tech address", "error")
            return render_template('login.html')

        logger.debug(f"Normalized - Email: '{email}', Password: '{password}'")
        if email in users and users[email]["password"] == password:
            logger.info(f"Login successful for user: {email}")
            session['role'] = users[email]["role"]
            session['authenticated'] = True
            session['email'] = email
            flash(f"Welcome, {email}! Logged in successfully", "success")
            redirect_url = url_for('admin.admin_home') if session['role'] == 'admin' else url_for('user.user_home')
            logger.debug(f"Redirecting to: {redirect_url}, Session: {session}")
            return redirect(redirect_url)
        else:
            logger.warning(f"Login failed for user: {email}")
            flash("Invalid email or password", "error")

    logger.debug(f"Rendering login page, Session: {session}")
    return render_template('login.html')

@login_bp.route('/logout', methods=['POST'])
def logout():
    """Clear session and log out user"""
    session.clear()
    flash("Logged out successfully", "success")
    logger.info("User logged out")
    return redirect(url_for('login.login'))

@login_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """Handle password reset with user-defined plain text password"""
    if request.method == 'POST':
        step = request.form.get('step')

        if step == 'verify_code':
            email = request.form.get('email')
            code = request.form.get('code')
            if email:
                email = email.strip().lower()

            if not is_valid_email(email):
                flash("Email must be a valid @megaserve.tech address", "error")
                return render_template('forgot_password.html', step='verify_code')

            if email not in users:
                flash("Email not found", "error")
                return render_template('forgot_password.html', step='verify_code')

            if code == users[email]["code"]:
                logger.info(f"Code verified for {email}")
                session['reset_email'] = email
                return render_template('forgot_password.html', step='reset_password', email=email)
            else:
                flash("Invalid code", "error")
                return render_template('forgot_password.html', step='verify_code')

        elif step == 'reset_password':
            email = session.get('reset_email')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not email or not is_valid_email(email):
                flash("Invalid session. Please start over.", "error")
                return render_template('forgot_password.html', step='verify_code')

            if new_password != confirm_password:
                flash("Passwords do not match", "error")
                return render_template('forgot_password.html', step='reset_password', email=email)

            if len(new_password) < 6:
                flash("Password must be at least 6 characters long", "error")
                return render_template('forgot_password.html', step='reset_password', email=email)

            # Update password with user-defined plain text password and save to file
            users[email]["password"] = new_password
            logger.debug(f"New user-defined password for {email}: {new_password}")
            try:
                save_users(users)
                logger.info(f"Password reset and saved for {email}")
            except Exception as e:
                logger.error(f"Failed to save password update: {e}")
                flash("Error saving new password. Please try again.", "error")
                return render_template('forgot_password.html', step='reset_password', email=email)

            session.clear()  # Clear session to avoid authentication conflicts
            flash("Password reset successfully. Please log in.", "success")
            logger.info(f"Password reset successful for {email}")
            return redirect(url_for('login.login'))

    return render_template('forgot_password.html', step='verify_code')

@login_bp.route('/add_user', methods=['GET', 'POST'])
def add_user():
    """Allow admin to add new users with user-defined plain text password"""
    # Restrict to admin only
    if 'authenticated' not in session or session.get('role') != 'admin':
        flash("You must be an admin to add users.", "error")
        return redirect(url_for('login.login'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        code = request.form.get('code')

        if email:
            email = email.strip().lower()
        if password:
            password = password.strip()
        if code:
            code = code.strip()

        # Validate inputs
        if not is_valid_email(email):
            flash("Email must be a valid @megaserve.tech address", "error")
            return render_template('add_user.html')

        if email in users:
            flash("Email already exists", "error")
            return render_template('add_user.html')

        if password != confirm_password:
            flash("Passwords do not match", "error")
            return render_template('add_user.html')

        if len(password) < 6:
            flash("Password must be at least 6 characters long", "error")
            return render_template('add_user.html')

        if not code:
            flash("Security code is required", "error")
            return render_template('add_user.html')

        # Add new user with user-defined plain text password
        users[email] = {
            "password": password,
            "role": "user",
            "code": code
        }
        logger.debug(f"New user added: {email}, Password: {password}, Code: {code}")
        try:
            save_users(users)
            logger.info(f"New user saved to {USERS_FILE}: {email}")
            flash(f"User {email} added successfully", "success")
            return redirect(url_for('login.add_user'))
        except Exception as e:
            logger.error(f"Failed to save new user: {e}")
            flash("Error saving new user. Please try again.", "error")
            return render_template('add_user.html')

    return render_template('add_user.html')