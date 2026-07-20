from __future__ import annotations

from datetime import datetime

from .models import Club, ClubCategory, ClubPosition, ClubRevision, ClubRevisionHonor, PresidentTransferRequest, UploadedFile, User


def date(value: datetime | None):
    return value.isoformat() + "Z" if value else None


def user_data(user: User, detailed: bool = False):
    data = {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "created_at": date(user.created_at),
    }
    if detailed:
        data.update({
            "requested_role": user.requested_role,
            "application_reason": user.application_reason,
            "rejection_reason": user.rejection_reason,
            "reviewed_at": date(user.reviewed_at),
            "last_login_at": date(user.last_login_at),
        })
    return data


def category_data(category: ClubCategory):
    return {"id": category.id, "name": category.name, "slug": category.slug, "icon": category.icon, "sort_order": category.sort_order, "is_active": category.is_active}


def file_url(file: UploadedFile | None):
    return f"/uploads/{file.storage_key}" if file and not file.deleted_at else None


def revision_data(revision: ClubRevision, session, detailed: bool = True):
    category = session.get(ClubCategory, revision.category_id) if revision.category_id else None
    icon = session.get(UploadedFile, revision.icon_file_id) if revision.icon_file_id else None
    poster = session.get(UploadedFile, revision.poster_file_id) if revision.poster_file_id else None
    data = {
        "id": revision.id, "club_id": revision.club_id, "version_no": revision.version_no,
        "name": revision.name, "category": category_data(category) if category else None,
        "category_id": revision.category_id, "short_intro": revision.short_intro,
        "recruitment_slogan": revision.recruitment_slogan, "full_intro": revision.full_intro,
        "advisor": revision.advisor, "activity_time": revision.activity_time, "activity_location": revision.activity_location,
        "icon_file_id": revision.icon_file_id, "icon_url": file_url(icon), "poster_file_id": revision.poster_file_id,
        "poster_url": file_url(poster), "review_status": revision.review_status, "lock_version": revision.lock_version,
        "created_at": date(revision.created_at), "updated_at": date(revision.updated_at),
    }
    if detailed:
        honors = session.query(ClubRevisionHonor).filter_by(revision_id=revision.id).order_by(ClubRevisionHonor.sort_order).all()
        data.update({
            "honors": [{"id": h.id, "title": h.title, "year": h.year, "level": h.level, "description": h.description, "sort_order": h.sort_order} for h in honors],
            "submitted_by": revision.submitted_by, "submitted_at": date(revision.submitted_at),
            "reviewed_by": revision.reviewed_by, "reviewed_at": date(revision.reviewed_at), "review_note": revision.review_note,
        })
    return data


def club_data(club: Club, session, include_current: bool = True, include_internal: bool = False):
    current = session.get(ClubRevision, club.current_revision_id) if club.current_revision_id else None
    data = {"id": club.id, "slug": club.slug, "lifecycle_status": club.lifecycle_status, "created_by": club.created_by, "created_at": date(club.created_at), "updated_at": date(club.updated_at)}
    if current and include_current:
        data["current_revision"] = revision_data(current, session)
        data.update({key: data["current_revision"][key] for key in ["name", "category", "short_intro", "recruitment_slogan", "icon_url"]})
    if include_internal:
        pending = session.query(ClubRevision).filter_by(club_id=club.id, review_status="PENDING").first()
        data["display_status"] = "MODIFICATION_PENDING" if club.lifecycle_status == "PUBLISHED" and pending else club.lifecycle_status
        data["current_revision_id"] = club.current_revision_id
    return data


def position_data(position: ClubPosition, session):
    user = session.get(User, position.user_id)
    return {"id": position.id, "club_id": position.club_id, "user": user_data(user) if user else None, "user_id": position.user_id, "position": position.position, "status": position.status, "invited_by": position.invited_by, "accepted_at": date(position.accepted_at), "ended_at": date(position.ended_at), "created_at": date(position.created_at)}


def transfer_data(transfer: PresidentTransferRequest, session):
    return {"id": transfer.id, "club_id": transfer.club_id, "from_user": user_data(session.get(User, transfer.from_user_id)), "to_user": user_data(session.get(User, transfer.to_user_id)), "status": transfer.status, "message": transfer.message, "created_at": date(transfer.created_at), "responded_at": date(transfer.responded_at), "completed_at": date(transfer.completed_at), "expires_at": date(transfer.expires_at)}
