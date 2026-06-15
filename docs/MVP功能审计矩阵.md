# MVP 功能审计矩阵

本矩阵用于说明当前系统的真实功能边界，并作为开发文档删改依据。

审计事实来源：

- `app/api/routes/*` 公开接口
- `app/static/index.html` 与 `app/static/admin.js` 的实际工作台流程
- `app/models/*` 与 `app/services/*` 的数据模型和执行链路
- `tests/test_api.py` 与 `tests/test_services.py` 的已覆盖行为

## 对照结果

| 文档章节/能力 | 审计结论 | 当前真实情况 | 处理动作 |
| --- | --- | --- | --- |
| 系统定位为“通知公告结构化平台” | 实际已偏航需改写 | 当前是“高校官网采集工作台”，核心是发现真实数据源并采集三类公开内容 | 改写 |
| 仅支持通知公告采集 | 实际已偏航需改写 | 已支持 `admissions_notice`、`news_center`、`school_profile` 三类采集域 | 改写 |
| `admissions_notice` 只做本科/研究生粗分 | 实际已扩展需改写 | 当前已支持六轨识别：`undergraduate`、`graduate`、`international`、`continuing_education`、`mba`、`second_bachelor` | 改写 |
| 自然语言驱动受控执行 | 已实现 | `nl/parse`、`nl/execute`、`ask` 都走固定解析与编排链路 | 保留并补充细节 |
| 自然语言只面向通知检索/采集 | 实际已偏航需改写 | 还支持“发现真实数据源”场景 | 改写 |
| 多高校/学院采集源配置 | 已实现 | `sources`、`adapters`、`templates`、`validate` 已落地 | 保留并扩展描述 |
| 受控选源智能体 | 已实现 | `SourceDiscoveryAgentService` 已接入 `admissions_notice`，支持 `agent_trace`、`candidate_rankings`、`failure_reason` | 补充 |
| 候选类型判定 | 已实现 | 已区分 `site_home`、`channel_page`、`list_page`、`detail_page`、`file_page`、`stats_page` | 补充 |
| 从列表页发现链接、从详情页抽取正文/附件 | 已实现 | `ListDetailPipeline` + `DetailParser` + 提取器链路已覆盖 | 保留 |
| 结构化抽取 + 原始证据保留 | 已实现 | `documents`、`raw_pages`、`llm_logs`、`evidence` 接口齐全 | 保留 |
| 定时调度与失败重试 | 部分实现 | APScheduler 与任务重试已实现，失败策略不是文档原写的 Redis/Celery 架构 | 改写 |
| PostgreSQL 主库 | 未实现应删除 | 当前默认开发数据库是 SQLite，PostgreSQL 只是可选部署方式 | 删除“当前已支持”表述 |
| Redis 任务队列 | 未实现应删除 | 当前后台执行使用进程内线程，不依赖 Redis | 删除“当前已支持”表述 |
| Celery Worker 架构 | 未实现应删除 | 没有 Celery 运行链路 | 删除 |
| PostgreSQL 全文检索 | 未实现应删除 | 当前查询是 SQL 条件过滤 + `ilike` 模糊匹配 | 删除并改写 |
| 向量检索 / pgvector / embeddings | 未实现应删除 | 只有占位实现，无调用链 | 删除并降级为未来扩展 |
| 教师主页/课程系统第一阶段支持 | 未实现应删除 | 当前无接口、无模型、无执行链路 | 删除 |
| 文件存储/MinIO 附件体系 | 未实现应删除 | 当前只记录附件 URL 与元信息 | 删除 |
| 开放式自治抓取 | 未实现应删除 | 当前受控、固定管道执行 | 删除 |
| 数据源探测与真实候选返回 | 文档少写了但系统已有 | `sources/probe` 与 `nl/execute -> discover_sources` 已实现 | 补充 |
| 候选数据源基础校验并保存 | 文档少写了但系统已有 | `sources/validate-candidate` 已实现 | 补充 |
| 候选排序、拒绝原因、智能体轨迹 | 文档少写了但系统已有 | 工作台和接口已返回 `candidate_rankings`、`reject_reason_code`、`agent_trace` | 补充 |
| 数据源模板与适配器目录 | 文档少写了但系统已有 | `sources/adapters`、`sources/templates` 已实现 | 补充 |
| 文档按大学分组 | 文档少写了但系统已有 | `documents/by-university` 已实现 | 补充 |
| 文档大学归属后处理分类 | 文档少写了但系统已有 | `documents/postprocess/university-classify` 已实现 | 补充 |
| 删除文档 / 删除数据源 | 文档少写了但系统已有 | `DELETE /documents/{id}`、`DELETE /sources/{id}` 已实现 | 补充 |
| 管理工作台首页 | 文档少写了但系统已有 | 根路径 `/` 已提供实际操作工作台 | 补充 |
| 任务进度轮询 | 文档少写了但系统已有 | `tasks/{id}` 返回进度字段，前端轮询展示 | 补充 |

## 冗余清理分级

### 建议直接删除

- `app/services/search/hybrid_search.py`
- `app/services/llm/embedder.py`
- 顶层 `crawler/` 残留骨架与 `__pycache__`
- 根目录 0 字节的百分号编码 Markdown 残留文件

### 本轮已处理

- `app/services/crawler/notice_crawler.py`
- 旧版 Redis、scraperai、嵌入模型占位配置已清理
- `docker/docker-compose.yml` 已收敛为单 API 服务 + SQLite 数据卷，不再声明 PostgreSQL/Redis

### 不删，只修正文档

- DeepSeek 驱动的数据源发现
- 受控选源智能体
- 候选校验与保存
- 学校概况采集
- 新闻中心采集
- 文档大学分类后处理

这些能力虽偏离旧版开发文档，但都具备真实入口、测试覆盖或用户价值。
