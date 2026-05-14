# 任务 4：历史数据分页 API

## Goal

为历史电费读数提供后端/API 层分页与排序能力，避免前端一次性拉取全量历史再本地截断，解决页面下方历史数据过长和后续数据增长后的性能问题。

## What I Already Know

* 用户明确要求不要只在前端 `slice`，分页应由后端/API 支持。
* 用户建议响应结构为 `{ items, page, pageSize, total }`。
* 验收要求支持 `page`、`pageSize` 和排序。
* 默认排序应按采集时间倒序。
* 空数据、越界页、非法 `pageSize` 必须有稳定行为。
* 前端不应一次性拿全量历史数据。
* 当前 `/api/readings` 由 `MonitorService.readings()` 暴露，返回 `{ readings, currentReading }`。
* 当前 `Repository.list_readings(limit=200)` 只支持固定上限，不支持 offset/count 或总数。
* 当前前端 `refreshReadings()` 调用 `/api/readings`，直接把返回的 `readings` 同时用于表格和趋势图。
* `backend/app/main.py` 当前存在未解决冲突标记；实现该任务前需先处理或绕开冲突影响。
* 本任务建立在任务 3 的历史数据落库基础上：历史读数应以数据库为事实来源。

## Assumptions

* 本任务应覆盖后端 API、service、repository 和前端消费方式，属于跨层改动。
* 先采用 page-based pagination，除非实现时发现数据规模或 UX 明确需要 cursor pagination。
* `page` 使用 1-based 编号，默认 `page=1`。
* 默认 `pageSize` 使用一个小而稳定的值，例如 20；允许上限由后端统一限制，例如最大 100。
* 越界页返回空 `items`，同时保留规范化后的 `page`、`pageSize`、`total`。
* 非法 `page` / `pageSize` 应走项目既有 validation/error contract，而不是静默产生不可预测结果。
* 排序字段先限制在白名单内，至少支持采集时间；方向支持 `asc` / `desc`。
* 为避免图表方向被倒序影响，前端可以在当前页数据内按需要重新排列展示，但不能靠前端截断全量数据。

## Open Questions

* 暂无阻塞问题；实现时优先沿用项目现有 API 错误格式与前端交互风格。

## Requirements

* 后端读数历史接口支持分页：
  * 接受 `page` 和 `pageSize` 查询参数。
  * 返回 `items`、`page`、`pageSize`、`total`。
  * `items` 中的 Reading payload 保持现有字段语义：`collectedAt`、`building`、`room`、`numericValue`、`unit`。
* 后端读数历史接口支持排序：
  * 默认按采集时间倒序。
  * 排序参数必须使用白名单，避免 SQL 拼接风险。
  * 同一采集时间下应使用稳定的次级排序，例如读数自增 id。
* 边界行为稳定：
  * 空数据库返回 `items: []`、`total: 0`，page 元数据稳定。
  * 越界页返回空 `items`，不抛运行时错误。
  * 非法 `page`、非法 `pageSize`、超过最大 `pageSize` 有明确一致行为，并有测试覆盖。
* 前端改为分页消费：
  * 不再一次性拉取全量历史读数。
  * 表格只渲染当前页数据。
  * UI 提供基本分页控制，能刷新当前页或切换页。
  * 趋势图使用当前接口返回的数据或单独的有界数据请求，不能恢复全量拉取。
* 兼容与迁移：
  * 如果保留 `/api/readings` 路径，应明确新旧字段的兼容策略。
  * 如果新增路径，也要让前端迁移到新分页路径，避免继续调用全量接口。

## Acceptance Criteria

* [ ] 历史读数 API 支持 `page` / `pageSize` / 排序查询参数。
* [ ] API 默认按采集时间倒序返回。
* [ ] API 响应包含 `items`、`page`、`pageSize`、`total`，且 `items` 为 Reading 数组。
* [ ] 空数据返回稳定空分页结果。
* [ ] 越界页返回稳定空 `items`，不会 500。
* [ ] 非法 `pageSize` 和非法排序参数按项目错误约定稳定处理。
* [ ] 前端不再一次性拿全量历史数据后本地截断。
* [ ] 前端历史表格能基于分页元数据翻页/刷新。
* [ ] 单元测试覆盖 repository/service/API 层分页、排序和边界输入。
* [ ] 前端相关测试或轻量验证覆盖分页请求参数和空/越界状态。

## Definition Of Done

* Backend tests added or updated for pagination query behavior and repository count/list behavior.
* Frontend behavior updated so network request is bounded by page/pageSize.
* Lint/typecheck or project-equivalent verification passes if configured.
* API contract changes are reflected in `.trellis/spec/backend/` or `.trellis/spec/frontend/` if they become durable conventions.
* Existing task 3 persistence behavior remains intact.

## Out Of Scope

* 不实现无限滚动或 cursor pagination，除非实现前发现 page-based pagination 明显不适合当前数据模型。
* 不重做趋势图视觉设计。
* 不新增外部数据库或缓存服务。
* 不改变校园门户采集、登录、告警策略。
* 不处理与本任务无关的现有工作区冲突，除非它们阻塞实现或测试。

## Technical Notes

* Likely impacted code:
  * `backend/app/persistence/repository.py`
  * `backend/app/services/monitor_service.py`
  * `backend/app/main.py`
  * `backend/tests/unit/test_monitor_service.py`
  * repository/API tests that cover `/api/readings`
  * `frontend/src/app.js`
  * `frontend/src/index.html`
  * `frontend/src/styles.css`
* Relevant existing behavior:
  * `Repository.list_readings(limit=200)` currently orders by `collected_at DESC, id DESC LIMIT ?` and then reverses rows before returning.
  * `MonitorService.readings()` currently returns `{"readings": ..., "currentReading": ...}`.
  * Frontend `refreshReadings()` currently destructures `{ readings }` from `/api/readings`.
* Recommended implementation shape:
  * Add a small pagination parser/validator near API/service boundary.
  * Keep SQL sort fields whitelisted and use parameter binding for numeric values.
  * Prefer `COUNT(*)` plus `LIMIT/OFFSET` for page-based pagination.
  * Preserve current reading lookup separately via `get_latest_successful_reading()`.
* Before implementation, read the backend and frontend spec checklist files referenced in this task's JSONL context.

## Context Files

* `implement.jsonl` and `check.jsonl` should include only spec/research context files, not source code paths.
