# 增量拉取功能设计

> 文档状态：核心功能已实施
> 目标 schema：v8
> 适用范围：批量下载、多话连载漫画、增量章节检查

## 1. 功能目标

批量下载代表漫画可能仍在连载。每次建立批量下载任务时，同步维护一条增量拉取任务：

- 记录当前拉取进度。
- 记录上次检查、拉取和执行时间。
- 保存漫画处于连载或完结状态。
- 手动检查来源站是否出现新章节。
- 将新章节转换为新的批量下载任务。
- 批量 Worker 完成章节后同步更新增量进度。

## 2. 页面

下载管理使用三个 Tab：

```text
[普通下载] [批量下载] [增量拉取]
```

增量拉取入口：

```text
/manage/downloads/incremental
```

页面支持：

- 按漫画 ID、名称和错误消息搜索。
- 按连载中、已完结筛选。
- 按空闲、检查中、建立任务、下载中、错误筛选。
- 每页 30 / 50 / 100 条。
- 首页、上一页、页码、下一页和尾页分页。
- 查看当前进度、最新已知章节、待处理任务数。
- 手动增量拉取。
- 标记完结或恢复连载。

## 3. 数据表

新增：

```text
incremental_pull_tasks
```

每本漫画唯一一条记录。

### 3.1 主要字段

| 字段 | 说明 |
| --- | --- |
| `id` | 自增整数主键 |
| `comic_id` | 所属漫画 ID |
| `comic_name` | 漫画名称 |
| `series_url` | 系列目录页 |
| `source_host` | 来源站 |
| `comic_status` | 0 连载中，1 已完结 |
| `current_chapter_key` | 当前完成进度章节键 |
| `current_chapter_title` | 当前完成进度章节名称 |
| `current_chapter_order` | 当前完成进度顺序 |
| `latest_known_chapter_key` | 上次确认时最新章节键 |
| `latest_known_chapter_title` | 上次确认时最新章节名称 |
| `latest_known_chapter_order` | 上次确认时最新章节顺序 |
| `known_chapter_count` | 最近检查时来源章节总数 |
| `pending_task_count` | 当前活动批量章节数 |
| `last_batch_no` | 最近一次增量批次号 |
| `task_state` | idle、checking、creating_tasks、downloading、error |
| `last_checked_at` | 最近检查来源站时间 |
| `last_pull_at` | 最近生成批量任务时间 |
| `last_run_at` | 最近执行或进度同步时间 |
| `last_completed_at` | 最近完成章节时间 |

### 3.2 约束

```sql
CREATE UNIQUE INDEX uq_incremental_pull_comic
ON incremental_pull_tasks(comic_id);
```

所有表继续不使用外键。

## 4. 与批量任务同步

批量任务创建事务中同步 upsert 增量任务：

1. 首次创建批量任务时插入增量任务。
2. 同一漫画后续批次只更新名称、URL、最新章节和最近拉取时间。
3. 用户已标记完结时，不自动恢复连载。
4. 批量事务回滚时增量任务一起回滚。

schema v8 升级时按历史 `batch_download_tasks.parent_id` 回填增量记录。

## 5. 增量检查

点击“增量拉取”后：

1. 校验漫画状态为连载中。
2. 校验当前没有活动批量章节。
3. 重新读取系列目录。
4. 按稳定章节顺序与 `latest_known_chapter_order` 比较。
5. 过滤已完整下载和已有活动任务的章节。
6. 有更新时写入临时批量预分析缓存。
7. 弹窗展示新章节并默认全选。

取消弹窗不会推进最新章节基线。

## 6. 生成批量任务

确认弹窗后复用现有批量任务创建 API：

```http
POST /api/manage/downloads/batch
```

生成新的 `batch_no`，并交给现有单线程批量 Worker。

## 7. Worker 同步

章节完成后：

- 从 `book_chapters` 找到最高完成章节。
- 只有当新章节顺序更高时，才更新 `current_chapter_*`。
- 重新计算 `pending_task_count`。
- 更新 `last_run_at` 和 `last_completed_at`。
- 没有活动任务时恢复 `task_state=idle`。

失败或中断：

- `task_state=error`。
- 保存错误代码和错误消息。
- 不清空下载断点。
- 不修改漫画完结状态。

## 8. API

```http
GET  /api/manage/downloads/incremental
POST /api/manage/downloads/incremental/{id}/preview
POST /api/manage/downloads/incremental/{id}/status
```

创建增量批次继续使用：

```http
POST /api/manage/downloads/batch
```

## 9. 验收标准

- 每个批量漫画自动生成一条增量任务。
- 同一漫画不会重复生成增量记录。
- 增量任务可分页、搜索和筛选。
- 无新章节时弹窗显示“无新的漫画可以拉取。”
- 有新章节时可以生成新的批量下载任务。
- 章节完成后当前进度自动推进。
- 标记完结后不再执行增量检查。
- schema v8 升级前生成 `library.db.pre-v8.bak`。
