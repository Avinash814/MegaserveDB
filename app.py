from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_caching import Cache
from flask_compress import Compress
from sqlalchemy import text, create_engine, String, Float, Integer, Date
import os
import pandas as pd
import threading
import io
import json
import math
import mysql.connector
import logging
from datetime import datetime
from utils import get_db_function, get_tables, get_table_columns, logger
from mapping import table_mappings, normalize_column_name, ob_column_mapping
from login import login_bp
from admin import admin_bp
from user import user_bp
import glob
import hashlib
import zipfile

app = Flask(__name__, template_folder='templates')
app.secret_key = 'your_secret_key'

def zip_filter(*args, **kwargs):
    return zip(*args, **kwargs)

app.jinja_env.filters['zip'] = zip_filter
print("Zip filter registered:", 'zip' in app.jinja_env.filters)

cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})
Compress(app)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'amanrai123',
    'database': 'MST'
}

@cache.memoize(timeout=300)
def get_tables_cached(prefix=None):
    return get_tables(prefix)

@cache.memoize(timeout=300)
def get_table_columns_cached(table):
    return get_table_columns(table)

def check_index_exists(connection, table_name, index_name):
    try:
        result = connection.execute(
            text(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = '{index_name}'")
        )
        return result.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking index {index_name} on table {table_name}: {str(e)}")
        return False

def get_column_type(connection, table_name, column_name):
    try:
        result = connection.execute(
            text(f"SHOW COLUMNS FROM `{table_name}` WHERE Field = '{column_name}'")
        )
        row = result.fetchone()
        return row[1] if row else None
    except Exception as e:
        logger.error(f"Error checking column type for {column_name} in {table_name}: {str(e)}")
        return None

def get_db_connection():
    try:
        return create_engine(
            f"mysql+mysqlconnector://{db_config['user']}:{db_config['password']}@{db_config['host']}/{db_config['database']}",
            pool_size=10,
            max_overflow=20,
            pool_timeout=30
        )
    except Exception as e:
        logger.error(f"Failed to connect to database: {str(e)}")
        return None

def get_existing_tables():
    engine = get_db_connection()
    if engine is None:
        return []
    try:
        connection = engine.raw_connection()
        cursor = connection.cursor()
        cursor.execute("SHOW TABLES")
        tables = [table[0] for table in cursor.fetchall()]
        cursor.close()
        connection.close()
        logger.info(f"Retrieved tables: {tables}")
        return tables
    except Exception as e:
        logger.error(f"Error fetching tables: {str(e)}")
        return []

def create_new_table(table_name):
    engine = get_db_connection()
    if engine is None:
        return False, "Failed to connect to database", "error"
    
    try:
        connection = engine.raw_connection()
        cursor = connection.cursor()
        
        primary_key = 'id' if table_name.lower() == 'upload_log' else 'row_id'
        create_query = f"""
        CREATE TABLE IF NOT EXISTS `{table_name}` (
            {primary_key} INT AUTO_INCREMENT PRIMARY KEY
        ) ENGINE=InnoDB;
        """
        cursor.execute(create_query)
        connection.commit()
        cursor.close()
        connection.close()
        logger.info(f"Table '{table_name}' created successfully")
        return True, f"Table '{table_name}' created! Columns will be added based on uploaded files.", "success"
    except Exception as e:
        logger.error(f"Error creating table '{table_name}': {str(e)}")
        return False, f"Error creating table: {str(e)}", "error"

def create_upload_log_table():
    engine = get_db_connection()
    if engine is None:
        logger.error("Failed to connect to database while creating upload_log table")
        return False, "Failed to connect to database", "error"
    
    try:
        with engine.connect() as connection:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS upload_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    table_name VARCHAR(255) NOT NULL,
                    upload_time DATETIME NOT NULL,
                    uploaded_by VARCHAR(255) NOT NULL,
                    file_hash VARCHAR(64) NOT NULL,
                    file_name VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB;
            """))
            connection.commit()
            logger.info("Upload log table created or verified successfully")
            return True, "Upload log table ready", "success"
    except Exception as e:
        logger.error(f"Error creating upload_log table: {type(e).__name__} - {str(e)}")
        return False, f"Error creating upload_log table: {str(e)}", "error"

def log_upload(table_name, uploaded_by, file_hash, file_name):
    engine = get_db_connection()
    if engine is None:
        logger.error("Failed to connect to database while logging upload")
        return False
    
    try:
        with engine.connect() as connection:
            connection.execute(
                text("INSERT INTO upload_log (table_name, upload_time, uploaded_by, file_hash, file_name) VALUES (:table_name, :upload_time, :uploaded_by, :file_hash, :file_name)"),
                {"table_name": table_name, "upload_time": datetime.now(), "uploaded_by": uploaded_by, "file_hash": file_hash, "file_name": file_name}
            )
            connection.commit()
            logger.info(f"Logged upload for table {table_name} by {uploaded_by} with file hash {file_hash} and file name {file_name}")
            return True
    except Exception as e:
        logger.error(f"Error logging upload for table {table_name}: {type(e).__name__} - {str(e)}")
        return False

def check_file_exists(file_path, table_name, file_name):
    try:
        with open(file_path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        
        engine = get_db_connection()
        if engine is None:
            logger.error("Database connection failed in check_file_exists")
            return True, "Database connection failed", file_hash
        
        try:
            with engine.connect() as connection:
                result = connection.execute(
                    text("SELECT COUNT(*) FROM upload_log WHERE table_name = :table_name AND file_hash = :file_hash"),
                    {"table_name": table_name.lower(), "file_hash": file_hash}
                )
                count = result.scalar()
                if count > 0:
                    logger.warning(f"Duplicate file detected: {file_name} (hash: {file_hash}) already uploaded to {table_name}")
                    return True, f"File {file_name} has already been uploaded to {table_name}", file_hash
                return False, None, file_hash
        except Exception as e:
            if "Table 'mst.upload_log' doesn't exist" in str(e):
                logger.warning(f"upload_log table does not exist, attempting to create it")
                success, msg, category = create_upload_log_table()
                if category == "success":
                    logger.info("Retrying file existence check after creating upload_log table")
                    with engine.connect() as connection:
                        result = connection.execute(
                            text("SELECT COUNT(*) FROM upload_log WHERE table_name = :table_name AND file_hash = :file_hash"),
                            {"table_name": table_name.lower(), "file_hash": file_hash}
                        )
                        count = result.scalar()
                        if count > 0:
                            logger.warning(f"Duplicate file detected after table creation: {file_name} (hash: {file_hash}) already uploaded to {table_name}")
                            return True, f"File {file_name} has already been uploaded to {table_name}", file_hash
                        return False, None, file_hash
                else:
                    logger.error(f"Failed to create upload_log table: {msg}")
                    return True, f"Cannot check for duplicate file {file_name}: {msg}", file_hash
            else:
                logger.error(f"Unexpected error checking file hash for {file_path}: {str(e)}")
                return True, f"Error checking file {file_name}: {str(e)}", file_hash
    except Exception as e:
        logger.error(f"Error reading file {file_path} for hash calculation: {str(e)}")
        return True, f"Error reading file {file_name}: {str(e)}", None

def check_column_alias_conflict(columns, column_mapping):
    normalized_to_original = {}
    for col in columns:
        normalized = normalize_column_name(col, column_mapping)
        if normalized == col.lower():
            continue
        if normalized in normalized_to_original and normalized_to_original[normalized] != col:
            logger.warning(f"Column alias conflict: '{col}' normalizes to '{normalized}', which conflicts with '{normalized_to_original[normalized]}'")
            return True, col, normalized_to_original[normalized]
        normalized_to_original[normalized] = col

    alias_map = {}
    for mapped_col, variations in column_mapping.items():
        if isinstance(variations, dict) and 'aliases' in variations:
            aliases = variations.get('aliases', [])
        elif isinstance(variations, list):
            aliases = variations
        else:
            aliases = []
        aliases = [str(a).strip().lower() for a in aliases]
        if mapped_col.lower() not in aliases:
            aliases.append(mapped_col.lower())
        for alias in aliases:
            normalized = normalize_column_name(alias, column_mapping)
            if normalized == mapped_col.lower():
                continue
            if normalized in alias_map and alias_map[normalized] != mapped_col:
                logger.warning(f"Alias conflict in mapping: '{alias}' for '{mapped_col}' normalizes to '{normalized}', which conflicts with '{alias_map[normalized]}'")
                return True, alias, alias_map[normalized]
            alias_map[normalized] = mapped_col
    return False, None, None

def check_unmapped_columns(columns, column_mapping, file_name, table_name):
    unmapped_columns = []
    valid_columns = set()
    
    for mapped_col, variations in column_mapping.items():
        valid_columns.add(mapped_col.lower())
        if isinstance(variations, dict) and 'aliases' in variations:
            aliases = variations.get('aliases', [])
        elif isinstance(variations, list):
            aliases = variations
        else:
            aliases = []
        for alias in aliases:
            valid_columns.add(str(alias).strip().lower())
    
    for col in columns:
        if table_name.lower() == 'ob' and col.lower().startswith('unnamed:'):
            continue
        normalized_col = normalize_column_name(col, column_mapping).lower()
        if normalized_col not in valid_columns and col.lower() not in valid_columns:
            unmapped_columns.append(col)
    
    if unmapped_columns:
        logger.warning(f"File {file_name} has columns not in mapping: {unmapped_columns}")
        return True, f"File {file_name} has columns not in mapping: {', '.join(unmapped_columns)}"
    return False, None

def get_latest_uploads(limit=5):
    engine = get_db_connection()
    if engine is None:
        logger.error("Failed to connect to database while fetching latest uploads")
        return []
    
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT id, table_name, upload_time, uploaded_by, file_name FROM upload_log ORDER BY upload_time DESC LIMIT :limit"),
                {"limit": limit}
            )
            uploads = [dict(row._mapping) for row in result.fetchall()]
            return uploads
    except Exception as e:
        logger.error(f"Error fetching latest uploads: {type(e).__name__} - {str(e)}")
        return []

def extract_server_and_date(filename):
    filename = os.path.splitext(filename)[0]
    parts = filename.split()
    server = parts[0] if parts else 'unknown'
    if len(parts) < 4:
        logger.error(f"Filename {filename} does not have enough parts for date extraction")
        return server, None
    
    date_parts = parts[1:4]
    try:
        day, month, year = [str(part) for part in date_parts]
        month = month[:3].lower()
        
        month_to_number = {
            'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
            'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
            'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
        }
        
        if month not in month_to_number:
            logger.error(f"Invalid month abbreviation in {filename}: {month}")
            return server, None
        
        if not day.isdigit() or not year.isdigit():
            logger.error(f"Invalid day or year in {filename}: day={day}, year={year}")
            return server, None
            
        date_str = f"{year}-{month_to_number[month]}-{day.zfill(2)}"
        return server, datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError as e:
        logger.error(f"Invalid date format in {filename}: {str(e)}")
        return server, None
    except Exception as e:
        logger.error(f"Error parsing date from {filename}: {str(e)}")
        return server, None

def create_table(table_name):
    table_name = table_name.lower()
    if not table_name.isalnum():
        return False, "Table name must be alphanumeric", "error"
    engine = get_db_connection()
    if not engine:
        return False, "Database connection failed", "error"
    try:
        with engine.connect() as connection:
            with connection.begin():
                primary_key = 'id' if table_name == 'upload_log' else 'row_id'
                connection.execute(text(f"CREATE TABLE IF NOT EXISTS `{table_name}` ({primary_key} INT AUTO_INCREMENT PRIMARY KEY)"))
                logger.info(f"Created table `{table_name}` with {primary_key}")

                if table_name in table_mappings:
                    mapping = table_mappings[table_name]
                    existing_columns = set(col.lower() for col in get_table_columns(table_name))
                    for col, info in mapping.items():
                        if col.lower() not in existing_columns:
                            try:
                                if col == primary_key:
                                    continue
                                mysql_type = info.get("datatype")
                                if not isinstance(mysql_type, str):
                                    logger.warning(f"Invalid datatype for column `{col}` in `{table_name}`, skipping column")
                                    continue
                                if table_name.lower() == 'gridlog' and col == 'timestamp':
                                    mysql_type = "TEXT"
                                elif table_name.lower() == 'gridlog' and col == 'date':
                                    mysql_type = "DATE"
                                connection.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{col}` {mysql_type}"))
                                existing_columns.add(col.lower())
                                if col in ['server', 'date']:
                                    index_name = f"idx_{table_name}_{col}"
                                    if not check_index_exists(connection, table_name, index_name):
                                        if col == 'date':
                                            connection.execute(text(f"CREATE INDEX {index_name} ON `{table_name}` (`{col}`)"))
                                        else:
                                            prefix_length = 255
                                            connection.execute(text(f"CREATE INDEX {index_name} ON `{table_name}` (`{col}`({prefix_length}))"))
                            except Exception as e:
                                if 'Duplicate column name' in str(e):
                                    logger.warning(f"Column `{col}` already exists in `{table_name}`, skipping addition")
                                else:
                                    logger.error(f"Error adding column `{col}` to `{table_name}`: {type(e).__name__} - {str(e)}")
                                    raise
                else:
                    logger.info(f"No predefined mapping for table `{table_name}`, using default structure with {primary_key} only")
        
        logger.info(f"Table '{table_name}' created successfully")
        cache.delete_memoized(get_tables_cached)
        cache.delete_memoized(get_table_columns_cached, table_name)
        return True, f"Table '{table_name}' created successfully!", "success"
    except Exception as e:
        logger.error(f"Error creating table {table_name}: {type(e).__name__} - {str(e)}")
        return False, f"Error creating table: {type(e).__name__} - {str(e)}", "error"

def initialize_predefined_tables():
    predefined_tables = ['orderbook', 'users', 'portfolios', 'ob', 'strategytags', 'legs', 'multilegorders', 'positions', 'gridlog']
    engine = get_db_connection()
    if not engine:
        logger.error("Database connection failed during predefined tables initialization")
        raise RuntimeError("Database connection failed during initialization")
    
    with engine.connect() as connection:
        existing_tables = get_tables()
        for table_name in predefined_tables:
            table_name_lower = table_name.lower()
            if table_name_lower not in existing_tables:
                try:
                    with connection.begin():
                        success, msg, category = create_table(table_name_lower)
                        if not success:
                            raise RuntimeError(msg)
                    logger.info(f"Table '{table_name_lower}' created successfully")
                except Exception as e:
                    logger.error(f"Error creating table {table_name_lower}: {type(e).__name__} - {str(e)}")
                    raise RuntimeError(f"Failed to initialize predefined table '{table_name_lower}': {type(e).__name__} - {str(e)}")
            else:
                try:
                    with connection.begin():
                        if table_name_lower in table_mappings:
                            mapping = table_mappings[table_name_lower]
                            existing_columns = set(col.lower() for col in get_table_columns(table_name_lower))
                            for col, info in mapping.items():
                                if col.lower() not in existing_columns:
                                    try:
                                        mysql_type = info.get("datatype")
                                        if not isinstance(mysql_type, str):
                                            logger.warning(f"Invalid datatype for column `{col}` in `{table_name_lower}`, skipping column")
                                            continue
                                        if table_name_lower == 'gridlog' and col == 'timestamp':
                                            mysql_type = "TEXT"
                                        elif table_name_lower == 'gridlog' and col == 'date':
                                            mysql_type = "DATE"
                                        logger.info(f"Adding column `{col}` to table `{table_name_lower}` with type {mysql_type}")
                                        connection.execute(text(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` {mysql_type}"))
                                        existing_columns.add(col.lower())
                                        if col in ['server', 'date']:
                                            index_name = f"idx_{table_name_lower}_{col}"
                                            if not check_index_exists(connection, table_name_lower, index_name):
                                                if col == 'date':
                                                    connection.execute(text(f"CREATE INDEX {index_name} ON `{table_name_lower}` (`{col}`)"))
                                                else:
                                                    prefix_length = 255
                                                    connection.execute(text(f"CREATE INDEX {index_name} ON `{table_name_lower}` (`{col}`({prefix_length}))"))
                                    except Exception as e:
                                        if 'Duplicate column name' in str(e):
                                            logger.warning(f"Column `{col}` already exists in `{table_name_lower}`, skipping addition")
                                        else:
                                            logger.error(f"Error adding column `{col}` to `{table_name_lower}`: {type(e).__name__} - {str(e)}")
                                            raise
                                else:
                                    logger.debug(f"Column `{col}` already exists in `{table_name_lower}`, skipping")
                    logger.info(f"Table '{table_name_lower}' already exists, ensured columns from mapping")
                    cache.delete_memoized(get_table_columns_cached, table_name_lower)
                except Exception as e:
                    logger.error(f"Error updating table {table_name_lower} with mapped columns: {type(e).__name__} - {str(e)}")
                    raise RuntimeError(f"Failed to update predefined table '{table_name_lower}': {type(e).__name__} - {str(e)}")

def map_columns(df, column_mapping):
    new_columns = {}
    for col in df.columns:
        normalized = normalize_column_name(col, column_mapping)
        if normalized in column_mapping:
            new_columns[col] = normalized
    df.rename(columns=new_columns, inplace=True)
    return df

def get_existing_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return [col[0] for col in cursor.fetchall()]


def upload_files_to_table(file_source, table_name, result_list, event, uploaded_by, has_header=True, batch_size=1000):
    
    result_lock = threading.Lock()
    
    def task():
        folder_path = file_source
        logger.info(f"Starting upload of files from {folder_path} to table {table_name}")

        if not os.path.isdir(folder_path):
            with result_lock:
                result_list.append(("error", "Invalid folder path"))
            event.set()
            return

        files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.csv', '.xlsx', '.xls', '.xlsb'))]
        if not files:
            with result_lock:
                result_list.append(("warning", "No CSV or Excel files found"))
            event.set()
            return

        logger.info(f"Found {len(files)} files to upload: {files}")
        
        engine = get_db_connection()
        if not engine:
            with result_lock:
                result_list.append(("error", "Database connection failed during upload"))
            event.set()
            return

        existing_tables = get_tables()
        table_name_lower = table_name.lower()
        if table_name_lower not in existing_tables:
            success, msg, category = create_new_table(table_name_lower)
            with result_lock:
                result_list.append((category, msg))
            if category == "error":
                event.set()
                return

        connection = None
        cursor = None
        total_rows = 0

        try:
            connection = engine.raw_connection()
            cursor = connection.cursor()

            for file in files:
                file_path = os.path.join(folder_path, file)
                file_name = os.path.basename(file_path)
                max_file_size = 100 * 1024 * 1024  # 100 MB
                if os.path.getsize(file_path) > max_file_size:
                    with result_lock:
                        result_list.append(("error", f"File {file_name} is too large (max {max_file_size / 1024 / 1024} MB)"))
                    continue

                file_exists, error_msg, file_hash = check_file_exists(file_path, table_name_lower, file_name)
                if file_exists:
                    with result_lock:
                        result_list.append(("error", error_msg))
                    continue

                try:
                    server, date = extract_server_and_date(file_name)
                    df = None
                    column_mapping = ob_column_mapping if table_name_lower == 'ob' else table_mappings.get(table_name_lower, {})

                    expected_columns = set(col for col in column_mapping.keys() if col.lower() not in ['server', 'date'])
                    logger.debug(f"Expected columns for '{table_name_lower}' (excluding server, date): {expected_columns}")

                    if file_name.lower().endswith('.csv'):
                        # Read headers first to check count
                        with open(file_path, 'r') as f:
                            first_line = f.readline().strip().split(',')
                            headers = [str(col).strip().replace(' ', '_').replace('/', '_') for col in first_line] if has_header else [f"column_{i}" for i in range(len(first_line))]

                        if table_name_lower == 'ob':
                            if len(headers) not in [19, 20]:
                                logger.warning(f"Expected 19 or 20 headers in {file_name}, found {len(headers)}")
                                logger.debug(f"Headers in {file_name}: {headers}")
                                with result_lock:
                                    result_list.append(("error", f"Expected 19 or 20 headers in {file_name}, found {len(headers)}"))
                                continue
                            if len(headers) == 20:
                                logger.info(f"Found 20 headers in {file_name}, using first 19 and setting 20th as 'Tag'")
                                headers = headers[:19]
                                headers.append("Tag")
                            else:
                                headers.append("Tag")

                        df = pd.read_csv(file_path, header=None if table_name_lower == 'ob' else (0 if has_header else None), 
                                       skiprows=1 if table_name_lower == 'ob' else 0, 
                                       names=headers if table_name_lower == 'ob' else None,
                                       dtype_backend='numpy_nullable', keep_default_na=False, dtype=str)
                        
                        if table_name_lower == 'ob' and len(df.columns) != 20:
                            logger.warning(f"Expected 20 columns in data rows of {file_name}, found {len(df.columns)}")
                            with result_lock:
                                result_list.append(("error", f"Expected 20 columns in data rows of {file_name}, found {len(df.columns)}"))
                            continue

                        if not has_header and table_name_lower != 'ob':
                            df.columns = [f"column_{i}" for i in range(len(df.columns))]
                        elif has_header and table_name_lower != 'ob':
                            df.columns = [str(col).strip().replace(' ', '_').replace('/', '_') for col in df.columns]

                        has_unmapped, unmapped_error = check_unmapped_columns(df.columns, column_mapping, file_name, table_name_lower)
                        if has_unmapped:
                            with result_lock:
                                result_list.append(("error", unmapped_error))
                            continue

                        conflict, conflicting_col, conflicting_with = check_column_alias_conflict(df.columns, column_mapping)
                        if conflict:
                            with result_lock:
                                result_list.append(("error", f"Column alias conflict in {file_name}: '{conflicting_col}' conflicts with '{conflicting_with}'"))
                            continue

                        normalized_columns = {normalize_column_name(col, column_mapping) for col in df.columns if not col.lower().startswith('unnamed:')}
                        normalized_columns = {col for col in normalized_columns if col.lower() not in ['server', 'date']}
                        logger.debug(f"CSV {file_name} normalized columns: {normalized_columns}")

                        if not expected_columns.issubset(normalized_columns):
                            logger.warning(f"CSV {file_name} does not match expected '{table_name_lower}' table columns: {expected_columns}")
                            with result_lock:
                                result_list.append(("warning", f"CSV {file_name} does not match expected '{table_name_lower}' table columns"))
                            continue
                    else:
                        engine_param = 'pyxlsb' if file_name.lower().endswith('.xlsb') else None
                        xls = pd.ExcelFile(file_path, engine=engine_param)
                        sheet_names = xls.sheet_names
                        logger.info(f"Sheets in {file_name}: {sheet_names}")

                        sheet_mapping = {
                            "orderbook": "Order Book",
                            "users": "Users",
                            "portfolios": "Portfolios",
                            "strategytags": "Strategy Tags",
                            "legs": "Legs",
                            "multilegorders": "MultiLeg Orders",
                            "positions": "Positions",
                            "gridlog": "Gridlog"
                        }
                        
                        target_sheet = sheet_mapping.get(table_name_lower)
                        matching_sheet = None

                        if target_sheet and target_sheet in sheet_names:
                            try:
                                df = pd.read_excel(file_path, sheet_name=target_sheet, header=0, index_col=None, 
                                                dtype_backend='numpy_nullable', keep_default_na=False, engine=engine_param)
                                headers = [str(col).strip().replace(' ', '_').replace('/', '_') for col in df.columns]
                                
                                if table_name_lower == 'ob':
                                    if len(headers) not in [19, 20]:
                                        logger.warning(f"Expected 19 or 20 headers in {file_name} (sheet: {target_sheet}), found {len(headers)}")
                                        logger.debug(f"Headers in {file_name} (sheet: {target_sheet}): {headers}")
                                        with result_lock:
                                            result_list.append(("error", f"Expected 19 or 20 headers in {file_name} (sheet: {target_sheet}), found {len(headers)}"))
                                        continue
                                    if len(headers) == 20:
                                        logger.info(f"Found 20 headers in {file_name} (sheet: {target_sheet}), using first 19 and setting 20th as 'Tag'")
                                        headers = headers[:19]
                                        headers.append("Tag")
                                    else:
                                        headers.append("Tag")
                                    df.columns = headers
                                    if len(df.columns) != 20:
                                        logger.warning(f"Expected 20 columns in data rows of {file_name} (sheet: {target_sheet}), found {len(df.columns)}")
                                        with result_lock:
                                            result_list.append(("error", f"Expected 20 columns in data rows of {file_name} (sheet: {target_sheet}), found {len(df.columns)}"))
                                        continue
                                
                                has_unmapped, unmapped_error = check_unmapped_columns(df.columns, column_mapping, 
                                                                                    f"{file_name} (sheet: {target_sheet})", table_name_lower)
                                if has_unmapped:
                                    logger.warning(unmapped_error)
                                else:
                                    conflict, conflicting_col, conflicting_with = check_column_alias_conflict(df.columns, column_mapping)
                                    if conflict:
                                        logger.warning(f"Column alias conflict in sheet {target_sheet} of {file_name}: '{conflicting_col}' conflicts with '{conflicting_with}'")
                                    else:
                                        normalized_columns = {normalize_column_name(col, column_mapping) for col in df.columns 
                                                            if not col.lower().startswith('unnamed:')}
                                        normalized_columns = {col for col in normalized_columns if col.lower() not in ['server', 'date']}
                                        logger.debug(f"Sheet {target_sheet} normalized columns: {normalized_columns}")

                                        if expected_columns.issubset(normalized_columns):
                                            matching_sheet = target_sheet
                                            logger.info(f"Found matching sheet '{target_sheet}' in {file_name} with columns: {normalized_columns}")
                            except Exception as e:
                                logger.warning(f"Error reading sheet {target_sheet} in {file_name}: {str(e)}")
                        
                        if not matching_sheet:
                            for sheet_name in sheet_names:
                                try:
                                    temp_df = pd.read_excel(file_path, sheet_name=sheet_name, header=0, index_col=None, 
                                                          dtype_backend='numpy_nullable', keep_default_na=False, engine=engine_param)
                                    headers = [str(col).strip().replace(' ', '_').replace('/', '_') for col in temp_df.columns]
                                    
                                    if table_name_lower == 'ob':
                                        if len(headers) not in [19, 20]:
                                            logger.warning(f"Expected 19 or 20 headers in {file_name} (sheet: {sheet_name}), found {len(headers)}")
                                            logger.debug(f"Headers in {file_name} (sheet: {sheet_name}): {headers}")
                                            continue
                                        if len(headers) == 20:
                                            logger.info(f"Found 20 headers in {file_name} (sheet: {sheet_name}), using first 19 and setting 20th as 'Tag'")
                                            headers = headers[:19]
                                            headers.append("Tag")
                                        else:
                                            headers.append("Tag")
                                        temp_df.columns = headers
                                        if len(temp_df.columns) != 20:
                                            logger.warning(f"Expected 20 columns in data rows of {file_name} (sheet: {sheet_name}), found {len(temp_df.columns)}")
                                            continue
                                    
                                    has_unmapped, unmapped_error = check_unmapped_columns(headers, column_mapping, 
                                                                                        f"{file_name} (sheet: {sheet_name})", table_name_lower)
                                    if has_unmapped:
                                        logger.warning(unmapped_error)
                                        continue
                                    conflict, conflicting_col, conflicting_with = check_column_alias_conflict(headers, column_mapping)
                                    if conflict:
                                        logger.warning(f"Column alias conflict in sheet {sheet_name} of {file_name}: '{conflicting_col}' conflicts with '{conflicting_with}'")
                                        continue
                                    normalized_columns = {normalize_column_name(col, column_mapping) for col in headers 
                                                        if not col.lower().startswith('unnamed:')}
                                    normalized_columns = {col for col in normalized_columns if col.lower() not in ['server', 'date']}
                                    logger.debug(f"Sheet {sheet_name} normalized columns: {normalized_columns}")

                                    if expected_columns.issubset(normalized_columns):
                                        matching_sheet = sheet_name
                                        df = temp_df
                                        df.columns = headers
                                        logger.info(f"Found matching sheet '{sheet_name}' in {file_name} with columns: {normalized_columns}")
                                        break
                                except Exception as e:
                                    logger.warning(f"Error reading sheet {sheet_name} in {file_name}: {str(e)}")
                                    continue

                        if not matching_sheet:
                            logger.warning(f"No sheet in {file_name} matches expected '{table_name_lower}' table columns: {expected_columns}")
                            with result_lock:
                                result_list.append(("warning", f"No sheet in {file_name} matches expected '{table_name_lower}' table columns"))
                            continue

                        has_unmapped, unmapped_error = check_unmapped_columns(df.columns, column_mapping, file_name, table_name_lower)
                        if has_unmapped:
                            with result_lock:
                                result_list.append(("error", unmapped_error))
                            continue
                        conflict, conflicting_col, conflicting_with = check_column_alias_conflict(df.columns, column_mapping)
                        if conflict:
                            with result_lock:
                                result_list.append(("error", f"Column alias conflict in {file_name}: '{conflicting_col}' conflicts with '{conflicting_with}'"))
                            continue

                    if column_mapping:
                        df = map_columns(df, column_mapping)
                    else:
                        df.columns = [str(col).strip().replace(' ', '_').replace('(', '').replace(')', '').lower() for col in df.columns]

                    if table_name_lower == 'ob' and 'tag' in df.columns:
                        df['tag'] = df['tag'].fillna('').astype(str)

                    logger.info(f"Processed DataFrame for {file_name}: {df.head().to_dict()}")

                    if 'server' not in df.columns:
                        df['server'] = server
                    if 'date' in df.columns:
                        df['date'] = date.strftime('%Y-%m-%d') if date else df['date']
                    else:
                        df['date'] = date.strftime('%Y-%m-%d') if date else None

                    def is_row_empty(row):
                        return all(pd.isna(row[col]) or str(row[col]).strip().lower() in ['', 'na', 'nan'] 
                                   for col in df.columns if col.lower() not in ['row_id', 'id', 'server', 'date'])

                    df = df[~df.apply(is_row_empty, axis=1)]

                    logger.info(f"Filtered DataFrame for {file_name}: {df.head().to_dict()}")

                    if df.empty:
                        logger.info(f"No valid rows to import from {file_name}")
                        with result_lock:
                            result_list.append(("warning", f"No valid rows to import from {file_name}"))
                        continue

                    cursor.execute(f"SHOW COLUMNS FROM `{table_name_lower}`")
                    existing_columns = [col.lower() for col in [row[0] for row in cursor.fetchall()]]

                    for col in df.columns:
                        if col.lower() not in existing_columns:
                            try:
                                if col == 'sno' and table_name_lower == 'ob':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` INT")
                                elif table_name_lower == 'gridlog' and col == 'timestamp':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` TEXT")
                                elif table_name_lower == 'gridlog' and col == 'date':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` DATE")
                                elif col.lower() == 'status_message':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` TEXT")
                                elif col.lower() == 'date':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` DATE")
                                elif col.lower() == 'tag' and table_name_lower == 'ob':
                                    cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` TEXT")
                                else:
                                    mysql_type = table_mappings.get(table_name_lower, {}).get(col, {}).get("datatype")
                                    if mysql_type and isinstance(mysql_type, str):
                                        cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` {mysql_type}")
                                    else:
                                        logger.warning(f"No valid datatype defined for column `{col}` in `{table_name_lower}`, using TEXT")
                                        cursor.execute(f"ALTER TABLE `{table_name_lower}` ADD COLUMN `{col}` TEXT")
                                existing_columns.append(col.lower())
                            except Exception as e:
                                if 'Duplicate column name' in str(e):
                                    logger.warning(f"Column `{col}` already exists in `{table_name_lower}`, skipping addition")
                                else:
                                    logger.error(f"Error adding column `{col}` to `{table_name_lower}`: {str(e)}")
                                    with result_lock:
                                        result_list.append(("error", f"Failed to add column {col} to {table_name_lower}: {str(e)}"))
                                    continue
                    connection.commit()

                    table_columns = get_existing_columns(cursor, table_name_lower)
                    insert_columns = [col for col in table_columns if col.lower() not in ['row_id', 'id']]
                    logger.info(f"Insert columns for {table_name_lower}: {insert_columns}")

                    df = df.reindex(columns=insert_columns, fill_value=None)
                    if 'sno' in df.columns and table_name_lower == 'ob':
                        df['sno'] = pd.to_numeric(df['sno'], errors='coerce').where(pd.notnull(df['sno']), None)
                    if 'date' in df.columns:
                        if table_name_lower == 'gridlog':
                            df['date'] = df['date'].replace(['NaT', pd.NaT, pd.NA, ''], None)
                        else:
                            df['date'] = pd.to_datetime(df['date'], errors='coerce', format='%Y-%m-%d').dt.date
                        df['date'] = df['date'].where(pd.notnull(df['date']), None)
                    if 'status' in df.columns:
                        df['status'] = df['status'].astype(str)
                    if 'status_message' in df.columns:
                        df['status_message'] = df['status_message'].fillna('').astype(str)
                    if 'tag' in df.columns and table_name_lower == 'ob':
                        df['tag'] = df['tag'].fillna('').astype(str)

                    for col in df.columns:
                        if table_mappings.get(table_name_lower, {}).get(col, {}).get("datatype") in ['INT', 'FLOAT', 'DECIMAL']:
                            df[col] = pd.to_numeric(df[col], errors='coerce').where(pd.notnull(df[col]), None)

                    df = df.astype(object).replace('nan', None).replace('', None)
                    values = [tuple(None if pd.isna(val) else val.item() if hasattr(val, 'item') else val for val in row) 
                             for row in df[insert_columns].values]

                    columns_str = ", ".join([f"`{col}`" for col in insert_columns])
                    placeholders = ", ".join(["%s"] * len(insert_columns))
                    insert_query = f"INSERT INTO `{table_name_lower}` ({columns_str}) VALUES ({placeholders})"

                    for i in range(0, len(values), batch_size):
                        batch = values[i:i + batch_size]
                        cursor.executemany(insert_query, batch)
                        connection.commit()
                        total_rows += len(batch)

                    logger.info(f"Imported {file_name} to {table_name_lower} ({len(values)} rows) with columns: {list(df.columns)}")
                    with result_lock:
                        result_list.append(("success", f"Imported {file_name} to {table_name_lower} ({len(values)} rows)"))
                    
                    log_upload(table_name_lower, uploaded_by, file_hash, file_name)

                except Exception as e:
                    logger.error(f"Error processing {file_name}: {type(e).__name__} - {str(e)}")
                    with result_lock:
                        result_list.append(("error", f"Error processing {file_name}: {type(e).__name__} - {str(e)}"))
            with result_lock:
                result_list.append(("success", f"File import completed! Total rows imported: {total_rows}"))

        except Exception as e:
            logger.error(f"Error in upload task: {type(e).__name__} - {str(e)}")
            with result_lock:
                result_list.append(("error", f"Error in upload task: {type(e).__name__} - {str(e)}"))
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception as e:
                    logger.error(f"Error closing cursor: {type(e).__name__} - {str(e)}")
            if connection and connection.is_connected():
                try:
                    connection.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {type(e).__name__} - {str(e)}")
            event.set()

    try:
        thread = threading.Thread(target=task)
        thread.start()
        return thread, event
    except Exception as e:
        logger.error(f"Error starting upload thread: {type(e).__name__} - {str(e)}")
        with result_lock:
            result_list.append(("error", f"Error starting upload thread: {type(e).__name__} - {str(e)}"))
        event.set()
        return None, event

@app.after_request
def add_no_cache_headers(response):
    if request.path.startswith('/user/') or request.path.startswith('/admin/') or request.path in ['/view_table', '/rename_data', '/search_table', '/filter_table', '/download_table']:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.route('/')
def index():
    if 'authenticated' in session and session['authenticated']:
        if session['role'] == 'admin':
            return redirect(url_for('admin.admin_home'))
        else:
            return redirect(url_for('user.user_home'))
    return redirect(url_for('login.login'))

@app.route('/create_table', methods=['GET', 'POST'])
def create_table_route():
    if 'authenticated' not in session or not session['authenticated']:
        flash('Please log in to create tables!', 'error')
        return redirect(url_for('login.login'))
    if request.method == 'POST':
        table_name = request.form['table_name'].strip().lower()
        if not table_name:
            flash('Table name cannot be empty!', 'error')
            return redirect(url_for('create_table_route'))
        if not table_name.isalnum():
            flash('Table name must be alphanumeric!', 'error')
            return redirect(url_for('create_table_route'))
        
        existing_tables = get_tables_cached()
        if table_name in existing_tables:
            flash(f"Table '{table_name}' already exists!", 'warning')
            return redirect(url_for('create_table_route'))
        
        success, msg, category = create_table(table_name)
        flash(msg, category)
        return redirect(url_for('create_table_route'))
    
    return render_template('create_table.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if 'authenticated' not in session or not session['authenticated']:
        flash('Please log in to upload files!', 'error')
        return redirect(url_for('login.login'))
    if request.method == 'POST':
        table_name = request.form.get('table_name', '').strip().lower()
        uploaded_by = session.get('username', 'unknown')
        has_header = 'has_header' in request.form
        
        if not table_name:
            flash('Please select or enter a table name!', 'error')
            return redirect(url_for('upload'))
        
        if 'file' not in request.files and 'folder_path' not in request.form:
            flash('No file or folder path provided!', 'error')
            return redirect(url_for('upload'))
        
        result_list = []
        event = threading.Event()
        
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls', '.xlsb')):
                flash('Invalid file format! Only CSV and Excel files are allowed.', 'error')
                return redirect(url_for('upload'))
            
            upload_dir = os.path.join('uploads', table_name)
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, file.filename)
            file.save(file_path)
            
            upload_files_to_table(upload_dir, table_name, result_list, event, uploaded_by, has_header)
            event.wait()
            
            for category, msg in result_list:
                flash(msg, category)
            
            return redirect(url_for('upload'))
        
        elif 'folder_path' in request.form and request.form['folder_path']:
            folder_path = request.form['folder_path'].strip()
            upload_files_to_table(folder_path, table_name, result_list, event, uploaded_by, has_header)
            event.wait()
            
            for category, msg in result_list:
                flash(msg, category)
            
            return redirect(url_for('upload'))
    
    tables = get_tables_cached()
    return render_template('upload.html', tables=tables)

from flask import jsonify, render_template, request, flash, redirect, url_for, session
from sqlalchemy import text
import math
from datetime import datetime

@app.route('/view_table/<table>', methods=['GET'])
def view_table(table):
    # Check authentication and role
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to view tables", "error")
        return redirect(url_for('login.login'))

    # Get database connection
    engine = get_db_connection()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('admin.admin_home') if session['role'] == 'admin' else url_for('user.user_home'))

    try:
        with engine.connect() as connection:
            # Get table columns
            columns = get_table_columns(table)
            if not columns:
                flash(f"No columns found for table {table}", "error")
                return redirect(url_for('admin.admin_home') if session['role'] == 'admin' else url_for('user.user_home'))

            # Get request parameters with safe defaults
            search_query = request.args.get('search_query', '').strip()
            from_date = request.args.get('from_date', '').strip()
            to_date = request.args.get('to_date', '').strip()
            page = int(request.args.get('page', '1')) if request.args.get('page', '1').strip().isdigit() else 1
            rows_per_page = int(request.args.get('rows_per_page', '500')) if request.args.get('rows_per_page', '500').strip().isdigit() else 500

            # Collect column-specific search terms
            column_searches = {k: v.strip() for k, v in request.args.items() if k.startswith('column_') and v.strip()}

            # Ensure page and rows_per_page are positive
            page = max(1, page)
            rows_per_page = max(1, rows_per_page)
            offset = (page - 1) * rows_per_page

            # Build SQL query with conditions
            query = f"SELECT * FROM `{table}`"
            conditions = []
            params = {}

            # Apply global search filter across all columns
            if search_query:
                search_conditions = [f"`{col}` LIKE :search" for col in columns]
                conditions.append("(" + " OR ".join(search_conditions) + ")")
                params['search'] = f"%{search_query}%"

            # Apply column-specific search filters
            for key, value in column_searches.items():
                try:
                    col_index = int(key.replace('column_', ''))
                    if 0 <= col_index < len(columns):
                        col_name = columns[col_index]
                        conditions.append(f"`{col_name}` LIKE :{key}")
                        params[key] = f"%{value}%"
                except ValueError:
                    continue  # Skip invalid column indices

            # Apply date filter if 'date' column exists
            if 'date' in columns and from_date and to_date:
                try:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                    if from_date_obj <= to_date_obj:
                        conditions.append("`date` BETWEEN :from_date AND :to_date")
                        params['from_date'] = from_date_obj
                        params['to_date'] = to_date_obj
                    else:
                        flash("Invalid date range: 'From' date must be before 'To' date", "warning")
                except ValueError:
                    flash("Invalid date format", "warning")
                    from_date = to_date = ''  # Clear invalid dates

            # Construct WHERE clause
            where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

            # Get total and filtered row counts
            total_rows_query = f"SELECT COUNT(*) FROM `{table}`"
            total_rows = connection.execute(text(total_rows_query)).scalar() or 0
            filtered_rows_query = f"SELECT COUNT(*) FROM `{table}` {where_clause}"
            filtered_rows = connection.execute(text(filtered_rows_query), params).scalar() or 0

            # Paginated query
            query_paginated = f"{query} {where_clause} LIMIT :limit OFFSET :offset"
            params['limit'] = rows_per_page
            params['offset'] = offset
            result = connection.execute(text(query_paginated), params)
            paginated_data = [dict(row._mapping) for row in result.fetchall()]

            # Calculate total pages
            total_pages = max(1, math.ceil(filtered_rows / rows_per_page))

            # Adjust page if it exceeds total_pages
            if page > total_pages:
                page = total_pages
                offset = (page - 1) * rows_per_page
                params['offset'] = offset
                result = connection.execute(text(query_paginated), params)
                paginated_data = [dict(row._mapping) for row in result.fetchall()]

            # Check if it's an AJAX request
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            if is_ajax:
                response_data = []
                for row in paginated_data:
                    row_data = {col: str(row.get(col, '')) for col in columns}
                    response_data.append(row_data)

                return jsonify({
                    "data": response_data,
                    "columns": columns,
                    "page": page,
                    "total_pages": total_pages,
                    "rows_per_page": rows_per_page,
                    "search_query": search_query,
                    "from_date": from_date,
                    "to_date": to_date
                })

            # Flash message for no data
            if not paginated_data and (search_query or from_date or to_date or column_searches):
                flash("No data found with the specified filters", "warning")
            elif not paginated_data:
                flash("No data found", "warning")

            # Render template for non-AJAX GET requests
            return render_template(
                "view_table.html",
                table=table,
                columns=columns,
                data=paginated_data,
                rows_per_page=rows_per_page,
                page=page,
                total_pages=total_pages,
                search_query=search_query,
                from_date=from_date,
                to_date=to_date
            )

    except Exception as e:
        flash(f"Error fetching data for table {table}: {str(e)}", "error")
        return redirect(url_for('admin.admin_home') if session['role'] == 'admin' else url_for('user.user_home'))
    
@app.route('/search_table/<table>')
def search_table(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to search tables", "error")
        return redirect(url_for('login.login'))
    return redirect(url_for('filter_table', table=table, page=1))

@app.route('/filter_table/<table>', methods=['GET', 'POST'])
def filter_table(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to filter tables", "error")
        return redirect(url_for('login.login'))
    
    engine = get_db_connection()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('admin.admin_home') if session['role'] == 'admin' else url_for('user.user_home'))
    
    try:
        with engine.connect() as connection:
            columns = get_table_columns_cached(table)
            if not columns:
                flash(f"No columns found for table {table}", "error")
                return redirect(url_for('view_table', table=table, page=1))
            
            if request.method == 'POST':
                draw = int(request.form.get('draw', 1))
                start = int(request.form.get('start', 0))
                length = int(request.form.get('length', 10))
                search_query = request.form.get('search_query', '')
                from_date = request.form.get('from_date', '')
                to_date = request.form.get('to_date', '')
                per_page = int(request.form.get('per_page', length))
                page = int(request.form.get('page', (start // length) + 1))
            else:
                page = int(request.args.get('page', 1))
                per_page = int(request.args.get('per_page', 1000))
                if per_page not in [500, 1000, 1500, 3000]:
                    per_page = 1000
                search_query = request.args.get('search_query', '')
                from_date = request.args.get('from_date', '')
                to_date = request.args.get('to_date', '')
                start = (page - 1) * per_page
                length = per_page
                draw = 1

            offset = start

            query = f"SELECT * FROM `{table}`"
            conditions = []
            params = {}

            if search_query:
                search_conditions = []
                for col in columns:
                    search_conditions.append(f"`{col}` LIKE :search")
                if search_conditions:
                    conditions.append("(" + " OR ".join(search_conditions) + ")")
                params['search'] = f"%{search_query}%"

            if 'date' in columns:
                if from_date:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                    conditions.append("`date` >= :from_date")
                    params['from_date'] = from_date_obj
                if to_date:
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                    conditions.append("`date` <= :to_date")
                    params['to_date'] = to_date_obj

            where_clause = ""
            if conditions:
                where_clause = " WHERE " + " AND ".join(conditions)

            total_rows_query = f"SELECT COUNT(*) FROM `{table}`"
            total_rows = connection.execute(text(total_rows_query)).scalar()
            filtered_rows_query = f"SELECT COUNT(*) FROM `{table}` {where_clause}"
            filtered_rows = connection.execute(text(filtered_rows_query), params).scalar()

            query_paginated = query + where_clause + f" LIMIT {length} OFFSET {offset}"
            result = connection.execute(text(query_paginated), params)
            paginated_data = [dict(row._mapping) for row in result.fetchall()]
            
            total_pages = math.ceil(filtered_rows / per_page)

            primary_key = 'id' if table.lower() == 'upload_log' else 'row_id'
            row_ids = [row[primary_key] for row in paginated_data if primary_key in row] if primary_key in columns else [i + offset + 1 for i in range(len(paginated_data))]
            page_range = range(1, total_pages + 1)

            if request.method == 'POST':
                response_data = []
                for row in paginated_data:
                    row_data = {}
                    for col in columns:
                        row_data[col] = str(row.get(col, ''))
                    response_data.append(row_data)

                return jsonify({
                    'draw': draw,
                    'recordsTotal': total_rows,
                    'recordsFiltered': filtered_rows,
                    'data': response_data,
                    'page': page,
                    'total_pages': total_pages
                })

            if not paginated_data and (from_date or to_date or search_query):
                flash("No data found with the specified filters", "warning")
            elif not paginated_data:
                logger.warning(f"No data returned for table {table} with query: {query} and params: {params}")
                flash("No data found", "warning")
            
            additional_columns = columns[1:] if len(columns) > 1 else []
            additional_columns_json = json.dumps(additional_columns)

            return render_template('search.html', 
                                 table=table, 
                                 columns=columns, 
                                 additional_columns=additional_columns,
                                 additional_columns_json=additional_columns_json,
                                 data=paginated_data, 
                                 page=page,
                                 total_pages=total_pages, 
                                 page_range=page_range, 
                                 row_ids=row_ids, 
                                 search_query=search_query, 
                                 from_date=from_date, 
                                 to_date=to_date, 
                                 per_page=per_page)

    except Exception as e:
        error_msg = f"Error filtering table {table}: {str(e) if str(e) else 'Unknown error'}"
        logger.error(error_msg)
        flash(error_msg, "error")
        return redirect(url_for('view_table', table=table, page=1))

@app.route('/rename_data/<table>', methods=['GET', 'POST'])
def rename_data(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to rename data", "error")
        return redirect(url_for('login.login'))
    if session['role'] != 'admin':
        flash("Only admins can rename data", "error")
        return redirect(url_for('view_table', table=table, page=1))

    engine = get_db_connection()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('view_table', table=table, page=1))

    try:
        with engine.connect() as connection:
            connection.execution_options(autocommit=False)
            columns = get_table_columns_cached(table)
            if not columns:
                flash(f"No columns found for table {table}", "error")
                return redirect(url_for('view_table', table=table, page=1))

            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 1000))
            if per_page not in [500, 1000, 1500, 3000]:
                per_page = 1000
            search_query = request.args.get('search_query', '')
            from_date = request.args.get('from_date', '')
            to_date = request.args.get('to_date', '')
            offset = (page - 1) * per_page

            query = f"SELECT * FROM `{table}`"
            conditions = []
            params = {}

            if search_query:
                search_conditions = []
                for col in columns:
                    search_conditions.append(f"`{col}` LIKE :search")
                if search_conditions:
                    conditions.append("(" + " OR ".join(search_conditions) + ")")
                params['search'] = f"%{search_query}%"

            if 'date' in columns:
                if from_date:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                    conditions.append("`date` >= :from_date")
                    params['from_date'] = from_date_obj
                if to_date:
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                    conditions.append("`date` <= :to_date")
                    params['to_date'] = to_date_obj

            where_clause = ""
            if conditions:
                where_clause = " WHERE " + " AND ".join(conditions)

            total_rows_query = f"SELECT COUNT(*) FROM `{table}`"
            total_rows = connection.execute(text(total_rows_query)).scalar()
            filtered_rows_query = f"SELECT COUNT(*) FROM `{table}` {where_clause}"
            filtered_rows = connection.execute(text(filtered_rows_query), params).scalar()

            total_pages = math.ceil(filtered_rows / per_page) if filtered_rows > 0 else 1
            page = min(max(1, page), total_pages)

            if request.method == 'POST':
                try:
                    if 'new_column' in request.form and 'old_column' in request.form:
                        old_column = request.form['old_column']
                        new_column = request.form['new_column'].strip()
                        if not new_column or not old_column in columns:
                            flash("Invalid column selection or empty new column namebroker", "error")
                        elif new_column in columns:
                            flash(f"Column name '{new_column}' already exists", "error")
                        elif not new_column.isalnum():
                            flash("New column name must be alphanumeric", "error")
                        else:
                            column_type = get_column_type(connection, table, old_column)
                            if column_type:
                                query = text(f"ALTER TABLE `{table}` CHANGE `{old_column}` `{new_column}` {column_type}")
                                connection.execute(query)
                                connection.commit()
                                flash(f"Column '{old_column}' renamed to '{new_column}'", "success")
                                cache.delete_memoized(get_table_columns_cached, table)
                                columns = get_table_columns_cached(table)
                            else:
                                flash(f"Could not determine type for column '{old_column}'", "error")

                    elif 'new_data' in request.form and 'column' in request.form:
                        column = request.form['column']
                        old_data = request.form['old_data']
                        new_data = request.form['new_data']
                        row_id = request.form.get('row_id', '').strip()
                        if not column in columns:
                            flash("Invalid column name", "error")
                        else:
                            primary_key = 'id' if table.lower() == 'upload_log' else 'row_id'
                            if row_id:
                                try:
                                    row_id = int(row_id) if row_id.isdigit() else row_id
                                    query = text(f"UPDATE `{table}` SET `{column}` = :new_data WHERE `{column}` = :old_data AND `{primary_key}` = :row_id")
                                    params_update = {"new_data": new_data, "old_data": old_data, "row_id": row_id}
                                    result = connection.execute(query, params_update)
                                    affected_rows = result.rowcount
                                    flash(f"Updated {affected_rows} row(s) with {primary_key} '{row_id}' in column '{column}'", "success" if affected_rows > 0 else "warning")
                                except ValueError:
                                    flash("Invalid row ID format", "error")
                            else:
                                query = text(f"UPDATE `{table}` SET `{column}` = :new_data WHERE `{column}` = :old_data")
                                params_update = {"new_data": new_data, "old_data": old_data}
                                result = connection.execute(query, params_update)
                                affected_rows = result.rowcount
                                flash(f"Updated {affected_rows} row(s) in column '{column}'", "success" if affected_rows > 0 else "warning")
                            connection.commit()

                    elif 'delete_rows' in request.form:
                        rows_to_delete = request.form.getlist('rows_to_delete')
                        if not rows_to_delete:
                            flash("No rows selected for deletion", "warning")
                        else:
                            primary_key = 'id' if table.lower() == 'upload_log' else 'row_id'
                            try:
                                valid_rows = [int(row_id) if row_id.isdigit() else row_id for row_id in rows_to_delete if row_id]
                                if not valid_rows:
                                    flash("No valid rows selected for deletion", "warning")
                                else:
                                    placeholders = ','.join([':row' + str(i) for i in range(len(valid_rows))])
                                    query = text(f"DELETE FROM `{table}` WHERE `{primary_key}` IN ({placeholders})")
                                    params_delete = {f"row{i}": row_id for i, row_id in enumerate(valid_rows)}
                                    result = connection.execute(query, params_delete)
                                    affected_rows = result.rowcount
                                    flash(f"Deleted {affected_rows} row(s)", "success" if affected_rows > 0 else "warning")
                                    connection.commit()
                            except ValueError:
                                flash("Invalid row ID format in selection", "error")

                    elif 'delete_columns' in request.form:
                        columns_to_delete = request.form.getlist('columns_to_delete')
                        primary_key = 'id' if table.lower() == 'upload_log' else 'row_id'
                        if not columns_to_delete:
                            flash("No columns selected for deletion", "warning")
                        else:
                            deleted_count = 0
                            for col in columns_to_delete:
                                if col in columns and col.lower() != primary_key.lower():
                                    try:
                                        connection.execute(text(f"ALTER TABLE `{table}` DROP COLUMN `{col}`"))
                                        deleted_count += 1
                                    except Exception as e:
                                        logger.error(f"Error deleting column '{col}' from table '{table}': {str(e)}")
                                        flash(f"Error deleting column '{col}': {str(e)}", "error")
                            if deleted_count > 0:
                                flash(f"Deleted {deleted_count} column(s)", "success")
                                cache.delete_memoized(get_table_columns_cached, table)
                                columns = get_table_columns_cached(table)
                            connection.commit()

                except Exception as e:
                    connection.rollback()
                    logger.error(f"Error processing POST request for table {table}: {type(e).__name__} - {str(e)}")
                    flash(f"Error: {type(e).__name__} - {str(e)}", "error")

            query_base = f"SELECT * FROM `{table}`"
            query_paginated = query_base
            if where_clause:
                query_paginated += where_clause
            query_paginated += f" LIMIT :per_page OFFSET :offset"
            params.update({"per_page": per_page, "offset": offset})
            result = connection.execute(text(query_paginated), params)
            data = [dict(row._mapping) for row in result.fetchall()]
            for row in data:
                for key in row:
                    row[key] = '' if row[key] is None else str(row[key])

            if request.args.get('ajax') == 'true':
                return jsonify({
                    'data': data,
                    'columns': columns,
                    'page': page,
                    'total_pages': total_pages
                })

            if not data and (search_query or from_date or to_date):
                flash("No data found with the specified filters", "warning")
            elif not data:
                flash("No data found", "warning")

            return render_template('rename.html',
                                 table_name=table,
                                 columns=columns,
                                 data=data,
                                 page=page,
                                 total_pages=total_pages,
                                 page_range=range(1, total_pages + 1),
                                 per_page=per_page,
                                 search_query=search_query,
                                 from_date=from_date,
                                 to_date=to_date)

    except Exception as e:
        logger.error(f"Error in rename_data for table {table}: {type(e).__name__} - {str(e)}")
        flash(f"Error: {type(e).__name__} - {str(e)}", "error")
        return redirect(url_for('view_table', table=table, page=1))


@app.route('/download_table/<table>')
def download_table(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to download tables", "error")
        return redirect(url_for('login.login'))
    if session['role'] not in ['admin', 'user']:
        flash("You do not have permission to download tables", "error")
        return redirect(url_for('view_table', table=table, page=1))
    
    engine = get_db_connection()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('view_table', table=table, page=1))
    
    try:
        with engine.connect() as connection:
            columns = get_table_columns(table)
            if not columns:
                flash(f"No columns found for table {table}", "error")
                return redirect(url_for('view_table', table=table, page=1))

            # Get request parameters with safe defaults
            search_query = request.args.get('search_query', '').strip()
            from_date = request.args.get('from_date', '').strip()
            to_date = request.args.get('to_date', '').strip()
            download_all = request.args.get('download_all', 'false').lower() == 'true'
            page = int(request.args.get('page', '1')) if request.args.get('page', '1').strip().isdigit() else 1
            rows_per_page = int(request.args.get('rows_per_page', '500')) if request.args.get('rows_per_page', '500').strip().isdigit() else 500

            # Collect column-specific search terms
            column_searches = {k: v.strip() for k, v in request.args.items() if k.startswith('column_') and v.strip()}

            # Ensure page and rows_per_page are positive
            page = max(1, page)
            rows_per_page = max(1, rows_per_page)
            offset = (page - 1) * rows_per_page

            # Build SQL query with conditions
            query = f"SELECT * FROM `{table}`"
            conditions = []
            params = {}

            # Apply global search filter across all columns
            if search_query:
                search_conditions = [f"`{col}` LIKE :search" for col in columns]
                conditions.append("(" + " OR ".join(search_conditions) + ")")
                params['search'] = f"%{search_query}%"

            # Apply column-specific search filters
            for key, value in column_searches.items():
                try:
                    col_index = int(key.replace('column_', ''))
                    if 0 <= col_index < len(columns):
                        col_name = columns[col_index]
                        conditions.append(f"`{col_name}` LIKE :{key}")
                        params[key] = f"%{value}%"
                except ValueError:
                    continue  # Skip invalid column indices

            # Apply date filter if 'date' column exists
            if 'date' in columns and from_date and to_date:
                try:
                    from_date_obj = datetime.strptime(from_date, '%Y-%m-%d').date()
                    to_date_obj = datetime.strptime(to_date, '%Y-%m-%d').date()
                    if from_date_obj <= to_date_obj:
                        conditions.append("`date` BETWEEN :from_date AND :to_date")
                        params['from_date'] = from_date_obj
                        params['to_date'] = to_date_obj
                except ValueError:
                    pass  # Skip invalid date formats

            # Construct WHERE clause
            where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

            # Prepare query based on download_all
            if download_all:
                # Fetch all filtered rows without pagination
                query_final = f"{query} {where_clause}"
            else:
                # Fetch only the current page
                query_final = f"{query} {where_clause} LIMIT :limit OFFSET :offset"
                params['limit'] = rows_per_page
                params['offset'] = offset

            # Execute query
            df = pd.read_sql(text(query_final), connection, params=params)

            # Generate CSV
            csv_output = io.BytesIO()
            df.to_csv(csv_output, index=False)
            csv_output.seek(0)
            
            csv_size = csv_output.getbuffer().nbytes
            size_limit = 50 * 1024 * 1024  # 50 MB in bytes
            
            if csv_size > size_limit:
                zip_output = io.BytesIO()
                with zipfile.ZipFile(zip_output, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    filename = f"{table}_filtered_all.csv" if download_all else f"{table}_filtered_page_{page}.csv"
                    zip_file.writestr(filename, csv_output.getvalue())
                zip_output.seek(0)
                download_name = f"{table}_filtered_all.zip" if download_all else f"{table}_filtered_page_{page}.zip"
                return send_file(
                    zip_output,
                    mimetype='application/zip',
                    as_attachment=True,
                    download_name=download_name
                )
            else:
                download_name = f"{table}_filtered_all.csv" if download_all else f"{table}_filtered_page_{page}.csv"
                return send_file(
                    csv_output,
                    mimetype='text/csv',
                    as_attachment=True,
                    download_name=download_name
                )
            
    except Exception as e:
        flash(f"Error downloading table {table}: {type(e).__name__} - {str(e)}", "error")
        return redirect(url_for('view_table', table=table, page=1))
    
@app.route('/help')
def help():
    if 'authenticated' not in session or not session['authenticated']:
        flash("Please log in to access help", "error")
        return redirect(url_for('login.login'))
    return render_template('help.html')

app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(user_bp, url_prefix='/user')
app.register_blueprint(login_bp, url_prefix='/login')

admin_bp.get_tables = get_tables
admin_bp.create_table = create_table
admin_bp.upload_files_to_table = upload_files_to_table
admin_bp.cache = cache

user_bp.get_tables = get_tables
user_bp.upload_files_to_table = upload_files_to_table
user_bp.cache = cache

with app.app_context():
    success, msg, category = create_upload_log_table()
    if category == "error":
        logger.error(f"Failed to initialize upload_log table: {msg}")
        raise RuntimeError(f"Failed to initialize upload_log table: {msg}")
    initialize_predefined_tables()

if __name__ == '__main__':
    app.run(debug=True)