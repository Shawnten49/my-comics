# 本地漫画库

一个运行在本机或私有局域网中的漫画浏览与管理系统。漫画元数据、章节、分类、收藏、下载任务、迁移记录和访问权限统一保存在 SQLite 中，漫画图片保存在本地目录或已登记的外盘目录。

项目不再依赖启动扫描目录。`books` 表是业务数据核心，首页、搜索、分类、收藏、阅读器和管理页面都直接读取数据库。

GitHub 仓库：[Shawnten49/my-comics](https://github.com/Shawnten49/my-comics)

## 主要功能

- 首页漫画缩略图、名称、页数、完成状态和分页浏览
- 按漫画名称、漫画 ID 和分类搜索
- 游客、普通用户、管理员三种角色
- 按漫画设置访问级别：所有用户、登录用户、管理员
- 用户收藏及独立收藏列表
- 多话漫画详情页和章节分页
- 纵向阅读器，滚动时分页加载图片
- 普通下载、批量章节下载和增量拉取
- 下载任务断点续传、失败重试和重新拉取
- 漫画迁移到外盘，并支持迁移记录与失败重试
- 单本漫画、系列漫画和指定章节删除
- 删除任务记录和失败重试
- 管理员用户管理
- 数据库系统设置
- 局域网 HTTPS 部署

## 技术架构

```text
浏览器
  ↓
Python HTTP 服务
  ↓
SQLite
  ├── books
  ├── book_chapters
  ├── categories
  ├── favorites / user_favorites
  ├── download_tasks
  ├── batch_download_tasks
  ├── incremental_pull_tasks
  ├── migration_records
  └── deletion_tasks
  ↓
本地或外盘图片文件
```

核心原则：

- SQLite 是业务数据唯一来源。
- 文件系统只负责保存漫画图片和资源。
- 网站启动时不扫描漫画目录。
- 网站启动时不预热或清理缩略图。
- 下载完成后直接写入 `books`、`book_chapters`、分类关系和搜索索引。
- 手工复制目录不会自动进入漫画库，需要显式执行修复工具。

## 环境要求

- Python 3.9+
- Chrome
- Pillow
- Playwright
- 本机运行 macOS 时支持 `caffeinate` 下载防休眠

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

如果 Playwright 下载较慢，可以使用国内镜像：

```bash
python3 -m pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --timeout 120 \
  --retries 10
```

项目使用系统 Chrome，不需要执行：

```bash
playwright install chromium
```

## 启动网站

```bash
cd /path/to/comic
./start_site.sh
```

默认地址：

```text
http://127.0.0.1:8767/
```

也可以在前台启动：

```bash
python3 comic_web/app.py
```

停止：

```bash
./stop_site.sh
```

运行日志：

```text
run/site.log
```

## 首次初始化

空数据库首次启动时自动创建管理员：

```text
用户名：admin
密码：admin123
```

首次登录会强制进入：

```text
/change-password
```

修改默认密码前不能使用管理功能。

空数据库首次启动时的迁移/删除校验码：

```text
123456
```

校验码在数据库中只保存 PBKDF2 哈希。登录后可在“系统设置”中修改。

可通过环境变量覆盖初始化值：

```bash
export COMIC_BOOTSTRAP_ADMIN_USERNAME=admin
export COMIC_BOOTSTRAP_ADMIN_PASSWORD=admin123
export COMIC_BOOTSTRAP_VERIFICATION_CODE=123456
```

已有用户、已有管理员或已有校验码不会在启动时被覆盖。

如果不希望使用默认值，可在首次启动前设置环境变量。

## 登录和权限

登录页面：

```text
http://127.0.0.1:8767/login
```

角色权限：

| 角色 | 权限 |
|---|---|
| 游客 | 浏览和搜索 access_level=0 的漫画 |
| 普通用户 | 浏览 access_level=0/1 的漫画，可收藏 |
| 管理员 | 浏览全部漫画，可使用所有管理功能 |

管理功能包括：

- 漫画管理
- 下载管理
- 批量下载
- 增量拉取
- 删除任务
- 迁移记录
- 用户管理
- 系统设置

## 系统设置

管理员入口：

```text
/manage/settings
```

当前配置项：

| 配置键 | 说明 |
|---|---|
| `download.root` | 漫画下载根目录 |
| `download.cdp_url` | Chrome DevTools Protocol 地址 |
| `security.verification_code` | 迁移和删除操作校验码 |

`download.root` 修改前会检查：

- 是否配置
- 是否为绝对路径
- 是否存在
- 是否为目录
- 是否具有读写权限
- 是否能够真实写入临时探测文件

路径修改后需要重启网站生效。

迁移和删除校验码只保存哈希，不会通过接口返回明文。

## 下载功能

下载任务使用 Chrome DevTools Protocol 控制浏览器。

网站启动时以及每次下载任务执行前都会检测 CDP 端口。如果本机
`http://127.0.0.1:9222` 未运行，会自动启动：

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.comic-chrome-profile"
```

专用 Chrome Profile 可通过环境变量覆盖：

```bash
export COMIC_CHROME_PROFILE_DIR="$HOME/.comic-chrome-profile"
```

Chrome 启动后，脚本会等待 `/json/version` 就绪，再继续连接 CDP。
站点需要登录或 Cloudflare 验证时，脚本仍会等待页面中的验证流程完成。

默认 CDP 地址：

```text
http://127.0.0.1:9222
```

可在“系统设置”中修改。

### 普通下载

入口：

```text
/manage/downloads
```

创建任务前会检查：

- `download.root` 是否可用
- 漫画 ID 是否已存在于 `books`
- 是否已有同漫画的活动下载任务
- 下载 URL 和漫画 ID 是否匹配

下载成功后直接更新：

- `books`
- 分类关系
- 全文搜索索引
- 缩略图缓存
- 下载任务状态

### 批量下载

支持连载漫画预分析、章节分页选择、逐章下载和单章重试。

初始批量下载要求漫画 ID 尚不存在。追加新章节应使用“增量拉取”。

每章下载完成后直接写入或更新 `book_chapters`，并重新计算父漫画的：

- 总页数
- 章节数
- 已完成章节数
- 完成状态
- 封面和更新时间

### 增量拉取

入口：

```text
/manage/downloads/incremental
```

用于检查连载漫画是否有新章节，并生成新的批量下载任务。

### 防休眠

下载期间默认启动 macOS `caffeinate`，阻止系统空闲休眠和磁盘休眠。

可选参数：

```text
--keep-awake
--no-keep-awake
--keep-display-awake
```

最后一个下载任务结束后会自动释放防休眠。

## 漫画管理

入口：

```text
/manage/books
```

支持：

- 分页
- 名称和漫画 ID 搜索
- 本地/外盘筛选
- 可用性筛选
- 访问级别修改
- 封面管理
- 删除漫画
- 删除指定章节

## 收藏

登录用户可在阅读器添加或取消收藏。

入口：

```text
/favorites
```

收藏记录按用户隔离。

## 分类

分类来源包括原始标题中的 `[]` 和 `()` 内容。

分类支持：

- 按漫画数量排列
- 查看分类漫画
- 屏蔽分类入口
- 恢复屏蔽分类

屏蔽分类只影响分类筛选入口，不会隐藏同时属于其他分类的漫画。

## 多话漫画

多话漫画使用两张核心表：

- `books`：漫画主体
- `book_chapters`：卷、话和章节

点击漫画进入详情页，选择章节后进入阅读器。

## 迁移到外盘

迁移入口：

```text
/manage/migrations
```

迁移流程：

1. 检查目标外盘。
2. 复制漫画资源。
3. 核对文件数量、大小和 SHA-256。
4. 原子切换到外盘目录。
5. 更新 `books.storage_id` 和 `books.directory`。
6. 删除本地源目录。

外盘在线时漫画可以继续浏览；外盘断开时会显示明确提示。

## 数据目录

默认数据库：

```text
comic_web/library.db
```

数据库升级备份：

```text
comic_web/library.db.pre-v19.bak
```

下载目录默认由 `download.root` 决定，通常为：

```text
download/
```

运行文件：

```text
run/
├── site.log
├── site.pid
├── site.url
├── site.local.url
└── tls/
```

以下内容不会提交到 Git：

- SQLite 数据库及备份
- 漫画图片
- 下载日志
- 下载结果
- TLS 私钥
- Cookie 和浏览器 Profile
- 临时文件

## 数据库优先和手工修复

网站不会在启动时扫描目录。

手工复制或移动漫画目录后，数据库不会自动发现。需要明确执行：

```bash
python3 repair_import.py
```

该脚本会扫描目录并修复数据库，只在人工执行时运行。

## 局域网部署

使用自动检测的局域网地址和自签名 HTTPS：

```bash
./start_site.sh --lan
```

指定地址：

```bash
./start_site.sh --lan --lan-address 192.168.0.107
```

局域网模式默认：

- 只允许私有网段访问
- 启用 HTTPS
- 校验 Host
- 不暴露到公网

自签名证书需要浏览器首次确认信任。

不建议使用：

```bash
./start_site.sh --lan --lan-allow-http
```

HTTP 会暴露密码和会话 Cookie。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖：

- 登录、角色和访问级别
- 收藏隔离
- 普通下载和批量下载
- 下载目录检测
- 管理员初始化
- 首次改密
- 章节汇总
- 删除任务
- 外盘迁移
- 数据库 migrations
- 缩略图懒加载
- 下载防休眠

## 辅助脚本

网站是主项目，以下脚本作为辅助工具存在：

- `image_crawler.py`：单页图片抓取
- `batch_download.py`：命令行批量网页下载
- `repair_import.py`：显式扫描目录并修复数据库
- `convert_rendered_webp.py`：转换 rendered PNG
- `delete_rendered_png.py`：清理 rendered PNG
- `import_series.py`：导入多话漫画目录
- `backfill_comic_titles.py`：回填旧漫画标题

脚本参数可使用：

```bash
python3 image_crawler.py --help
python3 batch_download.py --help
```

## 安全建议

- 首次登录后立即修改 `admin123`
- 局域网或多人使用前修改校验码 `123456`
- 不要提交数据库、日志、Cookie、TLS 私钥和浏览器 Profile
- 不要把服务直接暴露到公网
- 优先使用私有 GitHub 仓库
- 迁移和删除前保留数据库及漫画目录备份
