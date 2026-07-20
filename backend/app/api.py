from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import timedelta
from functools import wraps

import jwt
from flask import Blueprint, current_app, g, jsonify, make_response, request, send_from_directory
from sqlalchemy import and_, case, func, or_, select

from .extensions import db
from .models import (
    AuditLog, Club, ClubCategory, ClubLifecycle, ClubPosition, ClubRevision,
    PositionStatus, PositionType, PresidentTransferRequest, RefreshToken,
    ReviewStatus, TransferStatus, User, UserRole, UserStatus, utcnow,
)
from .serializers import category_data, club_data, position_data, revision_data, transfer_data, user_data
from .services import (
    AuditService, AuthService, ClubService, DomainError, PermissionService,
    PositionService, ReviewService, StorageService, TransferService,
)


api = Blueprint("api", __name__, url_prefix="/api/v1")


def result(data=None, status=200):
    return jsonify({"success": True, "data": data if data is not None else {}, "request_id": g.request_id}), status


def paginate(query, page, page_size):
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 24), 1), 60)
    total = query.count()
    return query.offset((page - 1) * page_size).limit(page_size).all(), {"page": page, "page_size": page_size, "total": total, "pages": max((total + page_size - 1) // page_size, 1)}


def parse_body():
    return request.get_json(silent=True) or {}


def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        raw = request.headers.get("Authorization", "")
        if not raw.startswith("Bearer "):
            raise DomainError("AUTH_REQUIRED", "需要登录", 401)
        try:
            token = jwt.decode(raw[7:], current_app.config["JWT_SECRET"], algorithms=["HS256"])
            user = db.session.get(User, int(token["sub"]))
        except (jwt.PyJWTError, ValueError, KeyError):
            raise DomainError("INVALID_ACCESS_TOKEN", "登录状态已失效", 401)
        if not user or user.status != UserStatus.ACTIVE.value or user.deleted_at:
            raise DomainError("ACCOUNT_DISABLED", "账号不可用", 403)
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @auth_required
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not PermissionService.is_admin(g.current_user):
            raise DomainError("FORBIDDEN", "仅管理员可执行该操作", 403)
        return fn(*args, **kwargs)
    return wrapped


def set_refresh_cookie(response, raw_token):
    response.set_cookie("refresh_token", raw_token, httponly=True, secure=False, samesite="Lax", max_age=current_app.config["REFRESH_TOKEN_DAYS"] * 86400, path="/api/v1/auth")
    return response


# Auth
@api.post("/auth/register")
def register():
    user = AuthService.register(db.session, parse_body())
    return result({"id": user.id, "username": user.username, "status": user.status}, 201)


@api.post("/auth/registration-status")
def registration_status():
    data = parse_body()
    key = f"{request.remote_addr}:{data.get('username', '')}"
    bucket = current_app.registration_status_attempts[key]
    now = utcnow().timestamp()
    bucket[:] = [time for time in bucket if now - time < 60]
    if len(bucket) >= 8:
        raise DomainError("RATE_LIMITED", "查询过于频繁，请稍后再试", 429)
    user = db.session.scalar(select(User).where(User.username == str(data.get("username", ""))))
    if not user or user.password != str(data.get("password", "")):
        bucket.append(now)
        raise DomainError("INVALID_REGISTRATION_STATUS_CREDENTIALS", "用户名或密码错误", 401)
    if user.status == UserStatus.ACTIVE.value:
        access, refresh, active_user = AuthService.login(db.session, data, current_app.config, request)
        response = make_response(result({"status": active_user.status, "access_token": access, "user": user_data(active_user)}))
        return set_refresh_cookie(response, refresh)
    payload = {"status": user.status}
    if user.status == UserStatus.REJECTED.value:
        payload["rejection_reason"] = user.rejection_reason
    return result(payload)


@api.post("/auth/resubmit-registration")
def resubmit_registration():
    data = parse_body()
    user = db.session.scalar(select(User).where(User.username == str(data.get("username", ""))))
    if not user or user.password != str(data.get("password", "")):
        raise DomainError("INVALID_REGISTRATION_STATUS_CREDENTIALS", "用户名或密码错误", 401)
    if user.status != UserStatus.REJECTED.value:
        raise DomainError("RESUBMIT_NOT_ALLOWED", "仅已驳回账号可以重新提交", 409)
    role, reason = data.get("requested_role"), (data.get("application_reason") or "").strip()
    if role not in {UserRole.USER.value, UserRole.CLUB_MANAGER.value} or (role == UserRole.CLUB_MANAGER.value and not reason):
        raise DomainError("VALIDATION_ERROR", "请填写有效的申请资料", 422)
    user.requested_role, user.application_reason, user.rejection_reason, user.reviewed_by, user.reviewed_at, user.status = role, reason or None, None, None, None, UserStatus.PENDING.value
    AuditService.record(db.session, user, "REGISTRATION_RESUBMITTED", "USER", user.id, after={"requested_role": role}, request=request)
    db.session.commit()
    return result({"status": user.status})


@api.post("/auth/login")
def login():
    access, refresh, user = AuthService.login(db.session, parse_body(), current_app.config, request)
    response = make_response(result({"access_token": access, "user": user_data(user)}))
    return set_refresh_cookie(response, refresh)


@api.post("/auth/refresh")
def refresh():
    access, refresh_token, user = AuthService.refresh(db.session, request.cookies.get("refresh_token"), current_app.config, request)
    response = make_response(result({"access_token": access, "user": user_data(user)}))
    return set_refresh_cookie(response, refresh_token)


@api.post("/auth/logout")
def logout():
    raw = request.cookies.get("refresh_token")
    if raw:
        token = db.session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hashlib.sha256(raw.encode()).hexdigest()))
        if token and not token.revoked_at:
            token.revoked_at = utcnow()
            db.session.commit()
    response = make_response(result())
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return response


@api.get("/auth/me")
@auth_required
def me():
    return result(user_data(g.current_user, detailed=True))


@api.post("/auth/change-password")
@auth_required
def change_password():
    data = parse_body()
    AuthService.change_password(db.session, g.current_user, str(data.get("old_password", "")), str(data.get("new_password", "")))
    return result()


# Public
@api.get("/public/categories")
def public_categories():
    categories = db.session.scalars(select(ClubCategory).where(ClubCategory.is_active.is_(True)).order_by(ClubCategory.sort_order, ClubCategory.name)).all()
    return result([category_data(c) for c in categories])


@api.get("/public/clubs")
def public_clubs():
    keyword, category_id, sort = (request.args.get("keyword") or "").strip(), request.args.get("category_id", type=int), request.args.get("sort", "published_desc")
    query = db.session.query(Club).filter(Club.lifecycle_status == ClubLifecycle.PUBLISHED.value, Club.current_revision_id.is_not(None), Club.deleted_at.is_(None)).join(ClubRevision, Club.current_revision_id == ClubRevision.id)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(or_(ClubRevision.name.ilike(like), ClubRevision.short_intro.ilike(like), ClubRevision.recruitment_slogan.ilike(like)))
    if category_id:
        query = query.filter(ClubRevision.category_id == category_id)
    if sort == "name_asc":
        query = query.order_by(ClubRevision.name.asc())
    elif sort == "name_desc":
        query = query.order_by(ClubRevision.name.desc())
    elif sort == "created_desc":
        query = query.order_by(Club.created_at.desc())
    else:
        query = query.order_by(Club.sort_order.desc(), Club.updated_at.desc())
    clubs, meta = paginate(query, request.args.get("page"), request.args.get("page_size"))
    return result({"items": [club_data(c, db.session) for c in clubs], "pagination": meta})


@api.get("/public/clubs/<slug>")
def public_club_detail(slug):
    club = db.session.scalar(select(Club).where(Club.slug == slug, Club.lifecycle_status == ClubLifecycle.PUBLISHED.value, Club.deleted_at.is_(None)))
    if not club or not club.current_revision_id:
        raise DomainError("CLUB_NOT_FOUND", "社团不存在或尚未发布", 404)
    data = club_data(club, db.session)
    positions = db.session.scalars(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.status == PositionStatus.ACTIVE.value).order_by(ClubPosition.position)).all()
    data["positions"] = [position_data(p, db.session) for p in positions]
    return result(data)


@api.get("/public/home")
def public_home():
    categories = db.session.scalars(select(ClubCategory).where(ClubCategory.is_active.is_(True)).order_by(ClubCategory.sort_order).limit(8)).all()
    clubs = db.session.query(Club).filter(Club.lifecycle_status == ClubLifecycle.PUBLISHED.value, Club.current_revision_id.is_not(None), Club.deleted_at.is_(None)).order_by(Club.sort_order.desc(), Club.updated_at.desc()).limit(8).all()
    return result({"categories": [category_data(c) for c in categories], "featured_clubs": [club_data(c, db.session) for c in clubs[:4]], "latest_clubs": [club_data(c, db.session) for c in clubs[4:]]})


# Club manager dashboard and revisions
@api.get("/dashboard/clubs")
@auth_required
def dashboard_clubs():
    user = g.current_user
    clubs = []
    if PermissionService.is_admin(user):
        clubs = db.session.scalars(select(Club).where(Club.deleted_at.is_(None)).order_by(Club.updated_at.desc())).all()
    else:
        ids = db.session.scalars(select(ClubPosition.club_id).where(ClubPosition.user_id == user.id, ClubPosition.status == PositionStatus.ACTIVE.value)).all()
        clubs = db.session.scalars(select(Club).where(Club.id.in_(ids), Club.deleted_at.is_(None)).order_by(Club.updated_at.desc())).all() if ids else []
    positions = db.session.scalars(select(ClubPosition).where(ClubPosition.user_id == user.id, ClubPosition.status == PositionStatus.PENDING.value)).all()
    transfers = db.session.scalars(select(PresidentTransferRequest).where(PresidentTransferRequest.to_user_id == user.id, PresidentTransferRequest.status == TransferStatus.PENDING.value)).all()
    return result({"clubs": [club_data(c, db.session, include_internal=True) for c in clubs], "position_invitations": [position_data(p, db.session) for p in positions], "transfer_invitations": [transfer_data(t, db.session) for t in transfers]})


@api.post("/clubs")
@auth_required
def create_club():
    club, revision = ClubService.create(db.session, g.current_user, parse_body(), request)
    return result({"club": club_data(club, db.session, include_internal=True), "revision": revision_data(revision, db.session)}, 201)


@api.get("/clubs/<int:club_id>")
@auth_required
def get_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    PermissionService.require_edit(db.session, g.current_user, club.id)
    return result(club_data(club, db.session, include_internal=True))


@api.patch("/clubs/<int:club_id>/draft")
@auth_required
def update_draft(club_id):
    club = ClubService.get_club(db.session, club_id)
    revision = ClubService.update_draft(db.session, club, g.current_user, parse_body(), request)
    return result(revision_data(revision, db.session))


@api.delete("/clubs/<int:club_id>/drafts/<int:revision_id>")
@auth_required
def delete_draft(club_id, revision_id):
    club = ClubService.get_club(db.session, club_id)
    ClubService.delete_draft(db.session, club, g.current_user, revision_id, request)
    return result()


@api.post("/clubs/<int:club_id>/submit")
@auth_required
def submit_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    revision = ClubService.submit(db.session, club, g.current_user, parse_body().get("revision_id"), request)
    return result(revision_data(revision, db.session))


@api.post("/clubs/<int:club_id>/withdraw")
@auth_required
def withdraw_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    revision = ClubService.withdraw(db.session, club, g.current_user, parse_body().get("revision_id"), request)
    return result(revision_data(revision, db.session))


@api.get("/clubs/<int:club_id>/revisions")
@auth_required
def club_revisions(club_id):
    club = ClubService.get_club(db.session, club_id)
    PermissionService.require_edit(db.session, g.current_user, club.id)
    revisions = db.session.scalars(select(ClubRevision).where(ClubRevision.club_id == club.id).order_by(ClubRevision.version_no.desc())).all()
    return result([revision_data(revision, db.session) for revision in revisions])


@api.get("/clubs/<int:club_id>/revisions/<int:revision_id>")
@auth_required
def club_revision(club_id, revision_id):
    club = ClubService.get_club(db.session, club_id)
    PermissionService.require_edit(db.session, g.current_user, club.id)
    revision = db.session.get(ClubRevision, revision_id)
    if not revision or revision.club_id != club.id:
        raise DomainError("REVISION_NOT_FOUND", "版本不存在", 404)
    return result(revision_data(revision, db.session))


# Position and transfer endpoints
@api.get("/clubs/<int:club_id>/positions")
@auth_required
def positions(club_id):
    club = ClubService.get_club(db.session, club_id)
    PermissionService.require_edit(db.session, g.current_user, club.id)
    values = db.session.scalars(select(ClubPosition).where(ClubPosition.club_id == club.id).order_by(ClubPosition.created_at.desc())).all()
    return result([position_data(p, db.session) for p in values])


@api.get("/users/search")
@auth_required
def search_users():
    keyword = (request.args.get("keyword") or "").strip()
    if len(keyword) < 1:
        return result([])
    like = f"%{keyword}%"
    users = db.session.scalars(select(User).where(User.status == UserStatus.ACTIVE.value, User.deleted_at.is_(None), or_(User.username.ilike(like), User.display_name.ilike(like))).order_by(User.display_name).limit(20)).all()
    return result([{key: user_data(u)[key] for key in ["id", "display_name", "username", "role"]} for u in users])


@api.post("/clubs/<int:club_id>/vice-presidents/invitations")
@auth_required
def invite_vice(club_id):
    club = ClubService.get_club(db.session, club_id)
    position = PositionService.invite_vice(db.session, club, g.current_user, int(parse_body().get("user_id", 0)), request)
    return result(position_data(position, db.session), 201)


@api.delete("/clubs/<int:club_id>/vice-presidents/invitations/<int:invitation_id>")
@auth_required
def cancel_vice_invitation(club_id, invitation_id):
    club = ClubService.get_club(db.session, club_id)
    PositionService.cancel_invitation(db.session, club, g.current_user, invitation_id, request)
    return result()


@api.post("/position-invitations/<int:position_id>/accept")
@auth_required
def accept_invitation(position_id):
    return result(position_data(PositionService.respond_invitation(db.session, position_id, g.current_user, True, request), db.session))


@api.post("/position-invitations/<int:position_id>/reject")
@auth_required
def reject_invitation(position_id):
    return result(position_data(PositionService.respond_invitation(db.session, position_id, g.current_user, False, request), db.session))


@api.delete("/clubs/<int:club_id>/vice-presidents/<int:user_id>")
@auth_required
def remove_vice(club_id, user_id):
    club = ClubService.get_club(db.session, club_id)
    PositionService.remove_vice(db.session, club, g.current_user, user_id, request)
    return result()


@api.post("/clubs/<int:club_id>/president-transfers")
@auth_required
def create_transfer(club_id):
    club = ClubService.get_club(db.session, club_id)
    data = parse_body()
    transfer = TransferService.create(db.session, club, g.current_user, int(data.get("to_user_id", 0)), data.get("message"), request)
    return result(transfer_data(transfer, db.session), 201)


@api.get("/president-transfers/pending")
@auth_required
def pending_transfers():
    transfer_values = db.session.scalars(select(PresidentTransferRequest).where(PresidentTransferRequest.to_user_id == g.current_user.id, PresidentTransferRequest.status == TransferStatus.PENDING.value)).all()
    return result([transfer_data(t, db.session) for t in transfer_values])


@api.post("/president-transfers/<int:transfer_id>/accept")
@auth_required
def accept_transfer(transfer_id):
    return result(transfer_data(TransferService.respond(db.session, transfer_id, g.current_user, True, request), db.session))


@api.post("/president-transfers/<int:transfer_id>/reject")
@auth_required
def reject_transfer(transfer_id):
    return result(transfer_data(TransferService.respond(db.session, transfer_id, g.current_user, False, request), db.session))


@api.post("/president-transfers/<int:transfer_id>/cancel")
@auth_required
def cancel_transfer(transfer_id):
    return result(transfer_data(TransferService.cancel(db.session, transfer_id, g.current_user, request), db.session))


# File storage
@api.post("/files/images")
@auth_required
def upload_image():
    file = request.files.get("file")
    if not file:
        raise DomainError("FILE_REQUIRED", "请选择图片文件", 422)
    uploaded = StorageService.save_image(db.session, file.stream, file.filename or "image", g.current_user, current_app.config["UPLOAD_DIR"])
    return result({"id": uploaded.id, "original_name": uploaded.original_name, "mime_type": uploaded.mime_type, "size": uploaded.size, "width": uploaded.width, "height": uploaded.height, "url": f"/uploads/{uploaded.storage_key}"}, 201)


# Admin
@api.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    count = lambda model, *filters: db.session.scalar(select(func.count()).select_from(model).where(*filters)) or 0
    return result({
        "users": count(User, User.deleted_at.is_(None)), "pending_users": count(User, User.status == UserStatus.PENDING.value),
        "published_clubs": count(Club, Club.lifecycle_status == ClubLifecycle.PUBLISHED.value, Club.deleted_at.is_(None)),
        "pending_clubs": count(ClubRevision, ClubRevision.review_status == ReviewStatus.PENDING.value),
        "hidden_clubs": count(Club, Club.lifecycle_status == ClubLifecycle.HIDDEN.value), "archived_clubs": count(Club, Club.lifecycle_status == ClubLifecycle.ARCHIVED.value),
        "recent_logs": [{"id": log.id, "action": log.action, "target_type": log.target_type, "target_id": log.target_id, "created_at": log.created_at.isoformat() + "Z"} for log in db.session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8))],
    })


@api.get("/admin/users")
@admin_required
def admin_users():
    query = db.session.query(User).filter(User.deleted_at.is_(None))
    keyword, status, role = (request.args.get("keyword") or "").strip(), request.args.get("status"), request.args.get("role")
    if keyword:
        query = query.filter(or_(User.username.ilike(f"%{keyword}%"), User.display_name.ilike(f"%{keyword}%")))
    if status:
        query = query.filter(User.status == status)
    if role:
        query = query.filter(User.role == role)
    users, meta = paginate(query.order_by(User.created_at.desc()), request.args.get("page"), request.args.get("page_size"))
    return result({"items": [user_data(u, detailed=True) for u in users], "pagination": meta})


@api.get("/admin/users/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user or user.deleted_at:
        raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
    club_ids = db.session.scalars(select(ClubPosition.club_id).where(ClubPosition.user_id == user.id, ClubPosition.status == PositionStatus.ACTIVE.value)).all()
    return result({"user": user_data(user, detailed=True), "clubs": [club_data(c, db.session, include_internal=True) for c in db.session.scalars(select(Club).where(Club.id.in_(club_ids))).all()] if club_ids else []})


def managed_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.deleted_at:
        raise DomainError("USER_NOT_FOUND", "用户不存在", 404)
    return user


@api.post("/admin/users/<int:user_id>/approve")
@admin_required
def approve_user(user_id):
    user = managed_user(user_id)
    if user.status != UserStatus.PENDING.value:
        raise DomainError("USER_NOT_PENDING", "该用户不处于待审核状态", 409)
    data = parse_body(); role = data.get("role", user.requested_role)
    if role not in {UserRole.USER.value, UserRole.CLUB_MANAGER.value}:
        raise DomainError("VALIDATION_ERROR", "审核身份无效", 422)
    user.status, user.role, user.reviewed_by, user.reviewed_at, user.rejection_reason = UserStatus.ACTIVE.value, role, g.current_user.id, utcnow(), None
    AuditService.record(db.session, g.current_user, "USER_APPROVED", "USER", user.id, after={"role": role}, request=request)
    db.session.commit()
    return result(user_data(user, detailed=True))


@api.post("/admin/users/<int:user_id>/reject")
@admin_required
def reject_user(user_id):
    user = managed_user(user_id); note = (parse_body().get("reason") or "").strip()
    if user.status != UserStatus.PENDING.value:
        raise DomainError("USER_NOT_PENDING", "该用户不处于待审核状态", 409)
    if not note:
        raise DomainError("REJECTION_REASON_REQUIRED", "驳回时必须填写原因", 422)
    user.status, user.rejection_reason, user.reviewed_by, user.reviewed_at = UserStatus.REJECTED.value, note, g.current_user.id, utcnow()
    AuditService.record(db.session, g.current_user, "USER_REJECTED", "USER", user.id, after={"reason": note}, request=request)
    db.session.commit()
    return result(user_data(user, detailed=True))


@api.patch("/admin/users/<int:user_id>")
@admin_required
def update_user(user_id):
    user, data = managed_user(user_id), parse_body()
    before = {"role": user.role, "display_name": user.display_name}
    if "role" in data:
        if data["role"] not in {role.value for role in UserRole}:
            raise DomainError("VALIDATION_ERROR", "身份无效", 422)
        user.role = data["role"]
    if "display_name" in data:
        user.display_name = str(data["display_name"]).strip()[:50]
    AuditService.record(db.session, g.current_user, "USER_UPDATED", "USER", user.id, before=before, after={"role": user.role, "display_name": user.display_name}, request=request)
    db.session.commit()
    return result(user_data(user, detailed=True))


@api.post("/admin/users/<int:user_id>/disable")
@admin_required
def disable_user(user_id):
    user = managed_user(user_id)
    if user.id == g.current_user.id:
        raise DomainError("SELF_DISABLE_FORBIDDEN", "不能禁用当前管理员账号", 409)
    user.status = UserStatus.DISABLED.value
    for token in db.session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))):
        token.revoked_at = utcnow()
    AuditService.record(db.session, g.current_user, "USER_DISABLED", "USER", user.id, request=request)
    db.session.commit()
    return result(user_data(user, detailed=True))


@api.post("/admin/users/<int:user_id>/enable")
@admin_required
def enable_user(user_id):
    user = managed_user(user_id)
    user.status = UserStatus.ACTIVE.value
    AuditService.record(db.session, g.current_user, "USER_ENABLED", "USER", user.id, request=request)
    db.session.commit()
    return result(user_data(user, detailed=True))


@api.post("/admin/users/<int:user_id>/reset-password")
@admin_required
def reset_password(user_id):
    user, password = managed_user(user_id), str(parse_body().get("new_password", ""))
    if len(password) < 6:
        raise DomainError("VALIDATION_ERROR", "密码至少需要 6 位", 422)
    user.password = password
    for token in db.session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))):
        token.revoked_at = utcnow()
    AuditService.record(db.session, g.current_user, "USER_PASSWORD_RESET", "USER", user.id, request=request)
    db.session.commit()
    return result()


@api.get("/admin/reviews/clubs")
@admin_required
def admin_club_reviews():
    revisions = db.session.scalars(select(ClubRevision).where(ClubRevision.review_status == ReviewStatus.PENDING.value).order_by(ClubRevision.submitted_at)).all()
    return result([revision_data(r, db.session) for r in revisions])


@api.get("/admin/reviews/clubs/<int:revision_id>")
@admin_required
def admin_review_detail(revision_id):
    revision = db.session.get(ClubRevision, revision_id)
    if not revision:
        raise DomainError("REVISION_NOT_FOUND", "版本不存在", 404)
    club = db.session.get(Club, revision.club_id)
    current = db.session.get(ClubRevision, club.current_revision_id) if club.current_revision_id else None
    return result({"pending_revision": revision_data(revision, db.session), "current_revision": revision_data(current, db.session) if current else None, "club": club_data(club, db.session, include_internal=True)})


@api.post("/admin/reviews/clubs/<int:revision_id>/approve")
@admin_required
def approve_club_review(revision_id):
    revision, club = ReviewService.review(db.session, revision_id, g.current_user, True, parse_body().get("note"), request)
    return result({"revision": revision_data(revision, db.session), "club": club_data(club, db.session, include_internal=True)})


@api.post("/admin/reviews/clubs/<int:revision_id>/reject")
@admin_required
def reject_club_review(revision_id):
    revision, club = ReviewService.review(db.session, revision_id, g.current_user, False, parse_body().get("note"), request)
    return result({"revision": revision_data(revision, db.session), "club": club_data(club, db.session, include_internal=True)})


@api.get("/admin/clubs")
@admin_required
def admin_clubs():
    query = db.session.query(Club)
    status, keyword, category_id = request.args.get("status"), (request.args.get("keyword") or "").strip(), request.args.get("category_id", type=int)
    if status:
        query = query.filter(Club.lifecycle_status == status)
    if keyword or category_id:
        latest = select(ClubRevision.club_id.label("club_id"), func.max(ClubRevision.version_no).label("version_no")).group_by(ClubRevision.club_id).subquery()
        query = query.join(latest, latest.c.club_id == Club.id).join(ClubRevision, and_(ClubRevision.club_id == latest.c.club_id, ClubRevision.version_no == latest.c.version_no))
        if keyword:
            query = query.filter(ClubRevision.name.ilike(f"%{keyword}%"))
        if category_id:
            query = query.filter(ClubRevision.category_id == category_id)
    deleted_last = case((Club.lifecycle_status == ClubLifecycle.DELETED.value, 1), else_=0)
    clubs, meta = paginate(query.order_by(deleted_last, Club.sort_order.desc(), Club.updated_at.desc()), request.args.get("page"), request.args.get("page_size"))
    return result({"items": [club_data(c, db.session, include_internal=True) for c in clubs], "pagination": meta})


@api.get("/admin/clubs/ordering")
@admin_required
def admin_club_ordering():
    clubs = db.session.scalars(select(Club).where(
        Club.lifecycle_status == ClubLifecycle.PUBLISHED.value,
        Club.current_revision_id.is_not(None),
        Club.deleted_at.is_(None),
    ).order_by(Club.sort_order.desc(), Club.updated_at.desc())).all()
    return result([club_data(club, db.session, include_internal=True) for club in clubs])


@api.put("/admin/clubs/ordering")
@admin_required
def update_admin_club_ordering():
    club_ids = parse_body().get("club_ids")
    if not isinstance(club_ids, list) or any(not isinstance(club_id, int) for club_id in club_ids) or len(set(club_ids)) != len(club_ids):
        raise DomainError("VALIDATION_ERROR", "排序内容无效", 422)
    clubs = db.session.scalars(select(Club).where(
        Club.lifecycle_status == ClubLifecycle.PUBLISHED.value,
        Club.current_revision_id.is_not(None),
        Club.deleted_at.is_(None),
    )).all()
    by_id = {club.id: club for club in clubs}
    if set(club_ids) != set(by_id):
        raise DomainError("ORDERING_CONFLICT", "社团列表已变化，请刷新后重新排序", 409)
    for index, club_id in enumerate(club_ids):
        by_id[club_id].sort_order = len(club_ids) - index
    AuditService.record(db.session, g.current_user, "CLUB_ORDER_UPDATED", "CLUB", None, after={"club_ids": club_ids}, request=request)
    db.session.commit()
    return result([club_data(by_id[club_id], db.session, include_internal=True) for club_id in club_ids])


@api.post("/admin/clubs")
@admin_required
def admin_create_club():
    club, revision = ClubService.create(db.session, g.current_user, parse_body(), request)
    return result({"club": club_data(club, db.session, include_internal=True), "revision": revision_data(revision, db.session)}, 201)


@api.patch("/admin/clubs/<int:club_id>")
@admin_required
def admin_edit_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    if not club.current_revision_id:
        revision = ClubService.update_draft(db.session, club, g.current_user, parse_body(), request)
    else:
        source = db.session.get(ClubRevision, club.current_revision_id)
        revision = ClubService.copy_revision(db.session, source, g.current_user)
        payload = dict(parse_body()); payload.update({"revision_id": revision.id, "lock_version": revision.lock_version})
        revision = ClubService.update_draft(db.session, club, g.current_user, payload, request)
        revision.review_status, revision.reviewed_by, revision.reviewed_at = ReviewStatus.APPROVED.value, g.current_user.id, utcnow()
        source.review_status, club.current_revision_id, club.lifecycle_status = ReviewStatus.SUPERSEDED.value, revision.id, ClubLifecycle.PUBLISHED.value
        AuditService.record(db.session, g.current_user, "ADMIN_CLUB_DIRECT_UPDATE", "CLUB", club.id, after={"revision_id": revision.id}, request=request)
        db.session.commit()
    return result({"club": club_data(club, db.session, include_internal=True), "revision": revision_data(revision, db.session)})


@api.post("/admin/clubs/<int:club_id>/publish")
@admin_required
def admin_publish_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    pending = db.session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id, ClubRevision.review_status == ReviewStatus.PENDING.value))
    if pending:
        revision, _ = ReviewService.review(db.session, pending.id, g.current_user, True, "管理员直接发布", request)
    else:
        revision = db.session.get(ClubRevision, club.current_revision_id)
        if not revision:
            raise DomainError("REVISION_NOT_FOUND", "社团没有可发布版本", 409)
        club.lifecycle_status = ClubLifecycle.PUBLISHED.value
        AuditService.record(db.session, g.current_user, "CLUB_PUBLISHED", "CLUB", club.id, request=request)
        db.session.commit()
    return result(club_data(club, db.session, include_internal=True))


def lifecycle_change(club_id, status, action, timestamp_field=None):
    club = ClubService.get_club(db.session, club_id)
    club.lifecycle_status = status
    if timestamp_field:
        setattr(club, timestamp_field, utcnow())
    AuditService.record(db.session, g.current_user, action, "CLUB", club.id, request=request)
    db.session.commit()
    return result(club_data(club, db.session, include_internal=True))


@api.post("/admin/clubs/<int:club_id>/hide")
@admin_required
def hide_club(club_id):
    return lifecycle_change(club_id, ClubLifecycle.HIDDEN.value, "CLUB_HIDDEN", "hidden_at")


@api.post("/admin/clubs/<int:club_id>/archive")
@admin_required
def archive_club(club_id):
    return lifecycle_change(club_id, ClubLifecycle.ARCHIVED.value, "CLUB_ARCHIVED", "archived_at")


@api.post("/admin/clubs/<int:club_id>/restore")
@admin_required
def restore_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    return lifecycle_change(club_id, ClubLifecycle.PUBLISHED.value if club.current_revision_id else ClubLifecycle.DRAFT.value, "CLUB_RESTORED")


@api.delete("/admin/clubs/<int:club_id>")
@admin_required
def delete_club(club_id):
    club = ClubService.get_club(db.session, club_id)
    club.lifecycle_status, club.deleted_at = ClubLifecycle.DELETED.value, utcnow()
    AuditService.record(db.session, g.current_user, "CLUB_DELETED", "CLUB", club.id, request=request)
    db.session.commit()
    return result()


@api.put("/admin/clubs/<int:club_id>/president")
@admin_required
def admin_set_president(club_id):
    club = ClubService.get_club(db.session, club_id)
    TransferService.admin_set_president(db.session, club, g.current_user, int(parse_body().get("user_id", 0)), request)
    return result()


@api.put("/admin/clubs/<int:club_id>/vice-presidents")
@admin_required
def admin_set_vice_presidents(club_id):
    club = ClubService.get_club(db.session, club_id)
    PositionService.admin_set_vice(db.session, club, g.current_user, [int(item) for item in parse_body().get("user_ids", [])], request)
    return result()


@api.get("/admin/categories")
@admin_required
def admin_categories():
    categories = db.session.scalars(select(ClubCategory).order_by(ClubCategory.sort_order, ClubCategory.name)).all()
    return result([category_data(c) for c in categories])


@api.post("/admin/categories")
@admin_required
def create_category():
    data, name = parse_body(), (parse_body().get("name") or "").strip()
    if not 1 <= len(name) <= 50:
        raise DomainError("VALIDATION_ERROR", "类别名称长度应为 1—50 字", 422)
    slug = (data.get("slug") or name).lower().replace(" ", "-")[:80]
    if db.session.scalar(select(ClubCategory).where(or_(ClubCategory.name == name, ClubCategory.slug == slug))):
        raise DomainError("CATEGORY_EXISTS", "类别名称或标识已存在", 409)
    category = ClubCategory(name=name, slug=slug, icon=data.get("icon"), sort_order=int(data.get("sort_order", 0)))
    db.session.add(category); AuditService.record(db.session, g.current_user, "CATEGORY_CREATED", "CATEGORY", None, after={"name": name}, request=request); db.session.commit()
    return result(category_data(category), 201)


@api.patch("/admin/categories/<int:category_id>")
@admin_required
def update_category(category_id):
    category = db.session.get(ClubCategory, category_id)
    if not category:
        raise DomainError("CATEGORY_NOT_FOUND", "类别不存在", 404)
    data = parse_body(); before = category_data(category)
    for key in ["name", "slug", "icon", "sort_order", "is_active"]:
        if key in data:
            setattr(category, key, data[key])
    AuditService.record(db.session, g.current_user, "CATEGORY_UPDATED", "CATEGORY", category.id, before=before, after=category_data(category), request=request); db.session.commit()
    return result(category_data(category))


@api.delete("/admin/categories/<int:category_id>")
@admin_required
def delete_category(category_id):
    category = db.session.get(ClubCategory, category_id)
    if not category:
        raise DomainError("CATEGORY_NOT_FOUND", "类别不存在", 404)
    category.is_active = False
    AuditService.record(db.session, g.current_user, "CATEGORY_DISABLED", "CATEGORY", category.id, request=request); db.session.commit()
    return result()


@api.get("/admin/audit-logs")
@admin_required
def audit_logs():
    query = db.session.query(AuditLog)
    if request.args.get("action"):
        query = query.filter(AuditLog.action == request.args["action"])
    logs, meta = paginate(query.order_by(AuditLog.created_at.desc()), request.args.get("page"), request.args.get("page_size"))
    return result({"items": [{"id": log.id, "actor_user_id": log.actor_user_id, "action": log.action, "target_type": log.target_type, "target_id": log.target_id, "before_data": log.before_data, "after_data": log.after_data, "metadata": log.metadata_json, "ip_address": log.ip_address, "user_agent": log.user_agent, "created_at": log.created_at.isoformat() + "Z"} for log in logs], "pagination": meta})


@api.get("/health")
def health():
    return result({"status": "ok", "cache": "none", "redis": False})


def upload_file(storage_key):
    return send_from_directory(current_app.config["UPLOAD_DIR"], storage_key, conditional=True)
