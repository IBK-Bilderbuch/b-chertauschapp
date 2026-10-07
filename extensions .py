"""
Zentrale Erweiterungen (Extensions) für die Flask-App.
Hier werden sie nur erzeugt, in app.py werden sie an die App gebunden
(init_app) – das vermeidet zirkuläre Imports zwischen app.py und models.py.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Bitte melde dich an, um fortzufahren."
login_manager.login_message_category = "info"

# async_mode=eventlet wird in Produktion (Render + gunicorn -k eventlet) genutzt
socketio = SocketIO(cors_allowed_origins="*")
