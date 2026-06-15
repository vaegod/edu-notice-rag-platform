from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source
from app.services.onboarding_service import SourceOnboardingService


REAL_SOURCE_DIR = Path(__file__).resolve().parents[2] / "config_templates" / "real_sources"


def sync_real_sources(session: Session) -> dict[str, int | str]:
    onboarding_service = SourceOnboardingService()
    created = 0
    updated = 0
    if not REAL_SOURCE_DIR.exists():
        return {"created": created, "updated": updated, "source_dir": str(REAL_SOURCE_DIR)}

    for path in sorted(REAL_SOURCE_DIR.glob("*.json")):
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
        raw_payload.setdefault("source_origin", "seed")
        raw_payload.setdefault("health_status", "healthy")
        raw_payload.setdefault("validation_evidence", {"seed_file": path.name})
        payload = onboarding_service.normalize_source_dict(
            session,
            raw_payload,
        )
        existing = session.scalar(select(Source).where(Source.name == payload["name"]))
        if existing is None:
            session.add(Source(**payload))
            created += 1
            continue

        for key, value in payload.items():
            setattr(existing, key, value)
        updated += 1

    session.commit()
    return {"created": created, "updated": updated, "source_dir": str(REAL_SOURCE_DIR)}
