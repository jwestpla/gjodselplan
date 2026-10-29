from urllib.parse import unquote

from gjodsel.feedback import append_feedback, count_feedback, mailto_url


def test_append_and_count(tmp_path):
    path = tmp_path / "feedback.csv"
    assert count_feedback(path) == 0
    append_feedback(path, {"navn": "Kari", "fane": "Jordprøver", "kommentar": "P-AL; enhet uklar\nandre linje"})
    append_feedback(path, {"navn": "Kari", "fane": "Generelt", "kommentar": "Fin"})
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1  # one BOM, one header
    assert count_feedback(path) == 2
    assert "tidspunkt;versjon;navn;fane;felt;foretak;år;kommentar" in raw.decode("utf-8-sig")


def test_mailto(tmp_path):
    url = mailto_url("test@example.no", "0.1.0", tmp_path / "feedback.csv", 3)
    assert url.startswith("mailto:test@example.no?subject=")
    body = unquote(url.split("&body=")[1])
    assert "versjon 0.1.0" in body and str(tmp_path / "feedback.csv") in body and "3 tilbakemelding" in body
    assert " " not in url  # fully encoded
