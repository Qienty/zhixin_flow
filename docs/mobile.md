# 手机端：当前形态与走向

**产品最终形态是手机应用。浏览器只是开发与演示环境**，不是交付形态。所有界面按 390px 宽的单列手机视口设计（`docs/mobile-design-review-2026-11-22.md`），桌面宽度下也保持同一条手机版心。

这个文件记录三件事：现在做到哪一步、怎么验、以及走向原生安装包还差什么。

## 一、当前形态：可安装的 PWA

现在已经是「能装到主屏、断网能开」的形态，不只是网页：

| 能力 | 实现位置 |
|---|---|
| 安装到主屏（standalone、无地址栏） | `frontend/manifest.json` |
| Android 主屏图标不被裁坏 | `icon-maskable-192.png` / `icon-maskable-512.png`（设计缩到 76% 居中） |
| 刘海 / 灵动岛 / 手势条避让 | `--safe-top` / `--safe-bottom`（`env(safe-area-inset-*)`） |
| 软键盘弹出时布局收缩 | `interactive-widget=resizes-content` |
| 离线打开外壳 | `frontend/sw.js` 网络优先 + 缓存兜底，API 不入缓存 |
| 触控目标 ≥ 40px（主操作 44px） | 品牌入口、语言切换、底栏、图标按钮 |
| 输入框 ≥ 16px（iOS 不会自动放大页面） | 统一 `input/textarea/select` 字号 |
| 去掉点击灰块、300ms 延迟 | `-webkit-tap-highlight-color`、`touch-action: manipulation` |
| 状态栏颜色跟随主题 | `theme-color` 与 manifest 主题色一致 |
| 应用内「添加到主屏」入口 | 「我的」页安装卡：Android 调系统安装框，iOS 给 Safari 操作说明 |
| 语音输入按能力渲染 | 有 `SpeechRecognition`（或壳注入原生桥）才显示麦克风；不支持时整个控件不渲染，「我的」里的语音语言选项也一起隐藏 |

## 二、自动验收（可重复）

```powershell
# 一次性准备（测试工具，不进生产依赖；.cache 已被 gitignore）
npm install --prefix .cache/mobile-tools playwright-core@1.62.1

$env:PLAYWRIGHT_MODULE = (Resolve-Path '.cache/mobile-tools/node_modules/playwright-core').Path
python -B tests/run_mobile_audit.py
```

`run_mobile_audit.py` 会起一个**隔离服务**（临时数据库、AI 禁用、注册限流调高），然后用 `tests/mobile_audit.mjs` 在真实浏览器引擎里跑 5 个设备视口：

| 设备 | 视口 |
|---|---|
| iphone-13 | 390×844 @3x |
| iphone-se | 375×667 @2x |
| pixel-7 | 412×915 @2.6x |
| android-small | 360×640 @3x |
| iphone-13-landscape | 844×390 |

每个设备走完整流程（登录页 → 注册 → 首页 → 自由表达浮层 → 练习 → 总结 → 我的 → 深色 → 英文 → 断网重开），并量化检查：

- **横向溢出**：`scrollWidth > innerWidth` 即失败
- **触控目标**：可点元素小于 40px（内联链接 24px）即失败
- **输入框字号**：小于 16px 即失败（iOS 会放大整页）
- **文字裁切**：不换行 + 溢出隐藏导致的真实裁切
- **底部导航**：是否被推出视口
- **浮层**：是否高于视口、顶部是否被推出、能否滚动
- **装机条件**：manifest 可解析、`display=standalone`、192/512 的 `any` 与 `maskable` 图标都存在且可下载、`viewport-fit=cover`
- **离线壳**：断网后页面仍能渲染
- **在线阶段控制台报错**

通过标准：`0 个错误，0 个提醒`。截图写入 `.cache/mobile-shots`（每个设备 9 张）。

当前基线（2026-11-23）：5 个设备全部通过，0 错误 0 提醒。

## 三、真机验收（必须人工做一次）

自动化测不出软键盘、手势和真实 DPR，发布前用真机走一遍：

| 项目 | 怎么做 | 期望 |
|---|---|---|
| iOS 安装 | Safari 打开 → 分享 → 添加到主屏 → 从主屏启动 | 无地址栏，顶部不被状态栏压住 |
| Android 安装 | Chrome → 菜单「安装应用」或「添加到主屏」 | 图标不被圆形/方形裁到变形 |
| 软键盘 | 在表达框里长按输入、切换到中文输入法 | 输入框可见，底栏不盖住输入区，页面不横向滚动 |
| 返回手势 | 打开浮层后左右滑退 | 先关浮层，不退出页面 |
| 离线 | 装好后开飞行模式冷启动 | 外壳可打开，不白屏 |
| 深色 | 系统切深色后打开 | 状态栏与页面同色，无白闪 |
| 语言 | 系统语言英文时首次打开 | 界面为中文（不再跟随浏览器语言）；在「我的 → 设置」切英文后，多设备以账号为准 |
| 字号 | 系统「更大字体」 | 页面不溢出、按钮文字不裁切 |

## 四、走向原生安装包（国内应用商店）

PWA 能装到主屏，但要在国内应用商店上架（以及 iOS 中国区）还需要一层壳。推荐 Capacitor：复用现有 `frontend/` 静态资源，不重写界面。国内发行还要准备软件著作权登记与 APP 备案，这两项是前置条件，不是最后一步。

改造要点（按先后顺序）：

1. **同源问题**：WebView 里页面来源是 `capacitor://localhost` 或 `https://localhost`，`/api` 变成跨域。要么把后端地址做成可配置的 base URL + 服务端 CORS 白名单，要么用 Capacitor 的本地服务插件把 API 代理进同源。
2. **Cookie**：现在会话是 `HttpOnly; SameSite=Lax` 的 Cookie。**已决定改用 Bearer token（B 方案）**：服务端对带 `X-Tether-Token-Auth: bearer` 的登录请求在响应体里下发 token，壳内把它存进本地存储并自动带 `Authorization: Bearer`；网页端不变，仍只用 HttpOnly Cookie，token 不下发给 JS。两条凭据共用同一张 `sessions` 表，退出、改密码、过期清理对两边同时生效。壳内接原生安全存储（Keychain / Keystore）是后续优化，现阶段用本地存储即可，接口留好了替换点。
3. **安全区**：Capacitor 会注入 `env(safe-area-inset-*)`，现有 CSS 已适配，通常不需要改。
4. **返回键 / 手势**：Android 物理返回要接到现有的 `history` 浮层逻辑上（`popstate` 已经处理，需在壳里映射）。
5. **推送**：要做提醒才接推送；当前产品刻意不做每日推送（避免打卡压力），只有「每周证据卡」这类低频通知值得推。
6. **审核与合规**：商店审核会看医疗声明。现有文案已严格避开诊断/治疗表述，提交时不要把「情绪调节」写成「治疗」；国内上架还需软件著作权与备案信息。
7. **离线**：原生壳内没有 Service Worker 也能加载本地资源，但 `sw.js` 的缓存逻辑仍可用于更新资源；保留即可。

以上 1—2 是真正的工作量，3—7 基本是接线。

### 返回键的壳内约定

`frontend/js/app.js` 已经把「浮层 / 引导 / 危机资源 / 确认层」的关闭挂在 `popstate` 上：打开浮层时 push 一条历史，返回时先关浮层而不是退出页面；关浮层时自己发的那次 `history.back()` 会被 `pendingPop` 认领，不会误触发页面回退。

所以壳里**不需要改前端**，只要把系统返回键接到浏览器历史：

```js
// Android：物理返回键 → 交给 WebView 的历史栈
backButton.addEventListener("click", () => webview.goBack());
```

这套行为已有测试锁住：`tests/run_frontend.mjs` 的「System back gesture closes the overlay and ignores our own history pop」——它同时验证了两条：返回手势先关浮层、以及自己发出的 pop 不触发页面回退。

**为什么不提前重构这块**：`popstate` 与 `pendingPop` 是修过 bug 的敏感区（历史记录错位会把刚打开的页面弹回去），在还没有壳、无法真机验证的情况下改动，收益为零、风险不为零。等壳到位、能真机验证时再动，测试已经就位。

### 语音输入的壳内约定

WebView 没有可用的 `SpeechRecognition`（iOS 完全没有，Android 也常静默失败）。所以前端按能力渲染：
浏览器有原生接口才显示麦克风；壳内必须注入原生桥，否则视为不可用、整个控件不渲染。

壳需要注入的接口（`window.__TETHER_SPEECH__`）：

```js
window.__TETHER_SPEECH__ = {
  start(onResult, onError, lang) {}, // onResult(text) 识别成功；onError() 失败/无权限
  stop() {},                          // 再次点击麦克风时调用
};
window.__TETHER_NATIVE__ = true;      // 同时让前端切到 Bearer token 与原生环境判断
```

插件可选用 `@capacitor-community/speech-recognition`，按上面的形状包一层即可，前端不用改。

具体排期、任务拆解、验收标准、风险与待拍板事项见 [下一步计划书](mobile-app-plan.md)。

## 五、已知限制

- 自动化用 Edge 引擎模拟 iOS 视口，**不是真的 Safari**：WebKit 特有的行为（如 `100vh`、惯性滚动、日期输入样式）需要在真机复核。
- 软键盘、触控滑动、震动反馈、来电打断等无法在无头环境验证。
- `orientation: portrait` 锁定竖屏；如果后续要做平板适配，需要在 manifest 与 CSS 两处一起放开。
- 危机资源号码在 App 内同样会过时，复核节奏见 `docs/crisis-resources.md`，与平台无关。
