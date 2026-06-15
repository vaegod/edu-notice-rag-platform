from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.university_directory import UniversityDirectory
from app.services.c9_scope import normalize_c9_university_name


class UniversityDirectoryService:
    def get_entry(
        self,
        session: Session,
        university_name: str,
        *,
        scope: str = "graduate_admissions",
    ) -> UniversityDirectory | None:
        normalized_name = normalize_c9_university_name(university_name)
        if not normalized_name:
            return None
        return session.scalar(
            select(UniversityDirectory).where(
                UniversityDirectory.normalized_name == normalized_name,
                UniversityDirectory.scope == scope,
                UniversityDirectory.status == "active",
            )
        )

    def selected_entrypoint_url(self, entry: UniversityDirectory | None) -> str | None:
        if entry is None:
            return None
        return entry.selected_entrypoint_url or entry.admissions_entry_url
