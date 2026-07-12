# langchain-chat

基于 LangChain 的多轮会话教学项目。

## 当前状态

本仓库已经具备用户、会话、预设、配置与存储层的基础实现，并开始补齐多环境配置隔离能力。

## 开发环境

建议使用 `uv` 管理环境与依赖：

```bash
uv venv
uv sync --extra dev
```

## 运行测试

```bash
uv run pytest
```

## 运行自检脚本

```bash
uv run python scripts/full_self_test.py
```

## 环境切换

通过 `APP_ENV` 切换运行环境，支持 `dev`、`test`、`prod`。每个环境只读取对应的 `.env.<env>` 与 `config/config.<env>.yaml`，避免密钥和数据库串串用。

```powershell
$env:APP_ENV = "dev"
uv run python scripts/full_self_test.py
```

```powershell
$env:APP_ENV = "test"
uv run pytest
```

```powershell
$env:APP_ENV = "prod"
uv run python scripts/full_self_test.py
```

## 配置约定

- `config/config.yaml` 提供基础配置
- `config/config.dev.yaml`、`config/config.test.yaml`、`config/config.prod.yaml` 提供环境覆盖
- `.env.dev`、`.env.test`、`.env.prod` 提供环境专属敏感信息
- 进程环境变量仅允许当前环境的 `APP_ENV` / `API_KEY` / `DATABASE_URL` 覆盖
- `dev` 默认使用 SQLite 和开发用便宜模型
- `test` 使用独立测试数据库（默认内存 SQLite）
- `prod` 使用 MySQL 和正式模型配置

## 已知后续工作

- 补齐 LangChain 真正的流式模型适配
- 完成 TUI 交互菜单与流式渲染
- 为搜索、导出、日志和多模型切换补齐业务实现
