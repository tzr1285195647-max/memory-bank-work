"""数据库与会话。

演示版是单用户单家庭的极简模型：注册/登录后自动创建家庭与授权记录，
业务查询一律带 family_id（产品安全基线：禁止仅凭资源 ID 查询）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
import re
import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings

# 演示账号：开发板首次启动时自动创建，登录页填这个或任意 11 位手机号都能进
DEMO_PHONE = "13800008899"
DEMO_PASSWORD = "123456"

DEMO_ACCOUNTS = (
    ("13800008899", "林奶奶", "elder", "female", 72, False),
    ("13900007788", "王爷爷", "elder", "male", 74, False),
    ("13700006677", "小刘", "family", "male", 35, True),
    ("13600005566", "小李", "family", "female", 32, False),
)


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
    gender: Mapped[str] = mapped_column(String(16), default="female")  # female | male
    age: Mapped[int] = mapped_column(Integer, default=60)
    active: Mapped[int] = mapped_column(Integer, default=1)
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
    is_admin: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class FamilyInvitation(Base):
    """手机号邀请；允许先邀请、后注册，适合单机多账号演示。"""

    __tablename__ = "family_invitations"
    __table_args__ = (UniqueConstraint("family_id", "phone", name="uq_family_invite_phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    invited_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(16), default="family")
    status: Mapped[str] = mapped_column(String(16), default="pending")
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
    # 讲述者在录音创建时确定，后续家人校对、整理或发布都不能覆盖。
    narrator_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    topic_id: Mapped[str] = mapped_column(String(32))
    object_key: Mapped[str] = mapped_column(String(255))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    consent_version: Mapped[int] = mapped_column(Integer, default=1)
    transcript: Mapped[str] = mapped_column(Text, default="")
    # 三份文字独立保存：原始 ASR 永不覆盖，Agent 仅给建议，confirmed 才能成为证据。
    asr_raw_text: Mapped[str] = mapped_column(Text, default="")
    agent_clean_text: Mapped[str] = mapped_column(Text, default="")
    confirmed_text: Mapped[str] = mapped_column(Text, default="")
    clean_changes_json: Mapped[str] = mapped_column(Text, default="[]")
    clean_uncertainties_json: Mapped[str] = mapped_column(Text, default="[]")
    clean_status: Mapped[str] = mapped_column(String(24), default="idle")
    clean_provider: Mapped[str] = mapped_column(String(64), default="")
    fragment_order: Mapped[int] = mapped_column(Integer, default=0)
    fragment_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(36), default=None)
    asr_task_id: Mapped[str] = mapped_column(String(32), default="")
    asr_status: Mapped[str] = mapped_column(String(16), default="idle")
    asr_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    narrator_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
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
    draft_sentences_json: Mapped[str] = mapped_column(Text, default="[]")
    audit_suggestions_json: Mapped[str] = mapped_column(Text, default="[]")
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
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


class MemoryFact(Base):
    """从已确认碎片抽取、可回溯到原文子串的结构化事实。"""

    __tablename__ = "memory_facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    fragment_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    narrator_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    element: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), default="0.8")
    status: Mapped[str] = mapped_column(String(24), default="confident")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class AgentCallLog(Base):
    """不保存正文，只保存模型运行元数据和输入证据 ID。"""

    __tablename__ = "agent_call_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(48), index=True)
    agent_version: Mapped[str] = mapped_column(String(24), default="p0-v1")
    model: Mapped[str] = mapped_column(String(96), default="")
    prompt_version: Mapped[str] = mapped_column(String(24), default="p0-v1")
    input_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fallback_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(240), default="")
    outcome: Mapped[str] = mapped_column(String(24), default="generated")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_database() -> None:
    Base.metadata.create_all(engine)
    _ensure_user_profile_columns()
    _ensure_membership_columns()
    _ensure_story_timeline_columns()
    _ensure_story_recording_columns()
    _ensure_recording_asr_columns()
    _ensure_recording_fragment_columns()
    _ensure_narrator_columns()
    _ensure_agent_p0_columns()
    seed()
    _repair_story_attributions()


def _ensure_user_profile_columns() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    statements = []
    if "gender" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN gender VARCHAR(16) DEFAULT 'female'")
    if "age" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN age INTEGER DEFAULT 60")
    if "active" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def _ensure_membership_columns() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("memberships")}
    if "is_admin" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE memberships ADD COLUMN is_admin INTEGER DEFAULT 0"))


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


def _ensure_recording_fragment_columns() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("recordings")}
    statements = []
    if "fragment_order" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN fragment_order INTEGER DEFAULT 0")
    if "fragment_confirmed" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN fragment_confirmed INTEGER DEFAULT 0")
    if "confirmed_by_user_id" not in columns:
        statements.append("ALTER TABLE recordings ADD COLUMN confirmed_by_user_id VARCHAR(36)")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
            # 旧版本只有点过“确认并保存”才会长期保留碎片；迁移时按已确认处理。
            connection.execute(text("UPDATE recordings SET fragment_confirmed = 1 WHERE transcript != ''"))


def _ensure_narrator_columns() -> None:
    """补齐不可变的讲述者归属，并从历史审计记录恢复旧录音的创建人。"""

    recording_columns = {column["name"] for column in inspect(engine).get_columns("recordings")}
    story_columns = {column["name"] for column in inspect(engine).get_columns("stories")}
    with engine.begin() as connection:
        if "narrator_user_id" not in recording_columns:
            connection.execute(text("ALTER TABLE recordings ADD COLUMN narrator_user_id VARCHAR(36)"))
        connection.execute(
            text(
                """
                UPDATE recordings
                SET narrator_user_id = (
                    SELECT audit_events.actor_user_id
                    FROM audit_events
                    WHERE audit_events.target_id = recordings.id
                      AND audit_events.action = 'recording_saved'
                      AND audit_events.actor_user_id IS NOT NULL
                    ORDER BY audit_events.created_at
                    LIMIT 1
                )
                WHERE narrator_user_id IS NULL
                """
            )
        )


def _ensure_agent_p0_columns() -> None:
    """原地兼容旧 SQLite；不要求清空已有录音或故事。"""
    recording_columns = {column["name"] for column in inspect(engine).get_columns("recordings")}
    story_columns = {column["name"] for column in inspect(engine).get_columns("stories")}
    recording_defs = {
        "asr_raw_text": "TEXT DEFAULT ''", "agent_clean_text": "TEXT DEFAULT ''",
        "confirmed_text": "TEXT DEFAULT ''", "clean_changes_json": "TEXT DEFAULT '[]'",
        "clean_uncertainties_json": "TEXT DEFAULT '[]'", "clean_status": "VARCHAR(24) DEFAULT 'idle'",
        "clean_provider": "VARCHAR(64) DEFAULT ''",
    }
    story_defs = {
        "draft_sentences_json": "TEXT DEFAULT '[]'", "audit_suggestions_json": "TEXT DEFAULT '[]'",
        "revision_count": "INTEGER DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in recording_defs.items():
            if name not in recording_columns:
                connection.execute(text(f"ALTER TABLE recordings ADD COLUMN {name} {definition}"))
        connection.execute(text("UPDATE recordings SET confirmed_text = transcript WHERE fragment_confirmed = 1 AND confirmed_text = ''"))
        connection.execute(text("UPDATE recordings SET asr_raw_text = transcript WHERE asr_raw_text = '' AND transcript != ''"))
        for name, definition in story_defs.items():
            if name not in story_columns:
                connection.execute(text(f"ALTER TABLE stories ADD COLUMN {name} {definition}"))
        # 仅清理由历史删除或异常中断留下、已无源录音的派生事实；不触碰任何原始录音或确认文字。
        connection.execute(text("DELETE FROM memory_facts WHERE recording_id NOT IN (SELECT id FROM recordings)"))
        if "narrator_user_id" not in story_columns:
            connection.execute(text("ALTER TABLE stories ADD COLUMN narrator_user_id VARCHAR(36)"))
        connection.execute(
            text(
                """
                UPDATE stories
                SET narrator_user_id = (
                    SELECT recordings.narrator_user_id
                    FROM recordings
                    WHERE recordings.id = stories.recording_id
                )
                WHERE narrator_user_id IS NULL AND recording_id IS NOT NULL
                """
            )
        )


def _repair_story_attributions() -> None:
    """只修复系统生成的标准署名句，不改动用户自己写的正文。"""

    patterns = (
        (re.compile(r"(?m)^(这是根据)[^。\n]{1,60}(亲口讲述整理、等待确认的一段家庭记忆。)$"), r"\1{name}\2"),
        (re.compile(r"(?m)^(这是)[^。\n]{1,60}(亲口讲述并等待确认的一段家庭记忆。)$"), r"\1{name}\2"),
        (re.compile(r"(?m)^(这是)[^。\n]{1,60}(确认过的口述原文。)$"), r"\1{name}\2"),
    )
    with SessionLocal() as session:
        changed = False
        stories = session.scalars(select(Story).where(Story.narrator_user_id.is_not(None))).all()
        for story in stories:
            narrator = session.get(User, story.narrator_user_id)
            if not narrator:
                continue
            body = story.body or ""
            repaired = body
            for pattern, replacement in patterns:
                repaired = pattern.sub(replacement.format(name=narrator.display_name), repaired)
            if repaired != body:
                story.body = repaired
                changed = True
        if changed:
            session.commit()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def seed() -> None:
    """写入主题、四个本机演示账号，并把它们关联到同一个家庭。"""
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
            user_id = str(uuid.uuid4())
            family_id = str(uuid.uuid4())
            demo = User(
                id=user_id,
                phone=DEMO_PHONE,
                display_name="林奶奶",
                role="elder",
                gender="female",
                age=72,
                password_hash=hash_password(DEMO_PASSWORD),
            )
            session.add(demo)
            session.add(Family(id=family_id, name="林家"))
            session.add(Membership(id=str(uuid.uuid4()), family_id=family_id, user_id=user_id, role="elder", is_admin=0))
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

        # 兼容旧数据库：保留既有故事，把旧演示账号升级为林奶奶。
        demo.display_name = "林奶奶"
        demo.role = "elder"
        demo.gender = "female"
        demo.age = 72
        demo.active = 1
        root_membership = session.query(Membership).filter_by(user_id=demo.id).one()
        family_id = root_membership.family_id
        family = session.get(Family, family_id)
        if family:
            family.name = "记忆银行演示家庭"

        for phone, name, role, gender, age, is_admin in DEMO_ACCOUNTS:
            user = session.query(User).filter_by(phone=phone).one_or_none()
            if user is None:
                user = User(
                    id=str(uuid.uuid4()), phone=phone, display_name=name, role=role,
                    gender=gender, age=age, active=1, password_hash=hash_password(DEMO_PASSWORD),
                )
                session.add(user)
                session.flush()
            else:
                user.display_name = name
                user.role = role
                user.gender = gender
                user.age = age
                user.active = 1
            membership = session.query(Membership).filter_by(user_id=user.id).one_or_none()
            if membership is None:
                membership = Membership(
                    id=str(uuid.uuid4()), family_id=family_id, user_id=user.id,
                    role=role, is_admin=1 if is_admin else 0,
                )
                session.add(membership)
            else:
                membership.family_id = family_id
                membership.role = role
                membership.is_admin = 1 if is_admin else 0
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
    "FamilyInvitation",
    "Membership",
    "Recording",
    "SessionLocal",
    "Story",
    "StoryRecording",
    "Topic",
    "User",
    "DEMO_PASSWORD",
    "DEMO_PHONE",
    "DEMO_ACCOUNTS",
    "get_session",
    "init_database",
    "seed",
    "utc_now",
]
