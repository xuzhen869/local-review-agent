# 项目状态报告（PySide6 桌面版 · 2026-08-06）

> 用途：记录「做了什么、还剩什么、下一步干什么」。
> 阅读顺序：§2 如何运行 → §3 已完成 → §4 未完成 → §10 下一步指引。

---

## 0. 一句话现状

**回顾 Agent 已重构为纯桌面软件（Web 端已移除），P1-P3 功能全部上线**：
- 桌面主窗口（PySide6 Dashboard：总览/**周报**/**问答**/事件流/番茄钟/审计/数据源设置，7 页）+ 托盘柴犬 + 本地无窗口后端
- **打包为独立 exe**（桌面 `C:\Users\xu\Desktop\回顾Agent\回顾Agent.exe`），双击即用，退出自动清理
- SQLCipher 全库加密、git/fs 捕获、喂养、番茄状态机、DeepSeek、RapidOCR 全部保留
- **已完成**：M1 基础设施、桌面宠物、喂养、番茄、桌面软件重构、**P1（分类/周报/问答）**、**P2（宠物对话/待办/健康提醒）**、**P3（学习回顾间隔重复）**
- **未做**：P0 活跃窗口追踪/自动截屏、M3 门控/trace、ICS/IMAP/剪贴板适配器

---

## 1. 项目概览

| 项 | 值 |
|---|---|
| 项目位置 | `C:\Users\xu\Desktop\学习陪伴助手\` |
| 定位 | 本地优先个人 Agent，桌面软件形态，不上屏监控，自动捕获日常痕迹 → 周报/学习回顾 |
| 软件入口 | 桌面 `C:\Users\xu\Desktop\回顾Agent\回顾Agent.exe`（双击）；开发 `python launch.py` |
| 主入口代码 | `launch.py`（一键启动：自动拉起无窗口后端 + 主窗口 + 托盘柴犬） |
| 主窗口 | `pet/panels/dashboard.py`（PySide6，左侧导航 + 右侧内容，7 页面） |
| 后端 | FastAPI 127.0.0.1:8100（无窗口子进程，退出自动终止） |
| 技术栈 | Python 3.13 / FastAPI / aiosqlite+sqlcipher3 / watchdog / httpx / PySide6 / cv2 / Pillow / pystray / **PyInstaller** |
| 加密 | SQLCipher 全库 AES-256 + Windows DPAPI 主密钥 + Argon2id 恢复口令 |
| LLM | DeepSeek（key 已录入 secrets 加密库） |

### 目录结构
```
学习陪伴助手/
├── backend/                 # 本地服务内核（纯 API，无 Web 托管）
│   ├── app/
│   │   ├── main.py          # FastAPI 入口 + 全部 API（已移除 Web 静态托管）
│   │   ├── runtime.py       # 运行时容器
│   │   ├── config.py        # config.yaml（默认模板已修 repos:[]/dirs:[] 空列表 bug）
│   │   ├── petstate.py      # 喂养系统 / 番茄状态机服务
│   │   ├── adapters/        # base/git/fs；ics/imap/clipboard 未实现
│   │   ├── db/ security/ llm/ bus.py scheduler.py capture.py
│   └── requirements.txt
├── pet/                     # 桌面端（PySide6）
│   ├── app.py               # ★ AppController：装配主题 + 主窗口 + 托盘柴犬
│   ├── api_client.py        # HTTP 客户端（urllib，QThread 内调用）
│   ├── screenshot.py        # 一键截图（全屏遮罩 + 区域选择）
│   ├── panels/              # ★ 主窗口页面（dashboard.py 7 页 + media.py 视频横幅）
│   ├── widgets/             # 可复用组件（theme/charts/cards/buttons/header/bubble）
│   ├── dialogs/             # 弹窗（chat/review/todos/pomodoro/pending/solution/key）
│   ├── core/                # 宠物内核（petstate/sprite/speech）
│   └── （顶层 dashboard.py/media.py/chat.py/... 为废弃 tkinter 旧版，不参与打包）
├── launch.py                # ★ 软件入口：双进程模式（开发=venv python / 打包=--run-backend）
├── run.py                   # 后端单独启动入口（兼容保留）
├── 启动回顾Agent.vbs        # 过渡启动脚本（双击，无控制台）
├── assets/icon.ico          # 柴犬图标（打包用）
├── dist/ReviewAgent/        # PyInstaller 打包产物（项目内副本）
├── logs/                    # app.log / audit.log / backend_stdout.log / backend_stderr.log
└── PROJECT_STATUS.md        # 本文件
```

> Web 端（frontend/ React + Vite + ECharts）已按主人决策整体移除（2026-08-05）。

---

## 2. 如何运行

### 桌面软件（最终交付）
```bash
# 双击桌面：
C:\Users\xu\Desktop\回顾Agent\回顾Agent.exe
```
- 自动拉起无窗口后端（同一 exe 的 --run-backend 子进程）→ 打开主窗口 + 托盘柴犬
- 退出（托盘「退出」/主界面关闭后托盘仍可开）自动终止后端子进程，无残留
- 默认真实数据目录 `~/.local_review_agent/`

### 开发模式
```bash
cd C:\Users\xu\Desktop\学习陪伴助手
backend/.venv/Scripts/python.exe launch.py          # 真实环境
backend/.venv/Scripts/python.exe launch.py --test    # 测试环境（lra_test，含 12 条演示事件）
```
- 或过渡脚本 `启动回顾Agent.vbs`（双击，无控制台窗口）

### 单独起后端（调试用）
```bash
LOCAL_REVIEW_AGENT_HOME="C:/Users/xu/AppData/Local/Temp/lra_test/home" backend/.venv/Scripts/python.exe run.py
```

### 重新打包 exe
```bash
backend/.venv/Scripts/python.exe -m pip install pyinstaller   # 已装（6.21.0）
backend/.venv/Scripts/python.exe -m PyInstaller --onedir --noconfirm --clean --windowed \
  --name "ReviewAgent" --paths backend --paths pet \
  --collect-data rapidocr_onnxruntime --collect-submodules rapidocr_onnxruntime \
  --icon assets/icon.ico launch.py
# 产物 dist/ReviewAgent/ → 复制到桌面「回顾Agent」目录
```
> 注意：pip.exe 在本环境无输出/异常，一律用 `python -m pip`。

### 关键：venv 在 `backend/.venv`，所有命令用 `backend/.venv/Scripts/python.exe`

---

## 3. 已完整完成（含验证证据）

### 3.0 桌面软件重构（2026-08-05，本次）✅

> ⚠️ 本节为 08-05 的 tkinter 过渡版记录；08-06 已按 Orbis 设计系统迁移至 PySide6（§11），主窗口现为 `pet/panels/dashboard.py`。

- 移除 Web 端：`frontend/` 目录删除（释放 105M），后端 `_serve_frontend()` 托管段删除，`/` 返回 404（纯 API）
- **主窗口 dashboard.py**（08-06 起 `pet/panels/dashboard.py`，PySide6，7 页面）：
  - 总览：4 指标卡 + 热力图 + 近14天趋势柱状图 + 来源环形图 + 最近活动（15s 轮询）
  - 事件流：来源筛选 + 表格；审计：表格
  - 番茄钟：统计卡片 + 打开完整 PomodoroDialog；数据源设置：表单 + 保存热重载 + 适配器状态
- **图表**（`pet/widgets/charts.py`）：柱状图/热力图/环形图，QPainter 绘制，暗色色板统一
- **一键启动 launch.py**：检测/拉起后端（开发=venv python，打包=exe --run-backend 双进程）→ 等待 health → 主窗口 + 柴犬；退出统一 terminate
- **PyInstaller 打包**：onedir + windowed，柴犬图标，收集 RapidOCR 模型；**exe 实测通过**
- 修复 3 个 bug：
  1. Dashboard `_nav_buttons` 初始化顺序（AttributeError）
  2. `_run_async` 异步回调竞态（页面切换后更新失效控件 TclError）→ winfo_exists 防护
  3. config.py 默认模板 `repos:`/`dirs:` 为 None 导致真实环境首次启动校验失败 → 改 `[]`
  4. launch.py GBK 控制台 print 崩溃 → reconfigure(errors=replace) + 安全打印

### 3.1 M1 基础设施 ✅（不变）
SQLCipher 全库加密 / DPAPI 主密钥 + Argon2id 恢复口令 / 事件总线 / Git+FS 适配器（增量游标/防抖/忽略）/ JSON Lines 日志 / PII 脱敏（8 类）/ 幂等去重 / 数据源热重载

### 3.2 桌面宠物 ✅（重构后仍为托盘柴犬 + 悬浮，可开关）
截图 OCR / 知识点 / 今日周报（DeepSeek 实测 3.1s）/ 喂养 / 番茄闹钟 全链路正常

### 3.3 喂养系统 ✅：饥饿衰减 / 打卡 / 喂食 / 委屈脸 / SQLite 持久化

### 3.4 番茄闹钟 ✅：完整状态机（focus→short→…→long）、可配置、跨重启恢复、统计 API、进度环、Web 版番茄页已并入桌面（统计卡片）

### 3.5 P1-P3 功能（2026-08-05，本次）✅
**P1 周报核心**
- **事件分类**：`backend/app/agent/classify.py`，LLM 批量打标 6 类 + 规则降级；`POST /api/classify`；实测 14 条全部分类成功
- **按周聚合周报**：`backend/app/agent/report.py`，metrics/timeline/categories + LLM conclusion **绑定 source_event_ids 防编造**；`POST/GET /api/report/weekly`、`GET /api/reports`；Dashboard「周报」页；实测主线/成就/阻塞/下周均带溯源
- **历史问答**：`backend/app/agent/qa.py`，时间意图解析 + 关键词检索（整段+2-gram）+ LLM 带溯源回答；`POST /api/qa/ask`；Dashboard「问答」页；实测回答质量好

**P2 宠物升级**
- **宠物 AI 对话**：`backend/app/agent/chat.py`，柴犬「阿柴」人设 + 最近 10 条事件上下文 + 历史；`POST /api/pet/chat`；`pet/chat.py` 聊天弹窗
- **待办 + 健康提醒**：`todos` 表 + `backend/app/todos.py` CRUD + `/api/todos/due`；`pet/todos.py` TodoDialog；喝水 45min/休息 60min/待办到期 5min 轮询（托盘通知）

**P3 学习回顾**
- **间隔重复**：`review_cards` 表 + `backend/app/agent/learnings.py`，LLM 提取知识卡片（去重）+ SM-2 四档打分；`POST /api/learning/extract`、`GET /api/learning/due`、`POST /api/learning/review/{id}`；`pet/review.py` 复习弹窗；实测提取 8 卡片、SM-2 间隔正确

**关键修复（本次实证）**：所有弹窗**不得用 `transient(root)`** —— 主 root withdraw 时 transient 子窗口在 Windows 上不可见（mapped=0）。宠物/周报/问答/待办/复习弹窗已全部移除。

---

## 4. 未完成 / 待办（按优先级）

### P0 真实使用准备
- [ ] **配置真实数据源**（主人未配）：桌面「数据源设置」页或 `~/.local_review_agent/config.yaml` 填真实 git 仓库/文档目录
- [ ] **P0 数据基础**（竞品核心，周报质量天花板）：活跃窗口/应用时间追踪 + 自动周期截屏（活动追踪/TimeLens/Dayflow 的立足点）

### P1 剩余（Agent 深化）
- [ ] 主题聚合 `aggregate` / 日摘要 `daily_summaries` / LLM 缓存 `llm_cache`（表已建未用）
- [ ] Agent Loop 状态机（perceive/plan/act/observe/reflect + trace）

### P2 M3 安全+可观测
- [ ] 高危操作门控审批流（approval_requests 表已建未用）
- [ ] Trace 记录/决策回放（traces 表已建未用）
- [ ] 敏感分级（grade 全 default internal）

### P3 数据源适配器 / 其他
- [ ] IcsAdapter / ImapAdapter / ClipboardAdapter（config 结构已预留）
- [ ] **PyInstaller 打包的 RapidOCR 懒加载回归**：打包后 OCR 首次调用需确认模型路径（_MEIPASS）正常
- [ ] 打包 exe 无数据源时首次引导（当前空库直接可用，需主人填真实源）
- [ ] 宠物/对话/复习弹窗的 toplevel 置顶体验回归（transient 移除后验证任务栏行为）

---

## 5. 关键架构与决策记录（为什么这样）

| 决策 | 原因 | 影响 |
|---|---|---|
| **tkinter → PySide6（最终）** | 08-05 因网络慢（~15KB/s）暂用 tkinter；08-06 网速恢复（~67KB/s），按 Orbis 设计系统迁移至 PySide6（Qt） | pet/panels/ + pet/widgets/（PySide6）；顶层 tkinter 旧版废弃 |
| **Web 端移除 → 桌面软件**（主人拍板 2026-08-05） | 不要浏览器，双击即用 | 主窗口 dashboard.py 承载全部功能；frontend/ 删除 |
| **保留 FastAPI 无窗口内核**（主人拍板） | 加密/适配器/LLM 逻辑复用，风险最低 | launch.py 自动拉起/终止后端子进程 |
| **PyInstaller 双进程模式** | 打包后无独立解释器跑 run.py | GUI 进程 Popen 自身 `--run-backend` 子进程 |
| **config 默认模板 repos:/dirs: 空列表** | YAML 注释后值为 None，pydantic 校验失败 | 已修 `[]`（真实环境首启暴露） |
| **SQLCipher 全库加密**（主人拍板） | 全库加密胜字段级 | sqlcipher3==0.6.2 |
| **宠物=瘦客户端 HTTP 调后端** | 避免多进程写库并发 | 桌面端无数据逻辑，全走 API |
| **数据源用户填写式** | 主人拍板不预置 | config.yaml + 桌面「数据源设置」页 + 热重载 |

---

## 6. 工程踩坑备忘（重要，勿重蹈）

1. **网络慢**：大依赖需后台慢慢下 + 懒加载；实测 PyPI 当前 ~67KB/s（比历史 15KB/s 快），PyInstaller 安装约 1 分钟。
2. **`pip.exe` 在本 bash 环境无输出/异常退出** → 一律用 `backend/.venv/Scripts/python.exe -m pip ...`。
3. **PyInstaller onedir**：windowed 模式无 stdout（print 崩）→ launch.py 已安全打印 + reconfigure；`--paths backend` 收集后端包；`--collect-data rapidocr_onnxruntime` 收 OCR 模型。
4. **打包后子进程**：不能 `subprocess` 调 venv python（不存在）→ `sys.executable --run-backend`。
5. **Tk 异步回调竞态**：页面销毁后回调更新失效控件 → `_run_async` 加 `winfo_exists` 防护；`except Exception as exc` 内 lambda 延迟执行须 `lambda e=exc:`（默认参数绑定）。
6. **config.py 默认模板**：YAML 键后仅注释 → 值 None → pydantic list 校验失败。模板必须给 `[]`。
7. **GBK 控制台乱码**是显示问题，数据/日志为 UTF-8；验证用 ensure_ascii=False 或写文件。
8. **cipher_memory_security = ON** 普通权限下 SQLCipher 原生崩溃 → 默认 false。
9. **sqlcipher3==0.6.2**（PyPI 无 binary 包名）；sqlcipher3 驱动参数需 aiosqlite monkeypatch + 显式 Row factory。
10. **bash heredoc 不可用**（返回 127）；用脚本文件 + 绝对路径执行。
11. **后台任务进程形态**：launch 起的 GUI/后端各带 spawn 子进程；判断实例看窗口标题，重启用 taskkill //F //PID。
12. **宠物诊断**：`logs/pet_events.log` + `logs/pet_errors.log`（Tk callback 异常）；主窗口问题先看 pet_errors.log。
13. **DeepSeek key 不明文**：已入 secrets 加密库，勿写进文件/日志。

---

## 7. 数据与配置

| 项 | 位置 | 说明 |
|---|---|---|
| 测试数据 | `C:\Users\xu\AppData\Local\Temp\lra_test\` | home(config/DB/secrets)、repo(git)、docs；含 12+ 条事件 |
| 真实数据 | `~/.local_review_agent/` | 默认；已初始化（config.yaml/data.db/secrets），空库无数据源 |
| 配置 | `config.yaml`（数据目录下） | 数据源、安全、日志参数 |
| 数据库 | `data.db` | SQLCipher 加密 |
| 密钥 | `secrets/master.key.enc` + `recovery.enc` | DPAPI 保护 + 恢复口令 |
| 日志 | `logs/`（app.log + audit.log + backend_*.log） | JSON Lines |

---

## 8. API 清单（后端 127.0.0.1:8100，无 Web 托管）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/health | 运维探针（适配器/队列/磁盘/事件数） |
| GET | /api/events | 事件查询（脱敏；source/category/limit/offset） |
| GET/POST | /api/sources | 数据源配置读写 + 热重载 |
| GET | /api/audit | 审计日志 |
| POST | /api/capture/screenshot | 截图上传 → 保存 + OCR + 入库 |
| POST | /api/capture/knowledge | 知识点录入 |
| GET | /api/summary/today | 今日小结（DeepSeek） |
| GET | /api/llm/status | LLM 配置状态 |
| POST | /api/llm/key | 录入 DeepSeek key（加密存储） |
| GET | /api/pet/state | 宠物状态 |
| POST | /api/pet/punch | 每日打卡 |
| POST | /api/pet/feed | 喂食 |
| GET | /api/pomodoro/config · POST 保存 | 番茄钟配置 |
| GET | /api/pomodoro/stats | 番茄统计 |
| POST | /api/pomodoro/record | 记录番茄 |
| GET/POST | /api/pomodoro/session | 进行中番茄（跨重启恢复） |

---

## 9. 依赖清单（backend/requirements.txt + 打包）

fastapi / uvicorn / aiosqlite / **sqlcipher3==0.6.2** / pydantic / pyyaml / watchdog / cryptography / httpx / python-multipart / pillow / pystray / **rapidocr_onnxruntime**（已装，OCR 生效）/ **pyinstaller 6.21.0**（打包用，已装）

---

## 10. 下一步开工指引（给未来的我）

**主人打开我时**，第一件事：说「继续项目」，我应该：
1. 读本文件 §3/§4 确认状态
2. 询问主人当前优先级，不要自作主张直接写 M2
3. 若主人只说「继续」，默认顺序：**P0 配置真实数据源 → P1 周报/回顾（M2）→ P2 门控/trace（M3）**

**当前未决事项**（主人选择）：
- 桌面软件是否满足预期，是否需要把 exe 放到其他位置/改名
- 是否配置真实数据源（需主人提供 git 仓库/文档目录路径）
- M2 周报/回顾是否现在做（DeepSeek key 已有）
- 打包 exe 是否需要图标进一步美化 / 单文件版（--onefile 体积更大但单文件）
- 是否需要 Imap/ICS 适配器（需主人邮箱/日历信息）

**打包回归待办**：打包 exe 中 RapidOCR 首次 OCR 调用需实测（模型在 _MEIPASS 内路径是否正确），截图功能在 exe 形态下完整走一遍。

---

## 11. Orbis UI 重构 → PySide6（2026-08-06 完成）

**网页 → 桌面映射**：Orbis.Nft 模板 → 软件 UI。固定暗色太空主题（移除深浅切换）。

**设计系统**（pet/widgets/theme.py 单一 space 主题）：
- 深空蓝底 #010828 + 霓虹绿 #6FFF00 + 紫渐变 #7C3AED→#B724FF
- Anton（拉丁标题/数字）+ Condiment（手写点缀）+ YaHei UI（中文）+ Cascadia Mono（标签）
- 玻璃卡：CARD #0B1133 + 顶部高光 GLASS_HI #3A4A8A + 边缘 GLASS_EDGE #1C2450

**4 Section → 7 页面映射**：
| 网页 | 桌面 | 视频 |
|---|---|---|
| Hero | 总览（顶部视频横幅） | hero.mp4 |
| About | 周报（视频带） | about.mp4 |
| Collection 3卡 | 总览 Collection 行（3 方形缩略 + 紫色 RARITY 按钮） | card1-3.mp4 |
| CTA | 数据源设置（底部视频带 + JOIN US·GO BEYOND） | cta.mp4 |

**新组件**：
- `pet/panels/media.py`：QMediaPlayer 视频横幅，cover 裁切，无视频→黑底降级（预留空间），页面切出即停
- `pet/widgets/`：玻璃卡（cards）/顶部高光/霓虹下划线/滚轮（ScrollFrame）
- `pet/panels/dashboard.py`：Hero 视频横幅 + 指标卡 + Bento 网格 + Collection 行 + 紫色圆按钮

**已交付**：
- exe 已重打包替换桌面版 `C:\Users\xu\Desktop\ReviewAgent\`（264M，含 cv2/numpy + assets 三件套）
- dev 冒烟 + exe 冒烟均通过：无 pet_errors、后端启动链路完整、8100 健康
- 7 页验收截图 → 桌面 `ReviewAgent验收截图\`
- 修复：`_run_async` 延迟 lambda 的 exc 闭包 NameError（默认参数绑定 e=exc）

**待办（主人后补）**：6 个视频（hero/about/card1/card2/card3/cta.mp4）放入 `assets\videos\` → 重打包 exe → 替换桌面版。视频缺失时界面自动黑底，不影响功能。

**✅ 已完成（2026-08-06 13:10）**：6 视频经本地 Clash 代理（127.0.0.1:7890/7897）从 CloudFront 全部下载成功（共 ~73MB，cv2 逐一验证可解码：hero 1860x1112 / about 1844x1124 / card1-3 1320x1568 竖卡 / cta 1784x1160，均 24fps）。exe 已重打包含视频并替换桌面版（334M）。dev 实测 6 视频全部出帧（VideoSurface has_video=True + 画布图片 item 生成）。**CloudFront 直连被封锁，走 127.0.0.1:7890/7897 代理可访问**（curl -x 或 HTTPS_PROXY 环境变量）。
