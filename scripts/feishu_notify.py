# -*- coding: utf-8 -*-
"""飞书群机器人推送：POST webhook，飞书手机端收通知
webhook 和 secret 存 config.json。密钥值绝不进日志、绝不打印。
用法:
    python feishu_notify.py "标题" "正文"
退出码 0=成功，1=失败（失败原因只打 stderr，不含 webhook 本体）
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_JSON = os.path.join(ROOT, "config.json")


def _load_config():
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
        hook = (cfg.get("飞书webhook") or "").strip()
        secret = (cfg.get("飞书secret") or "").strip()
        return hook or None, secret or None
    except Exception:
        return None, None


def _gen_sign(timestamp, secret):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_text(title, body, hook=None, secret=None):
    """发文本消息。返回 (ok, msg)。hook/secret 不打印、不进返回值。"""
    if hook is None or secret is None:
        h, s = _load_config()
        hook = hook or h
        secret = secret or s
    if not hook:
        return False, "config.json 未配置 飞书webhook"
    timestamp = str(int(time.time()))
    payload = {
        "timestamp": timestamp,
        "msg_type": "text",
        "content": {"text": f"{title}\n{body}"},
    }
    if secret:
        payload["sign"] = _gen_sign(timestamp, secret)
    req = urllib.request.Request(
        hook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            r = json.loads(resp.read().decode("utf-8"))
        if r.get("code") == 0:
            return True, "ok"
        return False, f"飞书返回 code={r.get('code')} msg={r.get('msg')}"
    except Exception as e:
        return False, f"请求失败: {type(e).__name__}"


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "测试"
    body = sys.argv[2] if len(sys.argv) > 2 else "微信信息管理系统推送测试"
    ok, msg = send_text(title, body)
    print("OK" if ok else f"FAIL: {msg}", file=sys.stderr if not ok else sys.stdout)
    sys.exit(0 if ok else 1)
