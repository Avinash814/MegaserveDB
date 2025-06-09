import pandas as pd
import re
from sqlalchemy import text
from utils import get_db_connection, get_table_columns
from mapping import normalize_column_name, table_mappings
from flask import Blueprint, render_template, request, send_file
from io import BytesIO
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create Flask Blueprint
margin_shortfall_bp = Blueprint('margin_shortfall', __name__)

def extract_shortfall(msg):
    if pd.isna(msg):
        return None
    try:
        if "Margin Shortfall[" in msg:
            return round(float(re.search(r'Margin Shortfall\[([\d.]+)\]', msg).group(1)), 2)
        elif "Shortfall:INR " in msg:
            return round(float(re.search(r'Shortfall:INR ([\d.]+)', msg).group(1)), 2)
        elif "Insufficient Funds; Required Amount" in msg:
            required = float(re.search(r'Required Amount ([\d.]+); Available Amount', msg).group(1))
            available = float(re.search(r'Available Amount ([\d.]+)', msg).group(1))
            return round(required - available, 2)
        elif ";Required:" in msg and "; Available:" in msg:
            required_amount = float(re.search(r';Required:([\d.]+)', msg).group(1))
            available_amount = float(re.search(r'; Available:([\d.]+)', msg).group(1))
            return round(required_amount - available_amount, 2)
    except Exception:
        return None
    return None

def get_column_mapping(table_columns, table_name):
    """
    Maps database column names to standardized column names using normalize_column_name.
    Args:
        table_columns (list): List of actual column names in the database table.
        table_name (str): Name of the table to get mappings for.
    Returns:
        dict: Mapping of database column names to standardized column names.
    """
    try:
        column_mapping = table_mappings.get(table_name, {})
        col_map = {}
        for db_col in table_columns:
            std_col = normalize_column_name(db_col, column_mapping)
            if std_col in column_mapping:
                col_map[std_col] = db_col
        return col_map
    except Exception as e:
        logger.error(f"Error mapping columns for table {table_name}: {str(e)}")
        return {}

def analyze_margin_shortfalls(selected_date=None):
    try:
        # Get database connection
        engine = get_db_connection()
        if not engine:
            logger.error("Database connection failed")
            return pd.DataFrame(), pd.DataFrame()

        # Get actual column names from database
        user_columns = get_table_columns('users')
        ob_columns = get_table_columns('ob')

        # Map columns using normalize_column_name
        user_col_map = get_column_mapping(user_columns, 'users')
        ob_col_map = get_column_mapping(ob_columns, 'ob')

        # Required and optional columns
        required_user_cols = ['user_id', 'alias', 'broker', 'mtm_all', 'allocation', 'max_loss', 'available_margin']
        required_order_cols = ['user_id', 'order_time', 'status_message', 'exchange', 'status']
        optional_cols = ['algo', 'server']

        # Check for missing required columns
        missing_user_cols = [col for col in required_user_cols if col not in user_col_map]
        missing_order_cols = [col for col in required_order_cols if col not in ob_col_map]
        if missing_user_cols:
            logger.error(f"Missing required columns in users table: {missing_user_cols}")
            return pd.DataFrame(), pd.DataFrame()
        if missing_order_cols:
            logger.error(f"Missing required columns in ob table: {missing_order_cols}")
            return pd.DataFrame(), pd.DataFrame()

        # Build SQL queries
        user_select_cols = [user_col_map[col] for col in required_user_cols if col in user_col_map]
        user_select_cols += [user_col_map[col] for col in optional_cols if col in user_col_map]
        ob_select_cols = [ob_col_map[col] for col in required_order_cols if col in ob_col_map]
        ob_select_cols += [ob_col_map[col] for col in ['user_alias'] + optional_cols if col in ob_col_map]

        user_query = f"SELECT {', '.join(user_select_cols)} FROM users"
        ob_query = f"SELECT {', '.join(ob_select_cols)} FROM ob"

        # Add date filter if selected_date is provided
        if selected_date:
            try:
                # Ensure selected_date is in YYYY-MM-DD format
                selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
                user_query += f" WHERE {user_col_map['date']} = :selected_date"
                ob_query += f" WHERE {ob_col_map['date']} = :selected_date"
            except ValueError:
                logger.error("Invalid date format provided")
                return pd.DataFrame(), pd.DataFrame()

        # Load data from database
        try:
            with engine.connect() as connection:
                if selected_date:
                    users_df = pd.read_sql(text(user_query), connection, params={'selected_date': selected_date})
                    orderbook_df = pd.read_sql(text(ob_query), connection, params={'selected_date': selected_date})
                else:
                    users_df = pd.read_sql(text(user_query), connection)
                    orderbook_df = pd.read_sql(text(ob_query), connection)
        except Exception as e:
            logger.error(f"Error executing database queries: {str(e)}")
            return pd.DataFrame(), pd.DataFrame()

        # Rename columns to standard names
        for std_col, db_col in user_col_map.items():
            if db_col in users_df.columns:
                users_df.rename(columns={db_col: std_col}, inplace=True)
        for std_col, db_col in ob_col_map.items():
            if db_col in orderbook_df.columns:
                orderbook_df.rename(columns={db_col: std_col}, inplace=True)

        # Convert and clean User ID
        users_df['user_id'] = users_df['user_id'].astype(str).str.strip().str.upper()
        orderbook_df['user_id'] = orderbook_df['user_id'].astype(str).str.strip().str.upper()

        # Remove duplicates in users_df
        users_df = users_df.drop_duplicates(subset=['user_id'])

        # Exclude specific users based on User Alias
        excluded_users = ["CC_SISL_GS_DEALER", "GSPLDEAL", "GSPLDEALER"]
        orderbook_df = orderbook_df[~orderbook_df["user_alias"].isin(excluded_users)]

        # Sort orderbook by User ID and Order Time
        orderbook_df['order_time'] = pd.to_datetime(orderbook_df['order_time'], format='%d-%m-%Y %H.%M', errors='coerce')
        if orderbook_df['order_time'].isna().any():
            min_valid_time = orderbook_df['order_time'].min()
            if pd.notna(min_valid_time):
                orderbook_df['order_time'] = orderbook_df['order_time'].fillna(min_valid_time)
            else:
                orderbook_df['order_time'] = orderbook_df['order_time'].fillna(pd.Timestamp('2025-05-29 00:00:00'))
        orderbook_df = orderbook_df.sort_values(['user_id', 'order_time'])

        # Extract shortfall from Status Message
        orderbook_df['Margin Shortfall'] = orderbook_df['status_message'].apply(extract_shortfall)

        # Filter for orders with shortfall
        shortfall_orders = orderbook_df[orderbook_df['Margin Shortfall'].notna()]

        if len(shortfall_orders) == 0:
            logger.info("No margin shortfall orders found")
            return pd.DataFrame(), pd.DataFrame()

        # Get unique User IDs with shortfall
        shortfall_users = shortfall_orders[['user_id']].drop_duplicates()

        # Calculate total shortfall per user
        total_shortfall = shortfall_orders.groupby('user_id')['Margin Shortfall'].sum().reset_index(name='Margin Shortfall_Total')

        # Merge with shortfall orders to include Exchange and individual shortfalls
        result_df = shortfall_users.merge(
            shortfall_orders[['user_id', 'Margin Shortfall', 'exchange']],
            on='user_id',
            how='left'
        ).merge(
            total_shortfall,
            on='user_id',
            how='left'
        )

        # Merge with user data
        user_columns = ['user_id', 'alias', 'broker', 'mtm_all', 'allocation', 'max_loss', 'available_margin']
        if 'algo' in users_df.columns:
            user_columns.append('algo')
        if 'server' in users_df.columns:
            user_columns.append('server')
        existing_columns = [col for col in user_columns if col in users_df.columns]
        result_df = result_df.merge(
            users_df[existing_columns],
            on='user_id',
            how='left'
        )

        # Filter for specific exchanges (NFO, BFO)
        result_df = result_df[result_df['exchange'].isin(['NFO', 'BFO'])]

        # Prepare result_df for Sheet 2 with specified columns
        result_columns = [
            'user_id', 'Margin Shortfall', 'exchange', 'Margin Shortfall_Total', 
            'alias', 'broker', 'mtm_all', 'allocation', 'max_loss', 'available_margin'
        ]
        if 'algo' in result_df.columns:
            result_columns.append('algo')
        if 'server' in result_df.columns:
            result_columns.append('server')
        result_df = result_df[result_columns]
        result_df.columns = [
            'User ID', 'Margin Shortfall', 'Exchange', 'Margin Shortfall_Total', 
            'Alias', 'Broker', 'MTM (All)', 'ALLOCATION', 'MAXLOSS', 'Available Margin',
            'ALGO' if 'algo' in result_df.columns else None,
            'SERVER' if 'server' in result_df.columns else None
        ]
        result_df = result_df[[col for col in result_df.columns if col is not None]]

        # Calculate status counts for orders (pivot_df for Sheet 1)
        group_cols = ['user_id']
        if 'algo' in users_df.columns:
            group_cols.append('algo')
        if 'server' in users_df.columns:
            group_cols.append('server')
        status_count = orderbook_df.merge(
            users_df[['user_id'] + [col for col in ['algo', 'server'] if col in users_df.columns]],
            on='user_id',
            how='left'
        ).groupby(group_cols)['status'].value_counts().unstack(fill_value=0).reset_index()

        # Ensure all required status columns are present
        status_columns = ['CANCELLED', 'COMPLETE', 'OPEN', 'REJECTED']
        for col in status_columns:
            if col not in status_count.columns:
                status_count[col] = 0

        # Calculate margin shortfall rejections
        margin_shortfall = shortfall_orders[shortfall_orders['Margin Shortfall'] > 0]
        rejections = margin_shortfall.merge(
            users_df[['user_id'] + [col for col in ['algo', 'server'] if col in users_df.columns]],
            on='user_id',
            how='left'
        ).groupby(group_cols).size().reset_index(name='Margin Shortfall Rejections')

        # Calculate total shortfall for pivot table
        shortfall_total_pivot = shortfall_orders.merge(
            users_df[['user_id'] + [col for col in ['algo', 'server'] if col in users_df.columns]],
            on='user_id',
            how='left'
        ).groupby(group_cols)['Margin Shortfall'].sum().reset_index(name='Margin Shortfall_Total')

        # Merge status counts with rejections and total shortfall
        pivot_df = pd.merge(status_count, rejections, on=group_cols, how='left')
        pivot_df = pd.merge(pivot_df, shortfall_total_pivot, on=group_cols, how='left')
        pivot_df['Margin Shortfall Rejections'] = pivot_df['Margin Shortfall Rejections'].fillna(0).astype(int)
        pivot_df['Margin Shortfall_Total'] = pivot_df['Margin Shortfall_Total'].fillna(0).round(2)

        # Prepare pivot_df for Sheet 1 with specified columns
        pivot_columns = ['user_id', 'CANCELLED', 'COMPLETE', 'OPEN', 'REJECTED', 'Margin Shortfall Rejections', 'Margin Shortfall_Total']
        if 'algo' in pivot_df.columns:
            pivot_columns.insert(1, 'algo')
        if 'server' in pivot_df.columns:
            pivot_columns.insert(1 if 'algo' not in pivot_df.columns else 2, 'server')
        pivot_df = pivot_df[pivot_columns]
        pivot_df.columns = [
            'User ID', 
            'ALGO' if 'algo' in pivot_df.columns else None, 
            'SERVER' if 'server' in pivot_df.columns else None, 
            'CANCELLED', 'COMPLETE', 'OPEN', 'REJECTED', 
            'Margin Shortfall Rejections', 'Margin Shortfall_Total'
        ]
        pivot_df = pivot_df[[col for col in pivot_df.columns if col is not None]]

        return result_df, pivot_df

    except Exception as e:
        logger.error(f"Error processing margin shortfall analysis: {str(e)}")
        return pd.DataFrame(), pd.DataFrame()

@margin_shortfall_bp.route('/margin_shortfall', methods=['GET', 'POST'])
def display_margin_shortfall():
    pivot_data = None
    error = None
    selected_date = None

    if request.method == 'POST' and 'analyze' in request.form:
        selected_date = request.form.get('selected_date')
        result_df, pivot_df = analyze_margin_shortfalls(selected_date)
        if pivot_df.empty:
            error = "No margin shortfall data found for the selected date"
        else:
            pivot_data = pivot_df.to_dict(orient='records')

    return render_template('margin_shortfall.html', pivot_data=pivot_data, error=error, selected_date=selected_date)

@margin_shortfall_bp.route('/export_margin_shortfall', methods=['POST'])
def export_margin_shortfall():
    selected_date = request.form.get('selected_date')
    result_df, pivot_df = analyze_margin_shortfalls(selected_date)
    
    if result_df.empty or pivot_df.empty:
        return render_template('margin_shortfall.html', pivot_data=None, error="No data to export", selected_date=selected_date)
    
    try:
        # Create Excel file with two sheets
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            pivot_df.to_excel(writer, sheet_name='Detailed Shortfall', index=False)
            result_df.to_excel(writer, sheet_name='Summary Pivot', index=False)
        
        output.seek(0)
        filename = f"margin_shortfall_{selected_date or 'all'}.xlsx"
        return send_file(output, download_name=filename, as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        logger.error(f"Error exporting Excel file: {str(e)}")
        return render_template('margin_shortfall.html', pivot_data=None, error="Failed to export data", selected_date=selected_date)