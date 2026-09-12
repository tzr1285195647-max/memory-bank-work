"""请求与响应模型（严格校验，响应只暴露公开字段）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(RequestModel):
    phone: str = Field(min_length=11, max_length=11, pattern=r"^1\d{10}$")
    password: str = Field(min_length=6, max_length=64)
    role: str = Field(default="elder", pattern=r"^(elder|family)$")


class UserOut(BaseModel):
    id: str
    displayName: str
    phoneMasked: str
    role: str


class LoginResponse(BaseModel):
    token: str
    expiresAt: str
    familyId: str
    consentVersion: int
    user: UserOut


class TopicOut(BaseModel):
    id: str
    glyph: str
    title: str
    subtitle: str


class HomeOut(BaseModel):
    today: dict
    recent: list[dict]


class StatsOut(BaseModel):
    storyCount: int
    audioMinutes: int
    memberCount: int


class ProfileOut(BaseModel):
    displayName: str
    avatarText: str
    roleLabel: str
    phoneMasked: str
    stats: StatsOut


class StoryOut(BaseModel):
    id: str
    index: str
    title: str
    body: str
    mode: str
    status: str
    durationMs: int
    durationText: str
    dayLabel: str
    audioUrl: str | None = None
    hasAudio: bool = False


class StoryListOut(BaseModel):
    items: list[StoryOut]
    total: int


class StoryPatchRequest(RequestModel):
    body: str = Field(min_length=1, max_length=4000)
    consentVersion: int = Field(ge=1)


class StoryActionRequest(RequestModel):
    consentVersion: int = Field(ge=1)


class RecordingOut(BaseModel):
    assetId: str
    durationMs: int
    audioUrl: str
    transcript: str
    consentVersion: int


class FamilyOut(BaseModel):
    memberCount: int
    invitedCount: int
    doneStories: int
    totalStories: int
    pending: list[StoryOut]


class RevokeOut(BaseModel):
    revokedAt: str
    deletedStories: int
    deletedRecordings: int
    message: str


class HealthOut(BaseModel):
    status: str
    mode: str
    version: str
