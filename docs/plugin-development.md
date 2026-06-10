# DataPortal 插件开发指南

## 概述

DataPortal 2.0 提供了完整的插件架构，支持通过标准 Python 包扩展核心功能。插件使用 setuptools entry_points 机制自动发现和加载。

## 插件类型

| 类型 | 基类 | 用途 |
|------|------|------|
| OutputPlugin | `dataportal.plugins.base.OutputPlugin` | 添加输出格式 (CSV, XML 等) |
| HTMLBlockPlugin | `dataportal.plugins.base.HTMLBlockPlugin` | 注入 HTML 区块到现有页面 |
| PagePlugin | `dataportal.plugins.base.PagePlugin` | 注册独立路由和页面 |
| SQLFilterPlugin | `dataportal.plugins.base.SQLFilterPlugin` | SQL 前后 AST 处理 (权限过滤/脱敏) |

## 快速开始

### 1. 项目结构

```
dataportal-plugin-yaml/
├── pyproject.toml
├── src/
│   └── dataportal_plugin_yaml/
│       └── __init__.py
└── tests/
    └── test_yaml_output.py
```

### 2. pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "dataportal-plugin-yaml"
version = "1.0.0"
dependencies = ["pyyaml>=6.0", "dataportal>=2.0.0"]

[project.entry-points."dataportal.plugins"]
format-yaml = "dataportal_plugin_yaml:YAMLOutputPlugin"

[tool.setuptools.packages.find]
where = ["src"]
```

### 3. 插件实现

```python
"""dataportal_plugin_yaml/__init__.py"""
import yaml
from dataportal.plugins.base import OutputPlugin, PluginMeta


class YAMLOutputPlugin(OutputPlugin):
    meta = PluginMeta(
        name="format-yaml",
        version="1.0.0",
        dataportal_version=">=2.0.0",
        python_version=">=3.10",
        author="Your Name",
        description="YAML output format for DataPortal query results",
        conflicts_with=[],
        permissions=[],
    )

    async def initialize(self, ctx) -> None:
        """Called when the plugin is loaded. Access app context here."""
        pass

    async def health_check(self) -> dict:
        """Return health status. Called periodically."""
        return {"status": "healthy", "format": "yaml"}

    async def shutdown(self) -> None:
        """Cleanup resources when plugin is unloaded."""
        pass

    def format_name(self) -> str:
        return "yaml"

    def content_type(self) -> str:
        return "application/x-yaml; charset=utf-8"

    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        data = {
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in rows],
            "metadata": metadata,
        }
        return yaml.dump(data, allow_unicode=True).encode("utf-8")
```

## 插件生命周期

```
DISCOVERED → VALIDATED → LOADED → INITIALIZED → HEALTHY
                                       ↓              ↓
                                    FAILED        UNHEALTHY
                                                      ↓
                                                  UNLOADED
```

1. **DISCOVERED**: 通过 entry_points 扫描发现插件类
2. **VALIDATED**: 版本兼容性和冲突检查通过
3. **LOADED**: 插件类实例化成功
4. **INITIALIZED**: `initialize(ctx)` 调用成功，功能已注册
5. **HEALTHY/UNHEALTHY**: `health_check()` 返回结果
6. **FAILED**: 初始化失败，自动回滚所有注册
7. **UNLOADED**: `shutdown()` 调用完成，功能已注销

### 失败回滚

如果 `initialize()` 抛出异常，PluginManager 会自动：
- 移除已注册的路由
- 移除已注册的输出格式
- 移除已注入的 HTML 区块
- 将插件标记为 FAILED 状态

## 插件元数据 (PluginMeta)

```python
@dataclass
class PluginMeta:
    name: str                          # 唯一标识名
    version: str                       # 语义化版本
    dataportal_version: str = ">=1.0.0"  # 兼容的 DataPortal 版本范围
    python_version: str = ">=3.10"     # 兼容的 Python 版本范围
    conflicts_with: list[str] = []     # 冲突插件名列表
    permissions: list[str] = []        # 请求的权限 (用于沙箱)
    author: str = ""
    description: str = ""
```

## 各类型插件详解

### OutputPlugin - 输出格式

```python
class MyFormatPlugin(OutputPlugin):
    def format_name(self) -> str:
        """返回格式标识符，用于 ?format=xxx 查询参数"""
        return "myformat"

    def content_type(self) -> str:
        """HTTP Content-Type 头"""
        return "application/x-myformat"

    def render(self, columns: list[str], rows: list[list], metadata: dict) -> bytes:
        """将查询结果渲染为字节流"""
        ...
```

使用方式: `GET /sql/execute?database=mydb&sql=SELECT...&format=myformat`

### HTMLBlockPlugin - HTML 区块注入

```python
class MyBlockPlugin(HTMLBlockPlugin):
    def injection_point(self) -> str:
        """注入点标识: index.after_stats, browse.toolbar, sql.before_editor"""
        return "index.after_stats"

    async def render_block(self, request, context: dict) -> str:
        """返回 HTML 片段"""
        return '<div class="my-widget">Custom Content</div>'
```

### PagePlugin - 独立页面

```python
from starlette.routing import Route
from starlette.responses import HTMLResponse

class MyPagePlugin(PagePlugin):
    def get_routes(self) -> list[Route]:
        return [Route("/my-page", self._handler)]

    def nav_items(self) -> list[dict]:
        return [{"label": "My Page", "url": "/my-page"}]

    async def _handler(self, request):
        return HTMLResponse("<h1>My Custom Page</h1>")
```

### SQLFilterPlugin - SQL 过滤器

```python
class MyFilterPlugin(SQLFilterPlugin):
    def pre_process(self, sql: str, context: dict) -> str:
        """在 SQL 执行前修改查询 (如注入 WHERE 子句)
        context 包含: {"database": "db_name", "user": ...}
        """
        # 使用 sqlglot 进行 AST 级别修改
        import sqlglot
        parsed = sqlglot.parse_one(sql, dialect="sqlite")
        # ... modify AST ...
        return parsed.sql(dialect="sqlite")

    def post_process(self, columns: list[str], rows: list[list], context: dict):
        """在结果返回前对数据进行脱敏
        返回 (columns, rows) 元组
        """
        # 遮盖敏感列
        for i, row in enumerate(rows):
            rows[i][email_idx] = mask_email(row[email_idx])
        return columns, rows
```

## 权限沙箱

插件在 Python 级别的权限沙箱中运行。以下模块默认被阻止：

- `subprocess`, `ctypes`, `multiprocessing`
- `signal`, `resource`, `pty`, `fcntl`, `termios`

如需使用受限模块，在 `PluginMeta.permissions` 中声明:

```python
meta = PluginMeta(
    name="my-plugin",
    permissions=["subprocess"],  # 请求 subprocess 权限
    ...
)
```

管理员需在配置中批准:
```json
{
  "plugins": {
    "sandbox_allow": {"my-plugin": ["subprocess"]}
  }
}
```

## Ed25519 签名

### 生成密钥对

```bash
dataportal plugin keygen
```

### 签名插件包

```python
from dataportal.plugins.signing import sign_package

signature = sign_package("dist/my_plugin-1.0.0.tar.gz", private_key_b64)
# 将 signature 保存为 .sig 文件
```

### 配置信任密钥

```json
{
  "plugins": {
    "require_signatures": true,
    "trusted_keys": ["base64-encoded-public-key"]
  }
}
```

## 测试

### 使用 pytest 测试插件

```python
import pytest
from dataportal.plugins.base import PluginMeta


class TestMyPlugin:
    @pytest.fixture
    def plugin(self):
        from my_plugin import MyPlugin
        return MyPlugin()

    def test_meta(self, plugin):
        assert plugin.meta.name == "my-plugin"
        assert plugin.meta.version == "1.0.0"

    def test_render(self, plugin):
        data = plugin.render(["id", "name"], [[1, "test"]], {})
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_lifecycle(self, plugin):
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.config.get.return_value = {}

        await plugin.initialize(ctx)
        health = await plugin.health_check()
        assert health["status"] == "healthy"
        await plugin.shutdown()
```

### 集成测试

```python
from starlette.testclient import TestClient

def test_format_in_api(client):
    resp = client.get("/sql/execute.json?database=test&sql=SELECT 1&format=yaml")
    assert resp.status_code == 200
    assert "application/x-yaml" in resp.headers["content-type"]
```

## 发布流程

1. **开发**: 实现插件，编写测试
2. **构建**: `python -m build`
3. **签名** (可选):
   ```bash
   dataportal plugin keygen  # 一次性生成
   python -c "from dataportal.plugins.signing import sign_package; print(sign_package('dist/xxx.tar.gz', 'PRIVATE_KEY'))"
   ```
4. **发布**: `twine upload dist/*`
5. **安装**: `dataportal plugin install dataportal-plugin-yaml`
6. **验证**: `dataportal plugin list`

## CLI 管理命令

```bash
dataportal plugin list              # 列出所有插件
dataportal plugin install <package> # 安装插件
dataportal plugin uninstall <name>  # 卸载插件
dataportal plugin enable <name>     # 启用插件
dataportal plugin disable <name>    # 禁用插件 (不卸载)
dataportal plugin health            # 健康检查
dataportal plugin info <name>       # 详细信息
dataportal plugin keygen            # 生成签名密钥对
```

## 配置参考

```json
{
  "plugins": {
    "enabled": true,
    "require_signatures": false,
    "trusted_keys": [],
    "auto_discover": true
  },
  "sql_filter": {
    "row_filters": [
      {"table": "users", "condition": "department = 'public'"}
    ],
    "column_masks": [
      {"table": "users", "column": "email", "strategy": "partial"},
      {"table": "users", "column": "phone", "strategy": "partial"},
      {"table": "orders", "column": "credit_card", "strategy": "full"}
    ]
  }
}
```

### 脱敏策略

| 策略 | 效果 | 示例 |
|------|------|------|
| `full` | 完全遮盖 | `***` |
| `partial` | 部分保留 | `us***@example.com`, `***1234` |
| `hash` | SHA-256 前16位 | `a3f2b8c9d1e4f5a6` |
| `null` | 置空 | `` |
