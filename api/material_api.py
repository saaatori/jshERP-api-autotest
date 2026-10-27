class MaterialApi:
    """商品管理:新增/查询/删除(报文结构与 Postman 冒烟集一致)。"""

    def __init__(self, client):
        self.c = client

    def add(self, name: str, bar_code: str, purchase="10", retail="20", low="20", unit="个"):
        return self.c.post("/material/add", json_body={
            "name": name, "unit": unit, "mnemonic": "EXPFsp", "imgName": "",
            "meList": [{"id": "", "barCode": bar_code, "commodityUnit": unit, "sku": "",
                        "purchaseDecimal": purchase, "commodityDecimal": retail,
                        "wholesaleDecimal": "", "lowDecimal": low}],
            "stock": [], "sortList": [], "unitId": "", "meDeleteIdList": []})

    def list(self, page: int = 1, size: int = 50):
        return self.c.get("/material/list", params={"currentPage": page, "pageSize": size})

    def find_by_name(self, name: str):
        """列表客户端过滤定位(规避 search URL 编码坑,Postman 已验证)。返回整行或 None。"""
        rows = self.list().json()["data"]["rows"]
        hit = [r for r in rows if r["name"] == name]
        return hit[0] if hit else None

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.delete("/material/deleteBatch", params={"ids": id_str})
