"""Read the sample contract with the live model and print counts only.

    python scripts/live_read.py [--key-file PATH] [PDF]

--key-file reads ONLY the GEMINI_API_KEY or GOOGLE_API_KEY line from a dotenv
file into this process (nothing else from that file, nothing printed).
"""
import argparse, asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_one_key(path: str) -> None:
    for line in Path(path).read_text().splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and value.strip():
            os.environ["GEMINI_API_KEY"] = value.strip().strip('"').strip("'")
            return
    raise SystemExit("no Gemini key line found in that file")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default=str(ROOT / "samples/northwind-harbour-services-agreement.pdf"))
    ap.add_argument("--key-file")
    ap.add_argument("--save")
    a = ap.parse_args()
    if a.key_file:
        load_one_key(a.key_file)
    from app.contract_reader import read_contract
    from app.llm import model_id
    t0 = time.time()
    sched = await read_contract(Path(a.pdf).read_bytes())
    s = sched.stats()
    print(f"model={model_id()} seconds={time.time() - t0:.1f} pages={sched.pages} {json.dumps(s)}")
    for m in sched.milestones:
        print(f"  {m.code} {m.name[:40]!r} {m.currency} {m.amount} due={m.due_date} pay={m.payment_cite.status}@p{m.payment_cite.page} "
              f"acc={m.acceptance_cite.status if m.acceptance_cite else '-'}@p{m.acceptance_cite.page if m.acceptance_cite else '-'} criteria={len(m.acceptance_criteria)}")
    for name in ("payment_terms", "late_payment", "invoice_condition"):
        c = getattr(sched, name)
        print(f"  {name}: {c.cite.status + '@p' + str(c.cite.page) if c else None}")
    print("  net_days", sched.net_days, "issues", sched.issues)
    if a.save:
        Path(a.save).write_text(sched.model_dump_json(indent=1))


asyncio.run(main())
