

# utils.py
import logging
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import gdown  # Added for Google Drive downloads

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_db_connection():
    """
    Establishes a connection to the MySQL database.
    Returns: SQLAlchemy engine object or None if connection fails.
    """
    try:
        # Replace with your actual database credentials
        db_user = os.getenv('DB_USER', 'root')
        db_password = os.getenv('DB_PASSWORD', 'amanrai123')
        db_host = os.getenv('DB_HOST', 'localhost')
        db_name = os.getenv('DB_NAME', 'MST')
        
        connection_string = f"mysql+mysqlconnector://{db_user}:{db_password}@{db_host}/{db_name}"
        engine = create_engine(connection_string, echo=False)
        # Test the connection
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("Database connection established successfully")
        return engine
    except Exception as e:
        logger.error(f"Failed to connect to database: {str(e)}")
        return None

def get_db_function(db_type=None):
    """
    Retrieves the appropriate database function or connector based on the database type.
    Args:
        db_type (str, optional): The type of database (e.g., 'mysql', 'sqlite').
    Returns:
        str: The connector or function name for the specified database type.
    """
    try:
        db_functions = {
            'mysql': 'mysql.connector',
            'sqlite': 'sqlite3'
        }
        return db_functions.get(db_type, 'mysql.connector')  # Default to MySQL connector
    except Exception as e:
        logger.error(f"Error retrieving database function for {db_type}: {str(e)}")
        return 'mysql.connector'  # Fallback to default

def get_tables(prefix=None):
    """
    Retrieves a list of table names from the database.
    Args:
        prefix (str, optional): Filter tables by prefix.
    Returns:
        List of table names.
    """
    engine = get_db_connection()
    if not engine:
        logger.error("Cannot retrieve tables: Database connection failed")
        return []
    
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SHOW TABLES"))
            tables = [row[0] for row in result.fetchall()]
            if prefix:
                tables = [table for table in tables if table.startswith(prefix)]
            logger.info(f"Retrieved {len(tables)} tables from the database")
            return tables
    except Exception as e:
        logger.error(f"Error retrieving tables: {str(e)}")
        return []

def get_table_columns(table):
    """
    Retrieves the column names of a given table.
    Args:
        table (str): Name of the table.
    Returns:
        List of column names.
    """
    engine = get_db_connection()
    if not engine:
        logger.error("Cannot retrieve table columns: Database connection failed")
        return []
    
    try:
        with engine.connect() as connection:
            result = connection.execute(text(f"SHOW COLUMNS FROM `{table}`"))
            columns = [row[0] for row in result.fetchall()]
            logger.info(f"Retrieved columns for table '{table}': {columns}")
            return columns
    except Exception as e:
        logger.error(f"Error retrieving columns for table '{table}': {str(e)}")
        return []

def download_from_google_drive(drive_link, output_dir):
    """
    Download files from a Google Drive folder link using gdown.
    Args:
        drive_link (str): The Google Drive folder link.
        output_dir (str): The directory to save the downloaded files.
    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        gdown.download_folder(drive_link, output=output_dir, quiet=False)
        logger.info(f"Successfully downloaded files from Google Drive to {output_dir}")
        return True
    except Exception as e:
        logger.error(f"Error downloading from Google Drive: {str(e)}")
        return False