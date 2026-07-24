# 03 数据模型与存储边界

## 1. 三个 schema，三类职责

```text
Teable-owned tables   9 张领域表，督导办可视化维护，应用经 Teable REST 读写
sd_app schema         会话/审计/幂等/outbox/job/report/AI 等工程表，Alembic 管理
bi schema             对观远和聚合查询的稳定视图/物化快照，Alembic 管理
```

禁止直接 SQL 写 Teable 内部表。Teable 升级可以改变内部表名，`bi` 兼容层必须用 POC 和自动 contract test 验证。

## 2. 领域关系

```text
org_unit 1──N person
person   1──N role_assignment N──1 org_unit(scope root)
key_work 1──N task 1──N task_owner N──1 person
                   ├──N progress_log
                   ├──N urge_log
                   └──N related task
work_calendar 独立提供工作日/节假日规则
```

## 3. Teable 领域表

### 3.1 org_unit

`unit_id`、`name`、`type`、`parent_id`、`oa_org_id`、`sort`、`active`、`updated_at`。`oa_org_id` 唯一（非空时）；禁物理删除。

### 3.2 person

`person_id`、`name`、`unit_id`、`oa_account`、`active`、`authz_version`、`updated_at`。移除单值 `role`；一个人可通过 role_assignment 拥有多角色。`oa_account` 唯一且不进入 LLM。

### 3.3 role_assignment

| 字段 | 说明 |
|---|---|
| assignment_id | 主键 |
| person_id | 人员 |
| role | `supervision_admin/unit_coordinator/domain_owner/leader/ops_admin` |
| scope_unit_id | 数据范围根；全集团角色为空 |
| valid_from/valid_until | 生效区间 |
| active | 停用，不物理删除 |

唯一约束语义：同一人、角色、范围、生效区间不得重复。变更后 `person.authz_version + 1` 并使权限缓存失效。

### 3.4 key_work

`kw_id`、`year`、`seq`、`name`、`goal`、`lead_unit_id`、`lead_person_id`、`status`、`progress`（派生）、`revision`、`updated_at`。`(year,seq)` 唯一。

### 3.5 task

`task_id`、`kw_id`、`unit_id`、`category`、`content`、`measures`、`deadline`、`cycle`、`weight`、`progress`（派生）、`status`、`related_tasks`、`ai_flag`、`ai_note`、`exempt_until`、`revision`、`updated_at`。

- `weight` 默认 1，范围 0.1–100；重点工作进度按未暂缓任务权重平均。
- `revision` 用于并发修改检查。
- `status` 由规则引擎计算，人工豁免不等于完成。

### 3.6 task_owner

`task_owner_id`、`task_id`、`person_id`、`owner_type(primary/collaborator)`、`active`。每个 task 必须且只能有一个 active primary owner，避免依赖多选字段顺序。

### 3.7 progress_log（append-first）

| 字段 | 说明 |
|---|---|
| log_id/task_id | 主键/事项 |
| command_id | BFF 幂等命令 ID，唯一 |
| report_date/submitted_at | 业务日期/真实提交时间（带时区） |
| reporter_id/on_behalf_of | 实际提交人/代报对象 |
| content/progress | 正文/0–100 |
| attachments | 已完成扫描的附件引用 |
| is_correction/correction_reason/approved_by | 进度回退或更正的审计链 |
| ai_result/ai_comment/ai_question/ai_run_id | AI 建议及来源 |
| reply/appeal/review_status | 回复、申诉、人工裁决状态 |

正常填报只追加。纠错不覆盖旧行，追加 `is_correction=true` 的新行并引用原因/批准人。禁止“进度绝不回退”的假精确性。

### 3.8 urge_log

`urge_id`、`task_id`、`outbox_id`、`type`、`level`、`target_id`、`content`、`dedup_key`、`planned_at`、`sent_at`、`oa_msg_id`、`result`。`dedup_key` 唯一，内容是最终对用户可见的快照。

### 3.9 work_calendar

`calendar_date`（主键）、`is_workday`、`name`、`source`、`revision`。每年提前维护，缺少目标日期时 scheduler 拒绝计算并告警，不猜工作日。

## 4. sd_app 应用表（Alembic 管理）

| 表 | 关键字段/约束 | 用途 |
|---|---|---|
| auth_session | sid、person_id、kid、expires_at、revoked_at、last_seen_at | 单会话撤销/审计 |
| file_object | file_id、owner_person_id、task_id、storage_key、sha256、scan_state、scan_result | 附件元数据、对象级授权与扫描证据；二进制不入数据库 |
| submission_command | command_id、idempotency_key UNIQUE、person_id、task_id、payload_hash、state、teable_record_id | 跨 Teable/PG saga 恢复 |
| webhook_receipt | provider、event_id UNIQUE、received_at、payload_hash、state | Webhook 重放/幂等 |
| audit_event | event_id、who、what、target、result、ip、request_id、created_at | 2 年不可通过 API 删除 |
| job_run | job_run_id、job、scheduled_for UNIQUE、state、config_hash、counts、error_code | 调度证明与重跑 |
| outbox_message | outbox_id、kind、dedup_key UNIQUE、payload、state、available_at、attempt_count | durable 外部动作 |
| outbox_attempt | attempt_id、outbox_id、started/finished、result、status_code、redacted_error | 每次外呼证据 |
| outbox_replay_approval | approval_id、outbox_id、审批/执行幂等键、approved/consumed_by/at、reason_hash | dead letter 双人审批与人工补发证据 |
| report_version | report_id、period、audience、revision、content_md、state、approved/issued_by/at | 周/月报版本和签发 |
| ai_run | ai_run_id、purpose、input_hash、source_ids、prompt_version、model、params、output、schema_valid、reviewed_by/result | AI 全链溯源 |

`file_object` 只有 `scan_state=clean` 且 owner/task 绑定与本次命令一致时才可进入填报；扫描中、失败或隔离态均 fail closed。outbox payload 不存 OA 密码/token。敏感正文只保存必要引用或加密字段，并按数据分类设置保留期。

同一 dead letter 同时只能有一条未消费补发审批。`supervision_admin` 审批后，必须由不同人员的 `ops_admin` 消费审批并把消息重置为 `retry`；历史 attempt 不删除，补发批次的重试计数从 0 重新开始。审批和执行均使用独立 UUID 幂等键，并与 `audit_event` 在同一事务提交。

`job_run` 的正确性边界是 PostgreSQL：执行前先申请由 `job + scheduled_for` 派生的事务级 advisory lock，再以 `(job, scheduled_for)` 唯一键插入 `running` 记录；竞争失败或唯一键冲突均按幂等跳过。完成时只允许把仍为 `running` 的同一 `job_run_id` 改为 `succeeded/failed`，外部异常正文不得写入，仅保存稳定 `error_code`。

## 5. 读写矩阵

| 主体 | 允许 | 禁止 |
|---|---|---|
| sd-api | Teable REST 领域读写；SQLAlchemy Core 读写 `sd_app`; 读 `bi` | Teable 内部 SQL 写；同步发送 OA/LLM 长任务 |
| sd-worker | Teable REST；读写 `sd_app`; 刷新 `bi` 物化对象 | 对外用户接口；绕过 outbox 发送 |
| web | BFF | Teable/PG/LLM/OA 直连 |
| 观远 | `bi_reader` 只读 `bi` | 业务 schema、`sd_app`、写操作 |
| 督导办 | Teable 管理 UI + 受控 BFF | 直接 DB 管理日常业务 |

## 6. BI 稳定合同

| 对象 | 类型 | 用途 |
|---|---|---|
| bi.v_task | view | 事项全景、责任人、状态、权重进度 |
| bi.v_progress | view | 流水、及时性、更正标识 |
| bi.v_urge | view | 催办、回执、响应时长 |
| bi.week_snapshot | app-owned table/view | 按周冻结指标，worker 经受控 SQL 写/刷新 |

`bi.week_snapshot` 明确属于自有对象，不再与“PG 只读”冲突。对观远字段名和类型视为外部 API，删除/改名需版本迁移和双写窗口。

## 7. 指标口径

| 指标 | 公式 |
|---|---|
| 完成率 | 完成 task 数 / 排除暂缓后的 task 总数 |
| 加权进度 | Σ(task.progress × task.weight) / Σ有效 weight |
| 逾期率 | 逾期未完成数 / 未完成数 |
| 填报及时率 | 截止时点前有效提交数 / 应提交数 |
| 催办响应率 | 催办后 3 个工作日内出现有效新流水的催办数 / 已送达催办数 |
| AI 预警分布 | 当前有效 ai_flag 按类别计数；人工驳回单列 |

所有指标函数在后端 `metrics/` 单点实现，BI 使用 `bi` 视图结果，不复制公式。

## 8. 迁移、备份与保留

- Alembic 管理 `sd_app` 与 `bi`；`docker-entrypoint-initdb.d` 仅做首次角色/数据库 bootstrap，不是迁移系统。
- 每次迁移必须有 upgrade、可行的 downgrade 或明确不可逆说明、备份点和数据校验 SQL。
- 目标：WAL 持续归档，RPO ≤15 分钟，RTO ≤4 小时；每日全量备份，至少一份离机/异地、加密、季度恢复演练。
- 审计保留 ≥2 年；业务/附件/AI 数据保留按制度确认，未确认前不得实现自动清理。
