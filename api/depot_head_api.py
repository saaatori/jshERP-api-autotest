"""单据管理:通用单据接口(info/rows 为 JSON 字符串,报文模板与 Postman 冒烟集一致)。

注意:入库单 totalPrice/changeAmount 为负值、出库单为正值;ids 参数带尾逗号。
"""
import json


class DepotHeadApi:
    def __init__(self, client):
        self.c = client

    def build_number(self) -> str:
        """取单据编号(jsh_sequence),返回 defaultNumber,调用方拼接 CGRK/XSCK 等前缀。"""
        resp = self.c.get("/sequence/buildNumber")
        return resp.json()["data"]["defaultNumber"]

    def add(self, info: dict, rows: list):
        return self.c.post("/depotHead/addDepotHeadAndDetail", json_body={
            "info": json.dumps(info, ensure_ascii=False),
            "rows": json.dumps(rows, ensure_ascii=False)})

    def get_detail_by_number(self, number: str):
        return self.c.get("/depotHead/getDetailByNumber", params={"number": number})

    def batch_set_status(self, status: str, ids):
        """审核/反审核。实测 ids 必须为带尾逗号的字符串,数组形式报"未知异常"。"""
        id_str = "".join(f"{i}," for i in ids)
        return self.c.post("/depotHead/batchSetStatus",
                           json_body={"status": status, "ids": id_str})

    def item_list(self, header_id):
        """源单明细(转单取 linkId 用)。"""
        return self.c.get("/depotItem/getDetailList", params={"headerId": header_id})

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.delete("/depotHead/deleteBatch", params={"ids": id_str})
