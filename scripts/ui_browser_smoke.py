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
        goal1 = post(f"/projects/{project['id']}/goals", {"title": "前置验收目标", "success_criteria": "确认基础界面", "priority": 50})
        goal2 = post(f"/projects/{project['id']}/goals", {"title": "后续验收目标", "success_criteria": "确认后续流程", "priority": 50})
        post(f"/goals/{goal2['id']}/dependencies", {"predecessor_id": goal1["id"]})
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

        # Exercise the redesigned navigation and filters without mutating business data.
        visit(page, "/", None, "总览", mobile)
        page.get_by_role("link", name="查看执行阻塞", exact=True).click()
        expect(page.get_by_label("状态", exact=True)).to_have_value("blocked")
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_label("状态", exact=True)).to_have_value("all")
        page.locator(".task-status-filter").filter(has_text="等待授权").click()
        expect(page.get_by_label("状态", exact=True)).to_have_value("waiting_approval")
        screenshot(page, f"{name}-task-filter")
        visit(page, "/projects", "项目与任务", "项目管理", mobile)
        expect(page.get_by_role("group", name="项目内容")).to_be_visible()
        page.locator(".workspace-switch button").filter(has_text="成员").click()
        expect(page.get_by_role("heading", name="项目成员", exact=True)).to_be_visible()
        screenshot(page, f"{name}-project-members")
        page.locator(".workspace-switch button").filter(has_text="任务").click()
        expect(page.get_by_role("heading", name="任务列表", exact=True)).to_be_visible()
        visit(page, "/command", None, "智能指挥中心", mobile)
        expect(page.locator(".command-advanced")).not_to_have_attribute("open", "")
        page.locator(".command-advanced summary").click()
        expect(page.get_by_role("heading", name="操作计划查询")).to_be_visible()
        screenshot(page, f"{name}-command-tools")
        page.locator(".command-advanced summary").click()

        visit(page, "/knowledge", "知识与成果", "知识库", mobile)
        page.get_by_label("知识状态", exact=True).select_option("published")
        expect(page.get_by_label("知识状态", exact=True)).to_have_value("published")
        visit(page, "/skills", "知识与成果", "技能管理", mobile)
        page.get_by_label("技能状态", exact=True).select_option("published")
        page.get_by_label("搜索技能", exact=True).fill("不存在的技能")
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_label("搜索技能", exact=True)).to_have_value("")
        visit(page, "/artifacts", "知识与成果", "成果中心", mobile)
        page.get_by_label("成果验收状态", exact=True).select_option("pending")
        page.locator(".artifact-upload-disclosure summary").click()
        expect(page.get_by_role("button", name="上传为新版本", exact=True)).to_be_visible()
        screenshot(page, f"{name}-artifact-upload")
        visit(page, "/governance", "审批与安全", "审批与治理", mobile)
        page.locator(".approval-switch button").filter(has_text="待验收").click()
        expect(page.locator(".approval-switch button").filter(has_text="待验收")).to_have_attribute("aria-pressed", "true")
        visit(page, "/system", None, "系统设置", mobile)
        page.locator(".maintenance-disclosure summary").filter(has_text="服务诊断与版本维护").click()
        expect(page.get_by_role("heading", name="连接诊断", exact=True)).to_be_visible()
        screenshot(page, f"{name}-system-maintenance")

        # Remaining administrative workspaces: check non-destructive controls.
        visit(page, "/tasks", "项目与任务", "任务中心", mobile)
        page.get_by_role("button", name="清除筛选", exact=True).click()
        page.locator(".task-center-row").first.get_by_role("button", name="详情", exact=True).click()
        detail = page.get_by_role("dialog").filter(has=page.locator("#task-detail-title"))
        expect(detail).to_be_visible()
        detail.locator("summary").filter(has_text="执行信息与任务标识").click()
        expect(detail.locator(".task-detail-meta")).to_be_visible()
        screenshot(page, f"{name}-task-detail")
        detail.get_by_role("button", name="关闭任务详情", exact=True).click()
        # Open but never confirm a write operation; verify safe initial focus and cancellation.
        dispatch = page.get_by_role("button", name="立即调度", exact=True)
        dispatch.click()
        confirm = page.get_by_role("dialog", name="执行任务调度")
        expect(confirm).to_be_visible()
        expect(confirm.get_by_role("button", name="取消", exact=True)).to_be_focused()
        page.keyboard.press("Escape")
        expect(confirm).to_have_count(0)
        expect(dispatch).to_be_focused()

        visit(page, "/runtimes", "智能体与接入", "运行监控", mobile)
        page.locator(".runtime-status-switch button").filter(has_text="离线").click()
        expect(page.get_by_label("运行实例状态", exact=True)).to_have_value("offline")
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_label("运行实例状态", exact=True)).to_have_value("all")
        check_width(page, f"{name}-runtime-filters", problems)
        screenshot(page, f"{name}-runtime-filters")
        visit(page, "/interfaces", "智能体与接入", "外部接口", mobile)
        page.get_by_label("委托状态", exact=True).select_option("unknown")
        page.locator(".external-capabilities summary").click()
        expect(page.get_by_role("heading", name="MCP 工具桥", exact=True)).to_be_visible()
        screenshot(page, f"{name}-external-capabilities")

        page.get_by_role("button", name="搜索页面", exact=True).click()
        search = page.get_by_role("dialog", name="搜索页面")
        expect(search.get_by_label("搜索菜单", exact=True)).to_be_focused()
        search.get_by_label("搜索菜单", exact=True).fill("心跳")
        expect(search.get_by_role("link", name="运行监控", exact=True)).to_be_visible()
        search.get_by_label("搜索菜单", exact=True).press("Enter")
        page.wait_for_url(ORIGIN + "/runtimes")
        expect(search).to_have_count(0)
        page.get_by_role("button", name="搜索页面", exact=True).click()
        search.get_by_label("搜索菜单", exact=True).fill("不存在的页面")
        expect(search.get_by_text("没有匹配的页面")).to_be_visible()
        page.keyboard.press("Escape")
        expect(search).to_have_count(0)
        visit(page, "/agents", "智能体与接入", "智能体管理", mobile)
        page.get_by_label("搜索智能体", exact=True).fill("不存在的智能体")
        expect(page.get_by_text("没有符合条件的智能体", exact=True)).to_be_visible()
        page.get_by_role("button", name="清除筛选", exact=True).click()
        card = page.locator(".agent-card").filter(has_text="测试智能体")
        expect(card).to_be_visible()
        card.locator(".agent-more-actions summary").click()
        expect(card.get_by_role("button", name="重置令牌", exact=True)).to_be_visible()
        check_width(page, f"{name}-agent-actions", problems)
        screenshot(page, f"{name}-agent-actions")

        visit(page, "/projects", "项目与任务", "项目管理", mobile)
        page.get_by_label("搜索项目任务", exact=True).fill("不存在的任务")
        expect(page.get_by_text("没有符合条件的任务", exact=True)).to_be_visible()
        page.get_by_label("搜索项目任务", exact=True).fill("")
        expect(page.get_by_text("界面验收任务", exact=True)).to_be_visible()
        page.locator(".workspace-switch button").filter(has_text="成员").click()
        page.get_by_label("搜索项目成员", exact=True).fill("不存在的成员")
        expect(page.get_by_text("没有符合条件的成员", exact=True)).to_be_visible()
        visit(page, "/tasks", "项目与任务", "任务中心", mobile)
        page.get_by_role("button", name="紧凑显示", exact=True).click()
        expect(page.get_by_role("button", name="展开摘要", exact=True)).to_have_attribute("aria-pressed", "true")
        page.get_by_label("每页任务数", exact=True).select_option("10")
        expect(page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
        check_width(page, f"{name}-compact-tasks", problems)
        screenshot(page, f"{name}-compact-tasks")

        visit(page, "/system", None, "系统设置", mobile)
        page.get_by_label("搜索项目事件", exact=True).fill("不存在的事件")
        expect(page.get_by_text("没有符合条件的事件", exact=True)).to_be_visible()
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_label("搜索项目事件", exact=True)).to_have_value("")
        page.get_by_label("事件分页每页条数", exact=True).select_option("10")
        expect(page.get_by_role("group", name="事件分页", exact=True)).to_be_visible()
        check_width(page, f"{name}-event-pagination", problems)
        screenshot(page, f"{name}-event-pagination")
        visit(page, "/portfolio", None, "运营分析", mobile)
        page.get_by_label("搜索通知", exact=True).fill("不存在的通知")
        expect(page.get_by_text("暂无此类通知", exact=True)).to_be_visible()
        page.get_by_label("通知所属项目", exact=True).select_option("system")
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.get_by_label("搜索通知", exact=True)).to_have_value("")
        expect(page.get_by_label("通知所属项目", exact=True)).to_have_value("all")
        check_width(page, f"{name}-notification-filter", problems)

        visit(page, "/goals-workflows", "项目与任务", "目标与工作流", mobile)
        goal_graph = page.get_by_role("region", name="目标依赖概览")
        expect(goal_graph.locator(".goal-layer").nth(0)).to_contain_text("前置验收目标")
        expect(goal_graph.locator(".goal-layer").nth(1)).to_contain_text("后续验收目标")
        expect(goal_graph.locator(".goal-layer").nth(1)).to_contain_text("等待前置目标")
        check_width(page, f"{name}-goal-layers", problems)
        screenshot(page, f"{name}-goal-layers")
        page.get_by_label("目标状态", exact=True).select_option("waiting")
        expect(page.locator(".goal-card")).to_have_count(1)
        goal_graph.get_by_role("button", name="前置验收目标", exact=False).click()
        expect(page.get_by_label("目标状态", exact=True)).to_have_value("all")
        expect(page.locator(".goal-card-focused")).to_contain_text("前置验收目标")

        page.get_by_role("button", name="工作流规则", exact=False).click()
        page.get_by_role("group", name="工作流运行状态").get_by_role("button", name="失败", exact=True).click()
        expect(page.get_by_text("暂无符合条件的运行记录", exact=True)).to_be_visible()
        visit(page, "/portfolio", None, "运营分析", mobile)
        expect(page.locator(".portfolio-distribution-bar")).to_be_visible()
        page.get_by_label("搜索运营项目", exact=True).fill("不存在的项目")
        expect(page.get_by_text("没有符合条件的项目", exact=True)).to_be_visible()
        page.get_by_label("搜索运营项目", exact=True).fill("")
        page.get_by_label("项目指标排序", exact=True).select_option("blocked")

        page.locator(".distribution-legend").get_by_role("link", name="已阻塞", exact=False).click()
        expect(page.get_by_label("状态", exact=True)).to_have_value("blocked")
        visit(page, "/command", None, "智能指挥中心", mobile)
        page.get_by_role("button", name="查看项目状态", exact=True).click()
        expect(page.get_by_label("输入指令", exact=True)).to_be_focused()
        expect(page.get_by_label("输入指令", exact=True)).to_have_value("查看项目状态和指标")
        page.get_by_label("输入指令", exact=True).fill("查看项目状态")
        expect(page.get_by_role("log", name="项目对话记录")).to_be_visible()
        expect(page.get_by_role("button", name="发送", exact=True)).to_be_enabled()
        check_width(page, f"{name}-command-composer", problems)

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


def project_scope_checks(browser, problems):
    # Separate fixture after the original single-project checks.
    with httpx.Client(base_url=ORIGIN, timeout=15, headers={"Authorization": "Bearer " + OWNER}) as client:
        response = client.get("/api/v1/projects")
        response.raise_for_status()
        first = next(p for p in response.json() if p["name"] == "界面验收项目")
        response = client.post("/api/v1/projects", json={"name": "项目切换验收项目"})
        response.raise_for_status()
        second = response.json()
    context = browser.new_context(viewport={"width": 390, "height": 844}, locale="zh-CN", reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: problems.append(f"project-scope: JS error {error}"))
    try:
        page.goto(ORIGIN)
        page.get_by_label("用户名", exact=True).fill("ui-admin")
        page.get_by_label("密码", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.locator("main h1")).to_have_text("总览", timeout=20000)
        page.goto(ORIGIN + "/projects?project=" + first["id"])
        expect(page.get_by_text("界面验收任务", exact=True)).to_be_visible()
        page.get_by_role("button", name="新建任务", exact=True).click()
        page.get_by_label("任务标题", exact=True).fill("未保存的测试草稿")
        page.locator(".project-list-item").filter(has_text=second["name"]).click()
        expect(page.get_by_text("暂无任务", exact=True)).to_be_visible()
        expect(page.get_by_text("界面验收任务", exact=True)).to_have_count(0)
        expect(page.get_by_label("任务标题", exact=True)).to_have_count(0)
        page.get_by_role("button", name="新建任务", exact=True).click()
        expect(page.get_by_label("任务标题", exact=True)).to_have_value("")
        check_width(page, "project-scope-switch", problems)
        page.goto(ORIGIN + "/goals-workflows")
        selector = page.get_by_label("选择项目", exact=True)
        selector.select_option(first["id"])
        expect(page.locator(".goal-layer-node").filter(has_text="前置验收目标")).to_be_visible()
        selector.select_option(second["id"])
        expect(page.get_by_text("尚未定义目标", exact=True)).to_be_visible()
        expect(page.locator(".goal-layer-node")).to_have_count(0)
        page.goto(ORIGIN + "/artifacts")
        page.get_by_label("选择项目", exact=True).select_option(second["id"])
        page.get_by_text("归档与回收", exact=True).click()
        page.get_by_role("button", name="预览回收范围", exact=True).click()
        expect(page.locator(".gc-result")).to_be_visible()
        page.get_by_label("保留版本数", exact=True).fill("3")
        expect(page.locator(".gc-result")).to_have_count(0)
        expect(page.get_by_role("button", name="确认回收", exact=True)).to_be_disabled()
        screenshot(page, "project-scope-gc-preview")
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
                        project_scope_checks(browser, problems)
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
