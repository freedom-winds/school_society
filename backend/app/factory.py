from __future__ import annotations

import secrets
from collections import defaultdict
from pathlib import Path

from flask import Flask, g, jsonify

from .api import api, upload_file
from .config import Config
from .extensions import Base, db
from .models import ClubCategory, User, UserRole, UserStatus
from .services import DomainError


DEFAULT_CATEGORIES = [
    ("学术与科创", "academic", "⌁"), ("文化与艺术", "arts", "◐"), ("体育健康", "sports", "△"),
    ("公益服务", "service", "＋"), ("传媒表达", "media", "◌"), ("生活兴趣", "lifestyle", "◇"),
]


def create_app(test_config: dict | None = None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    database_path = app.config["DATABASE_URL"].replace("sqlite:///", "")
    if app.config["DATABASE_URL"].startswith("sqlite:///"):
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    with app.app_context():
        Base.metadata.create_all(db.engine)
        seed(db.session, app)
    app.registration_status_attempts = defaultdict(list)

    @app.before_request
    def request_id():
        g.request_id = secrets.token_hex(12)

    @app.errorhandler(DomainError)
    def domain_error(error):
        return jsonify({"success": False, "error": {"code": error.code, "message": error.message, "fields": error.fields}, "request_id": getattr(g, "request_id", None)}), error.status

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"success": False, "error": {"code": "NOT_FOUND", "message": "接口不存在", "fields": {}}, "request_id": getattr(g, "request_id", None)}), 404

    @app.errorhandler(413)
    def file_too_large(_error):
        return jsonify({"success": False, "error": {"code": "FILE_TOO_LARGE", "message": "上传文件超过 10 MB", "fields": {}}, "request_id": getattr(g, "request_id", None)}), 413

    app.register_blueprint(api)
    app.add_url_rule("/uploads/<path:storage_key>", "uploads", upload_file)
    return app


def seed(session, app):
    if not session.query(User).filter_by(username="admin").first():
        session.add(User(username="admin", password=app.config["INITIAL_ADMIN_PASSWORD"], display_name="系统管理员", requested_role=UserRole.ADMIN.value, role=UserRole.ADMIN.value, status=UserStatus.ACTIVE.value))
    if not session.query(ClubCategory).count():
        for order, (name, slug, icon) in enumerate(DEFAULT_CATEGORIES):
            session.add(ClubCategory(name=name, slug=slug, icon=icon, sort_order=order))
    session.commit()
