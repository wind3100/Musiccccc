from nm.naming import build_filename, normalize_artist, normalize_title, sanitize_filename


def test_normalize_title_fullwidth_and_case():
    assert normalize_title("Ｈｅｌｌｏ　Ｗｏｒｌｄ") == normalize_title("Hello World")
    assert normalize_title("  Song  Name  ") == "song name"
    assert normalize_title("A\u200bB") == "ab"


def test_normalize_title_keeps_distinct_titles_distinct():
    # 归一化只抹写法差异，不做模糊匹配
    assert normalize_title("Song (Live)") != normalize_title("Song")
    assert normalize_title("Song") != normalize_title("Song 2")
    assert normalize_title("夜曲") != normalize_title("夜曲 live")


def test_normalize_title_hyphen_variants():
    assert normalize_title("A—B") == "a-b"
    assert normalize_title("A–B") == "a-b"
    assert normalize_title("A－B") == "a-b"


def test_normalize_artist_multi_separators():
    assert normalize_artist("A／B") == "A/B"
    assert normalize_artist("A、B") == "A/B"
    assert normalize_artist("A / B") == "A/B"


def test_sanitize_filename_illegal_chars():
    out = sanitize_filename('A:B?C*D"E<F>G|H')
    assert not any(ch in out for ch in '<>:"/\\|?*')


def test_sanitize_filename_trailing_dot_space_and_reserved():
    assert sanitize_filename("name.  ") == "name"
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("") == "未命名"


def test_sanitize_filename_length_limit():
    out = sanitize_filename("字" * 300, max_length=50)
    assert len(out) <= 50


def test_build_filename_uses_playlist_metadata_only():
    meta = {"title": "晴天", "artist": "周杰伦", "album": "叶惠美"}
    assert build_filename(meta) == "晴天 - 周杰伦"


def test_build_filename_missing_artist():
    assert build_filename({"title": "纯音乐", "artist": ""}) == "纯音乐"


def test_build_filename_custom_template():
    meta = {"title": "晴天", "artist": "周杰伦", "album": "叶惠美", "song_id": 186016}
    assert build_filename(meta, template="{artist} - {album} - {title}") == "周杰伦 - 叶惠美 - 晴天"
    assert build_filename(meta, template="{song_id} {title}") == "186016 晴天"
