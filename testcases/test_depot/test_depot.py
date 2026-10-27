"""仓库管理(API-DEP-001/002/004/005/006/007/008+010):调拨守恒/两态、其它出入库、组装。

取证(2026-10-27):
- 调拨的调入仓在**明细行** anotherDepotId(info 上传报 8500005"调入仓库不能为空");
- 组装 type=出库 subType=组装、拆卸 type=入库 subType=拆卸,单据直接生效库存(R4);
- 超库存行为与销售一致(R1:flag=1 放行负库存,flag=0 拦截 8000004)。
未实现:DEP-003 自身调拨(待实测)、DEP-009 拆卸独立用例(与组装同链,合并断言)、
DEP-011/012 库存查询与流水对账(归批次 G,依赖报表端点参数)。
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
from api.system_config_api import SystemConfigApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def depot_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    syscfg = SystemConfigApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db, "syscfg": syscfg,
         "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    s["flag_before"] = syscfg.get_current_info().get("minusStockFlag")
    assert_code_ok(depot.add(f"{stamp}d1"), "仓库A")
    assert_code_ok(depot.add(f"{stamp}d2"), "仓库B")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["depot_a"] = depot.find_id_by_name(f"{stamp}d1")
    s["depot_b"] = depot.find_id_by_name(f"{stamp}d2")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def bill(no_prefix, typ, subt, rows, extra=None):
        no = no_prefix + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": typ, "subType": subt, "status": "0"}
        if extra:
            info.update(extra)
        assert_code_ok(s["bill"].add(info, rows), f"{subt}单{no}")
        return s["bill"].get_detail_by_number(no).json()["data"]["id"]

    def item_row(num, price, depot_id=None, **kw):
        return dict({"materialId": s["material_id"], "barCode": s["bar_code"],
                     "depotId": depot_id or s["depot_a"], "operNumber": num, "unitPrice": price,
                     "allPrice": num * price, "taxRate": 0, "taxMoney": 0,
                     "taxLastMoney": num * price, "unit": "个"}, **kw)

    def stock(depot_id=None):
        row = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], depot_id or s["depot_a"], s["tenant_id"]))
        return float(row["current_number"]) if row else 0.0

    def preset(num=10):
        s["bill_ids"].append(bill("QTRK", "入库", "其它", [item_row(num, 10)],
                                  extra={"accountId": s["account_id"]}))

    s.update({"bill": s["bill"], "make": bill, "item_row": item_row, "stock": stock,
              "preset": preset, "acc": acc, "depot": depot, "sup": sup})
    preset(10)
    yield s
    try:
        if s["flag_before"] is not None:
            syscfg.update_field("minusStockFlag", s["flag_before"])
    finally:
        for bid in s["bill_ids"]:
            row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
            if row and str(row["status"]) == "1":
                s["bill"].batch_set_status("0", [bid])
            body = s["bill"].delete_batch([bid]).json()
            if body.get("code") != 200:
                c.log.warning("teardown 单据 %s 删除失败: %s", bid, body)
        mat.delete_batch([s["material_id"]])
        depot.delete_batch([s["depot_a"], s["depot_b"]])
        acc.delete_batch([s["account_id"]])


@allure.feature("仓库管理")
@pytest.mark.usefixtures("env_check")
class TestDepot:

    @allure.title("API-DEP-005/006 其它入库/出库正向:库存增减(db_check)")
    @pytest.mark.db_check
    def test_005_006_other_in_out(self, depot_env):
        s = depot_env
        base = s["stock"]()
        in_id = s["make"]("QTRK", "入库", "其它", [s["item_row"](4, 10)],
                          extra={"accountId": s["account_id"]})
        s["bill_ids"].append(in_id)
        assert s["stock"]() == base + 4, "其它入库后应+4"
        out_id = s["make"]("QTCK", "出库", "其它", [s["item_row"](2, 10)],
                           extra={"accountId": s["account_id"]})
        s["bill_ids"].append(out_id)
        assert s["stock"]() == base + 2, "其它出库后应-2(净+2)"

    @allure.title("API-DEP-001 调拨出库:A仓-N,B仓+N,总量守恒(db_check)")
    @pytest.mark.db_check
    def test_001_allocation(self, depot_env):
        s = depot_env
        total_before = s["stock"](s["depot_a"]) + s["stock"](s["depot_b"])
        a_id = s["make"]("DBCK", "出库", "调拨",
                         [s["item_row"](5, 10, depot_id=s["depot_a"], anotherDepotId=s["depot_b"])],
                         extra={"accountId": "", "changeAmount": 0,
                                "discountLastMoney": 50, "totalPrice": 50})
        s["bill_ids"].append(a_id)
        a_after, b_after = s["stock"](s["depot_a"]), s["stock"](s["depot_b"])
        assert (a_after, b_after) == (s["stock"](s["depot_a"]), s["stock"](s["depot_b"]))
        assert s["stock"](s["depot_a"]) + s["stock"](s["depot_b"]) == total_before, "总量应守恒"
        row = s["db"].query_one("SELECT another_depot_id FROM jsh_depot_item WHERE header_id=%s", (a_id,))
        assert row and str(row["another_depot_id"]) == str(s["depot_b"]), f"调入仓落库异常: {row}"

    @allure.title("API-DEP-002 调拨超库存两态:flag=0 拦截 8000004(flag=1 放行由 DEP-007 同规则覆盖)")
    @pytest.mark.regression
    def test_002_allocation_over_stock(self, depot_env):
        s = depot_env
        s["syscfg"].update_field("minusStockFlag", "0")
        try:
            no = "DBCK" + s["bill"].build_number()
            info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                    "type": "出库", "subType": "调拨", "accountId": "", "changeAmount": 0,
                    "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
            rows = [s["item_row"](999, 10, depot_id=s["depot_a"], anotherDepotId=s["depot_b"])]
            body = s["bill"].add(info, rows).json()
            assert body.get("code") == 8000004, f"flag=0 应拦截 8000004,实际: {body}"
        finally:
            s["syscfg"].update_field("minusStockFlag", s["flag_before"])

    @allure.title("API-DEP-004 调拨单删除:两仓库存回退(F7)")
    @pytest.mark.db_check
    def test_004_allocation_delete_revert(self, depot_env):
        s = depot_env
        a_before, b_before = s["stock"](s["depot_a"]), s["stock"](s["depot_b"])
        a_id = s["make"]("DBCK", "出库", "调拨",
                         [s["item_row"](3, 10, depot_id=s["depot_a"], anotherDepotId=s["depot_b"])],
                         extra={"accountId": "", "changeAmount": 0,
                                "discountLastMoney": 30, "totalPrice": 30})
        assert s["stock"](s["depot_a"]) == a_before - 3
        assert_code_ok(s["bill"].delete_batch([a_id]), "删除调拨单")
        assert s["stock"](s["depot_a"]) == a_before and s["stock"](s["depot_b"]) == b_before, \
            "删除后两仓库存应完全回退(F7)"

    @allure.title("API-DEP-007 其它出库超库存两态:flag=1 放行负库存(db_check)")
    @pytest.mark.db_check
    def test_007_other_out_over_stock_flag1(self, depot_env):
        s = depot_env
        before = s["stock"]()
        out_id = s["make"]("QTCK", "出库", "其它", [s["item_row"](50, 10)],
                           extra={"accountId": s["account_id"]})
        s["bill_ids"].append(out_id)
        assert s["stock"]() == before - 50, f"flag=1 应放行记负库存,实际 {s['stock']()}"

    @allure.title("API-DEP-007b 其它出库超库存:flag=0 拦截 8000004")
    @pytest.mark.regression
    def test_007b_other_out_over_stock_flag0(self, depot_env):
        s = depot_env
        s["syscfg"].update_field("minusStockFlag", "0")
        try:
            no = "QTCK" + s["bill"].build_number()
            info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                    "type": "出库", "subType": "其它", "accountId": s["account_id"],
                    "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
            body = s["bill"].add(info, [s["item_row"](999, 10)]).json()
            assert body.get("code") == 8000004, f"flag=0 应拦截,实际: {body}"
        finally:
            s["syscfg"].update_field("minusStockFlag", s["flag_before"])

    @allure.title("API-DEP-008/010 组装单:子件出库库存减少,单据生效(db_check)")
    @pytest.mark.db_check
    def test_008_assemble(self, depot_env):
        s = depot_env
        before = s["stock"](s["depot_a"])
        z_id = s["make"]("ZZ", "出库", "组装", [s["item_row"](2, 10)],
                         extra={"accountId": ""})
        s["bill_ids"].append(z_id)
        assert s["stock"](s["depot_a"]) == before - 2, "组装子件应出库-2(R4 生效)"
        head = s["db"].query_one(
            "SELECT sub_type FROM jsh_depot_head WHERE id=%s", (z_id,))
        assert head["sub_type"] == "组装"
