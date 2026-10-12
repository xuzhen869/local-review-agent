# 个人工作/学习回顾 Agent（桌面软件版）

本地优先的个人 Agent：自动捕获日常痕迹（git 提交 / 文档变更 / 剪贴板），生成周报与学习回顾，全流程可观测 + 敏感操作门控。**纯桌面软件形态，无需浏览器、无需手动起服务**。

> **不上屏监控，只从用户日常留下的痕迹全自动捕获，本地生成周报 + 学习回顾。**

## 核心特性

| 方向 | 能力 |
|---|---|
| 形态 | **桌面软件**：主窗口（总览/事件流/番茄钟/审计/数据源设置）+ 托盘柴犬宠物，双击即用 |
| 数据主权 | 全本地存储，SQLCipher AES-256 全库加密，Windows DPAPI 托管主密钥 + Argon2id 恢复口令 |
| 全自动捕获 | 可插拔数据源适配器（git / 文档，ICS/邮件/剪贴板规划中），增量游标 + 防抖 |
| 桌面宠物 | 置顶卡通柴犬（PySide6）：一键截图（OCR）/ 记录知识点 / 今日周报 / 喂养系统 / 番茄闹钟 |
| 安全 | PII 脱敏前置、三级敏感分级、高危操作 fail-closed 门控、审计日志独立 |
| 成本可控 | LLM 模型分层 + 内容 hash 缓存 + 并发限流 + 本地 Ollama 可选 |

## 技术栈

- 后端：Python 3.13 / FastAPI / aiosqlite + sqlcipher3 / watchdog
- 桌面端：PySide6 + cv2 + Pillow + pystray（主窗口 pet/panels/dashboard.py + QPainter 图表）
- 打包：PyInstaller（onedir + windowed）
- LLM：DeepSeek（默认，OpenAI 兼容协议）

## 快速开始

### 桌面软件（推荐）
双击 PyInstaller 打包产物 `回顾Agent.exe` —— 自动拉起本地后端、打开主界面与托盘柴犬，退出自动清理，无需手动起服务。

### 开发模式
```bash
# 创建虚拟环境并安装依赖（首次）
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 启动桌面软件（真实环境）／测试环境
cd ..
backend\.venv\Scripts\python.exe launch.py
backend\.venv\Scripts\python.exe launch.py --test
```

首次启动自动创建 `~/.local_review_agent/config.yaml`，数据源在桌面「数据源设置」页或该文件 `sources:` 段填写。

### 语义检索模型（可选）

语义检索依赖本地 ONNX 嵌入模型 `backend/app/agent/bge/model.onnx`（约 91MB，体积过大未纳入版本库）：

```bash
pip install huggingface_hub
huggingface-cli download Xenova/bge-small-zh-v1.5 --local-dir backend/app/agent/bge
```

模型缺失时不会报错——语义检索自动降级为纯关键词检索，其余功能不受影响。

## 配置项

| 项 | 说明 |
|---|---|
| `LOCAL_REVIEW_AGENT_HOME` | 数据目录覆盖（默认 `~/.local_review_agent`） |
| `server.host/port` | 服务绑定（默认 127.0.0.1:8100，本地回环） |
| `security.lock_memory` | SQLCipher mlock 内存锁，需管理员权限，默认 false |
| `logging.level` | 日志级别（INFO 起） |

> 敏感信息（邮箱密码、API Key）一律入 `secrets` 加密库，禁止写入 config.yaml。

## API（本地回环 127.0.0.1:8100，供桌面端调用）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 运维探针（适配器状态 / 队列 / 磁盘 / 密钥） |
| GET | `/api/events` | 事件查询（脱敏） |
| GET/POST | `/api/sources` | 数据源配置读写 + 热重载 |
| GET | `/api/audit` | 审计日志 |
| POST | `/api/capture/screenshot` | 截图上传（multipart）→ 保存 + OCR + 入库 |
| POST | `/api/capture/knowledge` | 知识点录入 |
| GET | `/api/summary/today` | 今日小结（DeepSeek LLM） |
| GET/POST | `/api/llm/*` | LLM 状态 / Key 录入 |
| GET/POST | `/api/pet/*` | 宠物状态 / 打卡 / 喂食 |
| GET/POST | `/api/pomodoro/*` | 番茄钟配置 / 统计 / 记录 / 会话 |

## 里程碑

- [x] **M1 基础设施**：SQLCipher 全库加密 + 事件总线 + Git/FS 适配器 + 结构化日志 + 数据源配置
- [x] **桌面软件重构**：移除 Web 端，主窗口 + 一键启动 + PyInstaller exe
- [x] **桌面宠物 / 喂养系统 / 番茄闹钟**：柴犬 + 截图OCR + 知识点 + 周报 + 喂养 + 番茄状态机
- [ ] **M2 Agent 核心**：LLM 分类/摘要/聚合、周报、学习回顾
- [ ] **M3 安全+可观测**：门控审批流、Trace 回放
- [ ] **M4 数据源扩展**：ICS 日历、IMAP 邮件、剪贴板捕获
