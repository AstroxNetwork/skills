# HolyCrab Generation API 文档

> 来源：`light-mode` 分支当前的 `client/user-front/public/api-docs.html`  
> Base URL：`https://abgzfc.holycrab.ai`  
> 更新日期：2026-07-27

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

`X-User-Token: YOUR_32_CHAR_USER_TOKEN`

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
  -H 'X-User-Token: YOUR_32_CHAR_USER_TOKEN'
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

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | Body | string | 是 | 素材展示名 |
| `object_key` | Body | string | 是 | 上一步返回的 `objectKey` |
| `content_type` | Body | string | 是 | 与上传所用 MIME type 一致 |
| `duration_seconds` | Body | integer | 否 | 音频或视频时长（秒） |

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

### 5.1 提交视频生成任务

`POST /api/tasks/generation`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `prompt` | string | 是 | 提示词 |
| `model` | string | 是 | `seedance-2-0`、`seedance-2-0-fast`、`seedance-2-0-mini` |
| `ratio` | string | 否 | 例如 `16:9`；默认沿用来源比例 |
| `duration` | integer | 是 | `4`–`15` 秒 |
| `resolution` | string | 是 | `480p`、`720p`、`1080p`、`4k`；受模型限制 |
| `generateAudio` | boolean | 否 | 是否生成伴随音频；默认 `false` |
| `imageAssetIds` | string[] | 否 | 当前账户拥有的图片素材 `uniqId` |
| `videoAssetIds` | string[] | 否 | 当前账户拥有的视频素材 `uniqId` |
| `audioAssetIds` | string[] | 否 | 当前账户拥有的音频素材 `uniqId` |
| `firstFrameAssetId` | string | 否 | 首帧图片素材 `uniqId` |
| `lastFrameAssetId` | string | 否 | 尾帧图片素材 `uniqId` |

返回：`uniqId: string`、`step: integer`。

规则：

1. `480p`、`720p` 支持三个 Seedance 模型；`1080p`、`4k` 只支持 `seedance-2-0`。
2. `duration` 必须是 `4` 到 `15` 的整数。
3. 最多 9 张图片、3 段视频、3 段音频参考。
4. 每段参考视频：2–15 秒、最大 200 MB、仅 `mp4`/`mov`、宽高比 0.4–2.5；多段参考视频总时长不超过 15 秒。
5. 每段参考音频：2–15 秒、最大 15 MB、仅 `wav`/`mp3`；多段总时长不超过 15 秒。
6. 有尾帧必须同时有首帧；首尾帧模式不能和图片、视频、音频参考列表同时使用。
7. 音频不能是唯一参考，至少还要有一张图片或一段视频。

```bash
curl -sS 'https://abgzfc.holycrab.ai/api/tasks/generation' \
  -H 'X-User-Token: YOUR_32_CHAR_USER_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "A cinematic sunset over the ocean",
    "model": "seedance-2-0",
    "ratio": "16:9",
    "duration": 5,
    "resolution": "720p",
    "generateAudio": false
  }'
```

### 5.2 预估视频冻结积分

`POST /api/tasks/generation/freeze-credit`

鉴权：API Key

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 视频模型 ID |
| `duration` | integer | 是 | 请求时长 |
| `resolution` | string | 是 | 请求清晰度 |

返回：`frozenCredit: integer`。该接口只预估，不创建任务。

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
  -H 'X-User-Token: YOUR_32_CHAR_USER_TOKEN' \
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
  -H 'X-User-Token: YOUR_32_CHAR_USER_TOKEN' \
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
3. 调用“登记已上传素材”，把 `objectKey` 原样传给 `object_key`。
4. 轮询素材详情，直到 `step` 成功或失败。
5. 将成功素材的 `uniqId` 填入视频生成的素材字段。

### 创建生成任务

1. 可先调用对应的 `freeze-credit` 预估积分。
2. 提交视频、图片或语音生成接口。
3. 保存返回的 `uniqId`。
4. 轮询 `GET /api/tasks/{uniqId}`，直到 `step=2` 或 `step=3`。

## 9. 变更说明

本版本包含 `light-mode` 分支已更新的：

- 视频首尾帧、全能参考对应的服务端请求字段与互斥规则。
- Seedream 5.0 Pro / Lite / 4.5 的图片尺寸、参考图、格式约束。
- 语音生成的参考对象、配置字段与兼容别名。
- 素材预签名上传、登记和按 URL 创建的完整流程。
