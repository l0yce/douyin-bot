"""Verify installed Firefox without cookies, chat pages or persistent profiles."""
import asyncio
import os
from pathlib import Path

os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(Path(__file__).resolve().parents[1]/'douyin-friend-ai-bot/ms-playwright'))
from playwright.async_api import async_playwright

async def check():
    async with async_playwright() as pw:
        browser=await pw.firefox.launch(headless=True,timeout=30000)
        try:
            page=await browser.new_page()
            await page.set_content('<title>offline-check</title><p>browser ready</p>')
            assert await page.title()=='offline-check'
            print('PASS: Firefox 启动并渲染离线页面；没有访问抖音或使用登录态。')
        finally:await browser.close()

if __name__=='__main__':
    try:asyncio.run(check())
    except Exception as error:
        raise SystemExit('Firefox 检查失败。安装器会在此检查通过后才写入和启动服务；单独运行检查不会改变已有服务。请按 docs/LINUX.md 检查浏览器系统库、glibc 和 Playwright 错误：\n'+str(error))
