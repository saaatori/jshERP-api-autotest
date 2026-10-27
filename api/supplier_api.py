import json


class SupplierApi:
    """商家管理:供应商/客户/会员三类往来单位共用 jsh_supplier(type 区分)。

    提取 ID 走 GET /supplier/list + search(实测可用);findBySelect_* 系列缺参会
    "未知异常"(NPE,待抓包确认参数后另测)。
    """

    def __init__(self, client):
        self.c = client

    def add(self, name: str, type_: str, contacts: str = "", telephone: str = ""):
        return self.c.post("/supplier/add", json_body={
            "supplier": name, "type": type_, "contacts": contacts or name,
            "telephone": telephone, "email": "", "description": "API自动化",
            "enabled": True, "advanceIn": 0, "beginNeedGet": 0, "beginNeedPay": 0,
            "taxRate": 0, "isSystem": False})

    def list(self, name: str = ""):
        return self.c.get("/supplier/list", params={
            "search": json.dumps({"supplier": name}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50})

    def find_rows_by_name(self, name: str):
        rows = self.list(name).json()["data"]["rows"]
        return [r for r in rows if r["supplier"] == name]

    def find_id_by_name(self, name: str):
        rows = self.find_rows_by_name(name)
        return rows[0]["id"] if rows else None

    def find_ids_by_name(self, name: str):
        """同名可能有多条(无唯一性校验缺陷),清理时需全删。"""
        return [r["id"] for r in self.find_rows_by_name(name)]

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)  # 尾逗号写法与页面一致
        return self.c.delete("/supplier/deleteBatch", params={"ids": id_str})
