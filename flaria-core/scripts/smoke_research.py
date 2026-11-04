#!/usr/bin/env python3
"""End-to-end test of research mode against a running server started with FLARIA_RESEARCH_MODE=true.

    python scripts/smoke_research.py --base http://127.0.0.1:8000 --admin-email a@x.org --admin-password '...'
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import re
import sys
import uuid
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_e2e import stream, ws_connect  # noqa: E402

PW = "Research-Test-Pass-2026!"


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


async def ws_close_code(ws_base: str, token: str) -> int | None:
    try:
        async with ws_connect(f"{ws_base}/ws/stream", subprotocols=["flaria.v1", f"bearer.{token}"]) as ws:
            await ws.recv()
    except Exception as exc:  # noqa: BLE001
        return getattr(getattr(exc, "rcvd", None), "code", None)
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    args = ap.parse_args()
    ws_base = args.base.replace("http", "ws", 1)
    tag = uuid.uuid4().hex[:6]

    async with httpx.AsyncClient(base_url=args.base, timeout=30) as c:
        async def register(name: str) -> tuple[str, str, str]:
            email = f"{name.lower().replace(' ', '.')}-{tag}@example.org"
            r = await c.post("/api/v1/auth/register", json={"email": email, "password": PW, "full_name": name})
            assert r.status_code == 201, r.text
            return email, r.json()["access_token"], r.json()["user"]["id"]

        def h(tok: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {tok}"}

        study = (await c.get("/api/v1/research/study")).json()
        check(study["research_mode"] and study["version"].startswith("v1-") and len(study["sections"]) == 7,
              "study information sheet published")
        check(study["approved"] is False, "no ethics reference configured: shown as not approved")
        cfg = (await c.get("/api/v1/config")).json()
        check(cfg.get("research_mode") is True, "public config reports research mode")

        p_email, p_tok, p_id = await register("Ada Participant")
        q_email, q_tok, q_id = await register("Ben Participant")
        check(await ws_close_code(ws_base, p_tok) == 4412, "live session refused before consent (4412)")

        r = await c.post("/api/v1/research/consent", headers=h(p_tok), json={"consent_version": "v1-wrong", "agreements_accepted": True})
        check(r.status_code == 409, "outdated consent version refused")
        r = await c.post("/api/v1/research/consent", headers=h(p_tok), json={"consent_version": study["version"], "agreements_accepted": False})
        check(r.status_code == 400, "consent without accepting both statements refused")
        r = await c.post("/api/v1/research/consent", headers=h(p_tok), json={"consent_version": study["version"], "agreements_accepted": True})
        check(r.status_code == 204, "participant consents")
        me = (await c.get("/api/v1/research/me", headers=h(p_tok))).json()
        check(me["consented"] and re.fullmatch(r"FLR-\d{4,}", me["study_code"] or ""), f"participant has study code {me['study_code']}")

        session_id, ticks, hz = await stream(ws_base, p_tok, 22)
        check(len(ticks) > 22 * hz * 0.6, f"consented participant streamed a session ({len(ticks)} updates)")

        r = await c.post("/api/v1/research/consent", headers=h(q_tok), json={"consent_version": study["version"], "agreements_accepted": True})
        check(r.status_code == 204, "second participant consents")
        await stream(ws_base, q_tok, 8)

        admin = (await c.post("/api/v1/auth/login", json={"email": args.admin_email, "password": args.admin_password})).json()["access_token"]
        c_email, c_tok, c_id = await register("Dr Chi Clinician")
        r = await c.put(f"/api/v1/admin/users/{c_id}/role", headers=h(admin), json={"role": "CLINICIAN"})
        check(r.status_code == 204, "admin promotes the clinician")
        c_tok = (await c.post("/api/v1/auth/login", json={"email": c_email, "password": PW})).json()["access_token"]
        r = await c.post("/api/v1/admin/care-team", headers=h(admin), json={"clinician_id": c_id, "patient_id": p_id})
        check(r.status_code == 204, "admin assigns participant A to the clinician")

        secret_note = f"Seen at Example Hospital by Dr Chi {tag}"
        r = await c.put(f"/api/v1/research/participants/{p_id}/label", headers=h(c_tok),
                        json={"label": "stroke", "severity": "mild", "detail": secret_note})
        check(r.status_code == 204, "clinician records a confirmed label for their participant")
        r = await c.put(f"/api/v1/research/participants/{q_id}/label", headers=h(c_tok), json={"label": "als"})
        check(r.status_code == 403, "clinician can't label someone outside their care team")
        r = await c.put(f"/api/v1/research/participants/{p_id}/label", headers=h(p_tok), json={"label": "healthy_control"})
        check(r.status_code == 403, "participants can't label themselves")
        r = await c.put(f"/api/v1/research/participants/{q_id}/label", headers=h(admin), json={"label": "healthy_control"})
        check(r.status_code == 204, "admin can label any participant")
        lab = (await c.get(f"/api/v1/research/participants/{p_id}/label", headers=h(c_tok))).json()
        check(lab["label"]["label"] == "stroke" and lab["consented"], "label and consent visible to the clinician")

        r = await c.post("/api/v1/research/withdraw", headers=h(q_tok))
        check(r.status_code == 204, "second participant withdraws")
        check(await ws_close_code(ws_base, q_tok) == 4412, "withdrawn participant can't record")

        plist = (await c.get("/api/v1/research/participants", headers=h(admin))).json()["participants"]
        states = {p["email"]: p["state"] for p in plist}
        check(states.get(p_email) == "consented" and states.get(q_email) == "withdrawn", "admin sees consent states")
        check((await c.get("/api/v1/research/participants", headers=h(c_tok))).status_code == 403, "clinicians can't list all participants")
        check((await c.get("/api/v1/research/export", headers=h(c_tok))).status_code == 403, "clinicians can't export")

        r = await c.get("/api/v1/research/export", headers=h(admin))
        check(r.status_code == 200 and r.headers["content-type"] == "application/zip", "admin downloads the export")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        everything = "".join(z.read(n).decode() for n in z.namelist())
        parts = list(csv.DictReader(io.StringIO(z.read("participants.csv").decode())))
        codes = [p["study_code"] for p in parts]
        check(me["study_code"] in codes and len(codes) == 1, "export holds only the consenting participant")
        check(parts[0]["label"] == "stroke" and parts[0]["severity"] == "mild", "label and severity exported")
        samples = list(csv.DictReader(io.StringIO(z.read("samples.csv").decode())))
        check(len(samples) >= 15 and all(s["study_code"] == me["study_code"] for s in samples), f"{len(samples)} one-second samples exported")
        for leak in (p_email, q_email, c_email, "Ada", "Ben", "Chi", secret_note, p_id, q_id, session_id):
            if leak in everything:
                check(False, f"export leaked {leak!r}")
        check(not re.search(r"20\d\d-\d\d-\d\dT", everything), "no timestamps in the export")
        check(True, "no names, e-mails, ids, notes or dates in the export")

        r = await c.delete("/api/v1/research/me/data", headers=h(p_tok))
        check(r.status_code == 204, "participant deletes their data")
        sessions = (await c.get("/api/v1/sessions", headers=h(p_tok))).json()
        check(sessions == [], "their sessions are gone")
        check(await ws_close_code(ws_base, p_tok) == 4412, "deleting data also ends participation")
        audit = (await c.get("/api/v1/admin/audit", headers=h(admin), params={"category": "research", "limit": 50})).json()["items"]
        actions = {e["action"] for e in audit}
        check({"research.consent_given", "research.label_set", "research.consent_withdrawn", "research.export",
               "research.data_deleted"} <= actions, "every research action is in the audit log")
    print("ALL RESEARCH CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
