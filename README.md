# CCOS

CCOS 是一个 Python agentic coding CLI。

**核心特点：**

- **多模型支持** — Anthropic Claude、OpenAI GPT、Grok、Ollama 等任意 LLM
- **完整 Agentic Loop** — 流式响应 → 工具调用 → 执行 → 反馈循环，最多 50 轮
- **19 个内置工具** — 文件读写、Bash 执行、代码搜索、Web 抓取等
- **MCP 协议支持** — stdio/SSE/HTTP/WebSocket 四种传输，连接外部工具服务器
- **自动记忆系统** — 跨会话持久化用户偏好、项目上下文、反馈记录

## 快速开始

### 环境要求

- Python 3.11+
- Windows / macOS / Linux
- 至少一个 LLM 提供商的 API Key

### 安装

```bash
# 克隆仓库
git clone <repo-url>
cd ccos

# 创建虚拟环境
python -m venv venv
source venv/bin/activate      # Linux/Mac
# 或: venv\Scripts\activate   # Windows

# 安装依赖
pip install -e .

# Windows 用户建议设置编码
set PYTHONIOENCODING=utf-8
```

### 配置 API Key

方式一：环境变量（推荐）

```bash
export ANTHROPIC_API_KEY="sk-ant-..."    # Claude
export OPENAI_API_KEY="sk-..."           # OpenAI
export XAI_API_KEY="xai-..."             # Grok
```

方式二：交互式登录

```bash
ccos
> /login
```

方式三：配置文件 `~/.ccos/config.json`

```json
{
  "default_provider": "anthropic",
  "default_model": "claude-sonnet-4-6",
  "providers": {
    "anthropic": { "api_key_env": "ANTHROPIC_API_KEY" },
    "openai": { "api_key_env": "OPENAI_API_KEY", "default_model": "gpt-4o" },
    "ollama": { "base_url": "http://localhost:11434/v1", "default_model": "llama3.1" },
    "grok": { "type": "openai_compat", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY", "default_model": "grok-3" }
  }
}
```

### 运行

```bash
# 交互模式（REPL）
python run.py

# 或通过安装的入口点
ccos

# 单次查询
ccos "explain this codebase"

# 指定模型和提供商
ccos -p openai -m gpt-4o "hello"
ccos -p ollama -m llama3.1 "hello"

# 恢复上次会话
ccos --resume <session-id>
```

---

## CLI 选项

```
Usage: ccos [OPTIONS] [PROMPT]...

Options:
  -m, --model TEXT               模型名称 (e.g. claude-sonnet-4-6, gpt-4o)
  -p, --provider TEXT            提供商 (anthropic, openai, ollama, grok)
  --cwd TEXT                     工作目录（默认当前目录）
  --dangerously-skip-permissions 跳过所有权限检查
  -r, --resume TEXT              恢复之前的会话 ID
  -v, --version                  显示版本
  -h, --help                     显示帮助
```

---

## 斜杠命令

交互模式下支持 39 个斜杠命令：

### 基础

| 命令         | 说明       |
| ---------- | -------- |
| `/help`    | 显示所有可用命令 |
| `/exit`    | 退出       |
| `/clear`   | 清除对话历史   |
| `/status`  | 显示当前会话状态 |
| `/compact` | 压缩对话上下文  |

### 模型 & 提供商

| 命令                 | 说明                   |
| ------------------ | -------------------- |
| `/model [name]`    | 切换/显示当前模型            |
| `/provider [name]` | 切换/显示提供商（动态查询可用模型列表） |
| `/cost`            | 显示 token 用量和费用       |

### 会话管理

| 命令             | 说明             |
| -------------- | -------------- |
| `/history`     | 列出最近会话         |
| `/resume <id>` | 恢复之前的会话        |
| `/session`     | 显示当前会话信息       |
| `/export`      | 导出对话为 Markdown |
| `/rewind [n]`  | 回退最近 n 轮对话     |

### 记忆系统

| 命令                      | 说明      |
| ----------------------- | ------- |
| `/memory`               | 列出所有记忆  |
| `/memory add <name>`    | 交互式创建记忆 |
| `/memory show <name>`   | 查看记忆详情  |
| `/memory edit <name>`   | 编辑记忆    |
| `/memory delete <name>` | 删除记忆    |

### MCP 服务器

| 命令                             | 说明              |
| ------------------------------ | --------------- |
| `/mcp`                         | 显示所有 MCP 服务器状态  |
| `/mcp add [name]`              | 交互式添加服务器        |
| `/mcp remove <name>`           | 删除服务器           |
| `/mcp reconnect <name>`        | 重连服务器           |
| `/mcp enable/disable <name>`   | 启用/禁用服务器        |
| `/mcp test <name>`             | 交互式测试服务器工具      |
| `/mcp tools/resources/prompts` | 列出 MCP 工具/资源/提示 |

### 开发辅助

| 命令                   | 说明           |
| -------------------- | ------------ |
| `/diff`              | 显示 git diff  |
| `/branch`            | 显示当前 git 分支  |
| `/review`            | 审查所有未提交改动    |
| `/plan`              | 显示计划模式状态     |
| `/doctor`            | 检查系统配置和依赖    |
| `/login` / `/logout` | 管理 API 凭证    |
| `/mode [mode]`       | 切换权限模式       |
| `/hooks`             | 显示已安装的 hooks |

---

## 项目结构

```
ccos/
├── run.py                    # 入口脚本
├── pyproject.toml               # 项目配置 & 依赖
│
├── ccos/
    ├── __init__.py              # 版本号 (0.1.0)
    ├── main.py                  # CLI 入口 (Click)
    ├── app.py                   # 主应用类 — 组装所有组件
    ├── config.py                # 配置管理 (~/.ccos/config.json)
    ├── auth.py                  # API Key 凭证管理
    ├── hooks.py                 # Hook 系统 (PreToolUse/PostToolUse)
    ├── plan.py                  # 计划模式 (word-slug 文件命名)
    │
    ├── providers/               # LLM 提供商抽象层
    │   ├── base.py              #   LLMProvider 协议 + 通用类型
    │   ├── registry.py          #   ProviderRegistry 工厂
    │   ├── anthropic.py         #   Claude API (直连/Bedrock/Vertex)
    │   ├── openai.py            #   OpenAI API
    │   ├── openai_compat.py     #   通用 OpenAI 兼容接口
    │   └── ollama.py            #   本地 Ollama
    │
    ├── engine/                  # 核心 Agentic Loop
    │   ├── query_engine.py      #   LLM 调用 → 工具执行 → 循环
    │   ├── message_manager.py   #   对话历史管理
    │   ├── tool_executor.py     #   工具调用 + 权限检查
    │   └── cost_tracker.py      #   API 费用追踪
    │
    ├── tools/                   # 19 个内置工具
    │   ├── base.py              #   Tool 协议 + ToolRegistry
    │   ├── bash.py              #   Shell 执行 (流式输出)
    │   ├── file_read.py         #   读取文件
    │   ├── file_write.py        #   写入文件
    │   ├── file_edit.py         #   精确编辑 (old_string → new_string)
    │   ├── glob_tool.py         #   文件模式匹配
    │   ├── grep_tool.py         #   正则搜索
    │   ├── web_fetch.py         #   抓取网页内容
    │   ├── web_search.py        #   Web 搜索
    │   ├── agent.py             #   子 Agent 生成
    │   ├── plan_mode.py         #   计划模式切换
    │   ├── todo.py              #   任务列表
    │   ├── tool_search.py       #   工具发现
    │   ├── ask_user.py          #   交互式用户提问
    │   ├── task_tools.py        #   后台任务调度
    │   ├── notebook_edit.py     #   Jupyter Notebook 编辑
    │   └── powershell.py        #   PowerShell (Windows)
    │
    ├── mcp/                     # MCP 协议集成
    │   ├── types.py             #   类型定义 (TransportType, ConnectionState)
    │   ├── transport.py         #   传输层 (Stdio/SSE/HTTP/WebSocket)
    │   ├── client.py            #   MCP 客户端 + 连接管理
    │   └── tools.py             #   MCP 工具注册到 ToolRegistry
    │
    ├── memory/                  # 自动记忆系统
    │   ├── types.py             #   记忆类型 (user/feedback/project/reference)
    │   ├── store.py             #   文件系统存储 + YAML frontmatter
    │   ├── extractor.py         #   后台记忆提取 Agent
    │   └── recall.py            #   智能记忆召回
    │
    ├── prompt/                  # 系统提示词构建
    │   ├── builder.py           #   PromptBuilder 组装器
    │   ├── sections.py          #   10+ 提示词段落 (CC 完全翻译)
    │   ├── memory.py            #   记忆索引加载
    │   └── context.py           #   环境信息注入
    │
    ├── commands/                # 斜杠命令
    │   ├── registry.py          #   命令注册表
    │   └── builtin.py           #   39 个内置命令
    │
    ├── permissions/             # 权限系统
    │   ├── manager.py           #   权限决策 (default/auto/trust_all/plan/read_only)
    │   └── prompts.py           #   用户授权提示
    │
    ├── history/                 # 会话持久化
    │   └── session.py           #   JSONL 会话记录
    │
    ├── ui/                      # 终端 UI
    │   ├── renderer.py          #   Rich 渲染 (Clawd ASCII art + 流式输出)
    │   ├── input.py             #   prompt_toolkit 交互输入
    │   ├── status.py            #   状态栏
    │   ├── themes.py            #   CC 精确配色方案 (dark/light)
    │   └── figures.py           #   Unicode 符号
    │
    └── utils/                   # 工具函数
        ├── paths.py             #   路径处理
        └── platform_info.py     #   平台检测
 

```

---

## 架构设计

### 核心流程

```
用户输入 → PromptBuilder (系统提示 + 记忆 + 工具 schema)
         → LLMProvider.stream() (流式调用 LLM)
         → StreamChunk (实时渲染文本/思考/工具调用)
         → ToolExecutor (权限检查 → Hook → 执行)
         → ToolResult 注入对话 → 下一轮 LLM 调用
         → 直到 stop_reason == "end_turn" (最多 50 轮)
```

### Provider 抽象层

所有 LLM 统一到一套接口，通过 `LLMProvider` 协议：

```python
class LLMProvider(ABC):
    @property
    def name(self) -> str: ...
    async def stream(self, *, messages, system, tools, model, ...) -> AsyncIterator[StreamChunk]: ...
    async def list_models(self) -> list[str]: ...
```

内部使用统一的 `Message`, `ContentBlock`, `ToolCallContent`, `ToolResultContent` 类型，无论底层是 Anthropic 还是 OpenAI API。

### 工具系统

每个工具实现 `Tool` 协议：

```python
class Tool(ABC):
    name: str
    description: str
    input_schema: dict
    async def execute(self, params, ctx) -> ToolOutput: ...
```

工具通过 `ToolRegistry` 注册，MCP 外部工具也统一注册到同一个 registry。

### MCP 架构

```
MCPManager (管理多个连接)
  └── MCPConnection (单个服务器)
        └── MCPTransport (传输层抽象)
              ├── StdioTransport   — 子进程 stdin/stdout
              ├── SSETransport     — HTTP GET 长连接 + POST
              ├── HTTPTransport    — Streamable HTTP
              └── WebSocketTransport — 全双工 WS
```

### 记忆系统

```
~/.ccos/projects/<sha256[:16]>/memory/
  ├── MEMORY.md              # 索引 (常驻注入 system prompt)
  ├── user_role.md           # type: user
  ├── feedback_testing.md    # type: feedback (含 Why + How to apply)
  └── project_deadline.md    # type: project
```

四种类型：`user` (用户偏好)、`feedback` (工作反馈)、`project` (项目上下文)、`reference` (外部引用)。

---

## MCP 服务器配置

### Stdio (本地子进程)

```json
{
  "mcp_servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    }
  }
}
```

### SSE (远程 HTTP)

```json
{
  "mcp_servers": {
    "remote-api": {
      "type": "sse",
      "url": "https://mcp.example.com/sse",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

### HTTP Streamable

```json
{
  "mcp_servers": {
    "streaming-api": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {}
    }
  }
}
```

### WebSocket

```json
{
  "mcp_servers": {
    "ws-server": {
      "type": "ws",
      "url": "wss://mcp.example.com/ws"
    }
  }
}
```

也可以通过 `/mcp add` 命令交互式配置。

---

## 权限模式

| 模式          | 说明                                     |
| ----------- | -------------------------------------- |
| `default`   | 每次工具调用都询问用户                            |
| `auto`      | 自动批准大部分操作，只对危险命令 (git push 等) 和目录外写入询问 |
| `trust_all` | 全部自动批准 (谨慎使用)                          |
| `plan`      | 只读模式 + 只能写计划文件                         |
| `read_only` | 只允许读操作                                 |

切换方式：`/mode auto` 或 `--dangerously-skip-permissions`

---

## 数据存储

| 路径                                | 内容          |
| --------------------------------- | ----------- |
| `~/.ccos/config.json`             | 全局配置        |
| `~/.ccos/credentials.json`        | API Key 凭证  |
| `~/.ccos/sessions/<project>/`     | 会话 JSONL 记录 |
| `~/.ccos/projects/<hash>/memory/` | 项目记忆文件      |
| `./CCOS.md`                       | 项目级指令文件     |

---

## 继续开发 — 待实现功能

### 高优先级

| 功能                  | 说明                                                                             |
| ------------------- | ------------------------------------------------------------------------------ |
| **OAuth 2.0 (MCP)** | MCP 服务器的 OAuth 认证流程                                                            |
| **剩余工具**            | CC 有 ~40 个工具，目前实现了 19 个。缺少：MultiTool, TodoRead, WebScreenshot, ListDirectory 等 |
| **剩余斜杠命令**          | CC 有 ~80 个命令，目前 39 个。缺少：/approved-tools, /listen, /ide, /migrate 等             |
| **Git Worktree**    | Agent 隔离执行环境 — 在 worktree 中运行子 agent                                           |
| **图片支持**            | 截图读取、图片内容分析、拖拽图片输入                                                             |
| **上下文自动压缩**         | token 接近限制时自动触发 compact                                                        |

### 中优先级

| 功能                  | 说明                                |
| ------------------- | --------------------------------- |
| **Skill/Plugin 系统** | 用户自定义技能，如 `/commit`, `/review-pr` |
| **文件监听**            | 检测外部文件变更并通知                       |
| **IDE 集成**          | VS Code / JetBrains 扩展支持          |
| **Tab 补全增强**        | 文件路径补全、工具名补全                      |
| **Thinking 模式**     | 扩展思考 (Extended Thinking) 完整支持     |
| **多轮 Compact**      | 更智能的上下文压缩策略                       |

### 低优先级

| 功能                | 说明                 |
| ----------------- | ------------------ |
| **团队记忆同步**        | 团队共享记忆 (team/ 子目录) |
| **Enterprise 策略** | 组织级配置和权限管控         |
| **Telemetry**     | 使用统计和错误上报          |
| **国际化**           | 多语言 UI             |

### 测试基础设施

目前没有自动化测试。需要：

```
tests/
├── test_providers/      # 各 provider 的 mock 测试
├── test_tools/          # 工具执行测试
├── test_engine/         # agentic loop 测试
├── test_mcp/            # MCP 传输层测试
├── test_memory/         # 记忆存储测试
├── test_commands/       # 斜杠命令测试
└── conftest.py          # pytest fixtures
```

---

## 开发指南

### 添加新的 LLM 提供商

1. 在 `ccos/providers/` 下创建新文件（如 `gemini.py`）
2. 继承 `LLMProvider`，实现 `stream()` 和 `list_models()`
3. 在 `ccos/providers/registry.py` 的 `get_provider()` 中添加分发逻辑
4. 在 `ccos/config.py` 的 `_default()` 中添加默认配置

### 添加新的工具

1. 在 `ccos/tools/` 下创建新文件
2. 继承 `Tool`，实现 `execute(params, ctx) -> ToolOutput`
3. 在 `ccos/tools/base.py` 的 `create_default_registry()` 中注册

### 添加新的斜杠命令

1. 在 `ccos/commands/builtin.py` 的 `register_builtin_commands()` 中定义命令函数
2. 在末尾的注册块中添加 `registry.register(SlashCommand(...))`

### 添加新的 MCP 传输

1. 在 `ccos/mcp/transport.py` 中创建新的 `MCPTransport` 子类
2. 在 `ccos/mcp/types.py` 的 `TransportType` 枚举中添加类型
3. 在 `create_transport()` 工厂中添加分发
