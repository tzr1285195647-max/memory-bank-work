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
    topicId: str = ""
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
    auditPassed: bool = True
    claims: list[dict] = Field(default_factory=list)
    missingFields: list[str] = Field(default_factory=list)
    findings: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    sessionId: str = ""
    recordings: list[dict] = Field(default_factory=list)
    familyNoteCount: int = 0
    memoryYear: int | None = None
    lifeStage: str = "未分类"


class StoryListOut(BaseModel):
    items: list[StoryOut]
    total: int


class StoryPatchRequest(RequestModel):
    body: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(
        default=None, pattern=r"^(原味口述|自然整理|适合成书)$"
    )
    consentVersion: int = Field(ge=1)
    memoryYear: int | None = Field(default=None, ge=1900, le=2100)
    lifeStage: str | None = Field(
        default=None, pattern=r"^(童年|求学|工作|家庭|晚年|未分类)$"
    )


class StoryActionRequest(RequestModel):
    consentVersion: int = Field(ge=1)


class StoryAuditRequest(RequestModel):
    body: str = Field(min_length=1, max_length=4000)
    consentVersion: int = Field(ge=1)


class StoryAuditOut(BaseModel):
    auditPassed: bool
    findings: list[dict] = Field(default_factory=list)


class StoryDiscardOut(BaseModel):
    discardedStoryId: str
    deletedRecordings: int
    message: str


class FamilyNoteCreateRequest(RequestModel):
    kind: str = Field(pattern=r"^(supplement|correction)$")
    content: str = Field(min_length=2, max_length=500)
    consentVersion: int = Field(ge=1)


class FamilyNoteActionRequest(RequestModel):
    action: str = Field(pattern=r"^(accept|ignore)$")
    consentVersion: int = Field(ge=1)


class FamilyNoteOut(BaseModel):
    id: str
    storyId: str
    authorName: str
    kind: str
    kindLabel: str
    content: str
    status: str
    statusLabel: str
    dayLabel: str


class RecordingOut(BaseModel):
    assetId: str
    durationMs: int
    audioUrl: str
    transcript: str
    consentVersion: int


class TranscriptionOut(BaseModel):
    recordingId: str
    status: str
    transcript: str = ""
    error: str = ""
    provider: str = "tencent"


class MemoryFragmentConfirmRequest(RequestModel):
    transcript: str = Field(min_length=1, max_length=6000)
    consentVersion: int = Field(ge=1)


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


class AuditEventOut(BaseModel):
    id: str
    action: str
    category: str
    categoryLabel: str
    summary: str
    actorName: str
    timeLabel: str
    createdAt: str


class AuditListOut(BaseModel):
    items: list[AuditEventOut]
    total: int


class HealthOut(BaseModel):
    status: str
    mode: str
    version: str
