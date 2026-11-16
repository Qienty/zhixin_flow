# Tether · 情绪记录与自我调节

移动端优先的情绪记录工具。Python 标准库后端 + 原生 JavaScript 前端，无第三方运行依赖。定位为记录与自我调节，不提供诊断或治疗。

## 启动

需要 Python 3.10+：

```sh
python backend/server.py 8000
```

访问 http://127.0.0.1:8000/。Windows 可运行 `start.bat`，优先使用 PATH 中的 Python；项目不附带 Python 运行时。命令行未指定端口时可用 `PORT` 配置，`HOST` 默认 `127.0.0.1`。

首次启动自动建库并迁移表结构。每个账号的数据相互隔离；升级前应备份 SQLite 数据库。默认位置为 `backend/data/tether.db`，可通过进程环境变量 `TETHER_DATA_DIR` 指定其他数据目录。不要删除数据库来更新应用。

## 记录与 14 天边界

- 点情绪只创建内存草稿。跳过、关闭、返回均不写入；完成引导后保存，也可选择「只记录，暂不引导」并主动保存强度或原因。网络失败时保留草稿，重试使用同一请求标识防止重复记录。草稿不跨刷新保存。
- 首页只在最近 14 个本地自然日中选取强度 ≥4 的记录，按强度、时间、ID 降序取 3 条。没有符合条件的记录时显示最近记录。
- 总结的统计、走势图、归因和 AI 都使用同一 14 天窗口。以查看设备的本地日期确定今天，并与记录发生时的 `local_date` 比较；例如 11 月 16 日计入 11 月 3 日至 16 日。
- 超过 14 天的数据不会删除。记录页支持全历史筛选、严重程度/时间排序、旧记录筛选、编辑、删除和逐条补全。服务端游标分页，每页 40 条，浏览器仅渲染当前页。
- JSON/CSV 导出包含全部记录。JSON 导入只追加，完全相同的条目跳过，错误文件整批拒绝。每次最多 5000 条、8 MiB；更大的备份需拆分。
- 演示数据先确认再追加 28 条，永不覆盖；演示记录参与统计，建议在测试账号使用。已有未补全记录不会自动清除。
- 顶部可手动刷新；切换页面、从后台返回和网络恢复时更新数据，页面停留期间每分钟检查一次。自动更新会避开正在编辑、保存或分析的操作，保留记录页的当前分页位置。
- 普通请求等待上限为 30 秒，AI 分析为 5 分钟。超时会恢复操作入口并保留当前保存草稿，不自动重发写入请求。AI 可点击「停止等待」；这不会撤销服务器已经收到的操作，也不保证停止模型计费。

## AI 与 Pro

Pro 通过兑换码开通，没有订阅或支付。复制 `.env.example` 为 `.env` 并配置兼容服务：

```ini
TETHER_AI_API_KEY=your-key
TETHER_AI_BASE_URL=https://api.deepseek.com/v1
TETHER_AI_MODEL=deepseek-flash
```

打开总结页仅计算统计与读取有效缓存。用户点击「开始分析 / 重新分析」才会调用模型；没配 key、样本不足或请求失败时保留规则统计。模型会收到聚合结果及最多 60 条记录摘要（原因最多 40 字、备注最多 60 字）。

缓存依据记录内容版本、账号、语言、模型以及当前本地日期生成指纹；旧的按天数缓存自动失效。趋势数字与文案来自同一规则计算，模型不能覆盖趋势文本。归因证据默认折叠，场景提醒并入对应卡片。

AI 缓存按账号保存。新增、编辑、删除记录会在同一数据库事务中清理该账号的旧摘要，清空记录也会清理该账号缓存。记录版本检查会拒绝保存分析期间已被修改或删除的数据所生成的摘要。缓存有效期为 24 小时，读写缓存时及服务启动后每小时删除过期缓存行；服务停止期间的清理在下次启动后执行。

升级会自动清除无法识别账号归属的旧版 AI 缓存，保留原始记录和账号。这是数据库缓存清理，不会处理此前导出的文件、服务器备份或外部模型服务保存的数据。

生成兑换码：

```sh
cd backend
python -m app.codes 10
python -m app.codes --list
```

兑换额度检查、使用次数扣减和账号升级在同一个写事务内完成，限制并发请求重复消耗一次性兑换码。

## 隐私与登录保护

数据保存在部署服务器上的明文 SQLite 数据库，按账号隔离，非端到端加密。拥有服务器或数据库权限的管理员可能访问记录；导出文件需要自行保管。HTTPS 部署时传输加密。网站不会因为存在账号体系就自动获得数据库加密。

密码使用 scrypt 哈希，新密码要求 8—128 个字符，拒绝常见弱口令、重复单字符和用户名。旧的短密码仍可登录，以免升级锁出用户；修改密码后其他会话失效。修改界面带确认框和长度提示。

登录和旧密码验证按账号及客户端 IP 限速：5 分钟内账号最多 5 次失败，IP 最多 20 次；达到上限后返回 429 和 Retry-After。限速状态保存在单进程内存中，重启会清零。多进程部署应在网关增加共享限速。没有自助找回密码；界面提供联系管理员说明。

退出登录只有收到服务器成功响应后才刷新页面。失败或超时会提示会话可能仍有效，并允许重新点击退出；不会把网络失败显示成退出成功。

## HTTPS 与反向代理

项目当前不附带 Nginx 部署模板。部署时需自行配置域名、证书和反向代理，并设置 Python 服务的进程环境变量 `TETHER_TRUSTED_PROXIES=127.0.0.1`（按照实际代理地址调整）。代理必须覆盖 `X-Real-IP`、`X-Forwarded-Proto`；不信任普通客户端自报的头。仅可信 HTTPS 代理下发 Secure Cookie 和 HSTS。AI 请求可能持续数分钟，代理读取超时应配合前端的 5 分钟等待上限设置。

Python 提供 CSP、X-Content-Type-Options、X-Frame-Options、Referrer-Policy 和 Permissions-Policy，文本响应使用 UTF-8。页面脚本全部外置，匹配 CSP。未知 API 返回 JSON 404，未知静态路径返回 HTML 404；API 响应禁止缓存。

挂到 `/qienty/` 时访问地址必须带尾部 `/`，Nginx 使用带尾部 `/` 的 proxy_pass 去掉前缀。前端及 service worker 均按当前部署前缀工作。

应用代码不会自动启用 HTTPS，需由部署环境配置证书和代理。

## PWA

清单包含 192/512 PNG 图标和 180 像素 Apple 图标，沿用现有 SVG 标识。项目当前不附带图标生成脚本；更新标识时需同步替换 `frontend/icon.svg`、`frontend/icon-192.png`、`frontend/icon-512.png` 和 `frontend/apple-touch-icon-180.png`。

Python 提供 `sw.js` 时自动将前端资产内容 hash 注入缓存版本。静态壳使用网络优先、离线兜底；API 不进入 service worker 缓存。若额外使用独立静态服务器，需等价实现 hash 注入，不能直接绕过 Python 托管。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bootstrap` | 200 返回登录状态；已登录时包含元数据、账号、首页记录 |
| GET | `/api/home` | 14 天关注记录或最近记录 |
| GET/POST | `/api/entries` | 游标分页 / 保存记录 |
| GET/PATCH/DELETE | `/api/entries/{id}` | 读取、补全、删除单条 |
| GET | `/api/stats/basic`、`/api/insights` | 固定 14 天的统计与缓存；days 若传只能为 14 |
| POST | `/api/insights/analyze` | 手动 AI 分析，body 可传 force |
| POST | `/api/import` | JSON entries 数组，原子追加并去重 |
| GET | `/api/export?format=json或csv` | 全量导出，CSV 文本带公式注入防护 |
| POST | `/api/demo/seed` | 仅追加，拒绝 reset=true |
| POST | `/api/auth/register或login或logout或password` | 账号与会话 |
| GET | `/api/auth/me` | 未登录返回 logged_in=false，不制造首屏 401 |
| GET | `/api/meta`、`/api/crisis` | 公开元数据与危机资源 |

`POST /api/entries` 必须传 `tz_offset`（东正西负，整数分钟，-840 至 840）；occurred_at 若传必须带时区。cause 最多 500 字，note 最多 1000 字，severity 为 1—5 整数。可传 client_id 让同一账号的保存重试幂等。

列表支持 emotion、sort=severity或time、unfilled、older、q、days、limit（1—100）、cursor。前端统一传 tz_offset 以确定本地日期。查询校验失败返回 400；记录接口始终按账号隔离。

## 验证

```sh
python -m unittest discover -s tests -v
```

`tests/test_regressions.py` 使用临时数据库，覆盖兑换并发与事务回滚、缓存账号隔离和清理、分析期间删除记录、旧缓存迁移、保存幂等、跨账号记录隔离、JSON 导入导出和登录会话。测试不读取真实业务数据库，不调用模型服务。

前端纯逻辑测试需要 Node.js：

```sh
node tests/run_frontend.mjs
```

`tests/run_frontend.mjs` 使用 Node.js 内置模块和隔离的 API/DOM stub，覆盖请求超时与取消、前后台刷新、旧响应隔离、退出重试、草稿重试与双语键一致性。不需要安装 npm 依赖；这些检查不等同于真实浏览器视觉及交互验收。

`__` 开头的开发页面默认返回 404，只有显式设置 `TETHER_DEV_PAGES=1` 才开放。`frontend/__harness.html` 是历史人工调试页面，不作为当前自动化测试入口。
