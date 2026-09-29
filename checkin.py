"""
GLaDOS 自动签到脚本
支持多账号、多种推送渠道、重试机制、日志脱敏
"""
import os
import json
import time
import random
import hashlib
import hmac
import base64
import urllib.parse
from typing import List, Dict, Any, Tuple
from functools import wraps
import requests

# ==================== 配置 ====================
CHECKIN_URL = "https://glados.cloud/api/user/checkin"
STATUS_URL = "https://glados.cloud/api/user/status"
POINTS_URL = "https://glados.cloud/api/user/points"
HEADERS_BASE = {
    "origin": "https://glados.cloud",
    "referer": "https://glados.cloud/console/checkin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "content-type": "application/json;charset=UTF-8",
}
PAYLOAD = {"token": "glados.cloud"}
TIMEOUT = 12
MAX_RETRY = 3
RETRY_MIN_WAIT = 2.0
RETRY_MAX_WAIT = 10.0
MIN_DELAY = 1.0
MAX_DELAY = 2.0
TELEGRAM_MAX_LENGTH = 4000
TELEGRAM_TRUNCATE_LENGTH = 3990
COOKIE_MASK_LENGTH = 10
COOKIE_MIN_LENGTH = 20


# ==================== 工具函数 ====================
def safe_json(resp: requests.Response) -> Dict[str, Any]:
    """安全解析 JSON 响应"""
    try:
        return resp.json()
    except Exception:
        return {}


def mask_email(email: str) -> str:
    """
    邮箱脱敏：保留前两个字符和最后一个字符，中间用 *** 替代
    Examples:
        mask_email("test@example.com")   -> "te***t@example.com"
        mask_email("ab@example.com")     -> "ab***b@example.com"
        mask_email("a@example.com")      -> "a***a@example.com"
        mask_email("unknown")            -> "unknown"
    """
    if not email or email == "unknown" or "@" not in email:
        return email
    try:
        name, domain = email.rsplit("@", 1)
        if len(name) <= 2:
            masked_name = f"{name}***{name[-1]}"
        else:
            masked_name = f"{name[:2]}***{name[-1]}"
        return f"{masked_name}@{domain}"
    except Exception:
        return email


def mask_cookie(cookie: str) -> str:
    """Cookie 脱敏（只显示前后各10个字符）"""
    if not cookie or len(cookie) <= COOKIE_MIN_LENGTH:
        return "***"
    return f"{cookie[:COOKIE_MASK_LENGTH]}...{cookie[-COOKIE_MASK_LENGTH:]}"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    text = f"{title}\n\n{content}"

    # Telegram 单条消息上限 4096 字符，做截断避免发送失败。
    if len(text) > 4000:
        text = text[:3990] + "..."

def validate_cookie(cookie: str) -> Tuple[bool, str]:
    """验证 Cookie 是否包含必要字段（新版 gld:sess，兼容旧版 koa:sess）"""
    if not cookie or not cookie.strip():
        return False, "Cookie 为空"
    cookie = cookie.strip()
    if "gld:sess=" not in cookie and "koa:sess=" not in cookie:
        return False, "Cookie 缺少必要字段: gld:sess（旧版为 koa:sess）"
    if "gld:sess.sig=" not in cookie and "koa:sess.sig=" not in cookie:
        return False, "Cookie 缺少必要字段: gld:sess.sig（旧版为 koa:sess.sig）"
    return True, ""


def retry_on_failure(max_retries: int = MAX_RETRY, min_wait: float = RETRY_MIN_WAIT,
                     max_wait: float = RETRY_MAX_WAIT):
    """重试装饰器（指数退避）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        wait_time = min(min_wait * (2 ** attempt), max_wait)
                        print(f"⚠️ 第 {attempt + 1} 次尝试失败: {e}，{wait_time:.1f}秒后重试...")
                        time.sleep(wait_time)
            raise last_exception  # type: ignore
        return wrapper
    return decorator


# ==================== 推送函数 ====================
def push_deer(key: str, title: str, content: str) -> None:
    """PushDeer 推送"""
    if not key:
        return
    try:
        r = requests.post(
            "https://api2.pushdeer.com/message/push",
            json={"pushkey": key, "text": f"{title}\n\n{content}", "type": "text"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("code") == 0:
            print("✅ PushDeer 推送成功")
        else:
            print(f"⚠️ PushDeer 推送失败: {resp.get('message', r.text)}")
    except Exception as e:
        print(f"⚠️ PushDeer 推送异常: {e}")


def push_serverchan(key: str, title: str, content: str) -> None:
    """Server酱推送"""
    if not key:
        return
    try:
        r = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": content},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("code") == 0:
            print("✅ Server酱推送成功")
        else:
            print(f"⚠️ Server酱推送失败: {resp.get('message', r.text)}")
    except Exception as e:
        print(f"⚠️ Server酱推送异常: {e}")


def push_telegram(bot_token: str, chat_id: str, title: str, content: str) -> None:
    """Telegram Bot 推送"""
    if not bot_token or not chat_id:
        return
    text = f"{title}\n\n{content}"
    if len(text) > TELEGRAM_MAX_LENGTH:
        text = text[:TELEGRAM_TRUNCATE_LENGTH] + "\n..."
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("ok"):
            print("✅ Telegram 推送成功")
        else:
            print(f"⚠️ Telegram 推送失败: HTTP {r.status_code} | {resp.get('description', r.text)}")
    except Exception as e:
        print(f"⚠️ Telegram 推送异常: {e}")


def push_pushplus(token: str, title: str, content: str) -> None:
    """PushPlus 推送"""
    if not token:
        return
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": token, "title": title, "content": content, "template": "html"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("code") == 200:
            print("✅ PushPlus 推送成功")
        else:
            print(f"⚠️ PushPlus 推送失败: {resp.get('msg', r.text)}")
    except Exception as e:
        print(f"⚠️ PushPlus 推送异常: {e}")


def push_dingtalk(webhook_url: str, title: str, content: str) -> None:
    """钉钉机器人推送（支持加签）"""
    if not webhook_url:
        return
    try:
        secret = os.getenv("DINGTALK_SECRET", "")
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            separator = "&" if "?" in webhook_url else "?"
            webhook_url = f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"

        r = requests.post(
            webhook_url,
            json={
                "msgtype": "markdown",
                "markdown": {"title": title, "text": f"### {title}\n\n{content}"},
            },
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("errcode") == 0:
            print("✅ 钉钉机器人推送成功")
        else:
            print(f"⚠️ 钉钉机器人推送失败: {resp.get('errmsg', r.text)}")
    except Exception as e:
        print(f"⚠️ 钉钉机器人推送异常: {e}")


def push_feishu(webhook_url: str, title: str, content: str) -> None:
    """飞书机器人推送（支持加签）"""
    if not webhook_url:
        return
    try:
        data: Dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [{"tag": "markdown", "content": content}],
            },
        }

        secret = os.getenv("FEISHU_SECRET", "")
        if secret:
            timestamp = str(round(time.time()))
            string_to_sign = f"{timestamp}\n{secret}"
            # 飞书签名：以 string_to_sign 为 key，空字符串为 message
            hmac_code = hmac.new(
                string_to_sign.encode("utf-8"),
                b"",
                digestmod=hashlib.sha256,
            ).digest()
            sign = base64.b64encode(hmac_code).decode("utf-8")
            data["timestamp"] = timestamp
            data["sign"] = sign

        r = requests.post(
            webhook_url,
            json=data,
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("code") == 0:
            print("✅ 飞书机器人推送成功")
        else:
            print(f"⚠️ 飞书机器人推送失败: {resp.get('msg', r.text)}")
    except Exception as e:
        print(f"⚠️ 飞书机器人推送异常: {e}")


def push_wecom_bot(webhook_url: str, title: str, content: str) -> None:
    """企业微信机器人推送"""
    if not webhook_url:
        return
    try:
        r = requests.post(
            webhook_url,
            json={
                "msgtype": "markdown",
                "markdown": {"content": f"### {title}\n\n{content}"},
            },
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("errcode") == 0:
            print("✅ 企业微信机器人推送成功")
        else:
            print(f"⚠️ 企业微信机器人推送失败: {resp.get('errmsg', r.text)}")
    except Exception as e:
        print(f"⚠️ 企业微信机器人推送异常: {e}")


def push_yunhu(token: str, recv_id: str, title: str, content: str) -> None:
    """云湖机器人推送"""
    if not token or not recv_id:
        return
    try:
        recv_type = os.getenv("YUNHU_RECV_TYPE", "group")
        r = requests.post(
            "https://chat-go.jwzhd.com/open-apis/v1/bot/send-message",
            json={
                "token": token,
                "recvId": recv_id,
                "recvType": recv_type,
                "contentType": 1,
                "content": f"**{title}**\n\n{content}",
            },
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp = safe_json(r)
        if r.ok and resp.get("code") == 1:
            print("✅ 云湖机器人推送成功")
        else:
            print(f"⚠️ 云湖机器人推送失败: {resp.get('msg', resp.get('message', r.text))}")
    except Exception as e:
        print(f"⚠️ 云湖机器人推送异常: {e}")


def push_all(title: str, content: str) -> None:
    """推送到所有已配置的通知渠道"""
    configured = []

    # PushDeer
    key = os.getenv("SENDKEY", "")
    if key:
        push_deer(key, title, content)
        configured.append("PushDeer")

    # Server酱
    key = os.getenv("SERVERCHAN_KEY", "")
    if key:
        push_serverchan(key, title, content)
        configured.append("Server酱")

    # Telegram
    bot_token = os.getenv("TG_BOT_TOKEN", "")
    chat_id = os.getenv("TG_CHAT_ID", "")
    if bot_token and chat_id:
        push_telegram(bot_token, chat_id, title, content)
        configured.append("Telegram")

    # PushPlus
    token = os.getenv("PUSHPLUS_TOKEN", "")
    if token:
        push_pushplus(token, title, content)
        configured.append("PushPlus")

    # 钉钉机器人
    webhook = os.getenv("DINGTALK_WEBHOOK", "")
    if webhook:
        push_dingtalk(webhook, title, content)
        configured.append("钉钉机器人")

    # 飞书机器人
    webhook = os.getenv("FEISHU_WEBHOOK", "")
    if webhook:
        push_feishu(webhook, title, content)
        configured.append("飞书机器人")

    # 企业微信机器人
    webhook = os.getenv("WECOM_BOT_WEBHOOK", "")
    if webhook:
        push_wecom_bot(webhook, title, content)
        configured.append("企业微信机器人")

    # 云湖机器人
    token = os.getenv("YUNHU_TOKEN", "")
    recv_id = os.getenv("YUNHU_RECV_ID", "")
    if token and recv_id:
        push_yunhu(token, recv_id, title, content)
        configured.append("云湖机器人")

    if not configured:
        print("⚠️ 未配置任何推送服务，请在 Secrets 中设置至少一种推送渠道")
    else:
        print(f"📬 已推送至: {', '.join(configured)}")


# ==================== 签到逻辑 ====================
def classify_checkin(code: int, message: str) -> str:
    """
    判断签到结果: ok / repeat / fail
    GLaDOS API: code=0 成功, code=1 已签到, 其他失败
    """
    if code == 0:
        return "ok"
    if code == 1:
        return "repeat"
    msg = message.lower()
    if "got" in msg:
        return "ok"
    if any(kw in msg for kw in ("repeat", "already", "重复", "已签到", "签到过", "请勿")):
        return "repeat"
    return "fail"


@retry_on_failure()
def checkin_request(session: requests.Session, headers: Dict[str, str]) -> Dict[str, Any]:
    """执行签到请求（带重试）"""
    r = session.post(CHECKIN_URL, headers=headers, data=json.dumps(PAYLOAD), timeout=TIMEOUT)
    return safe_json(r)


@retry_on_failure()
def get_status(session: requests.Session, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """查询账号状态/积分（带重试）"""
    r = session.get(url, headers=headers, timeout=TIMEOUT)
    return safe_json(r)


def checkin_account(session: requests.Session, cookie: str, index: int) -> Dict[str, str]:
    """执行单个账号的签到，返回账号信息字典"""
    headers = dict(HEADERS_BASE)
    headers["cookie"] = cookie

    email = "unknown"
    days = "-"
    total_points = "-"
    earned = 0
    status = ""

    try:
        # 1. 签到
        j = checkin_request(session, headers)
        code = j.get("code", -2)
        message = j.get("message", "")
        earned = j.get("points", 0) or 0
        result = classify_checkin(code, message)

        if result == "ok":
            status = f"✅ 成功 (+{earned}积分)"
        elif result == "repeat":
            status = "🔄 已签到"
        else:
            status = f"❌ 失败({message})"

        # 2. 查询账号状态（剩余天数、邮箱）
        try:
            s = get_status(session, STATUS_URL, headers)
            data = s.get("data") or {}
            email = data.get("email", email)
            if data.get("leftDays") is not None:
                days = f"{int(float(data['leftDays']))} 天"
        except Exception:
            pass

        # 3. 查询总积分
        try:
            p = get_status(session, POINTS_URL, headers)
            if p.get("points") is not None:
                total_points = f"{int(float(p['points']))} 积分"
        except Exception:
            pass

    except Exception as e:
        status = f"❌ 异常({e})"

    return {
        "index": index,
        "email": mask_email(email),
        "status": status,
        "total_points": total_points,
        "remaining_days": days,
    }


# ==================== 主流程 ====================
def main() -> None:
    cookies = [c.strip() for c in os.getenv("COOKIES", "").split("&") if c.strip()]

    if not cookies:
        push_all("GLaDOS 签到", "❌ 未检测到 COOKIES，请配置 GitHub Secrets")
        return

    print(f"📋 检测到 {len(cookies)} 个账号")

    # 验证 Cookie 格式
    for idx, cookie in enumerate(cookies, 1):
        is_valid, error_msg = validate_cookie(cookie)
        if not is_valid:
            print(f"⚠️ 账号 {idx} Cookie 格式异常: {error_msg}")
            print(f"   Cookie 片段: {mask_cookie(cookie)}")

    session = requests.Session()
    ok = fail = repeat = 0
    lines = []

    for idx, cookie in enumerate(cookies, 1):
        print(f"\n🔄 正在处理账号 {idx}/{len(cookies)}...")
        acc = checkin_account(session, cookie, idx)

        if "✅" in acc["status"]:
            ok += 1
        elif "🔄" in acc["status"]:
            repeat += 1
        else:
            fail += 1

        lines.append(
            f"{acc['index']}. {acc['email']} | {acc['status']} | "
            f"总积分:{acc['total_points']} | 剩余:{acc['remaining_days']}"
        )

        # 非最后一个账号时随机延迟
        if idx < len(cookies):
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    title = f"GLaDOS 签到完成 ✅{ok} ❌{fail} 🔄{repeat}"
    content = "\n".join(lines)

    print(f"\n{'='*50}")
    print(content)
    print(f"{'='*50}")

    push_all(title, content)


if __name__ == "__main__":
    main()
