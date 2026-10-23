#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 网页搜索 / 抓取 CLI（仅依赖 Python 标准库）。

  Search: POST https://api.deepseek.com/v1/tools/search
  Fetch : POST https://api.deepseek.com/v1/tools/fetch

示例:
  python3 ds_web.py search "DeepSeek V4 发布时间" --count 8
  python3 ds_web.py search "vllm changelog" --question "vllm 最新版本改了什么" --content
  python3 ds_web.py search "grpc 超时配置 site:github.com lang:en after:2025"
  python3 ds_web.py fetch https://example.com/a https://example.com/b
  python3 ds_web.py fetch https://example.com/big --find "timeout"

鉴权与配置: 读取 skill 目录下的 config.json（用 `ds_web.py config --init` 生成模板），
也可用 --api-key / --base-url 临时覆盖。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"
DEFAULT_BASE = "https://api.deepseek.com"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(SKILL_DIR, "config.json")
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
USER_AGENT = "deepseek-search-skill/1.0"
KEY_PLACEHOLDER = "sk-替换为你的-DEEPSEEK-API-KEY"


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        self.status = status
        self.message = message
        self.code = code
        super().__init__(f"HTTP {status}: {message}" if status else message)


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _parse_error(raw: str) -> tuple[str, str | None]:
    try:
        err = (json.loads(raw) or {}).get("error") or {}
        return str(err.get("message") or raw.strip()[:300]), err.get("code")
    except Exception:
        return (raw.strip()[:300] or "未知错误"), None


def _retry_after(headers, fallback: float) -> float:
    value = headers.get("Retry-After") if headers else None
    try:
        return max(float(value), 1.0)  # 秒
    except (TypeError, ValueError):
        return fallback


def normalize_base(base: str) -> str:
    """容错：允许传入带末尾 / 或 /v1 的 base url。"""
    base = (base or DEFAULT_BASE).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base or DEFAULT_BASE


# ---------------------------------------------------------------- 配置

def config_path(raw: str | None = None) -> str:
    return os.path.expanduser(raw or DEFAULT_CONFIG)

def mask_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "(空)"
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ApiError(0, f"配置文件不是合法 JSON（{path}）：{exc}") from None
    except OSError as exc:
        raise ApiError(0, f"读取配置文件失败（{path}）：{exc}") from None
    if not isinstance(data, dict):
        raise ApiError(0, f"配置文件顶层必须是 JSON 对象（{path}）")
    return data


def resolve_auth(args) -> tuple[str, str]:
    """优先级：命令行参数 > 配置文件 > 默认值。"""
    config = load_config(config_path(args.config))
    api_key = (args.api_key or config.get("api_key") or "")
    base_url = args.base_url or config.get("base_url") or DEFAULT_BASE
    return str(api_key).strip(), normalize_base(str(base_url))


def cmd_config(args) -> int:
    path = config_path(args.config)
    if args.init:
        if os.path.exists(path):
            print(f"配置文件已存在：{path}\n直接编辑它，或用 --config 指定其它路径。")
            return 0
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"api_key": KEY_PLACEHOLDER, "base_url": DEFAULT_BASE},
                      handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(path, 0o600)
        print(f"已创建配置模板：{path}\n"
              f"请把 api_key 换成真实 Key（申请：https://platform.deepseek.com/api_keys）。")
        return 0

    if not os.path.exists(path):
        print(f"配置文件不存在：{path}\n运行 `config --init` 生成模板。")
        return 0

    config = load_config(path)
    api_key = str(config.get("api_key") or "").strip()
    base_url = normalize_base(str(config.get("base_url") or DEFAULT_BASE))
    print(f"配置文件: {path}")
    print(f"api_key : {mask_key(api_key)}")
    print(f"base_url: {base_url}")
    if api_key in ("", KEY_PLACEHOLDER):
        print("\n⚠️  api_key 还没填，请编辑配置文件后重试。")
    if os.stat(path).st_mode & 0o077:
        print(f"\n⚠️  文件权限过于开放，建议执行：chmod 600 {path}")
    return 0


def http_post(base: str, path: str, payload: dict, api_key: str,
              timeout: float, retries: int) -> dict:
    url = normalize_base(base) + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    delay = 1.0

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            message, code = _parse_error(exc.read().decode("utf-8", "replace"))
            if exc.code not in RETRY_STATUS or attempt >= retries:
                raise ApiError(exc.code, message, code) from None
            wait = _retry_after(exc.headers, delay)
            log(f"[retry] HTTP {exc.code} {message} → {wait:.0f}s 后重试 ({attempt}/{retries})")
            time.sleep(wait)
            delay = min(delay * 2, 16.0)
            continue
        except json.JSONDecodeError:
            raise ApiError(0, "响应不是合法 JSON") from None
        except (urllib.error.URLError, OSError) as exc:
            if attempt >= retries:
                raise ApiError(0, f"网络错误: {exc}") from None
            log(f"[retry] 网络错误 {exc} → {delay:.0f}s 后重试 ({attempt}/{retries})")
            time.sleep(delay)
            delay = min(delay * 2, 16.0)
            continue

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(0, "响应不是合法 JSON") from None

    raise ApiError(0, "重试次数用尽")


# ---------------------------------------------------------------- 输出工具

def one_line(text) -> str:
    return " ".join(str(text or "").split())


def short_date(value) -> str | None:
    if not value:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text


def ensure_save_dir(given: str | None) -> str:
    if given:
        os.makedirs(given, exist_ok=True)
        return given
    return tempfile.mkdtemp(prefix="ds_web_")


def safe_name(url: str, index: int | None = None) -> str:
    host = urllib.parse.urlparse(url).netloc or "page"
    host = re.sub(r"[^A-Za-z0-9.-]+", "_", host)[:40]
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    prefix = f"r{index}_" if index is not None else ""
    return f"{prefix}{host}_{digest}.md"


def save_text(text: str, name: str, save_dir: str) -> str:
    path = os.path.join(save_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def truncate_note(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


# ---------------------------------------------------------------- search

def build_search_payload(args, query: str) -> dict:
    payload = {"query": query, "count": args.count, "need_content": bool(args.content)}
    if args.question:
        payload["ori_question"] = args.question
    if args.localization:
        payload["localization"] = args.localization
    return payload


def render_search(payload: dict, query: str, args, save_dir: str) -> int:
    results = payload.get("results") or []
    if not results:
        print(f"## DeepSeek Search · {query}\n\n无结果。换更通用的关键词，或去掉过窄的 site:/lang:/after: 限定后重试。")
        return 0

    kept = []
    for item in results:
        score = item.get("rerank_score") or 0
        authority = item.get("authority")
        if score < args.min_score:
            continue
        if isinstance(authority, int) and 0 <= authority < args.min_authority:
            continue
        kept.append(item)

    print(f"## DeepSeek Search · {query}")
    print(f"共 {len(results)} 条，过滤后 {len(kept)} 条"
          f"（min_score={args.min_score}, min_authority={args.min_authority}）\n")

    for index, item in enumerate(kept, 1):
        meta = []
        if item.get("site_name"):
            meta.append(f"site={item['site_name']}")
        if short_date(item.get("date")):
            meta.append(f"date={short_date(item.get('date'))}")
        if item.get("rerank_score") is not None:
            meta.append(f"score={item['rerank_score']}")
        if item.get("authority") is not None:
            meta.append(f"authority={item['authority']}")

        print(f"[{index}] {one_line(item.get('title')) or '(无标题)'}")
        print(f"URL: {item.get('url', '')}")
        if meta:
            print("Meta: " + " | ".join(meta))
        summary = one_line(item.get("summary")) or one_line(item.get("snippet"))
        if summary:
            print(f"Summary: {summary}")

        content = item.get("content") or ""
        if args.content and content:
            url = item.get("url", "")
            path = save_text(content, safe_name(url, index), save_dir)
            head, clipped = truncate_note(content, args.content_chars)
            print(f"Content: {path} ({len(content)} 字符"
                  + (f"，以下为前 {args.content_chars} 字符" if clipped else "") + ")")
            print(head)
        elif args.content:
            print("Content: (接口未返回正文)")
        print()

    return 0


def cmd_search(args) -> int:
    query = " ".join(args.query).strip()
    if not query:
        raise ApiError(0, "query 不能为空")
    if len(query) > 128:
        raise ApiError(0, f"query 不能超过 128 个字符（当前 {len(query)}），请缩短关键词")
    if not (1 <= args.count <= 50):
        raise ApiError(0, "count 需在 1~50 之间")

    payload = build_search_payload(args, query)
    data = http_post(args.base_url, SEARCH_PATH, payload, args.api_key,
                     args.timeout, args.retries)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    return render_search(data, query, args, ensure_save_dir(args.save_dir))


# ---------------------------------------------------------------- fetch

def fetch_one(args, url: str) -> dict:
    data = http_post(args.base_url, FETCH_PATH, {"url": url}, args.api_key,
                     args.timeout, args.retries)
    data["_url"] = url
    return data


def find_context(text: str, needle: str, before: int = 3, after: int = 8,
                 limit: int = 5, line_cap: int = 400) -> tuple[str, int]:
    lines = text.splitlines()
    lowered = needle.lower()
    hits = [i for i, line in enumerate(lines) if lowered in line.lower()]
    if not hits:
        return "", 0
    chunks = []
    for hit in hits[:limit]:
        start = max(0, hit - before)
        end = min(len(lines), hit + after + 1)
        window = [line if len(line) <= line_cap else line[:line_cap] + " …"
                  for line in lines[start:end]]
        chunks.append("\n".join(window))
    return "\n\n---\n\n".join(chunks), len(hits)


def render_fetch(results: list[dict], args, save_dir: str) -> int:
    for index, data in enumerate(results, 1):
        url = data.get("_url", "")
        title = one_line(data.get("title")) or "(无标题)"
        content = data.get("content") or ""
        print(f"[{index}] {title}")
        print(f"URL: {url}")
        print(f"Length: {len(content)} 字符")
        if not content:
            print("(无正文)\n")
            continue

        path = save_text(content, safe_name(url), save_dir)
        print(f"File: {path}")

        if args.find:
            snippet, hits = find_context(content, args.find)
            if not hits:
                head, _ = truncate_note(content, min(args.max_chars, 600))
                print(f"未找到 '{args.find}'，以下为开头片段：\n{head}\n")
                continue
            print(f"匹配 {hits} 处（最多展示 5 处上下文）：\n")
            print(snippet)
            print()
            continue

        body, clipped = truncate_note(content, args.max_chars)
        print("---")
        print(body)
        if clipped:
            print(f"\n... (已截断，共 {len(content)} 字符；全文见 {path}，"
                  f"或用 --find '<关键词>' 定位)")
        print()

    return 0


def cmd_fetch(args) -> int:
    urls = args.urls
    if not urls:
        raise ApiError(0, "至少需要一个 URL")
    save_dir = ensure_save_dir(args.save_dir)
    workers = max(1, min(args.workers, len(urls)))

    results: list[dict] = []
    if workers == 1:
        for url in urls:
            results.append(fetch_one(args, url))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_one, args, url) for url in urls]
            for future in futures:
                try:
                    results.append(future.result())
                except ApiError as exc:
                    log(f"[warn] {exc}")

    if not results:
        raise ApiError(0, "全部 URL 抓取失败")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    return render_fetch(results, args, save_dir)


# ---------------------------------------------------------------- CLI

def _add_global_flags(parser: argparse.ArgumentParser, sub: bool = False) -> None:
    """全局参数也允许写在子命令之后（子 parser 用 SUPPRESS，不覆盖主 parser 的值）。"""

    def kw(default):
        return {"default": argparse.SUPPRESS} if sub else {"default": default}

    parser.add_argument("--config", metavar="PATH", **kw(DEFAULT_CONFIG),
                        help=f"配置文件路径，默认 {DEFAULT_CONFIG}")
    parser.add_argument("--api-key", **kw(None), help="临时覆盖配置文件里的 api_key")
    parser.add_argument("--base-url", **kw(None),
                        help=f"临时覆盖配置文件里的 base_url（默认 {DEFAULT_BASE}）")
    parser.add_argument("--timeout", type=float, **kw(60.0), help="单次请求超时秒数，默认 60")
    parser.add_argument("--retries", type=int, **kw(3), help="失败重试次数，默认 3")
    parser.add_argument("--save-dir", **kw(None), help="正文落盘目录，默认临时目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ds_web.py",
        description="DeepSeek 网页搜索 / 抓取 CLI（Search + Fetch）",
        epilog="配置：运行 `ds_web.py config --init` 生成配置文件模板。",
    )
    _add_global_flags(parser)

    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="搜索网页")
    _add_global_flags(search, sub=True)
    search.add_argument("query", nargs="+", help="搜索词（支持 site:/lang:/after:/\"短语\" 等语法）")
    search.add_argument("-c", "--count", type=int, default=10, help="返回条数 1~50，默认 10")
    search.add_argument("--content", action="store_true", help="请求并输出网页正文")
    search.add_argument("--content-chars", type=int, default=1500, help="每条正文内联展示上限，默认 1500")
    search.add_argument("-q", "--question", help="用户原始问题，帮助理解搜索意图（<=128 字符）")
    search.add_argument("--localization", default="", help="用于本地化结果的 IP 地址")
    search.add_argument("--min-score", type=float, default=0.0, help="过滤 rerank_score 下限（1.5 为优质）")
    search.add_argument("--min-authority", type=int, default=-1, help="过滤 authority 下限（3 为极权威，-1 不过滤）")
    search.add_argument("--json", action="store_true", help="输出原始 JSON")
    search.set_defaults(func=cmd_search)

    fetch = sub.add_parser("fetch", help="抓取网页正文（Markdown）")
    _add_global_flags(fetch, sub=True)
    fetch.add_argument("urls", nargs="+", help="一个或多个 http/https URL")
    fetch.add_argument("--max-chars", type=int, default=4000, help="内联展示上限，默认 4000")
    fetch.add_argument("--find", help="只展示命中关键字的上下文片段")
    fetch.add_argument("--workers", type=int, default=4, help="并发抓取数，默认 4")
    fetch.add_argument("--json", action="store_true", help="输出原始 JSON")
    fetch.set_defaults(func=cmd_fetch)

    config = sub.add_parser("config", help="查看 / 初始化配置文件")
    _add_global_flags(config, sub=True)
    config.add_argument("--init", action="store_true", help="生成配置模板（已存在则不覆盖）")
    config.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "config":
        try:
            return cmd_config(args)
        except ApiError as exc:
            log(f"错误：{exc}")
            return 1

    try:
        args.api_key, args.base_url = resolve_auth(args)
    except ApiError as exc:
        log(f"错误：{exc}")
        return 2

    if not args.api_key or args.api_key == KEY_PLACEHOLDER:
        log("错误：未配置 API Key。")
        log(f"  配置文件：{config_path(args.config)}")
        log("  初始化：  python3 <skill>/scripts/ds_web.py config --init")
        log("  Key 申请：https://platform.deepseek.com/api_keys")
        return 2

    try:
        return args.func(args)
    except ApiError as exc:
        log(f"错误：{exc}")
        if exc.status in (408, 429, 500, 502, 503, 504):
            log("提示：限流/服务端错误，稍后重试。")
        elif exc.status == 401:
            log("提示：API Key 无效或缺失。")
        elif exc.status == 402:
            log("提示：账号余额不足，需充值后重试。")
        elif exc.status == 403:
            log("提示：Fetch 被目标站点的安全策略拒绝，换其它来源。")
        elif exc.status == 404:
            log("提示：无法获取可用正文（URL 失效/被拦截/无 Markdown 正文），不要重试同一 URL。")
        elif exc.status in (400, 413):
            log("提示：请求参数不合法，修正后重试；原样重试无效。")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:  # 配合 `| head` 使用时不报错
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
