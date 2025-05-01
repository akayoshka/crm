import os
import logging
from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from jinja2_filters import register_filters


from config import config

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # Register custom Jinja2 filters
    register_filters(app)

    # Ensure upload and log directories exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['LOG_FOLDER'], exist_ok=True)

    # Set up logging - MODIFIED TO USE FileHandler WITH 'w' MODE
    if not app.debug and not app.testing:
        # Create log file path
        log_file_path = os.path.join(app.config['LOG_FOLDER'], 'crm.log')

        # Use FileHandler with 'w' mode to overwrite the file on each startup
        file_handler = logging.FileHandler(log_file_path, mode='w')

        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)

        # Also add a stream handler to see logs in console
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        stream_handler.setLevel(logging.INFO)
        app.logger.addHandler(stream_handler)

        app.logger.setLevel(logging.INFO)
        app.logger.info('CRM startup - Log file will be overwritten on each startup')

    # Import models to ensure they are registered with SQLAlchemy
    from models import users, operations

    # Set up user loader
    @login_manager.user_loader
    def load_user(user_id):
        from models.users import User
        return User.query.get(int(user_id))

    # Register error handlers
    register_error_handlers(app)

    # Register blueprints
    from views.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint)

    from views.admin import admin as admin_blueprint
    app.register_blueprint(admin_blueprint)

    from views.main import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # Register tasks blueprint
    from views.tasks import tasks as tasks_blueprint
    app.register_blueprint(tasks_blueprint)

    # Register other blueprints
    from views.routes import routes as routes_blueprint
    app.register_blueprint(routes_blueprint)

    from views.messages import messages as messages_blueprint
    app.register_blueprint(messages_blueprint)

    from views.driver import driver as driver_blueprint
    app.register_blueprint(driver_blueprint)

    from views.statistics import statistics as statistics_blueprint
    app.register_blueprint(statistics_blueprint)

    from views.documents import documents as documents_blueprint
    app.register_blueprint(documents_blueprint)

    from views.operator import operator as operator_blueprint
    app.register_blueprint(operator_blueprint)

    from views.owner import owner as owner_blueprint
    app.register_blueprint(owner_blueprint)

    return app


def register_error_handlers(app):
    """
    Register error handlers for the Flask application

    Args:
        app: Flask application instance
    """

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template('errors/500.html'), 500

    @app.errorhandler(401)
    def unauthorized(e):
        return render_template('errors/401.html'), 401

    @app.errorhandler(400)
    def bad_request(e):
        return render_template('errors/400.html'), 400

    @app.errorhandler(405)
    def method_not_allowed(e):
        return render_template('errors/405.html'), 405

    @app.errorhandler(413)
    def request_entity_too_large(e):
        return render_template('errors/413.html'), 413

    return app