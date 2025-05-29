# aggregate.py
from flask import render_template, request, send_file, flash, redirect, url_for, session
from sqlalchemy import text
import pandas as pd
import io
import os
from utils import get_db_function, logger

# Get the absolute path to the project root
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def aggregate_page():
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to access aggregate page", "error")
        return redirect(url_for('login.login'))
    
    if session['role'] != 'admin':
        flash("Only admins can access aggregate page", "error")
        return redirect(url_for('user.user_home'))

    engine = get_db_function()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('admin.admin_home'))

    try:
        with engine.connect() as connection:
            tables = ['summary', 'orderbook', 'gridlog', 'other']
            return render_template(os.path.join(BASE_DIR, 'aggregate_page.html'), tables=tables)
    except Exception as e:
        logger.error(f"Error loading aggregate page: {str(e)}")
        flash(f"Error loading aggregate page: {str(e)}", "error")
        return redirect(url_for('admin.admin_home'))

def aggregate(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to perform aggregation", "error")
        return redirect(url_for('login.login'))
    
    if session['role'] != 'admin':
        flash("Only admins can perform aggregation", "error")
        return redirect(url_for('view_table', table=table, page=1))

    engine = get_db_function()
    if not engine:
        flash("Database connection failed", "error")
        return redirect(url_for('admin.admin_home'))

    try:
        with engine.connect() as connection:
            columns = [col[0] for col in connection.execute(text(f"DESCRIBE `{table}`")).fetchall()]
            numerical_columns = [col[0] for col in connection.execute(text(f"DESCRIBE `{table}`")).fetchall() 
                               if col[1].startswith(('int', 'float', 'double', 'decimal'))]
            tables = ['summary', 'orderbook', 'gridlog', 'other']

            page = int(request.args.get('page', 1))
            per_page = 500
            offset = (page - 1) * per_page
            total_rows = connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
            total_pages = (total_rows + per_page - 1) // per_page
            page_range = range(1, total_pages + 1)

            result = connection.execute(text(f"SELECT * FROM `{table}` LIMIT {per_page} OFFSET {offset}"))
            data = [list(row) for row in result.fetchall()]
            row_ids = list(range(offset + 1, offset + len(data) + 1))

            aggregations = {}
            vlookup_result = None
            pivot_result = None
            calc_result = None

            if request.method == 'POST':
                if 'aggregate' in request.form:
                    agg_column = request.form['agg_column']
                    agg_function = request.form['agg_function']
                    if agg_column and agg_function:
                        query = f"SELECT {agg_function.upper()}(`{agg_column}`) as result FROM `{table}`"
                        result = connection.execute(text(query)).scalar()
                        aggregations[agg_column] = result

                elif 'vlookup' in request.form:
                    lookup_table = request.form['lookup_table']
                    lookup_column = request.form['lookup_column']
                    return_column = request.form['return_column']
                    match_column = request.form['match_column']
                    query = f"""
                        SELECT t1.*, t2.`{return_column}`
                        FROM `{table}` t1
                        LEFT JOIN `{lookup_table}` t2
                        ON t1.`{match_column}` = t2.`{lookup_column}`
                    """
                    vlookup_result = pd.read_sql(text(query), connection)

                elif 'pivot' in request.form:
                    pivot_table = request.form.get('pivot_table', '')
                    pivot_index = request.form.get('pivot_index', '')
                    pivot_columns = request.form.get('pivot_columns', '')
                    pivot_values = request.form.getlist('pivot_values')
                    pivot_aggfunc = request.form.getlist('pivot_aggfunc')
                    
                    if pivot_index and pivot_values:
                        base_query = f"SELECT * FROM `{table}`"
                        if pivot_table:
                            base_query = f"""
                                SELECT t1.*, t2.*
                                FROM `{table}` t1
                                LEFT JOIN `{pivot_table}` t2
                                ON t1.`{pivot_index}` = t2.`{pivot_index}`
                            """
                        df = pd.read_sql(text(base_query), connection)
                        pivot_result = pd.pivot_table(
                            df,
                            index=pivot_index if pivot_index else None,
                            columns=pivot_columns if pivot_columns else None,
                            values=pivot_values,
                            aggfunc=pivot_aggfunc
                        )

                elif 'calculate' in request.form:
                    calc_columns = request.form.getlist('calc_columns')
                    calc_function = request.form['calc_function']
                    if calc_columns and calc_function:
                        for column in calc_columns:
                            query = f"SELECT {calc_function.upper()}(`{column}`) as result FROM `{table}`"
                            result = connection.execute(text(query)).scalar()
                            calc_result = calc_result or {}
                            calc_result[column] = result

            session['aggregate_result'] = {
                'aggregations': aggregations,
                'vlookup_result': vlookup_result.to_json() if vlookup_result is not None else None,
                'pivot_result': pivot_result.to_json() if pivot_result is not None else None,
                'calc_result': calc_result
            }

            return render_template(os.path.join(BASE_DIR, 'aggregate.html'),
                                table_name=table,
                                columns=columns,
                                numerical_columns=numerical_columns,
                                tables=tables,
                                data=data,
                                row_ids=row_ids,
                                page=page,
                                total_pages=total_pages,
                                page_range=page_range,
                                aggregations=aggregations,
                                vlookup_result=vlookup_result,
                                pivot_result=pivot_result,
                                calc_result=calc_result)

    except Exception as e:
        logger.error(f"Error in aggregate for table {table}: {str(e)}")
        flash(f"Error performing operation: {str(e)}", "error")
        return redirect(url_for('admin.admin_aggregate_page'))

def download_aggregate(table):
    if 'role' not in session or not session['authenticated']:
        flash("Please log in to download aggregation", "error")
        return redirect(url_for('login.login'))
    
    if session['role'] != 'admin':
        flash("Only admins can download aggregation", "error")
        return redirect(url_for('view_table', table=table, page=1))

    if 'aggregate_result' not in session:
        flash("No aggregation result available to download", "error")
        return redirect(url_for('admin.admin_aggregate', table=table))

    try:
        result = session['aggregate_result']
        output = io.StringIO()
        
        if result['aggregations']:
            pd.DataFrame(result['aggregations'].items(), columns=['Column', 'Result']).to_csv(output, index=False)
        elif result['vlookup_result']:
            pd.read_json(result['vlookup_result']).to_csv(output, index=False)
        elif result['pivot_result']:
            pd.read_json(result['pivot_result']).to_csv(output)
        elif result['calc_result']:
            pd.DataFrame(result['calc_result'].items(), columns=['Column', 'Result']).to_csv(output, index=False)

        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f"{table}_aggregate.csv"
        )
    except Exception as e:
        logger.error(f"Error downloading aggregate for table {table}: {str(e)}")
        flash(f"Error downloading aggregation: {str(e)}", "error")
        return redirect(url_for('admin.admin_aggregate', table=table))