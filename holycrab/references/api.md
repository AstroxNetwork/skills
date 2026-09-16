# HolyCrab Generation API 文档

> 来源：HolyCrab Public API Contract
>
> Base URL：`https://abgzfc.holycrab.ai`
>
> 更新日期：2026-09-14（与本版 CLI 能力快照同步）
>
> 机器可读快照：[capabilities.json](capabilities.json)

## 目录

- [1. 通用约定](#1-通用约定)
- [2. 账户信息](#2-账户信息)
- [3. 任务查询](#3-任务查询)
- [4. 素材管理](#4-素材管理)
- [5. 视频生成](#5-视频生成)
- [6. 图片生成](#6-图片生成)
- [7. 语音生成](#7-语音生成)
- [8. 推荐调用流程](#8-推荐调用流程)
- [9. 真人授权与真人素材](#9-真人授权与真人素材)
- [10. CLI 安全层](#10-cli-安全层)
- [11. 变更说明](#11-变更说明)

## 1. 通用约定

### 统一响应

所有接口均使用下列响应包裹：

```json
{
  "code": 0,
  "data": {},
  "message": "",
  "errorCode": "OPTIONAL_ERROR_CODE",
  "requestId": "OPTIONAL_REQUEST_ID"
}
```

分页接口的 `data` 为：

```json
{
  "records": [],
  "total": 0,
  "size": 20,
  "current": 1,
  "pages": 0
}
```

### 鉴权

所有接口调用均使用：

API Key 鉴权由 HolyCrab CLI 在本机添加；不要把 Key 写入文档、脚本参数或聊天消息。

缺少、无效或已停用的 API Key 会返回 `401`。

> 不要把 API Key 写进前端公开代码、截图、日志或 Git 仓库。

> 本 Skill 不支持原始文档中需要控制台 JWT 的 `/api/user-tokens...` 接口。

---

## 2. 账户信息

### 获取当前账户

`GET /api/user/me`

鉴权：API Key

返回当前 API Key 对应的账户与可用积分。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `username` | string | 账户名 |
| `email` | string \| null | 已绑定邮箱 |
| `credit` | int | 可用积分 |
| `inviteCode` | string \| null | 邀请码 |

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/user/me' \
  -H 'X-User-Token: <API_KEY>'
```

---

## 3. 任务查询

### 3.1 查询当前 HolyCrab 用户的任务列表

`GET /api/tasks`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `page` | Query | integer | 否 | 默认 `1` |
| `pageSize` | Query | integer | 否 | 默认 `20`，最大 `100` |
| `startDate` | Query | string | 否 | 与 `endDate` 成对使用，格式 `yyyy-MM-dd` |
| `endDate` | Query | string | 否 | 与 `startDate` 成对使用，格式 `yyyy-MM-dd` |
| `taskType` | Query | string | 否 | `IMAGE`、`VIDEO`、`AUDIO` 或 `TEXT` |

返回：`records: TaskVO[]`、`total: integer`。

### 3.2 查询单个任务详情

`GET /api/tasks/{uniqId}`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `uniqId` | Path | string | 是 | 任务唯一 ID |

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| `uniqId` | string | 任务 ID |
| `step` | integer | `0` 已创建、`1` 运行中、`2` 已完成、`3` 失败 |
| `error` | string \| null | 失败原因 |
| `videoUrl` | string \| null | 视频输出 URL |
| `imageUrls` | string[] \| null | 图片输出 URL 列表 |
| `audioIds` | string \| null | 后端保存的 JSON 数组字符串；CLI 解析为 `audioUrls: string[]` |
| `cdnUrl` | string \| null | 可用时的 CDN 输出 URL |

---

## 4. 素材管理

### 4.1 素材列表

`GET /api/user-assets`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `page` | Query | integer | 否 | 默认 `1` |
| `pageSize` | Query | integer | 否 | 默认 `50`，最大 `100` |
| `name` | Query | string | 否 | 按名称模糊匹配 |
| `status` | Query | string | 否 | 处理状态筛选 |

返回：`records: AssetVO[]`、`total: integer`。

### 4.2 素材详情

`GET /api/user-assets/{uniqId}`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `uniqId` | Path | string | 是 | 素材唯一 ID |

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| `uniqId` | string | 素材 ID |
| `step` | string | 处理状态 |
| `url` | string | 媒体 URL |
| `error` | string \| null | 失败原因 |

### 4.3 获取预签名上传地址

`GET /api/user-assets/pre-signed-download-url`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `file_extension` | Query | string | 是 | 文件后缀，不含 `.` |
| `content_type` | Query | string | 是 | 上传时使用的 MIME type |
| `duration_seconds` | Query | integer | 否 | 音频或视频时长（秒） |

| 返回字段 | 类型 | 说明 |
| --- | --- | --- |
| `preSignedUrl` | string | 临时对象存储上传地址 |
| `objectKey` | string | 上传登记时原样传回 |
| `uniqId` | string | 新素材 ID |

### 4.4 登记已上传素材

`POST /api/user-assets/upload`

鉴权：API Key

先 PUT 文件到 `preSignedUrl`，成功后再调用此接口登记素材并启动处理。
请求必须使用 `multipart/form-data`；不能发送 JSON。此请求只登记上一步已经上传的对象，文件本身不需要再次放入表单。

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | multipart field | string | 是 | 素材展示名 |
| `object_key` | multipart field | string | 是 | 上一步返回的 `objectKey` |
| `content_type` | multipart field | string | 是 | 与上传所用 MIME type 一致 |
| `duration_seconds` | multipart field | integer | 否 | 音频或视频时长（秒） |

成功登记没有业务 payload（`data: null`）。

### 4.5 按 URL 创建素材

`POST /api/user-assets/create-asset-from-url`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `url` | Body | string | 是 | 可公开访问的 HTTPS 媒体 URL |
| `name` | Body | string | 否 | 展示名 |

后台异步处理。返回 `uniqId` 和初始 `step`。

### 4.6 删除素材

`POST /api/user-assets/delete`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `uniq_id` | Body | string | 是 | 素材 `uniqId` |

成功删除没有业务 payload（`data: null`）。

### 素材状态

`AssetVO.step` 可为：

`UPLOADED`、`UPLOADING_TO_ARK`、`GETTING_UPLOADED_RESULT`、`UPLOADED_TO_ARK`、`FAILED`、`DELETING`。

---

## 5. 视频生成

视频提交后异步创建任务；使用任务详情接口轮询进度与结果。

### 5.1 提交 Seedance 视频生成任务

`POST /api/tasks/generation`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `prompt` | string | 是 | 提示词 |
| `model` | string | 是 | `seedance-2-0`、`seedance-2-0-fast`、`seedance-2-0-mini`、`dreamina-seedance-2-5-260628` |
| `ratio` | string | 否 | `source`、`adaptive` 或具体画幅；受模型和任务模式限制 |
| `duration` | integer | 是 | 普通生成 `4`–`15` 秒；Seedance 2.5 可到 `30` 秒；智能编辑固定为 `-1` |
| `resolution` | string | 是 | `480p`、`720p`、`1080p`、`4k`；受模型限制 |
| `generateAudio` | boolean | 否 | 是否生成伴随音频；服务端默认 `false`，HolyCrab CLI 和本地 MCP 对支持该能力的 Seedance 模型在省略时默认补为 `true`；显式 `false` 保持静音 |
| `imageAssetIds` | string[] | 否 | 当前账户拥有的图片素材 `uniqId` |
| `videoAssetIds` | string[] | 否 | 当前账户拥有的视频素材 `uniqId` |
| `audioAssetIds` | string[] | 否 | 当前账户拥有的音频素材 `uniqId` |
| `firstFrameAssetId` | string | 否 | 首帧图片素材 `uniqId` |
| `lastFrameAssetId` | string | 否 | 尾帧图片素材 `uniqId` |
| `videoTaskType` | string | 否 | `reference`、`frames`、`edit`、`extend`；省略时根据首帧推断 |
| `sourceVideoAssetId` | string | 条件必填 | `edit` 或 `extend` 的主视频素材 `uniqId` |

返回：`uniqId: string`、`step: integer`。

通用规则：

1. `seedance-2-0` 支持 `480p`、`720p`、`1080p`、`4k`；Seedance 2.5 支持 `480p`、`720p`、`1080p`；Fast 和 Mini 支持 `480p`、`720p`。
2. 有尾帧必须同时有首帧；首尾帧不能和图片、视频、音频参考列表同时使用。
3. `frames` 必须提供首帧；`edit`、`extend` 必须且只能配合 `sourceVideoAssetId`，并且不能使用首尾帧。
4. 素材 ID 必须属于当前 API Key 对应账户，并且素材类型与字段一致。

Seedance 2.0 / Fast / Mini：

- `duration` 为 `4`–`15` 秒。
- 最多 9 张图片、3 段视频、3 段音频；音频不能是唯一参考类型。
- 参考视频和参考音频的合计时长分别不能超过 15 秒。

Seedance 2.5（`dreamina-seedance-2-5-260628`）：

- `reference`、`frames`、`extend` 的 `duration` 为 `4`–`30` 秒；`edit` 必须传 `-1`。
- 支持最多 30 张图片、10 段视频、10 段音频，三类合计不超过 50 个；允许仅音频参考。
- 参考视频和参考音频的合计时长分别不能超过 30 秒。
- `frames`、`edit`、`extend` 强制把 `ratio` 规范化为 `adaptive`。
- `edit` 主视频必须为 4–30 秒；`extend` 主视频必须为 2–30 秒。

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/tasks/generation' \
  -H 'X-User-Token: <API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "A cinematic sunset over the ocean",
    "model": "dreamina-seedance-2-5-260628",
    "ratio": "adaptive",
    "duration": 10,
    "resolution": "720p",
    "generateAudio": true,
    "videoTaskType": "reference"
  }'
```

### 5.2 预估 Seedance 视频冻结积分

`POST /api/tasks/generation/freeze-credit`

鉴权：API Key

该接口使用与 5.1 相同的请求结构和校验器。`model`、`duration`、`resolution` 必填；使用参考素材、首尾帧、智能编辑或视频续写时，必须把正式提交会使用的下列字段原样传入：

- `imageAssetIds`、`videoAssetIds`、`audioAssetIds`
- `firstFrameAssetId`、`lastFrameAssetId`
- `videoTaskType`、`sourceVideoAssetId`

参考视频会影响冻结积分；不要只传模型、时长和清晰度来估算一个实际包含素材的任务。`prompt`、`ratio`、`generateAudio` 可随正式请求传入，但当前冻结金额不直接使用它们。

返回：`frozenCredit: integer`。该接口只预估，不创建任务。

### 5.3 提交 MiniMax H3 视频生成任务

`POST /api/tasks/minimax-generation`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 仅支持 `MiniMax-H3` |
| `prompt` | string | 是 | 非空提示词 |
| `resolution` | string | 是 | `768P` 或 `2K` |
| `duration` | integer | 是 | `4`–`15` 秒 |
| `ratio` | string | 条件必填 | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` |
| `imageAssetIds` | string[] | 否 | 最多 9 张账户图片素材 |
| `videoAssetIds` | string[] | 否 | 最多 3 段账户视频素材 |
| `audioAssetIds` | string[] | 否 | 最多 3 段账户音频素材 |
| `firstFrameAssetId` | string | 否 | 首帧图片素材；可单独使用 |
| `lastFrameAssetId` | string | 否 | 尾帧图片素材；H3 允许没有首帧 |

规则：

1. 纯文字生成必须选择具体画幅，不能使用 `adaptive`。
2. 有参考素材但没有首尾帧时，省略 `ratio` 会规范化为 `adaptive`。
3. 提供任一首尾帧时强制使用 `adaptive`，且不能再提供图片、视频或音频参考列表。
4. 音频不能作为唯一参考类型；必须同时提供至少一张图片或一段视频。
5. 每段参考视频和音频为 2–15 秒；两类素材的合计时长分别不能超过 15 秒。
6. H3 不支持 `generateAudio`，请求中不要发送该字段。

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/tasks/minimax-generation' \
  -H 'X-User-Token: <API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "MiniMax-H3",
    "prompt": "A cinematic sunrise over a quiet mountain lake",
    "resolution": "768P",
    "duration": 5,
    "ratio": "16:9"
  }'
```

### 5.4 预估 MiniMax H3 冻结积分

`POST /api/tasks/minimax-generation/freeze-credit`

鉴权：API Key

请求体必须与 5.3 的正式提交完全一致，包括 `prompt`、画幅和全部素材字段；服务端运行同一套请求与素材校验。返回：`frozenCredit: integer`。该接口只预估，不创建任务。

---

## 6. 图片生成

每次图片生成请求创建一个异步任务。

### 6.1 提交图片生成任务

`POST /api/tasks/image-generation`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `prompt` | string | 是 | 提示词 |
| `model` | string | 是 | `seedream-5-0-pro-260628`、`seedream-5-0-lite-260128`、`seedream-4-5-251128` |
| `size` | string | 否 | 模型支持的分辨率档位或 `宽x高` |
| `aspectRatio` | string | 否 | `auto`、`21:9`、`16:9`、`3:2`、`4:3`、`1:1`、`3:4`、`2:3`、`9:16`；作为 UI 元数据保存 |
| `resolution` | string | 否 | `1k`、`2k`、`3k`、`4k`；必须为所选模型支持的档位，作为 UI 元数据保存 |
| `imageUrls` | string[] | 否 | 公开可访问的 HTTPS 参考图片 URL |
| `outputFormat` | string | 否 | 支持时为 `jpeg` 或 `png` |

返回：`uniqId: string`、`step: integer`。

规则：

| 模型 | 参考图上限 | 支持档位 | 自定义尺寸像素范围 |
| --- | --- | --- | --- |
| Seedream 5.0 Pro | 10 | 1K、2K | 921,600–4,624,220 |
| Seedream 5.0 Lite | 14 | 2K、3K、4K | 3,686,400–16,777,216 |
| Seedream 4.5 | 14 | 2K、4K | 3,686,400–16,777,216 |

- Seedream 5.0 Pro 自定义宽高必须是 16 的倍数；Lite 和 4.5 没有此限制。三者比例范围均为 1:16–16:1。
- 参考图支持 `jpeg`、`png`、`webp`、`bmp`、`tiff`、`gif`、`heic`、`heif`。
- 单张参考图最大 30 MB，宽高都必须大于 14 px，总像素不超过 36,000,000。
- `outputFormat` 仅适用于 Seedream 5.0 Pro / Lite；可选 `jpeg`、`png`。Seedream 4.5 不使用该字段；省略或无效值时使用 `jpeg`。

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/tasks/image-generation' \
  -H 'X-User-Token: <API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "A crab astronaut on the moon",
    "model": "seedream-5-0-lite-260128",
    "size": "2k",
    "outputFormat": "png"
  }'
```

### 6.2 预估图片冻结积分

`POST /api/tasks/image-generation/freeze-credit`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | Seedream 模型 ID |
| `size` | string | 否 | 请求尺寸 |
| `imageUrls` | string[] | 否 | 参考图片 URL |

返回：`frozenCredit: integer`。该接口只预估，不创建任务。

---

## 7. 语音生成

语音输出为异步任务。使用任务详情接口读取 `audioIds`、状态和错误。

### 7.1 提交语音生成任务

`POST /api/tasks/audio-generation`

鉴权：API Key

固定模型：`seed-audio-1.0`（服务端自动设置，请求体不传 `model`）。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `textPrompt` | string | 是 | 最多 3000 字符 |
| `references` | object[] | 否 | 音频或图片参考对象 |
| `audioConfig.format` | string | 否 | `wav`、`mp3`、`pcm`、`ogg_opus` |
| `audioConfig.sample_rate` | integer | 否 | 8000、16000、24000、32000、44100、48000 |
| `audioConfig.speech_rate` | integer | 否 | -50 至 100 |
| `audioConfig.loudness_rate` | integer | 否 | -50 至 100 |
| `audioConfig.pitch_rate` | integer | 否 | -12 至 12 |

返回：`uniqId: string`、`step: integer`。

规则：

1. 每个音频参考对象只能选一个：`references[].speaker`、`references[].audio_data` 或 `references[].audio_url`。
2. 每个图片参考对象只能选一个：`references[].image_data` 或 `references[].image_url`。
3. 最多 3 段音频，或 1 张图片；图片和音频参考不能混用。
4. 单段参考音频最长 30 秒、最大 10 MB，支持 `wav`、`mp3`、`pcm`、`ogg`；参考图最大 10 MB，支持 `jpeg`、`png`、`webp`。
5. 生成音频最长 120 秒。
6. 多段音频在提示词中按 `@Audio1`、`@Audio2`、`@Audio3` 引用。
7. 纯文本生成时省略 `references`。图片参考模式只传 1 张图片和提示词，提示词中不要写 `@Image1`。
8. 公开示例使用 camelCase；控制器兼容 `text_prompt` 和 `audio_config` 两个别名。

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/tasks/audio-generation' \
  -H 'X-User-Token: <API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "textPrompt": "A calm narrator describes a quiet morning.",
    "audioConfig": {
      "format": "mp3",
      "sample_rate": 24000
    }
  }'
```

### 7.2 预估语音冻结积分

`POST /api/tasks/audio-generation/freeze-credit`

鉴权：API Key

无需路径参数、Query 或请求体。

返回：`frozenCredit: integer`。该接口只预估，不创建任务。

---

## 8. 推荐调用流程

### 本地文件作为素材

1. 调用“获取预签名上传地址”。
2. 用返回的 `preSignedUrl` 将文件 PUT 到对象存储。
3. 用 `multipart/form-data` 调用“登记已上传素材”，把 `objectKey` 原样传给 `object_key`。
4. 轮询素材详情，直到 `step` 成功或失败。
5. 将成功素材的 `uniqId` 填入视频生成的素材字段。

### 创建生成任务

1. 可先调用对应的 `freeze-credit` 预估积分。
2. 提交视频、图片或语音生成接口。
3. 保存返回的 `uniqId`。
4. 轮询 `GET /api/tasks/{uniqId}`，直到 `step=2` 或 `step=3`。

## 9. 真人授权与真人素材

本节对应 `v0.4.2` 工具能力，需要配套 API 和官方回调页已部署。工具不替本人完成验证；真人授权也不代替素材上传确认或生成任务的积分确认。所有账户操作使用现有 API Key，公共 ID 为 1–64 位字母或数字。

### 9.1 发起授权

`POST /api/real-human-authorizations/sessions`

请求：`name`（必填，去除首尾空白后 1–255 字符）、`callbackUrl`（工具固定为 `https://generate.holycrab.ai/real-human-authorization/callback`）。CLI/MCP 不接受自定义回调地址。

公开工具输出：`authorizationId`、`h5Link`、`expiresAt`、`qrPath`。MCP 另附 PNG 图片内容；二维码保存失败时用 `warning` 替代 `qrPath`，仍可使用链接。上游原始响应可能包含验证凭据和内部字段，工具不会原样返回。

会话有效期为 30 分钟，远端状态为准。当前 API 时间戳可能不带时区，客户端不能把它当作本机时区。本人完成 H5 验证后必须点击完成返回官方回调页，由网页提交结果；Agent 不调用结果提交接口，不收集独立的验证令牌。

### 9.2 查询授权

`GET /api/real-human-authorizations/{authorizationId}`

公开输出：`authorizationId`、`status`、可选 `completedAt`，成功时含 `group: {uniqId, name}`。

| 状态 | 处理 |
| --- | --- |
| `CREATED` | 等待本人操作，稍后查询同一 ID |
| `SUCCEEDED` | 使用 `group.uniqId` 查询或上传人物素材 |
| `FAILED` | 停止等待，解释验证失败，不自动重建 |
| `EXPIRED` | 停止等待，需要用户再次发起授权 |

CLI `real-human wait` 默认间隔 5 秒、超时 600 秒；间隔必须为有限正数，超时为有限非负数。成功退出码 0，失败/过期为 1，等待超时为 2。MCP 只提供短查询，不阻塞等待。查询终态时删除本机二维码，后续 CLI/MCP 调用清理过期文件；没有后台清理服务。

### 9.3 人物及素材列表

| 接口 | 参数 | 公开记录字段 |
| --- | --- | --- |
| `GET /api/real-human-groups` | `page` 默认 1，`pageSize` 默认 20、最大 100 | `uniqId`、`name`、`coverUrl`、`assetCount`、`processingCount`、`createdAt` |
| `GET /api/real-human-groups/{groupUniqId}/assets` | `page` 默认 1，`pageSize` 默认 50、最大 100 | 公开素材字段及 `ready` |

保留分页的 `records`、`total` 及存在时的 `current`、`size`、`pages`，不能只看第一页就判定人物不存在。分组名称可能重复，使用 ID 区分。

### 9.4 上传及等待真人素材

1. 检查指定人物分组可访问。
2. 复用 `GET /api/user-assets/pre-signed-download-url`，PUT 图片或视频到返回的对象存储地址；不向对象存储转发 API Key。
3. 使用 multipart 调用 `POST /api/real-human-groups/{groupUniqId}/assets/upload`；字段为 `name`、`object_key`、`content_type`，可选 `duration_seconds`。
4. CLI/MCP 在真正上传前先生成 30 分钟有效的本地计划，列出目标人物及全部文件；一次确认后才按顺序执行。计划不保存 Key、上传地址或文件内容，执行前重新核对文件。
5. 返回 `assetUniqId`、`groupUniqId` 和 `ready: false`。登记完成不代表审核或同步完成。
6. 调用 `GET /api/user-assets/{uniqId}`。工具仅在 `step: UPLOADED_TO_ARK` 时返回 `ready: true`，`FAILED` 时返回公开 `error`。CLI `assets wait` 可接收多个 ID，并使用相同的间隔、超时和退出码规则。

公开素材字段：`uniqId`、`name`、`assetType`、`step`、`status`、`error`、`duration`、`url`、`createTime`、`updateTime`、`ready`；不返回上游素材 ID。就绪后把公开 `uniqId` 放入现有视频请求的 `imageAssetIds` 或 `videoAssetIds`，仍需先查模型能力和预估积分。

### 9.5 重命名与永久删除

| 操作 | 后端接口 | CLI | MCP |
| --- | --- | --- | --- |
| 重命名人物 | `PATCH /api/real-human-groups/{groupUniqId}`，JSON `{name}` | `real-human groups rename GROUP_ID --name NAME` | `real_human_group_rename` |
| 删除人物 | `DELETE /api/real-human-groups/{groupUniqId}` | `real-human groups delete GROUP_ID` | `real_human_group_delete` |
| 删除真人素材 | `DELETE /api/real-human-groups/{groupUniqId}/assets/{assetUniqId}` | `real-human assets delete ASSET_ID --group GROUP_ID` | `real_human_asset_delete` |

重命名名称去除首尾空白后必须为 1–255 字符，成功只返回公开人物 ID 和名称。删除人物会同时删除组内素材和上游人物分组；删除单个素材会清理存储、上游记录和数据库记录，均不可恢复。

CLI 删除前查询并显示目标。交互模式要求确认；`--yes` 只可用于用户已经明确授权的具体对象。MCP 删除工具要求 `confirmed: true`，缺失或为 `false` 时不会查询或删除。Agent 不能把真人授权成功、素材上传或生成确认当作删除确认。

PATCH/DELETE 遇到连接中断、超时、5xx 或响应格式异常时只发送一次，随后通过列表或详情查询实际状态，不自动重试。工具只接受公开 `groupUniqId` 和素材 `uniqId`；不支持授权撤销或自动留存清理。

创建授权或登记素材时，连接中断、网关 502/504、HTTP 200 无效响应都可能无法确认操作结果。保留已知 ID并查询，不自动重新创建、重新上传或回退到普通素材分组。真人批量最多 10 个文件；图片小于 30 MiB、视频不超过 50 MiB，音频禁止。首个失败或不明确结果会停止整批，并分别返回已上传、失败或不明、未执行清单。

## 10. CLI 安全层

- `setup` / `auth set-key` 保留公开账号字段并输出一个 JSON 结果，追加 `configured`、`valid`、`savedKeyVerified`、`credentialSource` 和 `onboarding`；保存成功不代表环境变量覆盖后的活动账号有效。
- `doctor`、`auth status`、MCP `cli_status` / `account_get` 追加统一 `onboarding`，包括 `state`、`instruction`、`command`、`businessUses`、三个 `examples`、`question` 和 `agentInstruction`。状态为 `CONNECT_ACCOUNT / VERIFY_ACCOUNT / READY`；普通离线自检不会返回 `READY`。异常成功响应不能证明账号有效。
- Agent 仅在安装或首次连接对话中、账号验证成功后介绍业务场景一次，并使用用户当前语言。示例不构成上传、授权、付费或删除许可；费用估算留在实际执行流程，不要求用户在需求中提及。
- 测试安装通过 `HOLYCRAB_INSTALL_REF` 指定完整提交号，默认下载引用仍为版本标签；下载引用不改变版本号校验。正式更新清除该变量，保留 GitHub Release、SHA-256 和严格版本校验。
- `estimate` 与 `create` 共用能力校验器；已知非法模型、分辨率、时长、素材组合和字段会在请求前拒绝。
- 生成提交只在收到合法任务 ID 时记为 `created`；明确 4xx 业务拒绝记为 `failed`；408、5xx、断网、异常 2xx 和缺少任务 ID 都记为 `unknown`，不会重试。用 `generate attempts list|get` 或 MCP `generation_attempt_list|get` 查询本地记录。
- 授权、素材、估算、任务及需要恢复的 attempt 结果包含 `nextAction: {code, instruction, command}`。缺少文件、输出位置或请求时 `command` 为 `null`，Agent 应询问用户，不应拼接占位命令。即时查询无需额外建议。轮询仅在状态变化和结束时输出。
- CLI 过程提示仅在交互终端写入 stderr；JSON stdout 和 MCP 消息不含过程文本。`Ctrl+C` 返回 130：只读等待停止本机轮询；写入开始后保留已知 ID，生成 attempt 记为 `unknown`，上传计划保留已执行边界和批次记录，不自动重试。下载残片会被清理，已有文件保留。
- MCP 上传仅提供 `asset_upload_prepare` 和 `asset_upload_execute`：前者只检查并预览，后者必须接收 `uploadPlanId + confirmed: true`。
- 下载只接受无内嵌凭据、无 fragment、非本机/内网地址的 HTTPS；每次重定向重新校验，限制跳转和总大小，先写私有临时文件再原子落盘，默认不覆盖现有文件。
- 任务接口原始字符串 `audioIds` 由 CLI 解析为 `audioUrls`，音频结果可由 `holycrab download` 下载。
- 普通 `doctor` 只看本地状态和版本缓存；`doctor --online` 额外刷新版本并验证 API Key。更新只接受更高的稳定语义版本，需用户运行 `holycrab update`，不会静默安装。
- `holycrab uninstall [--purge] [--yes]` 是纯本地生命周期命令，不对应后端 API 或 MCP 工具。默认保留凭据与任务记录；`--purge` 只删除已知本地状态，不撤销服务端 API Key。命令只移除安装清单证明属于当前 CLI 的文件、PATH 和 MCP 登记，修改过或无法确认归属的内容保留并提示。
  安装清单的 `agentRegistrations` 仅记录 Codex/Claude Code 的实际客户端路径、用户级登记命令/参数和 `managed` 归属，不包含凭据。卸载优先使用记录路径，并核对完整命令/参数；其他名称、不同命令、额外参数或非安装器管理的登记不删除。Claude 只删除 `--scope user` 的登记。客户端不可用或检查失败时明确报告 `MCP cleanup pending`，程序卸载仍可继续但不报告全量清理完成；匹配登记删除失败则在删除程序前终止。旧清单仍须通过完整命令/参数核对，不靠字符串包含关系判断。

## 11. 变更说明

本公开版本包含：

- 真人授权链接、本地二维码、授权状态与真人分组查询、指定人物素材上传和就绪状态查询。
- 真人分组重命名、真人分组永久删除和单个真人素材永久删除，并要求明确确认。

- 新增 Seedance 2.5 模型、30 秒生成、智能编辑、视频续写和 50 个参考素材限制。
- 新增 MiniMax H3 独立生成与冻结积分接口。
- 修正 Seedance `freeze-credit` 必须携带实际素材和任务模式字段的说明。
- Seedream 5.0 Pro / Lite / 4.5 的图片尺寸、参考图、格式约束。
- 语音生成的参考对象、配置字段与兼容别名。
- 素材预签名上传、登记和按 URL 创建的完整流程。
