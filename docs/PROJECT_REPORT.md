# LLM 驱动的高校公开信息发现、结构化抽取与证据问答平台

## 项目概述

本项目是一个面向高校公开信息治理与大模型应用开发场景的端到端 MVP。系统支持用户用自然语言触发“站内找源智能体”，从高校官方入口目录或用户提供入口出发，在官方域内发现真实可采集数据源，对候选源做基础校验和类型判定，然后走受控采集管道完成结构化抽取，并基于已采集文档生成带证据引用的问答结果。

核心链路：

`自然语言 -> 官方入口目录/健康 Source 复用 -> 站内找源智能体 -> 候选排序/校验 -> 受控采集 -> 结构化抽取 -> 证据型 RAG 问答`

## 架构亮点

- 受控 LLM 工作流：模型负责理解、候选排序和失败诊断，系统负责校验、调度、去重和入库。
- 官方入口目录：单独维护 C9 高校官方主页与研究生招生入口，只承担冷启动入口定位，不直接充当 source；非 C9 高校需要用户提供官方首页或研招入口 URL 后进入受控站内发现。
- 站内找源智能体：将官网入口、招生子站、栏目页、列表页、详情页、附件页区分建模，支持 `agent_trace`、`candidate_rankings` 和 `failure_reason`，避免把单篇详情页或附件页误保存为长期 source。
- 数据源知识库治理：已发现 source 会沉淀为长期记忆，并维护 `health_status`、`entrypoint_url`、`last_success_at`、`last_failure_reason`，用于热启动复用和失效后重发现。
- 六轨招生识别：在 `admissions_notice` 内统一处理 `undergraduate`、`graduate`、`international`、`continuing_education`、`mba`、`second_bachelor`。
- 证据型问答：回答必须绑定已召回文档，并返回 citation、原文链接和片段。
- 可解释执行：自然语言执行接口返回固定 workflow steps，便于定位每一步状态。

## 技术栈

- FastAPI
- SQLAlchemy + Alembic
- SQLite 默认本地运行
- Playwright / Crawl4AI / requests
- SiliconFlow + DeepSeek
- pytest

## 关键设计取舍

- 当前不引入向量数据库，优先用 SQL 过滤和轻量打分保证可运行、可解释。
- 当前不做开放式全网找官网，优先通过 C9 官方入口目录解决冷启动；对非 C9 高校，要求用户提供官方入口 URL 后再由站内找源智能体完成真实 source 发现。
- 数据源知识库只复用健康且新鲜的 source，不增加向量数据库或外部检索依赖。
- 当前不引入 Celery/Redis，后台任务使用进程内线程，适合本地 demo。
- 当前不做开放式 Agent 抓取，避免模型直接控制系统行为；智能体只负责找源、排序、诊断，真正采集仍走受控流水线。
- 当前保留三个采集域，优先把 RAG、证据引用和前端证据展示做扎实。

## 对外展示表述建议

- 设计并实现 LLM 驱动的高校公开信息发现、结构化抽取与证据问答平台，支持自然语言触发的官方入口目录、站内找源智能体、候选校验、受控采集和引用式 RAG 问答。
- 构建可解释工作流与证据展示，返回 workflow steps、candidate rankings、citations、原文链接和片段，降低问答幻觉与错误选源风险。
- 使用 FastAPI、SQLAlchemy、Playwright/Crawl4AI、SiliconFlow/DeepSeek 完成从官网选源、受控采集、结构化抽取到检索问答的端到端链路。
