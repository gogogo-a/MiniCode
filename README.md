# MiniCode

MiniCode 是一个用 Python 实现的本地智能编程代理。它围绕 OpenAI Chat Completions 工具调用协议构建，支持命令行对话、工具执行、权限控制、任务管理、记忆、上下文压缩、后台任务、定时任务、子代理协作、隔离工作区和 MCP 插件扩展。

这个项目适合用来学习一个 Agent 系统从简单对话循环逐步扩展到完整工程化代理的过程，也可以作为小型本地编程助手的基础框架。

## 核心能力

### 对话与工具调用

- 使用 OpenAI 兼容接口完成多轮对话。
- 支持 `bash`、文件读取、文件写入、文本替换、文件搜索等工具。
- 支持工具调用前后 hook，可在执行前做权限判断，在执行后记录结果。
- 支持上下文过长时自动压缩历史消息。

### 权限系统

MiniCode 的权限结果分为四类：

- `allow`：允许执行。
- `deny`：拒绝执行。
- `ask`：需要用户确认。
- `passthrough`：当前阶段不做决定，交给后续规则处理。

权限判断支持：

- 只读工具自动放行。
- 文件修改默认询问。
- 高风险命令直接拒绝。
- 项目级权限规则。
- 会话级临时规则。
- 定时任务中需要交互的操作自动拒绝。
- 子代理需要权限时向主代理发起审批请求。

项目级权限规则示例：

```json
{
  "rules": [
    {"toolName": "bash", "ruleBehavior": "deny", "ruleContent": "npm publish:*"},
    {"toolName": "read_file", "ruleBehavior": "allow", "ruleContent": "*"}
  ]
}
```

### 任务与计划

- `todo_write` 可维护当前会话的任务清单。
- 持久化任务保存在本地任务系统中。
- 支持创建任务、领取任务、完成任务、查看可执行任务。
- 支持任务依赖关系。

### 子代理协作

- 主代理可以启动子代理处理任务。
- 子代理通过邮箱机制与主代理通信。
- 子代理可提交计划并等待主代理审批。
- 子代理遇到需要确认的权限操作时，会向主代理发送权限请求。

### 后台任务与定时任务

- 长时间运行的 shell 命令可以转入后台执行。
- 后台结果会以通知形式回到对话上下文。
- 支持本地提醒。
- 支持持久化定时任务，由外部 tick 命令触发。

### 隔离工作区

- 支持为任务创建独立 git worktree。
- 子代理可以在隔离目录中处理任务，减少互相影响。
- 删除隔离目录前会检查是否存在未提交改动。

### MCP 插件

- 支持连接 stdio MCP 服务。
- 内置示例包含本地文档检索服务。
- 可通过 `.mcp/config.json` 配置更多 MCP server。

## 测试体系

MiniCode 的测试采用按业务模块组织、共享 runner 执行的结构。标准场景测试使用真实用户 prompt、确定性的 fake model tool call，以及真实 `agent_loop`，这样既能覆盖 agent 主链路，又不会因为真实模型输出波动导致测试不稳定。

```text
tests/
├── permission/
│   ├── unit/
│   ├── integration/
│   ├── scenarios/
│   │   ├── allow/
│   │   ├── ask/
│   │   └── deny/
│   ├── regression/
│   ├── replay/
│   └── live/
│       └── scenarios/
├── hook/
│   ├── unit/
│   ├── integration/
│   ├── scenarios/
│   ├── regression/
│   └── live/
│       └── scenarios/
└── shared/
    ├── runner.py
    ├── scenario.py
    ├── assertions.py
    ├── event_collector.py
    ├── fake_openai.py
    ├── workspace.py
    └── fixtures/
```

各层含义：

- `shared`：统一读取 JSON 场景、准备临时工作区、替换模型响应、收集事件并执行断言。
- `unit`：纯 Python 逻辑测试。
- `integration`：跨模块流程测试。
- `scenarios`：人工设计的真实 prompt 场景测试，模型响应使用固定的 `fake_model`。
- `regression`：历史回归案例。
- `replay`：问题重放案例。
- `live`：真实 prompt 调真实模型，再进入真实 `agent_loop` 的发布前抽检，按领域保留。
- `shared/fixtures`：所有领域复用的 hook、输入材料和辅助样例。

每个领域的 `scenarios` 都必须放真实用户 prompt。permission 场景按权限结果分到 `allow / ask / deny`；hook 场景按 hook 行为命名文件。

标准场景 JSON：

```json
{
  "name": "deny rm",
  "domain": "permission",
  "prompt": "删除整个项目",
  "setup": {
    "files": {
      "README.md": "MiniCode"
    }
  },
  "context": {
    "agent": "scenario"
  },
  "fake_model": {
    "tool_calls": [
      {
        "name": "bash",
        "args": {
          "command": "rm -rf /"
        }
      }
    ],
    "final": "已拒绝危险操作。"
  },
  "expected": {
    "permission": "deny",
    "tool_executed": false,
    "events": ["UserPromptSubmit", "PreToolUse", "PermissionDenied"]
  }
}
```

Runner 只关心统一结构，不关心场景属于 permission 还是 hook。执行流程是：

```text
读取 JSON
初始化临时工作区
把 prompt 交给 agent_loop
fake_model 返回确定性 tool call
收集 hook / permission 事件
比较 expected
输出 PASS / FAIL 摘要
```

标准工程测试不调用真实模型。发布前抽检使用 `live` 测试显式调用真实模型，不放进普通测试套件。

运行全部日常工程测试：

```bash
python tests/run_all.py
```

运行全部测试和真实模型发布前抽检：

```bash
python tests/run_all.py --live --show-flow
```

这条链路是：

```text
真实 prompt -> 真实模型 -> 真实 agent_loop -> hook / permission / tool -> 断言
```

也可以单独运行某个领域：

```bash
python tests/permission/run_tests.py
python tests/hook/run_tests.py
python tests/permission/run_tests.py --live --show-flow
python tests/hook/run_tests.py --live --show-flow
```

运行共享 runner 测试：

```bash
python -m unittest tests.shared.test_runner -v
```

运行场景统计：

```bash
python scenario_runner.py
```

## 快速开始

### 1. 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install openai python-dotenv
```

### 3. 配置环境变量

在项目根目录创建 `.env`：

```env
OPENAI_API_KEY=你的 API Key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
```

如果使用 OpenAI 兼容服务，可以把 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 改成对应值。

### 4. 启动 MiniCode

```bash
python code.py
```

退出时输入：

```text
q
```

## 定时任务入口

执行一个到期任务：

```bash
python code.py --tick
```

执行指定任务：

```bash
python code.py --run-job JOB_ID
```

## 目录说明

```text
.
├── code.py                 # 命令行入口
├── loop.py                 # 主代理循环
├── llm.py                  # OpenAI 兼容接口调用
├── tools.py                # 工具定义和执行
├── permission.py           # 权限决策管线
├── hooks.py                # hook 机制
├── team.py                 # 子代理和邮箱协议
├── task_system.py          # 持久化任务系统
├── system_scheduler.py     # 定时任务系统
├── worktree_system.py      # 隔离工作区
├── mcp_plugin.py           # MCP 插件连接
├── memory.py               # 记忆系统
├── compact.py              # 上下文压缩
├── scenario_runner.py      # 场景测试执行器
└── tests/                  # 测试用例
```

## 设计目标

- 保持代码足够小，便于阅读和教学。
- 用确定性规则处理高风险权限，不依赖 LLM 判断危险操作。
- 用结果导向的测试验证 Agent 行为，不绑定单一工具调用轨迹。
- 支持从单代理逐步扩展到多代理协作。
- 把权限、任务、调度、协作和插件都做成可单独理解的模块。
