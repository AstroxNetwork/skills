# Codex Skills

本目录统一存放 Astrox 项目使用的 Codex Skills。各 Skill 独立维护，可供多个项目复用。

## Skill 清单

| Skill | 用途 | 鉴权 | 文档 |
| --- | --- | --- | --- |
| `holycrab` | 调用 HolyCrab Generation API，支持账户、任务、素材、视频、图片和语音生成 | 环境变量 `HOLYCRAB_API_KEY` | [SKILL.md](holycrab/SKILL.md) |

## Holycrab

`holycrab` Skill 提供以下能力：

- 查询当前账户、积分、任务列表和任务详情。
- 管理素材，包括列表、详情、URL 导入、删除以及本地文件上传登记。
- 提交 Seedance 视频、Seedream 图片和语音生成任务。
- 调用对应的 `freeze-credit` 接口预估冻结积分。
- 根据任务 `uniqId` 轮询异步任务，直到完成、失败或超时。
- 使用通用请求命令调用 API Key 鉴权的 HolyCrab 接口。

所有请求只使用环境变量 `HOLYCRAB_API_KEY`：

```bash
export HOLYCRAB_API_KEY='YOUR_32_CHAR_USER_TOKEN'
```

可选使用 `HOLYCRAB_BASE_URL` 覆盖默认服务地址：

```bash
export HOLYCRAB_BASE_URL='https://abgzfc.holycrab.ai'
```

常用命令：

```bash
# 查询账户
python3 holycrab/scripts/holycrab_api.py request GET /api/user/me

# 查询任务
python3 holycrab/scripts/holycrab_api.py \
  request GET /api/tasks --query page=1 --query pageSize=20

# 提交图片生成任务；写请求会立即执行
python3 holycrab/scripts/holycrab_api.py \
  request POST /api/tasks/image-generation \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2k"}'

# 轮询任务
python3 holycrab/scripts/holycrab_api.py poll-task TASK_UNIQ_ID

# 上传并登记本地素材
python3 holycrab/scripts/holycrab_api.py \
  upload-asset /absolute/path/media.mp4 \
  --content-type video/mp4 \
  --duration-seconds 8
```

完整接口约束参见 [HolyCrab API 参考](holycrab/references/api.md)。写操作不会二次确认，调用前必须检查接口、参数和费用。

## 新增 Skill 约定

后续新增 Skill 时，统一采用小写短横线命名，并使用以下结构：

```text
skills/<skill-name>/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/       # 可选：可重复执行的工具
├── references/    # 可选：API、协议或领域文档
└── assets/        # 可选：输出所需模板和静态资源
```

新增后需要：

1. 在上方“Skill 清单”中登记名称、用途、鉴权和文档入口。
2. 在本 README 增加对应的简要说明、环境变量和最常用示例。
3. 凭据只通过环境变量读取，不写入代码、命令示例或仓库。
4. 使用 Skill 校验工具验证目录结构和 `SKILL.md` 元数据。
