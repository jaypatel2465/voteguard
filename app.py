"""
Main Flask Application
VoteGuard
"""
from flask import Flask, render_template, redirect, url_for
from config import Config
from modules.security import mask_aadhar

def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY
    
    # Initialize app directories
    Config.init_app(app)
    
    # Initialize database
    from models.database import Database
    db = Database()
    
    # Register blueprints
    from routes.admin import admin_bp
    from routes.user import user_bp
    from routes.voting import voting_bp
    from routes.candidates import candidates_bp
    
    app.register_blueprint(admin_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(voting_bp)
    app.register_blueprint(candidates_bp)
    
    # Home route
    @app.route('/')
    def index():
        """Landing page"""
        return render_template('index.html')

    @app.route('/verify/<eid_hash>')
    def verify_voter_public(eid_hash):
        """Public verification endpoint for voter E-ID QR codes."""
        voter = db.get_user_by_eid_hash(eid_hash)
        if not voter:
            return render_template('verify_voter_public.html', error='Invalid or unverified voter QR.'), 404

        return render_template(
            'verify_voter_public.html',
            verified=True,
            voter=voter,
            masked_aadhar=mask_aadhar(voter.get('aadhar_last4'))
        )
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', error='Page not found'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return render_template('error.html', error='Internal server error'), 500
    
    return app

if __name__ == '__main__':
    app = create_app()
    print("=" * 60)
    print("VoteGuard")
    print("=" * 60)
    print("Admin Credentials:")
    print(f"  Email: {Config.ADMIN_EMAIL}")
    print(f"  Password: {Config.ADMIN_PASSWORD}")
    print("=" * 60)
    print("Server running at: http://127.0.0.1:5000")
    print("=" * 60)
    run_kwargs = dict(debug=True, host='0.0.0.0', port=5000)
    if Config.ENABLE_HTTPS:
        run_kwargs['ssl_context'] = 'adhoc'
    app.run(**run_kwargs)
