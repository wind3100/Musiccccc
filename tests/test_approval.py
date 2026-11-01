from nm.approval import describe, run_menu
from nm.db import DB


def make_db(tmp_path):
    db = DB(tmp_path / "a.sqlite3")
    for sid in (1, 2, 3):
        db.upsert_song({"song_id": sid, "title": f"歌{sid}", "artist": "歌手", "duration_ms": 1000})
        db.add_approval(sid, "no_lossless", "无无损资源")
    return db


def test_menu_approve_then_quit(tmp_path):
    db = make_db(tmp_path)
    answers = iter(["a", "q"])
    counts = run_menu(db, input_fn=lambda _prompt="": next(answers), print_fn=lambda *a, **k: None)
    assert counts["approved"] == 1
    pending = db.pending_approvals()
    assert len(pending) == 2, "退出后剩余项必须保留在队列"
    db.close()


def test_menu_batch_approve_all(tmp_path):
    db = make_db(tmp_path)
    answers = iter(["A"])
    counts = run_menu(db, input_fn=lambda _prompt="": next(answers), print_fn=lambda *a, **k: None)
    assert counts["approved"] == 3
    assert db.pending_approvals() == []
    db.close()


def test_menu_reject_marks_song_rejected(tmp_path):
    db = make_db(tmp_path)
    answers = iter(["r", "r", "r"])
    counts = run_menu(db, input_fn=lambda _prompt="": next(answers), print_fn=lambda *a, **k: None)
    assert counts["rejected"] == 3
    assert db.is_rejected(1)
    db.close()


def test_menu_skip_keeps_pending(tmp_path):
    db = make_db(tmp_path)
    answers = iter(["s", "s", "s"])
    run_menu(db, input_fn=lambda _prompt="": next(answers), print_fn=lambda *a, **k: None)
    assert len(db.pending_approvals()) == 3
    db.close()


def test_menu_empty_queue(tmp_path):
    db = DB(tmp_path / "b.sqlite3")
    printed = []
    counts = run_menu(db, input_fn=lambda _p="": "a", print_fn=printed.append)
    assert counts == {"approved": 0, "rejected": 0, "skipped": 0}
    assert "为空" in printed[0]
    db.close()


def test_describe_contains_kind_label(tmp_path):
    db = make_db(tmp_path)
    item = db.pending_approvals()[0]
    text = describe(item)
    assert "无无损资源" in text and "歌1" in text
    db.close()
