from flask import Blueprint, render_template, session, redirect, url_for, flash, request
from functools import wraps
from auth import Auth
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

super_admin = Blueprint('super_admin', __name__)

# List of allowed email IDs for general access (includes avinash@megaserve.tech)
ALLOWED_EMAILS = [
    'admin1@megaserve.tech',
    'superadmin@megaserve.tech',
    'user1@megaserve.tech',
    'avinash@megaserve.tech'
]

# Decorator to restrict access based on email
def restrict_email(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session or session['email'] not in ALLOWED_EMAILS:
            flash('Access denied: Unauthorized email.', 'danger')
            return render_template('super_admin.html', is_authorized=False)
        return f(*args, **kwargs)
    return decorated_function

# Decorator to restrict user management to avinash@megaserve.tech with super_admin role
def restrict_super_admin_email(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session or session['email'] != 'avinash@megaserve.tech' or session['role'] != 'super_admin':
            flash('Access denied: Only avinash@megaserve.tech with super_admin role can access this page.', 'danger')
            return redirect(url_for('dashboard.dashboard_route'))
        return f(*args, **kwargs)
    return decorated_function

# Super admin user and role management route
@super_admin.route('/super_admin', methods=['GET', 'POST'])
@restrict_email
@restrict_super_admin_email
def super_admin():
    try:
        # Initialize database
        Auth.init_db()
    except Exception as e:
        flash("Database initialization failed. Please try again later.", "danger")
        logger.error(f"Database initialization failed: {e}")
        return render_template('super_admin.html', users=[], is_authorized=True)

    # Fetch all users from the database
    try:
        users = Auth.get_all_users()  # Assumes Auth.get_all_users() returns list of user dicts
        if not users:
            logger.info("No users found in the database.")
    except Exception as e:
        flash("Error fetching users. Please try again.", "danger")
        logger.error(f"Error fetching users: {e}")
        users = []

    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')

        if action == 'add_user':
            password = request.form.get('password')
            confirm_password = request.form.get('confirm_password')
            code = request.form.get('code')
            role = request.form.get('role')

            # Validate inputs
            if not Auth.is_valid_email(email):
                flash("Email must be a valid @megaserve.tech address", "danger")
            elif password != confirm_password:
                flash('Passwords do not match.', 'danger')
            elif len(password) < 6:
                flash("Password must be at least 6 characters long", "danger")
            elif not code:
                flash("Security code is required", "danger")
            elif role not in ['user', 'admin', 'super_admin']:
                flash("Invalid role selected", "danger")
            else:
                try:
                    if Auth.add_user(email, password, code, role):
                        flash(f'User {email} added successfully.', 'success')
                        logger.info(f"User {email} added with role {role}")
                    else:
                        flash('Error adding user. Email may already exist.', 'danger')
                        logger.warning(f"Failed to add user {email}: Email may already exist")
                except Exception as e:
                    flash("Error adding user. Please try again.", "danger")
                    logger.error(f"Error adding user {email}: {e}")

        elif action == 'delete_user':
            if email == session['email']:
                flash("You cannot delete your own account.", "danger")
            else:
                try:
                    if Auth.delete_user(email):
                        flash(f'User {email} deleted successfully.', 'success')
                        logger.info(f"User {email} deleted by {session['email']}")
                    else:
                        flash('Error deleting user. User may not exist.', 'danger')
                        logger.warning(f"Failed to delete user {email}")
                except Exception as e:
                    flash("Error deleting user. Please try again.", "danger")
                    logger.error(f"Error deleting user {email}: {e}")

        elif action == 'update_role':
            new_role = request.form.get('new_role')
            if email == session['email']:
                flash("You cannot change your own role.", "danger")
            elif new_role not in ['user', 'admin', 'super_admin']:
                flash("Invalid role selected", "danger")
            else:
                try:
                    if Auth.update_role(email, new_role):
                        flash(f"Role updated to {new_role.capitalize()} for {email}.", 'success')
                        logger.info(f"Role updated to {new_role} for {email} by {session['email']}")
                    else:
                        flash('Error updating role. User may not exist.', 'danger')
                        logger.warning(f"Failed to update role for {email}")
                except Exception as e:
                    flash("Error updating role. Please try again.", "danger")
                    logger.error(f"Error updating role for {email}: {e}")

        return redirect(url_for('super_admin.super_admin'))

    return render_template('super_admin.html', users=users, is_authorized=True)