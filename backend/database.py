"""数据库与会话。

演示版是单用户单家庭的极简模型：注册/登录后自动创建家庭与授权记录，
业务查询一律带 family_id（产品安全基线：禁止仅凭资源 ID 查询）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Integer, String, Text, create_engine
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


engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def init_database() -> None:
    Base.metadata.create_all(engine)
    seed()


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
                    ),
                ]
            )
            session.commit()


__all__ = [
    "Base",
    "ConsentGrant",
    "Family",
    "Membership",
    "Recording",
    "SessionLocal",
    "Story",
    "Topic",
    "User",
    "DEMO_PASSWORD",
    "DEMO_PHONE",
    "get_session",
    "init_database",
    "seed",
    "utc_now",
]
