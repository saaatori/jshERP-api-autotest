class AccountApi:
    """结算账户。"""

    def __init__(self, client):
        self.c = client

    def add(self, name: str, initial=0):
        return self.c.post("/account/add", json_body={
            "name": name, "serialNo": "", "initialAmount": initial,
            "currentAmount": initial, "remark": "API自动化", "isDefault": False})

    def find_by_select(self):
        """下拉数据接口,返回 [{"Id":..,"AccountName":..}](键名为大写)。"""
        return self.c.get("/account/findBySelect")

    def find_id_by_name(self, name: str):
        rows = self.find_by_select().json()
        hit = [r for r in rows if r.get("AccountName") == name]
        return hit[0]["Id"] if hit else None

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.delete("/account/deleteBatch", params={"ids": id_str})
