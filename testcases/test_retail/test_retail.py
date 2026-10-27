"""零售管理(API-RET-001/002/003/005/007/008):散客/会员/预付/折扣/库存/退货。

取证(2026-10-27):
- 会员预存走"收预付款"财务单(type=收预付款),advance_in 实时 500→预付消费后 480,
  删除充值单实时回退 0(R5/DEF-EXP-01:期初手工值被重算覆盖);预存不足被 8500016 拦截;
- 折扣三件套 discount/discountMoney/discountLastMoney 与 totalPrice 落库一致;
- 零售退货 type=入库 subType=零售退货,linkNumber 关联原单,库存回补。
未实现(阻塞/推迟):RET-004 结算方式选项查询(findBySelect_retail 缺参 NPE)、
RET-006 多账户(multi_account_flag=0)、RET-009 超量退货(待实测钉断言)。
"""
from datetime import datetime

import allure
import pytest

from api.account_api import AccountApi
from api.account_head_api import AccountHeadApi
from api.depot_api import DepotApi
from api.depot_head_api import DepotHeadApi
from api.material_api import MaterialApi
from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def retail_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "acc_head": AccountHeadApi(c), "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b",
         "bill_ids": [], "fin_ids": []}
    assert_code_ok(sup.add(f"{stamp}mem", "会员"), "会员")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["member_id"] = sup.find_id_by_name(f"{stamp}mem")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def retail_out(num, price, organ_id="", pay_type="现付", extra=None):
        no = "LSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "零售", "organId": organ_id,
                "accountId": s["account_id"], "changeAmount": num * price,
                "discountLastMoney": num * price, "totalPrice": num * price,
                "payType": pay_type, "status": "0"}
        if extra:
            info.update(extra)
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": price, "allPrice": num * price, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * price, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), f"零售单{no}")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        return no

    def charge(amount):
        """收预付款充值,返回财务单号。"""
        no = "SKQ" + s["acc_head"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "收预付款",
                "billNo": no, "organId": s["member_id"], "handsPersonId": "",
                "billTime": datetime.now().strftime("%Y-%m-%d"), "accountId": s["account_id"],
                "totalPrice": amount, "discountMoney": "", "discountDeposit": "", "remark": "AUTO"}
        assert_code_ok(s["acc_head"].add(info, [{"eachAmount": amount}]), f"充值{no}")
        s["fin_ids"].append(s["acc_head"].get_detail_by_number(no).json()["data"]["id"])
        return no

    def stock():
        row = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        return float(row["current_number"]) if row else 0.0

    def advance_in():
        row = db.query_one("SELECT advance_in FROM jsh_supplier WHERE id=%s", (s["member_id"],))
        return float(row["advance_in"] or 0)

    def preset(num=10):
        no = "QTRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "其它", "organId": "", "accountId": s["account_id"],
                "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": 10, "allPrice": num * 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "其它入库预置")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])

    s.update({"retail_out": retail_out, "charge": charge, "stock": stock,
              "advance_in": advance_in, "preset": preset})
    preset(10)
    charge(500)
    yield s
    for bid in s["bill_ids"]:
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) == "1":
            s["bill"].batch_set_status("0", [bid])
        body = s["bill"].delete_batch([bid]).json()
        if body.get("code") != 200:
            c.log.warning("teardown 零售单 %s 删除失败: %s", bid, body)
    for fid in s["fin_ids"]:
        s["acc_head"].delete_batch([fid])
    mat.delete_batch([s["material_id"]])
    body = sup.delete_batch([s["member_id"]]).json()
    if body.get("code") != 200:
        c.log.warning("teardown 会员删除失败: %s", body)
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


@allure.feature("零售管理")
@pytest.mark.usefixtures("env_check")
class TestRetail:

    @allure.title("API-RET-001 散客零售现付:库存-2,单据落库(db_check)")
    @pytest.mark.db_check
    def test_001_walk_in(self, retail_env):
        s = retail_env
        before = s["stock"]()
        no = s["retail_out"](2, 20)
        head = s["db"].query_one(
            "SELECT sub_type, pay_type, change_amount, delete_flag FROM jsh_depot_head "
            "WHERE number=%s AND tenant_id=%s AND delete_flag=0", (no, s["tenant_id"]))
        assert head and head["sub_type"] == "零售" and head["pay_type"] == "现付", f"落库异常: {head}"
        assert float(head["change_amount"]) == 40.0
        assert s["stock"]() == before - 2, f"库存应-2,实际 {s['stock']()}"

    @allure.title("API-RET-002 会员零售现付:预存余额不变")
    @pytest.mark.regression
    def test_002_member_cash(self, retail_env):
        s = retail_env
        before = s["advance_in"]()
        s["retail_out"](1, 20, organ_id=s["member_id"], pay_type="现付")
        assert s["advance_in"]() == before, "现付不应扣预存"

    @allure.title("API-RET-003 会员预付款结算:advance_in 500→480 实时扣减(db_check,P0)")
    @pytest.mark.db_check
    def test_003_advance_pay(self, retail_env):
        s = retail_env
        assert s["advance_in"]() == 500.0, "前置:充值 500 后预存应为 500"
        s["retail_out"](1, 20, organ_id=s["member_id"], pay_type="预付款")
        assert s["advance_in"]() == 480.0, \
            f"预付消费 20 后应 480(实时重算 R5),实际 {s['advance_in']()}"

    @allure.title("API-RET-005 零售折扣三件套落库:discount=10/money=10/last=90(db_check)")
    @pytest.mark.db_check
    def test_005_discount(self, retail_env):
        s = retail_env
        no = "LSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "零售", "organId": "", "accountId": s["account_id"],
                "changeAmount": 90, "discount": "10", "discountMoney": "10",
                "discountLastMoney": 90, "totalPrice": 100, "payType": "现付", "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": 5, "unitPrice": 20, "allPrice": 100, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 100, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "折扣零售")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        head = s["db"].query_one(
            "SELECT discount, discount_money, discount_last_money, total_price "
            "FROM jsh_depot_head WHERE number=%s AND tenant_id=%s", (no, s["tenant_id"]))
        assert (float(head["discount"]) == 10.0 and float(head["discount_money"]) == 10.0
                and float(head["discount_last_money"]) == 90.0
                and float(head["total_price"]) == 100.0), f"折扣三件套异常: {head}"

    @allure.title("API-RET-007 零售出库库存扣减并可重算对账(db_check)")
    @pytest.mark.db_check
    def test_007_stock_deduct(self, retail_env):
        s = retail_env
        before = s["stock"]()
        s["retail_out"](3, 20)
        assert s["stock"]() == before - 3, "零售后库存应精确-3"

    @allure.title("API-RET-008 零售退货:库存回补+link_number 关联(db_check)")
    @pytest.mark.db_check
    def test_008_retail_return(self, retail_env):
        s = retail_env
        src_no = s["retail_out"](1, 20)
        before = s["stock"]()
        no = "LSTK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "零售退货", "organId": "",
                "accountId": s["account_id"], "changeAmount": -20,
                "discountLastMoney": 20, "totalPrice": -20,
                "linkNumber": src_no, "payType": "现付", "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": 1, "unitPrice": 20, "allPrice": 20, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 20, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "零售退货")
        detail = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(detail["id"])
        assert detail["linkNumber"] == src_no or \
            s["db"].query_one("SELECT link_number FROM jsh_depot_head WHERE id=%s",
                              (detail["id"],))["link_number"] == src_no, "退货应关联原单"
        assert s["stock"]() == before + 1, f"退货后库存应回补+1,实际 {s['stock']()}"
