from pydantic import BaseModel, Field, field_validator


class RegisterInput(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=255)
    display_name: str = Field(min_length=1, max_length=50)
    requested_role: str
    application_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("requested_role")
    @classmethod
    def role_is_public_role(cls, value):
        if value not in {"USER", "CLUB_MANAGER"}:
            raise ValueError("申请身份只能为普通用户或社团负责人")
        return value

    def validate_application(self):
        if self.requested_role == "CLUB_MANAGER" and not (self.application_reason or "").strip():
            raise ValueError("申请社团负责人时必须填写申请说明")


class ClubDraftInput(BaseModel):
    revision_id: int | None = None
    lock_version: int | None = None
    name: str = Field(default="", max_length=30)
    category_id: int | None = None
    short_intro: str = Field(default="", max_length=100)
    recruitment_slogan: str = Field(default="", max_length=80)
    full_intro: str = Field(default="", max_length=3000)
    advisor: str | None = Field(default=None, max_length=50)
    activity_time: str | None = Field(default=None, max_length=100)
    activity_location: str | None = Field(default=None, max_length=100)
    icon_file_id: int | None = None
    poster_file_id: int | None = None
    honors: list[dict] = Field(default_factory=list)
