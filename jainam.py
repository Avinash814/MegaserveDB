from flask import Blueprint, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from sqlalchemy import String, Float, Integer, Date, Text
from utils import get_db_connection  # Import from app.py's utils

# Initialize Blueprint
jainam_bp = Blueprint('jainam', __name__, template_folder='templates')

# Initialize SQLAlchemy without app binding
db = SQLAlchemy()

# Database Models
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(Integer, primary_key=True)
    sno = db.Column(Integer, nullable=True)  # From mapping
    enabled = db.Column(String(255), default='True')  # VARCHAR(255) per mapping
    user_id = db.Column(String(255), unique=True, nullable=False)
    alias = db.Column(String(255), nullable=False)
    logged_in = db.Column(String(255), default='False')
    sqoff_done = db.Column(Text, default='False')
    broker = db.Column(String(255), nullable=False)
    qty_multiplier = db.Column(Float, default=1.0)
    mtm_all = db.Column(Float, default=0.0)
    allocation = db.Column(Float, default=0.0)
    max_loss = db.Column(Float, default=0.0)
    available_margin = db.Column(Float, default=0.0)
    total_orders = db.Column(Integer, default=0)
    total_lots = db.Column(Integer, default=0)
    server = db.Column(String(255), nullable=True)
    date = db.Column(Date, nullable=True)  # Added per mapping
    algo = db.Column(String(255), nullable=True)
    remark = db.Column(String(255), nullable=True)
    operator = db.Column(String(255), nullable=True)
    dte = db.Column(String(255), nullable=True)  # Added per mapping

class Partner(db.Model):
    __tablename__ = 'partners'
    
    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(String(50), nullable=False)
    alias = db.Column(String(100), nullable=False)
    partner_name = db.Column(String(100), nullable=False)
    allocation = db.Column(Float, nullable=False)
    created_at = db.Column(Date, default=datetime.utcnow().date)

class JainamExport(db.Model):
    __tablename__ = 'jainam_exports'
    
    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(String(50), nullable=False)
    alias = db.Column(String(100), nullable=False)
    partners = db.Column(String(100), nullable=True)  # Partner name or empty
    mtm_all = db.Column(Float, default=0.0)
    allocation = db.Column(Float, default=0.0)
    mtm_percentage = db.Column(Float, default=0.0)
    date = db.Column(Date, default=datetime.utcnow().date)
    max_loss = db.Column(Float, default=0.0)
    export_date = db.Column(Date, default=datetime.utcnow().date)

# Routes
@jainam_bp.route('/')
def index():
    return render_template('jainam.html')

@jainam_bp.route('/api/users', methods=['GET'])
def get_users():
    broker_filter = request.args.get('broker', '')
    query = User.query
    
    if broker_filter:
        query = query.filter(User.broker.contains(broker_filter.upper()))
    
    users = query.all()
    users_data = []
    
    for user in users:
        users_data.append({
            'id': user.id,
            'sno': user.sno,
            'enabled': user.enabled,
            'user_id': user.user_id,
            'alias': user.alias,
            'logged_in': user.logged_in,
            'sqoff_done': user.sqoff_done,
            'broker': user.broker,
            'qty_multiplier': user.qty_multiplier,
            'mtm_all': user.mtm_all,
            'allocation': user.allocation,
            'max_loss': user.max_loss,
            'available_margin': user.available_margin,
            'total_orders': user.total_orders,
            'total_lots': user.total_lots,
            'server': user.server,
            'date': user.date.strftime('%Y-%m-%d') if user.date else None,
            'algo': user.algo,
            'remark': user.remark,
            'operator': user.operator,
            'dte': user.dte
        })
    
    return jsonify(users_data)

@jainam_bp.route('/api/partners/<user_id>', methods=['GET'])
def get_partners(user_id):
    partners = Partner.query.filter_by(user_id=user_id).all()
    partners_data = []
    
    for partner in partners:
        partners_data.append({
            'id': partner.id,
            'user_id': partner.user_id,
            'alias': partner.alias,
            'partner_name': partner.partner_name,
            'allocation': partner.allocation,
            'created_at': partner.created_at.strftime('%Y-%m-%d') if partner.created_at else None
        })
    
    return jsonify(partners_data)

@jainam_bp.route('/api/partners', methods=['POST'])
def add_partner():
    data = request.json
    
    partner = Partner(
        user_id=data['user_id'],
        alias=data['alias'],
        partner_name=data['partner_name'],
        allocation=float(data['allocation'])
    )
    
    db.session.add(partner)
    db.session.commit()
    
    return jsonify({'message': 'Partner added successfully', 'id': partner.id})

@jainam_bp.route('/api/partners/<int:partner_id>', methods=['DELETE'])
def delete_partner(partner_id):
    partner = Partner.query.get_or_404(partner_id)
    db.session.delete(partner)
    db.session.commit()
    
    return jsonify({'message': 'Partner deleted successfully'})

@jainam_bp.route('/api/export-jainam', methods=['POST'])
def export_to_jainam():
    broker_filter = request.json.get('broker', 'JAINAM')
    
    # Clear existing exports for today
    today = datetime.utcnow().date()
    JainamExport.query.filter_by(date=today).delete()
    
    # Get filtered users
    users = User.query.filter(User.broker.contains(broker_filter.upper())).all()
    
    for user in users:
        partners = Partner.query.filter_by(user_id=user.user_id).all()
        
        if partners:
            # Calculate total partner allocation
            total_partner_allocation = sum(p.allocation for p in partners)
            
            # Create main row with total allocation and MTM
            main_export = JainamExport(
                user_id=user.user_id,
                alias=user.alias,
                partners='',  # Empty for main row
                mtm_all=user.mtm_all,
                allocation=user.allocation,
                mtm_percentage=round((user.mtm_all / user.allocation * 100) if user.allocation > 0 else 0, 2),
                date=today,
                max_loss=user.max_loss
            )
            db.session.add(main_export)
            
            # Create partner rows with proportional allocation
            for partner in partners:
                partner_ratio = partner.allocation / total_partner_allocation if total_partner_allocation > 0 else 0
                partner_mtm = user.mtm_all * partner_ratio
                partner_allocation = partner.allocation
                
                partner_export = JainamExport(
                    user_id=user.user_id,
                    alias=user.alias,
                    partners=partner.partner_name,
                    mtm_all=round(partner_mtm, 2),
                    allocation=partner_allocation,
                    mtm_percentage=round((partner_mtm / partner_allocation * 100) if partner_allocation > 0 else 0, 2),
                    date=today,
                    max_loss=user.max_loss * partner_ratio
                )
                db.session.add(partner_export)
        else:
            # No partners, create single row
            single_export = JainamExport(
                user_id=user.user_id,
                alias=user.alias,
                partners='',
                mtm_all=user.mtm_all,
                allocation=user.allocation,
                mtm_percentage=round((user.mtm_all / user.allocation * 100) if user.allocation > 0 else 0, 2),
                date=today,
                max_loss=user.max_loss
            )
            db.session.add(single_export)
    
    db.session.commit()
    
    return jsonify({'message': f'Successfully exported {len(users)} users to Jainam table'})

@jainam_bp.route('/api/jainam-exports', methods=['GET'])
def get_jainam_exports():
    date_filter = request.args.get('date')
    query = JainamExport.query
    
    if date_filter:
        query = query.filter_by(date=datetime.strptime(date_filter, '%Y-%m-%d').date())
    else:
        # Get today's data by default
        query = query.filter_by(date=datetime.utcnow().date())
    
    exports = query.order_by(JainamExport.user_id, JainamExport.partners).all()
    exports_data = []
    
    for export in exports:
        exports_data.append({
            'id': export.id,
            'user_id': export.user_id,
            'alias': export.alias,
            'partners': export.partners,
            'mtm_all': export.mtm_all,
            'allocation': export.allocation,
            'mtm_percentage': export.mtm_percentage,
            'date': export.date.strftime('%Y-%m-%d') if export.date else None,
            'max_loss': export.max_loss,
            'export_date': export.export_date.strftime('%Y-%m-%d') if export.export_date else None
        })
    
    return jsonify(exports_data)

# Initialize database tables
def init_jainam_db(app):
    with app.app_context():
        engine = get_db_connection()
        if not engine:
            raise RuntimeError("Failed to get database connection")
        app.config['SQLALCHEMY_DATABASE_URI'] = engine.url
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        db.init_app(app)
        CORS(app)
        # Create only partners and jainam_exports tables
        db.create_all(bind_key=None, tables=[Partner.__table__, JainamExport.__table__])