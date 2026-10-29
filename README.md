# deepseek-search skill

把 DeepSeek 官方 **Search / Fetch** 接口封装成一个通用「联网搜索」skill，用法与能力对齐 Claude Code 的 `WebSearch` / `WebFetch`、Codex 的 `web_search`。

- 文档：[Search & Fetch 指南](https://api-docs.deepseek.com/zh-cn/guides/search) · [Search API](https://api-docs.deepseek.com/zh-cn/api/search) · [Fetch API](https://api-docs.deepseek.com/zh-cn/api/fetch)

## 结构

```
.
├── SKILL.md              # 触发描述 + 工作流 + query 规则 + 错误/成本/安全
├── scripts/ds_web.py     # 仅标准库的 CLI：search / fetch / config
├── references/api.md     # 完整字段、搜索语法、错误码、计费、限速
└── config.json           # 本地凭证（config --init 生成，已 gitignore）
```

## 安装

已软链到 `~/.agents/skills/deepseek-search`，pi / Claude Code 会自动发现：

```bash
ln -sfn "$PWD" ~/.agents/skills/deepseek-search   # 已执行
```

换机器时重新执行上面这条即可。仓库保持为唯一源码副本，修改后 `git commit` 即生效。

## 配置

配置只走**配置文件**，不用环境变量。文件就放在 skill 目录下：`config.json`

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.deepseek.com"
}
```

生成模板（已存在则不覆盖，自动 `chmod 600`）：

```bash
python3 scripts/ds_web.py config --init
```

查看当前配置（key 脱敏，并提示文件权限问题）：

```bash
python3 scripts/ds_web.py config
```

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `api_key` | DeepSeek API Key：[申请](https://platform.deepseek.com/api_keys) | 无（必填） |
| `base_url` | 接口域名，走网关/代理时改；可带或不带末尾 `/`、`/v1`，脚本会自动规范 | `https://api.deepseek.com` |

补充说明：

- 配置文件路径固定跟随脚本位置（`scripts/` 的上一级），整个目录拷到哪就跟到哪，不依赖 `$HOME`。
- 优先级：`--api-key` / `--base-url` 命令行参数 > 配置文件 > 默认值。
- 换路径：`--config /path/to/config.json`（可放在子命令前或后）。
- 权限建议保持 600：`chmod 600 config.json`。
- `config.json` 已在 `.gitignore` 中，不会误提交；不要把 Key 贴进对话或日志。

## 直接试用

```bash
python3 scripts/ds_web.py search "DeepSeek V4 发布时间" --count 8
python3 scripts/ds_web.py fetch https://api-docs.deepseek.com/zh-cn/api/search
```

无 `python3` 时 SKILL.md 内提供等价的 curl 命令。
