"""The page itself: link checkers and preview bots (18 Oct 2026)."""

from fastapi.testclient import TestClient

from app import server


def test_the_page_answers_head_and_carries_its_preview() -> None:
    client = TestClient(server.app)
    assert client.head("/").status_code == 200
    page = client.get("/").text
    for tag in ('property="og:title"', 'property="og:description"', 'property="og:image"', 'name="twitter:card"'):
        assert tag in page
    image = client.get("/static/og.png")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.head("/static/og.png").status_code == 200


def test_ag_grid_is_pinned_to_one_version_and_checked_by_its_hash() -> None:
    import re
    page = TestClient(server.app).get("/").text
    tag = re.search(r'<script src="(https://cdn\.jsdelivr\.net/npm/ag-grid-community@[^"]+)"\s+integrity="([^"]+)"\s+crossorigin="anonymous"', page)
    assert tag, "AG Grid must load from a pinned URL with an integrity hash"
    assert re.search(r"@\d+\.\d+\.\d+/", tag.group(1)) and tag.group(2).startswith("sha384-")


def test_the_page_script_never_inserts_html() -> None:
    """Values from the server and the model are inserted as text. AG Grid renders a
    string returned by a cell renderer as HTML, so renderers return DOM nodes."""
    from pathlib import Path
    js = (Path(server.ROOT) / "static" / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink
    # the one HTML string handed to AG Grid is the empty-table message, built from constants
    assert js.count("overlayNoRowsTemplate") == 2 and "${NO_ROWS." in js


def test_the_sample_evidence_files_exist() -> None:
    from pathlib import Path
    folder = Path(server.SAMPLE_EVIDENCE)
    names = {p.name for p in folder.iterdir()}
    assert {"m1-acceptance-email.pdf", "m1-acceptance-certificate.png", "m2-dataset-handover.docx"} <= names
