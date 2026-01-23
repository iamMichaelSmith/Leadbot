import run


def test_strip_tracking_params():
    q = "utm_source=x&foo=1&utm_medium=y&bar=2"
    assert run.strip_tracking_params(q) == "foo=1&bar=2"


def test_normalize_url_strips_fragment_and_tracking():
    u = "https://example.com/path?utm_source=a&x=1#frag"
    assert run.normalize_url(u) == "https://example.com/path?x=1"


def test_is_candidate_email_blocks_placeholders():
    assert not run.is_candidate_email("user@example.com")
    assert not run.is_candidate_email("email@domain.com")


def test_library_confidence_basics():
    title = "Production Music Library"
    headings = "Library Music Catalog"
    body = "Royalty-free music library with a large catalog"
    score = run.library_confidence(title, headings, body, "https://example.com/library")
    assert score >= 60