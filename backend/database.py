"""数据库与会话。

演示版是单用户单家庭的极简模型：注册/登录后自动创建家庭与授权记录，
业务查询一律带 family_id（产品安全基线：禁止仅凭资源 ID 查询）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings

# 演示账号：开发板首次启动时自动创建，登录页填这个或任意 11 位手机号都能进
DEMO_PHONE = "13800008899"
DEMO_PASSWORD = "123456"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="elder")  # elder | family
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Family(Base):
    __tablename__ = "families"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="elder")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class ConsentGrant(Base):
    """授权：写请求必须校验 consent_version，撤回后旧版本结果一律丢弃。"""

    __tablename__ = "consent_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    subject_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scope: Mapped[str] = mapped_column(String(64), default="interview,transcription,ai_processing,family_sharing")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    glyph: Mapped[str] = mapped_column(String(4))
    title: Mapped[str] = mapped_column(String(64))
    subtitle: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    topic_id: Mapped[str] = mapped_column(String(32))
    object_key: Mapped[str] = mapped_column(String(255))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    transcript: Mapped[str] = mapped_column(Text, default="")
    asr_task_id: Mapped[str] = mapped_column(String(32), default="")
    asr_status: Mapped[str] = mapped_column(String(16), default="idle")
    asr_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    topic_id: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="自然整理")
    status: Mapped[str] = mapped_column(String(24), default="pending_review")  # pending_review | confirmed
    recording_id: Mapped[str | None] = mapped_column(ForeignKey("recordings.id"), default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    memory_year: Mapped[int | None] = mapped_column(Integer, default=None)
    life_stage: Mapped[str] = mapped_column(String(24), default="未分类")
    # --- 证据链（由多智能体生成，随草稿一起保存，便于界面展示"凭什么这么说"）---
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    claims_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    conflicts_json: Mapped[str] = mapped_column(Text, default="[]")
    audit_passed: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)

    recording: Mapped[Recording | None] = relationship(lazy="joined")


class StoryRecording(Base):
    """故事与每轮原声的关联；一轮采访对应一段可独立回听的录音。"""

    __tablename__ = "story_recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    round_index: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    speaker_label: Mapped[str] = mapped_column(String(24), default="长辈")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class FamilyNote(Base):
    """家人对故事的补充或修改建议；建议不能直接改写故事正文。"""

    __tablename__ = "family_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    author_name: Mapped[str] = mapped_column(String(64), default="家人")
    kind: Mapped[str] = mapped_column(String(16), default="supplement")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)


class AuditEvent(Base):
    """不保存故事正文或录音地址的操作记录；内容删除后仍保留。"""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), default=None)
    actor_name: Mapped[str] = mapped_column(String(64), default="系统")
    action: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(16), default="story")
    summary: Mapped[str] = mapped_column(String(180))
    target_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_database() -> None:
    Base.metadata.create_all(engine)
    _ensure_story_timeline_columns()
    _ensure_story_recording_columns()
    _ensure_recording_asr_columns()
    seed()


def _ensure_story_timeline_columns() -> None:
    """为已存在的演示数据库补齐时间轴字段，不要求用户重置数据。"""
    columns = {column["name"] for column in inspect(engine).get_columns("stories")}
    statements = []
    if "memory_year" not in columns:
        statements.append("ALTER TABLE stories ADD COLUMN memory_year INTEGER")
    if "life_stage" not in columns:
        statements.append("ALTER TABLE stories ADD COLUMN life_stage VARCHAR(24) DEFAULT '未分类'")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def _ensure_story_recording_columns() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("story_recordings")}
    if "speaker_label" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE story_recordings ADD COLUMN speaker_label VARCHAR(24) DEFAULT '长辈'")
            )


def _ensure_recording_asr_columns() -> None:
    """为已有演示数据库补齐转写状态，不要求删除历史录音。"""
    columns = {column["name"] for column in inspect(engine).get_columns("recordings")}
    statements = []
    if "asr_task_id" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN asr_task_id VARCHAR(32) DEFAULT ''")
    if "asr_status" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN asr_status VARCHAR(16) DEFAULT 'idle'")
    if "asr_error" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN asr_error TEXT DEFAULT ''")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def seed() -> None:
    """写入设计稿里的四个主题与一条待确认故事示例。"""
    from .security import hash_password

    topics = [
        ("hometown", "乡", "我的家乡", "老街、河流、赶集与邻里", 1),
        ("school", "校", "上学的日子", "老师、同学与第一次离家", 2),
        ("work", "业", "工作与手艺", "第一份工作和难忘的师傅", 3),
        ("family", "家", "爱情与家庭", "相识、婚礼和成为父母", 4),
    ]
    with SessionLocal() as session:
        if not session.query(Topic).count():
            session.add_all(
                Topic(id=t[0], glyph=t[1], title=t[2], subtitle=t[3], sort_order=t[4]) for t in topics
            )
            session.commit()

        demo = session.query(User).filter_by(phone=DEMO_PHONE).one_or_none()
        if demo is None:
            import uuid

            user_id = str(uuid.uuid4())
            family_id = str(uuid.uuid4())
            demo = User(
                id=user_id,
                phone=DEMO_PHONE,
                display_name="林阿姨",
                role="elder",
                password_hash=hash_password(DEMO_PASSWORD),
            )
            session.add(demo)
            session.add(Family(id=family_id, name="林家"))
            session.add(Membership(id=str(uuid.uuid4()), family_id=family_id, user_id=user_id, role="elder"))
            session.add(ConsentGrant(id=str(uuid.uuid4()), family_id=family_id, subject_user_id=user_id))
            session.add_all(
                [
                    Story(
                        id=str(uuid.uuid4()),
                        family_id=family_id,
                        topic_id="hometown",
                        title="外婆的桂花树",
                        body=(
                            "那年秋天，院子里的桂花开得很早。\n"
                            "我和妹妹每天放学，都要绕路去看一眼。\n"
                            "风一吹，整条巷子都是甜的。"
                        ),
                        mode="自然整理",
                        status="confirmed",
                        duration_ms=222000,
                        sort_order=1,
                        memory_year=1968,
                        life_stage="童年",
                    ),
                    Story(
                        id=str(uuid.uuid4()),
                        family_id=family_id,
                        topic_id="school",
                        title="第一次离开家",
                        body="十八岁那年，我拎着一只旧皮箱，坐了一夜的绿皮火车。",
                        mode="原味口述",
                        status="confirmed",
                        duration_ms=318000,
                        sort_order=2,
                        memory_year=1976,
                        life_stage="求学",
                    ),
                    Story(
                        id=str(uuid.uuid4()),
                        family_id=family_id,
                        topic_id="family",
                        title="院子里的夏天",
                        body="竹床、蒲扇、井水冰过的西瓜，还有怎么赶也赶不走的蝉声。",
                        mode="适合成书",
                        status="confirmed",
                        duration_ms=176000,
                        sort_order=3,
                        memory_year=1988,
                        life_stage="家庭",
                    ),
                    Story(
                        id=str(uuid.uuid4()),
                        family_id=family_id,
                        topic_id="hometown",
                        title="林阿姨的故事",
                        body="",
                        mode="待家人确认",
                        status="pending_review",
                        duration_ms=0,
                        sort_order=4,
                        memory_year=1969,
                        life_stage="童年",
                    ),
                ]
            )
            session.commit()

        # 老版本演示数据升级后保留原内容，仅补充缺失的展示坐标。
        defaults = {
            1: (1968, "童年"),
            2: (1976, "求学"),
            3: (1988, "家庭"),
            4: (1969, "童年"),
        }
        changed = False
        for story in session.query(Story).filter(Story.sort_order.in_(defaults)).all():
            year, stage = defaults[story.sort_order]
            if story.memory_year is None:
                story.memory_year = year
                changed = True
            if not story.life_stage or story.life_stage == "未分类":
                story.life_stage = stage
                changed = True
        if changed:
            session.commit()


__all__ = [
    "Base",
    "AuditEvent",
    "ConsentGrant",
    "Family",
    "FamilyNote",
    "Membership",
    "Recording",
    "SessionLocal",
    "Story",
    "StoryRecording",
    "Topic",
    "User",
    "DEMO_PASSWORD",
    "DEMO_PHONE",
    "get_session",
    "init_database",
    "seed",
    "utc_now",
]
