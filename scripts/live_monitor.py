#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_monitor.py — 微信群聊实时监控 + 关键词分类 + 飞书推送

每隔 N 秒（默认 30s）快照 message_0.db → 解密 → 查新消息 → 按群白名单过滤
→ 关键词匹配分类 → 重要消息飞书推送。

依赖：decrypt_db.py、db_key.json、config.json（wechat-assistant）
用法:
  python live_monitor.py                    # 前台运行，默认 30s 轮询
  python live_monitor.py --interval 15     # 15 秒轮询一次
  python live_monitor.py --once            # 只跑一次（测试用）
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import subprocess
import argparse
import datetime
import tempfile
import hashlib
import re
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(SCRIPT_DIR, "wechat-chat-export", "scripts")
ASSISTANT_DIR = os.path.join(SCRIPT_DIR, "wechat-assistant")
CONFIG_PATH = os.path.join(ASSISTANT_DIR, "config.json")
WATERMARK_PATH = os.path.join(ASSISTANT_DIR, "live_watermark.json")
LOG_PATH = os.path.join(ASSISTANT_DIR, "live_monitor.log")

CANDIDATE_ROOTS = [
    r"E:\one files\xwechat_files\<wxid>\db_storage",
    r"E:\个人文件\xwechat_files\<wxid>\db_storage",
]


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_watermark():
    if os.path.exists(WATERMARK_PATH):
        with open(WATERMARK_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"last_ts": 0}


def save_watermark(wm):
    with open(WATERMARK_PATH, "w", encoding="utf-8") as f:
        json.dump(wm, f, ensure_ascii=False, indent=2)


def detect_db_root():
    for p in CANDIDATE_ROOTS:
        if os.path.isdir(p):
            return p
    for drive in ("E:\\", "D:\\", "C:\\"):
        import glob
        hits = glob.glob(os.path.join(drive, "*", "xwechat_files", "*", "db_storage"))
        if hits:
            return hits[0]
    return None


def snapshot_and_decrypt(db_root, keyfile, tmpdir):
    src = os.path.join(db_root, "message", "message_0.db")
    if not os.path.exists(src):
        log(f"[error] 找不到 message_0.db: {src}")
        return None
    snap = os.path.join(tmpdir, "message_0.db")
    try:
        shutil.copy2(src, snap)
    except Exception as e:
        log(f"[error] 快照失败: {e}")
        return None
    plain = os.path.join(tmpdir, "message_0.plain.db")
    r = subprocess.run(
        [sys.executable, os.path.join(EXPORT_DIR, "decrypt_db.py"),
         snap, "--keyfile", keyfile, "--out", plain, "--no-verify"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        log(f"[error] 解密失败: {r.stderr[:200]}")
        return None
    return plain


def discover_msg_tables(cur):
    tabs = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    msg_tables = [t for t in tabs if t.lower().startswith("msg_")]
    name2id = {}
    for cand in ["Name2Id", "name2id"]:
        if cand in tabs:
            try:
                cols = [d[0] for d in cur.execute(f"SELECT * FROM {cand} LIMIT 1").description]
                ucol = next((c for c in cols if c.lower() in ("username", "user_name", "usename")), cols[0])
                mcol = next((c for c in cols if c.lower() in ("md5", "md5sum", "tablename", "table_suffix")), cols[1] if len(cols) > 1 else None)
                if mcol:
                    for u, m in cur.execute(f"SELECT {ucol}, {mcol} FROM {cand}"):
                        name2id[str(u)] = str(m)
            except Exception:
                pass
            break
    return msg_tables, name2id


def load_contact_names(contact_db_path):
    name_map = {}
    if not contact_db_path or not os.path.exists(contact_db_path):
        return name_map
    try:
        c2 = sqlite3.connect(contact_db_path)
        cur = c2.cursor()
        tabs = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "contact" not in tabs:
            c2.close()
            return name_map
        cols = [d[0] for d in cur.execute("SELECT * FROM contact LIMIT 1").description]
        ucol = next((c for c in cols if c.lower() in ("username", "username", "user_name")), cols[0])
        rcol = next((c for c in cols if c.lower() in ("remark", "remark")), None)
        ncol = next((c for c in cols if c.lower() in ("nick_name", "nickname", "nickname")), None)
        if not ucol:
            c2.close()
            return name_map
        for row in cur.execute(f"SELECT {ucol}, {rcol or 'NULL'}, {ncol or 'NULL'} FROM contact"):
            u, r, n = row
            disp = (r if r else n) if (r or n) else u
            name_map[str(u)] = str(disp)
        c2.close()
    except Exception:
        pass
    return name_map


def resolve_group_name(table, name2id, name_map):
    for u, m in name2id.items():
        if table.endswith(m) or m.endswith(table.split("Msg_")[-1]):
            return name_map.get(u, u)
    return None


def classify_message(text, config):
    rules = config.get("分类规则", {})
    keywords = config.get("关注关键词", [])
    groups = config.get("群聊", [])

    if not text:
        return "trash"

    text_lower = text.lower()

    notify_keywords = ["@所有人", "@all", "Loya-art", "智贤", "截止", "报名", "会议",
                       "直播", "培训", "通知", "接龙", "名单", "面试",
                       "学生会", "学术科技", "就业创业"]
    help_keywords = ["怎么", "如何", "请问", "求助", "求购", "出售", "二手",
                     "回收", "电脑推荐", "有没人", "有没有人", "谁能",
                     "求一个", "求带", "有人知道"]
    value_keywords = ["教程", "工具", "资源", "分享", "AI", "ComfyUI", "生图",
                      "漫剧", "板绘", "PS", "设计", "接单", "赚钱", "利润",
                      "成本", "报价", "话术", "运营", "引流", "变现", "干货",
                      "课程", "网盘", "免费", "开源"]

    for kw in notify_keywords:
        if kw in text:
            return "notify"
    for kw in help_keywords:
        if kw in text:
            return "help"
    for kw in value_keywords:
        if kw.lower() in text_lower:
            return "value"

    if keywords:
        for kw in keywords:
            if kw.lower() in text_lower:
                return "value"

    return "trash"


def query_new_messages(plain_db_path, contact_db_path, group_whitelist, last_ts):
    con = sqlite3.connect(plain_db_path)
    cur = con.cursor()
    msg_tables, name2id = discover_msg_tables(cur)
    name_map = load_contact_names(contact_db_path)

    cols_time = ["create_time", "createTime", "create_time_", "msgCreateTime", "time"]
    cols_content = ["message_content", "content", "msg_content", "messageContent"]
    cols_sender = ["talker", "talkerId", "talker_id", "sender", "username"]
    cols_is_sender = ["is_sender", "IsSender", "isSender", "msg_sender", "from_self"]
    cols_type = ["type", "msg_type", "msgType", "message_type"]

    new_messages = []

    for t in msg_tables:
        try:
            cols = [d[0] for d in cur.execute(f'SELECT * FROM "{t}" LIMIT 1').description]
        except Exception:
            continue

        t_time = next((c for c in cols_time if c in cols), None)
        if not t_time:
            continue

        group_name = resolve_group_name(t, name2id, name_map)
        if group_name and group_name not in group_whitelist:
            continue
        if not group_name:
            parts = t.split("Msg_")
            group_name = parts[-1] if len(parts) > 1 else t

        t_content = next((c for c in cols_content if c in cols), None)
        t_sender = next((c for c in cols_sender if c in cols), None)
        t_is_send = next((c for c in cols_is_sender if c in cols), None)
        t_type = next((c for c in cols_type if c in cols), None)

        q = f'SELECT * FROM "{t}" WHERE "{t_time}" > ? ORDER BY "{t_time}"'
        try:
            for row in cur.execute(q, (last_ts,)):
                vals = dict(zip(cols, row))
                ts = vals.get(t_time)
                content = vals.get(t_content)
                sender = vals.get(t_sender)
                is_send = vals.get(t_is_send) if t_is_send else None
                mtype = vals.get(t_type) if t_type else None

                if isinstance(ts, (int, float)) and ts > 1000000000:
                    pass
                else:
                    continue

                if isinstance(content, bytes):
                    content = content.decode("utf-8", "replace").strip("\x00")
                elif content is None:
                    content = ""
                content = str(content)

                if not content and mtype not in (None, 0):
                    content = f"[非文本消息 type={mtype}]"
                if not content:
                    continue

                if is_send is not None:
                    who = "我" if (is_send in (1, True, "1", "true", 1.0)) else (name_map.get(str(sender), str(sender)) if sender else "对方")
                else:
                    who = name_map.get(str(sender), str(sender)) if sender else ""

                new_messages.append({
                    "ts": int(ts),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                    "group": group_name,
                    "sender": who,
                    "content": content,
                    "table": t,
                })
        except Exception as e:
            log(f"[warn] 查询 {t} 出错: {e}")
            continue

    con.close()
    new_messages.sort(key=lambda m: m["ts"])
    return new_messages


def feishu_push(title, body):
    try:
        sys.path.insert(0, os.path.join(ASSISTANT_DIR, "scripts"))
        import feishu_notify
        ok, msg = feishu_notify.send_text(title, body)
        return ok, msg
    except Exception as e:
        return False, str(e)


def run_once(config, keyfile, db_root, tmpdir):
    wm = load_watermark()
    last_ts = wm.get("last_ts", 0)

    plain_msg = snapshot_and_decrypt(db_root, keyfile, tmpdir)
    if not plain_msg:
        return False

    contact_db = os.path.join(db_root, "contact", "contact.db")
    contact_plain = None
    if os.path.exists(contact_db):
        contact_snap = os.path.join(tmpdir, "contact.db")
        shutil.copy2(contact_db, contact_snap)
        contact_plain = os.path.join(tmpdir, "contact.plain.db")
        r = subprocess.run(
            [sys.executable, os.path.join(EXPORT_DIR, "decrypt_db.py"),
             contact_snap, "--keyfile", keyfile, "--out", contact_plain, "--no-verify"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            contact_plain = None

    group_whitelist = set(config.get("群聊", []))
    new_msgs = query_new_messages(plain_msg, contact_plain, group_whitelist, last_ts)

    if not new_msgs:
        log(f"[idle] 无新消息（watermark={last_ts}）")
        return True

    log(f"[new] 发现 {len(new_msgs)} 条新消息")

    notify_msgs = []
    help_msgs = []
    value_msgs = []

    for m in new_msgs:
        cat = classify_message(m["content"], config)
        m["category"] = cat
        if cat == "notify":
            notify_msgs.append(m)
        elif cat == "help":
            help_msgs.append(m)
        elif cat == "value":
            value_msgs.append(m)

    max_ts = max(m["ts"] for m in new_msgs)

    if notify_msgs:
        lines = []
        for m in notify_msgs:
            lines.append(f"[{m['time']}] [{m['group']}] {m['sender']}: {m['content'][:100]}")
        body = "\n".join(lines[:10])
        if len(lines) > 10:
            body += f"\n...还有 {len(lines)-10} 条"
        ok, msg = feishu_push(f"📢 重要通知 ({len(notify_msgs)}条)", body)
        log(f"[push notify] {'OK' if ok else 'FAIL: '+msg}")

    if help_msgs:
        lines = []
        for m in help_msgs:
            lines.append(f"[{m['time']}] [{m['group']}] {m['sender']}: {m['content'][:100]}")
        body = "\n".join(lines[:10])
        if len(lines) > 10:
            body += f"\n...还有 {len(lines)-10} 条"
        ok, msg = feishu_push(f"🔧 可帮忙的问题 ({len(help_msgs)}条)", body)
        log(f"[push help] {'OK' if ok else 'FAIL: '+msg}")

    if value_msgs:
        lines = []
        for m in value_msgs:
            lines.append(f"[{m['time']}] [{m['group']}] {m['sender']}: {m['content'][:100]}")
        body = "\n".join(lines[:10])
        if len(lines) > 10:
            body += f"\n...还有 {len(lines)-10} 条"
        ok, msg = feishu_push(f"💡 有价值信息 ({len(value_msgs)}条)", body)
        log(f"[push value] {'OK' if ok else 'FAIL: '+msg}")

    wm["last_ts"] = max_ts
    save_watermark(wm)
    log(f"[done] 已更新水位至 {max_ts} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(max_ts))})")
    return True


def main():
    ap = argparse.ArgumentParser(description="微信群聊实时监控")
    ap.add_argument("--interval", type=int, default=30, help="轮询间隔秒数（默认30）")
    ap.add_argument("--once", action="store_true", help="只跑一次（测试）")
    args = ap.parse_args()

    config = load_config()
    keyfile = os.path.join(EXPORT_DIR, "db_key.json")
    if not os.path.exists(keyfile):
        log(f"[FATAL] 密钥文件不存在: {keyfile}")
        log("请先运行: python capture2.py --watch  （然后重启微信抓取密钥）")
        sys.exit(1)

    db_root = detect_db_root()
    if not db_root:
        log("[FATAL] 找不到微信 db_storage 目录")
        sys.exit(1)
    log(f"[init] DB目录: {db_root}")
    log(f"[init] 轮询间隔: {args.interval}s")
    log(f"[init] 群白名单: {len(config.get('群聊', []))} 个群")

    tmpdir = tempfile.mkdtemp(prefix="wx_live_")

    if args.once:
        log("=== 单次模式 ===")
        run_once(config, keyfile, db_root, tmpdir)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return

    log("=== 实时监控启动 ===（Ctrl+C 停止）")
    try:
        while True:
            try:
                run_once(config, keyfile, db_root, tmpdir)
            except Exception as e:
                log(f"[error] {e}")
                log(traceback.format_exc()[:500])
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("=== 监控已停止 ===")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
