#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import sys
import time
import zipfile
from pathlib import Path
import shutil
from typing import Dict, Iterable, List, Tuple

import requests
import yaml

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT = 180
DEFAULT_SIZE = "1024x1024"
DEFAULT_OUTPUT_DIRNAME = "generated-images"
PROFILE_DEFAULT = "none"
TRANSPORT_AUTO = "auto"
TRANSPORT_IMAGES = "images"
TRANSPORT_RESPONSES = "responses"
TRANSPORT_CHAT = "chat"
SIZE_SQUARE = "1024x1024"
SIZE_PORTRAIT = "1024x1536"
SIZE_LANDSCAPE = "1536x1024"
SIZE_PHONE_WALLPAPER = "1024x1792"
SIZE_WIDE_BANNER = "1792x1024"
IMAGE_ONLY_MODEL_PREFIXES = ("gpt-image-",)

PROFILE_PRESETS = {
    "none": {
        "description": "Fully compatible with the current behavior; no strategy defaults applied.",
    },
    "official-like": {
        "description": "Stabler composition and polished output without changing model/backend defaults.",
        "quality": "high",
        "prompt_suffix": "clean composition, polished final-image quality, clear focal subject, coherent lighting, well-balanced framing",
    },
    "fast": {
        "description": "Faster, lighter default behavior with minimal augmentation.",
        "prompt_suffix": "clear subject, simple composition",
    },
    "anime-poster": {
        "description": "Poster-oriented anime / 国漫 style defaults.",
        "quality": "high",
        "size": SIZE_PORTRAIT,
        "prompt_suffix": "anime poster style, dynamic composition, strong focal subject, cinematic lighting, detailed costume design, clean character faces, no text, no watermark, no extra limbs",
    },
    "photo-real": {
        "description": "Photorealistic style defaults with realistic lighting and anatomy.",
        "quality": "high",
        "prompt_suffix": "photorealistic, realistic lighting, natural materials, natural proportions, clean facial details, natural hands, no text, no watermark",
    },
    "social-cover": {
        "description": "Social cover / 小红书封面 / 营销首图 defaults with legible composition and whitespace.",
        "quality": "high",
        "size": SIZE_PORTRAIT,
        "prompt_suffix": "social cover composition, strong focal subject, clean layout, leave usable whitespace for title overlay, high visual contrast, no text, no watermark",
    },
}


def _skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_external_profiles() -> Dict[str, dict]:
    path = _skill_root() / "templates" / "profiles.yaml"
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    raw = payload.get("profiles")
    return raw if isinstance(raw, dict) else {}


EXTERNAL_PROFILE_PRESETS = _load_external_profiles()
PROFILE_PRESETS = {**PROFILE_PRESETS, **EXTERNAL_PROFILE_PRESETS}

PROMPT_TEMPLATE_DEFAULTS = {
    "localized_fix_suffix": "only change the targeted local region, preserve layout, colors, borders, icons, typography alignment, and all unrelated regions",
    "preserve_unrelated_suffix": "preserve unrelated regions when editing",
    "variant_suffix": "create an alternate variation with similar style and overall intent, but different framing or secondary details",
}


def _load_external_prompt_templates() -> Dict[str, str]:
    path = _skill_root() / "templates" / "prompt-templates.yaml"
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    prompts = payload.get("prompts")
    if not isinstance(prompts, dict):
        return {}
    out: Dict[str, str] = {}
    for key in PROMPT_TEMPLATE_DEFAULTS:
        value = prompts.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


PROMPT_TEMPLATES = {**PROMPT_TEMPLATE_DEFAULTS, **_load_external_prompt_templates()}


def _hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "~/.hermes")
    return Path(raw).expanduser()


def _load_simple_env(env_path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        data[key] = value
    return data


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _first_nonempty(*values: str | None) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        else:
            text = str(value).strip()
            if text:
                return text
    return ""


def _normalize_profile(raw: str | None) -> str:
    value = (raw or PROFILE_DEFAULT).strip().lower()
    return value if value in PROFILE_PRESETS else PROFILE_DEFAULT


def _infer_mode_from_prompt(prompt: str | None, has_input: bool) -> str:
    if not has_input:
        return "generate"

    text = (prompt or "").strip().lower()
    if not text:
        return "edit"

    if re.search(
        r"(只改|局部|修字|改字|别的不要动|只修改|保留其余|保持其他不变|keep everything else unchanged|only change|"
        r"edit\b|retouch|修复|去掉|移除|删除|抠掉|擦掉|替换|换成|改成|p一下|p 一下)",
        text,
    ):
        return "edit"

    if re.search(
        r"(参考(这张|这个)?图.*(生成|做|画|出)|按(这张|这个)?图.*(生成|做|画|出)|"
        r"基于(这张|这个)?图.*(生成|做|画|出)|根据(这张|这个)?图.*(生成|做|画|出)|"
        r"以(这张|这个)?图为参考|参考图生成|同风格|同氛围|同构图|same style|similar style|"
        r"reference image|inspired by)",
        text,
    ):
        return "generate"

    return "edit"


def _normalize_mode(raw: str | None, has_input: bool, prompt: str | None = None) -> str:
    value = (raw or "").strip().lower()
    if value in {"generate", "edit", "localized-fix"}:
        return value
    return _infer_mode_from_prompt(prompt, has_input)


def _normalize_transport(raw: str | None, mode: str, has_input: bool) -> str:
    value = (raw or "").strip().lower()
    if value in {TRANSPORT_AUTO, TRANSPORT_IMAGES, TRANSPORT_RESPONSES, TRANSPORT_CHAT}:
        return value
    if mode == "generate":
        return TRANSPORT_AUTO
    return TRANSPORT_IMAGES


def _model_slug(model: str | None) -> str:
    raw = (model or "").strip().lower()
    if not raw:
        return ""
    return raw.split("/")[-1]


def _is_image_only_model(model: str | None) -> bool:
    slug = _model_slug(model)
    return any(slug.startswith(prefix) for prefix in IMAGE_ONLY_MODEL_PREFIXES)


def _generation_transport_order(selected: str, model: str | None = None) -> List[str]:
    if selected == TRANSPORT_AUTO:
        if _is_image_only_model(model):
            return [TRANSPORT_IMAGES]
        return [TRANSPORT_IMAGES, TRANSPORT_RESPONSES]
    return [selected]


def _resolve_runtime(args: argparse.Namespace) -> Tuple[str, str, str]:
    home = _hermes_home()
    env_map = _load_simple_env(home / ".env")
    config = _load_config(home / "config.yaml")

    api_key = _first_nonempty(
        args.api_key,
        os.environ.get("OPENAI_API_KEY_IMAGE"),
        env_map.get("OPENAI_API_KEY_IMAGE"),
        os.environ.get("OPENAI_API_KEY"),
        env_map.get("OPENAI_API_KEY"),
        str(((config.get("model") or {}).get("api_key") or "")).strip(),
    )
    if not api_key:
        raise SystemExit("No image API key found. Set OPENAI_API_KEY_IMAGE or OPENAI_API_KEY.")

    base_url = _first_nonempty(
        args.base_url,
        os.environ.get("OPENAI_BASE_URL_IMAGE"),
        env_map.get("OPENAI_BASE_URL_IMAGE"),
        os.environ.get("OPENAI_BASE_URL"),
        env_map.get("OPENAI_BASE_URL"),
        str(((config.get("model") or {}).get("base_url") or "")).strip(),
        "https://api.openai.com/v1",
    ).rstrip("/")

    model = _first_nonempty(
        args.model,
        os.environ.get("OPENAI_IMAGE_MODEL"),
        env_map.get("OPENAI_IMAGE_MODEL"),
        DEFAULT_MODEL,
    )
    return base_url, api_key, model


def _image_dimensions(path: str | Path) -> Tuple[int, int] | None:
    try:
        from PIL import Image  # type: ignore

        with Image.open(Path(path).expanduser().resolve()) as im:
            return im.size
    except Exception:
        return None


def _resolve_size(explicit_size: str, prompt: str, input_paths: List[str], profile: str) -> str:
    explicit = (explicit_size or "").strip()
    if explicit:
        return explicit

    preset_size = str((PROFILE_PRESETS.get(profile) or {}).get("size") or "").strip()
    if preset_size:
        return preset_size

    # Default to straight-through requests: if the caller did not explicitly ask for
    # a size and did not choose a profile with a preset size, let the provider decide.
    return ""


def _augment_prompt(prompt: str, profile: str, input_paths: List[str], mode: str, negative_hints: str) -> Tuple[str, bool]:
    base = (prompt or "").strip()
    preset = PROFILE_PRESETS.get(profile) or {}
    suffix = str(preset.get("prompt_suffix") or "").strip()
    if not suffix:
        return base, False

    lowered = base.lower()
    if suffix.lower() in lowered:
        return base, False

    if mode == "localized-fix":
        extra = PROMPT_TEMPLATES["localized_fix_suffix"]
        suffix = f"{suffix}, {extra}" if suffix else extra
    elif mode != "generate" and input_paths and profile in {"official-like", "photo-real"} and not re.search(r"(only change|keep everything else unchanged|只改|别的不要动)", lowered):
        suffix = f"{suffix}, {PROMPT_TEMPLATES['preserve_unrelated_suffix']}"

    if negative_hints.strip():
        suffix = f"{suffix}, avoid: {negative_hints.strip()}" if suffix else f"avoid: {negative_hints.strip()}"

    return f"{base}. {suffix}" if base and suffix else (base or suffix), bool(suffix)


def _sanitize_run_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip()).strip("-")
    return text[:80] or "run"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_variant_context(value: str) -> dict:
    if not value:
        return {}
    path = Path(value).expanduser()
    if path.exists() and path.is_file():
        data = _load_json(path)
        if isinstance(data, dict):
            return data
    return {}


def _resolve_request(args: argparse.Namespace, resolved_model: str) -> dict:
    mode = _normalize_mode(args.mode, bool(args.input), args.prompt)
    # Keep OpenAI-style defaults: once an input image is attached, default to edit semantics
    # for blank/auto/images transport selection, while still allowing explicit responses overrides.
    raw_transport = (args.transport or "").strip().lower()
    if args.input and mode == "generate" and raw_transport in {"", TRANSPORT_AUTO, TRANSPORT_IMAGES}:
        mode = "edit"
    transport = _normalize_transport(args.transport, mode, bool(args.input))
    explicit_profile = _normalize_profile(args.profile)
    profile = explicit_profile if explicit_profile != PROFILE_DEFAULT else PROFILE_DEFAULT
    preset = PROFILE_PRESETS.get(profile) or {}

    size = _resolve_size(args.size, args.prompt, list(args.input), profile)
    size_auto_omitted = False
    explicit_size = (args.size or "").strip()
    if mode == "generate" and not explicit_size and not size:
        size_auto_omitted = True
    variant_context = _load_variant_context(args.variant_of)
    variant_suffix = ""
    if variant_context:
        prior_prompt = str(variant_context.get("resolved_prompt") or variant_context.get("prompt") or "").strip()
        if prior_prompt:
            variant_suffix = PROMPT_TEMPLATES["variant_suffix"]
    base_prompt = (args.prompt or "").strip()
    if variant_suffix:
        base_prompt = f"{base_prompt}. {variant_suffix}" if base_prompt else variant_suffix
    prompt, prompt_augmented = _augment_prompt(base_prompt, profile, list(args.input), mode, args.negative_hints)

    quality = (args.quality or "").strip()
    if not quality:
        quality = str(preset.get("quality") or "").strip()

    n = max(args.n, args.best_of or 0, 1)
    transport_order = _generation_transport_order(transport, resolved_model) if mode == "generate" else [TRANSPORT_IMAGES]

    return {
        "mode": mode,
        "transport": transport,
        "transport_order": transport_order,
        "profile": profile,
        "model": resolved_model,
        "prompt": prompt,
        "size": size,
        "size_auto_omitted": size_auto_omitted,
        "quality": quality,
        "background": (args.background or "").strip(),
        "n": n,
        "best_of": args.best_of,
        "negative_hints": (args.negative_hints or "").strip(),
        "variant_of": args.variant_of,
        "prompt_augmented": prompt_augmented,
    }


def _planned_outdir(custom_outdir: str | None) -> Path:
    base = Path(custom_outdir).expanduser() if custom_outdir else (_hermes_home() / DEFAULT_OUTPUT_DIRNAME)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return base / stamp


def _ensure_outdir(outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _generated_images_root(custom_outdir: str | None = None) -> Path:
    return Path(custom_outdir).expanduser() if custom_outdir else (_hermes_home() / DEFAULT_OUTPUT_DIRNAME)


def _sanitize_ext(content_type: str | None, fallback: str = ".png") -> str:
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    return fallback


def _save_bytes(payload: bytes, outdir: Path, index: int, ext: str = ".png") -> str:
    result_dir = outdir / "result" if (outdir / "meta").exists() or (outdir / "result").exists() else outdir
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / f"image-{index:02d}{ext}"
    path.write_bytes(payload)
    return str(path.resolve())


def _safe_excerpt(text: str, limit: int = 220) -> str:
    raw = (text or "").strip()
    return raw if len(raw) <= limit else raw[:limit] + "..."


def _compact_for_log(value: object, *, str_limit: int = 1200, list_limit: int = 12, dict_limit: int = 40) -> object:
    if isinstance(value, str):
        return _safe_excerpt(value, str_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        items = list(value.items())
        compacted = {str(k): _compact_for_log(v, str_limit=str_limit, list_limit=list_limit, dict_limit=dict_limit) for k, v in items[:dict_limit]}
        if len(items) > dict_limit:
            compacted["_truncated_keys"] = len(items) - dict_limit
        return compacted
    if isinstance(value, (list, tuple)):
        compacted_list = [_compact_for_log(v, str_limit=str_limit, list_limit=list_limit, dict_limit=dict_limit) for v in list(value)[:list_limit]]
        if len(value) > list_limit:
            compacted_list.append({"_truncated_items": len(value) - list_limit})
        return compacted_list
    return _safe_excerpt(repr(value), str_limit)


def _redact_headers(headers: Dict[str, str] | None) -> Dict[str, str]:
    if not headers:
        return {}
    redacted: Dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _http_trace_root(custom_trace_dir: str | None = None) -> Path:
    return Path(custom_trace_dir).expanduser() if custom_trace_dir else (_generated_images_root() / "http-traces")


def _http_trace_path(transport: str, custom_trace_dir: str | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_transport = re.sub(r"[^a-zA-Z0-9._-]+", "-", transport or "unknown")
    return _http_trace_root(custom_trace_dir) / f"{stamp}-{_now_ms()}-{safe_transport}.json"


def _response_trace_info(resp: requests.Response | None, *, body_limit: int = 4000) -> dict | None:
    if resp is None:
        return None
    try:
        body_text = resp.text or ""
    except Exception:
        body_text = ""
    try:
        body_size = len(resp.content or b"")
    except Exception:
        body_size = 0
    info = {
        "status_code": resp.status_code,
        "ok": resp.ok,
        "reason": resp.reason,
        "headers": _redact_headers(dict(resp.headers)),
        "body_bytes": body_size,
    }
    if body_text:
        info["body_excerpt"] = _safe_excerpt(body_text, body_limit)
    return info


def _write_http_trace(
    args: argparse.Namespace,
    *,
    transport: str,
    method: str,
    url: str,
    request_headers: Dict[str, str],
    request_body: object,
    response: requests.Response | None = None,
    exception: Exception | None = None,
    elapsed_ms: int | None = None,
    extra: dict | None = None,
) -> str:
    if not getattr(args, "trace_http", False):
        return ""
    path = _http_trace_path(transport, getattr(args, "trace_http_dir", ""))
    payload: dict = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "transport": transport,
        "method": method,
        "url": url,
        "elapsed_ms": elapsed_ms,
        "request": {
            "headers": _redact_headers(request_headers),
            "body": _compact_for_log(request_body),
            "timeout": getattr(args, "timeout", None),
        },
        "response": _response_trace_info(response),
    }
    if exception is not None:
        payload["exception"] = {
            "type": type(exception).__name__,
            "message": str(exception),
        }
    if extra:
        payload["extra"] = _compact_for_log(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _write_run_record(outdir: Path, record: dict) -> str:
    meta_dir = outdir / "meta" if (outdir / "meta").exists() or (outdir / "result").exists() else outdir
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "run-record.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _write_archive(outdir: Path, output_paths: List[str], include_record: str | None = None) -> str:
    archive = outdir / "outputs.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for raw in output_paths:
            p = Path(raw)
            if p.exists():
                zf.write(p, arcname=p.name)
        if include_record:
            rp = Path(include_record)
            if rp.exists():
                zf.write(rp, arcname=rp.name)
    return str(archive.resolve())


def _init_archive_layout(outdir: Path) -> None:
    (outdir / "result").mkdir(parents=True, exist_ok=True)
    (outdir / "meta").mkdir(parents=True, exist_ok=True)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_info(path: str | Path) -> dict:
    p = Path(path)
    info = {
        "path": str(p.resolve()),
        "name": p.name,
        "bytes": p.stat().st_size if p.exists() else 0,
        "sha256": _sha256_file(p) if p.exists() else "",
    }
    dims = _image_dimensions(p)
    if dims:
        info["width"], info["height"] = dims
    mime = mimetypes.guess_type(str(p))[0]
    if mime:
        info["mime"] = mime
    return info


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except Exception:
            continue
    return total


def _run_dirs(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)


def _summarize_run_dir(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "bytes": _dir_size_bytes(path),
        "mtime": int(stat.st_mtime),
        "file_count": sum(1 for x in path.rglob("*") if x.is_file()),
    }


def _list_runs(root: Path, limit: int) -> dict:
    all_dirs = _run_dirs(root)
    nonempty_dirs = [p for p in all_dirs if any(x.is_file() for x in p.rglob("*"))]
    runs = [_summarize_run_dir(p) for p in nonempty_dirs[: max(limit, 1)]]
    total_bytes = sum(item["bytes"] for item in runs)
    return {
        "root": str(root.resolve()),
        "run_count": len(all_dirs),
        "nonempty_run_count": len(nonempty_dirs),
        "listed_count": len(runs),
        "listed_bytes": total_bytes,
        "summary": f"listed {len(runs)} recent non-empty run dirs under {root}; total dirs: {len(all_dirs)}, non-empty dirs: {len(nonempty_dirs)}.",
        "runs": runs,
    }


def _select_cleanup_targets(root: Path, keep: int, days: int) -> List[Path]:
    dirs = _run_dirs(root)
    now = time.time()
    targets: List[Path] = []
    if days > 0:
        cutoff = now - days * 86400
        targets.extend([p for p in dirs if p.stat().st_mtime < cutoff])
    elif keep >= 0:
        targets.extend(dirs[keep:])
    return sorted(set(targets), key=lambda p: p.name)


def _cleanup_runs(root: Path, keep: int, days: int, cleanup_all: bool, dry_run: bool) -> dict:
    root = root.resolve()
    expected_root = (_hermes_home() / DEFAULT_OUTPUT_DIRNAME).resolve()
    if root != expected_root:
        raise RuntimeError(f"Refusing cleanup outside Hermes image root: {root}")

    if cleanup_all:
        targets = _run_dirs(root)
        mode = "all"
    elif days > 0:
        targets = _select_cleanup_targets(root, keep=-1, days=days)
        mode = "days"
    else:
        targets = _select_cleanup_targets(root, keep=max(keep, 0), days=0)
        mode = "keep"

    items = [_summarize_run_dir(p) for p in targets]
    freed = sum(item["bytes"] for item in items)
    if not dry_run:
        for item in items:
            shutil.rmtree(item["path"], ignore_errors=True)

    action_word = "would delete" if dry_run else "deleted"
    if mode == "days":
        summary = f"{action_word} {len(items)} run dirs older than {days} days under {root}."
    elif mode == "keep":
        summary = f"{action_word} {len(items)} older run dirs and keep the newest {keep} under {root}."
    else:
        summary = f"{action_word} {len(items)} run dirs under {root}."

    return {
        "mode": mode,
        "root": str(root.resolve()),
        "dry_run": dry_run,
        "count": len(items),
        "bytes": freed,
        "items": items,
        "keep": keep,
        "days": days,
        "cleanup_all": cleanup_all,
        "summary": summary,
    }


def _is_management_mode(args: argparse.Namespace) -> bool:
    return bool(args.list_runs or args.cleanup_days > 0 or args.cleanup_keep >= 0 or args.cleanup_all)


def _write_meta_file(outdir: Path, name: str, payload: dict) -> str:
    meta_dir = outdir / "meta" if (outdir / "meta").exists() or (outdir / "result").exists() else outdir
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _filter_input_echoes(output_paths: List[str], input_paths: Iterable[str]) -> List[str]:
    input_hashes = {_sha256_file(Path(raw).expanduser().resolve()) for raw in input_paths}
    if not input_hashes:
        return output_paths

    seen_output_hashes: set[str] = set()
    filtered: List[str] = []
    for out in output_paths:
        digest = _sha256_file(out)
        if digest in input_hashes:
            continue
        if digest in seen_output_hashes:
            continue
        seen_output_hashes.add(digest)
        filtered.append(out)
    return filtered


def _download_file(url: str, outdir: Path, index: int, timeout: int) -> str:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    ext = _sanitize_ext(resp.headers.get("content-type"), fallback=Path(url).suffix or ".png")
    return _save_bytes(resp.content, outdir, index, ext=ext)


def _extract_paths(resp_json: dict, outdir: Path, timeout: int) -> List[str]:
    data = resp_json.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Image API returned no data field: {json.dumps(resp_json, ensure_ascii=False)[:400]}")

    paths: List[str] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json") or item.get("b64") or item.get("base64")
        if isinstance(b64, str) and b64.strip():
            blob = base64.b64decode(b64)
            paths.append(_save_bytes(blob, outdir, idx, ext=".png"))
            continue
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            paths.append(_download_file(url, outdir, idx, timeout=timeout))
            continue
        raise RuntimeError(f"Unsupported image item format: {json.dumps(item, ensure_ascii=False)[:300]}")

    if not paths:
        raise RuntimeError("Image API returned an empty result set.")
    return paths


def _response_payload_with_meta(
    data_items: List[dict],
    transport: str,
    *,
    response_model: str = "",
    image_tool_model: str = "",
    events: List[str] | None = None,
    stream_timings_ms: Dict[str, int] | None = None,
    raw_response_keys: List[str] | None = None,
) -> dict:
    payload: dict = {
        "data": data_items,
        "_transport": transport,
        "_response_model": response_model,
        "_image_tool_model": image_tool_model,
    }
    if events:
        payload["_events"] = events
    if stream_timings_ms:
        payload["_stream_timings_ms"] = stream_timings_ms
    if raw_response_keys:
        payload["_raw_response_keys"] = raw_response_keys
    return payload


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def _json_or_error(resp: requests.Response) -> dict:
    try:
        payload = resp.json()
    except Exception:
        text = (resp.text or "")[:600]
        raise RuntimeError(f"Image API returned non-JSON response (HTTP {resp.status_code}): {text}")

    if resp.ok:
        return payload

    err_text = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            err_text = str(err.get("message") or err)
        elif err:
            err_text = str(err)
    err_text = err_text or json.dumps(payload, ensure_ascii=False)[:600]
    raise RuntimeError(f"Image API error (HTTP {resp.status_code}): {err_text}")


def _classify_error(message: str) -> str:
    text = (message or "").lower()
    if "remote end closed connection without response" in text or "remotedisconnected" in text:
        return "provider_connection_closed"
    if "api key" in text or "unauthorized" in text or "401" in text or "403" in text:
        return "auth_error"
    if "responses-capable text model" in text or "image-only model" in text:
        return "transport_unsupported"
    if "not supported by codex backend" in text or "transport unsupported" in text:
        return "transport_unsupported"
    if "content policy" in text or "policy_violation" in text or "moderation" in text or "safety system" in text:
        return "content_blocked"
    if "503" in text or "no available channels" in text or "service unavailable" in text:
        return "provider_unavailable"
    if "429" in text or "rate limit" in text:
        return "rate_limited"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "unsupported image item format" in text or "unsupported" in text:
        return "unsupported_format"
    if "unchanged input images" in text or "no novel edited image" in text:
        return "edit_echo_only"
    if "400" in text or "bad request" in text:
        return "bad_request"
    if "404" in text:
        return "not_found"
    if "no data field" in text or "empty result set" in text:
        return "empty_result"
    return "unknown_error"


class RequestAttemptsError(RuntimeError):
    def __init__(self, primary_error: str, *, attempts: List[dict], last_error: str | None = None):
        super().__init__(primary_error)
        self.primary_error = primary_error
        self.primary_error_type = _classify_error(primary_error)
        self.last_error = last_error or primary_error
        self.last_error_type = _classify_error(self.last_error)
        self.attempts = attempts


class RequestTraceError(RuntimeError):
    def __init__(self, message: str, *, trace_path: str = ""):
        super().__init__(message)
        self.trace_path = trace_path


def _request_with_fallback(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace) -> Tuple[dict, List[dict]]:
    attempts: List[dict] = []
    fields_to_drop = []
    if args.compat_fallback:
        if resolved.get("quality"):
            fields_to_drop.append("quality")
        if resolved.get("background"):
            fields_to_drop.append("background")

    variants = [dict(resolved)]
    for field in fields_to_drop:
        modified = dict(resolved)
        modified[field] = ""
        variants.append(modified)

    last_error: Exception | None = None
    for idx, variant in enumerate(variants, start=1):
        dropped = [k for k in ["quality", "background"] if resolved.get(k) and not variant.get(k)]
        transports = [TRANSPORT_IMAGES] if variant.get("mode") != "generate" else list(variant.get("transport_order") or _generation_transport_order(variant.get("transport") or TRANSPORT_AUTO, variant.get("model")))
        for transport in transports:
            attempt_started_ms = _now_ms()
            try:
                if variant.get("mode") != "generate":
                    payload = _post_edit(base_url, api_key, variant, args)
                else:
                    payload = _post_generation(base_url, api_key, variant, args, transport=transport)
                elapsed_ms = max(_now_ms() - attempt_started_ms, 0)
                attempts.append({
                    "index": idx,
                    "transport": transport,
                    "success": True,
                    "dropped_fields": dropped,
                    "elapsed_ms": elapsed_ms,
                })
                trace_path = str(payload.get("_http_trace_path") or "") if isinstance(payload, dict) else ""
                if trace_path:
                    attempts[-1]["trace_path"] = trace_path
                return payload, attempts
            except Exception as e:
                last_error = e
                elapsed_ms = max(_now_ms() - attempt_started_ms, 0)
                attempts.append({
                    "index": idx,
                    "transport": transport,
                    "success": False,
                    "dropped_fields": dropped,
                    "elapsed_ms": elapsed_ms,
                    "error": str(e),
                    "error_type": _classify_error(str(e)),
                })
                trace_path = str(getattr(e, "trace_path", "") or "")
                if trace_path:
                    attempts[-1]["trace_path"] = trace_path
    assert last_error is not None
    first_failed = next((item for item in attempts if not item.get("success")), None)
    primary_error = str(first_failed.get("error") if first_failed else last_error)
    raise RequestAttemptsError(primary_error, attempts=attempts, last_error=str(last_error))


def _build_generation_payload(resolved: dict) -> dict:
    payload = {
        "model": resolved["model"],
        "prompt": resolved["prompt"],
        "n": resolved["n"],
    }
    size = str(resolved.get("size") or "").strip()
    if size:
        payload["size"] = size
    if resolved["quality"]:
        payload["quality"] = resolved["quality"]
    if resolved["background"]:
        payload["background"] = resolved["background"]
    return payload


def _open_input_files(paths: Iterable[str]) -> List[Tuple[str, tuple]]:
    files: List[Tuple[str, tuple]] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Input image not found: {path}")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        files.append((str(path), (path.name, open(path, "rb"), mime)))
    return files


def _responses_input_payload(prompt: str, input_paths: Iterable[str]) -> object:
    resolved_paths = [Path(raw).expanduser().resolve() for raw in input_paths]
    if not resolved_paths:
        return prompt

    content: List[dict] = []
    if (prompt or "").strip():
        content.append({"type": "input_text", "text": prompt})
    for path in resolved_paths:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Input image not found: {path}")
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})
    return [{"role": "user", "content": content}]


def _close_input_files(files: List[Tuple[str, tuple]]) -> None:
    for _, info in files:
        try:
            info[1].close()
        except Exception:
            pass


def _edit_attempt_specs(opened: List[Tuple[str, tuple]]) -> List[List[Tuple[str, tuple]]]:
    tuples_only = [item[1] for item in opened]
    if len(tuples_only) == 1:
        one = tuples_only[0]
        return [[("image", one)], [("image[]", one)]]
    return [
        [("image[]", f) for f in tuples_only],
        [("image", f) for f in tuples_only],
    ]


def _post_generation_images(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace) -> dict:
    if args.input:
        raise RuntimeError("transport unsupported: reference-image generation requires responses transport; images transport would drop input images")
    url = f"{base_url}/images/generations"
    headers = {**_headers(api_key), "Content-Type": "application/json"}
    body = _build_generation_payload(resolved)
    request_started_ms = _now_ms()
    try:
        resp = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=args.timeout,
        )
    except Exception as e:
        trace_path = _write_http_trace(
            args,
            transport=TRANSPORT_IMAGES,
            method="POST",
            url=url,
            request_headers=headers,
            request_body={"json": body},
            response=getattr(e, "response", None),
            exception=e,
            elapsed_ms=max(_now_ms() - request_started_ms, 0),
        )
        raise RequestTraceError(str(e), trace_path=trace_path) from e
    trace_path = _write_http_trace(
        args,
        transport=TRANSPORT_IMAGES,
        method="POST",
        url=url,
        request_headers=headers,
        request_body={"json": body},
        response=resp,
        elapsed_ms=max(_now_ms() - request_started_ms, 0),
    )
    payload = _json_or_error(resp)
    if isinstance(payload, dict):
        payload.setdefault("_transport", TRANSPORT_IMAGES)
        payload.setdefault("_response_model", resolved["model"])
        payload.setdefault("_image_tool_model", resolved["model"])
        if trace_path:
            payload.setdefault("_http_trace_path", trace_path)
    return payload


def _post_generation_chat(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace) -> dict:
    if args.input:
        raise RuntimeError("transport unsupported: reference-image generation requires responses transport; chat transport would drop input images")
    url = f"{base_url}/chat/completions"
    body = {
        "model": resolved["model"],
        "messages": [{"role": "user", "content": resolved["prompt"]}],
    }
    size = str(resolved.get("size") or "").strip()
    if size:
        body["size"] = size
    resp = requests.post(
        url,
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=args.timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"Image API error (HTTP {resp.status_code}): {(resp.text or '')[:600]}")
    text = resp.text or ""
    matches = re.findall(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)", text)
    if not matches:
        raise RuntimeError("Chat completion returned no inline image data.")
    return _response_payload_with_meta(
        [{"b64_json": item} for item in matches],
        TRANSPORT_CHAT,
        response_model=resolved["model"],
        image_tool_model=resolved["model"],
    )


def _post_generation_responses(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace) -> dict:
    url = f"{base_url}/responses"
    stream_started_ms = _now_ms()
    tool: dict = {"type": "image_generation"}
    size = str(resolved.get("size") or "").strip()
    if size:
        tool["size"] = size
    if resolved.get("quality"):
        tool["quality"] = resolved["quality"]
    if resolved.get("background"):
        tool["background"] = resolved["background"]
    body = {
        "model": resolved["model"],
        "input": _responses_input_payload(resolved["prompt"], args.input),
        "tools": [tool],
    }
    resp = requests.post(
        url,
        headers={**_headers(api_key), "Content-Type": "application/json", "Accept": "text/event-stream"},
        json=body,
        timeout=args.timeout,
        stream=True,
    )
    if not resp.ok:
        raise RuntimeError(f"Image API error (HTTP {resp.status_code}): {(resp.text or '')[:600]}")

    response_model = ""
    image_tool_model = ""
    events: List[str] = []
    stream_timings_ms: Dict[str, int] = {}
    chunks: List[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        delta_ms = max(_now_ms() - stream_started_ms, 0)
        stream_timings_ms.setdefault("first_line_ms", delta_ms)
        chunks.append(line)
        if line.startswith("event: "):
            event_name = line[7:]
            events.append(event_name)
            stream_timings_ms.setdefault("first_event_ms", delta_ms)
            if event_name == "keepalive":
                stream_timings_ms.setdefault("first_keepalive_ms", delta_ms)
            if "partial_image" in event_name:
                stream_timings_ms.setdefault("first_partial_image_ms", delta_ms)
            if event_name == "response.completed":
                stream_timings_ms["completed_ms"] = delta_ms
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        response = obj.get("response")
        if isinstance(response, dict):
            if isinstance(response.get("model"), str):
                response_model = response.get("model") or response_model
            tools = response.get("tools")
            if isinstance(tools, list) and tools and isinstance(tools[0], dict) and isinstance(tools[0].get("model"), str):
                image_tool_model = tools[0].get("model") or image_tool_model

    text = "\n".join(chunks)
    result_matches = re.findall(r'"result":"([A-Za-z0-9+/=]+)"', text)
    if not result_matches:
        result_matches = re.findall(r'"image_data":"([A-Za-z0-9+/=]+)"', text)
    if not result_matches:
        result_matches = re.findall(r'"b64_json":"([A-Za-z0-9+/=]+)"', text)
    if not result_matches:
        result_matches = re.findall(r'"partial_image_b64":"([A-Za-z0-9+/=]+)"', text)
    if not result_matches:
        raise RuntimeError("Responses API returned no image payload in SSE stream.")
    return _response_payload_with_meta(
        [{"b64_json": item} for item in result_matches],
        TRANSPORT_RESPONSES,
        response_model=response_model or resolved["model"],
        image_tool_model=image_tool_model or resolved["model"],
        events=events[-12:],
        stream_timings_ms=stream_timings_ms,
    )


def _post_generation(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace, *, transport: str) -> dict:
    if transport == TRANSPORT_RESPONSES:
        return _post_generation_responses(base_url, api_key, resolved, args)
    if transport == TRANSPORT_CHAT:
        return _post_generation_chat(base_url, api_key, resolved, args)
    return _post_generation_images(base_url, api_key, resolved, args)


def _post_edit(base_url: str, api_key: str, resolved: dict, args: argparse.Namespace) -> dict:
    url = f"{base_url}/images/edits"
    opened = _open_input_files(args.input)
    try:
        last_error: Exception | None = None
        for files in _edit_attempt_specs(opened):
            form = {
                "model": resolved["model"],
                "prompt": resolved["prompt"],
                "size": resolved["size"],
                "n": str(resolved["n"]),
            }
            if resolved["quality"]:
                form["quality"] = resolved["quality"]
            if resolved["background"]:
                form["background"] = resolved["background"]
            try:
                resp = requests.post(
                    url,
                    headers=_headers(api_key),
                    data=form,
                    files=files,
                    timeout=args.timeout,
                )
                return _json_or_error(resp)
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"All image edit request formats failed: {last_error}")
    finally:
        _close_input_files(opened)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate or edit images through an OpenAI-compatible Images API.")
    p.add_argument("--list-profiles", action="store_true", help="List available profiles and exit.")
    p.add_argument("--mode", default="", help="Optional explicit mode: generate, edit, localized-fix.")
    p.add_argument("--prompt", default="", help="Prompt to send to the image model. Required for generation/edit, not required for management commands.")
    p.add_argument("--input", action="append", default=[], help="Local input image path. Repeat to send multiple images.")
    p.add_argument("--model", default="", help=f"Image model (default fallback: {DEFAULT_MODEL}).")
    p.add_argument("--transport", default="", help="Generation transport: auto, responses, images, or chat. Generate defaults to auto; edit keeps images.")
    p.add_argument("--profile", default=PROFILE_DEFAULT, help="Optional request profile. Use --list-profiles to inspect.")
    p.add_argument("--size", default="", help="Optional requested size. Omit to let the provider choose naturally.")
    p.add_argument("--quality", default="", help="Optional quality setting, forwarded if non-empty.")
    p.add_argument("--background", default="", help="Optional background setting, forwarded if non-empty.")
    p.add_argument("--n", type=int, default=1, help="Number of images to request.")
    p.add_argument("--best-of", type=int, default=0, help="Explicitly request multiple candidates. Defaults to off; resolved n becomes max(n, best-of).")
    p.add_argument("--negative-hints", default="", help="Optional lightweight negative hints, e.g. 'no watermark, no extra fingers'.")
    p.add_argument("--variant-of", default="", help="Optional path to a previous run-record.json to create a similar variation.")
    p.add_argument("--base-url", default="", help="Optional image API base URL override; otherwise image-specific env or Hermes main config is used.")
    p.add_argument("--api-key", default="", help="Optional image API key override; otherwise image-specific env or Hermes defaults are used.")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    p.add_argument("--outdir", default="", help="Directory under which timestamped output folder will be created.")
    p.add_argument("--archive", action="store_true", help="Write outputs.zip containing images and run record.")
    p.add_argument("--record-run", action="store_true", help="Write run-record.json beside outputs for replay/debug.")
    p.add_argument("--compat-fallback", action="store_true", help="Retry once by dropping provider-incompatible optional fields such as quality/background.")
    p.add_argument("--list-runs", action="store_true", help="List generated image run directories under the generated-images root.")
    p.add_argument("--list-runs-limit", type=int, default=20, help="How many recent run directories to list.")
    p.add_argument("--cleanup-days", type=int, default=0, help="Delete run directories older than N days.")
    p.add_argument("--cleanup-keep", type=int, default=-1, help="Keep only the newest N run directories; delete older ones.")
    p.add_argument("--cleanup-all", action="store_true", help="Delete all run directories under generated-images root.")
    p.add_argument("--dry-run", action="store_true", help="Print resolved request plan without calling the API.")
    p.add_argument("--show-resolved", action="store_true", help="Include resolved profile/model/size/quality metadata in stdout JSON.")
    p.add_argument("--debug", action="store_true", help="Include base_url/mode metadata in stdout JSON.")
    p.add_argument("--trace-http", action="store_true", help="Write a redacted raw HTTP trace file for each request attempt, useful when upstream billed but the client received no parseable result.")
    p.add_argument("--trace-http-dir", default="", help="Optional directory for --trace-http JSON files. Defaults to ~/.hermes/generated-images/http-traces.")
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    run_started_ms = _now_ms()

    try:
        if args.list_profiles:
            print(json.dumps({
                "success": True,
                "profiles": PROFILE_PRESETS,
            }, ensure_ascii=False))
            return 0

        if _is_management_mode(args):
            root = _generated_images_root(args.outdir or None)
            if args.list_runs:
                print(json.dumps({
                    "success": True,
                    "management": "list_runs",
                    "data": _list_runs(root, args.list_runs_limit),
                }, ensure_ascii=False))
                return 0

            if args.cleanup_all and not args.dry_run:
                # 明确高风险，但按用户要求执行；CLI/TG 上层应自行做解释
                pass

            cleanup = _cleanup_runs(
                root=root,
                keep=args.cleanup_keep,
                days=args.cleanup_days,
                cleanup_all=args.cleanup_all,
                dry_run=args.dry_run,
            )
            print(json.dumps({
                "success": True,
                "management": "cleanup_runs",
                "data": cleanup,
            }, ensure_ascii=False))
            return 0

        if not args.prompt.strip():
            raise SystemExit("--prompt is required for image generation/edit commands.")

        timings_ms: dict = {}

        phase_started_ms = _now_ms()
        base_url, api_key, resolved_model = _resolve_runtime(args)
        timings_ms["resolve_runtime"] = max(_now_ms() - phase_started_ms, 0)

        phase_started_ms = _now_ms()
        mode = _normalize_mode(args.mode, bool(args.input), args.prompt)
        outdir = _planned_outdir(args.outdir or None)
        resolved = _resolve_request(args, resolved_model)
        timings_ms["prepare_request"] = max(_now_ms() - phase_started_ms, 0)

        if args.dry_run:
            result = {
                "success": True,
                "dry_run": True,
                "mode": mode,
                "model": resolved["model"],
                "base_url": base_url,
                "input_count": len(args.input),
                "outdir": str(outdir.resolve()),
                "prompt": args.prompt,
                "timings_ms": {**timings_ms, "total": max(_now_ms() - run_started_ms, 0)},
            }
            if args.show_resolved:
                result["resolved"] = {
                    "profile": resolved["profile"],
                    "mode": resolved["mode"],
                    "transport": resolved["transport"],
                    "transport_order": resolved["transport_order"],
                    "model": resolved["model"],
                    "size": resolved["size"],
                    "size_auto_omitted": resolved["size_auto_omitted"],
                    "quality": resolved["quality"],
                    "background": resolved["background"],
                    "n": resolved["n"],
                    "best_of": resolved["best_of"],
                    "negative_hints": resolved["negative_hints"],
                    "variant_of": resolved["variant_of"],
                    "prompt": resolved["prompt"],
                    "prompt_augmented": resolved["prompt_augmented"],
                }
            print(json.dumps(result, ensure_ascii=False))
            return 0

        phase_started_ms = _now_ms()
        response_json, attempts = _request_with_fallback(base_url, api_key, resolved, args)
        timings_ms["request"] = max(_now_ms() - phase_started_ms, 0)

        outdir = _ensure_outdir(outdir)
        if args.archive or args.record_run:
            _init_archive_layout(outdir)

        phase_started_ms = _now_ms()
        output_paths = _extract_paths(response_json, outdir, timeout=args.timeout)
        timings_ms["extract_output"] = max(_now_ms() - phase_started_ms, 0)
        if args.input:
            phase_started_ms = _now_ms()
            output_paths = _filter_input_echoes(output_paths, args.input)
            timings_ms["filter_echoes"] = max(_now_ms() - phase_started_ms, 0)
            if not output_paths:
                raise RuntimeError(
                    "Image edit API returned only unchanged input images; no novel edited image was produced."
                )

        actual_transport = str(response_json.get("_transport") or (TRANSPORT_IMAGES if resolved["mode"] != "generate" else resolved["transport_order"][0]))
        response_model = str(response_json.get("_response_model") or resolved["model"])
        image_tool_model = str(response_json.get("_image_tool_model") or resolved["model"])
        if isinstance(response_json.get("_stream_timings_ms"), dict):
            timings_ms["stream"] = response_json.get("_stream_timings_ms")

        result = {
            "success": True,
            "mode": mode,
            "transport": actual_transport,
            "model": image_tool_model,
            "response_model": response_model,
            "image_tool_model": image_tool_model,
            "prompt": resolved["prompt"],
            "output_paths": output_paths,
        }

        record_path = ""
        archive_path = ""
        record: dict | None = None
        if args.record_run or args.archive or args.variant_of:
            record = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mode": mode,
                "resolved_mode": resolved["mode"],
                "transport": actual_transport,
                "requested_transport": resolved["transport"],
                "transport_order": resolved["transport_order"],
                "profile": resolved["profile"],
                "model": image_tool_model,
                "response_model": response_model,
                "image_tool_model": image_tool_model,
                "size": resolved["size"],
                "size_auto_omitted": resolved["size_auto_omitted"],
                "quality": resolved["quality"],
                "background": resolved["background"],
                "n": resolved["n"],
                "best_of": resolved["best_of"],
                "negative_hints": resolved["negative_hints"],
                "variant_of": resolved["variant_of"],
                "user_prompt": _safe_excerpt(args.prompt, 800),
                "resolved_prompt": _safe_excerpt(resolved["prompt"], 1200),
                "prompt_augmented": resolved["prompt_augmented"],
                "output_paths": output_paths,
                "attempts": attempts,
                "events": response_json.get("_events") or [],
                "timings_ms": {},
            }
            phase_started_ms = _now_ms()
            record_path = _write_run_record(outdir, record)
            timings_ms["write_record"] = max(_now_ms() - phase_started_ms, 0)
            result["record_path"] = record_path

        if args.archive:
            phase_started_ms = _now_ms()
            archive_path = _write_archive(outdir, output_paths, include_record=record_path or None)
            timings_ms["write_archive"] = max(_now_ms() - phase_started_ms, 0)
            result["archive_path"] = archive_path

        timings_ms["total"] = max(_now_ms() - run_started_ms, 0)
        result["timings_ms"] = timings_ms

        if record_path and record is not None:
            record["timings_ms"] = timings_ms
            Path(record_path).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            if archive_path:
                _write_archive(outdir, output_paths, include_record=record_path)

        if args.show_resolved:
            result["resolved"] = {
                "profile": resolved["profile"],
                "mode": resolved["mode"],
                "transport": resolved["transport"],
                "transport_order": resolved["transport_order"],
                "size": resolved["size"],
                "size_auto_omitted": resolved["size_auto_omitted"],
                "quality": resolved["quality"],
                "background": resolved["background"],
                "n": resolved["n"],
                "best_of": resolved["best_of"],
                "negative_hints": resolved["negative_hints"],
                "variant_of": resolved["variant_of"],
                "prompt_augmented": resolved["prompt_augmented"],
            }
        if args.debug:
            result["base_url"] = base_url
            result["raw_keys"] = sorted(response_json.keys()) if isinstance(response_json, dict) else []
            result["attempts"] = attempts
        trace_paths = [item.get("trace_path") for item in attempts if item.get("trace_path")]
        if trace_paths:
            result["trace_paths"] = trace_paths
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        err = str(e)
        payload = {
            "success": False,
            "error": err,
            "error_type": _classify_error(err),
        }
        if isinstance(e, RequestAttemptsError):
            payload["error"] = e.primary_error
            payload["error_type"] = e.primary_error_type
            payload["primary_error"] = e.primary_error
            payload["primary_error_type"] = e.primary_error_type
            payload["last_error"] = e.last_error
            payload["last_error_type"] = e.last_error_type
            payload["attempts"] = e.attempts
            trace_paths = [item.get("trace_path") for item in e.attempts if item.get("trace_path")]
            if trace_paths:
                payload["trace_paths"] = trace_paths
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
