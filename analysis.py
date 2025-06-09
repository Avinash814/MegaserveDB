from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response
from functools import wraps
from utils import get_db_connection, logger
import pandas as pd
import tempfile
import os
import io

analysis_bp = Blueprint('analysis', __name__, template_folder='templates')

def require_role(roles):
    """Decorator to check session authentication and role."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session or session.get('role', '') not in roles or not session['authenticated']:
                logger.info(f"Redirecting to login due to failed session check: {session}")
                flash("Please log in with appropriate role to access this page", "error")
                return redirect(url_for('login.login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def handle_error(e, context="Operation"):
    """Standardize error handling with logging and flashing."""
    error_msg = f"{context}: {str(e)}"
    logger.error(error_msg)
    flash(error_msg, "error")
    return error_msg

@analysis_bp.route('/analysis', methods=['GET', 'POST'])
@require_role(['admin', 'user'])
def analysis_page():
    logger.info(f"Accessing analysis_page, Session: {session}, Method: {request.method}")
    data = None
    summary_stats = None
    ce_cat_counts = None
    pe_cat_counts = None

    try:
        if request.method == 'POST':
            if request.form.get('export') == 'csv' and 'processed_data_path' in session:
                try:
                    data_path = session['processed_data_path']
                    if not os.path.exists(data_path):
                        flash("Processed data not found. Please upload the file again.", "error")
                        return redirect(url_for('analysis.analysis_page'))

                    df = pd.read_csv(data_path)
                    summary_stats = df[['CE_HEDGE_RATIO', 'PE_HEDGE_RATIO']].describe().reset_index()
                    ce_cat_counts = df['CE_HEDGE_STATUS'].value_counts().reset_index()
                    ce_cat_counts.columns = ['CE_HEDGE_STATUS', 'Count']
                    pe_cat_counts = df['PE_HEDGE_STATUS'].value_counts().reset_index()
                    pe_cat_counts.columns = ['PE_HEDGE_STATUS', 'Count']

                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df.to_excel(writer, sheet_name='Hedge Data', index=False)
                        summary_stats.to_excel(writer, sheet_name='Summary', startrow=0, index=False)
                        ce_cat_counts.to_excel(writer, sheet_name='Summary', startrow=len(summary_stats) + 2, index=False)
                        pe_cat_counts.to_excel(writer, sheet_name='Summary', startrow=len(summary_stats) + len(ce_cat_counts) + 4, index=False)

                    output.seek(0)
                    original_filename = session.get('processed_filename', 'hedge_calculations')
                    download_name = f"{original_filename.rsplit('.', 1)[0]}_processed.xlsx"
                    return Response(
                        output,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment;filename={download_name}'}
                    )
                except Exception as e:
                    handle_error(e, "Generating Excel")
                    return redirect(url_for('analysis.analysis_page'))

            if request.form.get('clear_session') == 'true':
                if 'processed_data_path' in session:
                    data_path = session.pop('processed_data_path', None)
                    if data_path and os.path.exists(data_path):
                        os.remove(data_path)
                    session.pop('processed_filename', None)
                    session.modified = True
                flash("Session data cleared", "success")
                return redirect(url_for('analysis.analysis_page'))

            if 'file_upload' not in request.files:
                flash("No file selected", "error")
                return render_template('analysis.html', role=session.get('role'))

            file = request.files['file_upload']
            if file.filename == '':
                flash("No file selected", "error")
                return render_template('analysis.html', role=session.get('role'))

            if file and file.filename.endswith('.csv'):
                try:
                    df = pd.read_csv(file)
                    if 'Status' not in df.columns:
                        flash("Missing 'Status' column in uploaded file.", "error")
                        return render_template('analysis.html', role=session.get('role'))

                    df = df[df['Status'] == 'COMPLETE']
                    if df.empty:
                        flash("No valid data with Status == 'COMPLETE'.", "error")
                        return render_template('analysis.html', role=session.get('role'))

                    required_columns = {'Symbol', 'Transaction', 'Quantity', 'User ID'}
                    if not required_columns.issubset(df.columns):
                        missing = required_columns - set(df.columns)
                        flash(f"Missing required columns: {missing}", "error")
                        return render_template('analysis.html', role=session.get('role'))

                    df['Order Time'] = pd.to_datetime(df['Order Time'], errors='coerce')
                    df.sort_values(by=['User ID', 'Order Time'], inplace=True)
                    df.reset_index(drop=True, inplace=True)

                    df['CE/PE'] = df['Symbol'].str[-2:]
                    df['CE_B'] = 0
                    df['CE_S'] = 0
                    df['PE_B'] = 0
                    df['PE_S'] = 0

                    df.loc[(df['Transaction'] == 'BUY') & (df['CE/PE'] == 'CE'), 'CE_B'] = df['Quantity']
                    df.loc[(df['Transaction'] == 'SELL') & (df['CE/PE'] == 'CE'), 'CE_S'] = df['Quantity']
                    df.loc[(df['Transaction'] == 'BUY') & (df['CE/PE'] == 'PE'), 'PE_B'] = df['Quantity']
                    df.loc[(df['Transaction'] == 'SELL') & (df['CE/PE'] == 'PE'), 'PE_S'] = df['Quantity']

                    df['CUM_CE_B'] = df.groupby('User ID')['CE_B'].cumsum()
                    df['CUM_CE_S'] = df.groupby('User ID')['CE_S'].cumsum()
                    df['CUM_PE_B'] = df.groupby('User ID')['PE_B'].cumsum()
                    df['CUM_PE_S'] = df.groupby('User ID')['PE_S'].cumsum()

                    def calculate_hedge_ratio(buy, sell):
                        return 0 if sell == 0 else round(abs(buy) / abs(sell), 2)

                    df['CE_HEDGE_RATIO'] = df.apply(lambda r: calculate_hedge_ratio(r['CUM_CE_B'], r['CUM_CE_S']), axis=1)
                    df['PE_HEDGE_RATIO'] = df.apply(lambda r: calculate_hedge_ratio(r['CUM_PE_B'], r['CUM_PE_S']), axis=1)

                    def categorize_hedge_ratio(r):
                        if r < 0.90:
                            return "CRITICAL-NOT MAINTAINED"
                        elif 0.90 <= r <= 1.20:
                            return "MAINTAINED"
                        else:
                            return "CRITICAL-EXTRA BUY"

                    df['CE_HEDGE_STATUS'] = df['CE_HEDGE_RATIO'].apply(categorize_hedge_ratio)
                    df['PE_HEDGE_STATUS'] = df['PE_HEDGE_RATIO'].apply(categorize_hedge_ratio)

                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.csv')
                    df.to_csv(temp_file.name, index=False)

                    if 'processed_data_path' in session:
                        old = session['processed_data_path']
                        if old and os.path.exists(old):
                            os.remove(old)

                    session['processed_data_path'] = temp_file.name
                    session['processed_filename'] = file.filename
                    session.modified = True

                    data = df.head(100).to_dict('records')
                    summary_stats = df[['CE_HEDGE_RATIO', 'PE_HEDGE_RATIO']].describe().to_dict()
                    ce_cat_counts = df['CE_HEDGE_STATUS'].value_counts().to_dict()
                    pe_cat_counts = df['PE_HEDGE_STATUS'].value_counts().to_dict()

                    flash("Hedge calculations completed successfully", "success")

                except Exception as e:
                    handle_error(e, "Processing file")
                    return render_template('analysis.html', role=session.get('role'))

            else:
                flash("Invalid file format. Please upload a CSV file.", "error")

        return render_template('analysis.html', role=session.get('role'), data=data,
                               summary_stats=summary_stats, ce_cat_counts=ce_cat_counts, pe_cat_counts=pe_cat_counts)

    except Exception as e:
        handle_error(e, "Unexpected error")
        return redirect(url_for('admin.admin_home'))