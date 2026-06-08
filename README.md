# DataPortal - 生产级数据门户

一个命令启动完整的交互式数据探索网站，支持 SQLite 数据库和 CSV 文件。

## 快速启动

```bash
# 1. 安装
pip install -e .

# 2. 生成测试数据
python tests/create_test_data.py

# 3. 一个命令启动
dataportal serve tests/test_data/sample.sqlite tests/test_data/products.csv tests/test_data/sales_gbk.csv

# 打开浏览器: http://localhost:8001
```

## 功能特性

### 数据源支持
- **SQLite 数据库**: 直接加载 `.sqlite`/`.db` 文件
- **CSV 文件**: 异步增量导入，自动推断类型，处理多种编码（UTF-8/GBK等）
- **目录**: 指定目录自动扫描所有 SQLite 和 CSV 文件

### 首页仪表板
- 列出所有数据库、表、视图
- 显示行数、列数、索引统计
- 系统健康状态实时监控
- CSV 导入进度追踪

### 数据浏览
- 翻页浏览（可自定义每页行数）
- 多列排序（`sort=col1,-col2`）
- 全文搜索和列级过滤
- 保存视图（收藏常用查询参数）
- 表结构/索引信息展示

### SQL 编辑器
- 沙箱执行（禁止写入/DDL/危险操作）
- 查询超时自动取消（默认30秒）
- 错误智能解释
- 查询历史记录
- 结果导出 CSV
- 快捷键 Ctrl+Enter 执行

### JSON API
所有页面加 `.json` 后缀即可获取完整 JSON 响应：
```
GET /                    → 首页 HTML
GET /.json               → 首页完整元数据
GET /db/{db}/table/{t}   → 表浏览 HTML
GET /db/{db}/table/{t}.json → 表数据+分页+列类型+索引+血缘
GET /sql/{db}.json       → SQL编辑器上下文
GET /config.json         → 当前配置
GET /health              → 健康检查
```

### 配置热加载
通过 JSON 文件配置，修改后自动生效（无需重启）：
```bash
dataportal serve --config config.json tests/test_data/
```

可配置项：
| 配置项 | 说明 |
|--------|------|
| site.title | 网站标题 |
| site.copyright | 页脚版权 |
| theme.primary_color | 主题色 |
| theme.dark_mode | 暗色模式 |
| permissions.allow_sql_write | 允许写操作 |
| permissions.max_query_time_seconds | 查询超时 |
| import.chunk_size | CSV导入批次大小 |
| import.encoding_detection | 自动检测编码 |
| cache.ttl_seconds | 缓存过期时间 |

## 命令行

```bash
# 基本用法
dataportal serve <sources...> [--port 8001] [--host 0.0.0.0] [--config config.json]

# 示例
dataportal serve data.sqlite                     # 单个 SQLite
dataportal serve *.csv                           # 多个 CSV
dataportal serve ./data_dir/                     # 整个目录
dataportal serve a.sqlite b.csv ./dir/ -p 9000   # 混合源+自定义端口
```

## 运行测试

```bash
pip install -e ".[dev]"
python tests/create_test_data.py
pytest tests/ -v
```

## 性能说明

### CSV 导入性能
- **增量导入**: 通过 MD5 哈希避免重复导入
- **分块处理**: 默认每 5000 行提交一次，避免内存溢出
- **WAL 模式**: SQLite 使用 WAL 日志支持并发读取
- **异步 I/O**: 使用 aiosqlite 进行非阻塞数据库操作

### 查询性能
- **自动索引**: CSV 导入时自动为整数列和首列创建索引
- **LRU 缓存**: 相同查询参数的结果缓存（默认60秒TTL）
- **连接复用**: 数据库连接池化，避免重复打开
- **busy_timeout**: 设置5秒等待锁超时，避免并发死锁

### 并发支持
- **ASGI + uvicorn**: 完全异步处理请求
- **WAL 模式**: 支持并发读取不阻塞
- **连接级锁**: 确保连接操作原子性

### 建议生产部署
```bash
# 多 worker 部署
uvicorn dataportal.app:app --host 0.0.0.0 --port 8001 --workers 4

# 配合 nginx 反向代理
# 大文件 CSV 建议先用脚本导入，再指向生成的 .sqlite 文件
```

## 项目结构

```
src/dataportal/
├── __init__.py       # 包入口
├── __main__.py       # python -m dataportal
├── cli.py            # Click CLI (dataportal serve)
├── app.py            # Starlette 主应用+路由
├── config.py         # 配置管理+热加载
├── database.py       # 数据库注册+连接+查询
├── importer.py       # CSV 异步增量导入+类型推断
├── sandbox.py        # SQL 沙箱+权限+错误解释
├── cache.py          # LRU TTL 缓存
├── templates/        # Jinja2 HTML 模板
└── static/           # CSS 样式
```

## 许可证

MIT
