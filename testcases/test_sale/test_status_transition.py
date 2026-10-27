"""销售状态迁移(API-SALE-002/003/004/018/019):与采购链对称 + 删除库存回退。

取证(2026-10-27):已审核销售订单删除实测放行(与采购订单一致,与用例集 SALE-004
预期 8500011 矛盾,按单据类型差异记录);未审核出库单删除后库存实时回退(F7)。
"""
from datetime import datetime

import allure
import pytest

from api.account_api import AccountApi
from api.depot_api import DepotApi
from api.depot_head_api import DepotHeadApi
from api.material_api import MaterialApi
from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def sale_chain(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "depot": depot, "acc": acc, "mat": mat, "bill": DepotHeadApi(c),
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    assert_code_ok(sup.add(f"{stamp}cus", "客户"), "客户")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a"), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["customer_id"] = sup.find_id_by_name(f"{stamp}cus")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def mk_sale_order(no, num=10):
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "其它", "subType": "销售订单", "organId": s["customer_id"], "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 20, "allPrice": num * 20, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 20, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), f"销售订单{no}")
        return s["bill"].get_detail_by_number(no).json()["data"]["id"]

    def mk_out(no, num, extra=None, link_no=None, link_item=None):
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "销售", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": 0,
                "discountLastMoney": num * 20, "totalPrice": num * 20, "status": "0"}
        if link_no:
            info["linkNumber"] = link_no
        if extra:
            info.update(extra)
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 20, "allPrice": num * 20, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 20, "unit": "个"}]
        if link_item:
            rows[0]["linkId"] = link_item
        assert_code_ok(s["bill"].add(info, rows), f"出库单{no}")
        return s["bill"].get_detail_by_number(no).json()["data"]["id"]

    def stock_now():
        row = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        return float(row["current_number"]) if row else 0.0

    s.update({"mk_sale_order": mk_sale_order, "mk_out": mk_out, "stock_now": stock_now})
    s["order_no"] = "XSDD" + s["bill"].build_number()
    s["order_id"] = mk_sale_order(s["order_no"], 10)
    yield s
    for bid in list(dict.fromkeys(s["bill_ids"])):
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) == "1":
            s["bill"].batch_set_status("0", [bid])
        s["bill"].delete_batch([bid])
    mat.delete_batch([s["material_id"]])
    sup.delete_batch([s["customer_id"]])
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


def _status(s, bill_id):
    return str(s["db"].query_one(
        "SELECT status FROM jsh_depot_head WHERE id=%s", (bill_id,))["status"])


@allure.feature("销售管理")
@pytest.mark.regression
@pytest.mark.usefixtures("env_check")
class TestSaleTransition:

    @allure.title("API-SALE-002 销售订单审核 0→1,订单不动库存")
    def test_01_approve(self, sale_chain):
        s = sale_chain
        before = s["stock_now"]()
        assert_code_ok(s["bill"].batch_set_status("1", [s["order_id"]]), "审核")
        assert _status(s, s["order_id"]) == "1"
        assert s["stock_now"]() == before, "订单审核不应影响库存(R4 对称)"

    @allure.title("API-SALE-019 重复审核:状态保持已审核不变")
    def test_02_repeat_approve(self, sale_chain):
        s = sale_chain
        s["bill"].batch_set_status("1", [s["order_id"]])
        assert _status(s, s["order_id"]) == "1", "重复审核后应保持已审核"

    @allure.title("API-SALE-003 订单部分转出库:源单→3,出库单 link_number 关联")
    def test_03_partial_convert(self, sale_chain, db):
        s = sale_chain
        item_id = s["bill"].item_list(s["order_id"]).json()["data"]["rows"][0]["id"]
        no = "XSCK" + s["bill"].build_number()
        s["out_partial"] = s["mk_out"](no, 6, link_no=s["order_no"], link_item=item_id)
        assert _status(s, s["order_id"]) == "3", "部分转出库后源单应为 3"
        row = db.query_one("SELECT link_number FROM jsh_depot_head WHERE id=%s", (s["out_partial"],))
        assert row["link_number"] == s["order_no"], f"link_number 关联异常: {row}"

    @allure.title("API-SALE-018 未审核出库单删除:逻辑删除且库存实时回退(F7)")
    def test_04_delete_outbound_stock_revert(self, sale_chain, db):
        s = sale_chain
        no = "XSCK" + s["bill"].build_number()
        out_id = s["mk_out"](no, 2)
        before = s["stock_now"]()
        assert_code_ok(s["bill"].delete_batch([out_id]), "删除出库单")
        row = db.query_one("SELECT delete_flag FROM jsh_depot_head WHERE id=%s", (out_id,))
        assert str(row["delete_flag"]) == "1"
        assert s["stock_now"]() == before + 2, f"删除后库存应回退+2,实际 {s['stock_now']()}"

    @allure.title("API-SALE-004 已审核销售订单删除被拒(8500011,F6)")
    def test_05_delete_approved_order(self, sale_chain):
        s = sale_chain
        extra_no = "XSDD" + s["bill"].build_number()
        extra_id = s["mk_sale_order"](extra_no, 1)
        s["bill"].batch_set_status("1", [extra_id])
        body = s["bill"].delete_batch([extra_id]).json()
        assert body.get("code") == 8500011, f"期望 8500011,实际: {body}"
