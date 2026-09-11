from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from memory_bank.db import (
    Approval,
    ApprovalDecision,
    AssetStatus,
    AudioAsset,
    AuditEvent,
    Base,
    Chapter,
    ChapterVersion,
    Claim,
    ClaimEvidenceLink,
    ConsentGrant,
    ConsentStatus,
    DeletionReceipt,
    Delivery,
    DeliveryStatus,
    EvidenceRelationship,
    Family,
    FamilyMembership,
    InterviewSession,
    InterviewStatus,
    JobStatus,
    MediaJob,
    MediaJobType,
    MembershipRole,
    MembershipStatus,
    MemoryProject,
    ProjectStatus,
    TranscriptSegment,
    User,
    VersionStatus,
    create_database_engine,
    create_session_factory,
    initialize_database,
    session_scope,
    sqlite_url,
)


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(sqlite_url(tmp_path / "product.sqlite3"))
    initialize_database(engine)
    factory = create_session_factory(engine)
    try:
        yield engine, factory
    finally:
        engine.dispose()


def seed_complete_project(factory):
    with session_scope(factory) as session:
        user = User(
            email="Owner@Example.com",
            email_normalized="owner@example.com",
            display_name="项目所有者",
        )
        family = Family(name="林家", created_by=user, settings={"timezone": "Asia/Shanghai"})
        membership = FamilyMembership(
            family=family,
            user=user,
            role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        project = MemoryProject(
            family=family,
            created_by=user,
            title="外婆的夏天",
            subject_name="外婆",
            topic="童年暑假",
            status=ProjectStatus.ACTIVE,
            stage="interview",
            current_consent_version=1,
            workflow_thread_id="thread-project-001",
        )
        consent = ConsentGrant(
            project=project,
            version=1,
            status=ConsentStatus.ACTIVE,
            granted_by=user,
            granted_by_name="外婆本人",
            purpose="家庭记忆整理",
            scopes=["audio", "transcript", "writing"],
        )
        interview = InterviewSession(
            project=project,
            started_by=user,
            thread_id="thread-interview-001",
            status=InterviewStatus.ACTIVE,
            current_round=1,
            max_rounds=6,
            context={"last_question": "夏天最难忘的声音是什么？"},
        )
        audio = AudioAsset(
            project=project,
            interview_session=interview,
            uploaded_by=user,
            storage_key="families/lin/project/audio-001.wav",
            original_filename="外婆采访.wav",
            content_type="audio/wav",
            byte_size=2048,
            duration_ms=12_000,
            checksum_sha256="a" * 64,
            status=AssetStatus.READY,
            consent_version=1,
            properties={"sample_rate": 16_000},
        )
        segment = TranscriptSegment(
            project=project,
            interview_session=interview,
            audio_asset=audio,
            segment_index=0,
            start_ms=1_200,
            end_ms=6_800,
            text="傍晚的蝉声最大，我们把西瓜放在井水里。",
            confidence=0.97,
            consent_version=1,
            raw_result={"provider_segment_id": "seg-1"},
        )
        claim = Claim(
            project=project,
            created_by=user,
            text="家里会用井水冰西瓜。",
            confidence=0.94,
            consent_version=1,
            attributes={"time_period": "童年"},
        )
        evidence = ClaimEvidenceLink(
            claim=claim,
            transcript_segment=segment,
            relationship_type=EvidenceRelationship.SUPPORTS,
            quote_text="把西瓜放在井水里",
            relevance=0.98,
        )
        chapter = Chapter(
            project=project,
            created_by=user,
            slug="summer",
            title="井水里的夏天",
            position=0,
            latest_version_number=1,
        )
        version = ChapterVersion(
            chapter=chapter,
            created_by=user,
            version=1,
            content="傍晚，井水替一家人守着一块清凉。",
            status=VersionStatus.PENDING_REVIEW,
            audit_summary={"unsupported_claims": 0},
        )
        approval = Approval(
            project=project,
            chapter_version=version,
            decided_by=user,
            decision=ApprovalDecision.APPROVED,
            consent_version=1,
            idempotency_key="approve-v1",
        )
        media_job = MediaJob(
            project=project,
            requested_by=user,
            input_audio_asset=audio,
            chapter_version=version,
            job_type=MediaJobType.EXPORT,
            status=JobStatus.QUEUED,
            consent_version=1,
            idempotency_key="export-v1",
            payload={"format": "pdf"},
        )
        delivery = Delivery(
            project=project,
            chapter_version=version,
            created_by=user,
            delivery_type="memory_card",
            version=1,
            status=DeliveryStatus.READY,
            consent_version=1,
            idempotency_key="delivery-v1",
            properties={"format": "html"},
        )
        audit_event = AuditEvent(
            family=family,
            project=project,
            actor=user,
            event_type="chapter.approved",
            entity_type="chapter_version",
            payload={"version": 1},
        )
        deletion_receipt = DeletionReceipt(
            family=family,
            project=project,
            consent_grant=consent,
            requested_by=user,
            scope="project-content",
            reason="测试删除流程",
        )
        session.add_all(
            [
                membership,
                evidence,
                approval,
                media_job,
                delivery,
                audit_event,
                deletion_receipt,
            ]
        )
        session.flush()
        ids = {
            "user": user.id,
            "family": family.id,
            "project": project.id,
            "consent": consent.id,
            "interview": interview.id,
            "audio": audio.id,
            "segment": segment.id,
            "claim": claim.id,
            "chapter": chapter.id,
            "version": version.id,
        }
    return ids


def test_full_domain_graph_persists_with_portable_uuid_keys(database):
    _, factory = database
    ids = seed_complete_project(factory)

    assert all(str(uuid.UUID(value)) == value for value in ids.values())
    with factory() as session:
        project = session.get(MemoryProject, ids["project"])
        assert project is not None
        assert project.workflow_thread_id == "thread-project-001"
        assert project.current_consent_version == 1
        assert project.consent_grants[0].policy_version == "v1"
        assert project.family.memberships[0].role is MembershipRole.OWNER
        assert project.interview_sessions[0].audio_assets[0].transcript_segments[0].start_ms == 1_200
        assert project.claims[0].evidence_links[0].transcript_segment.text.startswith("傍晚")
        assert project.chapters[0].versions[0].approvals[0].decision is ApprovalDecision.APPROVED
        assert project.media_jobs[0].payload == {"format": "pdf"}
        assert project.media_jobs[0].max_attempts == 3
        assert project.media_jobs[0].progress_percent == 0
        assert project.media_jobs[0].revision == 0
        assert project.deliveries[0].properties == {"format": "html"}


def test_tenant_and_idempotency_constraints_are_enforced(database):
    _, factory = database
    ids = seed_complete_project(factory)

    with pytest.raises(IntegrityError):
        with session_scope(factory) as session:
            session.add(
                FamilyMembership(
                    family_id=ids["family"],
                    user_id=ids["user"],
                    role=MembershipRole.APPROVER,
                    status=MembershipStatus.ACTIVE,
                )
            )

    with pytest.raises(IntegrityError):
        with session_scope(factory) as session:
            session.add(
                MediaJob(
                    project_id=ids["project"],
                    job_type=MediaJobType.IMAGE,
                    consent_version=1,
                    idempotency_key="export-v1",
                )
            )

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(FamilyMembership)) == 1
        assert session.scalar(select(func.count()).select_from(MediaJob)) == 1


def test_family_delete_cascades_content_but_retains_receipts_and_audit(database):
    _, factory = database
    ids = seed_complete_project(factory)

    with session_scope(factory) as session:
        family = session.get(Family, ids["family"])
        assert family is not None
        session.delete(family)

    with factory() as session:
        assert session.get(MemoryProject, ids["project"]) is None
        assert session.scalar(select(func.count()).select_from(AudioAsset)) == 0
        assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0
        assert session.scalar(select(func.count()).select_from(Claim)) == 0
        assert session.scalar(select(func.count()).select_from(ChapterVersion)) == 0
        event = session.scalar(select(AuditEvent))
        receipt = session.scalar(select(DeletionReceipt))
        assert event is not None and event.family_id is None and event.project_id is None
        assert receipt is not None
        assert receipt.family_id is None and receipt.project_id is None and receipt.consent_grant_id is None


def test_alembic_upgrade_builds_the_same_table_set(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migrated.sqlite3"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))

    command.upgrade(config, "head")

    engine = create_database_engine(sqlite_url(database_path))
    try:
        table_names = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables).issubset(table_names)
        assert "alembic_version" in table_names
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == (
                "0001_product_schema"
            )
    finally:
        engine.dispose()
