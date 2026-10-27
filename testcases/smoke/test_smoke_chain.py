"""冒烟主链路:采购入库→欠款断言→销售出库→库存断言(对应 Postman jshERP-api-smoke)。

- 全程未审核保存(R4 实证:保存即入账,审核仅为状态标记),清理无状态机障碍。
- 整链共用一个 EXPF 前缀的动态数据;teardown 按单据→商品→往来→仓库→账户顺序清理
  (F7 实证:删除即实时重算回退库存/欠款)。
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


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(scope="class")
def smoke_chain(auth):
    """链路数据自建自清(类级一次);失败中断时已建数据留待按 EXPF 前缀人工清理。"""
    c = auth["client"]
    stamp = exp_id()
    s = {
        "client": c, "tenant_id": auth["tenant_id"],
        "sup": SupplierApi(c), "mat": MaterialApi(c), "depot": DepotApi(c),
        "acc": AccountApi(c), "bill": DepotHeadApi(c),
        "supplier_name": f"{stamp}sup", "customer_name": f"{stamp}cus",
        "depot_name": f"{stamp}dep", "account_name": f"{stamp}acc",
        "material_name": f"{stamp}mat", "bar_code": f"{stamp}bc",
        "bill_ids": [],
    }
    assert_code_ok(s["sup"].add(s["supplier_name"], "供应商"), "供应商新增")
    assert_code_ok(s["sup"].add(s["customer_name"], "客户"), "客户新增")
    assert_code_ok(s["depot"].add(s["depot_name"]), "仓库新增")
    assert_code_ok(s["acc"].add(s["account_name"]), "账户新增")
    assert_code_ok(s["mat"].add(s["material_name"], s["bar_code"]), "商品新增")
    s["supplier_id"] = s["sup"].find_id_by_name(s["supplier_name"])
    s["customer_id"] = s["sup"].find_id_by_name(s["customer_name"])
    s["depot_id"] = s["depot"].find_id_by_name(s["depot_name"])
    s["account_id"] = s["acc"].find_id_by_name(s["account_name"])
    mat_row = s["mat"].find_by_name(s["material_name"])
    s["material_id"] = mat_row["id"] if mat_row else None
    missing = [k for k in ("supplier_id", "customer_id", "depot_id",
                           "account_id", "material_id") if not s[k]]
    assert not missing, f"基础资料提取 ID 失败: {missing}"
    yield s
    # teardown:删单→商品→往来→仓库→账户(删除即重算回退,F7)
    for bid in s["bill_ids"]:
        resp = s["bill"].delete_batch([bid])
        c.log.info("清理单据 %s -> code=%s", bid, resp.json().get("code"))
    s["mat"].delete_batch([s["material_id"]])
    s["sup"].delete_batch([s["supplier_id"], s["customer_id"]])
    s["depot"].delete_batch([s["depot_id"]])
    s["acc"].delete_batch([s["account_id"]])
    assert s["mat"].find_by_name(s["material_name"]) is None, "清理复核:商品未删除"


@allure.feature("冒烟主链路")
@pytest.mark.usefixtures("env_check", "smoke_chain")
@pytest.mark.smoke
class TestSmokeChain:

    @allure.title("API-PUR-018 新增采购入库正向(3个@10,付款20,未审核保存)")
    def test_01_purchase_in(self, smoke_chain):
        """编号 CGRK 前缀自动取号;保存即返回 code=200"""
        s = smoke_chain
        bill_no = "CGRK" + s["bill"].build_number()
        info = {"operTime": _now(), "number": bill_no, "type": "入库", "subType": "采购",
                "organId": s["supplier_id"], "accountId": s["account_id"],
                "changeAmount": -20, "discountLastMoney": 30, "totalPrice": -30,
                "status": "0", "remark": "SMOKE"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 3, "unitPrice": 10,
                 "allPrice": 30, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 30,
                 "unit": "个", "remark": "SMOKE"}]
        assert_code_ok(s["bill"].add(info, rows), "采购入库新增")
        detail = s["bill"].get_detail_by_number(bill_no).json()
        assert detail["data"]["number"] == bill_no, f"按编号查单失败: {detail}"
        s["purchase_bill_no"] = bill_no
        s["bill_ids"].append(detail["data"]["id"])

    @allure.title("API-PUR-022/025 采购入库对账:头表金额落库+欠款计算值=10,库存即时+3")
    def test_02_purchase_db_check(self, smoke_chain, db):
        """R2 欠款公式 + R4 保存即入账(未审核单计入库存)。
        实测 DB 不落库 debt(debt/last_debt=NULL),欠款为接口计算值,经 getDetailByNumber 断言。"""
        s = smoke_chain
        row = db.query_one(
            "SELECT status, debt, total_price, discount_last_money, change_amount, delete_flag "
            "FROM jsh_depot_head WHERE number=%s AND tenant_id=%s AND delete_flag=0",
            (s["purchase_bill_no"], s["tenant_id"]))
        assert row, f"depot_head 无记录: {s['purchase_bill_no']}"
        assert str(row["status"]) == "0", f"未审核保存,实际 status={row['status']}"
        assert (float(row["total_price"]) == -30.0
                and float(row["discount_last_money"]) == 30.0
                and float(row["change_amount"]) == -20.0), f"头表金额字段异常: {row}"
        assert row["debt"] is None, f"实测 debt 不落库,实际 {row['debt']}"
        detail = s["bill"].get_detail_by_number(s["purchase_bill_no"]).json()
        assert float(detail["data"]["debt"]) == 10.0, \
            f"本次欠款应为 30-20=10(R2),实际 {detail['data'].get('debt')}"
        stock = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        assert stock and float(stock["current_number"]) == 3.0, \
            f"入库后库存应为 3(保存即入账 R4),实际 {stock}"

    @allure.title("API-SALE-005 新增销售出库正向(1个@20,收款20,未审核保存)")
    def test_03_sale_out(self, smoke_chain):
        s = smoke_chain
        bill_no = "XSCK" + s["bill"].build_number()
        info = {"operTime": _now(), "number": bill_no, "type": "出库", "subType": "销售",
                "organId": s["customer_id"], "accountId": s["account_id"],
                "changeAmount": 20, "discountLastMoney": 20, "totalPrice": 20,
                "status": "0", "remark": "SMOKE"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 1, "unitPrice": 20,
                 "allPrice": 20, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 20,
                 "unit": "个", "remark": "SMOKE"}]
        assert_code_ok(s["bill"].add(info, rows), "销售出库新增")
        detail = s["bill"].get_detail_by_number(bill_no).json()
        assert detail["data"]["number"] == bill_no, f"按编号查单失败: {detail}"
        s["sale_bill_no"] = bill_no
        s["bill_ids"].append(detail["data"]["id"])

    @allure.title("API-SALE-013 出库后库存 DB+接口 双断言:3-1=2")
    def test_04_stock_after_sale(self, smoke_chain, db):
        s = smoke_chain
        stock = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        assert stock and float(stock["current_number"]) == 2.0, \
            f"出库后库存应为 2(入3-出1),实际 {stock}"
        row = s["mat"].find_by_name(s["material_name"])
        assert row is not None and float(row["stock"]) == 2.0, \
            f"接口侧库存应为 2,实际 {row and row.get('stock')}"
