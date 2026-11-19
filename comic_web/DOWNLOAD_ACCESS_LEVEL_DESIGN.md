# 下载任务漫画可见性设计

> 文档状态：核心功能已实施  
> schema：v13  
> 适用范围：普通下载、批量下载、漫画访问权限

## 1. 目标

在下载管理中的两个入口增加“漫画可见性”：

1. “建立下载任务”弹窗。
2. “建立批量下载”创建页面。

字段规则：

```text
0：所有用户
1：登录用户
2：管理员
```

默认值：

```text
0
```

用户选择的可见性必须保存到下载任务，并在漫画首次写入 `books` 时设置：

```text
books.access_level
```

## 2. 页面设计

### 2.1 建立普通下载任务

当前字段：

```text
漫画 ID
下载 URL
```

新增：

```text
漫画可见性
```

下拉选项：

```text
0 · 所有用户（默认）
1 · 登录用户
2 · 管理员
```

提交示例：

```json
{
  "comic_id": 31293,
  "download_url": "https://18comic.vip/photo/31293",
  "access_level": 0
}
```

### 2.2 建立批量下载

批量创建页面的预分析表单当前字段：

```text
漫画 ID
漫画名称
系列 URL
```

新增：

```text
漫画可见性
```

提交预分析请求时同时提交：

```json
{
  "parent_id": 18427,
  "comic_name": "海贼王",
  "series_url": "https://www.xiaoaimanhua.com/18427",
  "access_level": 1
}
```

预分析结果缓存中保存该值，后续点击“确定并生成批量任务”时继续沿用。

同一批次的全部章节任务必须使用相同的 `access_level`。

## 3. 数据库设计

schema 升级为 v13。

升级前创建备份：

```text
comic_web/library.db.pre-v13.bak
```

### 3.1 普通下载任务表

```sql
ALTER TABLE download_tasks
ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0;
```

### 3.2 批量下载任务表

```sql
ALTER TABLE batch_download_tasks
ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0;
```

旧任务默认设置为：

```text
access_level = 0
```

保持向后兼容。

### 3.3 books

不新增字段。继续使用已有：

```sql
books.access_level
```

数据库无外键约束保持不变。

## 4. 校验规则

后端必须验证：

```python
access_level in {0, 1, 2}
```

非法值返回：

```http
400 invalid_access_level
```

不允许：

- 空字符串。
- `null`。
- 负数。
- 大于 2。
- 非数字。

可以兼容处理数字字符串：

```text
"0" → 0
"1" → 1
"2" → 2
```

## 5. 普通下载流程

1. 用户建立下载任务并选择可见性。
2. `DownloadRepository.create_task()` 保存 `access_level` 到任务表。
3. 下载 Worker 调用爬虫。
4. 下载完成后扫描并写入 `books`。
5. Worker 根据任务设置：

```python
library.set_access_level(task["comic_id"], task["access_level"])
```

6. 标记任务完成。

即使爬虫生成 `metadata.json`，最终可见性以任务字段为准。

## 6. 批量下载流程

### 6.1 预分析

1. 用户输入漫画信息并选择可见性。
2. 预分析缓存记录 `access_level`。
3. 页面翻页和选择章节时保持相同设置。

### 6.2 创建批次

1. 创建全部 `batch_download_tasks`。
2. 每行写入相同 `access_level`。
3. 建立或刷新父漫画 `books` 记录后设置 `books.access_level`。

### 6.3 章节完成

每章完成后再次同步父漫画：

```python
library.set_access_level(parent_id, task["access_level"])
```

保护措施：

- 防止首次入库扫描时遗漏。
- 防止父漫画后续被重新扫描时丢失。
- 不影响已完成的权限设置。

## 7. 已有漫画处理

普通下载任务在创建前已经会拒绝 comics ID 已存在于 `books` 的情况。

批量下载可能面对已存在的连载漫画：

- 新建批次时，用户选择的可见性作为本批次目标值。
- 创建批次后将父漫画 `books.access_level` 更新为用户选择值。
- 已下载的其他章节不删除。
- 后续任何章节任务都必须保持相同值。

如果用户不希望修改已有漫画权限，应通过后续“访问权限修改”功能调整，而不是建立新批次。

## 8. Indexer 行为

索引器插入新 books 时：

```text
access_level 默认 0
```

更新已有 books 时：

```text
不覆盖 access_level
```

这样可以避免下载完成入库时把管理员设置重置为 0。

首次入库后的权限由 Worker 显式写入。

## 9. API 调整

### 9.1 普通下载

```http
POST /api/manage/downloads
```

新增请求字段：

```json
{
  "access_level": 0
}
```

### 9.2 批量预分析

```http
POST /api/manage/downloads/batch/analyze
```

新增请求字段：

```json
{
  "access_level": 0
}
```

### 9.3 批量确认

```http
POST /api/manage/downloads/batch
```

服务端从预分析缓存读取 `access_level`，前端不能在同一次预分析后修改为另一个值。

### 9.4 任务返回

普通和批量任务列表响应增加：

```json
{
  "access_level": 0
}
```

管理页面可以显示任务目标可见性。

## 10. 前端设计

下拉框统一样式：

```html
<select id="downloadAccessLevel">
  <option value="0">0 · 所有用户</option>
  <option value="1">1 · 登录用户</option>
  <option value="2">2 · 管理员</option>
</select>
```

规则：

- 每次打开普通下载弹窗时重置为 `0`。
- 批量创建页面首次进入时重置为 `0`。
- 预分析后保持用户选择。
- 请求提交前转换为整数。
- 前端校验不替代后端校验。

## 11. 状态和错误

| 错误码 | 场景 |
| --- | --- |
| `invalid_access_level` | 不是 0、1、2 |
| `batch_access_level_conflict` | 同批次出现不同权限值 |
| `book_access_level_update_failed` | 漫画已下载但权限写入失败 |

如果漫画资源下载成功但权限写入失败：

- 任务不能标记完整成功。
- 状态设为失败。
- 保留漫画资源。
- 错误消息说明权限写入失败。
- 重试时重新应用权限，不重新下载图片。

## 12. 测试方案

### 普通下载

- 默认值为 0。
- 选择 0/1/2 后任务表保存正确。
- 非法值拒绝创建。
- 下载完成后 books.access_level 正确。

### 批量下载

- 预分析缓存保存权限值。
- 整批任务权限值一致。
- 父漫画首次入库权限正确。
- 已有父漫画权限按新建批次设置更新。
- 后续章节完成不会重置权限。

### 权限系统联动

- access_level=0：游客、普通用户、管理员可见。
- access_level=1：普通用户和管理员可见。
- access_level=2：仅管理员可见。
- 无权用户无法通过搜索或媒体 URL 访问。

### 回归

- 单本下载、批量下载、断点重试、重新拉取、增量拉取和删除功能不受影响。
- 下载完成直写 `books` 时不会覆盖已有 access_level。

## 13. 实施顺序

1. schema v13，为两类任务表增加 `access_level`。
2. Repository 增加权限值校验和持久化。
3. `ComicLibrary` 增加 `set_access_level()`。
4. 普通下载 Worker 在书籍入库后写入权限。
5. 批量 Repository 和 Worker 写入父漫画权限。
6. API 请求和响应增加 `access_level`。
7. 两个下载创建界面增加下拉框。
8. 增加任务列表可见性展示。
9. 完成三种角色的访问验证和回归测试。
