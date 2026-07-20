import io
from hashlib import sha256

from PIL import Image

from app.extensions import db
from app.models import AuditLog, Club, ClubLifecycle, ClubRevision, RefreshToken, User
from .conftest import approve, complete_revision, headers, login, register


def test_registration_review_login_status_and_leak_protection(client, app):
    admin = login(client, "admin", "Admin123!")
    user_id = register(client, "pending-user")
    assert client.post("/api/v1/auth/login", json={"username": "pending-user", "password": "password123"}).get_json()["error"]["code"] == "ACCOUNT_PENDING_REVIEW"
    wrong = client.post("/api/v1/auth/registration-status", json={"username": "does-not-exist", "password": "wrong"})
    assert wrong.status_code == 401 and wrong.get_json()["error"]["code"] == "INVALID_REGISTRATION_STATUS_CREDENTIALS"
    assert client.post("/api/v1/auth/registration-status", json={"username": "pending-user", "password": "password123"}).get_json()["data"] == {"status": "PENDING"}
    approve(client, admin, user_id)
    user_token = login(client, "pending-user", "password123")
    me = client.get("/api/v1/auth/me", headers=headers(user_token)).get_json()["data"]
    assert "password" not in me and me["role"] == "USER"
    forbidden = client.get("/api/v1/dashboard/clubs")
    assert forbidden.status_code == 401


def test_club_versions_public_visibility_and_optimistic_lock(client, app):
    admin = login(client, "admin", "Admin123!")
    manager_id = register(client, "manager-a", "CLUB_MANAGER", "现任社团负责人")
    normal_id = register(client, "normal-a")
    approve(client, admin, manager_id, "CLUB_MANAGER"); approve(client, admin, normal_id)
    normal = login(client, "normal-a", "password123")
    assert client.post("/api/v1/clubs", json={}, headers=headers(normal)).status_code == 403
    manager = login(client, "manager-a", "password123")
    created = client.post("/api/v1/clubs", json={"name": "物理实验社"}, headers=headers(manager)).get_json()["data"]
    club_id, revision_id = created["club"]["id"], created["revision"]["id"]
    assert client.get("/api/v1/public/clubs").get_json()["data"]["items"] == []
    saved = complete_revision(client, manager, club_id, revision_id)
    conflict = client.patch(f"/api/v1/clubs/{club_id}/draft", json={"revision_id": revision_id, "lock_version": 1}, headers=headers(manager))
    assert conflict.status_code == 409 and conflict.get_json()["error"]["code"] == "REVISION_CONFLICT"
    submit = client.post(f"/api/v1/clubs/{club_id}/submit", json={"revision_id": saved["id"]}, headers=headers(manager))
    assert submit.status_code == 200 and submit.get_json()["data"]["submitted_by"]
    assert client.get("/api/v1/public/clubs").get_json()["data"]["items"] == []
    approved = client.post(f"/api/v1/admin/reviews/clubs/{saved['id']}/approve", json={}, headers=headers(admin))
    assert approved.status_code == 200
    public_before = client.get("/api/v1/public/clubs").get_json()["data"]["items"]
    assert len(public_before) == 1 and public_before[0]["lifecycle_status"] == "PUBLISHED"
    revision2 = complete_revision(client, manager, club_id, None, suffix="（新版）")
    assert client.post(f"/api/v1/clubs/{club_id}/submit", json={"revision_id": revision2["id"]}, headers=headers(manager)).status_code == 200
    public_during = client.get("/api/v1/public/clubs").get_json()["data"]["items"]
    assert public_during[0]["name"] == "物理实验社"
    with app.app_context():
        club = db.session.get(Club, club_id)
        assert club.lifecycle_status == ClubLifecycle.PUBLISHED.value
        assert not hasattr(ClubLifecycle, "MODIFICATION_PENDING")
    client.post(f"/api/v1/admin/reviews/clubs/{revision2['id']}/approve", json={}, headers=headers(admin))
    assert client.get("/api/v1/public/clubs").get_json()["data"]["items"][0]["name"] == "物理实验社（新版）"


def test_position_invitation_and_president_transfer_permissions(client, app):
    admin = login(client, "admin", "Admin123!")
    manager_id = register(client, "president", "CLUB_MANAGER", "社长申请")
    vice_id = register(client, "vice-candidate")
    outsider_id = register(client, "outsider")
    approve(client, admin, manager_id, "CLUB_MANAGER"); approve(client, admin, vice_id); approve(client, admin, outsider_id)
    president = login(client, "president", "password123"); vice = login(client, "vice-candidate", "password123"); outsider = login(client, "outsider", "password123")
    created = client.post("/api/v1/clubs", json={"name": "交接测试社"}, headers=headers(president)).get_json()["data"]
    club_id = created["club"]["id"]
    invite = client.post(f"/api/v1/clubs/{club_id}/vice-presidents/invitations", json={"user_id": vice_id}, headers=headers(president))
    assert invite.status_code == 201
    invitation_id = invite.get_json()["data"]["id"]
    assert client.post(f"/api/v1/position-invitations/{invitation_id}/accept", headers=headers(vice)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers(vice)).get_json()["data"]["role"] == "CLUB_MANAGER"
    assert client.post(f"/api/v1/clubs/{club_id}/president-transfers", json={"to_user_id": vice_id}, headers=headers(outsider)).status_code == 403
    transfer = client.post(f"/api/v1/clubs/{club_id}/president-transfers", json={"to_user_id": vice_id, "message": "请接任"}, headers=headers(president))
    assert transfer.status_code == 201
    transfer_id = transfer.get_json()["data"]["id"]
    assert client.post(f"/api/v1/president-transfers/{transfer_id}/accept", headers=headers(vice)).status_code == 200
    assert client.post(f"/api/v1/president-transfers/{transfer_id}/accept", headers=headers(vice)).status_code == 404
    positions = client.get(f"/api/v1/clubs/{club_id}/positions", headers=headers(vice)).get_json()["data"]
    assert any(p["user_id"] == vice_id and p["position"] == "PRESIDENT" and p["status"] == "ACTIVE" for p in positions)
    assert any(p["user_id"] == manager_id and p["position"] == "VICE_PRESIDENT" and p["status"] == "ACTIVE" for p in positions)


def test_admin_disable_refresh_hash_audit_and_image_validation(client, app):
    admin = login(client, "admin", "Admin123!")
    user_id = register(client, "image-user")
    approve(client, admin, user_id)
    user_token = login(client, "image-user", "password123")
    assert client.get("/api/v1/admin/dashboard", headers=headers(user_token)).status_code == 403
    invalid = client.post("/api/v1/files/images", data={"file": (io.BytesIO(b"not-an-image"), "fake.png")}, headers=headers(user_token), content_type="multipart/form-data")
    assert invalid.status_code == 422
    image = Image.new("RGB", (256, 256), "navy"); buffer = io.BytesIO(); image.save(buffer, "PNG"); buffer.seek(0)
    uploaded = client.post("/api/v1/files/images", data={"file": (buffer, "club.png")}, headers=headers(user_token), content_type="multipart/form-data")
    assert uploaded.status_code == 201 and uploaded.get_json()["data"]["url"].startswith("/uploads/")
    client.post(f"/api/v1/admin/users/{user_id}/disable", headers=headers(admin))
    assert client.get("/api/v1/auth/me", headers=headers(user_token)).status_code == 403
    with app.app_context():
        logs = db.session.query(AuditLog).all()
        serialized = str([(l.before_data, l.after_data, l.metadata_json) for l in logs]).lower()
        assert "password123" not in serialized and "admin123" not in serialized
        token_rows = db.session.query(RefreshToken).all()
        assert token_rows and all(len(t.token_hash) == 64 for t in token_rows)
        assert all("password" not in t.__dict__ for t in token_rows)


def test_spec_edge_cases_admin_lifecycle_refresh_and_cross_club_permissions(client, app):
    """Covers the remaining SPEC acceptance points through two end-to-end flows."""
    admin = login(client, "admin", "Admin123!")
    president_id = register(client, "edge-president", "CLUB_MANAGER", "申请负责人")
    vice_id = register(client, "edge-vice")
    other_id = register(client, "edge-other", "CLUB_MANAGER", "另一社团负责人")
    approve(client, admin, president_id, "CLUB_MANAGER"); approve(client, admin, vice_id); approve(client, admin, other_id, "CLUB_MANAGER")
    president = login(client, "edge-president", "password123")
    vice = login(client, "edge-vice", "password123")
    other = login(client, "edge-other", "password123")
    first = client.post("/api/v1/clubs", json={"name": "边界社团"}, headers=headers(president)).get_json()["data"]
    # A raw draft explicitly permits empty submitted fields until submission.
    raw = client.get(f"/api/v1/clubs/{first['club']['id']}/revisions/{first['revision']['id']}", headers=headers(president)).get_json()["data"]
    assert raw["review_status"] == "DRAFT" and raw["submitted_by"] is None and raw["submitted_at"] is None
    second = client.post("/api/v1/clubs", json={"name": "另一边界社团"}, headers=headers(other)).get_json()["data"]
    invite = client.post(f"/api/v1/clubs/{first['club']['id']}/vice-presidents/invitations", json={"user_id": vice_id}, headers=headers(president)).get_json()["data"]
    client.post(f"/api/v1/position-invitations/{invite['id']}/accept", headers=headers(vice))
    # A vice president has no authority over an unrelated club.
    denied = client.patch(f"/api/v1/clubs/{second['club']['id']}/draft", json={"lock_version": 1}, headers=headers(vice))
    assert denied.status_code == 403
    # Refresh rotates a cookie token, and only a hashed server-side token exists.
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200 and refreshed.get_json()["data"]["access_token"]
    assert client.get("/api/v1/health").get_json()["data"] == {"status": "ok", "cache": "none", "redis": False}
    # Administrator can execute category and club lifecycle operations on every club.
    category = client.post("/api/v1/admin/categories", json={"name": "测试类别", "slug": "test-category"}, headers=headers(admin))
    assert category.status_code == 201
    cid = category.get_json()["data"]["id"]
    assert client.patch(f"/api/v1/admin/categories/{cid}", json={"sort_order": 99, "icon": "T"}, headers=headers(admin)).status_code == 200
    club_id = first["club"]["id"]
    assert client.post(f"/api/v1/admin/clubs/{club_id}/hide", headers=headers(admin)).status_code == 200
    assert client.post(f"/api/v1/admin/clubs/{club_id}/archive", headers=headers(admin)).status_code == 200
    assert client.post(f"/api/v1/admin/clubs/{club_id}/restore", headers=headers(admin)).status_code == 200
    assert client.put(f"/api/v1/admin/clubs/{club_id}/president", json={"user_id": vice_id}, headers=headers(admin)).status_code == 200
    assert client.put(f"/api/v1/admin/clubs/{club_id}/vice-presidents", json={"user_ids": [president_id]}, headers=headers(admin)).status_code == 200
    with app.app_context():
        refresh = db.session.query(RefreshToken).first()
        assert refresh and len(refresh.token_hash) == 64 and refresh.token_hash.isalnum()
