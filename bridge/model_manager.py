"""
RockoAgents Model Manager
Resolves model + provider per agent, loads credentials, calls API, handles fallback.
Never hardcodes model names. All config comes from project.json + .env
"""
import json, os, time, urllib.request, urllib.error
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── Globals set by bridge on load ─────────────────────────────────────────────
_project:  Dict = {}
_env_vars: Dict = {}

def init(project: Dict, env_vars: Dict):
    global _project, _env_vars
    _project  = project
    _env_vars = env_vars

def load_env(root_path: str) -> Dict:
    env = os.environ.copy()
    ef  = _project.get("env", {}).get("env_file", ".env")
    ep  = Path(root_path) / ef
    if ep.exists():
        with open(ep) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# ── Model resolution ──────────────────────────────────────────────────────────
def resolve_model(agent_def: Dict) -> Tuple[str, str, Dict]:
    """
    Returns (model_name, provider_key, provider_config)
    Priority: agent.model_override > project.default_model
    """
    model_cfg   = _project.get("model", {})
    providers   = model_cfg.get("providers", {})
    default_prov = model_cfg.get("default_provider", "anthropic")

    model_name   = agent_def.get("model_override") or model_cfg.get("default_model", "claude-sonnet-4-20250514")
    provider_key = agent_def.get("model_provider") or default_prov
    provider_cfg = providers.get(provider_key, {})

    return model_name, provider_key, provider_cfg

def resolve_api_key(provider_cfg: Dict, env: Dict) -> Optional[str]:
    env_var = provider_cfg.get("api_key_env") or provider_cfg.get("env_var")
    if not env_var:
        return None
    return env.get(env_var)

# ── Normalised response ───────────────────────────────────────────────────────
def _norm(text: str, model: str, provider: str, duration_ms: int,
          tokens: Dict, fallback_used: bool = False) -> Dict:
    return {
        "ok":           True,
        "text":         text,
        "model":        model,
        "provider":     provider,
        "duration_ms":  duration_ms,
        "tokens":       tokens,
        "fallback_used": fallback_used,
        "content":      [{"type": "text", "text": text}],
    }

def _err(reason: str, model: str, provider: str, duration_ms: int) -> Dict:
    return {
        "ok": False,
        "text": "",
        "model": model,
        "provider": provider,
        "error": reason,
        "duration_ms": duration_ms,
        "fallback_used": False,
        "content": [],
    }

# ── Anthropic ────────────────────────────────────────────────────────────────
def _call_anthropic(model: str, api_key: str, api_base: str,
                    system: str, messages: list, max_tokens: int = 1500) -> Dict:
    t0  = time.time()
    url = f"{api_base.rstrip('/')}/messages"
    payload = json.dumps({
        "model": model, "max_tokens": max_tokens,
        "system": system, "messages": messages
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        text = data.get("content", [{}])[0].get("text", "")
        dur  = round((time.time() - t0) * 1000)
        return _norm(text, model, "anthropic", dur, data.get("usage", {}))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return _err(f"HTTP {e.code}: {body[:200]}", model, "anthropic", round((time.time()-t0)*1000))
    except Exception as e:
        return _err(str(e), model, "anthropic", round((time.time()-t0)*1000))

# ── OpenAI-compatible (also covers local Ollama) ─────────────────────────────
def _call_openai_compat(model: str, api_key: Optional[str], api_base: str,
                         system: str, messages: list, max_tokens: int = 1500) -> Dict:
    t0  = time.time()
    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages
    }).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        dur  = round((time.time() - t0) * 1000)
        tok  = {"input_tokens": data.get("usage", {}).get("prompt_tokens"),
                "output_tokens": data.get("usage", {}).get("completion_tokens")}
        return _norm(text, model, "openai_compatible", dur, tok)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return _err(f"HTTP {e.code}: {body[:200]}", model, "openai_compatible", round((time.time()-t0)*1000))
    except Exception as e:
        return _err(str(e), model, "openai_compatible", round((time.time()-t0)*1000))

# ── Main entry point ──────────────────────────────────────────────────────────
def run_agent_model(agent_def: Dict, system_prompt: str, messages: list,
                    project_root: str = "", max_tokens: int = 1500) -> Dict:
    """
    Full model resolution → API call → normalised response.
    Attempts fallback_model if primary fails.
    """
    env          = load_env(project_root) if project_root else _env_vars
    model, prov_key, prov_cfg = resolve_model(agent_def)
    prov_type    = prov_cfg.get("type", prov_key)
    api_base     = prov_cfg.get("api_base") or prov_cfg.get("base_url", "https://api.anthropic.com/v1")
    api_key      = resolve_api_key(prov_cfg, env)

    # Validate key if required
    if prov_type not in ("local",) and not api_key:
        env_var = prov_cfg.get("api_key_env") or prov_cfg.get("env_var", "?")
        return _err(f"API key missing — set {env_var} in .env", model, prov_key, 0)

    # Primary call
    if prov_type == "anthropic":
        result = _call_anthropic(model, api_key, api_base, system_prompt, messages, max_tokens)
    else:
        result = _call_openai_compat(model, api_key, api_base, system_prompt, messages, max_tokens)

    if result["ok"]:
        return result

    # Fallback
    fallback_model = _project.get("model", {}).get("fallback_model")
    if fallback_model and fallback_model != model:
        fallback_def = {**agent_def, "model_override": fallback_model}
        fb_model, fb_prov_key, fb_prov_cfg = resolve_model(fallback_def)
        fb_type    = fb_prov_cfg.get("type", fb_prov_key)
        fb_base    = fb_prov_cfg.get("api_base") or fb_prov_cfg.get("base_url", api_base)
        fb_key     = resolve_api_key(fb_prov_cfg, env) or api_key
        if fb_type == "anthropic":
            fb_result = _call_anthropic(fb_model, fb_key, fb_base, system_prompt, messages, max_tokens)
        else:
            fb_result = _call_openai_compat(fb_model, fb_key, fb_base, system_prompt, messages, max_tokens)
        if fb_result["ok"]:
            fb_result["fallback_used"] = True
            fb_result["fallback_reason"] = result.get("error", "primary failed")
            return fb_result

    return result

def get_provider_status(project_root: str = "") -> Dict:
    """Returns provider health — whether API keys are present (not the keys themselves)."""
    env      = load_env(project_root) if project_root else _env_vars
    providers = _project.get("model", {}).get("providers", {})
    status   = {}
    for prov_key, prov_cfg in providers.items():
        prov_type = prov_cfg.get("type", prov_key)
        env_var   = prov_cfg.get("api_key_env") or prov_cfg.get("env_var")
        if prov_type == "local":
            status[prov_key] = {"type": "local", "key_required": False, "key_present": True,
                                "base_url": prov_cfg.get("base_url", "")}
        else:
            key_present = bool(env.get(env_var)) if env_var else False
            status[prov_key] = {"type": prov_type, "key_required": True,
                                "key_present": key_present, "env_var": env_var}
    return status