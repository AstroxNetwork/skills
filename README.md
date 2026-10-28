# HolyCrab Agent Tools

这个仓库提供一套运行在用户电脑上的 HolyCrab 工具：

- `holycrab` CLI：配置 API Key、查模型、估积分、发起真人授权、上传素材、创建和查询任务。
- 本地 MCP：由同一个命令通过 `holycrab mcp serve` 启动，供 Codex、Claude Code 等 Agent 调用。
- 薄 Skill：教 Agent 先查能力、先估积分、获得确认后只提交一次。

它们共用同一份公开能力快照和同一套安全请求代码，直接调用 HolyCrab 现有正式 API，不需要新增 OAuth 或远程 MCP 后端。`v0.6.0` 使用 `2026-10-23` 能力快照；这是随版本发布的静态合同，不冒充实时模型目录。

`v0.6.0` 新增四个 Berry 视频模型，合计 16 个模型，并同步服务端素材规则。CLI 检查请求结构与本地文件完整性，素材合规统一由后端判断。该版本还包括可靠性重构：严格校验积分预估、保留磁盘故障时已知任务和素材 ID、跨进程限制同一上传计划只执行一次，并增加上传记录查询。完整更新日志见 [v0.6.0 发布说明](.github/releases/v0.6.0.md)；官方稳定入口在对应 Release 正式发布并验证后同步。

`v0.5.0` 新增 MiniMax H3 Max、Agnes Video 2.5 和 Agnes Image 2.5 Flash，CLI 与本地 MCP 共用 12 个模型的能力与参数校验。账户查询返回有效的 `disabled_models`；缺失或异常表示权限未知，最终权限和积分以预估接口为准。保留既有安装、更新回滚与重复提交保护。官方稳定入口仅在对应 Release 发布并验证后同步，不安装 Draft 或测试分支。

`v0.4.1` 增加启动自检、每日更新提示、付费提交防重、严格下载防护和真人素材两阶段批量上传。真人功能依赖正式 API 和官方回调页；本人完成验证，Agent 只查询结果。

## v0.7.0 创作流程

直接在 Codex 描述视频、图片、配音或剪辑需求，HolyCrab 会读取对应指南、整理方案、预估积分，再按已有授权执行。多步骤任务可保存和恢复，支持局部修改；本地 FFmpeg 可完成拼接、音乐和字幕。终端默认简洁，详细记录可用 `--json-output` 查看。详见 [创作指南](CREATIVE-SKILLS.md)和 [0.7.0 发布说明](.github/releases/v0.7.0.md)。本轮创作 Agent 已验证 Codex。

## 系统要求与一键安装

支持 macOS、Linux 和 Windows，需要 Python 3.10+；macOS/Linux 还需要 `curl`。不需要 `sudo` 或管理员权限。

下面的官方命令始终安装最新正式版。只有通过发布验证并上传安装器的稳定版本才会更新这些入口；Draft 和预发布版本不会进入普通用户的安装流程。内部测试请先克隆仓库，再使用后面的本地源码安装方式。

macOS / Linux：

```bash
curl -fsSL https://holycrab.ai/cli/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"
```

Windows PowerShell：

```powershell
irm https://holycrab.ai/cli/install.ps1 | iex
```

安装器会把命令放到用户目录的 `.local/bin`，安装 Codex 与 Claude Code 的 Skill，并在检测到对应客户端时登记本地 MCP。macOS/Linux 安装器会幂等写入当前 shell 的启动配置；PowerShell 安装器会更新当前会话和用户级 PATH。它不会写入 API Key。

Windows 的安装目录不能含 `%` 或 `!`，这是 CMD 启动器的限制；可通过 `HOLYCRAB_INSTALL_PREFIX` 选择其他目录。请求文件仍可包含这些字符，按 [Windows 特殊字符调用示例](docs/automated-testing.md#windows-特殊字符请求文件) 传入，避免 Shell 在 CLI 启动前展开文件名。

## 覆盖安装、升级和卸载

从 `v0.4.1` 起，CLI 启动时会按本地缓存检查 GitHub 最新稳定版，只提示、不静默安装。`v0.6.0` 成功检查后缓存 24 小时，失败后约 1 小时再尝试；同一新版每天最多提醒一次，发现更高版本可重新提醒。MCP 持续运行期间也会在工具调用时按相同周期检查，Agent 在当前会话简短告知一次。自动检查采用短网络超时，失败不会中止原命令。

```bash
holycrab update --check
holycrab update
holycrab update --yes
```

安装器和更新器都校验 SHA-256，在同一位置替换程序，保留 API Key、本地 attempt、上传计划和原有 Agent 设置。替换前会临时备份托管文件，自检失败时恢复程序及本次改动的 Agent 连接；恢复不完整时保留备份并说明位置。安装成功后删除临时备份，不保留两个可运行版本。`v0.4.0` 没有更新器，因此旧用户仍需手动运行一键安装命令升级一次。离线或受管环境可设置 `HOLYCRAB_NO_UPDATE_CHECK=1`，关闭自动联网和自动提醒；显式 `update --check` 仍立即联网检查。新提醒策略在升级到 `v0.6.0` 后生效，旧客户端继续使用原有检查逻辑。

交互式卸载会先列出影响范围，并且只确认一次；脚本环境在确认后使用 `--yes`：

```text
holycrab uninstall
holycrab uninstall --yes
holycrab uninstall --purge
holycrab uninstall --purge --yes
```

默认卸载只移除当前安装器管理的 CLI、匹配的 MCP 登记和未修改的 Skill 文件，保留本机 API Key、attempt、上传计划及授权二维码记录。`--purge` 才清理这些已知本地状态；额外或修改过的文件不会删除。卸载不会联系 HolyCrab 服务，也不会让线上 API Key 失效，需要彻底停用时仍须前往 [API Key 页面](https://generate.holycrab.ai/user-tokens)撤销。卸载命令仅供本机用户执行，不提供 MCP 工具。

安装器记录 Codex、Claude Code 的实际客户端路径及登记归属，卸载时核对完整命令和参数后清理。即使普通终端的 PATH 中找不到客户端，也会尝试记录的路径及少量标准安装位置。Claude 只清理用户级登记；手动登记、其他 Agent 和其他 MCP 配置不修改。无法检查的登记列为 `MCP cleanup pending`，卸载返回退出码 `1`，按提示在对应 Agent 中清理；匹配登记删除失败时，先停止卸载，保留程序文件。原有或共享 PATH 按正常保留项提示。

覆盖安装可减少启用的 Agent：不再启用的、未修改的托管 Skill 和可确认归属的 MCP 登记会被清理；修改过的文件及暂时无法检查的登记保留，所有权记录也会保留。安装设置与保留资源分开记录，之后再次升级不会重新启用已关闭的 Agent。

安装、升级和卸载共用安装前缀下的 `.holycrab-install.lock` 目录锁；已有操作进行时新操作会停止。进程被强制终止后锁可能残留，先确认原进程已退出并检查提示中的回滚备份，再手动移除该锁目录。不要删除任务或上传记录来解除锁。

## 配置 Key 和自检

先在 [HolyCrab API Key 页面](https://generate.holycrab.ai/user-tokens) 创建或复制 Key，然后运行：

```bash
holycrab setup
holycrab doctor --json
holycrab auth status
```

`holycrab setup` 会隐藏输入、验证并保存 Key。`holycrab auth set-key` 仅为旧脚本保留隐藏兼容，不作为新流程入口。macOS/Linux 使用仅当前用户可读写的本地配置文件；Windows 使用当前用户 DPAPI 加密，首次读取旧版明文时会自动迁移。临时环境变量 `HOLYCRAB_API_KEY` 会覆盖本地配置；如果看到覆盖警告，请先清除该变量，再检查 Key 状态。

## 连接后开始创作

账号验证成功后，Agent 会继续你已描述的任务；尚未给出任务时提供简短示例。你可以直接告诉 Codex 或 Claude Code：

> 帮我把这些产品照片制作成电商商品主图，突出产品卖点，风格简洁。
>
> 把这几张产品图片做成一段适合社交媒体投放的广告视频，重点展示产品的使用场景。
>
> 把这段产品介绍制作成自然、清晰的中文配音，用于产品宣传视频。

提供素材和期望效果即可。Agent 会确认缺少的信息、检查模型支持情况，并在执行时说明费用、等待确认；上传文件会单独展示清单并询问确认。真人授权后仍需选择和上传素材。示例不会自动执行；如果 Agent 仍看不到工具，请重启 Agent 后继续。

普通离线自检只能确认本地文件及 Key 是否已配置，不能证明账号有效。直接在终端运行时，CLI 显示简短结果和适用的下一步，不打印内部 Agent 规则。脚本或管道调用仍输出完整 JSON；`holycrab doctor --json`、`holycrab models list --json` 和 `setup --stdin` 保留结构化输出。过程提示写入 stderr，MCP 协议不变。

安装显示 5 个阶段，重复安装和升级不再重复首次使用介绍。列表按条目显示摘要；授权成功后仍需选择并上传素材，上传成功后仍需等待处理。批量上传预览保留全部文件及完整路径，结果区分已上传、失败或不明确、未执行。Agent 用用户当前语言说明结果，不直接展示内部规则或原始 JSON。

测试固定提交时，通过 `HOLYCRAB_INSTALL_REF` 指定完整的 40 位提交号；不要替换安装器版本变量，也不要使用可变分支作为下载地址。PowerShell 示例中的 `COMMIT_SHA` 需替换为已验证的测试提交：

```powershell
$ref='COMMIT_SHA'; $oldRef=$env:HOLYCRAB_INSTALL_REF; try { $env:HOLYCRAB_INSTALL_REF=$ref; & ([scriptblock]::Create((irm "https://raw.githubusercontent.com/AstroxNetwork/skills/$ref/install.ps1"))) } finally { $env:HOLYCRAB_INSTALL_REF=$oldRef }
```

此命令面向原生 Windows PowerShell，先确认电脑已有 Python 3.10+。测试安装仍校验 `0.6.0` 版本与文件哈希；正式更新会清除临时下载引用，仅安装通过发布校验的稳定版。

## 常用命令

积分估算统一使用 `holycrab generate estimate`。`holycrab credits estimate` 仅作为旧脚本的兼容入口保留，不再列入推荐帮助。

脚本在用户确认估算后，用 `generate create ... --yes --approved-credits AMOUNT` 传入已确认的 `estimate.frozenCredit`；MCP 对应 `approvedCredits`。提交前估算金额改变时停止，要求重新确认。交互命令自动执行此检查。该检查不锁定后端最终扣费金额。请求文件 `--json @FILE` 支持 UTF-8、UTF-8 BOM 及带 BOM 的 UTF-16；输出仍为 UTF-8。

```bash
holycrab models list
holycrab models show dreamina-seedance-2-5-260628
holycrab credits balance

holycrab generate estimate --kind image \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2K"}'

holycrab generate create --kind image \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2K"}'

holycrab tasks list
holycrab tasks list --start-date 2026-09-01 --end-date 2026-10-09 --type VIDEO
holycrab tasks get TASK_ID
holycrab tasks wait TASK_ID --timeout 600
holycrab generate attempts list
holycrab assets upload /absolute/path/a.jpg /absolute/path/b.mp4 --duration-seconds 8
```

上述示例使用 macOS/Linux shell。`TASK_ID`、`ATTEMPT_ID`、`ASSET_ID`、`GROUP_ID` 需替换为对应查询返回的真实 ID；`/absolute/path/` 需替换为已选择文件的绝对路径。`[FILE ...]` 等方括号表示可选重复参数，不应逐字复制。

没有 `--yes` 时，创建命令会先显示积分估算并询问确认。脚本或 Agent 只有在用户已经明确确认后才能加 `--yes`。

Seedance 视频在没有填写 `generateAudio` 时，CLI 和本地 MCP 会默认补成 `true`；想要静音视频时显式传入 `"generateAudio": false`。MiniMax H3 不支持这个字段，因此不会自动添加。

同一提示词可以主动生成多次。每次明确确认都会创建新的本地 attempt ID 和新的线上任务；同一个 attempt ID 不能重复提交。断网、超时、HTTP 408/5xx、异常成功响应或缺少任务 ID 都记为 `unknown`，绝不自动再次 POST。可用 `holycrab generate attempts get ATTEMPT_ID` 查看本地记录，再查最近任务；查不到也不能证明线上没有创建。换电脑、删除本地记录或绕开 CLI 时，本地保护无法提供全局绝对防重。

## 真人授权和素材

终端中的慢操作会显示当前阶段；脚本调用不输出过程提示，JSON 和 MCP 输出保持纯净。输入 API Key 后会先显示正在验证，通过验证并保存后才提示账号已连接。需要继续操作时，结果中的 `nextAction` 给出指引；缺少文件、保存位置或具体请求时，`command` 为 `null`，由 Agent 询问用户，不拼接示例命令。

`Ctrl+C` 的退出码为 130，只停止本机操作，不会取消线上任务。提交或上传已经开始时，结果可能不明确：保留已知 ID 和批次清单，先查询，不能直接重复提交。Windows 卸载安排退出后的清理，不表示程序文件已同步删除。

```bash
holycrab real-human start --name "小林"
holycrab real-human wait AUTHORIZATION_ID --timeout 600
holycrab real-human groups list --page 1 --page-size 20
holycrab real-human groups rename GROUP_ID --name "新名称"
holycrab assets upload /absolute/path/a.jpg /absolute/path/b.mp4 --real-human-group GROUP_ID
holycrab assets wait ASSET_ID --timeout 600
holycrab real-human assets list --group GROUP_ID
```

发起后会返回临时授权链接和二维码 PNG 路径。二维码在本机生成，不需要额外安装 Python 包，也不会把链接交给第三方二维码服务。本人扫码或打开链接完成验证，并点击完成返回官方页面；Agent 查询到成功后，使用 `group.uniqId` 上传对应人物素材。

上传前 CLI 会显示人物名称、分组 ID 和最多 10 个文件的真实绝对路径、类型、大小及已知时长，只确认一次；任一文件本地校验失败时整批不上传，文件在确认后发生变化也会终止。真人素材只接受 JPG/JPEG、PNG、WEBP、GIF、HEIC、MP4、MOV；图片小于 30 MiB，视频不超过 50 MiB，音频拒绝。尺寸、比例、帧率、时长和编码仍由线上处理确认，本地通过不等于保证可用。

执行前会把整批已确认文件复制到临时文件，再从打开的临时文件流上传；复制前后核对大小、时间和内容哈希。临时磁盘空间需求等于整批文件大小，进程结束后关闭并清理。成功、失败和不明确的上传计划都保留为一次性记录：

```bash
holycrab assets uploads get PLAN_ID
```

返回已知素材 ID、失败项和未执行项。磁盘故障时若返回 `localStateSaved: false`，请保留终端中的 ID 并查询；不要把缺失的本地结果理解为线上未执行。成功的生成/上传在保存记录失败时仍返回已知 ID。MCP 的 `isError: true` 表示操作失败或结果不明确，不表示可以自动重试；成功查询一个失败任务仍是成功的查询。

素材只有在 `step: UPLOADED_TO_ARK`、`ready: true` 后才可用于生成。把公开素材 ID 放进现有 `imageAssetIds` / `videoAssetIds`，再估算积分、确认并提交一次。授权成功只允许向该人物分组上传，不代表用户确认了任何文件、不代表素材已经处理完，也不代表同意付费生成。

人物分组和真人素材也可以删除，但删除不可恢复。CLI 会先显示名称、素材数量等目标信息并询问；只有用户已经明确确认时才可使用 `--yes`。删除人物分组会连同组内素材和上游人物分组一起删除：

```bash
holycrab real-human assets delete ASSET_ID --group GROUP_ID
holycrab real-human groups delete GROUP_ID
```

MCP 提供 `real_human_authorization_start|get`、人物及素材列表、`real_human_group_rename|delete`、`real_human_asset_delete`、`asset_upload_prepare|execute`、`asset_upload_plan_get`、`asset_get`、`generation_attempt_list|get` 和 `cli_status`。上传仅使用两阶段接口，预览并确认后才能执行。删除工具必须传 `confirmed: true`，且 Agent 只能在用户明确要求删除该对象后这样做。发起授权工具同时返回二维码图片。链接和二维码含临时验证凭据，请只展示给本次操作的用户。所有授权与素材结果都带稳定 `nextAction`；失败、过期、超时或结果不明时不自动重试。

完整体验步骤见 [HolyCrab CLI 使用指南](HolyCrab%20CLI%20使用指南.md)。公开模型限制见 [capabilities.json](holycrab/references/capabilities.json)。

## 隐私、条款与支持

用户安装仅复制 `runtime-files.json` 列出的运行文件：CLI 入口、`holycrab_runtime` 模块、启动器、能力清单、Skill、Agent 配置以及二维码依赖。两个安装器从同一清单生成下载校验项。`tests/` 中的 Mock、fixture、烟测脚本及测试依赖，以及 `tools/` 开发验证脚本不会进入 CLI 或 Skill 安装目录。它们保留在 GitHub 源码和自动生成的源码压缩包中，不提供用户 Mock 模式。

HolyCrab CLI 不额外收集遥测数据；服务使用遵循 HolyCrab 隐私政策和服务条款。

- [Privacy Policy](https://holycrab.ai/privacy/)
- [Terms of Service](https://holycrab.ai/terms/)
- 普通使用问题：`cs@holycrab.ai`

## 本地开发验证

```bash
python3 tools/run_checks.py --report build/validation/report.json
```

独立二维码解码验证（开发环境专用，安装器不安装这些测试依赖）：

```bash
python3 -m venv /tmp/holycrab-qr-validator
/tmp/holycrab-qr-validator/bin/python -m pip install --only-binary=:all: -r tests/requirements-qr-test.txt
/tmp/holycrab-qr-validator/bin/python tools/validate_qr.py
```

默认自动化入口会隔离用户配置并禁止 Python 网络请求，不会调用正式生成接口或产生费用。报告区分通过、跳过、失败和未具备环境的项目；离线通过不等于正式发布获批。可选的网站示例、后端源码契约、独立二维码解码与旧版升级验收参数见 [自动化测试说明](docs/automated-testing.md)。历史 `live_release_smoke.py` 不在默认测试中，真实上传和付费生成需要明确的专用账号、素材及执行授权。实际使用 `holycrab generate create` 时，用户确认后会提交真实付费任务。安全问题请按 [SECURITY.md](SECURITY.md) 联系我们。

候选版架构和验证范围见 [v0.6.0 实施记录](docs/v0.6.0-implementation.md)。Release 只允许复用同版本、同提交、同字节的 Draft；公开发布须手动启用 `publish_release`，且仍受当前候选版本验收门禁约束。


## 素材校验与 Berry

能力快照描述模型规则；实际素材归属、格式、大小、数量、组合、时长、帧率和编码由后端统一判断。估算与提交返回的 `errors[]` 会在终端、JSON 和 MCP 中保留具体素材、字段、实际值及要求；`retryable` 不代表自动重试生成。

素材查询保留 `durationSeconds`、宽高、大小、格式、帧率、编码和 `metadataStatus`；未知值不补成 0。元数据缺失、处理中或探测失败不构成 CLI 的额外阻断。`ready` 仅表示现有处理状态，不保证满足任意模型条件。需要重新采集信息时显式运行 `holycrab assets metadata retry ASSET_ID`，或调用 MCP `asset_metadata_retry`；成功响应可能仍处于原状态或冷却期。

Berry 普通版及 Turbo 支持 480p / 720p / 1080p，Pro 及 Pro Turbo 支持 1080p / 2k / 4k，时长为 2–30 整数秒。默认时长 5 秒、1080p、adaptive、输出音频开启。使用 `audio` 控制音轨；不使用 Seedance 的 `generateAudio`。

```sh
holycrab models show berry-1.0
holycrab generate estimate --kind video --json '{"model":"berry-1.0","prompt":"A red crab on the beach"}'
holycrab generate estimate --kind image --json '{"model":"seedream-5-0-lite-260128","prompt":"A red crab","imageAssetIds":["ASSET_ID"]}'
holycrab generate estimate --kind audio --json '{"textPrompt":"@Audio1 speaks gently","references":[{"assetId":"ASSET_ID","type":"audio"}]}'
```

图片的 `imageAssetIds` 非空时优先于 `imageUrls`；音频使用素材 ID 或 `audio_url` / `image_url`。旧 Base64／speaker 引用请先显式上传，再改用素材 ID 或 URL；CLI 不隐式上传或转换。
