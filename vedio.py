import time
import keyboard  # 用于监听快捷键
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging

# --- 配置区 ---
HOTKEY_PLAY_PAUSE = "ctrl+alt+p"
HOTKEY_NEXT_TASK = "ctrl+alt+n"
# 配置日志输出到文件
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding="utf-8",
    filemode='w',  # 'w' 表示覆盖模式，'a' 表示追加模式
)


def get_video_obj():
    """获取视频对象"""
    try:
        return driver.find_element(By.TAG_NAME, "video")
    except:
        return None


def get_video_status():
    """获取视频状态：(当前时间, 总时长, 是否结束, 是否暂停)"""
    video = get_video_obj()
    if video:
        curr = driver.execute_script("return arguments[0].currentTime;", video)
        total = driver.execute_script("return arguments[0].duration;", video)
        ended = driver.execute_script("return arguments[0].ended;", video)
        paused = driver.execute_script("return arguments[0].paused;", video)
        return curr, total, ended, paused
    return 0, 0, False, True


def force_play():
    """强制视频开始播放"""
    video = get_video_obj()
    if video:
        driver.execute_script("arguments[0].play();", video)
        logging.info(f"\n[{time.strftime('%H:%M:%S')}]>>> 已尝试触发播放")


def toggle_play_pause():
    """切换播放与暂停的快捷键函数"""
    video = get_video_obj()
    if video:
        paused = driver.execute_script("return arguments[0].paused;", video)
        if paused:
            driver.execute_script("arguments[0].play();", video)
            logging.warning(f"\n[{time.strftime('%H:%M:%S')}][快捷键] 视频已开始播放")
        else:
            driver.execute_script("arguments[0].pause();", video)
            logging.warning(f"\n[{time.strftime('%H:%M:%S')}][快捷键] 视频已暂停")


def click_and_start_next():
    """手动跳转下一课的快捷键函数"""
    logging.info(f"\n[{time.strftime('%H:%M:%S')}][快捷键] 正在尝试手动跳转并开始下一课...")
    if perform_jump():
        # 跳转后等待加载并自动播放
        time.sleep(5)
        force_play()


def perform_jump():
    """寻找并点击下一个任务按钮"""
    try:
        xpath = "//p[span[text()='开始学习' or text()='继续学习']]"
        next_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        next_btn.click()
        logging.info(f"[{time.strftime('%H:%M:%S')}]已切换到下一个任务")
        return True
    except Exception as e:
        logging.error(f"[{time.strftime('%H:%M:%S')}]未发现可跳转的按钮.异常：")
        logging.error(e)
        return False


# --- 注册快捷键 ---
keyboard.add_hotkey(HOTKEY_PLAY_PAUSE, toggle_play_pause)
keyboard.add_hotkey(HOTKEY_NEXT_TASK, click_and_start_next)

# --- 主循环 ---
# 初始化浏览器
driver = webdriver.Edge()
driver.get("https://basic.sc.smartedu.cn/hd/teacherTraining/home")

print(f"[{time.strftime('%H:%M:%S')}]脚本已就绪！")
print(
    f"[{time.strftime('%H:%M:%S')}]快捷键说明: \n - 播放/暂停: {HOTKEY_PLAY_PAUSE} \n - 跳转下一节: {HOTKEY_NEXT_TASK}")
print("初次播放后请不要手动暂停视频，否则会自动跳转到下一个视频开始播放")
input("请在浏览器中登录后进入播放页并点击播放，然后在此按回车启动自动化监控...")

try:
    while True:
        curr, total, ended, paused = get_video_status()

        # 自动检测播放完毕并跳转
        if paused:
            logging.warning(f"\n[{time.strftime('%H:%M:%S')}]检测到视频结束，先刷新页面...")
            driver.refresh()  # 刷新页面
            time.sleep(5)  # 等待页面加载
            logging.warning(f"[{time.strftime('%H:%M:%S')}]页面刷新完成，开始跳转...")
            if perform_jump():
                time.sleep(5)
                force_play()

        # 打印简单状态
        if total > 0:
            logging.info(f"[{time.strftime('%H:%M:%S')}]进度: {int(curr)}/{int(total)}s | 暂停: {paused} ")

        time.sleep(60)

except KeyboardInterrupt:
    logging.error("\n脚本停止。")
