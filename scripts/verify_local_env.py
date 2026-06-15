from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _copy_fixture_site(target_dir: Path) -> None:
    fixture_dir = PROJECT_ROOT / "tests" / "fixtures"
    (target_dir / "notice").mkdir(parents=True, exist_ok=True)
    (target_dir / "files").mkdir(parents=True, exist_ok=True)

    shutil.copyfile(fixture_dir / "list_page.html", target_dir / "list.html")
    shutil.copyfile(fixture_dir / "detail_lecture.html", target_dir / "notice" / "lecture.html")
    shutil.copyfile(fixture_dir / "detail_apply.html", target_dir / "notice" / "apply.html")

    (target_dir / "files" / "lecture.pdf").write_text("demo lecture file", encoding="utf-8")
    (target_dir / "files" / "apply.docx").write_text("demo apply file", encoding="utf-8")


def _start_fixture_server(site_dir: Path, port: int) -> ThreadingHTTPServer:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site_dir), **kwargs)

        def guess_type(self, path: str) -> str:
            content_type = super().guess_type(path)
            if path.lower().endswith((".html", ".htm")) and "charset=" not in content_type:
                return f"{content_type}; charset=utf-8"
            return content_type

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _wait_for_http(url: str, timeout: int = 20) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # pragma: no cover - retry loop
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Service did not become ready: {url}. Last error: {last_error}")


def _request_json(method: str, url: str, payload: dict | None = None) -> dict | list:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:  # pragma: no cover - helpful failure surface
        raise RuntimeError(f"{method} {url} failed: {exc.status} {exc.read().decode('utf-8')}") from exc


def _request_text(url: str) -> str:
    with request.urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8")


def _wait_for_task(base_url: str, task_id: int, timeout: int = 30) -> dict:
    deadline = time.time() + timeout
    last_payload: dict | None = None
    while time.time() < deadline:
        payload = _request_json("GET", f"{base_url}/api/v1/tasks/{task_id}")
        if isinstance(payload, dict):
            last_payload = payload
            if payload.get("status") in {"success", "partial_success", "failed"}:
                return payload
        time.sleep(0.5)
    raise RuntimeError(f"Task {task_id} did not finish within {timeout}s. Last payload: {last_payload}")


def _module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify local app environment and optional real-site regressions.")
    parser.add_argument("--real-sites", action="store_true", help="Also run real-site regression checks after local fixture verification.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise SystemExit("Virtual environment is missing. Please create .venv first.")

    api_port = _free_port()
    fixture_port = _free_port()

    with tempfile.TemporaryDirectory(prefix="edu_notice_demo_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        _copy_fixture_site(temp_dir)
        fixture_server = _start_fixture_server(temp_dir, fixture_port)
        verify_db = temp_dir / "verify_app.db"
        api_env = os.environ.copy()
        api_env["DATABASE_URL"] = f"sqlite:///{verify_db.as_posix()}"
        api_env["SCHEDULER_ENABLED"] = "false"
        api_env["MOCK_LLM_ENABLED"] = "true"
        api_env["SILICONFLOW_API_KEY"] = ""

        api_process = subprocess.Popen(
            [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)],
            cwd=PROJECT_ROOT,
            env=api_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            _wait_for_http(f"http://127.0.0.1:{api_port}/health")

            source_payload = {
                "name": f"Local Fixture Demo {int(time.time())}",
                "organization_name": "上海交通大学",
                "source_type": "admissions_notice",
                "collection_domain": "admissions_notice",
                "base_url": f"http://127.0.0.1:{fixture_port}",
                "site_type": "college",
                "crawl_mode": "static",
                "status": "active",
                "scope_json": {
                    "university_name": "上海交通大学",
                    "admissions_levels": ["graduate"],
                    "admissions_tracks": ["graduate"],
                    "selected_track": "graduate",
                },
                "config_json": {
                    "collection_domain": "admissions_notice",
                    "institution": "上海交通大学",
                    "department": "计算机学院",
                    "aliases": ["上海交大", "上海交通大学", "上海交通大学计算机学院"],
                    "admissions_levels": ["graduate"],
                    "admissions_tracks": ["graduate"],
                    "selected_track": "graduate",
                    "list_pages": [f"http://127.0.0.1:{fixture_port}/list.html"],
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
                },
            }

            health = _request_json("GET", f"http://127.0.0.1:{api_port}/health")
            landing_page = _request_text(f"http://127.0.0.1:{api_port}/")
            created_source = _request_json(
                "POST",
                f"http://127.0.0.1:{api_port}/api/v1/sources",
                source_payload,
            )
            sources = _request_json("GET", f"http://127.0.0.1:{api_port}/api/v1/sources")
            validation = _request_json(
                "POST",
                f"http://127.0.0.1:{api_port}/api/v1/sources/{created_source['id']}/validate",
            )
            if validation["success_rate"] < 0.8:
                raise RuntimeError(f"Local fixture validation did not pass: {json.dumps(validation, ensure_ascii=False)}")

            task = _request_json(
                "POST",
                f"http://127.0.0.1:{api_port}/api/v1/tasks",
                {
                    "source_id": created_source["id"],
                    "task_payload": {},
                    "execute_immediately": True,
                },
            )
            task = _wait_for_task(f"http://127.0.0.1:{api_port}", task["id"])
            documents = _request_json("GET", f"http://127.0.0.1:{api_port}/api/v1/documents")
            overview = _request_json("GET", f"http://127.0.0.1:{api_port}/api/v1/dashboard/overview")
            nl_parse = _request_json(
                "POST",
                f"http://127.0.0.1:{api_port}/api/v1/nl/parse",
                {"query": "采集清华大学研究生招生公告"},
            )
            ask = _request_json(
                "POST",
                f"http://127.0.0.1:{api_port}/api/v1/ask",
                {"query": "最近有哪些上海交通大学研究生招生信息？"},
            )

            summary = {
                "health": health["status"],
                "landing_page_ok": "C9 研招公告采集工作台" in landing_page,
                "scrapy_playwright_available": _module_available("scrapy_playwright"),
                "crawl4ai_available": _module_available("crawl4ai"),
                "source_count": len(sources),
                "created_source_id": created_source["id"],
                "source_validation_success_rate": validation["success_rate"],
                "task_status": task["status"],
                "task_inserted": task["last_result"]["inserted"] if task.get("last_result") else None,
                "document_total": documents["total"],
                "overview_documents": overview["counts"]["total_documents"],
                "nl_intent": nl_parse["intent"],
                "nl_validation_status": nl_parse["validation_status"],
                "ask_citation_count": len(ask.get("citations") or []),
            }
            if summary["task_status"] not in {"success", "partial_success"}:
                raise RuntimeError(f"Local fixture task failed: {json.dumps(task, ensure_ascii=False)}")
            if not summary["landing_page_ok"] or summary["task_inserted"] < 1 or summary["document_total"] < 1 or summary["ask_citation_count"] < 1:
                raise RuntimeError(f"Local verification checks failed: {json.dumps(summary, ensure_ascii=False)}")
            if args.real_sites:
                regression_process = subprocess.run(
                    [str(python), "scripts/real_site_regression.py", "--min-success-rate", "0.8"],
                    cwd=PROJECT_ROOT,
                    env=api_env,
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
                regression_stdout = regression_process.stdout.strip() or "{}"
                try:
                    regression_payload = json.loads(regression_stdout)
                except json.JSONDecodeError:
                    regression_payload = {
                        "passed": False,
                        "raw_stdout": regression_stdout,
                        "raw_stderr": regression_process.stderr.strip(),
                    }
                summary["real_site_regression"] = {
                    "passed": regression_process.returncode == 0 and regression_payload.get("passed") is True,
                    "case_count": len(regression_payload.get("cases", [])),
                }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        finally:
            api_process.terminate()
            try:
                api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                api_process.kill()
            fixture_server.shutdown()


if __name__ == "__main__":
    main()
