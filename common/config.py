"""配置加载:config/env.yaml 为基线,config/env.local.yaml 覆盖敏感项。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _resolve_db_host(host: str) -> str:
    """auto-wsl:通过 wsl 命令解析 WSL2 eth0 IP(容器 3306 仅绑定在 WSL 侧)。"""
    if host != "auto-wsl":
        return host
    import subprocess
    out = subprocess.run(["wsl", "-d", "Ubuntu", "hostname", "-I"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="ignore", timeout=15).stdout
    ip = out.split()[0] if out.split() else ""
    if not ip:
        raise RuntimeError("auto-wsl 解析失败:请确认 WSL Ubuntu 在运行,或在 env.local.yaml 写死 db.host")
    return ip


def load_config() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "env.yaml").read_text(encoding="utf-8"))
    local = ROOT / "config" / "env.local.yaml"
    if local.exists():
        override = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    cfg["db"]["host"] = _resolve_db_host(cfg["db"]["host"])
    return cfg


CFG = load_config()
