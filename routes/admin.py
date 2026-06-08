"""
Admin Routes
Handles admin login, candidate management, model training, and results
"""
import csv
from io import StringIO
import os
import re
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from functools import wraps
from models.database import Database
from modules.security import hash_aadhar, mask_aadhar
from config import Config
from werkzeug.utils import secure_filename

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
db = Database()
CSV_IDENTIFIER_FIELDS = ('aadhar_number', 'email', 'phone')
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def admin_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin.login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def allowed_pdf_file(filename):
    """Check if PDF file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_PDF_EXTENSIONS

def allowed_csv_file(filename):
    """Check if CSV file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_CSV_EXTENSIONS

def _normalize_aadhar(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits if len(digits) == 12 else None

def _normalize_email(value):
    email = str(value or '').strip().lower()
    return email if EMAIL_PATTERN.match(email) else None

def _normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits if len(digits) == 10 else None

def _mask_identifier(identifier_type, value):
    if not value:
        return 'Not provided'
    cleaned = str(value).strip()
    if identifier_type == 'aadhar_number':
        return mask_aadhar(cleaned)
    if identifier_type == 'phone':
        digits = re.sub(r'\D', '', cleaned)
        return f"XXXXXX{digits[-4:]}" if digits else 'Invalid phone'
    if identifier_type == 'email':
        if '@' not in cleaned:
            return 'Invalid email'
        local_part, domain = cleaned.split('@', 1)
        visible = local_part[:2] if len(local_part) >= 2 else local_part[:1]
        return f"{visible}***@{domain}"
    return cleaned

def _get_uploaded_file_size(file_storage):
    stream = file_storage.stream
    current_pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current_pos)
    return size

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login page"""
    if request.method == 'GET':
        # Clear any existing session
        session.pop('admin_logged_in', None)
        session.pop('admin_id', None) # Clear admin_id on GET login
        return render_template('admin/login.html')
    
    # POST request - handle login
    data = request.get_json() if request.is_json else request.form
    email = data.get('email')
    password = data.get('password')
    
    # Verify credentials
    admin = db.verify_admin(email, password)
    
    if admin:
        session['admin_logged_in'] = True
        session['admin_id'] = admin['id'] # Store admin_id in session
        session['admin_email'] = email
        
        if request.is_json:
            return jsonify({'success': True, 'redirect': url_for('admin.dashboard')})
        return redirect(url_for('admin.dashboard'))
    else:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
        return render_template('admin/login.html', error='Invalid credentials')

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard"""
    return render_template('admin/dashboard.html')

@admin_bp.route('/add-candidate', methods=['GET', 'POST'])
@admin_required
def add_candidate():
    """Add candidate page and handler with enhanced fields"""
    admin_id = session.get('admin_id')
    if request.method == 'GET':
        polls = db.get_polls(admin_id=admin_id)
        active_poll = db.get_active_poll(admin_id=admin_id)
        return render_template('admin/add_candidate.html', polls=polls, active_poll=active_poll)
    
    # POST request - handle form submission
    candidate_name = request.form.get('candidate_name')
    party_name = request.form.get('party_name')
    age = request.form.get('age')
    description = request.form.get('description', '')
    poll_id = request.form.get('poll_id')
    party_symbol = request.files.get('party_symbol')
    manifesto = request.files.get('manifesto')
    
    # Validate inputs
    if not candidate_name or not party_name:
        return jsonify({'success': False, 'error': 'Candidate name and party name are required'}), 400
    
    # Validate party symbol file
    symbol_filename = None
    if party_symbol and party_symbol.filename:
        if not allowed_file(party_symbol.filename):
            return jsonify({'success': False, 'error': 'Invalid party symbol file type'}), 400
        
        # Save party symbol
        symbol_filename = secure_filename(f"{party_name}_{party_symbol.filename}")
        symbol_filepath = os.path.join(Config.PARTY_SYMBOLS_FOLDER, symbol_filename)
        party_symbol.save(symbol_filepath)
    
    # Handle manifesto PDF upload
    manifesto_filename = None
    if manifesto and manifesto.filename:
        if not allowed_pdf_file(manifesto.filename):
            return jsonify({'success': False, 'error': 'Invalid manifesto file type. Only PDF files are allowed'}), 400
        
        manifesto_filename = secure_filename(f"{party_name}_manifesto_{manifesto.filename}")
        manifesto_filepath = os.path.join(Config.MANIFESTOS_FOLDER, manifesto_filename)
        manifesto.save(manifesto_filepath)
    
    # Convert age to integer
    age_int = int(age) if age and age.isdigit() else None
    
    # Convert poll_id
    poll_id_int = int(poll_id) if poll_id and str(poll_id).isdigit() else None
    admin_id = session.get('admin_id')
    if poll_id_int is None:
        active_poll = db.get_active_poll(admin_id=admin_id)
        poll_id_int = active_poll['id'] if active_poll else None

    # Ownership check — prevent cross-admin candidate injection
    if poll_id_int is not None:
        target_poll = db.get_poll_by_id(poll_id_int)
        if not target_poll or target_poll.get('admin_id') != admin_id:
            return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403

    # Add to database with enhanced fields
    result = db.add_candidate_enhanced(
        candidate_name=candidate_name,
        party_name=party_name,
        party_symbol=symbol_filename,
        poll_id=poll_id_int,
        age=age_int,
        manifesto_path=manifesto_filename,
        description=description
    )
    
    if result['success']:
        return jsonify({'success': True, 'message': 'Candidate added successfully'})
    else:
        return jsonify({'success': False, 'error': result['error']}), 400

@admin_bp.route('/candidates', methods=['GET'])
@admin_required
def get_candidates():
    """Get all candidates for the current admin's polls"""
    admin_id = session.get('admin_id')
    candidates = db.get_all_candidates(admin_id=admin_id) # Filter candidates by admin_id
    return jsonify({'success': True, 'candidates': candidates})


@admin_bp.route('/results', methods=['GET'])
@admin_required
def results():
    """View election results with dashboard"""
    admin_id = session.get('admin_id')
    poll_id = request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and poll_id.isdigit() else None
    polls = db.get_polls(admin_id=admin_id)
    # Ownership check: ensure requested poll belongs to this admin
    poll = None
    if poll_id_int:
        candidate_poll = db.get_poll_by_id(poll_id_int)
        if candidate_poll and candidate_poll.get('admin_id') == admin_id:
            poll = candidate_poll
        else:
            poll_id_int = None  # block access to another admin's poll
    results_data = db.get_results(poll_id_int, admin_id=admin_id)
    return render_template('admin/results.html', results=results_data, poll=poll, polls=polls)

@admin_bp.route('/dashboard-data', methods=['GET'])
@admin_required
def dashboard_data():
    """API endpoint for dashboard statistics and chart data"""
    admin_id = session.get('admin_id')
    poll_id = request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and poll_id.isdigit() else None
    if poll_id_int is None:
        active_poll = db.get_active_poll(admin_id=admin_id) # Filter active poll by admin_id
        poll_id_int = active_poll['id'] if active_poll else None
    stats = db.get_vote_statistics(poll_id_int, admin_id=admin_id) # Filter stats by admin_id
    hourly = db.get_hourly_turnout(poll_id_int, admin_id=admin_id) # Filter hourly turnout by admin_id
    results_data = db.get_results(poll_id_int, admin_id=admin_id)
    
    return jsonify({
        'success': True,
        'stats': {
            'total_votes': stats['total_votes'],
            'total_voters': stats['total_voters'],
            'turnout_percentage': stats['turnout_percentage'],
            'failed_attempts': stats.get('failed_attempts', 0)
        },
        'vote_share': [
            {'label': c['candidate_name'], 'value': c['vote_count']}
            for c in stats['candidates']
        ],
        'candidate_comparison': [
            {'name': c['candidate_name'], 'votes': c['vote_count']}
            for c in stats['candidates']
        ],
        'votes_by_party': [
            {'party': p['party_name'], 'votes': p['votes']}
            for p in stats.get('votes_by_party', [])
        ],
        'hourly_turnout': hourly,
        'results': results_data
    })

@admin_bp.route('/results/export/csv', methods=['GET'])
@admin_required
def export_results_csv():
    """Export election results to CSV"""
    import csv
    from io import StringIO
    admin_id = session.get('admin_id')
    poll_id = request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and poll_id.isdigit() else None
    if poll_id_int is None:
        active_poll = db.get_active_poll(admin_id=admin_id)
        poll_id_int = active_poll['id'] if active_poll else None
    else:
        target_poll = db.get_poll_by_id(poll_id_int)
        if not target_poll or target_poll.get('admin_id') != admin_id:
            return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403
    results_data = db.get_results(poll_id_int, admin_id=admin_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Rank', 'Candidate Name', 'Party Name', 'Vote Count'])
    for idx, row in enumerate(results_data, start=1):
        writer.writerow([idx, row['candidate_name'], row['party_name'], row['vote_count']])
    output.seek(0)
    return output.getvalue(), 200, {
        'Content-Type': 'text/csv',
        'Content-Disposition': 'attachment; filename="election_results.csv"'
    }

@admin_bp.route('/results/export/pdf', methods=['GET'])
@admin_required
def export_results_pdf():
    """Export election results to PDF"""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from io import BytesIO
    admin_id = session.get('admin_id')
    poll_id = request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and poll_id.isdigit() else None
    if poll_id_int is None:
        active_poll = db.get_active_poll(admin_id=admin_id)
        poll_id_int = active_poll['id'] if active_poll else None
    else:
        target_poll = db.get_poll_by_id(poll_id_int)
        if not target_poll or target_poll.get('admin_id') != admin_id:
            return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403
    results_data = db.get_results(poll_id_int, admin_id=admin_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - 50, "Election Results")

    c.setFont("Helvetica-Bold", 12)
    y = height - 90
    c.drawString(50, y, "Rank")
    c.drawString(100, y, "Candidate")
    c.drawString(280, y, "Party")
    c.drawString(450, y, "Votes")
    y -= 20

    c.setFont("Helvetica", 11)
    for idx, row in enumerate(results_data, start=1):
        if y < 60:
            c.showPage()
            y = height - 80
        c.drawString(50, y, str(idx))
        c.drawString(100, y, row['candidate_name'])
        c.drawString(280, y, row['party_name'])
        c.drawString(450, y, str(row['vote_count']))
        y -= 18

    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="election_results.pdf", mimetype="application/pdf")

@admin_bp.route('/poll-settings', methods=['GET', 'POST'])
@admin_required
def poll_settings():
    """Legacy poll settings route (redirect to polls management)"""
    return redirect(url_for('admin.manage_polls'))

@admin_bp.route('/toggle-poll', methods=['POST'])
@admin_required
def toggle_poll():
    """Legacy toggle poll route (redirect to polls management)"""
    return jsonify({'success': False, 'error': 'Use /admin/polls to manage polls.'}), 400

@admin_bp.route('/polls', methods=['GET', 'POST'])
@admin_required
def manage_polls():
    """List and create polls — scoped to the logged-in admin"""
    admin_id = session.get('admin_id')
    if request.method == 'GET':
        polls = db.get_polls(admin_id=admin_id)
        enriched = []
        for poll in polls:
            info = db.get_poll_status_info(poll)
            poll.update(info)
            enriched.append(poll)
        active_poll = db.get_active_poll(admin_id=admin_id)
        max_voter_csv_size_mb = max(1, Config.MAX_VOTER_CSV_SIZE_BYTES // (1024 * 1024))
        return render_template(
            'admin/polls.html',
            polls=enriched,
            active_poll=active_poll,
            max_voter_csv_size_bytes=Config.MAX_VOTER_CSV_SIZE_BYTES,
            max_voter_csv_size_mb=max_voter_csv_size_mb
        )

    data = request.get_json() if request.is_json else request.form
    title = data.get('title')
    description = data.get('description', '')
    poll_start = data.get('poll_start_time')
    poll_end = data.get('poll_end_time')
    manual_start = str(data.get('manual_start', '')).lower() in ('1', 'true', 'on', 'yes')
    allow_nota = str(data.get('allow_nota', '')).lower() in ('1', 'true', 'on', 'yes')

    if manual_start:
        poll_start = None
        poll_end = None

    if not title:
        return jsonify({'success': False, 'error': 'Poll title is required'}), 400
    if poll_start and poll_end and poll_start >= poll_end:
        return jsonify({'success': False, 'error': 'Start time must be before end time'}), 400

    result = db.create_poll(title, description, poll_start, poll_end, allow_nota=int(allow_nota), admin_id=admin_id)
    if result['success']:
        return jsonify({'success': True, 'message': 'Poll created successfully'})
    return jsonify({'success': False, 'error': result['error']}), 400

@admin_bp.route('/polls/<int:poll_id>/toggle', methods=['POST'])
@admin_required
def toggle_poll_status(poll_id):
    """Activate or deactivate a poll (ownership enforced)"""
    data = request.get_json() if request.is_json else request.form
    is_active = int(data.get('is_active', 0))
    result = db.toggle_poll_status(poll_id, is_active, admin_id=session.get('admin_id'))
    if result['success']:
        status = 'activated' if is_active else 'deactivated'
        return jsonify({'success': True, 'message': f'Poll {status} successfully'})
    return jsonify({'success': False, 'error': result['error']}), 400

@admin_bp.route('/polls/<int:poll_id>/delete', methods=['POST'])
@admin_required
def delete_poll(poll_id):
    """Permanently delete a poll and all its data (ownership enforced)"""
    result = db.delete_poll(poll_id, admin_id=session.get('admin_id'))
    if result['success']:
        return jsonify({'success': True, 'message': 'Poll deleted successfully'})
    return jsonify({'success': False, 'error': result['error']}), 400



@admin_bp.route('/polls/<int:poll_id>/update', methods=['POST'])
@admin_required
def update_poll_details(poll_id):
    """Update poll details (ownership enforced)"""
    data = request.get_json() if request.is_json else request.form
    title = data.get('title')
    description = data.get('description')
    poll_start = data.get('poll_start_time')
    poll_end = data.get('poll_end_time')
    manual_start = str(data.get('manual_start', '')).lower() in ('1', 'true', 'on', 'yes')
    allow_nota = data.get('allow_nota')

    if title is not None:
        title = str(title).strip()
    if description is not None:
        description = str(description).strip()
    if poll_start is not None and str(poll_start).strip() == '':
        poll_start = None
    if poll_end is not None and str(poll_end).strip() == '':
        poll_end = None
    if manual_start:
        poll_start = None
        poll_end = None
    if allow_nota is not None:
        allow_nota = int(str(allow_nota).lower() in ('1', 'true', 'on', 'yes'))

    if title is not None and not title:
        return jsonify({'success': False, 'error': 'Poll title is required'}), 400
    if poll_start and poll_end and poll_start >= poll_end:
        return jsonify({'success': False, 'error': 'Start time must be before end time'}), 400

    result = db.update_poll(
        poll_id=poll_id,
        title=title,
        description=description,
        poll_start_time=poll_start,
        poll_end_time=poll_end,
        allow_nota=allow_nota,
        admin_id=session.get('admin_id')    # ownership check
    )
    if result['success']:
        return jsonify({'success': True, 'message': 'Poll updated successfully'})
    return jsonify({'success': False, 'error': result['error']}), 400

@admin_bp.route('/polls/<int:poll_id>/results', methods=['GET'])
@admin_required
def poll_results(poll_id):
    """View results for a specific poll (ownership enforced)"""
    admin_id = session.get('admin_id')
    target_poll = db.get_poll_by_id(poll_id)
    if not target_poll or target_poll.get('admin_id') != admin_id:
        return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403
    results_data = db.get_results(poll_id, admin_id=admin_id)
    polls = db.get_polls(admin_id=admin_id)
    return render_template('admin/results.html', results=results_data, poll=target_poll, polls=polls)

@admin_bp.route('/polls/<int:poll_id>/access', methods=['GET'])
@admin_required
def get_poll_access(poll_id):
    """Return all registered users with which ones are assigned to this poll"""
    admin_id = session.get('admin_id')
    target_poll = db.get_poll_by_id(poll_id)
    if not target_poll or target_poll.get('admin_id') != admin_id:
        return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403

    all_users = db.get_all_users()       # all registered voters
    assigned = db.get_eligible_users_for_poll(poll_id)
    assigned_ids = {u['id'] for u in assigned}

    users_payload = [
        {
            'id': u['id'],
            'name': ' '.join(part for part in [u.get('first_name'), u.get('last_name')] if part).strip(),
            'email': u.get('email') or '',
            'phone': u.get('phone') or '',
            'aadhar_last4': u.get('aadhar_last4') or '',
            'assigned': u['id'] in assigned_ids
        }
        for u in all_users
    ]
    return jsonify({'success': True, 'users': users_payload, 'poll_title': target_poll['title']})

@admin_bp.route('/polls/<int:poll_id>/access', methods=['POST'])
@admin_required
def set_poll_access(poll_id):
    """Replace the user assignments for a poll"""
    admin_id = session.get('admin_id')
    target_poll = db.get_poll_by_id(poll_id)
    if not target_poll or target_poll.get('admin_id') != admin_id:
        return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403

    data = request.get_json() if request.is_json else request.form
    raw_ids = data.get('user_ids', [])
    if isinstance(raw_ids, str):
        import json as _json
        raw_ids = _json.loads(raw_ids)
    user_ids = [int(uid) for uid in raw_ids if str(uid).isdigit()]

    result = db.assign_users_to_poll(poll_id, user_ids)
    if result['success']:
        return jsonify({'success': True, 'message': f'{len(user_ids)} user(s) assigned to poll'})
    return jsonify({'success': False, 'error': result['error']}), 400


@admin_bp.route('/poll/<int:poll_id>/upload-voters', methods=['POST'])
@admin_bp.route('/polls/<int:poll_id>/upload-voters', methods=['POST'])
@admin_required
def upload_voters_to_poll(poll_id):
    """Bulk assign voters to a poll from a CSV file."""
    admin_id = session.get('admin_id')
    target_poll = db.get_poll_by_id(poll_id)
    if not target_poll or target_poll.get('admin_id') != admin_id:
        return jsonify({'success': False, 'error': 'Poll not found or access denied'}), 403

    csv_file = request.files.get('file') or request.files.get('csv_file')
    if not csv_file or not csv_file.filename:
        return jsonify({'success': False, 'error': 'CSV file is required'}), 400
    if not allowed_csv_file(csv_file.filename):
        return jsonify({'success': False, 'error': 'Invalid file type. Only CSV files are allowed'}), 400

    file_size = _get_uploaded_file_size(csv_file)
    if file_size <= 0:
        return jsonify({'success': False, 'error': 'Uploaded CSV is empty'}), 400
    if file_size > Config.MAX_VOTER_CSV_SIZE_BYTES:
        size_mb = max(1, Config.MAX_VOTER_CSV_SIZE_BYTES // (1024 * 1024))
        return jsonify({'success': False, 'error': f'CSV file is too large. Maximum size is {size_mb} MB'}), 400

    try:
        decoded_csv = csv_file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return jsonify({'success': False, 'error': 'CSV must be UTF-8 encoded'}), 400

    try:
        reader = csv.DictReader(StringIO(decoded_csv))
    except csv.Error:
        return jsonify({'success': False, 'error': 'Invalid CSV format'}), 400

    if not reader.fieldnames:
        return jsonify({'success': False, 'error': 'CSV must include a header row'}), 400

    normalized_headers = [str(header or '').strip().lower() for header in reader.fieldnames]
    if not any(field in normalized_headers for field in CSV_IDENTIFIER_FIELDS):
        return jsonify({
            'success': False,
            'error': 'CSV must include at least one identifier column: aadhar_number, email, or phone'
        }), 400

    summary = {'total': 0, 'assigned': 0, 'not_found': 0, 'duplicates': 0}
    errors = []
    seen_rows = set()
    seen_user_ids = set()
    user_ids_to_assign = []
    assigned_ids = {user['id'] for user in db.get_eligible_users_for_poll(poll_id)}

    for row_number, row in enumerate(reader, start=2):
        normalized_row = {}
        for key, value in row.items():
            if key is None:
                continue
            normalized_row[str(key).strip().lower()] = str(value or '').strip()

        if not any(value for value in normalized_row.values()):
            continue

        summary['total'] += 1
        normalized_identifiers = {}
        raw_identifier_type = None
        raw_identifier_value = None

        for field in CSV_IDENTIFIER_FIELDS:
            raw_value = normalized_row.get(field, '')
            if not raw_value:
                continue
            if raw_identifier_type is None:
                raw_identifier_type = field
                raw_identifier_value = raw_value
            if field == 'aadhar_number':
                normalized_value = _normalize_aadhar(raw_value)
            elif field == 'email':
                normalized_value = _normalize_email(raw_value)
            else:
                normalized_value = _normalize_phone(raw_value)
            if normalized_value:
                normalized_identifiers[field] = normalized_value

        display_identifier = _mask_identifier(
            raw_identifier_type or 'aadhar_number',
            raw_identifier_value
        )
        row_key = tuple(
            (field, normalized_identifiers[field])
            for field in CSV_IDENTIFIER_FIELDS
            if field in normalized_identifiers
        )

        if not normalized_identifiers:
            summary['not_found'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Missing or invalid identifier'
            })
            continue

        if row_key in seen_rows:
            summary['duplicates'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Duplicate entry in CSV'
            })
            continue
        seen_rows.add(row_key)

        matched_users = []
        ambiguous_fields = []
        for field in CSV_IDENTIFIER_FIELDS:
            if field not in normalized_identifiers:
                continue
            if field == 'aadhar_number':
                user = db.get_user_by_aadhar_hash(hash_aadhar(normalized_identifiers[field]))
                if user:
                    matched_users.append(user)
            elif field == 'email':
                user = db.get_user_by_email(normalized_identifiers[field])
                if user:
                    matched_users.append(user)
            else:
                phone_matches = db.get_users_by_phone(normalized_identifiers[field])
                if len(phone_matches) == 1:
                    matched_users.append(phone_matches[0])
                elif len(phone_matches) > 1:
                    ambiguous_fields.append('phone')

        unique_match_ids = {user['id'] for user in matched_users}
        if ambiguous_fields and not unique_match_ids:
            summary['not_found'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Phone number matches multiple voters'
            })
            continue
        if not unique_match_ids:
            summary['not_found'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Voter not found'
            })
            continue
        if len(unique_match_ids) > 1:
            summary['not_found'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Identifiers match different voters'
            })
            continue

        matched_user_id = next(iter(unique_match_ids))
        if matched_user_id in assigned_ids or matched_user_id in seen_user_ids:
            summary['duplicates'] += 1
            errors.append({
                'row': row_number,
                'identifier': display_identifier,
                'reason': 'Voter already assigned or duplicated in upload'
            })
            continue

        seen_user_ids.add(matched_user_id)
        user_ids_to_assign.append(matched_user_id)

    if user_ids_to_assign:
        result = db.add_users_to_poll(poll_id, user_ids_to_assign)
        if not result['success']:
            return jsonify({'success': False, 'error': result['error']}), 400
        summary['assigned'] = result.get('assigned', len(user_ids_to_assign))

    return jsonify({
        'success': True,
        'message': f"Processed {summary['total']} row(s) for {target_poll['title']}",
        **summary,
        'errors': errors
    })


@admin_bp.route('/verify-receipt', methods=['GET'])
@admin_required
def verify_receipt_page():
    """QR scanner page for receipt verification (admin only)"""
    return render_template('admin/verify_receipt.html')

@admin_bp.route('/verify-receipt/<receipt_hash>', methods=['GET'])
@admin_required
def verify_receipt_hash(receipt_hash):
    """Verify receipt and show details (admin only)"""
    receipt = db.get_receipt_by_hash(receipt_hash)
    if not receipt:
        return render_template('admin/verify_receipt.html', error='Invalid or unverified receipt QR.')

    user = db.get_user_by_id(receipt['user_id'])
    candidate = db.get_candidate_by_id(receipt['candidate_id'])
    poll = db.get_poll_by_id(receipt.get('poll_id')) if receipt.get('poll_id') else None

    masked_aadhar = mask_aadhar(user.get('aadhar_last4')) if user else None
    return render_template(
        'admin/verify_receipt.html',
        verified=True,
        receipt=receipt,
        voter=user,
        candidate=candidate,
        poll=poll,
        masked_aadhar=masked_aadhar
    )

@admin_bp.route('/verify-voter', methods=['GET'])
@admin_required
def verify_voter_page():
    """QR scanner page for voter E-ID verification (admin only)"""
    return render_template('admin/verify_voter.html')

@admin_bp.route('/verify-voter/<eid_hash>', methods=['GET'])
@admin_required
def verify_voter_hash(eid_hash):
    """Verify voter E-ID and show details (admin only)"""
    voter = db.get_user_by_eid_hash(eid_hash)
    if not voter:
        return render_template('admin/verify_voter.html', error='Invalid or unverified voter QR.')
    return render_template(
        'admin/verify_voter.html',
        verified=True,
        voter=voter,
        masked_aadhar=mask_aadhar(voter.get('aadhar_last4'))
    )

@admin_bp.route('/logout')
def logout():
    """Admin logout - always redirect to home page"""
    session.clear()
    return redirect(url_for('index'))

@admin_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Admin registration page - create new admin accounts"""
    if request.method == 'GET':
        return render_template('admin/register.html')

    data = request.get_json() if request.is_json else request.form
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')
    secret_key = data.get('secret_key', '')

    # Validate fields
    if not email or not password or not confirm_password:
        error = 'All fields are required'
        if request.is_json:
            return jsonify({'success': False, 'error': error}), 400
        return render_template('admin/register.html', error=error)

    if password != confirm_password:
        error = 'Passwords do not match'
        if request.is_json:
            return jsonify({'success': False, 'error': error}), 400
        return render_template('admin/register.html', error=error)

    # Require secret key to prevent unauthorised admin creation
    if secret_key != Config.ADMIN_REGISTRATION_SECRET:
        error = 'Invalid registration key'
        if request.is_json:
            return jsonify({'success': False, 'error': error}), 403
        return render_template('admin/register.html', error=error)

    result = db.create_admin(email, password)
    if result['success']:
        if request.is_json:
            return jsonify({'success': True, 'redirect': url_for('admin.login')})
        return redirect(url_for('admin.login'))
    else:
        error = result.get('error', 'Registration failed')
        if request.is_json:
            return jsonify({'success': False, 'error': error}), 400
        return render_template('admin/register.html', error=error)
