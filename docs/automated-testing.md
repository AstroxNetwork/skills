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

CI 保留 Linux/macOS/Windows × Python 3.10/3.14 的六组基础检查，以及三个平台的原生安装检查。一个平台的结果不能代替另一平台。

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

`tests/test_automation_runner.py` 真正启动成功/失败/超时子进程、构造失败/跳过/空测试集，验证退出码和 JSON 报告；空测试集或全部跳过都必须失败。还验证网络阻断、环境隔离、Windows 新测试自动纳入和源码、公共文档、验收记录与嵌套 fixture 的指纹变化。

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
