"""Browser checks against a temporary, seeded local OtterNest instance.

Never points to a deployed server or uses real user credentials.
Run from the repository root after npm run build.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "ui-test-output"
ORIGIN = "http://127.0.0.1:8765"
OWNER = "ui-test-only-owner-token-not-for-production"
PASSWORD = "TestOnlyPassword123!"
ROUTES = [
    ("/", None, "总览"),
    ("/command", None, "智能指挥中心"),
    ("/projects", "项目与任务", "项目管理"),
    ("/tasks", "项目与任务", "任务中心"),
    ("/goals-workflows", "项目与任务", "目标与工作流"),
    ("/project-portability", "项目与任务", "项目导入导出"),
    ("/agents", "智能体与接入", "智能体管理"),
    ("/runtimes", "智能体与接入", "运行监控"),
    ("/interfaces", "智能体与接入", "外部接口"),
    ("/knowledge", "知识与成果", "知识库"),
    ("/skills", "知识与成果", "技能管理"),
    ("/artifacts", "知识与成果", "成果中心"),
    ("/governance", "审批与安全", "审批与治理"),
    ("/access", "审批与安全", "任务授权"),
    ("/account-security", "审批与安全", "账号安全"),
    ("/portfolio", None, "运营分析"),
    ("/system", None, "系统设置"),
]


def seed():
    with httpx.Client(base_url=ORIGIN, timeout=15) as client:
        def post(path, data):
            response = client.post(
                "/api/v1" + path, json=data,
                headers={"Authorization": "Bearer " + OWNER},
            )
            response.raise_for_status()
            return response.json()

        post("/auth/bootstrap", {
            "owner_token": OWNER, "username": "ui-admin",
            "password": PASSWORD, "display_name": "测试管理员",
        })
        project = post("/projects", {"name": "界面验收项目"})
        agent = post("/agents", {
            "name": "测试智能体", "platform": "local",
            "execution_mode": "manual", "capabilities": ["python"],
        })
        post(f"/projects/{project['id']}/members", {
            "agent_id": agent["id"], "role": "worker",
        })
        post(f"/projects/{project['id']}/tasks", {
            "title": "界面验收任务", "instruction": "核对页面显示",
            "acceptance": "完成界面检查", "risk": "R1",
        })


def screenshot(page, name):
    page.screenshot(path=str(OUTPUT / (name + ".png")), full_page=True,
                    animations="disabled")


def check_width(page, name, problems):
    widths = page.evaluate("""() => ({
        viewport: document.documentElement.clientWidth,
        document: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)
    })""")
    if widths["document"] > widths["viewport"] + 2:
        problems.append(f"{name}: horizontal overflow {widths}")


def visit(page, path, group, label, mobile):
    if mobile:
        toggle = page.get_by_role("button", name="菜单", exact=True)
        if toggle.is_visible():
            toggle.click()
    nav = page.get_by_role("navigation", name="主导航")
    if group:
        parent = nav.get_by_role("button", name=group, exact=True)
        if parent.get_attribute("aria-expanded") != "true":
            parent.click()
    nav.get_by_role("link", name=label, exact=True).click()
    page.wait_for_url(lambda url: url.split("?")[0].rstrip("/") == (ORIGIN + path).rstrip("/"))
    expect(page.locator("main h1").first).to_be_visible(timeout=20000)
    expect(page.locator("main .panel-loading:visible, main .loading-panel:visible")).to_have_count(0, timeout=20000)
    if mobile:
        expect(page.get_by_role("button", name="菜单", exact=True)).to_be_visible()
        expect(nav).to_be_hidden()


def react_checks(browser, name, width, height, problems):
    mobile = width < 820
    context = browser.new_context(viewport={"width": width, "height": height},
                                  locale="zh-CN", reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: problems.append(f"{name}: JS error {error}"))
    try:
        page.goto(ORIGIN)
        expect(page.get_by_role("heading", name="登录水獭窝")).to_be_visible()
        expect(page.get_by_label("用户名", exact=True)).to_be_visible()
        screenshot(page, f"{name}-login")
        check_width(page, f"{name}-login", problems)
        page.get_by_label("用户名", exact=True).fill("ui-admin")
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.locator("main h1")).to_have_text("总览", timeout=20000)

        for index, (path, group, label) in enumerate(ROUTES):
            case = f"{name}-{index:02d}-{path.strip('/') or 'overview'}"
            try:
                visit(page, path, group, label, mobile)
                expect(page.get_by_text("页面渲染遇到异常", exact=True)).to_have_count(0)
                check_width(page, case, problems)
                screenshot(page, case)
            except Exception as error:
                problems.append(f"{case}: {error}")
                screenshot(page, case + "-error")

        bell = page.get_by_role("button", name="通知收件箱")
        bell.click()
        expect(page.get_by_role("dialog", name="系统通知")).to_be_visible()
        screenshot(page, f"{name}-notifications")
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog", name="系统通知")).to_have_count(0)
        account = page.get_by_role("button", name="账户菜单")
        account.click()
        expect(page.get_by_role("button", name="退出登录")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("button", name="退出登录")).to_have_count(0)
    finally:
        context.close()


def legacy_checks(browser, problems):
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="zh-CN")
    page = context.new_page()
    page.on("pageerror", lambda error: problems.append(f"legacy: JS error {error}"))
    try:
        page.goto(ORIGIN + "/legacy")
        expect(page.locator("#login")).to_be_visible()
        screenshot(page, "legacy-mobile-login")
        check_width(page, "legacy-mobile-login", problems)
        page.locator("#loginTabToken").click()
        page.locator("#ownerToken").fill(OWNER)
        page.get_by_role("button", name="连接控制台", exact=True).click()
        expect(page.locator("#dashboard")).to_be_visible(timeout=20000)
        expect(page.locator("#nav .nav-btn").first).to_be_visible()
        screenshot(page, "legacy-mobile-overview")
        check_width(page, "legacy-mobile-overview", problems)
        page.locator("#themeToggleBtn").click()
        screenshot(page, "legacy-mobile-dark")
    finally:
        context.close()


def main():
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("Run npm run build before the UI test.")
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="otternest-ui-test-") as directory:
        env = {
            **os.environ,
            "OTTERNEST_DATABASE_URL": "sqlite+pysqlite:///" + str(Path(directory) / "ui.sqlite3"),
            "OTTERNEST_OWNER_TOKEN": OWNER,
            "OTTERNEST_WEB_DIST": str(ROOT / "frontend/dist"),
        }
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=ROOT, env=env, check=True)
        with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "otternest.app:app",
                 "--host", "127.0.0.1", "--port", "8765"],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Temporary backend stopped.")
                    try:
                        if httpx.get(ORIGIN + "/health/ready", timeout=1).is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Temporary backend did not become ready.")
                seed()
                problems = []
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        for name, width, height in [
                            ("desktop", 1440, 900), ("mobile", 390, 844),
                        ]:
                            react_checks(browser, name, width, height, problems)
                        legacy_checks(browser, problems)
                    finally:
                        browser.close()
                if problems:
                    raise AssertionError("\n".join(problems))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    main()
