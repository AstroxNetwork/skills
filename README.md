# HolyCrab Agent Tools

这个仓库提供一套运行在用户电脑上的 HolyCrab 工具：

- `holycrab` CLI：配置 API Key、查模型、估积分、上传本地素材、创建和查询任务。
- 本地 MCP：由同一个命令通过 `holycrab mcp serve` 启动，供 Codex、Claude Code 等 Agent 调用。
- 薄 Skill：教 Agent 先查能力、先估积分、获得确认后只提交一次。

它们共用同一份公开能力清单和同一套安全请求代码，直接调用 HolyCrab 现有正式 API，不需要新增 OAuth 或远程 MCP 后端。

## 一键安装

macOS / Linux：

下面的固定版本命令只在 `v0.2.1` 正式发布后可用；GitHub Draft Release 不算公开发布。内部测试请先克隆仓库，再使用后面的本地源码安装方式。

```bash
curl -fsSL https://raw.githubusercontent.com/AstroxNetwork/skills/v0.2.1/install.sh | sh
```

安装器会把命令放到 `~/.local/bin/holycrab`，安装 Codex 与 Claude Code 的 Skill，并在检测到对应客户端时登记本地 MCP。它不会写入 API Key。

如果终端还找不到命令，把下面一行加入 shell 配置：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 配置 Key 和自检

先在 [HolyCrab API Key 页面](https://generate.holycrab.ai/user-tokens) 创建或复制 Key，然后运行：

```bash
holycrab setup
holycrab doctor --json
holycrab auth status
```

`holycrab setup` 会隐藏输入、验证并保存 Key；`holycrab auth set-key` 提供相同的显式配置入口。API Key 保存在本机 `~/.config/holycrab/config.json`，文件权限为 `0600`。临时环境变量 `HOLYCRAB_API_KEY` 会覆盖本地配置；如果看到覆盖警告，请先运行 `unset HOLYCRAB_API_KEY`，再检查 Key 状态。

## 常用命令

```bash
holycrab models list
holycrab models show dreamina-seedance-2-5-260628
holycrab credits balance

holycrab generate estimate --kind image \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2k"}'

holycrab generate create --kind image \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2k"}'

holycrab tasks list
holycrab tasks get TASK_ID
holycrab tasks wait TASK_ID --timeout 600
holycrab assets upload /absolute/path/reference.mp4 --duration-seconds 8
```

没有 `--yes` 时，创建命令会先显示积分估算并询问确认。脚本或 Agent 只有在用户已经明确确认后才能加 `--yes`。

Seedance 视频在没有填写 `generateAudio` 时，CLI 和本地 MCP 会默认补成 `true`；想要静音视频时显式传入 `"generateAudio": false`。MiniMax H3 不支持这个字段，因此不会自动添加。

同一提示词可以主动生成多次。每次明确确认都会创建新的本地 attempt ID 和新的线上任务；同一个 attempt ID 不能重复提交，网络结果不明确时也不会自动重试付费请求。

完整体验步骤见 [HolyCrab CLI 使用指南](HolyCrab%20CLI%20使用指南.md)。公开模型限制见 [capabilities.json](holycrab/references/capabilities.json)。

## 本地开发验证

```bash
python3 -m unittest discover -s holycrab/tests -v
python3 -m py_compile holycrab/scripts/holycrab_api.py holycrab/scripts/holycrab_cli.py
python3 holycrab/scripts/validate_capabilities.py
sh -n install.sh bin/holycrab
```

仓库中的测试和开发验证不会调用正式生成接口或产生费用；实际使用 `holycrab generate create` 时，用户确认后会提交真实付费任务。安全问题请按 [SECURITY.md](SECURITY.md) 联系我们。
