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
