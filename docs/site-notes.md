# ACLClouds 站点适配笔记

`app.py` 是 ACLClouds 免费服务的自动续期脚本。这份文档记录站点当前的页面结构、
脚本依赖的选择器与判据，以及站点改版时的排查方法。

站点大致每隔几个月就会调整 DOM，此前已经适配过三轮（域名迁移、项目页表格化、
人机验证换成 Cap）。**页面结构一变，这里的结论全部作废，请按第六节重新核对。**

最后核对日期：2026-11-06

---

## 一、脚本做什么

```
打开 /dashboard/projects
  → 未登录则用账号密码登录（含人机验证）
  → 收集服务清单（service id / 名称 / 过期日期 / 详情页链接）
  → 逐个进 /server/<id> 详情页
      → 状态 offline：点 Start 拉起服务
      → 有 Renew 按钮：点击 → 完成人机验证 → 等待结果
      → 没有 Renew 按钮：判定为未到续期时间
  → 每个服务发一条 Telegram 通知
```

免费套餐是分片的续期周期：**bot 每 4 天一次，游戏每 6 小时一次**（`plan.renewal_days`
里也有）。可续期窗口很窄，所以大多数运行结果都是「未到续期时间」，这是正常的。

---

## 二、站点结构

### 登录页 `/auth/login`

| 元素 | 定位方式 |
|---|---|
| 邮箱 | `#username` |
| 密码 | `#password` |
| 提交 | `button[type="submit"]` |
| 人机验证 | `<cap-widget>` 自定义元素 |

登录成功后跳到 `/dashboard/projects`。**判据用 URL**，不要用标题——法语环境下
标题不是 "Home \| ACLClouds"。

未登录时访问 `/dashboard/projects` 会被重定向到 `/auth/login`；不要先开
`BASE_URL` 判断，未登录时会落在 `/fr/` 之类的落地页，既不是登录页也不是 dashboard。

### 人机验证：Cap（自托管 proof-of-work）

站点用的是自托管的 Cap，验证端点 `https://cap.aclclouds.com/<siteKey>/`，
页面上渲染成 `<cap-widget>`：

```html
<cap-widget data-cap-api-endpoint="https://cap.aclclouds.com/235a82a3e3/"
            data-cap-disable-haptics="true">
  <input type="hidden" name="cap-token">
</cap-widget>
```

内部结构在 **shadow DOM** 里，Selenium 的 CSS / XPath 查询穿不透，
只能走 `execute_script`（`element.shadow_root` 也行，但多步交互时更啰嗦）：

```
cap-widget
└── #shadow-root
    └── .captcha[data-state]        data-state: '' 待点击 / verifying 计算中 / done 已完成
        └── .captcha-trigger        role="button"，点击这个
```

**验证完成的判据**（三种取其一，见 `click_captcha_checkbox`）：

1. 隐藏域 `input[name="cap-token"]` 有值
2. `.captcha` 的 `data-state === 'done'`
3. `cap-widget` 从 DOM 中消失

第 3 条是必须的：**续期弹窗在验证通过后会立刻卸载**，组件消失本身就是成功信号。
只判断前两条会在弹窗场景下多循环一轮，然后把「找不到组件」误报成失败。

PoW 计算 + 请求 cap 服务大约 5 秒。

### 服务列表页 `/dashboard/projects`

**2026-11 卡片版**，每行是 `<ul>` 下的 `<li data-service-id>`：

```html
<ul>
  <li data-service-id="80eba126">
    <div>
      <h3>appa</h3>                     <!-- 服务名（自定义名） -->
      <p>Free · Discord bot</p>         <!-- 套餐 · 类型 -->
    </div>
    <p>
      <span>Active</span>                                  <!-- STATUS -->
      <span><span>Expires on</span> <span>11/28/2026</span></span>  <!-- EXPIRY -->
      <span>Free</span>
    </p>
    <div>
      <a href="/server/468d90dd-6b54-…">Manage</a>          <!-- 详情页 -->
      <button aria-expanded="false"
              aria-controls="service-details-server-80eba126">…</button>
    </div>
    <div id="service-details-server-80eba126" hidden></div> <!-- 展开面板 -->
  </li>
</ul>
```

- 行内 class 是 `ProjectsPage-module_xxx`（带构建哈希），**不稳定，不要依赖**。
  唯一可靠的锚点是 `data-service-id`。
- **⚠️ `data-service-id` 是短 id（`80eba126`），详情页链接里是完整 UUID
  （`468d90dd-…`），两者不同，不能互相拼接。** 拿不到链接时宁可跳过，也别拼
  `/server/<短 id>`（会 404）。
- 详情页链接直接是行内的 "Manage"，不必展开面板；过期日期也在折叠行里。
- 2026-09 的表格版用的是 `<article data-service-id>`。脚本两种都兼容：
  `main article[data-service-id], main li[data-service-id]`。

**⚠️ 列表页的 Renew 按钮是个陷阱**，详见第三节。

### 服务详情页 `/server/<id>` —— 续期入口与电源控制

头部状态胶囊与电源按钮（2026-11 版）：

```html
<div class="srv-head-titleline">
  <span role="status" data-server-status="offline">Offline</span>
</div>
<div>
  <button class="srv-power-btn">Start</button>
  <button class="srv-power-btn" disabled>Restart</button>
  <button class="srv-power-btn" disabled>Stop</button>
</div>
```

- **状态判据用 `data-server-status`**（`offline` / `starting` / `running` /
  `stopping`），与界面语言无关，比读 "Offline" 文案可靠。
- 服务离线时只有 Start 可点，Restart / Stop 都是 `disabled`。
- Start 文案随语言变化（Start / Démarrer / Starten / Iniciar / Avvia /
  Запустить / 启动），按**整词**匹配——"Restart" 里含 "start"，子串匹配会点错。
- 点击后状态先变 `starting`，几秒内变 `running`（脚本轮询 20 秒）。

续期提示条：

```html
<div style="display:flex; …">                    <!-- 提示条，全内联样式、无 class -->
  <div>
    <div>Time remaining: 10h 35min</div>
    <div>Free plan - renew every 4 days</div>
  </div>
  <button style="…">Renew</button>               <!-- 续期按钮 -->
</div>
```

提示条三行的含义（第二、三行的文案随语言和套餐变化）：

| 位置 | 内容 | 条件 |
|---|---|---|
| 第一行 | `Time remaining: 10h 35min` | 有到期时间时 |
| 第二行 | 套餐规则，如 `Free plan - renew every 4 days` | 有到期时间时 |
| 第三行 | 可续期时间说明，如 `Renewal will be available 2 days before expiration` | **仅在当前不可续期时出现** |
| — | `Renew` 按钮 | **仅当 `server.canRenew` 为 true** |

- 页面**异步加载**，首次约 5 秒才渲染出按钮；用 `wait_for_detail_page` 轮询
  `main.innerText` 长度作为就绪信号。
- 按钮和内层文案全是内联样式、没有 class，**只能按文本定位**。
- 第三行是"还没到续期时间"的明确文案，但随语言/套餐变化（免费 bot 用
  `renewAvailableFree`，付费用 `renewAvailable`，游戏类用 `renewAvailableMcFree`），
  脚本不解析它，仍以「Renew 按钮不存在」作为判据。

### 续期接口

```
POST /api/client/servers/{id}/upgrade/renew
```

响应分支（前端处理见 640.js 的续期组件）：

| 情况 | 处理 |
|---|---|
| 成功 | 提示成功 toast，**1.5 秒后 `window.location.reload()`** |
| 403 `{"code": "captcha_required"}` | 弹出人机验证弹窗，验证后带 `{captcha_token: …}` 重发 |
| 弹窗被用户取消 | `captchaCancelled`，静默返回（不重发） |
| `{"error": "renewal_not_available", "hours_remaining"/"days_remaining": n}` | 还没到续期时间 |
| `{"requires_payment": true, "checkout_url": …}` | 跳转支付 |
| 429 / 419 | 请求过多 / 会话过期 |

人机验证弹窗的结构（用 `aria-labelledby` 定位，**与界面语言无关**）：

```html
<div role="dialog" aria-modal="true" aria-labelledby="renew-captcha-title">
  <div id="renew-captcha-title">Anti-bot confirmation</div>   <!-- 法语: Confirmation anti-robot -->
  <div class="auth-cap-widget"><cap-widget …></cap-widget></div>
</div>
```

**这个弹窗是 `createPortal` 挂到 `document.body` 上的，不在 `<main>` 里。**
所以查 cap-widget 时不能把范围收窄到 `main`。

服务状态可以直接读 `GET /api/client`，里面有 `can_renew`、`auto_renew`、`is_free`、
`expires_at`、`plan.renewal_days` 等字段，排查时比看页面可靠。

---

## 三、踩坑记录

按踩到的顺序，每条都是实际浪费过时间的。

### 1. 列表页的 Renew 按钮存在，但被站点自己的 CSS 藏了（2026-09 表格版）

> 2026-11 卡片版的折叠行里只有 "Manage" 和 "更多" 菜单，没有可见的 Renew
> 按钮；续期入口仍然只在详情页。下面这条只在回到 `<article>` 表格版时才需要
> 重新确认。

```css
article[data-service-id] a[href^="/server/"] + button,
article[data-service-id] a[href^="/dashboard/vps/"] + button {
  display: none !important;
}
```

紧跟在 "View service details" 链接后面的按钮被强制隐藏。后果有两个，**第二个更坑**：

- 按钮点不到（`safe_click_element` 会失败或点到别处）
- **Selenium 的 `element.text` 对不可见元素返回空字符串**，于是
  `find_renew_buttons` 恒为空 → 每次都误报「未到续期时间」

而这时候后端 `can_renew` 其实是 `true`。**症状和「未到续期时间」完全一样，
但原因完全不同**——见到「找不到按钮」时，先确认按钮是真的不存在，
还是存在但不可见。

### 2. 用 `textContent` 而不是 Selenium 的 `.text`

`.text` 受可见性影响（见上一条），`get_attribute('textContent')` 不受影响。
`find_renew_buttons` 两者都用：先按 `is_displayed()` 过滤，再用 `textContent` 取文本。

### 3. 不要用 Selenium 的 `.text` 切行

`element.text` 按**渲染后的换行**切分，站点一改布局就会把相邻字段粘成一行。
实际踩到过：service id 和日期粘成 `"…dc2a4410/04/2026"`，正则从里面抠出了
`4410/04/20` 这种鬼日期。

改用 `row_text_lines`：取 DOM 叶子元素的 `textContent`，与 CSS 无关，
天然一段一个字段。

### 4. cap-widget 在 shadow DOM 里

Selenium 的 CSS/XPath 不穿透 shadow root。`element.shadow_root` 能用，
但涉及多步交互时直接用 `execute_script` 更省事。

### 5. 查找范围要收窄到 `<main>`，但验证弹窗除外

列表页导航里有 "My renewals"，也带 `renew` 字样，全文档搜索会误命中。
`find_main()` 把续期按钮的查找收窄到 `<main>`；但人机验证弹窗挂在 `body` 上，
查 cap-widget 时**不能**跟着收窄。

### 6. 界面文案不固定，不要匹配写死的英文

站点支持 7 种界面语言（en / fr / de / es / it / pt / ru），文案由 i18n 表驱动；
而且**同一次会话里都可能混着语言**——实测界面是英文时，`aria-label` 仍有
"Fil d'Ariane"、"Solde du compte" 这类法语。所以：

- 续期按钮文本按多语言匹配，`RENEW_LABEL_PATTERN` 实际是
  `renew|renouvel|erneuern|renovar|rinnov|продлить|reactivate|续期|延长|重新激活`
  （原正则只写 `renew`，法语界面下直接失效）
- 续期结果**不匹配提示语**，改用与语言无关的判据：详情页的 Renew 按钮消失
  且 URL 仍在 `/server/` 下
- 弹窗用 `aria-labelledby="renew-captcha-title"` 定位，不用标题文本

### 7. 「未到续期时间」在脚本层没有可用的结构化信号

站点确实会在提示条上多出一行说明（见第二节），但那行文案随语言和套餐变化，
拿它做判据是把语言问题引进来。所以脚本仍以「Renew 按钮不存在」判定 ——
**这个判据和"按钮存在但找不到"在日志上长得一模一样**，见第 1 条。

---

## 四、定位速查表

| 目标 | 定位方式 |
|---|---|
| 服务行 | `main article[data-service-id], main li[data-service-id]` |
| 服务名 | 行内第一个叶子节点文本（2026-11 版即 `<h3>`） |
| 过期日期 | 行内第一个日期（`Expires on` 后面那个） |
| 详情页链接 | 行内 `a[href^="/server/"]` 或 `/dashboard/vps/`（2026-11 版是 "Manage"） |
| 服务状态 | `main [data-server-status]`，值 offline / starting / running / stopping |
| Start 按钮 | `main button.srv-power-btn`，文本整词匹配多语言 Start |
| 续期按钮 | `<main>` 内文本匹配多语言 Renew 的可见 `button`/`a` |
| 人机验证组件 | `document.querySelectorAll('cap-widget')` 中可见的那个 |
| 人机验证弹窗 | `div[role="dialog"][aria-labelledby="renew-captcha-title"]` |
| 验证是否完成 | `cap-token` 有值 / `data-state=done` / 组件消失 |

---

## 五、验证方式

改完不要只靠"跑一遍看着像成功了"。分两层：

### 离线回归（不联网、不登录）

`.tmp/test_project_page.py` —— 把抓下来的真实 HTML 快照注回本地 `file://` 页面，
验证解析逻辑和续期全链路。覆盖：

- 列表页解析（行/名称/过期日期/详情页链接）
- **隐藏按钮防线**：夹具里加上站点那条真实 CSS，隐藏的 Renew 必须不被选中
- **范围防线**：`main` 外的 "My renewals" 必须不被命中
- 详情页全链路：等渲染 → 定位 → 点击 → 人机验证 → 结果确认
- 离线自动启动：`data-server-status` 判据、7 种语言的 Start 文案、
  "Restart 不能被当成 Start"
- 7 种语言的续期按钮文案

夹具由浏览器抓取生成，见 `fixture_*.html`。

### 真实运行

```bash
set -a && . ./.env && set +a
xvfb-run --auto-servernum '--server-args=-screen 0 1366x768x24' python3 app.py
```

会真实续期并发 Telegram 通知。注意到期窗口很窄，**大多数时候只能验证到
「未到续期时间」这条路径**；能不能点到续期按钮，要等窗口开启时（免费 bot
套餐是到期前 2 天）才测得到。

> `.tmp/` 已被 gitignore，上面这些脚本和夹具不会入库。

---

## 六、站点改版时的排查方法

页面一变，症状通常只是「找不到元素」。别猜，按下面走。

### 1. 先实测，别改代码

写一次性探针到 `.tmp/`，登录后直接 dump 页面：

- 关键元素是否存在、`is_displayed()`、`getBoundingClientRect()`、`getComputedStyle`
- 同时打印 JS `innerText` 和 Selenium `.text` —— 两者不一致往往就是问题所在
- 存截图，肉眼确认

`.tmp/` 下已经有一批可复用的探针（`probe_*.py`、`repro_expand.py`）。

### 2. 读后端字段，判断是"没到时间"还是"没找到"

在页面上执行 `fetch('/api/client', {credentials:'include'})`，看 `can_renew`、
`expires_at`、`auto_renew`。这能立刻区分「站点不给续期」和「脚本没找到按钮」。

### 3. 静态分析前端 bundle

前端是 React + webpack，页面数据和渲染条件都在 bundle 里，i18n 原文也在。

```bash
python3 .tmp/crawl_chunks.py     # 递归抓取所有 chunk 到 /tmp/acljs/<hash>/
```

原理：

- `main.js` 里有 `a.p="/assets/"`，chunk 文件名是 `{id}.{hash}.js`，
  hash 取 `a.h()` 的前 8 位
- 递归提取 `.e(<id>)` 拿到全部 chunk id
- 用关键词定位组件：`data-service-id`、`canRenew`、`projects.renew` 等 i18n key

脚本会从登录页 HTML 现取 hash，输出目录也带 hash，**站点重新部署后直接重跑即可**
（用旧缓存会读到过期代码）。

> 下载用 `requests` 而不是 `urllib` —— 这个站点的响应走 urllib 会稳定抛
> `IncompleteRead`。

本轮排查中各组件的落点（**这是 2026-09 版的映射，2026-11 改版后 chunk id
和 hash 很可能都变了，务必重跑 `crawl_chunks.py` 再核对**）：

| chunk | 内容 |
|---|---|
| `640.js` | 服务详情页：续期提示条 + Renew 按钮 + 续期逻辑 + 人机验证弹窗 |
| `9265.js` | 服务列表页：表格、行展开、列表页那个被隐藏的 Renew 按钮 |
| `7994.js` | 服务被暂停后的页面（另一套续期入口） |
| `main.js` | 各页面的 i18n 文案 |

### 4. 找渲染条件，而不是猜"应该有"

例：详情页续期按钮的显隐条件是从 JS 里读出来的，不是猜的——

```js
// 640.js
q = useStoreState(e => e.server.data.canRenew)   // 就是它
… !le && q && <button onClick={ye}>…renew…</button>
```

列表页那条隐藏按钮的 CSS，也是在 `document.styleSheets` 里逐条匹配元素找到的：

```js
for (const sheet of document.styleSheets)
  for (const rule of sheet.cssRules)
    if (btn.matches(rule.selectorText) && rule.style.display) hits.push(rule)
```

---

## 七、已知限制

- **列表页的续期按钮**：站点故意隐藏，脚本不去点它；只把它当作"没有按钮"的证据。
- **自动启动只点一次**：只在状态为 `offline` 时点 Start，点完以「状态离开 offline」
  为准，不保证服务能稳定跑住；若启动后崩溃，要等下一轮运行才会再次拉起。
- **续期动作无法随时验证**：免费 bot 套餐 4 天一个周期，只有窗口开着时才测得到。
- **续期失败的原因**：站点只在 toast 里提示，且文案随语言变化，
  脚本目前只回传"页面上新出现的文本"，不做解析。
- **CI 定时触发是关的**：`.github/workflows/renew.yml` 里的 `schedule` 被注释掉了，
  只能从 Actions 页面手动跑。
