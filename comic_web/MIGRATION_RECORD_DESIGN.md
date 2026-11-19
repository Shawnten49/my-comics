# 漫画迁移记录设计

> 文档状态：已实施  
> 目标版本：迁移记录模型 v4  
> 本文档替代 `MIGRATION_DESIGN.md` 中“批次主表 + 任务明细”的迁移记录与展示部分

## 1. 设计目标

迁移记录只围绕一个对象设计：

```text
一本漫画的一次迁移 = 一条迁移记录
```

用户一次选择多本漫画时，系统为每本漫画各自创建一条记录。一次提交产生的所有记录共享相同的 `batch_no`，但 `batch_no` 只是记录上的普通字段，不再对应独立批次表、批次详情页或二级明细页。

这样可以满足：

1. 迁移记录页直接展示所有漫画迁移记录。
2. 不显示“批次列表”，也不需要先进入批次再查看漫画。
3. 不保存批次汇总数据，不需要同步批次成功数、失败数、进度等派生统计。
4. 前端只维护一个分页记录列表。
5. 后台线程直接领取一条漫画迁移记录并处理。

## 2. 对“批次号”的重新定义

`batch_no` 保留，但语义调整为“本次批量提交的关联编号”：

```text
MIG-20261118-145930-A1B2C3
```

它不是业务主数据，也不是数据库中的独立对象。

例如一次选择三本漫画：

```text
记录 1：book_id=1001，batch_no=MIG-20261118-145930-A1B2C3
记录 2：book_id=1002，batch_no=MIG-20261118-145930-A1B2C3
记录 3：book_id=1003，batch_no=MIG-20261118-145930-A1B2C3
```

数据库中只有三条 `migration_records`，没有对应的批次主表记录。

用途仅限：

- 页面展示。
- 用户搜索本次批量提交的所有漫画。
- 提交成功后按该编号筛选刚生成的记录。
- 问题排查时关联一次操作。

不用于：

- 查询批次汇总。
- 进入批次详情。
- 作为外键。
- 计算批次完成状态。

## 3. 迁移记录页

### 3.1 路由

保留：

```text
/manage/migrations
/manage/migrations/new
```

移除：

```text
/manage/migrations/{batch_id}
```

迁移记录页只有一层列表。提交迁移后跳转到：

```text
/manage/migrations?q=MIG-20261118-145930-A1B2C3
```

这里只是把 `batch_no` 填入普通搜索框，不是进入批次详情。

### 3.2 页面结构

页面从上到下为：

```text
迁移记录标题
共 N 条记录
[迁移到外盘]

[搜索框]
[状态筛选]
[刷新]

迁移记录表格

[首页] [上一页] [页码] [下一页] [尾页]
```

不显示：

- 批次总进度条。
- 批次成功 / 失败汇总卡片。
- 批次详情入口。
- 展开按钮。
- 二级任务表。

### 3.3 表格字段

表格严格按照以下字段展示：

| 列 | 内容 | 展示规则 |
| --- | --- | --- |
| 迁移 ID | `id` | 从 `1` 开始自增的整数主键 |
| 漫画 ID | `book_id` | 等宽字体 |
| 漫画名称 | `book_title` | 过长省略，悬停显示完整名称 |
| 迁移批次号 | `batch_no` | 等宽字体，可点击后作为搜索词 |
| 原路径 | `source_path` | 完整文件路径快照，过长省略 |
| 迁移路径 | `destination_path` | 外盘最终目录的完整路径 |
| 迁移状态 | `status` + `stage` | 状态标签；迁移中时附带当前阶段 |
| 消息 | `message` | 成功固定为 `success`，失败显示具体错误 |
| 创建时间 | `created_at` | `YYYY-MM-DD HH:mm:ss` |
| 迁移时间 | `finished_at` | 未结束时显示 `—` |
| 操作 | 派生字段 | 可重试的失败记录显示“重试”，固定在最后一列 |

推荐列宽：

```text
迁移 ID         72px
漫画 ID       110px
漫画名称      260px
迁移批次号    220px
原路径        280px
迁移路径      280px
迁移状态      130px
消息          280px
创建时间      170px
迁移时间      170px
操作           80px
```

窄屏下表格可以横向滚动，不改成“批次卡片”或折叠详情。

### 3.4 状态显示

数据库可保留细粒度 `stage`，用户看到的状态归并为：

| 公开状态 | 状态文案 | 可附带阶段 |
| --- | --- | --- |
| `queued` | 等待迁移 | — |
| `migrating` | 迁移中 | 预检、复制、校验、切换存储、清理本地 |
| `completed` | 已完成 | — |
| `completed_with_warning` | 完成但有警告 | 本地隔离区清理失败 |
| `failed` | 失败 | 错误摘要 |
| `blocked_external` | 外盘未连接 | 等待重新连接 |
| `interrupted` | 已中断 | 等待服务恢复 |

例如：

```text
迁移中
正在校验
```

如果复制有进度，可以在状态单元格中显示：

```text
迁移中 · 复制
128 / 204 个文件
```

这只是当前记录自身的进度，不是批次进度。

错误信息不单独占一列。失败记录的“迁移状态”下方显示简短错误，完整内容作为悬停说明。

### 3.5 搜索

搜索框支持统一关键词匹配：

- 漫画 ID。
- 漫画名称。
- 迁移批次号。
- 原路径。
- 迁移路径。

示例：

```text
q=12345
q=漫画名称
q=MIG-20261118
q=/Volumes/Comics
```

### 3.6 状态筛选

```text
全部
等待迁移
迁移中
已完成
完成但有警告
失败
外盘未连接
已中断
```

### 3.7 分页

- 默认每页 30 条。
- 支持每页 `30 / 50 / 100` 条。
- 支持首页、上一页、页码、下一页、尾页。
- 默认按 `created_at DESC, id DESC` 排序。
- 查询参数写入 URL：

```text
/manage/migrations?q=&status=&page=1&page_size=30
```

### 3.8 刷新

当当前页存在未结束记录时：

- 页面每 2 秒刷新当前筛选列表。
- 页面进入后台时暂停刷新。
- 页面重新可见时立即刷新一次。
- 当前页没有未结束记录时停止轮询。

## 4. 单表数据模型

### 4.1 `migration_records`

使用一张表同时保存待执行任务、运行状态和历史结果：

```sql
CREATE TABLE migration_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL,
    book_id TEXT NOT NULL,
    book_title TEXT NOT NULL,
    source_storage_id TEXT NOT NULL DEFAULT 'local',
    destination_storage_id TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    destination_relative_path TEXT NOT NULL,
    source_path TEXT NOT NULL,
    destination_path TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    total_files INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    copied_files INTEGER NOT NULL DEFAULT 0,
    copied_bytes INTEGER NOT NULL DEFAULT 0,
    verified_files INTEGER NOT NULL DEFAULT 0,
    verification_summary TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
```

字段说明：

| 字段 | 用途 |
| --- | --- |
| `id` | 单条漫画迁移记录 ID |
| `batch_no` | 一次批量提交的关联编号，不是独立实体 |
| `book_id` | 漫画 ID 快照 |
| `book_title` | 漫画名称快照 |
| `source_storage_id` | 原存储，目前为 `local` |
| `destination_storage_id` | 外盘存储根 ID |
| `source_relative_path` | 原路径相对于本地漫画根目录 |
| `destination_relative_path` | 目标路径相对于外盘根目录 |
| `source_path` | 页面展示用原路径快照 |
| `destination_path` | 页面展示用迁移目标路径快照 |
| `status` | 对外状态 |
| `stage` | 内部执行阶段 |
| `created_at` | 记录创建时间 |
| `started_at` | 开始处理时间 |
| `finished_at` | 页面“迁移时间” |
| `updated_at` | 最近状态更新时间 |

`source_path` 和 `destination_path` 是历史快照，后续漫画名称、挂载路径或书籍记录变化时，原记录仍然可读。

实际执行时不能直接信任页面提交的路径：

- 源路径由 `books.storage_id + books.directory` 计算。
- 目标路径由 `storage_roots.root_path + destination_relative_path` 计算。
- 执行前重新验证路径仍在登记的存储根内。

### 4.2 索引

```sql
CREATE INDEX idx_migration_records_created
ON migration_records(created_at DESC, id DESC);

CREATE INDEX idx_migration_records_batch
ON migration_records(batch_no, created_at, id);

CREATE INDEX idx_migration_records_book
ON migration_records(book_id, created_at DESC);

CREATE INDEX idx_migration_records_status
ON migration_records(status, updated_at);
```

同一本漫画不能同时存在两个活动记录：

```sql
CREATE UNIQUE INDEX uq_migration_records_active_book
ON migration_records(book_id)
WHERE status IN ('queued', 'migrating', 'interrupted');
```

这也是应用层约束的兜底，不是外键。

### 4.3 无外键原则

`migration_records` 不声明外键：

- `book_id` 允许在历史页面中继续存在，即使书籍记录后来被重建。
- `destination_storage_id` 允许外盘记录暂时断开。
- `batch_no` 不是外键，也不要求存在 batch 主记录。

应用代码负责：

- 创建记录前检查漫画仍存在且位于本地。
- 创建记录前检查外盘存储根。
- 同一本漫画活动记录唯一。
- 运行前再次检查书籍和存储根。
- 记录失败时保留记录和源文件。

## 5. 创建记录的规则

用户一次选择 N 本漫画：

1. 生成一个 `batch_no`。
2. 创建 N 条 `migration_records`。
3. 每条记录的 `batch_no` 相同。
4. 每条记录都有独立的 `id`、`book_id`、`source_path`、`destination_path`、状态和时间。
5. N 条记录在同一个 SQLite 事务中写入。
6. 任意一本不合法时整批回滚，不生成半批记录。

响应示例：

```json
{
  "ok": true,
  "batch_no": "MIG-20261118-145930-A1B2C3",
  "count": 3,
  "record_ids": [1, 2, 3]
}
```

返回值包含 `batch_no` 只是为了前端跳转到：

```text
/manage/migrations?q=MIG-20261118-145930-A1B2C3
```

不存在 `/api/manage/batches/{batch_no}`。

## 6. 执行流程

迁移线程每次只领取一条未完成记录：

```sql
SELECT *
FROM migration_records
WHERE status IN ('queued', 'interrupted')
ORDER BY created_at, id
LIMIT 1;
```

领取后：

```text
queued
→ migrating + preflight
→ migrating + copying
→ migrating + verifying
→ migrating + switching_storage
→ migrating + cleaning_source
→ completed
```

异常状态：

```text
blocked_external
failed
interrupted
```

每条记录只更新自己的：

- `status`
- `stage`
- 文件数量。
- 字节数。
- 校验摘要。
- 错误信息。
- `started_at`
- `finished_at`
- `updated_at`

不再存在“刷新批次计数”的步骤。

复制和源目录删除安全规则保持不变：

1. 复制到外盘暂存目录。
2. 校验文件清单和 SHA-256。
3. 原子发布最终目录。
4. 切换 `books.storage_id`。
5. 本地源移入隔离目录。
6. 删除前再次核对。
7. 校验通过后删除本地隔离目录。
8. 记录标记完成。

任何校验失败都保留或恢复本地源目录。

macOS 在 exFAT 等外盘上自动生成的 `._*`、`.DS_Store` 和 `.localized` 文件属于文件系统元数据，不纳入内容文件清单、SHA-256 校验或漫画页面识别。

## 7. API 简化

### 7.1 查询记录

```http
GET /api/manage/migrations
```

参数：

```text
q
status
page
page_size
```

响应：

```json
{
  "total": 18,
  "page": 1,
  "page_size": 30,
  "total_pages": 1,
  "items": [
    {
      "id": 1,
      "book_id": "12345",
      "book_title": "示例漫画",
      "batch_no": "MIG-20261118-145930-A1B2C3",
      "source_path": "/path/to/comic/download/12345_示例漫画",
      "destination_path": "/Volumes/Comics/12345_示例漫画",
      "status": "migrating",
      "stage": "copying",
      "message": "",
      "progress": {
        "copied_files": 12,
        "total_files": 20
      },
      "created_at": "2026-11-18T14:59:30",
      "finished_at": null
    }
  ]
}
```

### 7.2 创建记录

```http
POST /api/manage/migrations
```

请求体不变：

```json
{
  "destination_path": "/Volumes/Comics",
  "book_ids": ["12345", "23456"],
  "verification_code": "<系统设置中配置的校验码>"
}
```

### 7.3 重试单条记录

```http
POST /api/manage/migrations/{record_id}/retry
```

重试规则：

- 只允许 `failed`、`blocked_external`、`interrupted`。
- 复用原记录，不新增重复行。
- `created_at` 保持不变。
- 清除 `finished_at`。
- 状态改为 `queued`。
- `retry_count` 加 1。

### 7.4 移除的接口

删除：

```text
GET /api/manage/migrations/{batch_id}
POST /api/manage/migrations/{batch_id}/retry
```

不提供：

```text
GET /api/manage/batches
GET /api/manage/batches/{batch_no}
```

## 8. 前端改造

迁移记录页只保留一个列表视图。

删除以下状态和 DOM：

- `migrationDetailView`
- `batchProgress`
- `batchJobsBody`
- `batchJobsPagination`
- `batchJobSearch`
- `batchJobStatusFilter`
- `retryBatchButton`
- `state.batchId`
- `state.batchPage`
- `loadBatch()`
- `renderBatchProgress()`
- `schedulePoll(batch)`
- `retryBatch()`

新增：

```text
state.migrationPage
state.migrationSearch
state.migrationStatus
loadMigrationRecords()
renderMigrationRecords()
renderRecordPagination()
```

迁移记录行直接显示：

```text
漫画 ID
漫画名称
迁移批次号
原路径
迁移路径
迁移状态
消息
创建时间
迁移时间
操作
```

## 9. 数据库升级

新版本使用 schema v4。

升级步骤：

1. 创建一致性备份。
2. 创建 `migration_records` 和索引。
3. 将现有 `migration_jobs` 逐条转换为迁移记录。
4. 从 `migration_batches.destination_*` 填充每条的 `batch_no` 和目标路径快照。
5. 状态映射：

```text
queued                 -> queued
preflight              -> migrating / preflight
copying                -> migrating / copying
stage_verifying        -> migrating / verifying
publishing             -> migrating / publishing
final_verifying        -> migrating / verifying
switching_storage      -> migrating / switching_storage
source_quarantining    -> migrating / cleaning_source
source_verifying       -> migrating / verifying_source
source_deleting        -> migrating / cleaning_source
completed              -> completed
completed_with_warning -> completed_with_warning
failed                 -> failed
blocked_external       -> blocked_external
interrupted            -> interrupted
```

6. 删除 `migration_batches`。
7. 删除 `migration_jobs`。
8. 删除 `migration_events`。
9. 将 schema 版本更新为 3。

转换前必须确认每条旧 job 都能从 batch 获取：

- `batch_no`
- 外盘根路径。
- 外盘存储 ID。

无法完整转换的旧记录不得静默丢弃，应保留在升级日志中并中止升级。

## 10. 验收标准

1. 选择 3 本漫画只生成 3 条漫画迁移记录。
2. 3 条记录共享同一个 `batch_no`。
3. 数据库不存在独立批次主表。
4. 迁移记录页不存在批次详情路由。
5. 页面每行展示迁移 ID、漫画 ID、漫画名称、迁移批次号、原路径、迁移路径、迁移状态、消息、创建时间、迁移时间和操作，操作固定为最后一列。
6. 搜索可以匹配漫画 ID、名称、批次号、原路径和迁移路径。
7. 状态筛选和分页正常。
8. 迁移中的记录可以自动刷新当前行状态。
9. 迁移完成后 `finished_at` 显示为迁移时间。
10. 外盘断开、校验失败和中断均能在对应记录中显示。
11. 失败任务重试时仍只有一条对应漫画记录。
12. 所有相关表仍不使用外键。

## 11. 实施顺序

1. schema v4：把迁移记录 ID 重建为从 `1` 开始的自增整数，并迁移旧数据。
2. 将迁移仓库的创建、抢占、更新和查询改为单表接口。
3. 将迁移 Worker 的状态更新从 `job + batch` 改为单条 record。
4. 简化 `/api/manage/migrations`。
5. 删除迁移记录详情页面和批次轮询逻辑。
6. 将记录页改成单表记录，并展示消息和操作列。
7. 更新 README 和测试。

这次调整的重点是减少对象层级；文件复制、SHA-256 校验、源目录保护和外盘在线/离线浏览规则保持不变。
