# AstrBot Magic Context 插件

> 基于 [OpenCode magic-context](https://github.com/cortexkit/magic-context) 的上下文压缩插件，为 AstrBot 提供智能对话上下文管理能力。

## 功能特性

- **标签系统 (Tagging)**：为消息自动分配 §N§ 标签，支持精准的上下文引用和工具结果匹配
- **启发式清理 (Heuristic Cleanup)**：智能识别并清理冗余内容，如重复的工具调用结果
- **洞穴压缩 (Caveman Compression)**：极简模式压缩对话历史，减少 Token 消耗
- **历史代理 (Historian Agent)**：基于 LLM 的智能摘要生成，自动压缩长对话

## 安装方法

1. 将本插件复制到 AstrBot 的插件目录：
   ```bash
   cp -r astrbot_plugin_magic-context /path/to/astrbot/data/plugins/
   ```

2. 重启 AstrBot 或执行插件重载命令

## 配置说明

插件配置位于 `data/plugins/astrbot_plugin_magic-context/_conf_schema.json`。

### 主要配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `max_context_tokens` | int | 4096 | 最大上下文 Token 数 |
| `enable_compression` | bool | true | 是否启用压缩 |
| `compression_ratio` | float | 0.5 | 压缩比例（0-1） |
| `enable_historian` | bool | true | 是否启用历史代理 |
| `historian_model` | str | "default" | 历史代理使用的模型 |

## 项目结构

```
astrbot_plugin_magic-context/
├── main.py              # 插件主入口
├── tags_db.py           # 标签数据库管理
├── cave_man.py          # 洞穴压缩实现
├── strip_reasoning.py   # 推理过程剥离
├── historian.py         # 历史代理实现
├── _conf_schema.json    # 配置模式定义
├── metadata.yaml        # 插件元数据
├── plan/                # 开发计划文档
├── pages/               # 插件页面
├── hooks/               # 事件钩子
├── storage/             # 数据存储
├── skill-astrbot-dev/   # AstrBot 开发技能参考
└── astrbotcore/         # AstrBot 核心接口
```

## 工作原理

### 标签系统

每条消息会被自动分配一个 `§N§` 标签（N 为递增数字），存储在数据库中。标签用于：
- 精确定位特定消息
- 匹配工具调用和结果
- 支持选择性压缩和删除

### 压缩流程

1. **检测阶段**：监控上下文长度，接近阈值时触发压缩
2. **预处理**：使用启发式规则清理明显冗余的内容
3. **摘要生成**：Historian Agent 对历史对话进行智能摘要
4. **洞穴压缩**：对非关键内容使用极简格式压缩
5. **更新阶段**：用压缩后的内容替换原对话历史

### 钩子机制

插件通过 AstrBot 的过滤器钩子介入对话流程：

- `@filter.on_agent_begin()`：在 Agent 处理前进行标签分配和预压缩
- `@filter.on_using_llm_tool()`：工具调用时进行实时拦截
- `@filter.on_llm_tool_respond()`：工具响应时进行匹配和清理

## 开发计划

项目采用分阶段实现，详见 `plan/` 目录：

| 阶段 | 文档 | 功能 |
|------|------|------|
| 01 | 01-tagging-system.md | 标签系统 |
| 02 | 02-heuristic-cleanup.md | 启发式清理 |
| 03 | 03-caveman-compression.md | 洞穴压缩 |
| 04 | 04-historian-agent.md | 历史代理 |
| 05 | 05-filter-hooks.md | 过滤器钩子 |
| 06 | 06-storage-layer.md | 存储层 |
| 07 | ~~07-memory-system.md~~ | ~~记忆系统~~（跳过，已有其他插件实现） |
| 08 | 08-provider-integration.md | 提供商集成 |
| 09 | 09-testing.md | 测试 |
| 10 | 10-documentation.md | 文档 |

## 技术细节

### 标签存储

- 使用 SQLite 数据库本地存储标签映射
- 标签 ID 全局唯一，不随消息内容变化
- 支持标签的增删改查操作

### 工具匹配

- 使用 AstrBot 的 `tool_call_id` 进行唯一标识
- 支持多轮工具调用的层级关系维护
- 自动清理孤儿工具结果

### 压缩策略

1. **保留**：用户明确标记的重要消息、系统提示词
2. **摘要**：对话内容转为关键信息摘要
3. **压缩**：详细内容转为极简描述
4. **丢弃**：临时性、已过期内容

## 依赖要求

- AstrBot >= 4.16, < 5
- Python >= 3.9

## 贡献指南

欢迎提交 Issue 和 Pull Request！

## 许可证

本项目采用与 AstrBot 相同的开源许可证。

## 致谢

- [OpenCode magic-context](https://github.com/cortexkit/magic-context) - 原版实现
- [AstrBot](https://github.com/Soulter/AstrBot) - 插件框架
