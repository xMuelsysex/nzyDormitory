# 任务 3：采集链路改为读写数据库

## Goal

把后端采集链路从内存/即时显示数据源收束为稳定的数据库读写链路：采集成功时持久化历史并维护可读取的最近成功读数，采集失败时留下简要可诊断状态，让余额展示和后续趋势图都能以数据库为事实来源。

## What I Already Know

* 用户明确要求数据库接上后先稳定后端落库，不马上改 UI。
* 每次成功采集需要生成一条历史记录或更新 latest snapshot。
* 失败采集也要记录简要状态，方便排查。
* 当前余额需要从数据库读取最近一次成功记录。
* 趋势图后续应可直接消费历史记录。
* 现有后端已经有 SQLite `Repository`、`electricity_readings`、`collection_runs`、`rooms`、`collection_failures` 等基础表。
* `CollectionScheduler.run_once()` 当前会创建 `collection_runs`，成功后调用 `insert_reading()`，失败后把 run 标为 `failed`。
* `Repository.list_readings()` 已按 `collected_at` 返回历史读数，`MonitorService.readings()` 直接暴露该列表。
* `electricity_readings` 已有 `(room_id, collection_window_start)` 唯一索引，具备按采集窗口去重的基础。

## Assumptions

* 本任务优先限定在后端：scheduler、service、repository、API payload 和测试。
* “latest snapshot” 可以是独立表，也可以由最近一次成功的 `electricity_readings` 查询派生；实现时优先遵循 `.trellis/spec/backend/database-guidelines.md`。
* 失败状态至少需要包含发生时间、状态/错误码、简短消息，以及尽可能关联房间和采集窗口。
* 趋势图消费的历史接口可以复用 `/api/readings`，但返回契约需要稳定、按时间排序、避免混入失败记录。

## Open Questions

* 暂无阻塞问题；实现前先按后端数据库规范确认 latest snapshot 的推荐形态。

## Requirements

* 成功采集必须持久化为数据库事实来源：
  * 对新采集窗口插入一条 `electricity_readings` 历史记录。
  * 对重复采集窗口保持幂等，不产生重复历史。
  * 采集 run 状态能区分 `success`、`duplicate` 和 `failed`。
* 失败采集必须持久化简要状态：
  * 手动 `run_once()` 和定时 `_run_and_reschedule()` 中的认证失败、会话过期、业务错误、未知异常都应在数据库中可查。
  * 错误消息需要截断到安全长度，避免污染数据库。
* 当前余额读取必须以数据库最近成功记录为准：
  * 新增或调整 service/repository 方法，读取最近一次成功采集的余额。
  * API 状态或读数响应应能让后端消费者拿到当前余额，不依赖 portal 即时状态。
* 历史记录需要适合趋势图后续消费：
  * 历史接口返回成功读数，不混入失败 run。
  * 默认返回数量有限，排序稳定。
  * payload 保留 `collectedAt`、`building`、`room`、`numericValue`、`unit` 等字段。
* 不改变现有 UI 行为和布局。

## Acceptance Criteria

* [ ] 每次成功采集会插入一条历史记录，或在同一房间/采集窗口下幂等更新 latest snapshot。
* [ ] 失败采集会记录简要状态，包含错误码/状态与消息，便于排查。
* [ ] 当前余额由数据库最近一次成功记录读取，不依赖刚抓取到的内存对象。
* [ ] 历史读数接口可以直接供趋势图消费，且只包含成功读数。
* [ ] 重复采集窗口不会插入重复读数，也不会重复触发基于新读数的告警。
* [ ] 单元测试覆盖成功落库、重复窗口、失败状态记录、最近成功余额读取。
* [ ] 后端测试通过。

## Definition Of Done

* Tests added/updated for repository, scheduler, and service behavior where relevant.
* Backend unit tests pass.
* Lint/typecheck or project-equivalent verification passes if configured.
* No UI implementation changes in this task.
* Any durable database contract discovered during implementation is reflected in `.trellis/spec/backend/` if needed.

## Out Of Scope

* 不改前端趋势图和当前余额 UI。
* 不新增外部数据库服务，继续使用项目现有 SQLite persistence。
* 不改校园门户登录/抓取交互，除非为持久化状态处理所必需。
* 不做告警策略重构。

## Technical Notes

* Relevant code inspected:
  * `backend/app/persistence/repository.py`
  * `backend/app/scheduler/collection_scheduler.py`
  * `backend/app/services/monitor_service.py`
  * `backend/app/services/models.py`
  * `backend/tests/unit/test_collection_scheduler.py`
  * `backend/tests/unit/test_monitor_service.py`
* Current worktree has an existing conflict marker in `backend/app/main.py`; implementation should resolve or avoid it intentionally before running full tests.
* Use `rg` for value/contract searches before changing database fields or response payloads.
* Backend spec index points to:
  * `.trellis/spec/backend/marketplace-adapted-contracts.md`
  * `.trellis/spec/backend/database-guidelines.md`

## Context Files

* `implement.jsonl` and `check.jsonl` should include only spec/research context files, not source code paths.
