# 第三方来源与修改说明

## HDS Interlude

- 原项目：MomoiCore / HDS-Interlude
- 来源：https://gitee.com/MomoiCore/HDS-Interlude
- 所包含版本：1.0.1-rc28
- 原许可证：AGPL-3.0，完整文本保留于 `douyin-interlude/upstream/LICENSE`。
- 本仓库同样采用 AGPL-3.0；依赖各自遵循其许可证。

本仓库于 2026-10-11 整理公开版本。`upstream/` 是包含本地适配的引擎快照，非未经修改的官方发行包。包含抖音 desktop bridge 接入、消息来源和发送结果约束；外层 `bridge.cjs`、`worker.cjs`、`hdsi-admin.cjs`、`message-grounding.cjs` 实现抖音白名单、管理接口和消息内容边界。

保留 TypeScript 源码、JavaScript 运行包、类型声明和上游说明。重新构建运行包的方法见 `docs/USAGE.md`。原项目名称和许可证不代表原作者为本仓库提供保证。

Python/npm 依赖通过 requirements 和 package-lock 安装，不复制虚拟环境、node_modules 或浏览器二进制。它们的许可证以各软件包为准。
