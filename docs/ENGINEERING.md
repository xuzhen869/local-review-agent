# 工程规范

## 架构（依赖单向）

- `shared/` —— 契约层（数据模型），**两端唯一事实源**，它不依赖任何人
- `backend/` —— FastAPI 后端（API 路由 / LLM agents / SQLCipher 加密库）
- `pet/` —— PySide6 前端（UI + `api_client.py` HTTP 客户端）

依赖方向只允许：`pet → shared`、`backend → shared`。**禁止反向依赖**。

## 交付门禁

改完任何代码，提交前必须过这道闸：

```
cd 项目根 && backend/.venv/Scripts/python.exe selfcheck.py
```

一条命令查三层防线：

1. 语法编译
2. 契约两端成对（后端 `response_model` 必须来自 `shared.contracts`；前端调用契约接口必须返回契约类型）
3. pytest 行为测试

**FAIL 禁止提交，修到 PASS 为止。**

## 改动铁律

1. 改任何**接口**（前后端都动）必须按此顺序：
   - a. 改 `shared/contracts.py` —— 唯一契约，禁止另存副本（双份会漂移）
   - b. 后端路由挂 `response_model=对应模型` —— 禁止 `dict[str, Any]` 裸返回
   - c. 前端 `pet/api_client.py` 对应方法返回该契约类型 —— 禁止 `Any`
2. 改任何**业务逻辑**（尤其 `backend/app/agent/router.py`）后必须跑 `selfcheck.py`（内含 pytest，不必单独再跑）
3. **禁止**：
   - 绕过契约手写 dict 返回
   - 直接改加密数据库 schema 而不说明迁移方案
   - 修改打包产物（`回顾Agent.exe` 等）

## 已知坑（改 UI 前必读）

- 真实 UI 在 `pet/panels/`、`pet/widgets/`；顶层 `pet/dashboard.py`、`pet/media.py` 是**废弃 tkinter 旧版，不参与打包** —— 改 UI 必须改 `panels/widgets`，别被旧文件误导。
- 打包替换 exe 前必须先杀旧实例（单例占 8100 端口），否则新 exe 启动即退出。
- 开发启动：`python launch.py`（自动拉起后端）；后端认证 token 由 launch 注入环境变量。
