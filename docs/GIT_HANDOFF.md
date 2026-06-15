# Git 展示交付清单

## 上传前检查

- 不提交 `.env`、`.venv/`、`*.db`、`__pycache__/`、`.pytest_cache/`、`tmp/`。
- `.env.example` 只保留空 API key 和可复现默认配置。
- 默认运行时使用 SQLite，不依赖 Redis、Celery、PostgreSQL 或向量数据库。
- 非 C9 高校不做开放式官网猜测；必须由用户提供官方首页或研究生招生入口 URL。

## 推荐本地命令

```powershell
.\.venv\Scripts\python.exe scripts\migrate_db.py
.\.venv\Scripts\python.exe -m pytest -q
```

## Boss 演示脚本

1. `提供C9高校研究生招生公告的真实数据源`
2. `采集清华大学研究生招生公告`
3. 非 C9：输入 `采集安徽工业大学研究生招生公告`，并填写该校官方入口 URL
4. 查询：`最近有哪些研究生招生复试信息？`

重点展示：

- `workflow_steps`：可解释执行过程
- `candidate_rankings`：候选数据源排序与拒绝原因
- `agent_trace`：站内找源过程
- `citations`：RAG 回答引用的文档和原文片段

## 当前边界

- C9 高校可无入口 URL 自动冷启动。
- 非 C9 高校需要显式入口 URL，准确率取决于入口页质量和站点结构。
- 本科、留学生、继续教育、MBA、第二学士学位不是当前主演示范围。
