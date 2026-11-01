import json

from nm.netease import (
    PRESET_KEY,
    aes_decrypt,
    aes_encrypt,
    build_payload,
    parse_track,
    rsa_encrypt,
)


def test_aes_roundtrip():
    text = json.dumps({"id": 123, "中文": "歌词"}, ensure_ascii=False)
    enc = aes_encrypt(text, PRESET_KEY)
    assert aes_decrypt(enc, PRESET_KEY) == text


def test_rsa_output_shape():
    out = rsa_encrypt("AbCdEfGhIjKlMnOp")
    assert len(out) == 256
    int(out, 16)  # 必须是合法十六进制


def test_build_payload_decrypts_back_to_json():
    data = {"id": 186016, "csrf_token": ""}
    params, enc_sec_key, secret = build_payload(data, secret_key="0123456789abcdef")
    inner = aes_decrypt(params, secret.encode("ascii"))
    raw = aes_decrypt(inner, PRESET_KEY)
    assert json.loads(raw) == data
    assert len(enc_sec_key) == 256


def test_parse_track_new_shape():
    d = parse_track({"id": 1, "name": "晴天", "ar": [{"name": "周杰伦"}],
                     "al": {"name": "叶惠美"}, "dt": 269000, "st": 0})
    assert (d.song_id, d.title, d.artist, d.album, d.duration_ms, d.status) == (
        1, "晴天", "周杰伦", "叶惠美", 269000, 0)


def test_parse_track_invalid_status():
    d = parse_track({"id": 2, "name": "下架歌", "ar": [], "al": {}, "dt": 1000, "st": -200})
    assert d.status == -200
