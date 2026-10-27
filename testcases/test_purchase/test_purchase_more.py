"""采购管理长尾(API-PUR-019/020/023/024/026/027/028/029)。

取证(2026-10-27):
- 漏传 discountLastMoney:头表 discount_last_money 落 NULL,getDetail 欠款显示 -80
  (DEF-EXP-03 复现,缺陷回归断言);
- batchSetLastDebt 修正欠款:ids 带尾逗号字符串可用,数组形式 500;
- 采购退货(type=出库 subType=采购退货+linkNumber/linkId)可对未审核入库单转退;
- 多账户场景(multi_account_flag=0)传 accountIdList 被拒 8500008,PUR-021 推迟。
未实现:PUR-017 转单关联查询接口 getBillListByLinkNumber 缺参 500(待抓包)、
PUR-030 超量退货边界(待实测钉断言)。
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
def pur_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "depot": depot, "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    assert_code_ok(sup.add(f"{stamp}s", "供应商"), "供应商")
    assert_code_ok(depot.add(f"{stamp}d1"), "仓库A")
    assert_code_ok(depot.add(f"{stamp}d2"), "仓库B")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["supplier_id"] = sup.find_id_by_name(f"{stamp}s")
    s["depot_a"] = depot.find_id_by_name(f"{stamp}d1")
    s["depot_b"] = depot.find_id_by_name(f"{stamp}d2")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def make_bill(no_prefix, typ, subt, info_extra=None, rows_extra=None, num=3, price=10):
        no = no_prefix + s["bill"].build_number()
        sign = -1 if typ == "入库" else 1
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": typ, "subType": subt, "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": sign * num * price,
                "discountLastMoney": num * price, "totalPrice": sign * num * price,
                "status": "0"}
        if info_extra:
            info.update(info_extra)
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": num, "unitPrice": price, "allPrice": num * price, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * price, "unit": "个"}]
        if rows_extra:
            rows = rows_extra(rows[0])
        assert_code_ok(s["bill"].add(info, rows), f"{subt}单{no}")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        s["bill_ids"].append(bill_id)
        return no, bill_id

    def stock(depot_id=None):
        row = db.query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], depot_id or s["depot_a"], s["tenant_id"]))
        return float(row["current_number"]) if row else 0.0

    s.update({"make_bill": make_bill, "stock": stock})
    yield s
    for bid in list(dict.fromkeys(s["bill_ids"])):
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) in ("1", "3"):
            s["bill"].batch_set_status("0", [bid])
        body = s["bill"].delete_batch([bid]).json()
        if body.get("code") != 200:
            c.log.warning("teardown 单据 %s 删除失败: %s", bid, body)
    mat.delete_batch([s["material_id"]])
    sup.delete_batch([s["supplier_id"]])
    depot.delete_batch([s["depot_a"], s["depot_b"]])
    acc.delete_batch([s["account_id"]])


@allure.feature("采购管理")
@pytest.mark.regression
class TestPurchaseMore:

    @allure.title("API-PUR-019 入库单缺失结算账户被拒(8500007,F2)")
    def test_019_missing_account(self, pur_env):
        s = pur_env
        no = "CGRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                "changeAmount": 0, "discountLastMoney": 10, "totalPrice": -10, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 1, "unitPrice": 10, "allPrice": 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 10, "unit": "个"}]
        body = s["bill"].add(info, rows).json()
        assert body.get("code") == 8500007, f"期望 8500007,实际: {body}"

    @allure.title("API-PUR-020 多行明细不同仓库:各仓库存分别增加(db_check)")
    @pytest.mark.db_check
    def test_020_multi_row(self, pur_env):
        s = pur_env
        a_before, b_before = s["stock"](s["depot_a"]), s["stock"](s["depot_b"])
        base = {"materialId": s["material_id"], "barCode": s["bar_code"], "operNumber": 2,
                "unitPrice": 10, "allPrice": 20, "taxRate": 0, "taxMoney": 0,
                "taxLastMoney": 20, "unit": "个"}
        rows = [dict(base, depotId=s["depot_a"]), dict(base, depotId=s["depot_b"])]
        no, _ = s["make_bill"]("CGRK", "入库", "采购", rows_extra=lambda _: rows)
        assert s["stock"](s["depot_a"]) == a_before + 2, "A仓应+2"
        assert s["stock"](s["depot_b"]) == b_before + 2, "B仓应+2"

    @allure.title("API-PUR-023 漏传 discountLastMoney:欠款显示-80(DEF-EXP-03 缺陷回归,db_check)")
    @pytest.mark.db_check
    def test_023_missing_discount_last(self, pur_env):
        s = pur_env
        no = "CGRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": -80,
                "totalPrice": -100, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 10, "unitPrice": 10, "allPrice": 100, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 100, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "漏字段建单(缺陷现状)")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        s["bill_ids"].append(bill_id)  # 该单是 DEF-EXP-08 触发源,必须随 teardown 清理
        head = s["db"].query_one(
            "SELECT discount_last_money FROM jsh_depot_head WHERE number=%s AND tenant_id=%s",
            (no, s["tenant_id"]))
        assert head["discount_last_money"] is None, f"实测该列不落库: {head}"
        detail = s["bill"].get_detail_by_number(no).json()["data"]
        assert float(detail["debt"]) == -80.0, \
            f"缺陷现状欠款显示-80,若修复为正确计算请翻转: {detail.get('debt')}"

    @allure.title("API-PUR-024 修正欠款 batchSetLastDebt:接口成功但 last_debt 列不落库(实测口径,db_check)")
    @pytest.mark.db_check
    def test_024_set_last_debt(self, pur_env):
        s = pur_env
        no, bill_id = s["make_bill"]("CGRK", "入库", "采购")
        body = s["client"].post("/depotHead/batchSetLastDebt",
                                json_body={"ids": f"{bill_id},", "lastDebt": 5}).json()
        assert body.get("code") == 200, f"修正欠款接口失败: {body}"
        row = s["db"].query_one(
            "SELECT last_debt FROM jsh_depot_head WHERE id=%s", (bill_id,))
        # 实测(2026-10-27):接口成功但 last_debt 列不落库(NULL),欠款为计算值口径;
        # 若后续版本改为落库,翻转断言并断列值=5
        assert row["last_debt"] is None, \
            f"实测 last_debt 不落库,若已落库请翻转断言: {row}"

    @allure.title("API-PUR-026 审核后库存不变(R4,db_check)")
    @pytest.mark.db_check
    def test_026_approve_no_stock_change(self, pur_env):
        s = pur_env
        before = s["stock"]()
        no, bill_id = s["make_bill"]("CGRK", "入库", "采购", num=2)
        assert s["stock"]() == before + 2, "保存即入账(R4)"
        assert_code_ok(s["bill"].batch_set_status("1", [bill_id]), "审核")
        assert s["stock"]() == before + 2, "审核仅为状态标记,库存不变(R4)"

    @allure.title("API-PUR-027 反审核不回退库存(与删除回退形成对照,F7/db_check)")
    @pytest.mark.db_check
    def test_027_reverse_no_stock_revert(self, pur_env):
        s = pur_env
        no, bill_id = s["make_bill"]("CGRK", "入库", "采购", num=2)
        s["bill"].batch_set_status("1", [bill_id])
        before = s["stock"]()
        assert_code_ok(s["bill"].batch_set_status("0", [bill_id]), "反审核")
        assert s["stock"]() == before, "反审核不应回退库存(账务风险观察项)"

    @allure.title("API-PUR-028 列表组合查询:type+subType+单号+日期区间(db_check)")
    @pytest.mark.db_check
    def test_028_combo_query(self, pur_env):
        s = pur_env
        no, _ = s["make_bill"]("CGRK", "入库", "采购", num=1)
        body = s["client"].get("/depotHead/list", params={
            "search": __import__("json").dumps(
                {"type": "入库", "subType": "采购", "number": no[4:],
                 "beginTime": "2026-01-01", "endTime": "2026-12-31"}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()
        numbers = [r["number"] for r in (body.get("data") or {}).get("rows") or []]
        assert no in numbers, f"组合查询应命中 {no}: {numbers[:5]}"

    @allure.title("API-PUR-029 采购退货转单:库存回减+link_number 关联(db_check)")
    @pytest.mark.db_check
    def test_029_purchase_return(self, pur_env):
        s = pur_env
        src_no, src_id = s["make_bill"]("CGRK", "入库", "采购", num=3)
        item_id = s["bill"].item_list(src_id).json()["data"]["rows"][0]["id"]
        before = s["stock"]()
        no = "CGTK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "采购退货", "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": -20,
                "discountLastMoney": 20, "totalPrice": -20,
                "linkNumber": src_no, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 2, "unitPrice": 10, "allPrice": 20, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 20, "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), "采购退货转单")
        rb = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(rb["id"])
        link = s["db"].query_one(
            "SELECT link_number FROM jsh_depot_head WHERE id=%s", (rb["id"],))["link_number"]
        assert link == src_no, f"退货应关联原入库单: {link}"
        assert s["stock"]() == before - 2, f"退货后库存应-2,实际 {s['stock']()}"
