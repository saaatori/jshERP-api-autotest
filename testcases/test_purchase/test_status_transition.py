"""采购状态迁移与转单链(API-PUR-009~016):审核/反审核/删除/转单状态机。

2026-10-27 取证:
- batchSetStatus 的 ids 必须为带尾逗号字符串(数组形式"未知异常");
- 部分转单→源单 status=3,转完→2,超量转单放行且累计可超订单量(R3,over_link_bill_flag=0);
- 已审核【入库单】删除被 8500011 拦截;但已审核【订单】删除实测放行——与用例集 PUR-012
  预期矛盾,属状态机拦截的单据类型差异,按实测断言并记缺陷差异(F6 修订项)。
顺序链用例(编号即执行顺序),类内共享转单数据,teardown 统一清理。
"""
import json
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
def order_chain(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "depot": depot, "acc": acc, "mat": mat, "bill": DepotHeadApi(c),
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    assert_code_ok(sup.add(f"{stamp}s", "供应商"), "供应商")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a"), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["supplier_id"] = sup.find_id_by_name(f"{stamp}s")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def mk_order(no, num=10):
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "其它", "subType": "采购订单", "organId": s["supplier_id"], "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 10, "allPrice": num * 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), f"订单{no}")
        detail = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(detail["id"])
        return detail["id"]

    def convert_from(source_id, num, link_no):
        item_id = s["bill"].item_list(source_id).json()["data"]["rows"][0]["id"]
        no = "CGRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                "accountId": s["account_id"], "linkNumber": link_no,
                "changeAmount": 0, "discountLastMoney": num * 10, "totalPrice": -(num * 10),
                "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 10, "allPrice": num * 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 10, "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), f"转单{no}")
        detail = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(detail["id"])
        return detail["id"]

    s["mk_order"], s["convert_from"] = mk_order, convert_from
    s["order_no"] = "CGDD" + s["bill"].build_number()
    s["order_id"] = mk_order(s["order_no"], 10)
    yield s
    # teardown:未审核的直接删,已审核的先反审核(F6)
    for bid in list(dict.fromkeys(s["bill_ids"])):
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) == "1":
            s["bill"].batch_set_status("0", [bid])
        s["bill"].delete_batch([bid])
    mat.delete_batch([s["material_id"]])
    sup.delete_batch([s["supplier_id"]])
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


def _status(s, bill_id):
    return str(s["db"].query_one(
        "SELECT status FROM jsh_depot_head WHERE id=%s", (bill_id,))["status"])


@allure.feature("采购管理")
@pytest.mark.regression
@pytest.mark.usefixtures("env_check")
class TestPurchaseTransition:

    @allure.title("API-PUR-010 未审核订单反审核被拒(8500013)")
    def test_01_reverse_unapproved(self, order_chain):
        s = order_chain
        body = s["bill"].batch_set_status("0", [s["order_id"]]).json()
        assert body["code"] == 8500013, f"期望 8500013,实际: {body}"

    @allure.title("API-PUR-009 订单审核状态迁移 0→1,DB 落库")
    def test_02_approve(self, order_chain):
        s = order_chain
        assert_code_ok(s["bill"].batch_set_status("1", [s["order_id"]]), "审核")
        assert _status(s, s["order_id"]) == "1"

    @allure.title("API-SALE-019对称 重复审核:状态保持已审核不变")
    def test_03_repeat_approve(self, order_chain):
        s = order_chain
        body = s["bill"].batch_set_status("1", [s["order_id"]]).json()
        assert _status(s, s["order_id"]) == "1", f"重复审核后状态异常: {body}"

    @allure.title("API-PUR-014 订单部分转单:源单→3(部分执行),库存+6")
    def test_04_partial_convert(self, order_chain, db):
        s = order_chain
        s["in_partial"] = s["convert_from"](s["order_id"], 6, s["order_no"])
        assert _status(s, s["order_id"]) == "3", "部分转单后源单应为 3(R3)"
        stock = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        assert float(stock["current_number"]) == 6.0, f"库存应+6,实际 {stock}"

    @allure.title("API-PUR-015 剩余量转完:源单→2(全部完成)")
    def test_05_full_convert(self, order_chain):
        s = order_chain
        s["in_rest"] = s["convert_from"](s["order_id"], 4, s["order_no"])
        assert _status(s, s["order_id"]) == "2"

    @allure.title("API-PUR-016 超量转单放行:累计16>订单10,源单保持2(R3 实测)")
    def test_06_over_convert(self, order_chain):
        s = order_chain
        s["in_over"] = s["convert_from"](s["order_id"], 5, s["order_no"])
        assert _status(s, s["order_id"]) == "2", "超量转单不应改变完成态"

    @allure.title("API-PUR-013 已完成订单反审核被拒(8500013,F6)")
    def test_07_reverse_completed(self, order_chain):
        s = order_chain
        body = s["bill"].batch_set_status("0", [s["order_id"]]).json()
        assert body["code"] == 8500013, f"期望 8500013,实际: {body}"

    @allure.title("API-PUR-012 已审核入库单删除被拒(8500011,F6)")
    def test_12_delete_approved_inbound(self, order_chain):
        s = order_chain
        in_id = s.get("in_partial") or s.get("in_rest")
        s["bill"].batch_set_status("1", [in_id])
        body = s["bill"].delete_batch([in_id]).json()
        assert body["code"] == 8500011, f"期望 8500011,实际: {body}"

    @allure.title("API-PUR-011 未审核入库单删除成功(逻辑删除)")
    def test_11_delete_unapproved(self, order_chain, db):
        s = order_chain
        target = s["in_over"]
        assert_code_ok(s["bill"].delete_batch([target]), "删除")
        row = db.query_one("SELECT delete_flag FROM jsh_depot_head WHERE id=%s", (target,))
        assert str(row["delete_flag"]) == "1", f"应逻辑删除,实际 {row}"
        s["bill_ids"].remove(target)

    @allure.title("API-PUR-012 已审核订单删除同样被拒(8500011,F6)")
    def test_12b_delete_approved_order(self, order_chain):
        """订单与入库单删除拦截一致(8500011)。注:此前探针记录的"订单放行"
        系探针脚本先反审核所致的误判,2026-10-27 单测复核订正。"""
        s = order_chain
        extra_no = "CGDD" + s["bill"].build_number()
        extra_id = s["mk_order"](extra_no, 1)
        s["bill"].batch_set_status("1", [extra_id])
        body = s["bill"].delete_batch([extra_id]).json()
        assert body.get("code") == 8500011, f"期望 8500011,实际: {body}"
