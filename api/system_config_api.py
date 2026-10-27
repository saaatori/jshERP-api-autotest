"""系统配置:租户级业务开关(minus_stock_flag 等)经 getCurrentInfo+update 读写。

实测(2026-10-27):PUT /systemConfig/update 以全量对象提交,字段驼峰(minusStockFlag),
DB jsh_system_config 立即生效。用例切换开关后必须恢复。
"""


class SystemConfigApi:
    def __init__(self, client):
        self.c = client

    def get_current_info(self) -> dict:
        return self.c.get("/systemConfig/getCurrentInfo").json()["data"]

    def update_field(self, field: str, value) -> dict:
        """读全量-改单字段-全量提交,返回更新后响应。"""
        obj = self.get_current_info()
        obj[field] = value
        return self.c.put("/systemConfig/update", json_body=obj).json()
