"""Check the sample evidence files against the sample contract with the live model.

    python scripts/live_evidence.py [--schedule saved.json] [FILE:MILESTONE ...]

Reads the sample contract (or a schedule saved by scripts/live_read.py --save), then
checks each evidence file against its milestone and prints the verdicts, one line per item.
Defaults: the three files in samples/evidence/ (M1 email, M1 certificate image, M2 handover).
"""
import argparse, asyncio, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULTS = ["m1-acceptance-email.pdf:M1", "m1-acceptance-certificate.png:M1", "m2-dataset-handover.docx:M2"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=DEFAULTS)
    ap.add_argument("--schedule")
    a = ap.parse_args()
    from app import evidence
    from app.contract_reader import read_contract
    from app.llm import model_id
    from app.schedule import Schedule
    if a.schedule:
        sched = Schedule.model_validate_json(Path(a.schedule).read_text())
    else:
        sched = await read_contract((ROOT / "samples/northwind-harbour-services-agreement.pdf").read_bytes())
    print(f"model={model_id()} milestones={[m.code for m in sched.milestones]}")
    for spec in a.files:
        name, _, code = spec.rpartition(":")
        path = Path(name) if Path(name).exists() else ROOT / "samples/evidence" / name
        m = next(x for x in sched.milestones if x.code == code)
        ev = evidence.load_evidence(path.read_bytes(), path.name)
        t0 = time.time()
        out = await evidence.check_evidence(sched, m, ev)
        print(f"\n{path.name} -> {code}: {out.recommendation.upper()} ({time.time() - t0:.1f} s, {out.evidence_kind!r})")
        for c in out.criteria:
            print(f"  {c.verdict:<9} [{c.quote_status}{'@p' + str(c.page) if c.page else ''}] {c.criterion[:70]}")
            if c.quote:
                print(f"            \"{c.quote[:110]}\"")
        print(f"  summary: {out.summary}")


asyncio.run(main())
