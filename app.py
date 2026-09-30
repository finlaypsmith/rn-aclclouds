#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

# ----- ddddocr 惰性加载：用于识别人机验证码 option 图上的文字 -----
# ACLClouds 的点选验证码选项是图片，且 aria-label/alt 均无可读文字，
# 文字匹配恒不命中。ddddocr 可 OCR 出 option 图里的品牌/服务名（如
# "panel"/"serveur"/"discord"），再与挑战提示的目标词匹配。
# 惰性加载 + 失败回退：未安装时不影响原有验证码流程。
_DDDDOCR = None
def _get_ocr():
    global _DDDDOCR
    if _DDDDOCR is None:
        try:
            import ddddocr
            _DDDDOCR = ddddocr.DdddOcr(ocr=True, beta=True, show_ad=False)
        except Exception as e:
            print(f"ddddocr 初始化失败，验证码将回退到盲点模式: {e}")
            _DDDDOCR = False  # 标记为不可用，避免每次重试
    return _DDDDOCR or None

def ocr_option_text(element):
    """OCR 一个验证码 option 元素，返回识别到的文字（小写）。失败返回 ''。"""
    ocr = _get_ocr()
    if not ocr:
        return ''
    try:
        # 优先取 option 内 <img> 的截图字节，最适合 ddddocr 直接识别
        try:
            img = element.find_element(By.TAG_NAME, 'img')
            png = img.screenshot_as_png
        except Exception:
            # 退路：对整个 option 元素截图
            png = element.screenshot_as_png
        if not png:
            return ''
        text = ocr.classification(png)
        return (text or '').strip().lower()
    except Exception as e:
        print(f"OCR option 失败: {e}")
        return ''

def match_option_by_text(options, target):
    """在三源文字（option.text / img.alt / aria-label）里匹配 target。
    命中返回该 option，否则返回 None。"""
    if not target:
        return None
    target_lower = target.lower()
    for opt in options:
        opt_text = (opt.text or '').strip()
        if not opt_text:
            try:
                img = opt.find_element(By.TAG_NAME, 'img')
                opt_text = (img.get_attribute('alt') or '').strip()
            except Exception:
                pass
        if not opt_text:
            try:
                opt_text = (opt.get_attribute('aria-label') or '').strip()
            except Exception:
                pass
        if opt_text and target_lower in opt_text.lower():
            return opt
    return None

def match_option_by_ocr(options, target):
    """当三源文字匹配不到时，用 ddddocr 对各 option 图 OCR 后匹配 target。
    命中返回该 option，否则返回 None。"""
    if not target:
        return None
    target_lower = target.lower().strip()
    ocr = _get_ocr()
    if not ocr:
        return None
    for opt in options:
        ocr_text = ocr_option_text(opt)
        if ocr_text and target_lower in ocr_text:
            print(f"OCR 命中: target={target!r}, option 文字='{ocr_text}'")
            return opt
    return None

# ----- 配置（从环境变量读取或在双引号内填写） -----
EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'

def beijing_time_str():
    try:
        return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

def send_telegram(message):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {'chat_id': TG_CHAT_ID, 'text': message}
        try:
            requests.post(url, data=data, timeout=10)
            print(f"Telegram sent: {message[:50]}...")
        except Exception as e:
            print(f"Failed to send Telegram: {e}")
    else:
        print(f"[Telegram disabled] {message}")

def wait_for_url_change(sb, original_url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_url = sb.get_current_url()
        if current_url != original_url:
            return True
        sb.sleep(0.5)
    raise Exception(f"等待 URL 变化超时 ({timeout}秒)，当前仍为: {original_url}")

def is_login_page(sb):
    return LOGIN_PATH in sb.get_current_url()

def is_logged_in(sb):
    # 必须在 dashboard 路径才算已登录：落地页（如法语 /fr/）同样属于
    # BASE_URL 且不含 /auth/login，不能据此认为已登录
    current_url = sb.get_current_url()
    return BASE_URL in current_url and '/dashboard' in current_url and LOGIN_PATH not in current_url

def scroll_to_selector(sb, selector):
    sb.scroll_to(selector)
    sb.sleep(0.2)

def safe_click_element(sb, element, label):
    try:
        sb.driver.execute_script(
            'arguments[0].scrollIntoView({block: "center", inline: "center"});',
            element,
        )
        sb.sleep(0.5)

        try:
            element.click()
            return True
        except (ElementClickInterceptedException, WebDriverException, StaleElementReferenceException) as e:
            print(f"{label} 普通点击失败，改用 JavaScript 点击: {e}")

        sb.driver.execute_script('arguments[0].click();', element)
        sb.sleep(0.5)
        return True
    except StaleElementReferenceException:
        print(f"{label} 元素已失效，点击前需要重新定位")
        return False

def element_text(element):
    try:
        return element.text.strip()
    except Exception:
        return ''

def unique_elements(elements):
    unique = []
    seen = set()
    for element in elements:
        element_id = getattr(element, 'id', None)
        if element_id and element_id in seen:
            continue
        if element_id:
            seen.add(element_id)
        unique.append(element)
    return unique

# 站点按 IP / 浏览器语言切换文案，按钮和提示语都不固定，统一用多语言匹配。
RENEW_LABEL_PATTERN = re.compile(
    r'renew|renouvel|erneuern|renovar|rinnov|продлить|reactivate|续期|延长|重新激活',
    re.I,
)

# 2026-09 版续期人机验证弹窗：固定带这个 aria-labelledby，与语言无关
RENEW_CAPTCHA_DIALOG = 'div[role="dialog"][aria-labelledby="renew-captcha-title"]'

# 服务行里这些行不是服务名：状态/续期方式/过期提示等
NOISE_LINE_PATTERN = re.compile(
    r'renew|renouvel|expires|expiry|expire|suspended|valid|automatic|active|custom name|'
    r'service id|effective date|renewal|detail|support|invoice|cancel|storage|cpu|ram|'
    r'续期|重新激活|恢复|暂停|过期|到期|自动|有效',
    re.I,
)

def find_renew_buttons(root):
    """在服务行里找「续期」按钮。

    2026-09 表格版：续期按钮在行展开面板的操作区，文案随语言变化
    （Renew / Renouveler / Erneuern / Renovar / Rinnova / Продлить），
    并且只有到了可续期时间才渲染出来 —— 找不到即表示还没到续期时间。
    """
    try:
        candidates = root.find_elements(By.CSS_SELECTOR, 'button, a')
    except Exception:
        return []

    buttons = []
    for button in candidates:
        try:
            label = ' '.join(filter(None, [
                element_text(button),
                (button.get_attribute('aria-label') or ''),
                (button.get_attribute('title') or ''),
            ]))
        except Exception:
            continue
        if RENEW_LABEL_PATTERN.search(label):
            buttons.append(button)

    return unique_elements(buttons)

def find_project_rows(sb):
    """定位项目/服务行。

    2026-09 表格版：每行是 main 下的 <article data-service-id="...">。
    行内 class 带构建哈希（ProjectsPage-module_xxx）不稳定，只认 data-service-id。
    """
    return sb.driver.find_elements(By.CSS_SELECTOR, 'main article[data-service-id]')

def find_row_by_service_id(sb, service_id):
    rows = sb.driver.find_elements(
        By.CSS_SELECTOR, f'main article[data-service-id="{service_id}"]'
    )
    return rows[0] if rows else None

def get_service_id(row):
    try:
        return row.get_attribute('data-service-id') or ''
    except Exception:
        return ''

def expand_service_rows(sb):
    """展开所有服务行 —— 过期倒计时和续期按钮都在折叠面板里。"""
    sb.driver.execute_script(
        '''
        document.querySelectorAll('main article[data-service-id] button[aria-expanded]')
          .forEach(btn => {
            if (btn.getAttribute('aria-expanded') === 'false') btn.click();
          });
        '''
    )

def extract_date_like(text):
    if not text:
        return ''
    patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''

def extract_duration_like(text):
    if not text:
        return ''

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        # "Expires in" 标签随语言变化：Expire dans / Läuft ab in / Expira en / ...
        if re.search(
            r'expires\s+in|expire\s+dans|l[äa]uft\s+ab\s+in|expira\s+en|истекает\s+через|'
            r'scade\s+tra|expira\s+em|剩余|还有',
            line,
            re.I,
        ) and idx + 1 < len(lines):
            return f"{line} {lines[idx + 1]}"

    match = re.search(
        r'(?:expires\s+in\s*)?\d+\s*(?:d|day|days|j|天|日)\s*\d*\s*(?:h|hour|hours|小时)?',
        text,
        re.I,
    )
    if match:
        return match.group(0).strip()

    match = re.search(r'\d+\s*(?:h|hour|hours|小时)', text, re.I)
    if match:
        return match.group(0).strip()

    return ''

def row_text_lines(row):
    """服务行的文本片段，按 DOM 顺序取叶子元素的文本。

    不用 Selenium 的 element.text：它按渲染后的换行切分，站点一改布局就会
    把相邻字段粘成一行（service id 会和日期粘出 "4410/04/2026"）。叶子文本
    与 CSS 无关，天然一段一个字段。
    """
    try:
        return row.parent.execute_script(
            '''
            return Array.from(arguments[0].querySelectorAll('*'))
              .filter(el => !el.children.length && !/^(script|style)$/i.test(el.tagName))
              .map(el => (el.textContent || '').trim())
              .filter(Boolean);
            ''',
            row,
        ) or []
    except Exception:
        return []

def get_project_name(row, idx):
    # 2026-09 表格版：服务行第一行是套餐/机型名（如 "bot-free"），
    # 展开面板里另有 "Custom name: appa"。取第一行有效文本作为名称。
    for line in row_text_lines(row):
        if len(line) > 80:
            continue
        if extract_date_like(line) or extract_duration_like(line):
            continue
        if NOISE_LINE_PATTERN.search(line):
            continue
        return line
    return f"项目 #{idx}"

def get_project_expiry(row):
    # 2026-09 表格版：折叠行的 EXPIRY 列是绝对日期（如 10/04/2026），
    # 展开面板里是倒计时（"Expires in 4j 6h"）。优先日期，其次倒计时。
    lines = row_text_lines(row)
    for line in lines:
        date_text = extract_date_like(line)
        if date_text:
            return date_text
    for line in lines:
        duration_text = extract_duration_like(line)
        if duration_text:
            return duration_text
    return '未知'

def wait_for_renew_result(sb, service_id, old_expiry, known_lines=(), timeout=60):
    """等待续期结果。

    站点提示语随界面语言变化，所以不匹配固定文案，改用与语言无关的判据：
    续期成功后服务行的 Renew 按钮会消失、过期时间会前移。
    行内新出现的文本（Renewing... / 成功或失败提示）作为 result_note 带出去。
    """
    known = set(known_lines)
    appeared = []
    stable_hits = 0
    start_time = time.time()

    while time.time() - start_time < timeout:
        row = find_row_by_service_id(sb, service_id)
        if row is None:
            sb.sleep(1)
            continue

        for line in row_text_lines(row):
            if line not in known and line not in appeared:
                appeared.append(line)

        if not find_renew_buttons(row):
            # 连续两次都看不到 Renew 按钮才判定，避开 React 重渲染的中间态
            stable_hits += 1
            if stable_hits >= 2:
                new_expiry = get_project_expiry(row)
                note = ' / '.join(appeared) or 'Renew 按钮已消失'
                return True, new_expiry, note
        else:
            stable_hits = 0

        sb.sleep(1)

    row = find_row_by_service_id(sb, service_id)
    expiry = get_project_expiry(row) if row else '未知'
    return False, expiry, ' / '.join(appeared)

def get_action_button_label(button):
    text = element_text(button)
    lowered = text.lower()
    if 'reactivate' in lowered or '重新激活' in text or '恢复' in text:
        return 'Reactivate'
    return 'Renew'

def log_projects_page_diagnostics(sb):
    current_url = sb.get_current_url()
    title = sb.get_title()
    body_text = ''
    try:
        body_text = sb.driver.find_element(By.TAG_NAME, 'body').text.strip()
    except Exception:
        pass
    print(f"项目页诊断 URL: {current_url}")
    print(f"项目页诊断标题: {title}")
    print(f"项目页可见文本摘要: {body_text[:1200]}")

def handle_renew_antibot(sb, project_name, timeout=10):
    """Renew 之后如果弹出 Anti-bot confirmation，就完成弹窗内的 cap-widget 验证。

    2026-09 版弹窗固定带 aria-labelledby="renew-captcha-title"，
    用它定位与界面语言无关（弹窗标题会随语言变成 "Confirmation anti-robot" 等）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            dialogs = sb.driver.find_elements(By.CSS_SELECTOR, RENEW_CAPTCHA_DIALOG)
        except Exception:
            dialogs = []
        if any(dialog.is_displayed() for dialog in dialogs):
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=45)
        sb.sleep(0.5)

    print(f"[{project_name}] 未检测到续期人机验证窗口，继续等待续期结果")
    return False

def read_cap_state(sb):
    """读取 <cap-widget> 的状态；组件未渲染时返回 None。

    2026-09 改版后站点的人机验证换成自托管 Cap（proof-of-work）组件
    <cap-widget>，内部结构在 shadow DOM 里，Selenium 的 CSS/XPath 查询
    无法穿透，只能走 JS。
    state: '' 待点击 / 'verifying' 计算中 / 'done' 已完成 / 'error' 失败
    token: 隐藏域 cap-token 的值，非空即代表验证通过
    """
    return sb.driver.execute_script(
        '''
        const widgets = Array.from(document.querySelectorAll('cap-widget'));
        const widget = widgets.find(w => w.offsetWidth || w.offsetHeight) || widgets[0];
        if (!widget) return null;
        const root = widget.shadowRoot;
        const box = root ? root.querySelector('.captcha') : null;
        const tokenInput = widget.querySelector('input[name="cap-token"]');
        return {
          state: box ? (box.getAttribute('data-state') || '') : '',
          token: tokenInput ? (tokenInput.value || '') : '',
        };
        '''
    )

def trigger_cap_widget(sb):
    """派发一次点击到 <cap-widget> 的复选框，返回是否成功触发。"""
    return bool(sb.driver.execute_script(
        '''
        const widgets = Array.from(document.querySelectorAll('cap-widget'));
        const widget = widgets.find(w => w.offsetWidth || w.offsetHeight) || widgets[0];
        if (!widget || !widget.shadowRoot) return false;
        const target = widget.shadowRoot.querySelector('.captcha-trigger')
                    || widget.shadowRoot.querySelector('.captcha');
        if (!target) return false;
        target.click();
        return true;
        '''
    ))

def click_captcha_checkbox(sb, label='验证码', timeout=30):
    """完成 ACLClouds 页面的人机验证。

    主路径是 <cap-widget>：先在 shadow DOM 里点一次复选框，再轮询
    cap-token 是否生成（PoW 计算 + 请求 cap 服务需要数秒）。
    页面确实没有 cap-widget 时，才回退到旧版复选框实现。
    """
    # 等 cap-widget 渲染出来，再决定走哪条路径
    deadline = time.time() + min(timeout, 15)
    while time.time() < deadline and read_cap_state(sb) is None:
        sb.sleep(0.3)

    if read_cap_state(sb) is None:
        if sb.is_element_present('div.auth-captcha-inner[role="checkbox"]'):
            return click_legacy_captcha_checkbox(sb, label, timeout)
        print(f"{label} 页面上没有找到人机验证组件（cap-widget）")
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_cap_state(sb) or {}
        if state.get('token'):
            print(f"{label} 验证通过")
            return True
        if state.get('state') == 'verifying':
            # 已在计算 PoW，别重复点击，等结果
            sb.sleep(0.5)
            continue
        if not trigger_cap_widget(sb):
            print(f"{label} 未能触发 cap-widget 点击")
            return False
        print(f"{label} 已点击人机验证，等待校验结果...")
        sb.sleep(1)

    print(f"{label} 等待人机验证结果超时（{timeout} 秒）")
    return False

def click_legacy_captcha_checkbox(sb, label='验证码', timeout=10):
    """旧版人机验证实现：点击 [role="checkbox"] 并处理图形点选挑战。

    站点 2026-09 改版后已不再使用，仅在页面没有 cap-widget 时回退。
    """
    selectors = [
        'div.auth-captcha-inner[role="checkbox"]',
        '//div[contains(., "Anti-bot confirmation")]//*[@role="checkbox"]',
        '//div[contains(., "I am not a robot")]//*[@role="checkbox"]',
        '//div[contains(@class, "modal") and contains(., "Secured by ACLClouds")]//*[@role="checkbox"]',
    ]

    last_error = None
    clicked = False
    selector = None
    for candidate in selectors:
        try:
            sb.wait_for_element_visible(candidate, timeout=timeout)
            scroll_to_selector(sb, candidate)
            sb.uc_click(candidate)
            sb.sleep(1)
            selector = candidate
            clicked = True
            break
        except Exception as e:
            last_error = e
            continue

    if not clicked:
        print(f"{label} 点击复选框失败: {last_error}")
        return False

    # 这里给 5 秒的加载缓冲，避免图形验证码尚未渲染完成时就开始点击
    sb.sleep(5)
    captcha_ok = handle_captcha_challenge(sb, label, timeout=20)
    if not captcha_ok:
        print(f"{label} 验证流程未完成，等待状态仍未确认。")
        return False

    # 验证复选框是否已勾选
    try:
        checked = sb.get_attribute(selector, 'aria-checked')
        if checked == 'true':
            print(f"{label} 验证通过")
            return True
        else:
            print(f"{label} 验证未完成，当前状态: {checked}")
            return False
    except Exception:
        return False

def handle_captcha_challenge(sb, label='验证码', timeout=20):
    """处理图形验证码挑战：先等待挑战加载，再尝试点击对应图像。"""
    start_time = time.time()
    challenge = None
    last_error = None
    challenge_selectors = [
        '.auth-captcha-challenge',
        '.auth-capcha-challenge',
        '//*[contains(@class, "captcha") and contains(@class, "challenge")]',
        '//*[contains(@aria-label, "Click on ") or contains(@aria-label, "Select ") or contains(@class, "challenge")]',
    ]

    def get_challenge():
        for selector in challenge_selectors:
            try:
                if selector.startswith('/'):
                    elems = sb.driver.find_elements(By.XPATH, selector)
                    for elem in elems:
                        if elem.is_displayed():
                            return elem
                else:
                    elem = sb.wait_for_element_visible(selector, timeout=1)
                    if elem and elem.is_displayed():
                        return elem
            except Exception:
                continue
        return None

    while time.time() - start_time < timeout:
        challenge = get_challenge()
        if challenge:
            print(f"{label} 检测到图形验证码挑战")
            break
        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass
        sb.sleep(0.3)

    if not challenge:
        print(f"{label} 等待验证码挑战加载超时: {last_error}")
        return False

    target = ''
    try:
        prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
        target = prompt.text.strip()
    except Exception:
        pass
    if not target:
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-capcha-prompt strong')
            target = prompt.text.strip()
        except Exception:
            pass
    if not target:
        aria_label = challenge.get_attribute('aria-label') or ''
        if 'Click on ' in aria_label:
            target = aria_label.split('Click on ')[-1].strip()

    print(f"{label} 目标文本: {target or '未识别'}")

    option_selectors = [
        '.auth-captcha-option',
        '.auth-capcha-option',
        './/button',
        './/a',
        './/div[@role="button"]',
    ]

    def get_options(challenge_elem):
        for sel in option_selectors:
            try:
                if sel.startswith('.') or sel.startswith('['):
                    elems = challenge_elem.find_elements(By.CSS_SELECTOR, sel)
                else:
                    elems = challenge_elem.find_elements(By.XPATH, sel)
                if elems:
                    return [elem for elem in elems if elem.is_displayed() and elem.is_enabled()]
            except Exception:
                continue
        return []

    options = get_options(challenge)
    if not options:
        print(f"{label} 未找到可点击的选项")
        return False

    matched = None
    if target:
        matched = match_option_by_text(options, target)
        if matched is None:
            # 文字 attr 全不命中（多为纯图片 option）→ OCR 兜底
            matched = match_option_by_ocr(options, target)
            if matched is None:
                print(f"{label} 首次匹配均未命中目标 {target!r}，进入重试循环")

    attempts = 0
    max_attempts = 8
    while attempts < max_attempts:
        challenge = get_challenge()
        if not challenge:
            return False

        options = get_options(challenge)
        if not options:
            print(f"{label} 当前挑战没有可点击选项，重试中...")
            attempts += 1
            sb.sleep(0.8)
            continue

        current_target = ''
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
            current_target = prompt.text.strip()
        except Exception:
            pass
        if not current_target:
            aria_label = challenge.get_attribute('aria-label') or ''
            if 'Click on ' in aria_label:
                current_target = aria_label.split('Click on ')[-1].strip()

        candidate = None
        if target and current_target and current_target.lower() == target.lower():
            candidate = match_option_by_text(options, target)
            if candidate is None:
                candidate = match_option_by_ocr(options, target)

        if candidate is None:
            # 连续 8 次都匹配不到 → 兜底点第一个（与原有行为一致），并提示
            print(f"{label} 多源匹配未命中目标 {current_target or target!r}，兜底点 options[0]")
            candidate = options[0]

        print(f"{label} 点击候选选项 #{attempts + 1} ...")
        clicked = safe_click_element(sb, candidate, f"{label} 选项候选")
        if not clicked:
            attempts += 1
            sb.sleep(0.8)
            continue

        sb.sleep(1.2)

        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass

        if not get_challenge():
            print(f"{label} 挑战已消失，验证完成")
            return True

        attempts += 1

    print(f"{label} 多次尝试后仍未完成验证码")
    return False

def mask_email(email):
    if not email or '@' not in email:
        return email or ''

    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = local[0] + '****' if local else '****'
    elif len(local) <= 4:
        masked_local = f"{local[0]}****{local[-1]}"
    else:
        masked_local = f"{local[:2]}****{local[-2:]}"
    return f"{masked_local}@{domain}"

def build_success_message(project_name, old_expiry, new_expiry):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "✅ 续期成功",
        f"⏱️ 新过期时间: {new_expiry}",
        f"👤 登录账户: {masked_email}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ]
    return "\n".join(lines)

def build_not_yet_due_message(project_name, expiry):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "⏳ 未到续期时间",
        f"⏱️ 当前过期时间: {expiry}",
        f"👤 登录账户: {masked_email}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ]
    return "\n".join(lines)

def build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        f"❌ 续期状态未确认: {project_name}",
        f"👤 登录账户: {masked_email}",
    ]
    if old_expiry and old_expiry.lower() not in ['suspended', 'paused', '暂停']:
        lines.append(f"旧过期: {old_expiry}")
    lines.extend([
        f"当前过期: {new_expiry}",
        f"页面提示: {result_note or '未发现成功提示'}",
    ])
    return "\n".join(lines)

def js_set_input_value(sb, selector, value):
    sb.execute_script(
        '''
        const el = document.querySelector(arguments[0]);
        if (!el) return false;
        el.focus();
        el.value = arguments[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
        ''',
        selector,
        value,
    )

def fill_input(sb, selector, value, label, timeout=15):
    sb.wait_for_element_visible(selector, timeout=timeout)
    scroll_to_selector(sb, selector)
    sb.click(selector)
    sb.clear(selector)
    sb.type(selector, value)

    entered_value = sb.get_value(selector)
    if label == '密码':
        print(f"{label}输入框当前值长度: {len(entered_value)}")
    else:
        print(f"{label}输入框当前值: '{entered_value}'")

    if entered_value != value:
        print(f"{label}输入未生效，使用 JavaScript 强制赋值并触发事件")
        js_set_input_value(sb, selector, value)
        entered_value = sb.get_value(selector)
        if label == '密码':
            print(f"JS 赋值后{label}长度: {len(entered_value)}")
        else:
            print(f"JS 赋值后{label}值: '{entered_value}'")

    return entered_value == value

def login(sb, email, password):
    """执行登录，返回是否成功"""
    print("开始登录流程...")

    # ---- 填写邮箱 ----
    if not fill_input(sb, '#username', email, '邮箱'):
        print("⚠️ 邮箱仍未能正确填入，可能页面有动态行为。")

    # ---- 填写密码 ----
    if not fill_input(sb, '#password', password, '密码'):
        print("⚠️ 密码仍未能正确填入。")

    # ---- 验证码 ----
    captcha_ok = click_captcha_checkbox(sb, '登录验证码')
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮，避免直接提交。")
        return False

    sb.sleep(1)

    # ---- 点击登录按钮 ----
    login_page_url = sb.get_current_url()
    clicked = False

    # 优先尝试提交按钮
    for selector in ['button[type="submit"]', 'div.auth-submit-btn',
                     '//button[contains(text(), "Sign in")]',
                     '//div[contains(text(), "Sign in")]']:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            scroll_to_selector(sb, selector)
            sb.click(selector)
            clicked = True
            print(f"点击 Sign in 使用: {selector}")
            break
        except Exception as e:
            print(f"选择器 {selector} 失败: {e}")
    if not clicked:
        print("所有选择器失败，使用 JS 点击")
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim() === 'Sign in') {
                    el.click();
                    return true;
                }
            }
            return false;
        ''')

    # ---- 等待登录结果 ----
    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        current_url = sb.get_current_url()
        if '/auth/login' not in current_url:
            # 法语环境下标题是法语版（非 "Home | ACLClouds"），
            # 以 URL 路径为准，标题仅打印不作为判据
            print(f"登录后 URL: {current_url}, 标题: {sb.get_title()}")
            print("✅ 登录成功！")
            return True
        else:
            # 提取错误信息
            error_msg = ""
            try:
                errors = sb.driver.find_elements(By.CSS_SELECTOR, '.auth-error-text, .alert-danger, .error-message')
                error_msg = errors[0].text.strip() if errors else ''
            except:
                pass
            print(f"❌ 登录失败，错误: {error_msg}")
            return False
    except Exception as e:
        print(f"登录过程异常: {e}")
        return False
    
# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

def main():

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.getenv('S5_PROXY') or os.getenv('PROXY_SERVER') or "socks://127.0.0.1:1080"

    sb_options = {'uc': True, 'headless': False}
    if IS_PROXY:
        sb_options['proxy'] = PROXY_SERVER
        print(f"🔗 挂载代理: {PROXY_SERVER}")
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_options) as sb:   # 本地调试 headless=False，CI 改为 True
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"获取出口IP失败: {e}")

        sb.set_window_size(1366, 768)

        # 直接进项目页：未登录会被站点重定向到 /auth/login；
        # 已登录（cookie 有效）则直接停在项目页。
        # 注意不能先开 BASE_URL 判断——新版未登录时落在 /fr/ 落地页，
        # 既不是登录页也不是 dashboard。
        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(2)

        if is_login_page(sb):
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 ACL_EMAIL 或 ACL_PASSWORD，无法执行账号密码登录。")
                send_telegram("⚠️ 未配置 ACL_EMAIL 或 ACL_PASSWORD。")
                return
            if not login(sb, EMAIL, PASSWORD):
                return
            # 登录成功后重新进入项目页
            sb.open(PROJECTS_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(2)
        elif not is_logged_in(sb):
            print(f"❌ 未能确认登录状态。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
            send_telegram("⚠️ 未能确认登录状态，请检查账号密码配置。")
            return
        else:
            print(f"✅ 当前已登录。URL: {sb.get_current_url()}，标题: {sb.get_title()}")

        # 2. 定位服务行（过期倒计时与 Renew 按钮都在行的折叠面板里，先展开）
        try:
            expand_service_rows(sb)
            time.sleep(1.5)
        except Exception as e:
            print(f"展开服务行失败（忽略）: {e}")

        rows = find_project_rows(sb)

        if not rows:
            print("❌ 未找到项目卡片。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未找到项目卡片，请检查页面结构。")
            return

        service_ids = [sid for sid in (get_service_id(row) for row in rows) if sid]
        print(f"找到 {len(service_ids)} 个项目卡片。")
        for idx, service_id in enumerate(service_ids, 1):
            try:
                # 每次重新定位：续期成功后 React 会重取列表，旧句柄会失效
                expand_service_rows(sb)
                row = find_row_by_service_id(sb, service_id)
                if row is None:
                    print(f"第 {idx} 个服务行已消失，跳过")
                    continue

                project_name = get_project_name(row, idx)
                old_expiry = get_project_expiry(row)
                before_lines = row_text_lines(row)
                print(f"[{project_name}] 当前过期: {old_expiry}")

                renew_btn = find_renew_buttons(row)

                if renew_btn:
                    action_label = get_action_button_label(renew_btn[0])
                    safe_click_element(sb, renew_btn[0], f"[{project_name}] {action_label}按钮")
                    print(f"[{project_name}] 点击 {action_label}...")
                    handle_renew_antibot(sb, project_name)
                    success, new_expiry, result_note = wait_for_renew_result(
                        sb, service_id, old_expiry, before_lines, timeout=60)
                    if success:
                        print(f"续期成功！状态: {result_note}，新过期: {new_expiry}")
                        send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    else:
                        send_telegram(build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note))
                else:
                    # 2026-09 版页面：只有到了可续期时间才渲染 Renew 按钮
                    print(f"[{project_name}] 页面上没有 Renew 按钮，判定为未到续期时间")
                    send_telegram(build_not_yet_due_message(project_name, old_expiry))
            except Exception as e:
                print(f"处理卡片 {idx} 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

        print("所有项目处理完成。")

if __name__ == '__main__':
    main()
