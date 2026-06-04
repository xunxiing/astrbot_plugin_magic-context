# 更新日志 (Changelog)

本项目的所有显著更新都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
并且本项目遵循 [语义化版本 (Semantic Versioning)](https://semver.org/spec/v2.0.0.html)。

## [未发布]

## [0.1.0] - 2026-06-04

### 新增

- 插件初始版本发布 (`astrbot_plugin_magic_context`)。
- 实现上下文标记阶段 (`hooks/tag.py`)。
- 实现启发式清理机制 (`hooks/heuristic_cleanup.py`)。
- 实现 Caveman 文本压缩算法 (`hooks/caveman.py`)。
- 实现用于消息摘要的 Historian 代理 (`hooks/historian.py`)。
- 集成了自定义 WebUI 概览页面 (`pages/overview`)。
