#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import requests
import yaml

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT = 180
DEFAULT_SIZE = "1024x1024"
DEFAULT_OUTPUT_DIRNAME = "generated-images"


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


def _resolve_runtime() -> Tuple[str, str]:
    home = _hermes_home()
    env_map = _load_simple_env(home / ".env")
    config = _load_config(home / "config.yaml")

    api_key = (
        os.environ.get("OPENAI_API_KEY_IMAGE")
        or env_map.get("OPENAI_API_KEY_IMAGE")
        or os.environ.get("OPENAI_API_KEY")
        or env_map.get("OPENAI_API_KEY")
        or str(((config.get("model") or {}).get("api_key") or "")).strip()
    )
    if not api_key:
        raise SystemExit("No image API key found. Set OPENAI_API_KEY_IMAGE or OPENAI_API_KEY.")

    base_url = str(((config.get("model") or {}).get("base_url") or "")).strip() or "https://api.openai.com/v1"
    base_url = base_url.rstrip("/")
    return base_url, api_key


def _ensure_outdir(custom_outdir: str | None) -> Path:
    base = Path(custom_outdir).expanduser() if custom_outdir else (_hermes_home() / DEFAULT_OUTPUT_DIRNAME)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = base / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _sanitize_ext(content_type: str | None, fallback: str = ".png") -> str:
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    return fallback


def _save_bytes(payload: bytes, outdir: Path, index: int, ext: str = ".png") -> str:
    path = outdir / f"image-{index:02d}{ext}"
    path.write_bytes(payload)
    return str(path.resolve())


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


def _build_generation_payload(args: argparse.Namespace) -> dict:
    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
        "n": args.n,
    }
    if args.quality:
        payload["quality"] = args.quality
    if args.background:
        payload["background"] = args.background
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


def _post_generation(base_url: str, api_key: str, args: argparse.Namespace) -> dict:
    url = f"{base_url}/images/generations"
    resp = requests.post(
        url,
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json=_build_generation_payload(args),
        timeout=args.timeout,
    )
    return _json_or_error(resp)


def _post_edit(base_url: str, api_key: str, args: argparse.Namespace) -> dict:
    url = f"{base_url}/images/edits"
    opened = _open_input_files(args.input)
    try:
        last_error: Exception | None = None
        for files in _edit_attempt_specs(opened):
            form = {
                "model": args.model,
                "prompt": args.prompt,
                "size": args.size,
                "n": str(args.n),
            }
            if args.quality:
                form["quality"] = args.quality
            if args.background:
                form["background"] = args.background
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
    p.add_argument("--prompt", required=True, help="Prompt to send to the image model.")
    p.add_argument("--input", action="append", default=[], help="Local input image path. Repeat to send multiple images.")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Image model (default: {DEFAULT_MODEL}).")
    p.add_argument("--size", default=DEFAULT_SIZE, help=f"Requested size (default: {DEFAULT_SIZE}).")
    p.add_argument("--quality", default="", help="Optional quality setting, forwarded if non-empty.")
    p.add_argument("--background", default="", help="Optional background setting, forwarded if non-empty.")
    p.add_argument("--n", type=int, default=1, help="Number of images to request.")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT}).")
    p.add_argument("--outdir", default="", help="Directory under which timestamped output folder will be created.")
    p.add_argument("--dry-run", action="store_true", help="Print resolved request plan without calling the API.")
    p.add_argument("--debug", action="store_true", help="Include base_url/mode metadata in stdout JSON.")
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        base_url, api_key = _resolve_runtime()
        mode = "edit" if args.input else "generate"
        outdir = _ensure_outdir(args.outdir or None)

        if args.dry_run:
            print(json.dumps({
                "success": True,
                "dry_run": True,
                "mode": mode,
                "model": args.model,
                "base_url": base_url,
                "input_count": len(args.input),
                "outdir": str(outdir.resolve()),
                "prompt": args.prompt,
            }, ensure_ascii=False))
            return 0

        response_json = _post_edit(base_url, api_key, args) if args.input else _post_generation(base_url, api_key, args)
        output_paths = _extract_paths(response_json, outdir, timeout=args.timeout)
        if args.input:
            output_paths = _filter_input_echoes(output_paths, args.input)
            if not output_paths:
                raise RuntimeError(
                    "Image edit API returned only unchanged input images; no novel edited image was produced."
                )

        result = {
            "success": True,
            "mode": mode,
            "model": args.model,
            "prompt": args.prompt,
            "output_paths": output_paths,
        }
        if args.debug:
            result["base_url"] = base_url
            result["raw_keys"] = sorted(response_json.keys()) if isinstance(response_json, dict) else []
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({
            "success": False,
            "error": str(e),
        }, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
