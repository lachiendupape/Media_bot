import time
import uuid

import memory


def _use_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory_test.db"
    monkeypatch.setattr(memory, "_DB_PATH", db_path)
    memory._ensure_db()


def _new_identity(prefix: str = "user") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def test_load_prior_turns_returns_latest_in_chronological_order(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    identity = _new_identity()

    for index in range(1, 6):
        role = "user" if index % 2 else "assistant"
        memory.save_turn(identity, role, f"turn-{index}")

    turns = memory.load_prior_turns(identity, max_turns=3)

    assert [turn["content"] for turn in turns] == ["turn-3", "turn-4", "turn-5"]


def test_trim_to_n_keeps_only_newest_turns(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    identity = _new_identity()

    for index in range(1, 7):
        role = "user" if index % 2 else "assistant"
        memory.save_turn(identity, role, f"turn-{index}")

    memory.trim_to_n(identity, max_turns=4)
    turns = memory.load_prior_turns(identity, max_turns=10)

    assert [turn["content"] for turn in turns] == ["turn-3", "turn-4", "turn-5", "turn-6"]


def test_trim_to_n_with_zero_deletes_identity_history(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    identity = _new_identity()

    memory.save_turn(identity, "user", "hello")
    memory.save_turn(identity, "assistant", "hi")

    memory.trim_to_n(identity, max_turns=0)

    assert memory.load_prior_turns(identity, max_turns=10) == []


def test_cleanup_expired_ttl_removes_old_turns(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    identity = _new_identity()

    now = int(time.time())
    old_ts = now - 7200

    with memory._lock:
        conn = memory._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO conversation_turns (identity, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (identity, "user", "old-turn", old_ts),
            )
            conn.execute(
                """
                INSERT INTO conversation_turns (identity, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (identity, "assistant", "new-turn", now),
            )
            conn.commit()
        finally:
            conn.close()

    memory.cleanup_expired_ttl(ttl_seconds=3600)
    turns = memory.load_prior_turns(identity, max_turns=10)

    assert [turn["content"] for turn in turns] == ["new-turn"]


def test_delete_identity_all_only_removes_target_identity(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)
    target_identity = _new_identity("target")
    other_identity = _new_identity("other")

    memory.save_turn(target_identity, "user", "target-message")
    memory.save_turn(other_identity, "user", "other-message")

    memory.delete_identity_all(target_identity)

    assert memory.load_prior_turns(target_identity, max_turns=10) == []
    assert [turn["content"] for turn in memory.load_prior_turns(other_identity, max_turns=10)] == ["other-message"]
