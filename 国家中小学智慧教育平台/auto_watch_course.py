"""
课程自动观看脚本
适用于国家中小学智慧教育平台（及类似结构的网课平台）

依赖安装:
    pip install playwright
    playwright install chromium

使用方法:
=========
1. 关闭所有 Edge 窗口

2. 用以下命令启动 Edge 并开启远程调试（PowerShell）:
       Start-Process "msedge.exe" -ArgumentList "--remote-debugging-port=9222"
   或者手动在 Edge 快捷方式"目标"末尾加上:
       --remote-debugging-port=9222
   （如果 Edge 已在运行，必须先全部关闭才能用新参数启动）

3. 在打开的 Edge 中手动登录平台，并导航到课程页面
官网：https://basic.smartedu.cn/

4. 运行本脚本:
       python auto_watch_course.py

5. 停止: 按 Ctrl+C

工作原理:
=========
- 通过 CDP 连接到已打开的 Edge
- 通过"目录"等标题文字精确定位右侧目录板块（排除导航栏）
- 自动启动视频播放（如果暂停）
- 监听视频 ended 状态
- 视频结束后在目录中递归查找下一个未播放的视频
- 自动展开嵌套的章/节、处理"我知道了"弹窗

配置:
======
- 状态识别依赖 PLAYED/PLAYING/UNPLAYED 关键词，如失效请按实际 DOM 调整
- CATALOG_ITEM_SELECTORS / CATALOG_TITLE_TEXTS 可按页面结构调整
- 首次运行建议保持 DEBUG_MODE = True
"""

import asyncio
import sys
import time
import logging

from playwright.async_api import async_playwright


# ============================================================
# 配置区 - 根据需要调整
# ============================================================

# Edge 远程调试地址
CDP_URL = "http://localhost:9222"

# 平台网址（留空使用 Edge 中已打开的页面）
PLATFORM_URL = ""

# 打印调试信息
DEBUG_MODE = True

# ====== 视频 ======

VIDEO_SELECTORS = [
    "video",
    ".video-player video",
    ".prism-player video",
    "#J_prismPlayer video",
    "[class*='player'] video",
    "[class*='Player'] video",
    "[class*='video'] video",
    "[class*='Video'] video",
]

# 播放按钮选择器（用于启动暂停的视频）
PLAY_BUTTON_SELECTORS = [
    ".prism-play-btn",
    ".prism-big-play-btn",
    ".vjs-play-button",
    ".vjs-big-play-button",
    "button[title*='播放']",
    "button[aria-label*='播放']",
    "button[title*='Play']",
    "[class*='play-btn']",
    "[class*='playBtn']",
    "[class*='big-play']",
    "[class*='player'] button",
    "[class*='Player'] button",
]

# 是否静音播放（如遇到自动播放被阻止可打开）
MUTE_VIDEO = False

# ====== 目录定位 ======

# 目录板块的标题文字（用于定位右侧目录容器）
# 按优先级：脚本会找这些文字，再往父级查找容器
CATALOG_TITLE_TEXTS = [
    "课程目录",
    "章节目录",
    "课程大纲",
    "目录",
    "课程列表",
    "课件列表",
]

# 目录项选择器（在目录容器内搜索）
# 按优先级：外层折叠面板 → 内层资源项
CATALOG_ITEM_SELECTORS = [
    ".fish-collapse-item",              # 智慧教育平台：外层折叠章节
    ".resource-item",                   # 智慧教育平台：内层视频/资源项
    ".resource-item-train",
    "[class*='catalog-item']",
    "[class*='catalog_item']",
    "[class*='CatalogItem']",
    "[class*='chapter-item']",
    "[class*='chapter_item']",
    "[class*='ChapterItem']",
    "[class*='course-item']",
    "[class*='list-item']",
    "[class*='tree-node']",
    "[class*='tree-node-content']",
    ".ant-tree-node-content-wrapper",
    ".el-tree-node__content",
    "li[class*='item']",
    "[class*='menu-item']",
    "[class*='outline'] li",
    "li",
]

# ====== 弹窗 ======

POPUP_BUTTON_TEXTS = [
    "我知道了",
    "我知道了！",
    "知道了",
    "知道了！",
    "好的",
    "确定",
    "确认",
    "OK",
]

POPUP_CONTAINER_SELECTORS = [
    # fish 弹窗（根据实际 DOM 优先匹配）
    ".fish-modal-confirm-btns",
    ".fish-modal",
    ".fish-dialog",
    ".fish-mask",
    ".fish-popup",
    # 通用弹窗
    "[class*='dialog']",
    "[class*='Dialog']",
    "[class*='modal']",
    "[class*='Modal']",
    "[class*='popup']",
    "[class*='Popup']",
    "[class*='mask']",
    "[class*='Mask']",
    "[class*='overlay']",
    "[class*='Overlay']",
    "[class*='modal-wrap']",
    "[class*='tips']",
    "[class*='Tips']",
    "[class*='tip']",
    "[class*='notice']",
    "[class*='Notice']",
    "[class*='toast']",
    "[class*='Toast']",
    "[class*='message']",
    "[class*='Message']",
    "[role='dialog']",
    "[role='alertdialog']",
    ".ant-modal",
    ".el-dialog",
    ".el-message-box",
    ".layui-layer",
]

# ====== 状态关键词（class/aria-label/文本匹配） ======

PLAYED_KEYWORDS = [
    "played", "completed", "finished", "done", "checked",
    "已学完", "已学", "已看", "已观看", "已完成", "看过", "已结束",
]

PLAYING_KEYWORDS = [
    "playing", "current", "active", "progress", "learning",
    "正在", "播放中", "学习中", "进行中", "已播放未看完",
]

UNPLAYED_KEYWORDS = [
    "unplayed", "pending", "todo", "not-started",
    "未播放", "未学习", "未开始", "未学", "未开始",
]

# ====== 时间参数 ======

POLL_INTERVAL = 3        # 视频轮询
POST_END_DELAY = 4       # 视频结束后等待页面更新
CLICK_DELAY = 2           # 点击后等待
EXPAND_DELAY = 1.0        # 展开后等待
CHILD_LOAD_TIMEOUT = 5    # 展开后等子项加载的最长时间
RETRY_INTERVAL = 30       # 没找到下一个时的重试间隔


# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto-watch")
if DEBUG_MODE:
    logging.getLogger("playwright").setLevel(logging.WARNING)


# ============================================================
# 主类
# ============================================================

class CourseAutoPlayer:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.start_url = None
        self._catalog_container = None  # 目录容器缓存

    # ==================== 连接 ====================

    async def connect(self):
        """连接到已打开的 Edge"""
        log.info("正在连接浏览器: %s", CDP_URL)
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            log.error("连接失败: %s", e)
            log.error("请确认 Edge 已通过 --remote-debugging-port=9222 启动")
            raise

        if not self.browser.contexts:
            log.error("找不到浏览器 context，请先在 Edge 中打开页面")
            sys.exit(1)

        self.context = self.browser.contexts[0]
        pages = self.context.pages
        if not pages:
            self.page = await self.context.new_page()
        else:
            video_page = None
            for p in pages:
                try:
                    if await p.query_selector("video"):
                        video_page = p
                        break
                except Exception:
                    pass
            self.page = video_page or pages[-1]

        self.start_url = self.page.url
        log.info("已连接到: %s", self.start_url[:100])
        return self.page

    async def navigate_if_needed(self):
        if PLATFORM_URL and not self.page.url.startswith(PLATFORM_URL):
            log.info("导航到: %s", PLATFORM_URL)
            await self.page.goto(PLATFORM_URL, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)

    # ==================== 视频 ====================

    async def get_video_element(self):
        for sel in VIDEO_SELECTORS:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    async def get_video_state(self):
        video = await self.get_video_element()
        if not video:
            return None
        try:
            return await video.evaluate(
                "v => ({currentTime: v.currentTime, duration: v.duration, "
                "ended: v.ended, paused: v.paused, readyState: v.readyState})"
            )
        except Exception:
            return None

    async def ensure_video_playing(self):
        """
        确保视频正在播放。
        每次尝试播放前会先关闭弹窗，形成「关弹窗 → 试播放」守护循环，
        解决弹窗在点击视频后才出现、阻塞播放的问题。
        """
        for attempt in range(6):
            # 1. 获取视频状态
            video = await self.get_video_element()
            if not video:
                if attempt < 3:
                    await asyncio.sleep(1.0)
                    continue
                log.warning("未找到视频元素，无法启动播放")
                return False

            state = await self.get_video_state()
            if not state:
                if attempt < 3:
                    await asyncio.sleep(1.0)
                    continue
                return False

            # 已结束的视频不需要启动
            if state["ended"]:
                log.info("视频已结束，跳过")
                return False

            # 正在播放 → 直接返回
            if not state["paused"] and state["currentTime"] > 0:
                if attempt == 0:
                    log.info("视频正在播放中 (%.1fs / %.1fs)",
                             state["currentTime"], state["duration"] or 0)
                else:
                    log.info("弹窗已关闭，视频恢复播放")
                return True

            # 2. 弹窗已关闭但视频未播放 → 尝试启动
            if attempt == 0:
                log.info("视频未播放，启动播放...")

            ok = await self._try_start_playback(video)
            if ok:
                return True

            # 3. 未成功 → 可能弹窗又出现了，下一轮继续
            await asyncio.sleep(0.5)

        log.warning("多次尝试后仍无法启动视频")
        return False

    async def _try_start_playback(self, video):
        """单次播放尝试，返回是否成功"""
        # 策略 1：JS play()
        mute = "true" if MUTE_VIDEO else "false"
        try:
            await video.evaluate(
                f"v => {{ v.muted = {mute}; v.play().catch(() => {{}}); }}"
            )
            # 开始播放视频后，弹窗才出现
            await asyncio.sleep(0.5)
            # 0. 先关闭任何可能出现的弹窗
            await self.dismiss_popups(wait_timeout=3.0)
            await asyncio.sleep(0.3)
            new_state = await self.get_video_state()
            if new_state and not new_state["paused"]:
                log.info("JS play() 启动成功")
                return True
        except Exception:
            pass
        #
        # # 策略 2：点击播放按钮
        # for sel in PLAY_BUTTON_SELECTORS:
        #     try:
        #         btn = await self.page.query_selector(sel)
        #         if not btn:
        #             continue
        #         visible = await btn.is_visible()
        #         if not visible:
        #             continue
        #         in_player = await btn.evaluate(
        #             "el => !!(el.closest('[class*=\"player\"]') || "
        #             "el.closest('[class*=\"video\"]') || el.closest('video'))"
        #         )
        #         if not in_player:
        #             continue
        #         log.debug("点击播放按钮: %s", sel)
        #         await btn.click()
        #         await asyncio.sleep(1)
        #         new_state = await self.get_video_state()
        #         if new_state and not new_state["paused"]:
        #             log.info("播放按钮 %s 启动成功", sel)
        #             return True
        #     except Exception:
        #         continue
        #
        # # 策略 3：点击视频区域
        # try:
        #     log.debug("点击视频元素")
        #     await video.click(position={"x": 200, "y": 100})
        #     await asyncio.sleep(1)
        #     new_state = await self.get_video_state()
        #     if new_state and not new_state["paused"]:
        #         log.info("点击视频启动成功")
        #         return True
        # except Exception:
        #     pass

        return False

    async def wait_for_video_end(self):
        """等待当前视频播放自然结束"""
        log.info("等待当前视频结束...")
        last_log = 0.0

        while True:
            state = await self.get_video_state()

            # 视频元素消失 → 页面可能跳走了
            if state is None:
                log.info("视频元素已消失，认为当前节完成")
                return

            t = state["currentTime"]
            d = state["duration"]

            if state["ended"]:
                log.info("视频 ended")
                return

            if d and d > 0 and t >= d - 0.5:
                log.info("已到末尾")
                return

            now = time.time()
            if DEBUG_MODE and d and d > 0 and now - last_log > 15:
                log.debug("进度 %.1f / %.1f (%.0f%%)",
                          t, d, t / d * 100)
                last_log = now

            await asyncio.sleep(POLL_INTERVAL)

    # ==================== 弹窗 ====================

    @staticmethod
    async def _is_visible_safe(el):
        """安全地检查元素是否可见"""
        try:
            return await el.is_visible()
        except Exception:
            return False

    async def _find_clickable_in(self, root, text):
        """
        在 root 内找到包含目标文本的可点击元素。
        不仅限 <button>，也匹配 <a>、<span>、<div> 等。
        优先匹配最短文本的元素（避免点到大容器）。
        """
        selectors = [
            # fish 弹窗按钮（优先匹配实际 DOM）
            f".fish-modal-confirm-btns .fish-btn-primary:has-text('{text}')",
            f".fish-modal-confirm-btns button:has-text('{text}')",
            f".fish-btn-primary:has-text('{text}')",
            f".fish-btn:has-text('{text}')",
            # 通用按钮
            f"button:has-text('{text}')",
            f"a:has-text('{text}')",
            f"[class*='btn']:has-text('{text}')",
            f"[class*='button']:has-text('{text}')",
            f"span:has-text('{text}')",
            f"div:has-text('{text}')",
        ]
        best = None
        best_len = 99999
        for sel in selectors:
            try:
                candidates = await root.query_selector_all(sel)
                for c in candidates:
                    if not await self._is_visible_safe(c):
                        continue
                    try:
                        t = (await c.inner_text()).strip()
                    except Exception:
                        t = ""
                    # 优先匹配文本最贴近目标的小元素
                    if text in t and len(t) < best_len:
                        best_len = len(t)
                        best = c
            except Exception:
                continue
        return best

    async def _click_fish_modal_btn(self, text):
        """
        通过 JS 直接点击 fish-modal 中的按钮。
        绕过 Playwright 选择器/点击在某些 UI 框架上的兼容问题。
        """
        try:
            clicked = await self.page.evaluate(
                """(text) => {
                    const normalize = s => (s || '').replace(/\\s+/g, ' ').trim();
                    const candidates = Array.from(document.querySelectorAll(
                        '.fish-modal-confirm-btns .fish-btn-primary,' +
                        '.fish-modal-confirm-btns .fish-btn,' +
                        '.fish-modal-confirm-btns button,' +
                        '.fish-modal-wrap .fish-btn-primary,' +
                        '.fish-modal-wrap button,' +
                        '.fish-modal button'
                    ));
                    for (const btn of candidates) {
                        const t = normalize(btn.innerText || btn.textContent);
                        if (t === text || t.includes(text)) {
                            // 有些框架事件绑定在子 span 上，优先点 button，失败时点子元素
                            btn.click();
                            if (btn.firstElementChild) {
                                setTimeout(() => btn.firstElementChild.click(), 0);
                            }
                            return true;
                        }
                    }
                    return false;
                }""",
                text
            )
            return clicked
        except Exception:
            return False

    async def dismiss_popups(self, wait_timeout=6.0, max_rounds=8):
        """
        关闭页面上的弹窗。
        支持等待弹窗出现（最多 wait_timeout 秒），
        匹配多种按钮类型（button/a/span/div），兜底全局搜索。
        """
        start = time.time()
        for _ in range(max_rounds):
            # 耗时保护：总时间不能超过 wait_timeout
            if time.time() - start > wait_timeout:
                return False

            clicked = False
            for text in POPUP_BUTTON_TEXTS:
                # 0) fish 弹窗专用快速路径：JS 直接点击
                if await self._click_fish_modal_btn(text):
                    log.info("JS 关闭 fish 弹窗: %s", text)
                    await asyncio.sleep(0.6)
                    clicked = True
                    break

                if clicked:
                    break

                # 1) 在弹窗容器内查找（增加概率）
                for csel in POPUP_CONTAINER_SELECTORS:
                    try:
                        containers = await self.page.query_selector_all(csel)
                    except Exception:
                        continue
                    for c in containers:
                        if not await self._is_visible_safe(c):
                            continue
                        btn = await self._find_clickable_in(c, text)
                        if btn:
                            log.info("关闭弹窗: %s", text)
                            try:
                                await btn.click()
                            except Exception:
                                pass
                            await asyncio.sleep(0.6)
                            clicked = True
                            break
                    if clicked:
                        break
                if clicked:
                    break

                # 2) 全局兜底
                btn = await self._find_clickable_in(self.page, text)
                if btn:
                    log.info("关闭弹窗(全局): %s", text)
                    try:
                        await btn.click()
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
                    clicked = True
                    break

            if not clicked:
                # 3) 兜底：按 Escape 关闭弹窗
                try:
                    await self.page.keyboard.press("Escape")
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

                # 4) 兜底：点击可见遮罩层（常见模式：点遮罩关闭弹窗）
                try:
                    for msel in (
                        "[class*='mask']", "[class*='Mask']",
                        "[class*='overlay']", "[class*='Overlay']",
                        ".fish-mask",
                    ):
                        masks = await self.page.query_selector_all(msel)
                        for m in masks:
                            if await self._is_visible_safe(m):
                                try:
                                    await m.click()
                                except Exception:
                                    pass
                                await asyncio.sleep(0.4)
                                clicked = True
                                break
                        if clicked:
                            break
                except Exception:
                    pass

            if not clicked:
                # 当前没找到弹窗：等 0.5 秒再试
                elapsed = time.time() - start
                if elapsed < 1.0:
                    # 前 1 秒快速轮询
                    await asyncio.sleep(0.3)
                else:
                    await asyncio.sleep(0.5)
                # 如果已经超时一半以上还没找到，且不是第一轮，就返回
                if elapsed > wait_timeout * 0.5 and _ > 0:
                    return False
            else:
                # 找到了，短暂等待后继续检查是否还有弹窗
                await asyncio.sleep(0.3)

        return True

    # ==================== 目录定位 ====================

    async def find_catalog_container(self):
        """
        定位右侧「目录」板块容器。
        策略：
          1. 找包含"目录""课程目录"等文字的标题元素
          2. 往上追溯到包含列表项的父容器
          3. 排除导航栏/header 区域
          4. 兜底用 aside + 排除 nav/header
        """
        log.info("定位目录板块...")

        # --- 策略 1：通过标题文字找 ---
        for title_text in CATALOG_TITLE_TEXTS:
            try:
                result = await self.page.evaluate_handle(
                    """([title, navExclude]) => {
                        // 遍历所有可见元素，找文本等于 title 的
                        const candidates = [];
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            // 取直接子文本（排除后代文本），避免匹配太深
                            const directText = Array.from(el.childNodes)
                                .filter(n => n.nodeType === 3)
                                .map(n => n.textContent.trim())
                                .join('');
                            if (!directText) {
                                // 如果元素只有一个文本子节点，也算
                                if (el.childNodes.length === 1 &&
                                    el.childNodes[0].nodeType === 3) {
                                    const t = el.textContent.trim();
                                    if (t === title || t.includes(title)) {
                                        candidates.push(el);
                                    }
                                }
                                continue;
                            }
                            if (directText === title ||
                                (directText.length <= 20 && directText.includes(title))) {
                                candidates.push(el);
                            }
                        }
                        // 去重
                        const seen = new Set();
                        return candidates.map(el => {
                            // 往上找父容器
                            let node = el;
                            for (let i = 0; i < 10; i++) {
                                node = node.parentElement;
                                if (!node) break;
                                const tag = node.tagName.toLowerCase();
                                const cls = (node.className || '').toLowerCase();
                                // 排除导航栏/header
                                if (navExclude.includes(tag)) continue;
                                let excluded = false;
                                for (const kw of navExclude) {
                                    if (cls.includes(kw)) { excluded = true; break; }
                                }
                                if (excluded) continue;
                                // 检查内部是否有列表子项
                                const items = node.querySelectorAll(
                                    'li, [class*="item"], [class*="node"]');
                                if (items.length >= 2) {
                                    const id = node.id || cls.slice(0, 40) || tag;
                                    if (seen.has(id)) break;
                                    seen.add(id);
                                    return {
                                        tag: node.tagName,
                                        cls: cls.slice(0, 60),
                                        itemCount: items.length,
                                    };
                                }
                            }
                            return null;
                        }).filter(Boolean);
                    }""",
                    [title_text, ["nav", "header", "menu", "navbar", "breadcrumb"]],
                )
                candidates = await result.json_value()
                if candidates and len(candidates) > 0:
                    # 选目录项数量最多的那个
                    best = max(candidates, key=lambda x: x.get("itemCount", 0))
                    log.info(
                        "命中目录标题 '%s' → 容器 %s (%d 个列表项)",
                        title_text, best.get("cls", best.get("tag", "?")),
                        best.get("itemCount", 0),
                    )
                    # 用 CSS 选择器定位回去
                    container_cls = best.get("cls", "")
                    container_tag = best.get("tag", "div")
                    # 通过 class+tag 组合找
                    if container_cls:
                        # 取第一个 class 用于定位
                        first_cls = container_cls.split()[0]
                        if first_cls:
                            sel = f"{container_tag}.{first_cls}"
                            try:
                                el = await self.page.query_selector(sel)
                                if el:
                                    return el
                            except Exception:
                                pass
                    # class 有特殊字符，换个方式
                    # 直接用 evaluate 返回该元素的 xpath 或直接操作
                    result2 = await self.page.evaluate_handle(
                        """([title, best]) => {
                            // 重新找到那个元素
                            const all = document.querySelectorAll('*');
                            for (const el of all) {
                                const cls = (el.className || '').toLowerCase();
                                const tag = el.tagName.toLowerCase();
                                if (tag === best.tag &&
                                    cls.includes(best.cls.toLowerCase())) {
                                    return el;
                                }
                            }
                            return null;
                        }""",
                        [title_text, best],
                    )
                    el = result2.as_element()
                    if el:
                        return el
            except Exception as e:
                if DEBUG_MODE:
                    log.debug("策略1查找 '%s' 失败: %s", title_text, e)
                continue

        # --- 策略 2：兜底，用常见侧边栏选择器 ---
        fallback = [
            "aside",
            "[class*='fish-collapse']",         # 智慧教育平台折叠目录
            "[class*='catalog']:not(nav):not(header)",
            "[class*='Catalog']:not(nav):not(header)",
            "[class*='directory']:not(nav):not(header)",
            "[class*='sidebar']:not(nav):not(header)",
            "[class*='outline']:not(nav):not(header)",
        ]
        for sel in fallback:
            try:
                els = await self.page.query_selector_all(sel)
                for el in els:
                    try:
                        if not await el.is_visible():
                            continue
                    except Exception:
                        continue
                    # 避免选中导航栏
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    cls = await el.evaluate("e => (e.className||'').toLowerCase()")
                    combined = tag + " " + cls
                    if any(k in combined for k in
                           ("nav", "header", "menu", "navbar", "breadcrumb")):
                        continue
                    items = await el.query_selector_all(
                        "li, [class*='item'], [class*='node'], "
                        ".fish-collapse-item, .resource-item")
                    if items and len(items) >= 2:
                        log.info("兜底命中: %s → %s (%d 子项)",
                                 sel, cls[:50], len(items))
                        return el
            except Exception:
                continue

        # --- 策略 3：用 evaluate 在 js 里直接找 ---
        try:
            result = await self.page.evaluate_handle("""
                () => {
                    // 找到 class 名中包含 catalog/chapter/course/fish-collapse
                    // 且有很多 li/item 的元素
                    const candidates = document.querySelectorAll(
                        '[class*="fish-collapse"], ' +
                        '[class*="catalog"]:not(nav):not(header), ' +
                        '[class*="chapter"]:not(nav):not(header), ' +
                        '[class*="outline"]:not(nav):not(header), ' +
                        '[class*="directory"]:not(nav):not(header), ' +
                        'aside'
                    );
                    let best = null, bestCount = 0;
                    for (const el of candidates) {
                        if (el.offsetParent === null) continue;
                        const cls = (el.className||'').toLowerCase();
                        const tag = el.tagName.toLowerCase();
                        if (['nav','header'].includes(tag) ||
                            cls.includes('nav') || cls.includes('header') ||
                            cls.includes('menu') || cls.includes('navbar')) continue;
                        const items = el.querySelectorAll(
                            'li, [class*="item"], [class*="node"], ' +
                            '.fish-collapse-item, .resource-item');
                        if (items.length > bestCount) {
                            best = el;
                            bestCount = items.length;
                        }
                    }
                    if (best) {
                        return {
                            tag: best.tagName,
                            cls: (best.className||'').slice(0, 80),
                            itemCount: bestCount,
                        };
                    }
                    return null;
                }
            """)
            info = await result.json_value()
            if info and info.get("itemCount", 0) >= 2:
                # 用 JS 方式直接返回 element
                el_handle = await self.page.evaluate_handle(
                    """info => {
                        const candidates = document.querySelectorAll(
                            '[class*="fish-collapse"], ' +
                            '[class*="catalog"], [class*="chapter"], ' +
                            '[class*="outline"], [class*="directory"], aside');
                        for (const el of candidates) {
                            const cls = (el.className||'').toLowerCase();
                            if (info.cls && cls.includes(info.cls.toLowerCase()) &&
                                el.tagName === info.tag) return el;
                        }
                        return null;
                    }""",
                    info,
                )
                el = el_handle.as_element()
                if el:
                    log.info("策略3命中: %s (%,d 子项)",
                             info.get("cls", "?"), info.get("itemCount", 0))
                    return el
        except Exception:
            pass

        log.warning("未找到目录板块！请手动检查页面是否包含'目录'面板")
        return None

    async def get_catalog_container(self):
        """获取目录容器（带缓存，失效时重新查找）"""
        if self._catalog_container:
            try:
                await self._catalog_container.is_visible()
                return self._catalog_container
            except Exception:
                self._catalog_container = None
        self._catalog_container = await self.find_catalog_container()
        return self._catalog_container

    async def get_catalog_items(self, container=None):
        """
        获取目录项列表。
        如果 container=None，先定位目录板块，再在其中搜索。
        """
        if container is None:
            container = await self.get_catalog_container()
            if not container:
                return []

        best, best_count = [], 0
        for sel in CATALOG_ITEM_SELECTORS:
            try:
                items = await container.query_selector_all(sel)
                if items and len(items) > best_count:
                    best, best_count = items, len(items)
            except Exception:
                continue
        if DEBUG_MODE:
            log.debug("目录容器内找到 %d 个目录项", len(best))
        return best

    async def reset_catalog_cache(self):
        """重置目录容器缓存（页面结构变化时调用）"""
        self._catalog_container = None

    # ==================== 状态识别 ====================

    async def _item_info(self, item):
        try:
            return await item.evaluate(
                """el => {
                    const cls = el.className || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const text = (el.innerText || '').slice(0, 100);
                    // 收集子图标/状态标签的 title、class、src
                    const innerAttrs = Array.from(
                        el.querySelectorAll('i, span, svg, em, img'))
                        .map(n => {
                            const parts = [];
                            if (n.title) parts.push(n.title);
                            if (n.tagName === 'IMG' && n.src) parts.push(n.src);
                            const c = n.className;
                            const className = (c && c.baseVal !== undefined)
                                ? c.baseVal : (c || '');
                            if (className) parts.push(className);
                            return parts.join(' ');
                        })
                        .join(' | ');
                    return {cls, aria, text, innerAttrs};
                }"""
            )
        except Exception:
            return None

    def _classify_by_text(self, s):
        s_low = s.lower()
        for kw in PLAYED_KEYWORDS:
            if kw.lower() in s_low:
                return "played"
        for kw in PLAYING_KEYWORDS:
            if kw.lower() in s_low:
                return "playing"
        for kw in UNPLAYED_KEYWORDS:
            if kw.lower() in s_low:
                return "unplayed"
        return "unknown"

    async def classify_item_status(self, item):
        info = await self._item_info(item)
        if not info:
            return "unknown", ""
        s = info["cls"] + " " + info["aria"] + " " + info["innerAttrs"] + " " + info["text"]
        return self._classify_by_text(s), info["text"]

    async def is_expandable(self, item):
        """判断此项是否可展开（分类节点）。"""
        try:
            cls = (await item.get_attribute("class") or "").lower()

            # 0. fish-collapse 折叠面板
            if "fish-collapse-item" in cls:
                header = await item.query_selector(".fish-collapse-header")
                if header and await header.get_attribute("aria-expanded") is not None:
                    return True

            # 1. class 自身声明有子项
            if any(k in cls for k in (
                "has-children", "has_children", "parent", "expandable",
                "group", "folder", "branch",
            )):
                return True

            # 2. 有明确的展开/折叠图标
            for sel in (
                "[class*='arrow']", "[class*='toggle']",
                "[class*='switch']", "[class*='caret']",
                "[class*='chevron']", "[class*='folder']",
            ):
                # 排除 status icon（如 checkbox_linear 不含 arrow 等，安全）
                if await item.query_selector(sel):
                    return True

            # 3. 内部包含多个子目录项（严格模式：≥2 个子项）
            strict_child_selectors = [
                ".fish-collapse-item",
                ".resource-item",
                "[class*='catalog']",
                "[class*='Catalog']",
                "[class*='chapter']",
                "[class*='Chapter']",
                "[class*='tree-node']",
                "[class*='tree_node']",
                ".ant-tree-node",
                ".el-tree-node",
            ]
            for sel in strict_child_selectors:
                kids = await item.query_selector_all(sel)
                if kids and len(kids) >= 2:
                    return True

            return False
        except Exception:
            return False

    async def is_expanded(self, item):
        try:
            cls = (await item.get_attribute("class")) or ""

            # fish-collapse 折叠面板：以 header 的 aria-expanded 为准
            if "fish-collapse-item" in cls:
                header = await item.query_selector(".fish-collapse-header")
                if header:
                    expanded = await header.get_attribute("aria-expanded")
                    return expanded == "true"

            return any(k in cls for k in ("open", "expanded", "active", "selected"))
        except Exception:
            return False

    async def expand_item(self, item):
        try:
            cls = (await item.get_attribute("class") or "").lower()

            # fish-collapse 折叠面板：点击 header
            if "fish-collapse-item" in cls:
                header = await item.query_selector(".fish-collapse-header")
                if header:
                    await header.click()
                    return True
        except Exception:
            pass

        for sel in (
            "[class*='arrow']", "[class*='toggle']", "[class*='switch']",
            "[class*='expand']", "[class*='caret']", "[class*='chevron']",
        ):
            try:
                t = await item.query_selector(sel)
                if t:
                    await t.click()
                    return True
            except Exception:
                pass
        for sel in ("[class*='title']", "[class*='label']", "[class*='name']"):
            try:
                t = await item.query_selector(sel)
                if t:
                    await t.click()
                    return True
            except Exception:
                pass
        try:
            await item.click()
            return True
        except Exception:
            return False

    async def _wait_for_children(self, item, timeout=CHILD_LOAD_TIMEOUT):
        """展开后等待子项加载"""
        start = time.time()
        while time.time() - start < timeout:
            # 对 fish-collapse：等展开后的 content box 出现并包含 resource-item
            try:
                cls = (await item.get_attribute("class") or "").lower()
                if "fish-collapse-item" in cls:
                    content = await item.query_selector(
                        ".fish-collapse-content-active, .fish-collapse-content:not(.fish-collapse-content-hidden)"
                    )
                    if content:
                        items = await content.query_selector_all(
                            ".resource-item, .resource-item-train"
                        )
                        if items and len(items) >= 1:
                            await asyncio.sleep(0.3)
                            return
            except Exception:
                pass

            # 通用逻辑
            for sel in CATALOG_ITEM_SELECTORS:
                kids = await item.query_selector_all(sel)
                if kids and len(kids) >= 1:
                    await asyncio.sleep(0.3)
                    return
            await asyncio.sleep(0.5)
        await asyncio.sleep(EXPAND_DELAY)

    async def find_unplayed_recursive(self, container=None, depth=0, max_depth=8):
        """
        递归查找下一个未播放的视频。
        展开分类后会重新获取 items，以处理子项作为兄弟节点出现的平级结构。
        """
        if depth > max_depth:
            return None, None

        items = await self.get_catalog_items(container)
        if not items:
            return None, None

        i = 0
        while i < len(items):
            item = items[i]
            status, text = await self.classify_item_status(item)
            if DEBUG_MODE:
                log.debug("  [d=%d idx=%d] [%s] %s",
                          depth, i, status, text[:50].replace("\n", " "))

            if status == "played":
                i += 1
                continue

            expandable = await self.is_expandable(item)

            if expandable:
                if not await self.is_expanded(item):
                    log.info("展开: %s", text[:40].replace("\n", " "))
                    await self.expand_item(item)
                    await self._wait_for_children(item)

                    # 对 fish-collapse：子项嵌套在 .fish-collapse-content-active 里
                    try:
                        cls = (await item.get_attribute("class") or "").lower()
                        if "fish-collapse-item" in cls:
                            content = await item.query_selector(
                                ".fish-collapse-content-active, "
                                ".fish-collapse-content:not(.fish-collapse-content-hidden)"
                            )
                            if content:
                                result, info = await self.find_unplayed_recursive(
                                    content, depth + 1, max_depth)
                                if result:
                                    return result, info
                                i += 1
                                continue
                    except Exception:
                        pass

                    # 通用：重新获取 items（兄弟节点情况）
                    fresh_items = await self.get_catalog_items(container)
                    if fresh_items and len(fresh_items) >= len(items):
                        items = fresh_items
                        # 尝试在当前列表中定位刚才展开的项，继续处理它的下一项
                        found_idx = -1
                        norm_target = ' '.join(text.split())[:80]
                        for j, it in enumerate(items):
                            try:
                                it_text = await it.inner_text()
                            except Exception:
                                continue
                            if ' '.join(it_text.split())[:80] == norm_target:
                                found_idx = j
                                break
                        if found_idx >= 0 and found_idx + 1 < len(items):
                            i = found_idx + 1
                            continue
                else:
                    # 已展开：优先进入 fish-collapse 的 content 容器
                    try:
                        cls = (await item.get_attribute("class") or "").lower()
                        if "fish-collapse-item" in cls:
                            content = await item.query_selector(
                                ".fish-collapse-content-active, "
                                ".fish-collapse-content:not(.fish-collapse-content-hidden)"
                            )
                            if content:
                                result, info = await self.find_unplayed_recursive(
                                    content, depth + 1, max_depth)
                                if result:
                                    return result, info
                                i += 1
                                continue
                    except Exception:
                        pass

                    # 通用：递归进入 item 内部（嵌套结构）
                    result, info = await self.find_unplayed_recursive(
                        item, depth + 1, max_depth)
                    if result:
                        return result, info
                i += 1
                continue

            # 叶节点：未播放 / 正在播放 / 未知（保守也点一下）
            if status in ("unplayed", "playing", "unknown"):
                return item, {"status": status, "text": text}

            i += 1

        return None, None

    # ==================== 主流程 ====================

    async def click_and_play_next(self):
        """点击下一个未播放的目录项"""
        await self.dismiss_popups()
        item, info = await self.find_unplayed_recursive()

        if not item:
            return False, None

        status = info.get("status", "unknown")
        text = info.get("text", "")
        log.info("点击: [%s] %s", status, text[:60].replace("\n", " "))

        try:
            await item.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
            await item.click()
        except Exception as e:
            log.error("点击失败: %s", e)
            return False, None

        await asyncio.sleep(CLICK_DELAY)
        await self.dismiss_popups()
        await self.reset_catalog_cache()

        return True, text

    async def inspect(self):
        """打印页面关键信息"""
        log.info("=== 页面自检 ===")
        log.info("URL: %s", self.page.url)
        try:
            log.info("Title: %s", await self.page.title())
        except Exception:
            pass

        v = await self.get_video_element()
        log.info("视频元素: %s", "命中" if v else "未找到")
        if v:
            s = await self.get_video_state()
            if s:
                log.info("  状态: %.1f/%.1f paused=%s ended=%s",
                         s["currentTime"], s["duration"] or 0,
                         s["paused"], s["ended"])

        container = await self.get_catalog_container()
        if container:
            cls = await container.evaluate("e => e.className || e.tagName")
            log.info("目录容器: %s", cls[:80])
            items = await self.get_catalog_items(container)
            log.info("目录项: %d 个", len(items))
            for i, it in enumerate(items[:10]):
                status, text = await self.classify_item_status(it)
                exp = await self.is_expandable(it)
                log.info("  %2d. [%s]%s %s",
                         i + 1, status,
                         "[分类]" if exp else "     ",
                         text[:50].replace("\n", " "))
        else:
            log.info("目录容器: 未找到")

        log.info("状态关键词 | played:%s playing:%s unplayed:%s",
                 PLAYED_KEYWORDS[:3], PLAYING_KEYWORDS[:3], UNPLAYED_KEYWORDS[:3])

    async def run(self):
        await self.connect()
        await self.navigate_if_needed()

        if DEBUG_MODE:
            await self.inspect()

        # ====== 启动：尝试播放，如果有弹窗则关闭弹窗 ======
        await self.ensure_video_playing()

        log.info("=" * 50)
        log.info("开始自动观看（按 Ctrl+C 停止）")
        log.info("=" * 50)

        iteration = 0
        try:
            while True:
                iteration += 1
                log.info("--- 第 %d 轮 ---", iteration)

                # 1. 等当前视频结束
                await self.wait_for_video_end()

                # 2. 等页面更新
                await asyncio.sleep(POST_END_DELAY)
                await self.dismiss_popups()

                # 3. 找下一个并点击
                ok, _ = await self.click_and_play_next()
                if not ok:
                    log.warning("没找到下一个，%ds 后重试", RETRY_INTERVAL)
                    await asyncio.sleep(RETRY_INTERVAL)
                    continue

                # 4. 等新视频加载 + 启动播放
                await asyncio.sleep(2)
                await self.dismiss_popups()
                await self.ensure_video_playing()

        except KeyboardInterrupt:
            log.info("用户中断，退出")
        finally:
            if self.playwright:
                await self.playwright.stop()


# ============================================================
# 入口
# ============================================================

async def main():
    player = CourseAutoPlayer()
    await player.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")