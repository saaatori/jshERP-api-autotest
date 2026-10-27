class DepotApi:
    """仓库管理。"""

    def __init__(self, client):
        self.c = client

    def add(self, name: str):
        return self.c.post("/depot/add", json_body={
            "name": name, "sort": "", "remark": "API自动化", "isDefault": False})

    def get_all_list(self):
        return self.c.get("/depot/getAllList")

    def find_id_by_name(self, name: str):
        rows = self.get_all_list().json()["data"]
        hit = [r for r in rows if r.get("name") == name]
        return hit[0]["id"] if hit else None

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.delete("/depot/deleteBatch", params={"ids": id_str})
