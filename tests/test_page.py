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


def _static(name: str) -> str:
    from pathlib import Path
    return (Path(server.ROOT) / "static" / name).read_text()


def test_ledger_text_wraps_between_words() -> None:
    """Source and Detail are never narrower than their longest words, and grid cells
    wrap at word boundaries (no break-anywhere)."""
    import re
    js, css = _static("app.js"), _static("style.css")
    longest = max(len("(record_payment_for_invoice)"), len("accounts@harbourcity.example.com"))
    for field in ("source", "detail"):
        col = re.search(r'\{ field: "%s", headerName: "\w+", flex: [\d.]+, minWidth: (\d+), wrapText: true, autoHeight: true \}' % field, js)
        assert col, field
        assert int(col.group(1)) >= longest * 7.6  # about 7.5 px a character at the grid's 13.5 px
    rule = re.search(r"\.aggrid \.ag-cell-wrap-text \{([^}]*)\}", css).group(1)
    assert "word-break: normal" in rule and "anywhere" not in rule


def test_opening_a_ledger_tab_scrolls_it_into_view_and_respects_reduced_motion() -> None:
    js = _static("app.js")
    assert 'prefers-reduced-motion: reduce' in js and 'reducedMotion() ? "auto" : "smooth"' in js
    assert 'showBooks("led"); revealBooks();' in js and 'showBooks("inv"); revealBooks();' in js


def test_a_mouse_click_draws_no_cell_focus_box_but_the_keyboard_keeps_it() -> None:
    js, css = _static("app.js"), _static("style.css")
    assert 'addEventListener("pointerdown", () => host.classList.add("pointer-focus"), true)' in js
    assert 'addEventListener("keydown", () => host.classList.remove("pointer-focus"), true)' in js
    assert ".aggrid.pointer-focus .ag-cell-focus:not(.ag-cell-range-selected):focus-within { border-color: transparent; }" in css


def test_every_progress_message_list_has_a_message_from_the_first_second() -> None:
    """The typed-note check passed a list whose first message started at 2 s, so the
    progress helper threw before the request was sent: the button went grey and
    nothing happened (10 Nov 2026). Runs the page's own helper in Node."""
    import json
    import re
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = _static("app.js")
    helper = re.search(r"function progress\(node, steps, t0 = Date\.now\(\)\) \{.*?\n\}\n", js, re.S).group(0)
    steps = re.search(r"const CHECK_STEPS = \[.*?\];", js, re.S).group(0)
    note = re.search(r"const NOTE_STEPS = .*?;", js).group(0)
    used = set(re.findall(r"progress\(status, ([A-Z_]+)\)", js))
    assert used == {"CHECK_STEPS", "NOTE_STEPS"}
    script = helper + steps + note + """
      const out = {};
      for (const [name, list] of [["CHECK_STEPS", CHECK_STEPS], ["NOTE_STEPS", NOTE_STEPS], ["late", CHECK_STEPS.slice(1)]]) {
        const node = { textContent: "" };
        const stop = progress(node, list);
        out[name] = node.textContent; stop();
      }
      console.log(JSON.stringify(out));
    """
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    assert shown["CHECK_STEPS"] == "Reading the file..."
    assert shown["NOTE_STEPS"].startswith("Checking it against the contract")
    # A list that starts late shows its first message, it does not throw.
    assert shown["late"].startswith("Checking it against the contract")


def test_the_start_screen_says_what_to_press_and_names_paypal() -> None:
    page = TestClient(server.app).get("/").text
    assert "Two-minute tour, all in the PayPal sandbox" in page
    for step in ("use the sample contract", "sample evidence file", "send the PayPal invoice", "record the payment", "ask the billing agent"):
        assert step in page, step


def test_the_due_cell_is_redrawn_when_only_the_status_changes() -> None:
    """A paid invoice kept "in 30 d" beside its due date: the text comes from the row's
    days, and the grid only redraws a cell whose own value changed."""
    js = _static("app.js")
    after = js.split('GRIDS.inv.setGridOption("rowData", inv);', 1)[1][:400]
    assert 'GRIDS.inv.refreshCells({ columns: ["due_date"], force: true });' in after
