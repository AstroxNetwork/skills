# HolyCrab Generation API 文档

> 来源：HolyCrab Public API Contract
>
> Base URL：`https://abgzfc.holycrab.ai`
>
> 更新日期：2026-08-14
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

### 3.1 查询当前 API Key 的任务列表

`GET /api/tasks`

鉴权：API Key

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `page` | Query | integer | 否 | 默认 `1` |
| `pageSize` | Query | integer | 否 | 默认 `20`，最大 `100` |
| `startDate` | Query | string | 否 | 与 `endDate` 成对使用，格式 `yyyy-MM-dd` |
| `endDate` | Query | string | 否 | 与 `startDate` 成对使用，格式 `yyyy-MM-dd` |

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
| `audioIds` | string[] \| null | 音频输出 ID 列表 |
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

1. `seedance-2-0` 支持 `480p`、`720p`、`1080p`、`4k`；Fast、Mini 和 Seedance 2.5 只支持 `480p`、`720p`。
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
| `imageUrls` | string[] | 否 | 公开可访问的 HTTPS 参考图片 URL |
| `outputFormat` | string | 否 | 支持时为 `jpeg` 或 `png` |

返回：`uniqId: string`、`step: integer`。

规则：

| 模型 | 参考图上限 | 支持档位 | 自定义尺寸像素范围 |
| --- | --- | --- | --- |
| Seedream 5.0 Pro | 10 | 1K、2K | 921,600–4,624,220 |
| Seedream 5.0 Lite | 14 | 2K、3K、4K | 3,686,400–16,777,216 |
| Seedream 4.5 | 14 | 2K、4K | 3,686,400–16,777,216 |

- 自定义宽高必须是 16 的倍数，比例范围 1:16–16:1。
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

## 9. 变更说明

本公开版本包含：

- 新增 Seedance 2.5 模型、30 秒生成、智能编辑、视频续写和 50 个参考素材限制。
- 新增 MiniMax H3 独立生成与冻结积分接口。
- 修正 Seedance `freeze-credit` 必须携带实际素材和任务模式字段的说明。
- Seedream 5.0 Pro / Lite / 4.5 的图片尺寸、参考图、格式约束。
- 语音生成的参考对象、配置字段与兼容别名。
- 素材预签名上传、登记和按 URL 创建的完整流程。
