"""自定义主题必须兼容已有四主题的 SQLite 数据库。"""

from sqlalchemy import create_engine, inspect, text

from backend import database


def test_existing_topic_table_is_extended_without_losing_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'old-topics.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE topics (id VARCHAR(32) PRIMARY KEY, glyph VARCHAR(4), title VARCHAR(64), subtitle VARCHAR(128), sort_order INTEGER)"))
        connection.execute(text("INSERT INTO topics VALUES ('school', '校', '上学的日子', '老师与同学', 2)"))
    monkeypatch.setattr(database, "engine", engine)
    database._ensure_topic_family_column()
    database._ensure_topic_family_column()  # 重启时重复迁移也安全
    assert "family_id" in {column["name"] for column in inspect(engine).get_columns("topics")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id, family_id FROM topics")).one() == ("school", None)
