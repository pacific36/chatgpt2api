# 功能状态

本文基于当前仓库当前实现整理，用于帮助用户快速了解哪些功能已经可用、哪些仍在完善、哪些待实现。

| 功能 | 状态 | 说明 |
|:----------------------------------------|:--:|:--------------------------------------------------------------|
| OpenAI 兼容 `POST /v1/images/generations` | ✅  | 已支持，用于图片生成，并可通过 `n` 返回多张图片。 |
| OpenAI 兼容 `POST /v1/images/edits` | ✅  | 已支持，可上传图片进行编辑。 |
| 面向图片工作流的 `POST /v1/chat/completions` | ✅  | 已支持图片相关请求。 |
| 面向图片工作流的 `POST /v1/responses` | ✅  | 已支持图片生成工具调用。 |
| `GET /v1/models` 接口 | ✅  | 当前返回 `gpt-image-2`、`codex-gpt-image-2`、`auto`、`gpt-5`、`gpt-5-1`、`gpt-5-2`、`gpt-5-3`、`gpt-5-3-mini`、`gpt-5-mini`。 |
| 同时生成多张图片 | ✅  | 已支持，后端与前端都可进行多图生成。 |
| 前端图片工作台 | ✅  | 已支持图片生成、图片编辑、模型选择、历史记录与查看大图。 |
| 前端图片输入 / 参考图交互 | ✅  | 已支持参考图上传、预览、移除和编辑模式工作流。 |
| Codex 画图接口逆向 | ✅  | 已支持，仅 `Plus` / `Team` / `Pro` 订阅可用，模型别名为 `codex-gpt-image-2`；如有需要可自行在其他场景映射回 `gpt-image-2`。这是 Codex 逆向链路，用于和官网画图区分，同一账号通常会同时支持官网和 Codex 两份生图额度。 |
| Cherry Studio 接入 | ✅  | 已支持作为绘图接口接入 Cherry Studio。 |
| New API 接入 | ✅  | 已支持接入 New API。 |
| 账号池管理 | ✅  | 已支持列表、筛选、批量操作、导出、手动编辑、刷新和删除。 |
| 账号额度刷新与恢复时间同步 | ✅  | 已支持账号信息刷新，限流账号也会自动继续检查。 |
| 失效 Token 自动清理 | ✅  | 已支持自动移除失效 Token。 |
| CPA 连接管理 | ✅  | 已支持 CPA 连接的新增、修改、查询和删除。 |
| CPA 文件浏览与按需导入 | ✅  | 已支持读取远程文件列表、筛选、勾选并导入到本地号池。 |
| CPA 导入进度跟踪 | ✅  | 已支持导入进度展示与轮询更新。 |
| `sub2api` 连接管理与账号浏览 | ✅  | 已支持 `sub2api` 服务器的新增、修改、删除、分组查询和 OpenAI OAuth 账号列表读取。 |
| `sub2api` 导入 | ✅  | 已支持勾选 `sub2api` 中的 OpenAI OAuth 账号，批量拉取 `access_token` 导入本地号池，并展示导入进度。 |
| Docker 自托管部署 | ✅  | 已支持 Docker Compose 部署，并提供多架构镜像。 |
| 兼容接口中的多参考图能力 | ✅  | 已实现，支持在兼容接口中传入多参考图。 |
| 更高级的 Token 调度策略 | ⚠️ | 当前已有基础轮询与限流刷新机制，更复杂的调度策略仍在完善中。 |
| Render / Vercel 等部署表述 | ⚠️ | 当前主要以 Docker 部署为主，其他平台部署方式暂未重点说明。 |
| `/v1/complete` 文本补全与流式输出 | ✅  | 已实现。 |
| 流式输出支持 | ✅  | 已实现。 |
| 文本补全缓存与重复请求合并 | ✅  | `/v1/chat/completions` 文本链路默认启用 60 秒短缓存、流式结果回放、in-flight 请求合并和相邻重复消息清理；可通过 `chat_completion_cache` 配置关闭或调整。 |
| 对话文件附件（图片 / PDF / Word / 文本等） | ✅  | `/v1/chat/completions` 支持 OpenAI `image_url` 与 `file` / `input_file` 内容块；图片走 `multimodal` 上传并以 `image_asset_pointer` 入会话，文档走 `my_files` 上传并挂载到消息 attachments，已对真实上游验证 txt / pdf / docx / png。前端对话页支持附件选择、预览与历史回看。 |
| 模型沙箱（code interpreter）文件下载 | ✅  | 模型用 code interpreter 生成的 `sandbox:/mnt/data/...` 文件可真实下载，采用**惰性透传、零本地存储**：响应里把死链改写为带签名的 `/sandbox-files?cid=...&mid=...&p=...&a=...&s=...` 代理链接（生成时不发任何网络请求）；**点击时**才由代理用建该对话的那个账号会话现场调 `interpreter/download` 解析（带轮询等待落盘）→ 取回字节 → 流式透传，本地不留任何文件。链接生命周期与上游一致：上游对话/账号还在就能下，没了即死链（404）。链接用 `auth-key` 做 HMAC 签名，伪造直接 403。受 `sandbox_download` 开关控制（默认开启）；**开启即要求对话开启上游历史**（`history_and_training_disabled=False`），这些经 API 的对话会保存到 ChatGPT 账号并可能用于训练；关闭时回退为死链提示。已对真实账号端到端验证（点击 → 200、16 字节内容一致、attachment 头；伪造签名 → 403；上游无文件 → 404）。注意：能否下载取决于模型当次是否真执行了代码——`auto` 路由有时只输出链接而不执行，此时点击为 404。 |
| 图片尺寸参数 | ⚠️ | `/v1/images/generations` 已接受 `size` 参数并以提示词方式传递给上游；由于 ChatGPT 官网生图接口本身不支持精确尺寸控制，最终尺寸仍取决于上游。 |
| 服务端图片 URL 缓存 | ✅  | 已实现。 |
| `rt_token` 刷新 | ❌  | 待实现。 |
| 代理配置功能 | ✅  | 已支持网页端配置全局 HTTP / HTTPS / SOCKS5 / SOCKS5H 代理，并应用到出站请求。 |
| Anthropic 协议支持 | ✅  | 已支持 `POST /v1/messages`（流式 / 非流式、`system`、`tools`、`stop_sequences`、`max_tokens`）、`POST /v1/messages/count_tokens`，以及面向 Anthropic 客户端（携带 `anthropic-version` 头）的 `GET /v1/models`；错误以 Anthropic `{"type":"error",...}` 信封返回，鉴权支持 `x-api-key` 与 `Authorization: Bearer`。 |
