"""
Candidate Routes
Handles manifesto access
"""
from flask import Blueprint, send_from_directory, session, redirect, url_for, jsonify
from functools import wraps
from config import Config
import os
from werkzeug.utils import secure_filename

candidates_bp = Blueprint('candidates', __name__, url_prefix='/candidates')


def user_required(f):
    """Decorator to require user login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_logged_in' not in session:
            return redirect(url_for('user.login'))
        return f(*args, **kwargs)
    return decorated_function

@candidates_bp.route('/manifesto/<filename>')
@user_required
def manifesto(filename):
    """Serve manifesto PDF securely"""
    if not filename or not filename.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': 'Invalid file type'}), 400

    safe_name = secure_filename(filename)
    if safe_name != filename:
        return jsonify({'success': False, 'error': 'Invalid file name'}), 400

    file_path = os.path.join(Config.MANIFESTOS_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'error': 'File not found'}), 404

    return send_from_directory(Config.MANIFESTOS_FOLDER, filename, as_attachment=False)
