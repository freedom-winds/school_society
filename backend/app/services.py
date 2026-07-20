from __future__ import annotations

import hashlib
import io
import re
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditLog, Club, ClubCategory, ClubLifecycle, ClubPosition, ClubRevision,
    ClubRevisionHonor, PositionStatus, PositionType, PresidentTransferRequest,
    RefreshToken, ReviewStatus, TransferStatus, UploadedFile, User, UserRole,
    UserStatus, utcnow,
)


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, fields: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.fields = code, message, status, fields or {}


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower() != "password" and "token" not in k.lower()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


class AuditService:
    @staticmethod
    def record(session: Session, actor: User | None, action: str, target_type: str, target_id: int | None, before=None, after=None, request=None, metadata=None):
        session.add(AuditLog(
            actor_user_id=actor.id if actor else None, action=action, target_type=target_type, target_id=target_id,
            before_data=sanitize(before), after_data=sanitize(after), metadata_json=sanitize(metadata),
            ip_address=request.remote_addr if request else None,
            user_agent=(request.user_agent.string[:500] if request else None),
        ))


class AuthService:
    @staticmethod
    def register(session: Session, payload: dict):
        from .schemas import RegisterInput
        try:
            data = RegisterInput.model_validate(payload)
            data.validate_application()
        except Exception as exc:
            raise DomainError("VALIDATION_ERROR", str(exc), 422)
        if session.scalar(select(User).where(User.username == data.username)):
            raise DomainError("USERNAME_TAKEN", "用户名已被使用", 409)
        user = User(username=data.username, password=data.password, display_name=data.display_name.strip(), requested_role=data.requested_role, role=UserRole.USER.value, status=UserStatus.PENDING.value, application_reason=(data.application_reason or "").strip() or None)
        session.add(user)
        AuditService.record(session, None, "USER_REGISTERED", "USER", None, after={"username": data.username, "requested_role": data.requested_role})
        session.commit()
        return user

    @staticmethod
    def login(session: Session, payload: dict, config: dict, request):
        user = session.scalar(select(User).where(User.username == str(payload.get("username", ""))))
        if not user or user.password != str(payload.get("password", "")) or user.deleted_at:
            raise DomainError("INVALID_CREDENTIALS", "用户名或密码错误", 401)
        if user.status == UserStatus.PENDING.value:
            raise DomainError("ACCOUNT_PENDING_REVIEW", "账号正在等待审核", 403)
        if user.status == UserStatus.REJECTED.value:
            raise DomainError("ACCOUNT_REJECTED", "账号申请已被驳回", 403, {"rejection_reason": user.rejection_reason})
        if user.status == UserStatus.DISABLED.value:
            raise DomainError("ACCOUNT_DISABLED", "账号已被禁用", 403)
        user.last_login_at = utcnow()
        raw_refresh = secrets.token_urlsafe(48)
        session.add(RefreshToken(user_id=user.id, token_hash=hashlib.sha256(raw_refresh.encode()).hexdigest(), expires_at=utcnow() + timedelta(days=config["REFRESH_TOKEN_DAYS"]), ip_address=request.remote_addr))
        session.commit()
        return AuthService.issue_access_token(user, config), raw_refresh, user

    @staticmethod
    def issue_access_token(user: User, config):
        return jwt.encode({"sub": str(user.id), "role": user.role, "exp": utcnow() + timedelta(minutes=config["ACCESS_TOKEN_MINUTES"]), "iat": utcnow()}, config["JWT_SECRET"], algorithm="HS256")

    @staticmethod
    def refresh(session: Session, raw_token: str | None, config, request):
        if not raw_token:
            raise DomainError("REFRESH_TOKEN_REQUIRED", "刷新令牌不存在", 401)
        row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()))
        if not row or row.revoked_at or row.expires_at <= utcnow():
            raise DomainError("INVALID_REFRESH_TOKEN", "刷新令牌无效", 401)
        user = session.get(User, row.user_id)
        if not user or user.status != UserStatus.ACTIVE.value or user.deleted_at:
            raise DomainError("ACCOUNT_DISABLED", "账号不可用", 403)
        row.revoked_at = utcnow()
        new_token = secrets.token_urlsafe(48)
        session.add(RefreshToken(user_id=user.id, token_hash=hashlib.sha256(new_token.encode()).hexdigest(), expires_at=utcnow() + timedelta(days=config["REFRESH_TOKEN_DAYS"]), ip_address=request.remote_addr))
        session.commit()
        return AuthService.issue_access_token(user, config), new_token, user

    @staticmethod
    def change_password(session: Session, user: User, old_password: str, new_password: str):
        if user.password != old_password:
            raise DomainError("INVALID_OLD_PASSWORD", "旧密码错误", 400)
        if len(new_password) < 6:
            raise DomainError("VALIDATION_ERROR", "新密码至少需要 6 位", 422)
        user.password = new_password
        for token in session.scalars(select(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))):
            token.revoked_at = utcnow()
        AuditService.record(session, user, "PASSWORD_CHANGED", "USER", user.id)
        session.commit()


class PermissionService:
    @staticmethod
    def is_admin(user: User):
        return user.status == UserStatus.ACTIVE.value and user.role == UserRole.ADMIN.value

    @staticmethod
    def position(session, user_id: int, club_id: int, required: str | None = None):
        query = select(ClubPosition).where(ClubPosition.club_id == club_id, ClubPosition.user_id == user_id, ClubPosition.status == PositionStatus.ACTIVE.value)
        if required:
            query = query.where(ClubPosition.position == required)
        return session.scalar(query)

    @classmethod
    def can_create(cls, user):
        return cls.is_admin(user) or (user.status == UserStatus.ACTIVE.value and user.role == UserRole.CLUB_MANAGER.value)

    @classmethod
    def can_edit(cls, session, user, club_id):
        return cls.is_admin(user) or bool(cls.position(session, user.id, club_id))

    @classmethod
    def can_manage_positions(cls, session, user, club_id):
        return cls.is_admin(user) or bool(cls.position(session, user.id, club_id, PositionType.PRESIDENT.value))

    @classmethod
    def require_edit(cls, session, user, club_id):
        if not cls.can_edit(session, user, club_id):
            raise DomainError("FORBIDDEN", "无权编辑该社团", 403)

    @classmethod
    def require_position_manage(cls, session, user, club_id):
        if not cls.can_manage_positions(session, user, club_id):
            raise DomainError("FORBIDDEN", "仅社长或管理员可以管理人员", 403)


class ClubService:
    REQUIRED = {"name": (2, 30, "社团名称需为 2—30 字"), "short_intro": (20, 100, "短简介需为 20—100 字"), "recruitment_slogan": (5, 80, "招新语需为 5—80 字"), "full_intro": (100, 3000, "完整介绍需为 100—3000 字")}

    @staticmethod
    def slugify(name: str, session: Session):
        base = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-") or "club"
        base = base[:82]
        slug, serial = base, 1
        while session.scalar(select(Club).where(Club.slug == slug)):
            serial += 1
            slug = f"{base}-{serial}"
        return slug

    @staticmethod
    def latest_version(session, club_id):
        return (session.scalar(select(func.max(ClubRevision.version_no)).where(ClubRevision.club_id == club_id)) or 0) + 1

    @staticmethod
    def default_category_id(session: Session):
        academic = session.scalar(select(ClubCategory.id).where(ClubCategory.slug == "academic", ClubCategory.is_active.is_(True)))
        if academic:
            return academic
        return session.scalar(select(ClubCategory.id).where(ClubCategory.is_active.is_(True)).order_by(ClubCategory.sort_order, ClubCategory.id))

    @staticmethod
    def create(session: Session, actor: User, payload: dict, request=None):
        if not PermissionService.can_create(actor):
            raise DomainError("FORBIDDEN", "只有社团负责人或管理员可创建社团", 403)
        name = (payload.get("name") or "新建社团").strip()
        category_id = payload.get("category_id") or ClubService.default_category_id(session)
        category = session.get(ClubCategory, category_id) if isinstance(category_id, int) else None
        if not category or not category.is_active:
            raise DomainError("VALIDATION_ERROR", "请选择可用的社团类别", 422, {"category_id": "请选择可用的社团类别"})
        club = Club(slug=ClubService.slugify(name, session), created_by=actor.id)
        session.add(club)
        session.flush()
        revision = ClubRevision(club_id=club.id, version_no=1, name=name if name != "新建社团" else "", category_id=category.id, created_by=actor.id)
        session.add(revision)
        session.add(ClubPosition(club_id=club.id, user_id=actor.id, position=PositionType.PRESIDENT.value, status=PositionStatus.ACTIVE.value, accepted_at=utcnow()))
        AuditService.record(session, actor, "CLUB_CREATED", "CLUB", club.id, after={"slug": club.slug, "category_id": category.id}, request=request)
        session.commit()
        return club, revision

    @staticmethod
    def get_club(session, club_id: int):
        club = session.get(Club, club_id)
        if not club or club.deleted_at:
            raise DomainError("CLUB_NOT_FOUND", "社团不存在", 404)
        return club

    @staticmethod
    def copy_revision(session, source: ClubRevision, actor: User):
        revision = ClubRevision(club_id=source.club_id, version_no=ClubService.latest_version(session, source.club_id), name=source.name, category_id=source.category_id, short_intro=source.short_intro, recruitment_slogan=source.recruitment_slogan, full_intro=source.full_intro, advisor=source.advisor, activity_time=source.activity_time, activity_location=source.activity_location, icon_file_id=source.icon_file_id, poster_file_id=source.poster_file_id, created_by=actor.id)
        session.add(revision)
        session.flush()
        for honor in session.scalars(select(ClubRevisionHonor).where(ClubRevisionHonor.revision_id == source.id).order_by(ClubRevisionHonor.sort_order)):
            session.add(ClubRevisionHonor(revision_id=revision.id, title=honor.title, year=honor.year, level=honor.level, description=honor.description, sort_order=honor.sort_order))
        return revision

    @staticmethod
    def update_draft(session: Session, club: Club, actor: User, payload: dict, request=None):
        from .schemas import ClubDraftInput
        PermissionService.require_edit(session, actor, club.id)
        try:
            data = ClubDraftInput.model_validate(payload)
        except Exception as exc:
            raise DomainError("VALIDATION_ERROR", str(exc), 422)
        pending = session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id, ClubRevision.review_status == ReviewStatus.PENDING.value))
        if pending:
            raise DomainError("CLUB_REVIEW_PENDING", "该社团已有待审核版本", 409)
        revision = session.get(ClubRevision, data.revision_id) if data.revision_id else None
        if revision and (revision.club_id != club.id or revision.review_status != ReviewStatus.DRAFT.value):
            raise DomainError("DRAFT_NOT_EDITABLE", "该版本不能作为草稿编辑", 409)
        if not revision:
            source = session.get(ClubRevision, club.current_revision_id) if club.current_revision_id else session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id).order_by(ClubRevision.version_no.desc()))
            revision = ClubService.copy_revision(session, source, actor) if source else ClubRevision(club_id=club.id, version_no=ClubService.latest_version(session, club.id), created_by=actor.id)
            if not source:
                session.add(revision)
                session.flush()
        if data.lock_version is not None and data.lock_version != revision.lock_version:
            raise DomainError("REVISION_CONFLICT", "该草稿已被其他操作更新，请刷新后再试", 409)
        before = {"name": revision.name, "lock_version": revision.lock_version}
        for key, value in data.model_dump(exclude={"revision_id", "lock_version", "honors"}).items():
            setattr(revision, key, value.strip() if isinstance(value, str) else value)
        revision.lock_version += 1
        session.query(ClubRevisionHonor).filter_by(revision_id=revision.id).delete()
        for index, honor in enumerate(data.honors):
            title = str(honor.get("title", "")).strip()
            if title:
                session.add(ClubRevisionHonor(revision_id=revision.id, title=title[:100], year=honor.get("year"), level=(honor.get("level") or None), description=(honor.get("description") or None), sort_order=int(honor.get("sort_order", index))))
        AuditService.record(session, actor, "CLUB_DRAFT_SAVED", "CLUB_REVISION", revision.id, before=before, after={"name": revision.name, "lock_version": revision.lock_version}, request=request)
        session.commit()
        return revision

    @staticmethod
    def validate_submission(session: Session, revision: ClubRevision):
        fields = {}
        for key, (minimum, maximum, message) in ClubService.REQUIRED.items():
            if not minimum <= len((getattr(revision, key) or "").strip()) <= maximum:
                fields[key] = message
        category = session.get(ClubCategory, revision.category_id) if revision.category_id else None
        if not category or not category.is_active:
            fields["category_id"] = "请选择可用的社团类别"
        if fields:
            raise DomainError("VALIDATION_ERROR", "请完善社团必填信息", 422, fields)

    @staticmethod
    def submit(session: Session, club: Club, actor: User, revision_id: int | None, request=None):
        PermissionService.require_edit(session, actor, club.id)
        revision = session.get(ClubRevision, revision_id) if revision_id else session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id, ClubRevision.review_status == ReviewStatus.DRAFT.value).order_by(ClubRevision.version_no.desc()))
        if not revision or revision.club_id != club.id or revision.review_status != ReviewStatus.DRAFT.value:
            raise DomainError("DRAFT_NOT_FOUND", "没有可提交的草稿", 404)
        if session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id, ClubRevision.review_status == ReviewStatus.PENDING.value)):
            raise DomainError("CLUB_REVIEW_PENDING", "该社团已有待审核版本", 409)
        ClubService.validate_submission(session, revision)
        revision.review_status, revision.submitted_by, revision.submitted_at = ReviewStatus.PENDING.value, actor.id, utcnow()
        if club.lifecycle_status in {ClubLifecycle.DRAFT.value, ClubLifecycle.PENDING_REVIEW.value}:
            club.lifecycle_status = ClubLifecycle.PENDING_REVIEW.value
        AuditService.record(session, actor, "CLUB_SUBMITTED", "CLUB_REVISION", revision.id, after={"club_id": club.id, "version_no": revision.version_no}, request=request)
        session.commit()
        return revision

    @staticmethod
    def withdraw(session: Session, club: Club, actor: User, revision_id: int | None, request=None):
        PermissionService.require_edit(session, actor, club.id)
        revision = session.get(ClubRevision, revision_id) if revision_id else session.scalar(select(ClubRevision).where(ClubRevision.club_id == club.id, ClubRevision.review_status == ReviewStatus.PENDING.value))
        if not revision or revision.club_id != club.id or revision.review_status != ReviewStatus.PENDING.value:
            raise DomainError("PENDING_REVISION_NOT_FOUND", "没有待审核版本", 404)
        revision.review_status = ReviewStatus.WITHDRAWN.value
        if club.current_revision_id is None:
            club.lifecycle_status = ClubLifecycle.DRAFT.value
        AuditService.record(session, actor, "CLUB_REVIEW_WITHDRAWN", "CLUB_REVISION", revision.id, request=request)
        session.commit()
        return revision


class ReviewService:
    @staticmethod
    def review(session: Session, revision_id: int, admin: User, approved: bool, note: str | None, request=None):
        revision = session.get(ClubRevision, revision_id)
        if not revision or revision.review_status != ReviewStatus.PENDING.value:
            raise DomainError("PENDING_REVISION_NOT_FOUND", "待审核版本不存在", 404)
        club = session.get(Club, revision.club_id)
        if not approved and not (note or "").strip():
            raise DomainError("REVIEW_NOTE_REQUIRED", "驳回时必须填写审核意见", 422)
        revision.reviewed_by, revision.reviewed_at, revision.review_note = admin.id, utcnow(), (note or "").strip() or None
        if approved:
            previous = session.get(ClubRevision, club.current_revision_id) if club.current_revision_id else None
            if previous and previous.id != revision.id:
                previous.review_status = ReviewStatus.SUPERSEDED.value
            revision.review_status = ReviewStatus.APPROVED.value
            club.current_revision_id, club.lifecycle_status = revision.id, ClubLifecycle.PUBLISHED.value
            action = "CLUB_REVIEW_APPROVED"
        else:
            revision.review_status = ReviewStatus.REJECTED.value
            if not club.current_revision_id:
                club.lifecycle_status = ClubLifecycle.DRAFT.value
            action = "CLUB_REVIEW_REJECTED"
        AuditService.record(session, admin, action, "CLUB_REVISION", revision.id, after={"club_id": club.id, "status": revision.review_status, "note": revision.review_note}, request=request)
        session.commit()
        return revision, club


class PositionService:
    @staticmethod
    def invite_vice(session: Session, club: Club, actor: User, user_id: int, request=None):
        PermissionService.require_position_manage(session, actor, club.id)
        user = session.get(User, user_id)
        if not user or user.status != UserStatus.ACTIVE.value:
            raise DomainError("USER_NOT_ELIGIBLE", "目标用户必须是正常账号", 422)
        existing = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.user_id == user_id, ClubPosition.status.in_([PositionStatus.PENDING.value, PositionStatus.ACTIVE.value])))
        if existing:
            raise DomainError("POSITION_EXISTS", "该用户已有待处理或生效中的职务", 409)
        position = ClubPosition(club_id=club.id, user_id=user_id, position=PositionType.VICE_PRESIDENT.value, status=PositionStatus.PENDING.value, invited_by=actor.id)
        session.add(position)
        AuditService.record(session, actor, "VICE_PRESIDENT_INVITED", "CLUB_POSITION", None, after={"club_id": club.id, "user_id": user_id}, request=request)
        session.commit()
        return position

    @staticmethod
    def respond_invitation(session: Session, position_id: int, user: User, accept: bool, request=None):
        position = session.get(ClubPosition, position_id)
        if not position or position.status != PositionStatus.PENDING.value or position.user_id != user.id:
            raise DomainError("INVITATION_NOT_FOUND", "邀请不存在或无法处理", 404)
        position.status = PositionStatus.ACTIVE.value if accept else PositionStatus.REJECTED.value
        if accept:
            position.accepted_at = utcnow()
            if user.role == UserRole.USER.value:
                user.role = UserRole.CLUB_MANAGER.value
        AuditService.record(session, user, "VICE_PRESIDENT_INVITATION_ACCEPTED" if accept else "VICE_PRESIDENT_INVITATION_REJECTED", "CLUB_POSITION", position.id, request=request)
        session.commit()
        return position

    @staticmethod
    def cancel_invitation(session: Session, club: Club, actor: User, position_id: int, request=None):
        PermissionService.require_position_manage(session, actor, club.id)
        position = session.get(ClubPosition, position_id)
        if not position or position.club_id != club.id or position.status != PositionStatus.PENDING.value:
            raise DomainError("INVITATION_NOT_FOUND", "待处理邀请不存在", 404)
        position.status = PositionStatus.CANCELLED.value
        AuditService.record(session, actor, "VICE_PRESIDENT_INVITATION_CANCELLED", "CLUB_POSITION", position.id, request=request)
        session.commit()

    @staticmethod
    def remove_vice(session: Session, club: Club, actor: User, user_id: int, request=None):
        PermissionService.require_position_manage(session, actor, club.id)
        position = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.user_id == user_id, ClubPosition.position == PositionType.VICE_PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value))
        if not position:
            raise DomainError("VICE_PRESIDENT_NOT_FOUND", "该用户不是现任副社长", 404)
        position.status, position.ended_at = PositionStatus.ENDED.value, utcnow()
        AuditService.record(session, actor, "VICE_PRESIDENT_REMOVED", "CLUB_POSITION", position.id, request=request)
        session.commit()

    @staticmethod
    def admin_set_vice(session: Session, club: Club, admin: User, user_ids: list[int], request=None):
        current = list(session.scalars(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.position == PositionType.VICE_PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value)))
        wanted = set(user_ids)
        for position in current:
            if position.user_id not in wanted:
                position.status, position.ended_at = PositionStatus.ENDED.value, utcnow()
        current_ids = {p.user_id for p in current}
        for user_id in wanted - current_ids:
            user = session.get(User, user_id)
            if not user or user.status != UserStatus.ACTIVE.value:
                raise DomainError("USER_NOT_ELIGIBLE", "副社长必须为正常账号", 422)
            if PermissionService.position(session, user_id, club.id, PositionType.PRESIDENT.value):
                continue
            session.add(ClubPosition(club_id=club.id, user_id=user_id, position=PositionType.VICE_PRESIDENT.value, status=PositionStatus.ACTIVE.value, invited_by=admin.id, accepted_at=utcnow()))
            if user.role == UserRole.USER.value:
                user.role = UserRole.CLUB_MANAGER.value
        AuditService.record(session, admin, "ADMIN_VICE_PRESIDENTS_SET", "CLUB", club.id, after={"user_ids": list(wanted)}, request=request)
        session.commit()


class TransferService:
    @staticmethod
    def create(session: Session, club: Club, actor: User, to_user_id: int, message: str | None, request=None):
        PermissionService.require_position_manage(session, actor, club.id)
        target = session.get(User, to_user_id)
        if not target or target.status != UserStatus.ACTIVE.value or target.id == actor.id:
            raise DomainError("INVALID_TRANSFER_TARGET", "后任必须是另一位正常账号用户", 422)
        if session.scalar(select(PresidentTransferRequest).where(PresidentTransferRequest.club_id == club.id, PresidentTransferRequest.status == TransferStatus.PENDING.value)):
            raise DomainError("TRANSFER_PENDING", "该社团已有待处理交接", 409)
        transfer = PresidentTransferRequest(club_id=club.id, from_user_id=actor.id, to_user_id=target.id, message=(message or "").strip() or None, expires_at=utcnow() + timedelta(days=14))
        session.add(transfer)
        AuditService.record(session, actor, "PRESIDENT_TRANSFER_CREATED", "PRESIDENT_TRANSFER", None, after={"club_id": club.id, "to_user_id": target.id}, request=request)
        session.commit()
        return transfer

    @staticmethod
    def respond(session: Session, transfer_id: int, user: User, accept: bool, request=None):
        transfer = session.get(PresidentTransferRequest, transfer_id)
        if not transfer or transfer.status != TransferStatus.PENDING.value or transfer.to_user_id != user.id:
            raise DomainError("TRANSFER_NOT_FOUND", "交接不存在或无法处理", 404)
        if transfer.expires_at and transfer.expires_at <= utcnow():
            transfer.status = TransferStatus.EXPIRED.value
            session.commit()
            raise DomainError("TRANSFER_EXPIRED", "交接已过期", 409)
        if not accept:
            transfer.status, transfer.responded_at = TransferStatus.REJECTED.value, utcnow()
            AuditService.record(session, user, "PRESIDENT_TRANSFER_REJECTED", "PRESIDENT_TRANSFER", transfer.id, request=request)
            session.commit()
            return transfer
        club = session.get(Club, transfer.club_id)
        old = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.user_id == transfer.from_user_id, ClubPosition.position == PositionType.PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value))
        if not old:
            raise DomainError("TRANSFER_STALE", "原社长职务已变化", 409)
        old.status, old.ended_at = PositionStatus.ENDED.value, utcnow()
        existing_target_vice = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.user_id == user.id, ClubPosition.position == PositionType.VICE_PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value))
        if existing_target_vice:
            existing_target_vice.status, existing_target_vice.ended_at = PositionStatus.ENDED.value, utcnow()
        session.add(ClubPosition(club_id=club.id, user_id=transfer.from_user_id, position=PositionType.VICE_PRESIDENT.value, status=PositionStatus.ACTIVE.value, accepted_at=utcnow()))
        session.add(ClubPosition(club_id=club.id, user_id=user.id, position=PositionType.PRESIDENT.value, status=PositionStatus.ACTIVE.value, accepted_at=utcnow()))
        user.role = UserRole.CLUB_MANAGER.value
        transfer.status, transfer.responded_at, transfer.completed_at = TransferStatus.ACCEPTED.value, utcnow(), utcnow()
        AuditService.record(session, user, "PRESIDENT_TRANSFER_ACCEPTED", "PRESIDENT_TRANSFER", transfer.id, after={"club_id": club.id, "new_president": user.id}, request=request)
        session.commit()
        return transfer

    @staticmethod
    def cancel(session: Session, transfer_id: int, actor: User, request=None):
        transfer = session.get(PresidentTransferRequest, transfer_id)
        if not transfer or transfer.status != TransferStatus.PENDING.value:
            raise DomainError("TRANSFER_NOT_FOUND", "待处理交接不存在", 404)
        if not PermissionService.is_admin(actor) and transfer.from_user_id != actor.id:
            raise DomainError("FORBIDDEN", "无权取消该交接", 403)
        transfer.status = TransferStatus.CANCELLED.value
        AuditService.record(session, actor, "PRESIDENT_TRANSFER_CANCELLED", "PRESIDENT_TRANSFER", transfer.id, request=request)
        session.commit()
        return transfer

    @staticmethod
    def admin_set_president(session: Session, club: Club, admin: User, user_id: int, request=None):
        target = session.get(User, user_id)
        if not target or target.status != UserStatus.ACTIVE.value:
            raise DomainError("USER_NOT_ELIGIBLE", "社长必须为正常账号", 422)
        existing = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.position == PositionType.PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value))
        if existing and existing.user_id == user_id:
            return
        if existing:
            existing.status, existing.ended_at = PositionStatus.ENDED.value, utcnow()
            session.add(ClubPosition(club_id=club.id, user_id=existing.user_id, position=PositionType.VICE_PRESIDENT.value, status=PositionStatus.ACTIVE.value, accepted_at=utcnow()))
        target_vice = session.scalar(select(ClubPosition).where(ClubPosition.club_id == club.id, ClubPosition.user_id == user_id, ClubPosition.position == PositionType.VICE_PRESIDENT.value, ClubPosition.status == PositionStatus.ACTIVE.value))
        if target_vice:
            target_vice.status, target_vice.ended_at = PositionStatus.ENDED.value, utcnow()
        session.add(ClubPosition(club_id=club.id, user_id=user_id, position=PositionType.PRESIDENT.value, status=PositionStatus.ACTIVE.value, accepted_at=utcnow()))
        target.role = UserRole.CLUB_MANAGER.value
        AuditService.record(session, admin, "ADMIN_PRESIDENT_SET", "CLUB", club.id, after={"user_id": user_id}, request=request)
        session.commit()


class StorageService:
    VALID_FORMATS = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}

    @staticmethod
    def save_image(session: Session, stream, filename: str, user: User, upload_dir: Path):
        original = stream.read()
        if not original or len(original) > 10 * 1024 * 1024:
            raise DomainError("INVALID_IMAGE", "图片为空或超过 10 MB", 422)
        try:
            image = Image.open(io.BytesIO(original))
            image.verify()
            image = Image.open(io.BytesIO(original))
        except (UnidentifiedImageError, OSError):
            raise DomainError("INVALID_IMAGE", "文件不是有效图片", 422)
        fmt = image.format
        if fmt not in StorageService.VALID_FORMATS:
            raise DomainError("INVALID_IMAGE_FORMAT", "仅支持 PNG、JPEG、WebP 图片，禁止 SVG", 422)
        if max(image.size) > 4000:
            raise DomainError("INVALID_IMAGE_DIMENSION", "图片最长边不能超过 4000px", 422)
        mime, extension = StorageService.VALID_FORMATS[fmt]
        key = f"{uuid.uuid4().hex}{extension}"
        upload_dir.mkdir(parents=True, exist_ok=True)
        image.convert("RGB" if fmt == "JPEG" else image.mode).save(upload_dir / key, format=fmt, quality=90)
        record = UploadedFile(storage_key=key, original_name=Path(filename).name[:255], mime_type=mime, size=(upload_dir / key).stat().st_size, width=image.width, height=image.height, uploaded_by=user.id)
        session.add(record)
        session.commit()
        return record
