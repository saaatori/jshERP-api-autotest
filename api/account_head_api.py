"""财务管理:收入/支出/收款/付款/转账/收预付款共用 accountHead 接口。

报文与 depotHead 同构:info/rows 为 JSON 字符串;billNo 需自带前缀(FKD/SKD/YSD/ZCD+取号);
明细 eachAmount 存负值(付款)/正值(收款);优惠走 info.discountMoney。
"""
import json


class AccountHeadApi:
    def __init__(self, client):
        self.c = client

    def build_number(self) -> str:
        return self.c.get("/sequence/buildNumber").json()["data"]["defaultNumber"]

    def add(self, info: dict, rows: list):
        return self.c.post("/accountHead/addAccountHeadAndDetail", json_body={
            "info": json.dumps(info, ensure_ascii=False),
            "rows": json.dumps(rows, ensure_ascii=False)})

    def get_detail_by_number(self, bill_no: str):
        return self.c.get("/accountHead/getDetailByNumber", params={"billNo": bill_no})

    def batch_set_status(self, status: str, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.post("/accountHead/batchSetStatus",
                           json_body={"status": status, "ids": id_str})

    def delete_batch(self, ids):
        id_str = "".join(f"{i}," for i in ids)
        return self.c.delete("/accountHead/deleteBatch", params={"ids": id_str})
