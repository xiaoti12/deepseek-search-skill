# DeepSeek Search / Fetch API 参考

Base URL：`https://api.deepseek.com`

| API | Endpoint |
| --- | --- |
| Search | `POST /v1/tools/search` |
| Fetch | `POST /v1/tools/fetch` |

请求头：

| Header | 必填 | 值 |
| --- | --- | --- |
| `Authorization` | 是 | `Bearer <DeepSeek API Key>` |
| `Content-Type` | 是 | `application/json` |

## Search 请求

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `query` | string | 是 | - | 1～128 个 Unicode 字符，不能全为空白，支持搜索语法 |
| `count` | integer | 否 | `10` | 返回条数 1～50 |
| `ori_question` | string \| null | 否 | - | 触发搜索的用户原始问题，1～128 字符，帮助理解意图 |
| `localization` | string | 否 | `""` | 用于优先返回本地化结果的 IP 地址 |
| `need_content` | boolean | 否 | `false` | 是否在 `content` 返回网页正文；`false` 时恒为空字符串 |

未知字段会被忽略。

## Search 搜索语法（原样透传，可组合）

| 操作符 | 示例 | 说明 |
| --- | --- | --- |
| `site:` | `DeepSeek site:github.com` | 限定站点；多个用空格分隔并以 `OR` 连接：`site:a.com OR site:b.com` |
| `-site:` | `DeepSeek -site:example.com` | 排除站点，多个用空格分隔 |
| `-` | `DeepSeek -广告` | 排除包含该关键词的网页 |
| `-"..."` | `DeepSeek -"release notes"` | 排除包含该短语的网页 |
| `"..."` | `DeepSeek "精确短语"` | 精确匹配短语 |
| `lang:` | `DeepSeek lang:zh,en` | 限定语言，逗号分隔 |
| `after:` | `after:2025` / `after:2025-01-01` | 发布时间晚于 |
| `before:` | `before:2026` / `before:2026-06-01` | 发布时间早于 |

组合示例：`"DeepSeek 模型 site:github.com OR site:huggingface.co lang:zh after:2025"`

## Search 响应

```json
{
  "results": [
    {
      "rerank_score": 2.4,
      "title": "DeepSeek V4 模型发布",
      "url": "https://example.com/deepseek-v4",
      "snippet": "短摘要 100~200 字符",
      "summary": "长摘要 600~800 字符",
      "content": "网页正文（need_content=true 时才有）",
      "site_name": "Example News",
      "site_icon": "https://example.com/favicon.ico",
      "date": "2026-06-01T00:00:00+00:00",
      "authority": 3
    }
  ]
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `rerank_score` | number | 0～3，综合相关性/权威性/时效性；**≥1.5 为优质结果** |
| `title` | string | 标题 |
| `url` | string | 链接 |
| `snippet` | string | 短摘要，适合前端展示 |
| `summary` | string | 长摘要 600～800 字符，**适合给模型阅读**（优先用它做初筛） |
| `content` | string | 正文；`need_content=false` 或正文缺失时为空 |
| `site_name` | string \| null | 站点名 |
| `site_icon` | string \| null | 站点图标 |
| `date` | string \| null | UTC RFC 3339 发布时间，未知为 `null` |
| `authority` | integer | 0～4，`-1` 未知；**≥3 为极权威** |

搜索正常结束但无匹配时返回空数组 `{"results": []}`。

## Fetch 请求

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `url` | string | 是 | ≤8192 字符，必须是含 host 的 HTTP/HTTPS URL，且不能带用户名/密码 |

## Fetch 响应

```json
{ "title": "Example article", "content": "# Example article\n\nArticle content." }
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 页面标题，缺失时为空字符串 |
| `content` | string | Markdown 正文 |

Fetch 结果受目标站点可用性与访问策略影响。

## 错误

OpenAI 兼容格式，`param` 恒为 `null`：

```json
{ "error": { "message": "'count' must be between 1 and 50", "type": "invalid_request_error", "param": null, "code": "invalid_request_error" } }
```

| HTTP | 含义 | 是否重试 |
| --- | --- | --- |
| 400 | 请求头/请求体不合法、字段值无效 | 否，改参数 |
| 401 | API Key 缺失或无效 | 否，检查 Key |
| 402 | 余额不足 | 否，充值 |
| 403 | Fetch 被安全策略拒绝 | 否，换来源 |
| 404 | Fetch 未取到可用正文（URL 不可用/被拦/类型不支持/无可提取正文） | 否，换来源 |
| 408 | 读取请求体超时 | 是 |
| 413 | 请求体过大 | 否，缩短 URL/参数 |
| 429 | Search 或 Fetch 触发 QPS 限制 | 是，退避后重试 |
| 500 | 服务器内部错误 | 是 |
| 504 | 下游超时 | 是 |

## 计费

按**请求次数**计费，与 token、`count` 无关：

| API | 单价 |
| --- | --- |
| Search | 5 元 / 1,000 次成功请求 |
| Fetch | 5 元 / 1,000 次成功请求 |

只有返回 HTTP 200 且产生结果才计费（Search 的 `results` 非空，Fetch 返回正文）；失败、无结果、超时不收费。

## 限速

| API | 账号级 QPS |
| --- | --- |
| Search | 100 |
| Fetch | 25 |

两者独立计算，与 API Key 无关（按账号）。Fetch 另有**目标域名级** QPS 限制，超限同样返回 429。

## 原始请求示例

```bash
# Search（<DEEPSEEK_API_KEY> 取自 skill 目录下 config.json 的 api_key）
curl -sS https://api.deepseek.com/v1/tools/search \
  -H "Authorization: Bearer <DEEPSEEK_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query":"DeepSeek V4 模型 发布","count":5,"need_content":true}'

# Fetch
curl -sS https://api.deepseek.com/v1/tools/fetch \
  -H "Authorization: Bearer <DEEPSEEK_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/article"}'
```

```python
import requests

response = requests.post(
    "https://api.deepseek.com/v1/tools/search",
    headers={"Authorization": "Bearer <DeepSeek API Key>"},
    json={"query": "DeepSeek V4 模型 发布", "count": 5, "need_content": True},
    timeout=60,
)
response.raise_for_status()
for result in response.json()["results"]:
    print(result["title"], result["url"])
    print(result["summary"])
```
