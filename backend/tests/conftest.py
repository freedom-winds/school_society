import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
if DEPS.exists():
    sys.path.insert(0, str(DEPS))
sys.path.insert(0, str(ROOT))

import pytest
from app import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
        "UPLOAD_DIR": tmp_path / "uploads",
        "JWT_SECRET": "test-jwt-secret",
        "SECRET_KEY": "test-secret",
    })
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username, password):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def register(client, username, role="USER", reason=None):
    payload = {"username": username, "password": "password123", "display_name": username, "requested_role": role, "application_reason": reason}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["id"]


def approve(client, admin_token, user_id, role=None):
    response = client.post(f"/api/v1/admin/users/{user_id}/approve", json=({"role": role} if role else {}), headers=headers(admin_token))
    assert response.status_code == 200, response.get_json()


def complete_revision(client, token, club_id, revision_id, lock_version=1, suffix=""):
    category = client.get("/api/v1/public/categories").get_json()["data"][0]
    payload = {
        "revision_id": revision_id, "lock_version": lock_version, "name": f"物理实验社{suffix}", "category_id": category["id"],
        "short_intro": "面向全校同学开展探究实验与科学交流的物理兴趣社团。",
        "recruitment_slogan": "把好奇心变成实验", "full_intro": "我们每周组织实验设计、主题讲座和开放讨论，鼓励成员从日常现象中提出问题、完成可验证的实验，并把严谨的科学方法带进校园生活。这里欢迎所有对物理和动手实践有兴趣的同学。社团也会组织跨年级项目组，完整记录实验失败与改进过程，让每位成员在分享中建立自己的研究习惯，并把想法落实成可以展示的作品。",
        "advisor": "张老师", "activity_time": "每周三 16:30", "activity_location": "实验楼 301", "honors": [{"title": "校园科技节一等奖", "year": 2026, "level": "校级", "description": "实验项目展示", "sort_order": 0}],
    }
    response = client.patch(f"/api/v1/clubs/{club_id}/draft", json=payload, headers=headers(token))
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]
