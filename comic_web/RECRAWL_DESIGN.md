# 已完成任务重新拉取设计

> 文档状态：核心功能已实施
> 目标 schema：v10

## 1. 功能

普通下载和批量下载中，状态为 `completed` 的任务提供“重新拉取”操作。

重新拉取会：

1. 复用原任务 ID。
2. 将状态改为 `pending`。
3. 设置 `pull_mode=recrawl`。
4. 重新打开来源页并下载全部图片。
5. 成功后将新目录整体替换旧目录。
6. 不保留旧 manifest、旧图片或旧页面顺序。

## 2. 数据表

`download_tasks` 和 `batch_download_tasks` 新增：

```text
pull_mode TEXT DEFAULT 'initial'
recrawl_count INTEGER DEFAULT 0
recrawl_requested_at TEXT
```

`pull_mode`：

```text
initial
recrawl
```

## 3. API

```http
POST /api/manage/downloads/{task_id}/recrawl
POST /api/manage/downloads/batch/tasks/{task_id}/recrawl
```

## 4. Worker

重新拉取使用新临时目录：

```text
.recrawl-task-<id>-<timestamp>
```

下载命令使用 `--force`，不使用 `--resume`。

全部图片成功后：

1. 旧目录改名为备份目录。
2. 临时目录改名为正式目录。
3. 删除旧目录。
4. 刷新索引和增量进度。

失败或中断时：

- 删除临时目录。
- 保留原正式目录。
- 任务标记失败或中断。
- 再次重试继续使用 `recrawl` 模式。

## 5. 页面

已完成任务操作列显示“重新拉取”。

任务状态中额外显示：

```text
重新拉取 #1
```

## 6. 限制

- 只有 `completed` 状态允许重新拉取。
- 迁移或删除活动期间拒绝重新拉取。
- 重新拉取期间删除功能会检测到活动下载任务。
