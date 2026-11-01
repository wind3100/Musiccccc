from nm.db import DB
from nm.sync import (
    PlaylistEntry,
    apply_sync,
    compute_diff,
    entries_from_tracks,
    load_entries_from_json,
    save_snapshot,
)


def make_db(tmp_path):
    return DB(tmp_path / "t.sqlite3")


def e(song_id, title="歌", status=0, artist="歌手", album="专辑", duration=200000):
    return PlaylistEntry(song_id=song_id, title=title, artist=artist, album=album,
                         duration_ms=duration, status=status)


def test_diff_first_sync_all_added(tmp_path):
    db = make_db(tmp_path)
    diff = apply_sync(db, [e(1), e(2)])
    assert diff.added == [1, 2]
    assert diff.removed == [] and diff.invalid == []
    rows = db.list_bucket("like")
    assert [r["song_id"] for r in rows] == [1, 2]
    assert all(r["marker"] == "+" for r in rows)
    db.close()


def test_cloud_order_is_the_only_order(tmp_path):
    db = make_db(tmp_path)
    apply_sync(db, [e(1), e(2), e(3)])
    apply_sync(db, [e(3), e(1), e(2)])  # 云端重排
    rows = db.list_bucket("like")
    assert [r["song_id"] for r in rows] == [3, 1, 2]
    db.close()


def test_removed_moves_to_unliked_and_file_kept(tmp_path):
    db = make_db(tmp_path)
    apply_sync(db, [e(1), e(2)])
    db.add_file(2, tmp_path / "lib" / "2.flac", "flac", 1234)
    diff = apply_sync(db, [e(1)])
    assert diff.removed == [2]
    unliked = db.list_bucket("unliked")
    assert [r["song_id"] for r in unliked] == [2]
    assert unliked[0]["marker"] == "-"
    assert db.files_for(2), "本地文件必须保留（本地主权化）"
    db.close()


def test_invalid_goes_bottom_of_like(tmp_path):
    db = make_db(tmp_path)
    diff = apply_sync(db, [e(1), e(2, status=-200), e(3)])
    assert diff.invalid == [2]
    like = db.list_bucket("like")
    invalid = db.list_bucket("invalid")
    assert [r["song_id"] for r in like] == [1, 3]
    assert [r["song_id"] for r in invalid] == [2]
    assert invalid[0]["marker"] == "!"
    # 失效置底：position 大于所有正常歌曲
    assert invalid[0]["position"] > max(r["position"] for r in like)
    db.close()


def test_invalid_not_at_bottom_when_configured(tmp_path):
    db = make_db(tmp_path)
    apply_sync(db, [e(1), e(2, status=-200), e(3)], invalid_at_bottom=False)
    like = db.list_bucket("like")
    invalid = db.list_bucket("invalid")
    assert [r["song_id"] for r in like] == [1, 3]
    assert [r["song_id"] for r in invalid] == [2]
    assert invalid[0]["position"] == 1  # 保持云端原位
    db.close()


def test_restored_after_remove(tmp_path):
    db = make_db(tmp_path)
    apply_sync(db, [e(1), e(2)])
    apply_sync(db, [e(1)])            # 2 被移除 → unliked
    diff = apply_sync(db, [e(1), e(2)])  # 误触后收藏回来
    assert diff.restored == [2]
    assert diff.added == []
    assert [r["song_id"] for r in db.list_bucket("like")] == [1, 2]
    assert db.list_bucket("unliked") == []
    db.close()


def test_copyright_recovered_song_no_marker(tmp_path):
    db = make_db(tmp_path)
    apply_sync(db, [e(1), e(2, status=-200)])
    diff = apply_sync(db, [e(1), e(2)])
    assert diff.invalid == []
    assert 2 in diff.kept
    row = [r for r in db.list_bucket("like") if r["song_id"] == 2][0]
    assert row["marker"] is None
    db.close()


def test_duplicate_ids_in_cloud_are_deduped():
    entries = entries_from_tracks([{"id": 1, "name": "a", "dt": 1000}, {"id": 1, "name": "a", "dt": 1000}])
    assert len(entries) == 1


def test_parse_old_and_new_track_shapes():
    new = entries_from_tracks([{"id": 7, "name": "晴天", "ar": [{"name": "周杰伦"}],
                                "al": {"name": "叶惠美"}, "dt": 269000, "st": 0}])[0]
    old = entries_from_tracks([{"id": 8, "name": "夜曲", "artists": [{"name": "周杰伦"}],
                                "album": {"name": "十一月的萧邦"}, "duration": 226000}])[0]
    assert (new.artist, new.album, new.duration_ms) == ("周杰伦", "叶惠美", 269000)
    assert (old.artist, old.album, old.duration_ms) == ("周杰伦", "十一月的萧邦", 226000)


def test_offline_json_roundtrip(tmp_path):
    db = make_db(tmp_path)
    entries = [e(1, "晴天"), e(2, "夜曲")]
    snap = save_snapshot(tmp_path / "sync", entries, "run.json")
    loaded = load_entries_from_json(snap)
    assert [x.song_id for x in loaded] == [1, 2]
    assert loaded[0].title == "晴天"
    db.close()


# --- 回归：as_meta() 用的是 duration_ms 键，解析时不能只认 dt（否则时长全丢，全部误判审批）---
def test_entry_from_meta_dict_keeps_duration_and_status():
    from nm.sync import entry_from_track

    entry = entry_from_track({"song_id": 7, "title": "only my railgun", "artist": "fripSide",
                              "album": "only my railgun", "duration_ms": 257093, "status": 0})
    assert entry.duration_ms == 257093
    assert entry.status == 0
    assert entry.title == "only my railgun"


def test_entries_from_details_preserve_all_fields():
    from nm.sync import entries_from_details

    class D:
        song_id = 42
        title = "白幻花"
        artist = "ヒバナ"
        album = "白幻花"
        duration_ms = 188307
        status = -200

    entries = entries_from_details([D()])
    assert len(entries) == 1
    got = entries[0]
    assert (got.song_id, got.title, got.artist, got.album, got.duration_ms, got.status) == (
        42, "白幻花", "ヒバナ", "白幻花", 188307, -200)
    assert got.invalid is True
