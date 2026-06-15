# LLM 驱动的高校公开信息发现、结构化抽取与证据问答平台

本仓库实现的是一个面向高校公开信息治理与大模型应用开发场景的端到端 MVP，当前主流程为：

`自然语言输入 -> 官方入口目录/健康 Source 复用 -> 站内找源智能体 -> 候选校验/保存 -> 受控采集 -> 结构化抽取 -> 证据引用式问答`

## 当前范围与泛化策略

为了把演示稳定性放在第一位，当前自然语言主线已收缩为：

- 默认自动处理 C9 高校校级研究生招生公告数据源发现与采集
- C9 范围：北京大学、清华大学、复旦大学、上海交通大学、浙江大学、南京大学、中国科学技术大学、哈尔滨工业大学、西安交通大学
- 非 C9 高校支持“用户提供官方首页或研究生招生入口 URL”后的受控站内找源，不做开放式全网找官网
- 自然语言入口会明确拒绝高校本科/留学生/继续教育/MBA/第二学士学位，以及新闻中心、学校概况这类非主演示域
- 底层仍保留 `news_center`、`school_profile` 的手动数据源、采集和文档查询能力，便于兼容旧数据和内部验证

## 当前已实现能力

- 底层支持三类采集域：
  - `admissions_notice`：招生公告
  - `news_center`：新闻中心
  - `school_profile`：学校概况
- `admissions_notice` 保留六轨识别兼容，但当前自动发现只放行 `graduate`：
  - `undergraduate`
  - `graduate`
  - `international`
  - `continuing_education`
  - `mba`
  - `second_bachelor`
- FastAPI 后端与内置管理工作台
- 通过 SiliconFlow 接入 DeepSeek-V3.2，驱动自然语言解析、Source 解析、候选排序、结构化抽取和结果总结
- 官方入口目录：
  - 为 C9 高校维护官方主页与研究生招生入口
  - 只承担冷启动入口定位，不直接充当可采集 source
- 站内找源智能体：
  - 从官方入口目录或用户提供入口出发，只在官方域内发现招生子站、栏目页和列表页
  - 对非 C9 高校要求用户提供官方入口 URL，再进入同一套候选发现、排序、校验和保存流程
  - 返回 `agent_trace`、`candidate_rankings`、`failure_reason`
  - 区分 `site_home`、`channel_page`、`list_page`、`detail_page`、`file_page`
- 数据源知识库复用与治理：
  - 复用已保存的健康 source，而不是每次重新发现
  - 记录 `health_status`、`entrypoint_url`、`last_discovered_at`、`last_success_at`、`last_failure_reason`
  - source 失效后会重新进入站内发现，而不是继续盲用旧入口
- 轻量证据型 RAG：回答返回 citations、召回文档和置信说明
- 自然语言采集链路会在本次采集完成后读取新文档，并立即返回证据型回答
- 可解释工作流：自然语言执行接口返回固定步骤状态
- 数据源模板、适配器目录、候选数据源基础校验与保存
- 文档查询、文档证据查看、按大学分组、大学归属后处理分类
- APScheduler 进程内定时调度
- SQLAlchemy + Alembic 数据库模型与迁移
- pytest 覆盖当前 MVP 关键链路

## 快速启动

1. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
```

2. 复制环境变量模板

```bash
copy .env.example .env
```

3. 安装 Playwright Chromium

```bash
python -m playwright install chromium
```

4. 初始化或迁移数据库

```bash
python scripts/migrate_db.py
```

5. 启动服务

```bash
uvicorn app.main:app --reload
```

浏览器访问：

- 首页工作台：`http://127.0.0.1:8000/`
- OpenAPI 文档：`http://127.0.0.1:8000/docs`

## Windows 常用命令

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_dev.ps1
powershell -ExecutionPolicy Bypass -File scripts/status_dev.ps1
powershell -ExecutionPolicy Bypass -File scripts/stop_dev.ps1
powershell -ExecutionPolicy Bypass -File scripts/restart_dev.ps1
```

项目根目录也提供了快捷入口：

```powershell
.\dev.ps1 up
.\dev.ps1 down
.\dev.ps1 status
.\dev.ps1 restart
```

## 推荐操作流

### 稳定 C9 演示

1. 在首页输入 C9 研究生招生公告相关自然语言需求，例如 `采集清华大学研究生招生公告`
2. 不填写站内入口 URL 时，系统会先查健康 source；若未命中，则从官方入口目录拿到研究生招生入口
3. 可选填写高校官网首页或研招入口 URL，覆盖默认入口并进入实时站内发现
4. 先校验并保存候选数据源
5. 对指定数据源发起采集
6. 在结果面板查看候选排序、失败原因、智能体轨迹、bootstrap strategy 和 source 健康状态
7. 在文档区查看结果、证据和查询结果

### 非 C9 泛化演示

非 C9 高校不走内置官方目录。请同时输入高校名称和该校官网首页或研究生招生入口 URL，例如：

```text
采集安徽工业大学研究生招生公告
```

并在“站内入口 URL”中填写该校官方入口。系统会只在该入口同域内做候选发现、候选排序、结构化校验和 source 保存；如果没有提供入口 URL，会返回可解释拒绝信息，避免模型凭空猜官网。

## API 概览

### 数据源

- `POST /api/v1/sources`
- `GET /api/v1/sources`
- `GET /api/v1/sources/{id}`
- `PUT /api/v1/sources/{id}`
- `DELETE /api/v1/sources/{id}`
- `GET /api/v1/sources/adapters`
- `GET /api/v1/sources/templates`
- `POST /api/v1/sources/probe`
- `POST /api/v1/sources/validate-candidate`
- `POST /api/v1/sources/{id}/validate`

### 任务

- `GET /api/v1/tasks`
- `POST /api/v1/tasks`
- `GET /api/v1/tasks/{id}`
- `POST /api/v1/tasks/{id}/retry`

### 文档

- `GET /api/v1/documents`
- `GET /api/v1/documents/by-university`
- `GET /api/v1/documents/{id}`
- `GET /api/v1/documents/{id}/evidence`
- `DELETE /api/v1/documents/{id}`
- `POST /api/v1/documents/postprocess/university-classify`

### 自然语言

- `POST /api/v1/nl/parse`
- `POST /api/v1/nl/execute`
- `GET /api/v1/nl/tasks`
- `POST /api/v1/ask`

### 仪表盘

- `GET /api/v1/dashboard/overview`

## 当前实现说明

- 默认本地开发数据库是 SQLite，不要求 PostgreSQL 或 Redis 才能运行
- 官方入口目录和数据源知识库分层：
  - 官方入口目录只保存 C9 高校官方主页/研招入口
  - 非 C9 高校需要用户提供官方入口 URL 后进入受控站内发现
  - 数据源知识库只保存已验证、可复用的真实列表页/栏目页
- 文档查询当前采用 SQL 条件过滤和模糊匹配，不是向量检索
- 后台任务当前采用 FastAPI 进程内线程执行，不是 Celery/Redis 架构
- 如未配置 `SILICONFLOW_API_KEY`，部分链路会回退到规则逻辑，便于本地开发

## 相关文档

- Agent 协作手册：[AGENTS.md](/D:/1zhinengcaiji/AGENTS.md)
- 项目报告：[PROJECT_REPORT.md](/D:/1zhinengcaiji/docs/PROJECT_REPORT.md)
- 功能审计矩阵：[MVP功能审计矩阵.md](/D:/1zhinengcaiji/docs/MVP功能审计矩阵.md)
- 演示流程：[DEMO_WORKFLOW.md](/D:/1zhinengcaiji/docs/DEMO_WORKFLOW.md)
- RAG 闭环说明：[RAG_WORKFLOW.md](/D:/1zhinengcaiji/docs/RAG_WORKFLOW.md)
- Git 展示交付清单：[GIT_HANDOFF.md](/D:/1zhinengcaiji/docs/GIT_HANDOFF.md)
