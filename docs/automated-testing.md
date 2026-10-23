# CLI 自动化验收

在 CLI 仓库根目录执行：

```sh
python3 tools/run_checks.py
```

Windows 使用 `python tools/run_checks.py`。需要 Python 3.10 或以上；基础检查只用标准库，不安装依赖，不调用真实账号、不上传、不生成、不发布。默认报告为 `build/validation/report.json`，每个阶段另有 `.log`，单测另有 `unit-results.json`。

## 一条命令覆盖什么

- 自动发现全部 `tests/test_*.py`，包括 CLI/MCP 协议、更新发现与提醒、严格参数校验、提交防重、上传恢复、凭据保护和命令示例。以后增加测试文件无需修改 CI 白名单。
- 使用临时 HOME、配置和安装目录；清除继承的 HolyCrab/Agent 配置变量。Python 子进程通过网络审计钩子拒绝未 mock 的连接和 DNS 查询；业务响应使用离线 fixture。
- Unix 的真实 shell 安装、升级失败回滚、卸载、用户文件保留和 PTY 输入输出由测试直接执行。不会操作开发者已安装的 CLI。
- 校验完整 runtime 清单、安装器哈希、公开能力快照，编译全部 runtime、tools 和 tests，检查 Unix shell 语法；Git checkout 中还检查补丁空白问题。
- Windows 自动发现同一套测试，明确记录 Unix 安装/卸载及 PTY 用例的跳过原因；Windows CI 另外运行原生 PowerShell 5.1 安装、DPAPI、中文路径、MCP、健康检查失败回滚和卸载。

CI 保留 Linux/macOS 与 Windows Server 2025 × Python 3.10/3.14，并增加 Windows Server 2022 x64/Python 3.10 与 Windows 11 ARM/原生 Python 3.14。Windows 基础测试与原生验收在同一作业内执行。一个平台的结果不能代替另一平台。

## 对抗性检查

```sh
python3 tools/run_checks.py --profile adversarial
```

该入口在标准检查后实际安装候选、启动本地 HTTP 故障服务，逐一执行 16 个模型与 22 个 MCP 工具；检查真实请求体、退出码、状态文件和请求次数。测试子进程只允许访问指定的本机服务，使用假凭据。另覆盖断连、拒绝、并发同一生成/上传、上传注册结果未知、活动操作拒绝清理状态、重定向、206 与截断下载。

随后在临时副本执行十种定向变异：重复提交、清理活动状态、复用上传计划、丢音频估算请求体、吞错误退出码、错误 MCP isError、接受截断下载、跳过安装器摘要、删除修改后的 Skill、接受变化的费用。每项先证明基线行为通过，再要求变异因行为断言失败。编译错误、夹具报错、超时、缺报告、跳过均不算成功杀死变异；变异后的安装哈希会重新生成，避免只因哈希不符而误报。

## 原生 Windows 验收

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test_windows_adversarial.ps1 -Report build/validation/windows-native.json
python tools/verify_windows_coverage.py --unit build/validation/unit-results.json --native build/validation/windows-native.json --report build/validation/windows-coverage.json
```

外层需要管理员权限创建临时普通账户、配置仅对该账户生效的出站隔离；实际验收必须证明普通用户 token。源仓库需同时包含固定 v0.5.0 annotated tag 对象 `52f697e35ad2689791423cb10eae5c8259e4c943` 和其提交 `48126baa08521961de47d48bd2134c44a7e8cba2`；验证对象类型及两者绑定后，仅从固定提交提取源码；不会用改版本号代替真实升级。隔离用户执行 PowerShell 5.1、PowerShell 7、CMD、DPAPI、特殊路径、实际文件占用、升级/回滚/卸载和 ConPTY 控制台流程。清理必须观察最终文件结果。

`tests/windows_native_cases.json` 列出全部 54 个必需用例；`tests/windows_skip_coverage.json` 为基线 60 项跳过逐项归类：26 项恢复在 Windows 运行，31 项须原生补位，2 项纯 Unix，1 项网站外部验收。门禁同时核对完整原生列表、基础报告与原生报告的源码指纹。新增跳过、缺项、失败、重复 ID、管理员 token 或旧报告均失败。

GitHub 官方提供的 `windows-11-arm` 为 ARM 桌面环境，不代表 Windows 10/11 x64 桌面已验证；见 [runner 环境说明](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。请求文件支持 UTF-8、UTF-8 BOM 和带 BOM 的 UTF-16；PowerShell 5.1 重定向默认 UTF-16LE、PowerShell 7 默认 UTF-8 的差异单独验证，见 [微软编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding)。不支持的 UNC/保留名/目录重定向安装位置须明确拒绝；长路径按实际系统能力验证，不宣称所有文件系统相同。

安装前另拒绝包含 `%` 或 `!` 的安装目录，并提示更换 `HOLYCRAB_INSTALL_PREFIX`：这是 CMD 启动器的展开限制，不是 Windows 文件系统禁止这些字符。中文、emoji、空格、括号和 `&` 的安装路径继续执行实际启动验证。请求文件名含 `%` 或 `!` 仍受支持：`win.argv.shells` 通过关闭延迟展开的 batch 调用、引用环境变量及 `@request` 文件，验证三种 Shell 实际发送完全相同的请求体且没有执行其中的命令文本。不能由调用者先展开参数后，再要求 CLI 恢复原文；参见 [CMD 非递归变量替换](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd)。

ConPTY 驱动在重定向日志的工作进程下显式设置 `STARTF_USESTDHANDLES` 与空标准句柄，让 Windows 填入控制台句柄；原生探针要求三个 `isatty()`、三个 `GetConsoleMode()` 及双流捕获全部通过。依据：[Microsoft Terminal 维护者说明](https://github.com/microsoft/terminal/issues/4380#issuecomment-580865346)、[GetStdHandle 的附加控制台行为](https://learn.microsoft.com/en-us/windows/console/getstdhandle#attachdetach-behavior)。

CMD 启动器始终原样返回 Python 的退出码，不再因 CLI 入口缺失而自删；卸载清理由专用助手负责。`win.shells.launchers` 复制实际生成的启动器，以独立测试入口验证自删入口后退出 37/130，以及入口已缺失时普通 `--version` 返回 2 且启动器保留；这些是启动器契约测试，不冒充完整卸载。`win.uninstall.user_files` 另实际安装后持有配置文件共享锁，从 CMD 发起 `uninstall --purge --yes`，要求程序删除后清理配置失败仍返回 1、配置字节保留，并观察延迟清理最终完成。

安装中断回归先给已安装的一个运行时文件追加无行为注释，再记录全部文件摘要。到真实 doctor 启动屏障时，必须证明候选已经覆盖该文件；中断后要求完整快照及原注释全部恢复，避免同一候选覆盖同一候选产生“没有回滚也通过”的假阳性。最后只还原夹具主动修改的文件，不重装掩盖其他失败。

Windows 10/11 x64 桌面、真实授权/上传/付费生成未包含在离线验收。最终结果以同一提交的 CI 报告为准；以上是检查定义，不是已经通过的声明。

### Windows 特殊字符请求文件

PowerShell 直接调用 `.cmd` 时也经过 CMD 的参数解析。若请求文件名含 `%` 或 `!`，在不含这两个字符的工作目录中保存以下 `holycrab-estimate.cmd`（ASCII/UTF-8 无 BOM 均可）：

```bat
@echo off
setlocal DisableDelayedExpansion
"%HC_LAUNCHER%" generate estimate --kind image --json "%HC_REQUEST%"
```

PowerShell 5.1/7 设置字面值后执行；它只估算，不提交生成：

```powershell
$env:HC_LAUNCHER = (Get-Command holycrab.cmd -CommandType Application).Source
$env:HC_REQUEST = '@C:\素材\图片 (x)&%PATH%!TOKEN!.json'
.\holycrab-estimate.cmd
```

原生验收用同一调用形式检查实际 HTTP 请求。将特殊文本保留在 JSON 文件内；不要把提示词拼成 Shell 命令，也不要使用会再次展开参数的 `call`。

## 按需加上的本地检查

只传已有本地目录/解释器，runner 不自动下载资源或依赖：

```sh
python3 tools/run_checks.py \
  --website-root /absolute/path/to/website \
  --generate-root /absolute/path/to/Generate-checkout \
  --generate-ref VERIFIED_MAIN_SHA \
  --baseline-source /absolute/path/to/previous-cli-checkout \
  --qr-python /absolute/path/to/qr-venv/bin/python \
  --skill-validator /absolute/path/to/quick_validate.py \
  --skill-python /absolute/path/to/validator-venv/bin/python \
  --report /absolute/path/to/results/report.json
```

| 参数 | 验证内容 |
| --- | --- |
| `--website-root` | 执行五语言共 50 页 CLI 指南中的命令示例，使用隔离响应 |
| `--generate-root` | 与指定 Generate 源码比较模型、枚举、限制、字段；有差异就失败，不把旧快照当当前契约 |
| `--baseline-source` | 实际安装给定旧版，执行候选升级、失败回滚和卸载；保留凭据 fixture、Agent Skill 和用户文件 |
| `--qr-python` | 用 ZXing 独立解码三个离线二维码 fixture，依赖版本见 `tests/requirements-qr-test.txt` |
| `--skill-validator` | 执行已有的官方 Skill validator；`--skill-python` 可指定含 PyYAML 的隔离解释器 |
| `--timeout` | 每阶段超时秒数，默认 600；超时终止子进程树并失败退出 |

未提供可选输入时，报告明确标为 `skipped`；提供了输入但路径、依赖、结果有问题时，必须失败。原生 PowerShell 验收在 Windows CI 执行，不能用 macOS 模拟结果代替。

## 如何阅读报告

- `passed`：该阶段实际执行并通过。
- `passed_with_skips`：执行部分通过，同时存在明确列出的跳过项；不等于所有项目通过。
- `failed`、`timeout`、`unavailable`：本轮退出码为 1；不得作为发布通过证据。
- `skipped`：没有执行，并列明原因。基础离线命令允许可选检查跳过，因此其退出码可以是 0；必须同时查看报告的 `status` 和 `checks`。

报告记录 Python、系统、源码与测试文件 SHA-256 指纹。检查期间源码变化会使本轮失败，避免把旧结果套用到新代码。报告本身不会批准生产发布，也不覆盖未实际执行的线上行为。

## 测试脚本自身的检查

`tests/test_automation_runner.py` 真正启动成功/失败/超时子进程、构造失败/跳过/空测试集，验证退出码和 JSON 报告；空测试集、全部跳过、expectedFailure 或父进程退出后仍有子进程运行都必须失败。还验证网络阻断、环境隔离、Windows 新测试自动纳入和源码、公共文档、验收记录与嵌套 fixture 的指纹变化。

`tests/test_native_install_checks.py` 使用丢文件、多文件、改字节、遗漏新模块等反例验证安装清单检查；直接执行修改版本后的 CLI，防止版本 fixture 只是替换了无效字符串；构造哈希正确但 doctor 失败的安装包，验证故障确实发生，并在 Unix 实测逐文件回滚。Windows 使用同一故障 fixture 做原生安装回滚。

Windows 旧版号 fixture 仅验证版本识别和重装分支，不冒充执行过对应历史发行包。真实历史版本升级使用 `--baseline-source`，并在报告里记录实际版本。

需要真人授权、真实提供商生成结果或生产部署后的链路，仍属于另一个验收层次；离线 fixture 不会被标成真实业务通过。现有受限 live smoke 和发布后 smoke 保留为显式触发工具，不由这个默认入口执行。


## 本地不可变 Generate 验证与 CI 证据

先通过 GitHub 或 `git ls-remote` 核对远端 main 的完整 SHA，再只读验证本地 Git 对象：

```sh
python3 tools/validate_generate_contract.py /absolute/path/to/Generate \
  --ref VERIFIED_MAIN_SHA \
  --expected-main-sha VERIFIED_MAIN_SHA \
  --write-evidence docs/release-acceptance/v0.6.0.json
```

`--ref` 不切换后端分支，不读取未提交源码；所有输入从该提交提取到临时目录并逐个记录 SHA-256。严格校验失败不更新证据。必须等运行时、测试、文档、安装器哈希和工作流均稳定后再生成证据。

CI 的 Recorded Generate contract (offline) 检查证据的通过状态、完整输入清单、后端提交、验证器哈希、候选源码/测试/安装器指纹和内容摘要；证据有效期为 7 天，不允许超过 5 分钟的未来时间。缺失、被修改或过期时失败。它验证的是被记录的不可变主干合同，不会实时读取私有仓库；后端 main 更新后需重新核对并生成证据。内容摘要用于识别意外改动，并非独立签名或对执行过程的远程证明。

契约证据不批准真人验收或正式发布。真实业务门禁保持独立，旧版本豁免不适用于 v0.6.0 改动后的核心。

素材规则另外绑定已审计的四个后端规则源文件与全部模型说明摘要；源码或说明任一变化都要求重新核对并显式更新审计基线，不能仅重签一份通过记录。此方式保守地拒绝未审计变化，不在 Python 中再实现一份后端素材判定。

Windows 测试从 Python 启动 PowerShell 时清除继承的 `PSModulePath`，让目标 PowerShell 重建自己的模块路径；安装器也显式从当前 `$PSHOME` 加载系统模块。原生回归保留 PS7 经 Python 启动 PS5.1 的实际继承环境，验证完整 SHA-256 检查及卸载后的最终清理。行为依据：[Microsoft about_PSModulePath](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-7.6)。

卸载助手使用当前 Python 的独立标准库进程，以 `-I -S` 隔离环境和 sitecustomize，不依赖额外 PowerShell 宿主。它只删除仍匹配调度时文件身份与哈希的启动器，并尝试移除空程序目录；新安装文件和用户文件均保留。清理有明确时限，失败记录日志，测试必须观察父进程退出后的最终文件结果。

PowerShell 安装中断覆盖 5.1/7 × 独立脚本、流式 IEX、外层脚本内 IEX 共六种调用。独立脚本在回滚完成后退出 130；IEX 必须保留交互宿主，随后从该宿主读取 LASTEXITCODE=130。每种场景都先证明文件已被覆盖，再核对完整恢复，不能只检查提示语。

## 正式发布后的安装与升级验证

已有工作流 `post-release-smoke.yml` 支持从公开正式包执行完整检查：

```sh
gh workflow run post-release-smoke.yml --repo AstroxNetwork/skills --ref main -F verify_website=true
```

Linux、macOS、Windows Server 2025 分别全新安装 v0.6.0，再实际安装不可变 v0.5.0 并通过 `holycrab update --yes` 升级。检查真实版本、完整运行文件哈希、22 个 MCP 工具、16 个模型、凭据与恢复记录字节、用户文件和实际卸载完成。旧版夹具先经过自身 doctor 校验，使用有效期和私有权限；不会修改旧版版本号或复用运行时代码计算预期值。Windows 旧版安装使用 PowerShell 5.1 原生模块路径，避免 CI 的 PowerShell 7 路径污染；这是[微软记录的跨版本继承行为](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_psmodulepath?view=powershell-5.1)。

官网已部署后，额外在 macOS、Windows 从 `holycrab.ai/cli/` 下载，先比对正式 Release 摘要再安装。若官网尚待部署授权，可显式设 `verify_website=false` 仅验证公开 GitHub 包；官网步骤会显示跳过，不能宣称官网验收通过。

本机执行入口为 `python3 tests/post_release_upgrade.py --source . --report build/post-release/public-upgrade.json`。必须联网访问公开 GitHub 资产，但在隔离 HOME、配置和安装目录运行，不读取真实账号、不调用业务 API、不付费生成。结果和逐命令日志保存在指定目录，失败保留原始记录。脚本只适用于当前 v0.6.0 为 Latest 且本地发布文件与其完全一致时；后续版本须显式更新验收目标。
