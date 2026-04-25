"""
RockoAgents Model Manager
Resolves model + provider per agent, loads credentials, calls API, handles fallback.
Never hardcodes model names. All config comes from project.json + .env
"""
import json, os, time, urllib.request, urllib.error
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# -- Globals set by bridge on load ---------------------------------------------
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

# -- Model resolution ----------------------------------------------------------
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

# -- Normalised response -------------------------------------------------------
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

# -- Anthropic ----------------------------------------------------------------
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

# -- OpenAI-compatible (also covers local Ollama) -----------------------------
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

# -- Main entry point ----------------------------------------------------------
def run_agent_model(agent_def: Dict, system_prompt: str, messages: list,
                    project_root: str = "", max_tokens: int = 1500) -> Dict:
    """
    Full model resolution -> API call -> normalised response.
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
        return _err(f"API key missing - set {env_var} in .env", model, prov_key, 0)

    # Primary call
    # nvidia uses openai_compatible format with their own base URL
    # Ensure NVIDIA base URL is set correctly if not in config
    if prov_type == "nvidia" or prov_key == "nvidia":
        prov_type = "openai_compatible"
        if not api_base or "anthropic" in api_base:
            api_base = "https://integrate.api.nvidia.com/v1"

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

# Built-in provider registry - shown even if not in project.json
BUILTIN_PROVIDERS = {
    "anthropic": {
        "type": "anthropic",
        "display_name": "Anthropic",
        "api_base": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "key_required": True,
        "docs_url": "https://console.anthropic.com/",
    },
    "openai": {
        "type": "openai_compatible",
        "display_name": "OpenAI",
        "api_base": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "key_required": True,
        "docs_url": "https://platform.openai.com/",
    },
    "nvidia": {
        "type": "openai_compatible",
        "display_name": "NVIDIA",
        "api_base": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "key_required": True,
        "user_owned": True,  # user brings their own key - RockoAgents provides no NVIDIA access
        "docs_url": "https://build.nvidia.com/",
        "available_models": [
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1",
            "mistralai/mixtral-8x7b-instruct-v0.1",
            "google/gemma-3-27b-it",
            "microsoft/phi-4",
            "qwen/qwen2.5-72b-instruct",
        ],
        "note": "Users must supply their own NVIDIA_API_KEY. RockoAgents does not provide NVIDIA model access.",
    },
    "local": {
        "type": "openai_compatible",
        "display_name": "Ollama (Local)",
        "api_base": "http://localhost:11434/v1",
        "api_key_env": None,
        "key_required": False,
        "docs_url": "https://ollama.ai/",
        "local": True,
    },
    "lmstudio": {
        "type": "openai_compatible",
        "display_name": "LM Studio (Local)",
        "api_base": "http://localhost:1234/v1",
        "api_key_env": None,
        "key_required": False,
        "docs_url": "https://lmstudio.ai/",
        "local": True,
    },
    "gemini": {
        "type": "openai_compatible",
        "display_name": "Google Gemini",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GEMINI_API_KEY",
        "key_required": True,
        "docs_url": "https://aistudio.google.com/",
        "available_models": [
            "gemini-2.0-flash",
            "gemini-2.5-pro-preview-05-06",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
    },
    "custom": {
        "type": "openai_compatible",
        "display_name": "Custom Endpoint",
        "api_base": "",
        "api_key_env": "CUSTOM_API_KEY",
        "key_required": False,
        "docs_url": "",
        "custom": True,
    },
}

def get_provider_status(project_root: str = "") -> Dict:
    """Returns provider health - whether API keys are present (not the keys themselves)."""
    env       = load_env(project_root) if project_root else _env_vars
    providers = _project.get("model", {}).get("providers", {})
    status    = {}

    # Start with built-in providers so they always appear in UI
    for prov_key, builtin in BUILTIN_PROVIDERS.items():
        env_var     = builtin.get("api_key_env")
        key_present = bool(env.get(env_var)) if env_var else True
        entry = {
            "type":         builtin["type"],
            "display_name": builtin["display_name"],
            "key_required": builtin["key_required"],
            "key_present":  key_present,
            "env_var":      env_var,
            "api_base":     builtin["api_base"],
            "user_owned":   builtin.get("user_owned", False),
            "note":         builtin.get("note", ""),
        }
        if "available_models" in builtin:
            entry["available_models"] = builtin["available_models"]
        status[prov_key] = entry

    # Overlay with project-configured providers (may override or add custom)
    for prov_key, prov_cfg in providers.items():
        prov_type = prov_cfg.get("type", prov_key)
        env_var   = prov_cfg.get("api_key_env") or prov_cfg.get("env_var")
        key_present = (True if prov_type == "local"
                       else bool(env.get(env_var)) if env_var else False)
        status[prov_key] = {
            **status.get(prov_key, {}),
            "type":         prov_type,
            "key_required": prov_type != "local",
            "key_present":  key_present,
            "env_var":      env_var,
            "api_base":     prov_cfg.get("api_base") or prov_cfg.get("base_url", ""),
            "configured_in_project": True,
        }
        if prov_cfg.get("available_models"):
            status[prov_key]["available_models"] = prov_cfg["available_models"]

    return status


def validate_provider(provider_id: str, project_root: str = "",
                       base_url_override: str = "") -> Dict:
    """
    Validate a provider can actually work.
    Returns {ok, status, message, env_var, key_present, reachable}
    """
    env      = load_env(project_root) if project_root else _env_vars
    builtin  = BUILTIN_PROVIDERS.get(provider_id, {})
    providers = _project.get("model", {}).get("providers", {})
    proj_cfg  = providers.get(provider_id, {})

    cfg = {**builtin, **proj_cfg}
    if base_url_override:
        cfg["api_base"] = base_url_override

    is_local    = cfg.get("local") or not cfg.get("key_required")
    env_var     = cfg.get("api_key_env")
    key_present = bool(env.get(env_var)) if env_var else True
    api_base    = cfg.get("api_base", "")

    result = {
        "ok":          False,
        "provider_id": provider_id,
        "display_name": cfg.get("display_name", provider_id),
        "env_var":     env_var,
        "key_present": key_present,
        "reachable":   False,
        "status":      "unknown",
        "message":     "",
    }

    if not is_local and not key_present:
        result["status"]  = "missing_key"
        result["message"] = f"API key missing. Add {env_var} to your .env file."
        return result

    # Try to reach the endpoint
    if api_base:
        import urllib.request as _ur
        headers = {}
        if env_var and env.get(env_var):
            headers["Authorization"] = f"Bearer {env.get(env_var)}"
        try:
            test_url = api_base.rstrip("/") + "/models"
            req = _ur.Request(test_url, headers=headers)
            with _ur.urlopen(req, timeout=6) as r:
                r.read()
            result["reachable"] = True
            result["ok"]        = True
            result["status"]    = "connected"
            result["message"]   = "Provider connected and responding."
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "403" in err_str:
                result["status"]  = "auth_failed"
                result["message"] = "API key rejected. Check your key is correct."
            elif is_local or "refused" in err_str.lower() or "connection" in err_str.lower():
                result["status"]  = "offline"
                result["message"] = f"Local server not running at {api_base}. Start Ollama or LM Studio first."
            else:
                result["status"]  = "unreachable"
                result["message"] = f"Could not reach {api_base}: {err_str[:120]}"
    else:
        if provider_id == "custom":
            result["status"]  = "needs_config"
            result["message"] = "Enter your custom endpoint URL."
        else:
            result["status"]  = "configure_later"
            result["message"] = "Provider will be configured later."
            result["ok"]      = True

    return result