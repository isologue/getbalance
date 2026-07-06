# GetBalance

统一查询多个中转站余额的本地网站，适合个人在本机或 Docker 中部署使用。

## 功能

- 管理多个中转站配置
- 保存 `base_url`、Cookie、Authorization、额外请求头
- 支持粘贴 `curl` 命令自动填充站点配置
- 支持保存前测试请求、预览返回 JSON、点击字段自动填入路径
- 支持登录态失效后用账号密码调用 API 自动续期
- 按 adapter 查询余额
- 展示最近余额、状态、错误信息
- 保存历史查询记录
- 支持通用 `generic_json` adapter
- 内置 `mock` adapter，方便本地验证页面

## 技术栈

- FastAPI
- SQLite
- Jinja2 模板
- httpx
- Docker / Docker Compose

## 快速启动

### 方式一：本机 Python

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问：

```text
http://localhost:8000
```

### 方式二：Docker

```powershell
docker compose up -d --build
```

访问：

```text
http://localhost:8000
```

## 站点配置说明

新增站点时可配置：

- `name`：站点名
- `base_url`：站点根地址
- `balance_endpoint`：余额接口路径，也可直接填完整 URL
- `method`：默认 `GET`
- `adapter`：默认 `generic_json`
- `cookie`：登录 Cookie
- `authorization`：如 `Bearer xxx`
- `extra_headers`：JSON 对象格式，例如 `{"X-Token":"abc"}`
- `request_body`：请求体，支持 JSON / form-urlencoded / 原始文本
- `balance_path`：余额字段路径，例如：
  - `balance`
  - `data.balance`
  - `user.quota`
- `currency_path`：币种字段路径，可留空
- `default_currency`：默认币种
- `scale`：单位换算，例如接口返回“分”可填 `0.01`

## Adapter

### 1. generic_json

用于大多数返回 JSON 的中转站余额接口。

假设接口返回：

```json
{
  "data": {
    "balance": 12.34,
    "currency": "USD"
  }
}
```

则可配置：

- `balance_path = data.balance`
- `currency_path = data.currency`

### 2. mock

本地测试专用，固定返回：

- `balance = 123.45`
- `currency = default_currency`

建议首次先新增一个 `mock` 站点检查页面流程是否正常。

## curl 自动填充

新增 / 编辑站点页面支持直接粘贴浏览器复制出来的 `curl` 命令。

当前会自动识别并填充：

- `name`
- `base_url`
- `balance_endpoint`
- `method`
- `authorization`
- `cookie`
- `extra_headers`
- `request_body`

说明：

- `authorization` 和 `cookie` 会优先进入独立字段
- 其余请求头会进入 `extra_headers`
- `balance_path` 无法从 curl 自动推断，仍需要你手动填写

## JSON 预览与字段路径选择

粘贴并解析 curl 后，可以点击“测试请求并预览 JSON”。

请求成功后页面会展示：

- 接口返回 JSON
- 可点击的字段路径列表

可以直接点击：

- “设为余额字段”：填入 `balance_path`
- “设为币种字段”：填入 `currency_path`

## 登录态失效自动重登

编辑站点时可以开启“自动登录配置”。刷新余额时如果命中登录失效规则，例如 HTTP `401/403` 或响应包含 `unauthorized`、`token expired`、`未登录` 等关键词，系统会：

1. 调用配置的 `login_url`
2. 用 `login_body_template` 渲染账号密码
3. 从返回 JSON 提取 token，或从 `Set-Cookie` 保存 Cookie
4. 更新站点登录态
5. 自动重试一次余额查询

登录请求体模板示例：

```json
{"email":"{{username}}","password":"{{password}}"}
```

常用配置：

- 可以直接粘贴登录接口的 `curl`，系统会自动填：
  - 登录 URL
  - 登录请求头
  - 账号 / 密码
  - 登录请求体模板
- 粘贴登录 curl 后，可以点击“测试登录并预览 JSON”，再点击返回字段自动填 `login_token_path`
- `login_token_path`：例如 `data.token`
- `login_token_prefix`：默认 `Bearer`
- `login_cookie_from_response`：如果登录接口通过 Cookie 建立会话，则勾选
- `auth_fail_status_codes`：默认 `401,403`
- `auth_fail_keywords`：默认包含 `unauthorized,token expired,login required,未登录,登录过期`

如果登录响应包含 `captcha`、`cloudflare`、`recaptcha`、`hcaptcha`、`人机验证`、`安全验证` 等内容，系统会标记为 `need_manual_login`，不会尝试绕过人机检测。你需要人工完成登录后重新粘贴 curl 或更新 Cookie/Token。

## API

- `GET /api/sites`：站点列表
- `POST /api/sites`：新增站点
- `GET /api/sites/{id}`：查看站点
- `PUT /api/sites/{id}`：更新站点
- `DELETE /api/sites/{id}`：删除站点
- `POST /api/sites/{id}/refresh`：刷新单个站点余额
- `POST /api/refresh-all`：刷新全部站点
- `GET /api/history/{site_id}`：查询历史

## 数据存储

- 默认 SQLite 文件：

```text
./data/getbalance.sqlite3
```

- Docker 中挂载到：

```text
./data:/data
```

## 安全说明

当前版本按你的要求，**Cookie / Token 明文保存在本地 SQLite**。

因此仅建议：

- 本机个人使用
- 本地 Docker 使用
- 不直接暴露到公网

如果后续你要上 VPS，我可以再帮你加：

- 登录鉴权
- 凭据加密存储
- HTTPS 反代
- 定时任务自动巡检
- 站点插件化适配
