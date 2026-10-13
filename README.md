# 微信信息管理系统 · 部署指南（分享版）

> 本包为分享版，**已脱敏**：移除了原作者的学校/校区/群名/人名/微信 ID/webhook 等全部私人信息（见文末「脱敏说明」）。
> 目标机器需自己抓微信 DB key（两机密钥各自独立），并按本指南替换占位符。

## 一、环境要求

- Windows 10/11 + Python 3（无需管理员权限）
- 微信 **4.x** 电脑版（3.x 不适用），每天开机登录
- Claude Code（`claude.exe` 可命令行 headless 调用）
- Obsidian（可选，线二知识库用；`winget install Obsidian.Obsidian` 装）
- ffmpeg（可选，录音转 m4a 用）

```powershell
pip install frida pycryptodome win11toast requests keyboard sounddevice numpy faster-whisper
```

## 二、安装步骤

### 1. 复制到安装目录

把整个 `wechat-assistant-skill` 文件夹放到你的工作目录，如 `D:\wechat-assistant`。

### 2. 填写配置

```powershell
copy config.example.json config.json        # 然后按 config.json 里的注释填写：
                                            # 飞书webhook、群聊名单、公众号名单、
                                            # 忽略校区、vault目录、whisper模型目录、
                                            # 审核期结束日（建议设为今天+3天）
copy 分类规则模板.md 分类规则.md             # 按你的场景改写（所有 ⚙️ 标注处）
copy 行程表模板.md 行程表.md
copy kanban\data.example.json kanban\data.json
```

### 3. 替换占位符

把 prompts 里的 `<SKILL_DIR>` 全部替换成你的安装路径：

```powershell
$dir = "D:\wechat-assistant"
Get-ChildItem -Recurse -File -Filter *.md | Where-Object { $_.FullName -match 'prompts' } | ForEach-Object {
    $c = [System.IO.File]::ReadAllText($_.FullName)
    [System.IO.File]::WriteAllText($_.FullName, $c.Replace('<SKILL_DIR>', $dir), [System.Text.UTF8Encoding]::new($false))
}
```

> 脚本（scripts/）不需要改路径——全部按自身文件位置自动定位（`__file__` 推导），包放哪都能跑。

### 4. wechat-export 抓 key（一次性，约 15 分钟）

见 `wechat-export\接手说明.md`：
1. 全局搜索替换你的 wxid 目录名 / 微信安装路径 / 微信版本号（文档里有对照表）
2. 完全退出微信 → `python phase2e_login.py`（spawn 微信 + 扫码登录 + 观察 120 秒）→ `python phase3_verify3.py`（离线验证出 db_key.json）
3. `python decrypt_db.py --count` 验证解密 → `python daily_export.py` 全量导出

### 5. 配置 Windows 计划任务（机器活）

| 时间 | 动作 | 命令 |
|------|------|------|
| 12:00 | 增量导出 | `python -u wechat-export\daily_export.py`（工作目录 wechat-export） |
| 18:00 | 增量导出 | 同上 |
| 22:00 | 增量导出 | 同上 |
| 12:10 | 午班分析 | `scripts\run_analysis.bat noon` |
| 18:10 | 晚班分析 | `scripts\run_analysis.bat evening` |
| 21:00 | 行程提醒 | `scripts\run_analysis.bat trip` |
| 22:10 | 日报班 | `scripts\run_analysis.bat daily` |
| 23:00 | 导出兜底 + 媒体归档 | 同 12:00（wechat-export 自带流程） |

PowerShell 示例（注意别用 schtasks 包 cmd，Win11 上解析会失败）：

```powershell
$action = New-ScheduledTaskAction -Execute "python.exe" -Argument "-u daily_export.py" -WorkingDirectory "D:\wechat-assistant\wechat-export"
$trigger = New-ScheduledTaskTrigger -Daily -At "12:00"
Register-ScheduledTask -TaskName "WeChatExportNoon" -Action $action -Trigger $trigger -Force
```

### 6. 配置 Claude Cron（脑子活）

在 Claude Code 会话里让 Claude 建 5 个 Cron（会话级，Claude 关闭即失效，每次新会话让它检查重建）：

| Cron 表达式 | 班次 | 干什么 |
|------------|------|--------|
| `10 12 * * *` | 午班 | 按 prompts/prompt_noon.md 流程：分析→核对→入库→推送 |
| `10 18 * * *` | 晚班 | 同午班 |
| `0 21 * * *` | 行程 | 按 prompts/prompt_trip.md：明天+未来3天行程提醒 |
| `10 22 * * *` | 日报 | 按 prompts/prompt_daily.md：日报五件套+复盘底稿 |
| `30 22 * * *` | 备份 | 检查当天导出产物，缺摘要补生成 |

> 计划任务（机器活）与 Cron（脑子活）都跑同一份 prompt 文件，靠 `analysis_watermark.json` 水位防重复，同一班次只跑一个。

### 7. 首次审核期（前 3 天）

- 审核期内所有新信息先出「待审清单」给你过审（对话里回复"全过 / 删N号 / N号改XXX"）
- 每次纠正都会被写进 分类规则.md，AI 判断标准越跑越准
- 3 天后全自动模式：班次核对通过直接入库，你随时抽查

### 8. 日常使用

- 双击 `kanban` 入口（`open_kanban.py` 或 打开看板.bat）开看板；**不要直接双击 index.html**（file:// 下浏览器读不到 data.json）
- 录音：全局热键 `Ctrl+Alt+R` 或看板按钮，停止后自动转文字 + 入库复盘
- 看板由 recorder.py 常驻进程提供本地服务（127.0.0.1:8710），挂了就重开

## 三、脱敏说明

本分享版已做以下处理：

| 类别 | 处理方式 |
|------|---------|
| 飞书 webhook | 换成 `REPLACE_WITH_YOUR_WEBHOOK` 占位符 |
| 微信 wxid / Windows 用户名 | 换成 `wxid_YOURWXID` / `YOURNAME` 占位符 |
| 全部群聊名单（17 个） | 换成「示例群A/B…」占位 |
| 公众号名单 | 换成示例名称 |
| 学校名 / 校区名 | 从代码和文档中移除，改为 config 可配置项（忽略校区） |
| 群名相关的看板过滤规则 | 换成通用关键词 + 注释 |
| 人名 / 工资 / 课程等私人内容 | 从分类规则中全部移除 |
| 看板数据（data.json）/ 待审 / 日志 | 不打包，只给空的 data.example.json |
| 磁盘绝对路径 | 脚本改为 `__file__` 自动定位；文档用 `<SKILL_DIR>` 占位 |

## 四、常见问题

- **计划任务每天返回码 1、日志不写**：大概率是用 `schtasks /TR` 包了 cmd；用 PowerShell 的 Register-ScheduledTask 重建
- **看板打不开**：recorder.py 没在跑；`python scripts\recorder.py` 手动拉起
- **密钥失效 / 换机器**：重跑 wechat-export 第一步（两机密钥独立）
- **WAL 滞后**：微信 WAL 环形复用不合并，主库快照可能滞后几条消息，次日 checkpoint 自动补齐
- **headless 班次没跑**：检查 Claude Cron 是否存活（会话级，会话一关就死）——让 Claude 每次新会话开始时检查重建
