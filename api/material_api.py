class MaterialApi:
    """商品管理:新增/查询/删除(报文结构与 Postman 冒烟集一致)。"""

    def __init__(self, client):
        self.c = client

    def add(self, name: str, bar_code: str, purchase="10", retail="20", low="20", unit="个",
            unit_id=None, extra_bars=None):
        """新增商品。unit_id 挂多单位;extra_bars 为附加条码行(多单位副单位,如箱条码)。"""
        me_list = [{"id": "", "barCode": bar_code, "commodityUnit": unit, "sku": "",
                    "purchaseDecimal": purchase, "commodityDecimal": retail,
                    "wholesaleDecimal": "", "lowDecimal": low}]
        for extra in extra_bars or []:
            me_list.append({"id": "", "barCode": extra["bar_code"],
                            "commodityUnit": extra["unit"], "sku": "",
                            "purchaseDecimal": extra.get("purchase", ""),
                            "commodityDecimal": extra.get("retail", ""),
                            "wholesaleDecimal": "", "lowDecimal": extra.get("low", "")})
        return self.c.post("/material/add", json_body={
            "name": name, "unit": unit, "mnemonic": "EXPFsp", "imgName": "",
            "meList": me_list,
            "stock": [], "sortList": [], "unitId": unit_id or "", "meDeleteIdList": []})

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
