"""销售库存边界(API-SALE-006~009):负库存开关两态 + 最低售价边界。

R1 实测:minus_stock_flag=1 放行记负库存;=0 拦截 8000004;出库单价低于 low_decimal
被 8500018 拦截,等于放行。开关经 PUT /systemConfig/update 切换(实测生效),测后恢复。
"""
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
def stock_env(auth, db):
    """库存=10(其它入库预置)、最低售价20 的商品;记录开关原值,模块结束恢复。"""
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    syscfg = SystemConfigApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db, "syscfg": syscfg,
         "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    s["flag_before"] = syscfg.get_current_info().get("minusStockFlag")
    assert_code_ok(sup.add(f"{stamp}cus", "客户"), "客户")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a"), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"], purchase="10",
                           retail="20", low="20"), "商品(low=20)")
    s["customer_id"] = sup.find_id_by_name(f"{stamp}cus")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def add_stock(num=10):
        no = "QTRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "其它", "organId": "", "accountId": s["account_id"],
                "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 10, "allPrice": num * 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "其它入库预置库存")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])

    def sale_out(num, price=20):
        no = "XSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "销售", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": 0,
                "discountLastMoney": num * price, "totalPrice": num * price, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": price, "allPrice": num * price, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * price, "unit": "个"}]
        return s["bill"].add(info, rows), no

    s.update({"add_stock": add_stock, "sale_out": sale_out})
    add_stock(10)
    yield s
    try:
        if s["flag_before"] is not None:
            syscfg.update_field("minusStockFlag", s["flag_before"])
    finally:
        for bid in s["bill_ids"]:
            s["bill"].delete_batch([bid])
        mat.delete_batch([s["material_id"]])
        sup.delete_batch([s["customer_id"]])
        depot.delete_batch([s["depot_id"]])
        acc.delete_batch([s["account_id"]])


def _stock(s):
    row = s["db"].query_one(
        "SELECT current_number FROM jsh_material_current_stock "
        "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
        (s["material_id"], s["depot_id"], s["tenant_id"]))
    return float(row["current_number"]) if row else 0.0


@allure.feature("销售管理")
@pytest.mark.usefixtures("env_check")
class TestStockBoundary:

    @allure.title("API-SALE-006 超库存出库,flag=1 放行记负库存(db_check)")
    @pytest.mark.db_check
    def test_006_negative_stock_allowed(self, stock_env):
        s = stock_env
        resp, no = s["sale_out"](25)          # 库存10,出25
        assert_code_ok(resp, "超库存放行")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        assert _stock(s) == -15.0, f"放行后库存应=-15(10-25),实际 {_stock(s)}"

    @allure.title("API-SALE-007 超库存出库,flag=0 拦截(8000004),开关测后恢复")
    @pytest.mark.regression
    def test_007_negative_stock_blocked(self, stock_env):
        s = stock_env
        assert s["syscfg"].update_field("minusStockFlag", "0").get("code") == 200
        try:
            resp, no = s["sale_out"](5)       # 库存-15,仍不足
            body = resp.json()
            assert body.get("code") == 8000004, f"期望 8000004,实际: {body}"
            assert s["bill"].get_detail_by_number(no).json().get("code") != 200, \
                "拦截时不应产生单据"
        finally:
            s["syscfg"].update_field("minusStockFlag", s["flag_before"])

    @allure.title("API-SALE-008 出库单价低于最低售价被拒(8500018)")
    @pytest.mark.regression
    def test_008_price_below_low(self, stock_env):
        s = stock_env
        resp, _ = s["sale_out"](1, price=15)  # low=20
        body = resp.json()
        assert body.get("code") == 8500018, f"期望 8500018,实际: {body}"

    @allure.title("API-SALE-009 出库单价等于最低售价放行")
    @pytest.mark.regression
    def test_009_price_equal_low(self, stock_env):
        s = stock_env
        resp, no = s["sale_out"](1, price=20)
        assert_code_ok(resp, "等于最低价放行")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
