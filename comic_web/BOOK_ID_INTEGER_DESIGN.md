# books.id 整数主键设计

> 文档状态：核心功能已实施
> 目标 schema：v12

## 规则

```sql
id INTEGER PRIMARY KEY NOT NULL
```

- 不使用 `AUTOINCREMENT`。
- 创建漫画时必须显式传入 `id`。
- ID 必须是正整数。
- 重复 ID 拒绝创建。
- 触发器 `trg_books_require_id` 拒绝省略或非法 ID。

## 关联字段

以下字段均使用 INTEGER：

```text
book_chapters.book_id
book_categories.book_id
favorites.book_id
user_favorites.book_id
download_tasks.comic_id
batch_download_tasks.parent_id
incremental_pull_tasks.comic_id
deletion_tasks.book_id
migration_records.book_id
```

## 迁移

schema v12 升级前生成：

```text
library.db.pre-v12.bak
```

数字旧 ID 保持不变；历史非数字 ID 通过临时映射表显式分配整数，并保存到 `books.legacy_id`。

## API

漫画 ID 同时接受正整数和数字字符串，输出统一为整数。缺少 ID、0、负数或非数字均被拒绝。
