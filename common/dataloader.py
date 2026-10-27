"""yaml 用例数据加载:用例数据与代码分离(测试计划 3.3 技术约定 3)。"""
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_cases(name: str) -> list:
    """读取 data/<name>.yaml,返回用例列表(供 pytest.mark.parametrize 展开)。"""
    return yaml.safe_load((DATA_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
