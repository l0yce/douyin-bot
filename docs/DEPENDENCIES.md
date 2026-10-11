# 依赖检查记录

首次公开整理日期：2026-10-11。

Python 运行依赖采用部署时已使用的版本；npm 依赖由 `package-lock.json` 锁定。运行环境、浏览器二进制和 node_modules 不进入仓库。

默认抖音路径不使用幕间的 Puppeteer 浏览器，因此移除了顶层 `koishi-plugin-puppeteer` 安装依赖。默认禁用幕间网页浏览器；不要只打开该参数就期待运行时可用。抖音消息读取仍使用 Python Playwright/Firefox，不受此调整影响。

`npm audit --omit=dev` 在首次整理时报告 **0 个高危、12 个中危依赖节点**，主要来自 Koishi 依赖链上的 `file-type`，不是 12 个独立根因：

- [ASF 解析循环问题](https://github.com/advisories/GHSA-5v7r-6r5c-r473)
- [ZIP 解压资源消耗问题](https://github.com/advisories/GHSA-j47w-4g3g-c36v)

没有运行 `npm audit fix --force` 自动切换不兼容的 Koishi 或解析库版本。部署者应关注上游修复，升级后验证桥接、SQLite 和模型调用。此记录不是安全审计证明，漏洞数据库和依赖状态可能继续变化。
