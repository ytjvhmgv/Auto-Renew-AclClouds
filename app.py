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
EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://dash.aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'

# 核心通用状态正则 (兼容英、法、中)
STATE_REGEX = r'renew|reactivate|renouveler|reactiver|réactiver|suspended|suspendu|expiry|expire|expiré|expiration|valid|valide|续期|重新激活|恢复|暂停|过期|到期'

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
            if line and not re.search(STATE_REGEX, line, re.I):
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

def find_renew_buttons(card):
    """
    通过 Python 遍历彻底解决多语言（特别是法语）和图标按钮的匹配盲区。
    """
    buttons = []
    
    # 1. 直接按通用 CSS 选取
    selectors = ['.projects-renew-btn', '[class*="renew-btn"]', '[class*="reactivate-btn"]']
    for sel in selectors:
        try:
            buttons.extend(card.find_elements(By.CSS_SELECTOR, sel))
        except:
            pass

    # 2. 遍历卡片内所有 button 和 a 角色，匹配语言无关特征
    try:
        elements = card.find_elements(By.XPATH, './/button | .//a | .//*[@role="button"]')
        # 包括法文的 Renouveler, Réactiver 等
        keywords = ['renew', 'reactivate', 'renouveler', 'reactiver', 'réactiver', '续期', '恢复', '重新激活', 'resume', 'unsuspend']
        
        for elem in elements:
            if elem in buttons: continue
            
            texts_to_check = []
            try: texts_to_check.append((elem.text or '').strip().lower())
            except: pass
            try: texts_to_check.append((elem.get_attribute('title') or '').strip().lower())
            except: pass
            try: texts_to_check.append((elem.get_attribute('aria-label') or '').strip().lower())
            except: pass
            
            # (A) 文字层级匹配
            matched_by_text = False
            for t in texts_to_check:
                if any(kw in t for kw in keywords):
                    buttons.append(elem)
                    matched_by_text = True
                    break
            
            if matched_by_text:
                continue
                
            # (B) 图标层级匹配 (无视页面语言)
            try:
                inner_html = (elem.get_attribute('innerHTML') or '').lower()
                # FontAwesome / SVG 常用恢复和续期图标
                if 'fa-play' in inner_html or 'fa-sync' in inner_html or 'fa-rotate' in inner_html:
                    buttons.append(elem)
            except:
                pass
    except Exception as e:
        print(f"查找按钮时出小错: {e}")
        
    # 去重并只保留可见按钮
    valid_buttons = []
    for b in unique_elements(buttons):
        try:
            if b.is_displayed(): valid_buttons.append(b)
        except:
            pass
    return valid_buttons

def find_card_container_from_child(sb, child):
    return sb.driver.execute_script(
        '''
        const start = arguments[0];
        let node = start;
        for (let i = 0; node && i < 10; i += 1, node = node.parentElement) {
          const text = (node.innerText || '').trim();
          const cls = (node.className || '').toString().toLowerCase();
          const looksLikeProject = /renew|reactivate|renouveler|réactiver|suspended|suspendu|expiry|expire|expiré|valid|valide|续期|重新激活|恢复|暂停|过期|到期/i.test(text);
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
    # 增加法语关键词匹配
    keywords_list = ['renew', 'reactivate', 'renouveler', 'réactiver', 'reactiver', 'suspended', 'suspendu', 'expiry', 'expire', 'expiré', 'valid', 'valide', '续期', '重新激活', '恢复', '暂停', '过期', '到期']
    
    for selector in candidate_selectors:
        try:
            for card in sb.driver.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(card).lower()
                if any(keyword in text for keyword in keywords_list):
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
        # 兼容法语 "expire dans" 或 "restant"
        if re.search(r'expires\s+in|expire\s+dans|restant|剩余|还有', line, re.I) and idx + 1 < len(lines):
            candidate = lines[idx + 1]
            if extract_date_like(candidate) or re.search(r'\d', candidate):
                return re.sub(r'^(?:expires\s*in|expire\s+dans|restant|剩余|还有)\s*[:：]?\s*', '', candidate, flags=re.I).strip()

    # 兼容法文 jours, heures
    match = re.search(
        r'(?:expires\s*in\s*|expire\s+dans\s*)?(\d+\s*(?:days|day|d|j|jours|jour|天|日)\s*\d*\s*(?:hours|hour|h|heures|heure|小时)?)',
        text,
        re.I,
    )
    if match:
        return match.group(1).strip()

    match = re.search(r'\d+\s*(?:hours|hour|h|heures|heure|小时)', text, re.I)
    if match:
        return match.group(0).strip()

    return ''

def get_project_name(card, idx):
    selectors = [
        '.projects-card-title', 'h1', 'h2', 'h3', 'h4', '[class*="title"]', '[class*="name"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text and len(text) <= 80 and not re.search(STATE_REGEX, text, re.I) and not extract_duration_like(text):
                    return text
        except Exception:
            continue

    for line in element_text(card).splitlines():
        line = line.strip()
        if line and len(line) <= 80 and not extract_duration_like(line) and not re.search(STATE_REGEX, line, re.I):
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
    ]
    for selector in selectors:
        try:
            for elem in find_elements(card, selector):
                text = element_text(elem)
                date_text = extract_date_like(text)
                if date_text: return date_text
                duration_text = extract_duration_like(text)
                if duration_text: return duration_text
                if text and len(text) <= 120: return text
        except Exception:
            continue

    card_text = element_text(card)
    return extract_date_like(card_text) or extract_duration_like(card_text) or '未知'

def get_renewal_available_note(card):
    text = element_text(card)
    patterns = [
        r'Renewal\s+will\s+be\s+available[^\n]*',
        r'Le\s+renouvellement\s+sera\s+disponible[^\n]*',
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
            # 添加法语 success/succès 的支持
            success_xpath = '//div[contains(@class, "modal") and (contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "successfully") or contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "succès"))]'
            success_modals = sb.driver.find_elements(By.XPATH, success_xpath)
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
            pass
        sb.sleep(1)

    card = get_card_by_index(sb, idx)
    note = get_renewal_available_note(card) if card else ''
    expiry = get_project_expiry(card) if card else '未知'
    return False, expiry, note

def get_renew_note(card):
    selectors = [
        '.projects-renew-note', '[class*="renew-note"]', '[class*="note"]', '[class*="tip"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text: return text
        except Exception:
            continue
    return '未到续期时间'

def get_action_button_label(button):
    text = element_text(button)
    # 图标按钮没有文字，从 title / aria-label 里取按钮含义
    for attr in ('aria-label', 'title', 'innerHTML'):
        try:
            value = (button.get_attribute(attr) or '').strip()
        except Exception:
            value = ''
        if value:
            text = f"{text} {value}"
    
    lowered = text.lower()
    if any(k in lowered for k in ['reactivate', 'réactiver', 'reactiver', '重新激活', '恢复', 'fa-play']):
        return 'Reactivate (激活)'
    return 'Renew (续期)'

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
    # 增加法语 human/humain 的匹配
    selectors = [
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "anti-bot")]',
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "human")]',
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "humain")]',
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "robot")]',
    ]
    for selector in selectors:
        try:
            if any(elem.is_displayed() for elem in sb.driver.find_elements(By.XPATH, selector)):
                return True
        except Exception:
            continue
    return False

def click_captcha_checkbox(sb, label='验证码', timeout=10):
    """点击 ACLClouds 页面上的人机验证复选框，并处理图形验证码挑战。"""
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

    sb.sleep(5)
    captcha_ok = handle_captcha_challenge(sb, label, timeout=20)
    if not captcha_ok:
        print(f"{label} 验证流程未完成，等待状态仍未确认。")
        return False

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
            if target.lower() in opt_text.lower():
                matched = opt
                break

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
                if target.lower() in opt_text.lower():
                    candidate = opt
                    break

        if candidate is None:
            candidate = options[0]

        print(f"{label} 点击候选选项 #{attempts + 1} ...")
        clicked = safe_click_element(sb, candidate, f"{label} 选项候选")
        if not clicked:
            attempts += 1
            sb.sleep(0.8)
            continue

        sb.sleep(4.5)

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
        "✅ 续期/恢复成功",
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
    if old_expiry and old_expiry.lower() not in ['suspended', 'paused', '暂停', 'suspendu']:
        lines.append(f"旧过期: {old_expiry}")
    lines.extend([
        f"当前过期: {new_expiry}",
        f"页面提示: {result_note or '未发现成功提示'}",
    ])
    return "\n".join(lines)

def handle_renew_antibot(sb, project_name):
    modal_selectors = [
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "anti-bot")]',
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "human")]',
        '//div[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "humain")]',
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
    if entered_value != value:
        js_set_input_value(sb, selector, value)
        entered_value = sb.get_value(selector)

    return entered_value == value

def login(sb, email, password):
    print("开始登录流程...")

    if not fill_input(sb, '#username', email, '邮箱'):
        print("⚠️ 邮箱仍未能正确填入，可能页面有动态行为。")
    if not fill_input(sb, '#password', password, '密码'):
        print("⚠️ 密码仍未能正确填入。")

    captcha_ok = click_captcha_checkbox(sb, '登录验证码')
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮，避免直接提交。")
        return False

    sb.sleep(1)

    login_page_url = sb.get_current_url()
    clicked = False

    for selector in ['button[type="submit"]', 'div.auth-submit-btn',
                     '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "sign in")]',
                     '//button[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "connexion")]']:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            scroll_to_selector(sb, selector)
            sb.click(selector)
            clicked = True
            break
        except Exception as e:
            pass
            
    if not clicked:
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim().toLowerCase().includes('sign in') || el.textContent.trim().toLowerCase().includes('connexion')) {
                    el.click();
                    return true;
                }
            }
            return false;
        ''')

    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        current_url = sb.get_current_url()
        if '/auth/login' not in current_url:
            print(f"✅ 登录成功！当前标题: {sb.get_title()}")
            return True
        else:
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
        pass

def wait_warp_connected(timeout=40):
    start = time.time()
    last = ""
    while time.time() - start < timeout:
        proc = _warp_cli("status")
        last = "%s%s" % (proc.stdout or "", proc.stderr or "")
        if "Connected" in last and "Disconnected" not in last:
            return True
        time.sleep(2)
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
    _warp_cli("mode", "warp")
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
            continue
        last_v4, last_v6 = print_exit_ips("新")
        v4_changed = bool(last_v4 and last_v4 != old_v4)
        v6_changed = bool(last_v6 and last_v6 != old_v6)
        if v4_changed or v6_changed:
            print("✅ WARP 出口已切换")
            return True
    return False

def run_browser_session() -> bool:
    print("🌐 使用 Cloudflare WARP 网络")
    sb_options = {'uc': True, 'headless': False, 'chromium_arg': '--enable-ipv6'}

    print("🚀 启动浏览器...")
    with SB(**sb_options) as sb:
        try: print_exit_ips("浏览器会话")
        except: pass

        sb.set_window_size(1366, 768)

        sb.open(BASE_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(2)

        if not is_login_page(sb) and not is_logged_in(sb):
            login_url = f"https://aclclouds.com{LOGIN_PATH}"
            print(f"强制前往登录页: {login_url}")
            sb.open(login_url)
            sb.wait_for_ready_state_complete()
            time.sleep(3)

        if is_login_page(sb):
            if not EMAIL or not PASSWORD:
                return True
            if not login(sb, EMAIL, PASSWORD):
                return False
        elif is_logged_in(sb):
            print(f"✅ 当前已登录。URL: {sb.get_current_url()}")
        else:
            return False

        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(3)

        # 这里使用动态获取避免 StaleElementReferenceException
        initial_cards = find_project_cards(sb)
        total_cards = len(initial_cards)

        if total_cards == 0:
            print("❌ 未找到项目卡片。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未找到项目卡片，请检查页面结构。")
            return True

        print(f"找到 {total_cards} 个项目卡片。")
        for idx in range(1, total_cards + 1):
            try:
                card = get_card_by_index(sb, idx)
                if not card:
                    continue

                project_name = get_project_name(card, idx)
                old_expiry = get_project_expiry(card)
                print(f"[{project_name}] 当前过期: {old_expiry}")

                renew_btn = find_renew_buttons(card)

                if renew_btn:
                    action_label = get_action_button_label(renew_btn[0])
                    safe_click_element(sb, renew_btn[0], f"[{project_name}] {action_label}按钮")
                    print(f"[{project_name}] 点击 {action_label}...")
                    handle_renew_antibot(sb, project_name)
                    success, new_expiry, result_note = wait_for_renew_result(sb, idx, timeout=30)
                    if success:
                        print(f"续期/恢复成功！状态: {result_note}，新过期: {new_expiry}")
                        send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    else:
                        send_telegram(build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note))
                else:
                    note = get_renew_note(card)
                    print(f"无 Renew 按钮，提示: {note}")
                    send_telegram(build_not_yet_due_message(project_name, old_expiry))
            except Exception as e:
                print(f"处理卡片 {idx} 出错: {e}")

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
                break
        if run_browser_session():
            return

    print("\n❌ 多次尝试后仍失败。")

if __name__ == '__main__':
    main()
