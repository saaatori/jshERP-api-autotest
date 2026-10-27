"""动态测试数据生成:业务 ID 一律动态生成,禁止硬编码(用例设计规范 6.6)。

DEF-EXP-07:商品删除后 jsh_material_extend 条码仍占用,条码必须唯一且不可复用。
"""
import uuid


def exp_id(prefix: str = "EXPF") -> str:
    """生成 EXPF+6位随机串;同一链路内可继续拼接后缀保证可读与唯一。"""
    return f"{prefix}{uuid.uuid4().hex[:6]}"
