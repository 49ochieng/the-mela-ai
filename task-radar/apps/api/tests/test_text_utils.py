from app.utils.text import clean_message_body, content_hash, excerpt, html_to_text, strip_signature


def test_html_to_text_strips_tags():
    out = html_to_text("<p>Hello <b>world</b></p>")
    assert "Hello" in out and "world" in out and "<" not in out


def test_strip_signature_removes_after_marker():
    body = "Real content here.\n\n--\nJohn Doe\nSenior Manager"
    assert "John Doe" not in strip_signature(body)


def test_clean_message_body_combines():
    s = clean_message_body("<div>Please <i>review</i> the doc.</div>\n\n--\nSig")
    assert "Sig" not in s
    assert "review" in s


def test_content_hash_stable():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("a", "c")


def test_excerpt_truncates():
    out = excerpt("a" * 1000, 50)
    assert len(out) <= 53
