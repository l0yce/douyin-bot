// 打包前防线：rc16/rc17 曾因只跑 tsc（emitDeclarationOnly）而打出旧 bundle。
// prepack 时校验 lib/index.js 不早于 src/ 下任何 .ts 文件，过期即拒绝打包。
import { statSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

let bundleMtime
try {
  bundleMtime = statSync('lib/index.js').mtimeMs
} catch {
  console.error('[prepack] lib/index.js 不存在。请在工作区根目录运行 "npx yakumo build" 后再打包。')
  process.exit(1)
}

let newestSrc = 0
let newestFile = ''
function walk(dir) {
  for (const item of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, item.name)
    if (item.isDirectory()) walk(path)
    else if (item.name.endsWith('.ts')) {
      const mtime = statSync(path).mtimeMs
      if (mtime > newestSrc) { newestSrc = mtime; newestFile = path }
    }
  }
}
walk('src')

if (newestSrc > bundleMtime) {
  console.error(`[prepack] lib/index.js 落后于源码（最新改动：${newestFile}）。请在工作区根目录运行 "npx yakumo build" 后重新打包。`)
  process.exit(1)
}
console.log('[prepack] bundle 新鲜度校验通过')
