#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动刷课脚本（Edge + Selenium）
用途：在“视频列表.png”页面中自动点击所有“必学”视频，在新标签页播放至结束后关闭，返回列表继续下一个。

本脚本固定连接由 launch_edge_debug.py 启动的 Edge 远程调试端口（127.0.0.1:9222），
不会自己启动浏览器，避免与已有登录态冲突。

用法示例：
    # 1. 先启动 Edge（由 launch_edge_debug.py 完成）
    python D:/workSpace/python-script/Automatic-play-of-online-lessons/人教教师培训服务平台/launch_edge_debug.py

    # 2. 在打开的 Edge 中登录并导航到课程列表页，然后运行本脚本
    python auto_video_player.py --url "https://xxx.com/course/list"

    # 3. 2倍速播放
    python auto_video_player.py --url "..." --speed 2.0

    # 4. 只扫描列表，不播放
    python auto_video_player.py --url "..." --inspect

    # 5. 手动标记/取消标记已看
    python auto_video_player.py --mark-watched "四年级必学 | 强化育人核心功能..."
    python auto_video_player.py --mark-unwatched "五年级必学 | 做好高年级起步..."
"""

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import (
        TimeoutException,
        WebDriverException,
        NoSuchElementException,
        StaleElementReferenceException,
    )
except ImportError as e:
    print(f"缺少依赖: {e}")
    print("请先安装: pip install selenium")
    sys.exit(1)


DEFAULT_WATCHED_FILE = "watched.json"
WATCH_KEYWORDS = ["观看", "回放", "播放", "进入学习", "去学习", "查看", "立即学习"]


def log(msg: str) -> None:
    """带时间戳的日志。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_watched(path: str) -> Dict[str, dict]:
    """读取已看记录。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        # 兼容旧格式：如果存的是 list，转成 dict
        return {item if isinstance(item, str) else item["id"]: {"title": item} for item in data}
    except Exception as e:
        log(f"读取已看记录失败: {e}，将使用空记录")
        return {}


def save_watched(path: str, watched: Dict[str, dict]) -> None:
    """保存已看记录。"""
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        json.dump(watched, f, ensure_ascii=False, indent=2)


def video_id(title: str) -> str:
    """用标题 MD5 作为唯一 ID，避免标题里的特殊字符影响 JSON key。"""
    return hashlib.md5(title.encode("utf-8")).hexdigest()


def mark_watched(path: str, title: str, watched: bool = True) -> None:
    """手动标记某个视频为已看/未看。"""
    data = load_watched(path)
    vid = video_id(title)
    if watched:
        data[vid] = {"title": title, "watched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        log(f"已标记为已看: {title}")
    else:
        if vid in data:
            del data[vid]
        log(f"已取消已看标记: {title}")
    save_watched(path, data)


def create_driver(edge_driver_path: Optional[str] = None):
    """连接到已由 launch_edge_debug.py 启动的 Edge 远程调试端口。"""
    options = EdgeOptions()
    # 连接远程调试端口时，播放策略等偏好由 launch_edge_debug.py 启动参数决定，
    # 这里主要复用已打开的浏览器实例。
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

    service = None
    if edge_driver_path:
        service = EdgeService(executable_path=edge_driver_path)

    log("正在连接本机 127.0.0.1:9222 的 Edge...")
    driver = webdriver.Edge(service=service, options=options)
    log(f"已连接，当前窗口: {driver.title}")
    return driver


def scroll_to_load_all(driver, pause: float = 1.5) -> None:
    """滚动到页面底部，触发懒加载，确保所有视频卡片都渲染出来。"""
    log("滚动页面以加载全部内容...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    attempts = 0
    while attempts < 10:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        attempts += 1
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)


def find_required_videos(driver) -> List[dict]:
    """
    在列表页中查找所有“必学”视频。
    返回: [{"id": str, "title": str, "href": str, "buttonText": str}, ...]
    """
    scroll_to_load_all(driver)

    # 根据实际 HTML 结构：table > tr > td.txt_pxkc_xk（年级/必学）+
    # td.txt_pxkc_pxxx（课程标题）+ td.txt_pxrk（观看回放链接）。
    # 一年级标签使用 rowspan，因此需要按行遍历并跟踪当前“必学”分组。
    script = """
        const rows = Array.from(document.querySelectorAll('.con_table_pxkc_gztb2020b table tr, table tr'));
        const cards = [];
        const seenTitles = new Set();
        let currentGrade = '';
        let currentRequired = false;
        let autoId = 0;

        rows.forEach(tr => {
            // 更新当前年级/必学分组（处理 rowspan）
            const gradeCell = tr.querySelector('td.txt_pxkc_xk, .txt_pxkc_xk');
            if (gradeCell) {
                const gradeText = (gradeCell.innerText || '').trim().replace(/\\s+/g, ' ');
                if (gradeText) {
                    currentGrade = gradeText;
                    currentRequired = currentGrade.includes('必学');
                }
            }

            // 只收集“必学”分组下的行
            if (!currentRequired) return;

            // 查找该行的播放链接：txt_pxrk 里的 a 标签
            const actionCell = tr.querySelector('td.txt_pxrk, .txt_pxrk');
            if (!actionCell) return;

            const link = actionCell.querySelector('a[href*="entryRoom"], a');
            if (!link || !link.href) return;

            // 提取课程标题与时间/讲师
            const titleCell = tr.querySelector('td.txt_pxkc_pxxx, .txt_pxkc_pxxx');
            let courseTitle = '';
            let meta = '';
            if (titleCell) {
                const h6 = titleCell.querySelector('h6');
                courseTitle = h6 ? (h6.innerText || '').trim() : (titleCell.innerText || '').trim().split('\\n')[0].trim();
                const spans = Array.from(titleCell.querySelectorAll('span')).map(s => (s.innerText || '').trim()).filter(Boolean);
                meta = spans.join(' ');
            }

            const title = [currentGrade, courseTitle, meta].filter(Boolean).join(' | ').replace(/\\s+/g, ' ');
            if (!title || seenTitles.has(title)) return;
            seenTitles.add(title);

            link.setAttribute('data-auto-video-id', autoId.toString());
            cards.push({
                id: autoId.toString(),
                title: title,
                href: link.href,
                buttonText: (link.innerText || '').trim() || '观看回放'
            });
            autoId++;
        });
        return cards;
    """
    return driver.execute_script(script)


def wait_for_new_tab(driver, original_handles: List[str], timeout: int = 30) -> str:
    """等待新标签页打开并切换到新标签页。"""
    log(f"等待新标签页打开，当前窗口数: {len(original_handles)}")
    WebDriverWait(driver, timeout).until(EC.number_of_windows_to_be(len(original_handles) + 1))
    new_handles = [h for h in driver.window_handles if h not in original_handles]
    if not new_handles:
        raise RuntimeError("未检测到新窗口/标签页")
    new_handle = new_handles[0]
    driver.switch_to.window(new_handle)
    log(f"已切换到新标签页: {driver.title} (handle={new_handle[:16]}...)")
    return new_handle

def set_video_speed(driver, speed: float) -> bool:
    """
    设置播放倍速。
    优先通过播放器自定义倍速 UI（#volumeMultiple + .multipleCont）设置；
    失败时回退到直接修改 HTML5 video.playbackRate。
    """
    speed_label = f"{speed:g}X"  # 例如 2.0 -> "2X" 或 "2.0X"
    speed_label_alt = f"{speed:.1f}X"  # "2.0X"

    # 1. 尝试通过播放器 UI 设置
    try:
        # 打开倍速下拉菜单
        open_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "volumeMultiple"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", open_btn)
        driver.execute_script("arguments[0].click();", open_btn)
        log("已打开倍速选择菜单")
        time.sleep(0.5)

        # 查找目标倍速选项：.multipleCont a 下的 span 文本匹配 speedX
        options = driver.find_elements(By.CSS_SELECTOR, ".multipleCont a")
        for opt in options:
            spans = opt.find_elements(By.TAG_NAME, "span")
            for span in spans:
                text = (span.text or "").strip()
                if text == speed_label or text == speed_label_alt:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", opt)
                    driver.execute_script("arguments[0].click();", opt)
                    log(f"已通过播放器 UI 设置倍速为 {speed}x")
                    return True
        log(f"未在倍速菜单中找到 {speed_label}/{speed_label_alt}，尝试 JS 设置")
    except Exception as e:
        log(f"通过 UI 设置倍速失败，尝试 JS 设置: {e}")

    # 2. 兜底：直接修改 video.playbackRate
    try:
        video = driver.find_element(By.TAG_NAME, "video")
        driver.execute_script("""
            const v = arguments[0];
            v.playbackRate = arguments[1];
        """, video, speed)
        log(f"已通过 JS 设置倍速为 {speed}x")
        return True
    except Exception as e:
        log(f"JS 设置倍速也失败: {e}")
    return False


def wait_video_end(driver, speed: float, poll_interval: float = 2.0, max_wait: int = 0) -> bool:
    """
    等待 HTML5 video 播放结束。
    返回 True 表示正常结束；False 表示超时或无法判断。
    """
    # 等待 video 元素出现
    try:
        video = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "video"))
        )
    except TimeoutException:
        log("未在页面中找到 <video> 元素，可能不是标准 HTML5 播放器")
        return False

    # 设置倍速、静音、尝试播放（防止某些播放器未自动播放）
    set_video_speed(driver, speed)
    try:
        driver.execute_script("""
            const v = arguments[0];
            v.muted = true;
            if (v.paused) {
                v.play().catch(e => console.log('play error', e));
            }
        """, video)
    except WebDriverException as e:
        log(f"静音/播放失败: {e}")

    start = time.time()
    last_current = 0.0
    stall_count = 0
    last_log_time = 0.0

    while True:

        try:
            state = driver.execute_script("""
                const v = arguments[0];
                return {
                    ended: v.ended,
                    paused: v.paused,
                    duration: v.duration || 0,
                    currentTime: v.currentTime || 0,
                    readyState: v.readyState,
                    playbackRate: v.playbackRate
                };
            """, video)
        except StaleElementReferenceException:
            log("视频元素已失效，尝试重新查找...")
            try:
                video = driver.find_element(By.TAG_NAME, "video")
                continue
            except NoSuchElementException:
                return False

        ended = state.get("ended", False)
        duration = state.get("duration", 0)
        current = state.get("currentTime", 0)
        paused = state.get("paused", True)

        if ended:
            log(f"视频已播放结束 (duration={duration:.1f}s)")
            return True

        if duration > 0 and current > 0 and (duration - current) < 2:
            log(f"视频即将结束 (current={current:.1f}s, duration={duration:.1f}s)")
            return True

        # 进度卡死检测：如果连续 30 秒 currentTime 没变化，认为播放异常
        if abs(current - last_current) < 0.5 and not paused:
            stall_count += 1
            if stall_count >= 15:
                log("视频进度长时间未更新，可能卡死，结束当前视频")
                return False
        else:
            stall_count = 0
            last_current = current

        if max_wait > 0 and (time.time() - start) > max_wait:
            log(f"超过最大等待时间 {max_wait}s，结束当前视频")
            return False

        # 每 30 秒输出一次进度
        if time.time() - last_log_time >= 30:
            log(f"播放中... {current:.1f}s / {duration:.1f}s ({'已暂停' if paused else '播放中'})")
            last_log_time = time.time()

        time.sleep(poll_interval)


def dismiss_end_dialog(driver, timeout: int = 5) -> bool:
    """
    点击视频播放结束后弹出的“本视频已播放结束”弹窗中的“我知道了”按钮。
    返回是否成功关闭弹窗。
    """
    try:
        # 等待弹窗中的“我知道了”按钮出现
        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".stop-reporting-bg .stop-reporting .stop-reporting-btn"))
        )
        # 额外校验按钮文本是否包含“我知道了”，避免误点
        if "我知道了" not in (btn.text or "").strip():
            # 尝试用 XPath 按文本重新定位
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), '我知道了')]"))
            )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        log("已点击“我知道了”关闭播放结束弹窗")
        time.sleep(0.5)
        return True
    except TimeoutException:
        log("未检测到播放结束弹窗，继续下一步")
    except Exception as e:
        log(f"关闭播放结束弹窗失败: {e}")
    return False


def close_current_tab_and_return(driver, list_handle: str) -> None:
    """关闭当前标签页并切回列表页。"""
    try:
        driver.close()
    except WebDriverException:
        pass
    driver.switch_to.window(list_handle)


def auto_play(args) -> None:
    """主流程：自动刷课。"""
    watched = load_watched(args.watched_file)

    driver = create_driver(edge_driver_path=args.edge_driver)

    try:
        # 等待页面加载
        log("等待页面加载...")
        time.sleep(3)

        # 确保当前在列表页
        if driver.current_url != args.url:
            log(f"当前不在目标 URL，正在导航到: {args.url}")
            driver.get(args.url)
            time.sleep(3)

        # 扫描列表
        log("正在扫描“必学”视频...")
        videos = find_required_videos(driver)
        if not videos:
            log("未找到任何“必学”视频。可能页面未加载完或选择器不匹配。")
            log("建议先用 --inspect 检查页面结构，或按 F12 查看“必学”卡片和按钮的 HTML 结构。")
            return

        log(f"共找到 {len(videos)} 个“必学”视频")

        if args.inspect:
            for idx, v in enumerate(videos, 1):
                status = "已看" if video_id(v["title"]) in watched else "未看"
                print(f"{idx}. [{status}] {v['title']} (按钮: {v['buttonText']})")
            return

        list_handle = driver.current_window_handle

        for idx, v in enumerate(videos, 1):
            vid = video_id(v["title"])
            if vid in watched:
                log(f"跳过已看视频 ({idx}/{len(videos)}): {v['title']}")
                continue

            if idx < args.start_from:
                log(f"跳过 (start-from): {v['title']}")
                continue

            log(f"准备播放 ({idx}/{len(videos)}): {v['title']}")

            if args.dry_run:
                log("[模拟运行] 不会真正点击")
                continue

            opened_in_same_tab = False
            try:
                # 直接用 window.open 在后台打开播放链接的新标签页。
                # 比点击 <a> 更可靠：不依赖页面的 click 事件处理器，也能绕过被遮挡等问题。
                log(f"正在打开播放页: {v['href']}")
                original_handles = driver.window_handles
                driver.execute_script("window.open(arguments[0], '_blank');", v['href'])
                log("已发送打开新标签页命令，等待新标签页...")

                # 等待新标签页
                try:
                    wait_for_new_tab(driver, original_handles)
                    log(f"已进入播放页: {driver.title}")
                except TimeoutException:
                    log("未检测到新标签页，可能弹窗被拦截，改为在当前标签页打开...")
                    driver.get(v['href'])
                    log(f"已在当前页打开播放页: {driver.title}")
                    opened_in_same_tab = True

                # 等待视频播放结束
                finished = wait_video_end(driver, args.speed, max_wait=args.max_wait)

                # 如果视频正常结束，页面会弹出“本视频已播放结束”提示，先点击“我知道了”
                if finished:
                    dismiss_end_dialog(driver)

                if opened_in_same_tab:
                    # 当前标签页打开的，直接回到列表页
                    log("返回列表页...")
                    driver.get(args.url)
                else:
                    # 关闭播放标签并返回列表
                    close_current_tab_and_return(driver, list_handle)

                if finished or args.mark_on_attempt:
                    # 标记为已看（即使超时，默认也标记，因为脚本确实“看”过了）
                    watched[vid] = {"title": v["title"], "watched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                    save_watched(args.watched_file, watched)
                    log(f"已标记为已看: {v['title']}")
                else:
                    log(f"未完成，暂不标记: {v['title']}")

                # 返回列表页后稍等，让页面稳定
                time.sleep(1)

            except Exception as e:
                log(f"播放失败: {v['title']}，错误类型: {type(e).__name__}")
                log(f"错误详情: {repr(e)}")
                traceback.print_exc()
                # 尝试回到列表页
                try:
                    if driver.current_window_handle != list_handle:
                        if opened_in_same_tab:
                            driver.get(args.url)
                        else:
                            driver.close()
                    driver.switch_to.window(list_handle)
                except Exception:
                    pass
                if args.continue_on_error:
                    continue
                else:
                    raise

        log("所有“必学”视频处理完毕。")

    finally:
        # 连接的是用户已打开的 Edge，脚本只关闭 WebDriver 会话，不关闭浏览器窗口
        log("脚本结束，保留 Edge 浏览器窗口。")


def main() -> None:
    parser = argparse.ArgumentParser(description="自动刷课脚本（Edge + Selenium，仅连接 launch_edge_debug.py 启动的浏览器）")
    parser.add_argument("--url", required=False, help="视频列表页 URL")
    parser.add_argument("--watched-file", default=DEFAULT_WATCHED_FILE, help="已看记录文件路径（默认 watched.json）")
    parser.add_argument("--speed", type=float, default=1.0, help="播放倍速，例如 2.0（默认 1.0）")
    parser.add_argument("--edge-driver", default=None, help="msedgedriver.exe 路径（留空让 Selenium 自动下载）")
    parser.add_argument("--inspect", action="store_true", help="只扫描列表，不播放")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行：列出会点击的视频，但不真正点击")
    parser.add_argument("--start-from", type=int, default=1, help="从第 N 个视频开始播放")
    parser.add_argument("--max-wait", type=int, default=0, help="单个视频最大等待秒数，0 表示不限制")
    parser.add_argument("--mark-watched", metavar="TITLE", help="手动标记指定标题为已看")
    parser.add_argument("--mark-unwatched", metavar="TITLE", help="手动标记指定标题为未看")
    parser.add_argument("--mark-on-attempt", action="store_true", default=True,
                        help="视频尝试播放后即使超时也标记为已看（默认开启）")
    parser.add_argument("--no-mark-on-attempt", dest="mark_on_attempt", action="store_false",
                        help="只有正常播完才标记为已看")
    parser.add_argument("--continue-on-error", action="store_true", default=True,
                        help="单个视频出错后继续下一个（默认开启）")
    parser.add_argument("--stop-on-error", dest="continue_on_error", action="store_false",
                        help="单个视频出错后停止")

    args = parser.parse_args()

    # 手动标记模式
    if args.mark_watched:
        mark_watched(args.watched_file, args.mark_watched, watched=True)
        return
    if args.mark_unwatched:
        mark_watched(args.watched_file, args.mark_unwatched, watched=False)
        return

    if not args.url:
        parser.error("除 --mark-watched/--mark-unwatched 外，必须提供 --url")

    auto_play(args)


if __name__ == "__main__":
    main()
