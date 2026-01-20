import os
from flask import Flask
from .config import Config
from .extensions import db, migrate
from .commands import register_cli


def create_app():
    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config())

    # Evidencias
    os.makedirs(app.config["EVID_DIR"], exist_ok=True)

    # Extensiones
    db.init_app(app)
    migrate.init_app(app, db)

    # Blueprints
    from .blueprints.admin_bp import admin_bp
    from .blueprints.whitelist_bp import whitelist_bp
    from .blueprints.bot_bp import bot_bp

    app.register_blueprint(admin_bp)
    app.register_blueprint(whitelist_bp)
    app.register_blueprint(bot_bp)

    # DB
    with app.app_context():
        db.create_all()

    # Webhook (prod) si tienes dominio público fijo:
    # from .services.telegram import set_webhook
    # set_webhook(app, f"{app.config['PUBLIC_BASE_URL']}/telegram/webhook")

    register_cli(app)

    return app
