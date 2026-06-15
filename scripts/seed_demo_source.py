from pathlib import Path
import sys

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import get_session_factory, init_db
from app.models.source import Source


def main() -> None:
    init_db()
    session = get_session_factory()()
    try:
        existing = session.scalar(
            select(Source).where(Source.name == "Shanghai Jiao Tong University CS")
        )
        if existing is not None:
            print("Demo source already exists.")
            return

        source = Source(
            name="Shanghai Jiao Tong University CS",
            base_url="https://example.edu.cn",
            site_type="college",
            crawl_mode="static",
            status="active",
            config_json={
                "institution": "上海交通大学",
                "department": "计算机学院",
                "aliases": ["上海交大", "上海交通大学"],
                "list_pages": ["https://example.edu.cn/list.html"],
                "list": {
                    "item_selector": ".notice-item",
                    "title_selector": "a",
                    "link_selector": "a",
                    "date_selector": ".date",
                },
                "detail": {
                    "title_selector": "h1.notice-title",
                    "publish_date_selector": ".publish-date",
                    "content_selector": ".article",
                    "attachment_selector": ".article a",
                },
                "schedule_hours": 6,
                "hot_source": True,
            },
        )
        session.add(source)
        session.commit()
        print("Demo source created.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
