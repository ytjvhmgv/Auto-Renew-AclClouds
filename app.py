#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import shutil
import subprocess
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

# ----- 配置（从环境变量读取或在双引号内填写） -----
EMAIL = os.getenv('EMAIL') or ""         # 邮箱必填
PASSWORD = os.getenv('PASSWORD') or ""   # 密码必填
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""  # CHAT_ID 可选
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or "" # BOT_TOKEN 可选，需同时填写CHAT_ID生效

LOGIN_PATH = '/auth/login'
LOGIN_URL = f'https://aclclouds.com{LOGIN_PATH}'
BASE_URL = 'https://aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'
DASHBOARD_URL = f'{BASE_URL}/dashboard'

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
    current_url = sb.get_current_url()
    return BASE_URL in current_url and LOGIN_PATH not in current_url

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

def element_contains(parent, child):
    if parent == child:
        return True
    try:
        return parent.find_elements(By.XPATH, './/*').count(child) > 0
    except Exception:
        return False

def dedupe_project_cards(cards):
    cards = unique_elements(cards)
    if not cards:
        return []

    keep = []
    for card in cards:
        card_text = element_text(card)
        if len(card_text) < 3:
            continue

        duplicate = False
        for kept in list(keep):
            kept_text = element_text(kept)
            if element_contains(kept, card):
                duplicate = True
                break
            if element_contains(card, kept):
                if len(card_text) > len(kept_text):
                    keep.remove(kept)
                else:
                    duplicate = True
                break

        if not duplicate:
            keep.append(card)

    deduped = []
    seen_signatures = set()
    for card in keep:
        text = element_text(card)
        name = ''
        for line in text.splitlines():
            line = line.strip()
            if line and not re.search(r'expires|renewal|renew|reactivate|suspended|expiry|expire|valid|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
                name = line
                break
        signature = (name.lower(), get_project_expiry(card).lower())
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(card)

    return deduped

def find_elements(root, selector):
    by = By.XPATH if selector.startswith(('/', './/')) else By.CSS_SELECTOR
    return root.find_elements(by, selector)

def find_renew_buttons(root):
    selectors = [
        '.projects-renew-btn',
        # 续期按钮已改为图标按钮，没有文字，只能靠 title / aria-label 识别
        './/button['
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        './/button['
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "renew")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "reactivate")]',
    ]
    buttons = []
    for selector in selectors:
        try:
            buttons.extend(find_elements(root, selector))
        except Exception:
            continue
    return unique_elements([button for button in buttons if element_text(button) or button.is_displayed()])

def find_card_container_from_child(sb, child):
    return sb.driver.execute_script(
        '''
        const start = arguments[0];
        let node = start;
        for (let i = 0; node && i < 10; i += 1, node = node.parentElement) {
          const text = (node.innerText || '').trim();
          const cls = (node.className || '').toString().toLowerCase();
          const looksLikeProject = /renew|reactivate|suspended|expiry|expire|expires|valid|续期|重新激活|恢复|暂停|过期|到期/i.test(text);
          const looksLikeCard = /card|project|service|server|item|row/.test(cls);
          if (node !== start && text.length > 20 && (looksLikeProject || looksLikeCard)) {
            return node;
          }
        }
        return start.parentElement || start;
        ''',
        child,
    )

def find_project_cards(sb):
    candidate_selectors = [
        '.projects-card',
        '[class*="projects-card"]',
        '[class*="project"][class*="card"]',
        '[class*="Project"][class*="Card"]',
        '[class*="service"][class*="card"]',
        '[class*="server"][class*="card"]',
        'article',
    ]
    cards = []
    for selector in candidate_selectors:
        try:
            for card in sb.driver.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(card).lower()
                if any(keyword in text for keyword in ['renew', 'reactivate', 'suspended', 'expiry', 'expire', 'valid', '续期', '重新激活', '恢复', '暂停', '过期', '到期']):
                    cards.append(card)
        except Exception:
            continue

    if cards:
        return dedupe_project_cards(cards)

    for button in find_renew_buttons(sb.driver):
        try:
            cards.append(find_card_container_from_child(sb, button))
        except Exception:
            continue

    if cards:
        return dedupe_project_cards(cards)

    expiry_xpath = (
        '//*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expiry") '
        'or contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expire") '
        'or contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "valid") '
        'or contains(normalize-space(.), "过期") or contains(normalize-space(.), "到期")]'
    )
    for elem in sb.driver.find_elements(By.XPATH, expiry_xpath):
        try:
            cards.append(find_card_container_from_child(sb, elem))
        except Exception:
            continue

    return dedupe_project_cards(cards)

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
        if re.search(r'expires\s+in|剩余|还有', line, re.I) and idx + 1 < len(lines):
            candidate = lines[idx + 1]
            if extract_date_like(candidate) or re.search(r'\d', candidate):
                # “Expires in”标签单独占一行，时长值在下一行，去掉标签只保留数值
                return re.sub(r'^(?:expires\s*in|剩余|还有)\s*[:：]?\s*', '', candidate, flags=re.I).strip()

    match = re.search(
        r'(?:expires\s*in\s*)?(\d+\s*(?:days|day|d|j|天|日)\s*\d*\s*(?:hours|hour|h|小时)?)',
        text,
        re.I,
    )
    if match:
        return match.group(1).strip()

    match = re.search(r'\d+\s*(?:hours|hour|h|小时)', text, re.I)
    if match:
        return match.group(0).strip()

    return ''

def get_project_name(card, idx):
    selectors = [
        '.projects-card-title',
        'h1',
        'h2',
        'h3',
        'h4',
        '[class*="title"]',
        '[class*="name"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text and len(text) <= 80 and 'renew' not in text.lower() and 'expiry' not in text.lower() and not extract_duration_like(text):
                    return text
        except Exception:
            continue

    for line in element_text(card).splitlines():
        line = line.strip()
        if line and len(line) <= 80 and not extract_duration_like(line) and not re.search(r'renew|reactivate|suspended|expiry|expire|valid|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
            return line
    return f"项目 #{idx}"

def get_project_expiry(card):
    selectors = [
        '.projects-expiry-value',
        '.projects-service-cell--expiry strong',
        '[class*="expiry"] strong',
        '[class*="expiry"] [class*="value"]',
        '[class*="expiry"]',
        '[class*="expire"]',
        '[class*="Expires"]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expiry")]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expire")]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "valid")]',
        './/*[contains(normalize-space(.), "过期") or contains(normalize-space(.), "到期")]',
    ]
    for selector in selectors:
        try:
            for elem in find_elements(card, selector):
                text = element_text(elem)
                date_text = extract_date_like(text)
                if date_text:
                    return date_text
                duration_text = extract_duration_like(text)
                if duration_text:
                    return duration_text
                if text and len(text) <= 120:
                    return text
        except Exception:
            continue

    card_text = element_text(card)
    return extract_date_like(card_text) or extract_duration_like(card_text) or '未知'

def get_renewal_available_note(card):
    text = element_text(card)
    patterns = [
        r'Renewal\s+will\s+be\s+available[^\n]*',
        r'可续期[^\n]*',
        r'续期[^\n]*前[^\n]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0).strip()
    return ''

def get_card_by_index(sb, idx):
    cards = find_project_cards(sb)
    if idx <= len(cards):
        return cards[idx - 1]
    return None

def wait_for_renew_result(sb, idx, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            success_modals = sb.driver.find_elements(
                By.XPATH,
                '//div[contains(@class, "modal") and contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "successfully")]',
            )
            if any(modal.is_displayed() for modal in success_modals):
                card = get_card_by_index(sb, idx)
                return True, get_project_expiry(card) if card else '未知', 'success modal'

            card = get_card_by_index(sb, idx)
            if card:
                renewal_note = get_renewal_available_note(card)
                renew_buttons = find_renew_buttons(card)
                if renewal_note and not renew_buttons:
                    return True, get_project_expiry(card), renewal_note
        except Exception as e:
            print(f"检查续期结果时暂时失败: {e}")

        sb.sleep(1)

    card = get_card_by_index(sb, idx)
    note = get_renewal_available_note(card) if card else ''
    expiry = get_project_expiry(card) if card else '未知'
    return False, expiry, note

def get_renew_note(card):
    selectors = [
        '.projects-renew-note',
        '[class*="renew-note"]',
        '[class*="note"]',
        '[class*="tip"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text:
                    return text
        except Exception:
            continue
    return '未到续期时间'

def get_action_button_label(button):
    text = element_text(button)
    # 图标按钮没有文字，从 title / aria-label 里取按钮含义
    for attr in ('aria-label', 'title'):
        try:
            value = (button.get_attribute(attr) or '').strip()
        except Exception:
            value = ''
        if value:
            text = f"{text} {value}"
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

def has_renew_antibot_modal(sb):
    selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]
    for selector in selectors:
        try:
            if any(elem.is_displayed() for elem in sb.driver.find_elements(By.XPATH, selector)):
                return True
        except Exception:
            continue
    return False

def get_cap_widget_state(sb):
    """读取 Cap 验证组件当前状态：initial / verifying / done / error / missing。

    Cap 组件渲染在 <cap-widget> 的 shadow DOM 里，成功（绿色对勾）时
    .captcha 元素的 data-state 属性为 done。
    """
    return sb.driver.execute_script(
        '''
        const hosts = document.querySelectorAll('cap-widget');
        for (const host of hosts) {
          if (!host.shadowRoot) continue;
          const box = host.shadowRoot.querySelector('.captcha');
          if (box) return box.getAttribute('data-state') || 'initial';
        }
        return 'missing';
        '''
    )

def find_cap_trigger(sb):
    """定位 Cap 组件里 "Verify you're human" 前面的复选框（shadow DOM 内）。"""
    return sb.driver.execute_script(
        '''
        const hosts = document.querySelectorAll('cap-widget');
        for (const host of hosts) {
          if (!host.shadowRoot) continue;
          const trigger = host.shadowRoot.querySelector('.captcha-trigger');
          if (trigger) return trigger;
        }
        return null;
        '''
    )

def click_captcha_checkbox(sb, label='验证码', timeout=10):
    """新版 Cap 人机验证：点击 "Verify you're human" 前面的复选框，
    等待 5 秒后检查是否出现绿色对勾（data-state=done），出现即代表验证通过。"""
    max_attempts = 3
    state = ''
    for attempt in range(1, max_attempts + 1):
        try:
            sb.wait_for_element_visible('cap-widget', timeout=timeout)
        except Exception as e:
            print(f"{label} 未找到 Cap 验证组件: {e}")
            return False

        if attempt > 1:
            print(f"{label} 第 {attempt} 次尝试...")

        trigger = find_cap_trigger(sb)
        if trigger is None:
            print(f"{label} 未找到验证复选框")
            return False

        try:
            sb.driver.execute_script(
                'arguments[0].scrollIntoView({block: "center", inline: "center"});',
                trigger,
            )
        except Exception:
            pass

        # verifying 状态下说明上一次点击仍在校验中，不要重复点击
        state = get_cap_widget_state(sb)
        if state != 'verifying':
            try:
                trigger.click()
                print(f"{label} 已点击 Verify you're human 复选框")
            except (ElementClickInterceptedException, WebDriverException, StaleElementReferenceException) as e:
                print(f"{label} 普通点击失败，改用 JavaScript 点击: {e}")
                try:
                    sb.driver.execute_script('arguments[0].click();', trigger)
                except Exception as e2:
                    print(f"{label} JavaScript 点击也失败: {e2}")
                    return False

        # 等待 5 秒，让 Cap 开始校验
        sb.sleep(5)

        # 轮询等待绿色对勾（成功状态 data-state=done）
        start_time = time.time()
        while time.time() - start_time < 30:
            state = get_cap_widget_state(sb)
            if state == 'done':
                print(f"{label} 验证通过（绿色对勾已出现）")
                return True
            if state == 'error':
                print(f"{label} 本次校验未通过（error 状态），准备重试")
                break
            sb.sleep(1)

    print(f"{label} 验证未通过，最后状态: {state}")
    return False

# ==========================================================================
# 【已停用】2026-10 登录页验证码改版为 Cap 人机验证组件（shadow DOM），
# 不再出现“点击候选词”的图形验证码挑战，以下旧逻辑整体注释保留备查。
# ==========================================================================
# def handle_captcha_challenge(sb, label='验证码', timeout=20):
#     """处理图形验证码挑战：先等待挑战加载，再尝试点击对应图像。"""
#     start_time = time.time()
#     challenge = None
#     last_error = None
#     challenge_selectors = [
#         '.auth-captcha-challenge',
#         '.auth-capcha-challenge',
#         '//*[contains(@class, "captcha") and contains(@class, "challenge")]',
#         '//*[contains(@aria-label, "Click on ") or contains(@aria-label, "Select ") or contains(@class, "challenge")]',
#     ]

#     def get_challenge():
#         for selector in challenge_selectors:
#             try:
#                 if selector.startswith('/'):
#                     elems = sb.driver.find_elements(By.XPATH, selector)
#                     for elem in elems:
#                         if elem.is_displayed():
#                             return elem
#                 else:
#                     elem = sb.wait_for_element_visible(selector, timeout=1)
#                     if elem and elem.is_displayed():
#                         return elem
#             except Exception:
#                 continue
#         return None

#     while time.time() - start_time < timeout:
#         challenge = get_challenge()
#         if challenge:
#             print(f"{label} 检测到图形验证码挑战")
#             break
#         try:
#             checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
#             if checkbox.get_attribute('aria-checked') == 'true':
#                 print(f"{label} 验证复选框已勾选，验证码流程已完成")
#                 return True
#         except Exception:
#             pass
#         sb.sleep(0.3)

#     if not challenge:
#         print(f"{label} 等待验证码挑战加载超时: {last_error}")
#         return False

#     target = ''
#     try:
#         prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
#         target = prompt.text.strip()
#     except Exception:
#         pass
#     if not target:
#         try:
#             prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-capcha-prompt strong')
#             target = prompt.text.strip()
#         except Exception:
#             pass
#     if not target:
#         aria_label = challenge.get_attribute('aria-label') or ''
#         if 'Click on ' in aria_label:
#             target = aria_label.split('Click on ')[-1].strip()

#     print(f"{label} 目标文本: {target or '未识别'}")

#     option_selectors = [
#         '.auth-captcha-option',
#         '.auth-capcha-option',
#         '[class*="captcha-option"]',
#         '[class*="capcha-option"]',
#         'button',
#         'a',
#         '[role="button"]',
#         'img',
#     ]

#     def get_options(challenge_elem):
#         options = []
#         for sel in option_selectors:
#             try:
#                 elems = challenge_elem.find_elements(By.CSS_SELECTOR, sel)
#                 options.extend(elems)
#             except Exception:
#                 continue

#         unique = []
#         seen = set()
#         for elem in options:
#             try:
#                 if not elem.is_displayed() or not elem.is_enabled():
#                     continue
#                 elem_id = getattr(elem, 'id', None)
#                 if elem_id and elem_id in seen:
#                     continue
#                 if elem_id:
#                     seen.add(elem_id)
#                 unique.append(elem)
#             except Exception:
#                 continue
#         return unique

#     options = get_options(challenge)
#     if not options:
#         print(f"{label} 未找到可点击的选项")
#         return False

#     matched = None
#     if target:
#         for opt in options:
#             opt_text = (opt.text or '').strip()
#             if not opt_text:
#                 try:
#                     img = opt.find_element(By.TAG_NAME, 'img')
#                     opt_text = (img.get_attribute('alt') or '').strip()
#                 except Exception:
#                     pass
#             if not opt_text:
#                 try:
#                     opt_text = (opt.get_attribute('aria-label') or '').strip()
#                 except Exception:
#                     pass
#             if target.lower() in opt_text.lower():
#                 matched = opt
#                 break

#     attempts = 0
#     max_attempts = 8
#     while attempts < max_attempts:
#         challenge = get_challenge()
#         if not challenge:
#             return False

#         options = get_options(challenge)
#         if not options:
#             print(f"{label} 当前挑战没有可点击选项，重试中...")
#             attempts += 1
#             sb.sleep(0.8)
#             continue

#         current_target = ''
#         try:
#             prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
#             current_target = prompt.text.strip()
#         except Exception:
#             pass
#         if not current_target:
#             aria_label = challenge.get_attribute('aria-label') or ''
#             if 'Click on ' in aria_label:
#                 current_target = aria_label.split('Click on ')[-1].strip()

#         candidate = None
#         if target and current_target and current_target.lower() == target.lower():
#             for opt in options:
#                 opt_text = (opt.text or '').strip()
#                 if not opt_text:
#                     try:
#                         img = opt.find_element(By.TAG_NAME, 'img')
#                         opt_text = (img.get_attribute('alt') or '').strip()
#                     except Exception:
#                         pass
#                 if not opt_text:
#                     try:
#                         opt_text = (opt.get_attribute('aria-label') or '').strip()
#                     except Exception:
#                         pass
#                 if target.lower() in opt_text.lower():
#                     candidate = opt
#                     break

#         if candidate is None:
#             candidate = options[0]

#         print(f"{label} 点击候选选项 #{attempts + 1} ...")
#         clicked = safe_click_element(sb, candidate, f"{label} 选项候选")
#         if not clicked:
#             attempts += 1
#             sb.sleep(0.8)
#             continue

#         sb.sleep(4.5)

#         try:
#             checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
#             if checkbox.get_attribute('aria-checked') == 'true':
#                 print(f"{label} 验证复选框已勾选，验证码流程已完成")
#                 return True
#         except Exception:
#             pass

#         if not get_challenge():
#             print(f"{label} 挑战已消失，验证完成")
#             return True

#         attempts += 1

#     print(f"{label} 多次尝试后仍未完成验证码")
#     return False

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

def handle_renew_antibot(sb, project_name):
    """Renew 后如果弹出 Anti-bot confirmation，则点击确认。"""
    modal_selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]

    for selector in modal_selectors:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=5)
        except Exception:
            continue

    print(f"[{project_name}] 未检测到续期人机验证窗口，继续等待续期结果")
    return False

def cancel_renew_antibot_dialog(sb):
    """Cap 验证失败时点击续期弹窗里的 Cancel 关闭弹窗。"""
    for selector in ['//div[@role="dialog"]//button[normalize-space(.)="Cancel"]',
                     '//button[normalize-space(.)="Cancel"]']:
        try:
            buttons = [b for b in sb.driver.find_elements(By.XPATH, selector) if b.is_displayed()]
            if buttons:
                safe_click_element(sb, buttons[0], '续期弹窗 Cancel')
                sb.sleep(1)
                return True
        except Exception:
            continue
    return False

def close_notifications_popup(sb, label='通知弹窗', timeout=8):
    """登录后若弹出 Notifications 通知弹窗，点击 Dismiss（或右上角关闭按钮）关掉。

    弹窗结构：div[role="dialog"]（class 含 NotificationModal），内含
    Dismiss 按钮和 aria-label="Fermer" 的关闭按钮。没有弹窗时直接跳过。
    """
    dialog_selectors = [
        '//div[@role="dialog"][contains(@class, "NotificationModal")]',
        '//div[@role="dialog"][@aria-label="Notifications"]',
    ]
    dialog = None
    for selector in dialog_selectors:
        try:
            elems = [el for el in sb.driver.find_elements(By.XPATH, selector) if el.is_displayed()]
            if elems:
                dialog = elems[0]
                break
        except Exception:
            continue

    if dialog is None:
        print(f"未检测到{label}，跳过关闭。")
        return False

    button_xpaths = [
        './/button[contains(normalize-space(.), "Dismiss")]',
        './/button[contains(@class, "NotificationModalSecondaryAction")]',
        './/button[@aria-label="Fermer" or @aria-label="Close"]',
        './/button[contains(@class, "NotificationModalClose")]',
    ]
    for selector in button_xpaths:
        try:
            buttons = [b for b in dialog.find_elements(By.XPATH, selector) if b.is_displayed()]
        except Exception:
            continue
        if buttons:
            safe_click_element(sb, buttons[0], f'{label} 关闭按钮')
            sb.sleep(1)
            print(f"{label} 已关闭。")
            return True

    print(f"{label} 未找到可点击的关闭按钮。")
    return False

def find_dashboard_renew_buttons(sb):
    """找 dashboard 首页 Upcoming renewals 表格里可点击的 Renew 按钮。"""
    buttons = []
    for btn in sb.driver.find_elements(By.XPATH, '//button[normalize-space(.)="Renew"]'):
        try:
            if btn.is_displayed() and btn.is_enabled():
                buttons.append(btn)
        except Exception:
            continue
    return buttons

def get_dashboard_row_info(sb, renew_button):
    """向上定位包含该 Renew 按钮的续期行（含 data-label 单元格），返回整行文本。"""
    return sb.driver.execute_script(
        '''
        let node = arguments[0];
        for (let i = 0; i < 10 && node; i += 1, node = node.parentElement) {
          if (node.querySelector && node.querySelector('[data-label="ID"]') && node.querySelector('[data-label="Renewal"]')) {
            return node.innerText || '';
          }
        }
        return '';
        ''',
        renew_button,
    )

def parse_dashboard_row(row_text):
    """从 Upcoming renewals 行文本解析服务名、服务ID和当前过期时间。"""
    lines = [line.strip() for line in (row_text or '').splitlines() if line.strip()]
    service_id = lines[0] if lines else ''
    project_name = lines[1] if len(lines) > 1 else (service_id or '未知服务')
    old_expiry = extract_date_like(row_text) or '未知'
    return project_name, service_id, old_expiry

def wait_for_dashboard_renew_result(sb, service_id, old_expiry, timeout=40):
    """等待 dashboard 续期结果：Cap 弹窗关闭且服务离开续期列表即视为成功。"""
    start_time = time.time()
    note = ''
    while time.time() - start_time < timeout:
        try:
            body_text = sb.driver.find_element(By.TAG_NAME, 'body').text or ''

            if has_renew_antibot_modal(sb):
                note = 'Cap 验证弹窗尚未关闭'
            elif 'No renewal is currently available' in body_text:
                return True, '续期列表已清空'
            elif service_id and service_id not in body_text:
                return True, '服务已从续期列表移除'
            else:
                renewing = [b for b in sb.driver.find_elements(
                    By.XPATH, '//button[contains(normalize-space(.), "Renewing")]') if b.is_displayed()]
                if renewing:
                    note = 'Renew 处理中...'
                elif find_dashboard_renew_buttons(sb):
                    new_date = extract_date_like(body_text)
                    if new_date and old_expiry != '未知' and new_date != old_expiry:
                        return True, f'续期日期已更新: {new_date}'
                    note = 'Renew 按钮仍在，等待结果...'
                else:
                    return True, 'Renew 按钮已消失'
        except Exception as e:
            note = f'检查续期结果时暂时失败: {e}'
        sb.sleep(1)
    return False, note or '等待续期结果超时'

def fetch_new_expiry_from_projects(sb, service_id, timeout=30):
    """续期成功后到 My services 页面读取该服务最新的过期时间。"""
    if not service_id:
        return '未知'
    try:
        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        sb.sleep(3)
        xpath = f'//article[contains(normalize-space(.), "{service_id}")]'
        start_time = time.time()
        while time.time() - start_time < timeout:
            rows = [r for r in sb.driver.find_elements(By.XPATH, xpath) if r.is_displayed()]
            if rows:
                date_text = extract_date_like(element_text(rows[0]))
                if date_text:
                    return date_text
            sb.sleep(1)
        print(f"未在 My services 页面找到服务 {service_id} 的过期时间。")
    except Exception as e:
        print(f"读取新过期时间失败: {e}")
    return '未知'

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

    # ---- 验证码（2026-10 改版：Cap 组件，点击复选框后等 5 秒检查绿色对勾）----
    captcha_ok = click_captcha_checkbox(sb, '登录验证码', timeout=15)
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮，避免直接提交。")
        return False

    sb.sleep(2)

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
        if '/auth/login' not in sb.get_current_url():
            sb.assert_title('Home | ACLClouds')
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

def _curl_ip(ipv6):
    flag = "-6" if ipv6 else "-4"
    urls = (
        ("https://api64.ipify.org", "https://ipv6.icanhazip.com", "https://api-ipv6.ip.sb/ip")
        if ipv6 else
        ("https://api.ipify.org", "https://ipv4.icanhazip.com", "https://api-ipv4.ip.sb/ip")
    )
    for url in urls:
        try:
            proc = subprocess.run(
                ["curl", flag, "-sS", "--max-time", "10", url],
                capture_output=True, text=True, timeout=15,
            )
            ip = (proc.stdout or "").strip()
            if proc.returncode == 0 and ip and " " not in ip and "<" not in ip:
                return ip
        except Exception:
            continue
    return ""


def print_exit_ips(prefix="当前"):
    v4 = _curl_ip(False)
    v6 = _curl_ip(True)
    print("📍 %s IPv4: %s" % (prefix, v4 or "无"))
    print("📍 %s IPv6: %s" % (prefix, v6 or "无"))
    return v4, v6


def get_current_ip():
    v4, v6 = print_exit_ips("当前")
    return v6 or v4 or ""


def _warp_cli(*args, check=False):
    return subprocess.run(
        ["sudo", "warp-cli", "--accept-tos"] + list(args),
        check=check, timeout=30, capture_output=True, text=True,
    )


def prefer_ipv6():
    try:
        subprocess.run(["sudo", "sysctl", "-w", "net.ipv6.conf.all.disable_ipv6=0"], check=False, timeout=10, capture_output=True)
        subprocess.run(["sudo", "sysctl", "-w", "net.ipv6.conf.default.disable_ipv6=0"], check=False, timeout=10, capture_output=True)
        subprocess.run(
            ["sudo", "bash", "-c", "grep -q 'precedence ::/0 100' /etc/gai.conf 2>/dev/null || echo 'precedence ::/0 100' >> /etc/gai.conf"],
            check=False, timeout=10, capture_output=True,
        )
    except Exception as e:
        print("⚠️ 配置 IPv6 优先失败: %s" % e)


def wait_warp_connected(timeout=40):
    start = time.time()
    last = ""
    while time.time() - start < timeout:
        proc = _warp_cli("status")
        last = "%s%s" % (proc.stdout or "", proc.stderr or "")
        if "Connected" in last and "Disconnected" not in last:
            return True
        time.sleep(2)
    print("⚠️ WARP 未进入 Connected 状态: %s" % ((last.strip()[:300]) or "empty"))
    return False


def reset_warp_identity():
    _warp_cli("disconnect")
    time.sleep(1)
    _warp_cli("registration", "delete")
    subprocess.run(["sudo", "systemctl", "stop", "warp-svc"], check=False, timeout=30, capture_output=True)
    subprocess.run(["sudo", "rm", "-rf", "/var/lib/cloudflare-warp"], check=False, timeout=30, capture_output=True)
    subprocess.run(["sudo", "systemctl", "start", "warp-svc"], check=False, timeout=30, capture_output=True)
    time.sleep(4)
    _warp_cli("registration", "new", check=True)
    mode = _warp_cli("mode", "warp")
    if mode.returncode:
        print("⚠️ 切换 warp mode 失败: %s" % ((mode.stderr or mode.stdout or "").strip()[:200]))
    _warp_cli("connect", check=True)
    wait_warp_connected(40)
    time.sleep(5)


def restart_warp(max_rounds=3):
    if not shutil.which("warp-cli"):
        print("⚠️ 未找到 warp-cli，跳过 WARP 重连（本地直连运行时无需此步骤）")
        return False
    prefer_ipv6()
    print("🔄 正在重启 WARP 以更换出口（优先切换 IPv6）...")
    old_v4, old_v6 = print_exit_ips("当前")
    last_v4, last_v6 = old_v4, old_v6
    for round_i in range(1, max_rounds + 1):
        print("  ↻ 第 %s/%s 轮重置 WARP 身份..." % (round_i, max_rounds))
        try:
            reset_warp_identity()
        except Exception as e:
            print("  ⚠️ 重置异常: %s" % e)
            continue
        last_v4, last_v6 = print_exit_ips("新")
        v4_changed = bool(last_v4 and last_v4 != old_v4)
        v6_changed = bool(last_v6 and last_v6 != old_v6)
        if last_v6:
            print("  ✅ 已拿到 IPv6: %s" % last_v6)
        else:
            print("  ⚠️ 仍未拿到 IPv6，继续尝试...")
        if v4_changed or v6_changed:
            print("✅ WARP 出口已切换  IPv4 %s -> %s  IPv6 %s -> %s" % (old_v4 or "无", last_v4 or "无", old_v6 or "无", last_v6 or "无"))
            return True
        print("  ⚠️ 出口 IP 未变化，继续重置...")
    print("❌ WARP 未能换到新 IP（IPv4=%s, IPv6=%s）" % (last_v4 or "无", last_v6 or "无"))
    return False

def run_browser_session() -> bool:
    """单次浏览器会话。登录失败返回 False 以便更换 WARP IP 重试。"""
    print("🌐 使用 Cloudflare WARP 网络（系统级，不再使用 sing-box 代理）")
    sb_options = {'uc': True, 'headless': False, 'chromium_arg': '--enable-ipv6'}

    print("🚀 启动浏览器...")
    with SB(**sb_options) as sb:   # 本地调试 headless=False，CI 改为 True
        try:
            print_exit_ips("浏览器会话")
        except Exception as e:
            print(f"获取出口IP失败: {e}")

        sb.set_window_size(1366, 768)

        sb.open(LOGIN_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(2)

        if is_login_page(sb):
            print("执行正常登录...")
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 EMAIL 或 PASSWORD，无法执行账号密码登录。")
                send_telegram("⚠️ 未配置 EMAIL 或 PASSWORD。")
                return True
            if not login(sb, EMAIL, PASSWORD):
                print("❌ 登录失败")
                return False
        elif is_logged_in(sb):
            print(f"✅ 当前已登录。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
        else:
            print(f"❌ 未能确认登录状态。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
            send_telegram("⚠️ 未能确认登录状态，请检查账号密码配置。")
            return False

        # 2. 进入 dashboard 首页
        sb.open(DASHBOARD_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(3)

        # 3. 关闭登录后的 Notifications 通知弹窗（如有，点 Dismiss 即可）
        close_notifications_popup(sb)

        # 4. 逐个点击 Upcoming renewals 里的 Renew 按钮并完成 Cap 人机验证
        processed = 0
        max_services = 10  # 防止异常页面导致死循环
        while processed < max_services:
            renew_buttons = find_dashboard_renew_buttons(sb)
            if not renew_buttons:
                break

            row_text = get_dashboard_row_info(sb, renew_buttons[0])
            project_name, service_id, old_expiry = parse_dashboard_row(row_text)
            print(f"[{project_name}] 当前过期: {old_expiry}（服务ID: {service_id or '未知'}）")

            try:
                safe_click_element(sb, renew_buttons[0], f"[{project_name}] Renew按钮")
                print(f"[{project_name}] 点击 Renew...")
                captcha_ok = handle_renew_antibot(sb, project_name)
                if not captcha_ok:
                    print(f"[{project_name}] Cap 验证未完成，尝试点击 Cancel 关闭弹窗。")
                    cancel_renew_antibot_dialog(sb)

                success, result_note = wait_for_dashboard_renew_result(sb, service_id, old_expiry, timeout=40)
                if success:
                    new_expiry = fetch_new_expiry_from_projects(sb, service_id)
                    print(f"续期成功！状态: {result_note}，新过期: {new_expiry}")
                    send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    # 回到 dashboard 继续处理剩余服务
                    sb.open(DASHBOARD_URL)
                    sb.wait_for_ready_state_complete()
                    time.sleep(2)
                else:
                    print(f"续期状态未确认: {result_note}")
                    send_telegram(build_unconfirmed_message(project_name, old_expiry, old_expiry, result_note))
            except Exception as e:
                print(f"处理 [{project_name}] 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

            processed += 1

        if processed == 0:
            body_text = ''
            try:
                body_text = sb.driver.find_element(By.TAG_NAME, 'body').text
            except Exception:
                pass
            if 'No renewal is currently available' in body_text:
                print("当前没有可续期的服务。")
                send_telegram(build_not_yet_due_message('（无待续期服务）', 'No renewal is currently available'))
            else:
                print("❌ 未在 dashboard 找到 Renew 按钮。")
                log_projects_page_diagnostics(sb)
                send_telegram("⚠️ 未在 dashboard 找到 Renew 按钮，请检查页面结构。")

        print("所有项目处理完成。")
        return True


def main():
    print("#" * 25)
    print("   AclClouds 自动续期")
    print("#" * 25)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"\n🔁 第 {attempt}/{max_attempts} 次尝试")
        if attempt > 1:
            print("先更换 WARP IP 再重新登录...")
            if not restart_warp():
                print("⚠️ WARP 未能更换 IP，停止重试")
                break
        if run_browser_session():
            return

    print("\n❌ 多次尝试后仍登录失败，终止后续续期操作。")
    send_telegram("⚠️ 登录失败，请检查账号密码或 Cloudflare 验证。")

if __name__ == '__main__':
    main()
