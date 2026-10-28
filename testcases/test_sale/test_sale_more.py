"""销售长尾(API-SALE-012/022/023/024/025/026)+ 财务长尾(FIN-003/010/012/013)+ 仓库(DEP-003/009)。

取证(2026-10-27):
- 税率 13 落库精确:tax_rate=13/tax_money=13/tax_last_money=113/all_price=100;
- 自身调拨被 8500010 拦截(调入仓库与原仓库不能重复);拆卸 type=入库 subType=拆卸;
- 转账单:头 accountId=转出、明细 accountId=转入(each_amount 正值);未审核不动余额(F9);
- 收入单缺收支项目实测可建单(又一校验缺失,缺陷回归断言);
- batchSetAdvanceIn 直接设值被实时重算覆盖(DEF-EXP-01,FIN-013 缺陷回归)。
未实现:SALE-011/RET-006 多账户(multi_account_flag=0)、SALE-016/RET-009 超量退货(待实测)。
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
def sale_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "depot": depot, "acc": acc, "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    assert_code_ok(sup.add(f"{stamp}c", "客户"), "客户")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"], low="20"), "商品(low=20)")
    s["customer_id"] = sup.find_id_by_name(f"{stamp}c")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def make_sale(rows, price=20, info_extra=None):
        """带库存预置的销售出库(预置 20 个其它入库)。"""
        no = "QTRK" + s["bill"].build_number()
        pre = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
               "type": "入库", "subType": "其它", "organId": "", "accountId": s["account_id"],
               "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
        pre_rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                     "depotId": s["depot_id"], "operNumber": 20, "unitPrice": 10,
                     "allPrice": 200, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 200, "unit": "个"}]
        assert_code_ok(s["bill"].add(pre, pre_rows), "预置库存")
        pre_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        s["bill_ids"].append(pre_id)
        no2 = "XSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no2,
                "type": "出库", "subType": "销售", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": price * sum(
                    r["operNumber"] for r in rows),
                "discountLastMoney": price * sum(r["operNumber"] for r in rows),
                "totalPrice": price * sum(r["operNumber"] for r in rows),
                "status": "0"}
        if info_extra:
            info.update(info_extra)
        full_rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                      "depotId": s["depot_id"], "unit": "个", "taxRate": 0, "taxMoney": 0,
                      **r} for r in rows]
        assert_code_ok(s["bill"].add(info, full_rows), f"销售单{no2}")
        bill_id = s["bill"].get_detail_by_number(no2).json()["data"]["id"]
        s["bill_ids"].append(bill_id)
        return no2, bill_id

    s.update({"make_sale": make_sale})
    yield s
    for bid in list(dict.fromkeys(s["bill_ids"])):
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) in ("1", "3"):
            s["bill"].batch_set_status("0", [bid])
        body = s["bill"].delete_batch([bid]).json()
        if body.get("code") != 200:
            c.log.warning("teardown 单据 %s 删除失败: %s", bid, body)
    mat.delete_batch([s["material_id"]])
    sup.delete_batch([s["customer_id"]])
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


@pytest.fixture(scope="module")
def fin_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, acc, mat = SupplierApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "acc": acc, "acc_head": AccountHeadApi(c),
         "fin_ids": [], "account_names": []}
    assert_code_ok(sup.add(f"{stamp}sup", "供应商"), "供应商")
    s["supplier_id"] = sup.find_id_by_name(f"{stamp}sup")
    for i in (1, 2):
        assert_code_ok(acc.add(f"{stamp}a{i}", initial=0), f"账户{i}")
        s["account_names"].append(f"{stamp}a{i}")
    s["account_ids"] = [acc.find_id_by_name(n) for n in s["account_names"]]

    def transfer(amount):
        no = "ZZD" + s["acc_head"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "转账",
                "billNo": no, "organId": "", "handsPersonId": "",
                "billTime": datetime.now().strftime("%Y-%m-%d"),
                "accountId": s["account_ids"][0], "totalPrice": amount,
                "discountMoney": "", "discountDeposit": "", "remark": "AUTO"}
        assert_code_ok(s["acc_head"].add(info, [{"accountId": s["account_ids"][1],
                                                 "eachAmount": amount}]), f"转账{no}")
        fid = s["acc_head"].get_detail_by_number(no).json()["data"]["id"]
        s["fin_ids"].append(fid)
        return fid

    s.update({"transfer": transfer})
    yield s
    for fid in s["fin_ids"]:
        s["acc_head"].delete_batch([fid])
    sup.delete_batch([s["supplier_id"]])
    acc.delete_batch(s["account_ids"])


@allure.feature("销售管理")
@pytest.mark.usefixtures("env_check")
class TestSaleMore:

    @allure.title("API-SALE-012 销售欠款计算:含税200收款150→欠50(db_check)")
    @pytest.mark.db_check
    def test_012_debt(self, sale_env):
        s = sale_env
        no, _ = s["make_sale"]([{"operNumber": 10, "unitPrice": 20, "allPrice": 200,
                                 "taxLastMoney": 200}], price=20,
                               info_extra={"changeAmount": 150, "discountLastMoney": 200,
                                           "totalPrice": 200})
        detail = s["bill"].get_detail_by_number(no).json()["data"]
        assert float(detail["debt"]) == 50.0, f"欠款应=200-150=50(R2): {detail.get('debt')}"

    @allure.title("API-SALE-022 日期区间查询含边界日(db_check)")
    @pytest.mark.db_check
    def test_022_date_range(self, sale_env):
        s = sale_env
        no, _ = s["make_sale"]([{"operNumber": 1, "unitPrice": 20, "allPrice": 20,
                                 "taxLastMoney": 20}])
        body = s["client"].get("/depotHead/list", params={
            "search": __import__("json").dumps(
                {"type": "出库", "subType": "销售", "number": no[4:],
                 "beginTime": datetime.now().strftime("%Y-%m-%d"),
                 "endTime": datetime.now().strftime("%Y-%m-%d")}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()
        numbers = [r["number"] for r in (body.get("data") or {}).get("rows") or []]
        assert no in numbers, f"当日区间应含边界日命中: {numbers[:5]}"

    @allure.title("API-SALE-023 商品模糊:名称查询命中本单(列表行按单号断言,db_check)")
    @pytest.mark.db_check
    def test_023_material_param(self, sale_env):
        s = sale_env
        no, _ = s["make_sale"]([{"operNumber": 1, "unitPrice": 20, "allPrice": 20,
                                 "taxLastMoney": 20}])
        body = s["client"].get("/depotHead/list", params={
            "search": __import__("json").dumps(
                {"type": "出库", "subType": "销售", "materialParam": s["material_name"]},
                ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()
        numbers = [r["number"] for r in (body.get("data") or {}).get("rows") or []]
        assert no in numbers, f"按商品名称查询应命中本单 {no}"

    @allure.title("API-SALE-024/025 详情核对:明细行完整且ΣoperNumber金额=头表(db_check)")
    @pytest.mark.db_check
    def test_024_025_detail(self, sale_env):
        s = sale_env
        no, bill_id = s["make_sale"]([{"operNumber": 2, "unitPrice": 20, "allPrice": 40,
                                       "taxLastMoney": 40},
                                      {"operNumber": 3, "unitPrice": 20, "allPrice": 60,
                                       "taxLastMoney": 60}])
        head = s["bill"].get_detail_by_number(no).json()["data"]
        # getDetailByNumber 的 materialsList 是全量可选商品列表(非单据明细),
        # 明细行经 depotItem/getDetailList(headerId) 获取
        items = s["bill"].item_list(bill_id).json()["data"]["rows"]
        assert len(items) == 2, f"明细应2行: {len(items)}"
        assert all(r.get("operNumber") for r in items), "明细行字段不完整"
        assert sum(float(r["operNumber"]) for r in items) == 5.0, "明细数量合计应=5"
        assert float(head["totalPrice"]) == 100.0, f"头表价税合计应=100: {head.get('totalPrice')}"

    @allure.title("API-SALE-016 销售超量退货放行:出3退5(实测无累计校验,db_check)")
    @pytest.mark.db_check
    def test_016_over_return(self, sale_env):
        s = sale_env
        src_no, src_id = s["make_sale"]([{"operNumber": 3, "unitPrice": 20, "allPrice": 60,
                                          "taxLastMoney": 60}])
        item_id = s["bill"].item_list(src_id).json()["data"]["rows"][0]["id"]
        stock_before = None
        no = "XSTK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "销售退货", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": 100,
                "discountLastMoney": 100, "totalPrice": 100,
                "linkNumber": src_no, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 5, "unitPrice": 20,
                 "allPrice": 100, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 100,
                 "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), "超量退货(缺陷现状:无累计校验)")
        sr = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(sr["id"])
        link = s["db"].query_one("SELECT link_number FROM jsh_depot_head WHERE id=%s", (sr["id"],))
        assert link["link_number"] == src_no

    @allure.title("API-SALE-015/017 销售退货转单:库存回补+link 关联+应收联动(db_check)")
    @pytest.mark.db_check
    def test_015_017_sale_return(self, sale_env):
        s = sale_env
        src_no, src_id = s["make_sale"]([{"operNumber": 3, "unitPrice": 20, "allPrice": 60,
                                          "taxLastMoney": 60}])
        item_id = s["bill"].item_list(src_id).json()["data"]["rows"][0]["id"]
        import pymysql
        stock_before = float(s["db"].query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))["current_number"])
        no = "XSTK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "销售退货", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": -40,
                "discountLastMoney": 40, "totalPrice": -40,
                "linkNumber": src_no, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 2, "unitPrice": 20,
                 "allPrice": 40, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 40,
                 "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), "销售退货转单")
        sr = s["bill"].get_detail_by_number(no).json()["data"]
        s["bill_ids"].append(sr["id"])
        link = s["db"].query_one(
            "SELECT link_number FROM jsh_depot_head WHERE id=%s", (sr["id"],))["link_number"]
        assert link == src_no, f"退货应关联原出库单: {link}"
        stock_after = float(s["db"].query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))["current_number"])
        assert stock_after == stock_before + 2,             f"退货后库存应回补+2: {stock_before}→{stock_after}"

    @allure.title("API-SALE-026 税率13计算:tax_money=13/tax_last_money=113(db_check)")
    @pytest.mark.db_check
    def test_026_tax(self, sale_env):
        s = sale_env
        no = "XSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "销售", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": 113,
                "discountLastMoney": 113, "totalPrice": 113, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 1, "unitPrice": 100,
                 "allPrice": 100, "taxRate": 13, "taxMoney": 13, "taxLastMoney": 113,
                 "unit": "个"}]
        # 单价100>low20 不触发 8500018
        assert_code_ok(s["bill"].add(info, rows), "税率13销售")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        s["bill_ids"].append(bill_id)
        item = s["db"].query_one(
            "SELECT tax_rate, tax_money, tax_last_money, all_price FROM jsh_depot_item "
            "WHERE header_id=%s", (bill_id,))
        assert (float(item["tax_rate"]), float(item["tax_money"]),
                float(item["tax_last_money"]), float(item["all_price"])) == (13.0, 13.0, 113.0, 100.0), \
            f"税额落库异常: {item}"


@allure.feature("财务管理")
@pytest.mark.usefixtures("env_check")
class TestFinanceMore:

    @allure.title("API-FIN-003 收入单缺收支项目实测可建单(缺陷回归)")
    @pytest.mark.regression
    def test_003_income_no_item(self, fin_env):
        s = fin_env
        no = "YSD" + s["acc_head"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "收入",
                "billNo": no, "organId": "", "handsPersonId": "",
                "billTime": datetime.now().strftime("%Y-%m-%d"),
                "accountId": s["account_ids"][0], "totalPrice": 50,
                "discountMoney": "", "discountDeposit": "", "remark": "AUTO"}
        body = s["acc_head"].add(info, [{"eachAmount": 50}]).json()
        assert body.get("code") == 200, f"缺陷现状可建单,若加校验拦截请翻转: {body}"
        s["fin_ids"].append(s["acc_head"].get_detail_by_number(no).json()["data"]["id"])

    @allure.title("API-FIN-010 转账单:头转出/明细转入落库,未审核余额不动(F9/db_check)")
    @pytest.mark.db_check
    def test_010_transfer(self, fin_env):
        s = fin_env
        stat_before = s["client"].get("/account/getStatistics", params={
            "name": s["account_names"][0], "serialNo": ""}).json()["data"]
        fid = s["transfer"](30)
        head = s["db"].query_one(
            "SELECT account_id, total_price FROM jsh_account_head WHERE id=%s", (fid,))
        item = s["db"].query_one(
            "SELECT account_id, each_amount FROM jsh_account_item WHERE header_id=%s", (fid,))
        assert str(head["account_id"]) == str(s["account_ids"][0]), f"头账户应=转出: {head}"
        assert float(item["each_amount"]) == 30.0, f"明细应+30: {item}"
        stat_after = s["client"].get("/account/getStatistics", params={
            "name": s["account_names"][0], "serialNo": ""}).json()["data"]
        assert stat_after == stat_before, "未审核转账不应改变统计余额(F9)"

    @allure.title("API-FIN-012/013 删除充值单实时回退+batchSetAdvanceIn 被重算覆盖(DEF-EXP-01,db_check)")
    @pytest.mark.db_check
    def test_012_013_advance_charge_revert(self, fin_env):
        s = fin_env
        mem_name = exp_id("EXPFmem")
        assert_code_ok(s["sup"].add(mem_name, "会员"), "会员")
        mem_id = s["sup"].find_id_by_name(mem_name)
        try:
            # DEF-EXP-01:直接设期初 970
            body = s["client"].post("/supplier/batchSetAdvanceIn",
                                    json_body={"ids": f"{mem_id},", "advanceIn": 970}).json()
            assert body.get("code") == 200
            no = "SKQ" + s["acc_head"].build_number()
            info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "收预付款",
                    "billNo": no, "organId": mem_id, "handsPersonId": "",
                    "billTime": datetime.now().strftime("%Y-%m-%d"),
                    "accountId": s["account_ids"][0], "totalPrice": 500,
                    "discountMoney": "", "discountDeposit": "", "remark": "AUTO"}
            assert_code_ok(s["acc_head"].add(info, [{"eachAmount": 500}]), "充值500")
            fid = s["acc_head"].get_detail_by_number(no).json()["data"]["id"]
            row = s["db"].query_one("SELECT advance_in FROM jsh_supplier WHERE id=%s", (mem_id,))
            assert float(row["advance_in"]) == 500.0, \
                f"充值后余额=单据重算值500,期初970被覆盖(DEF-EXP-01 实测): {row}"
            assert_code_ok(s["acc_head"].delete_batch([fid]), "删充值单")
            row = s["db"].query_one("SELECT advance_in FROM jsh_supplier WHERE id=%s", (mem_id,))
            assert float(row["advance_in"] or 0) == 0.0, f"删除后应实时回退0: {row}"
        finally:
            s["sup"].delete_batch([mem_id])


@allure.feature("仓库管理")
@pytest.mark.usefixtures("env_check")
class TestDepotMore:

    @allure.title("API-DEP-009 拆卸单正向+DEP-003 自调拨拦截(8500010)(db_check)")
    @pytest.mark.db_check
    def test_009_disassemble_and_self_allocation(self, fin_env):
        s = fin_env
        name = exp_id("EXPFdm")
        assert_code_ok(s["sup"].add(name, "供应商"), "前置供应商(占位)")
        sup_id = s["sup"].find_id_by_name(name)
        from api.material_api import MaterialApi
        from api.depot_api import DepotApi
        mat, depot = MaterialApi(s["client"]), DepotApi(s["client"])
        try:
            assert_code_ok(mat.add(f"{name}m", f"{name}b"), "商品")
            assert_code_ok(depot.add(f"{name}d"), "仓库")
            mid = mat.find_by_name(f"{name}m")["id"]
            dep_id = depot.find_id_by_name(f"{name}d")
            no = "QTRK" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
            info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                    "type": "入库", "subType": "其它", "organId": "", "accountId": "",
                    "changeAmount": 0, "discountLastMoney": 0, "totalPrice": 0, "status": "0"}
            rows = [{"materialId": mid, "barCode": f"{name}b", "depotId": dep_id,
                     "operNumber": 2, "unitPrice": 10, "allPrice": 20, "taxRate": 0,
                     "taxMoney": 0, "taxLastMoney": 20, "unit": "个"}]
            assert_code_ok(s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
                "info": __import__("json").dumps(info, ensure_ascii=False),
                "rows": __import__("json").dumps(rows, ensure_ascii=False)}), "预置2")
            # DEP-003:同仓库自身调拨被拦(8500010,调入仓库与原仓库不能重复)
            no_self = "DBCK" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
            info_self = dict(info, number=no_self, type="出库", subType="调拨")
            rows_self = [dict(rows[0], anotherDepotId=dep_id)]
            body = s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
                "info": __import__("json").dumps(info_self, ensure_ascii=False),
                "rows": __import__("json").dumps(rows_self, ensure_ascii=False)}).json()
            assert body.get("code") == 8500010, f"自调拨应 8500010,实际: {body}"
            # DEP-009:拆卸单(组合件入库行)库存+1
            no2 = "CX" + s["client"].get("/sequence/buildNumber").json()["data"]["defaultNumber"]
            info2 = dict(info, number=no2, type="入库", subType="拆卸",
                         discountLastMoney=10, totalPrice=10)
            rows2 = [dict(rows[0], operNumber=1, allPrice=10, taxLastMoney=10)]
            assert_code_ok(s["client"].post("/depotHead/addDepotHeadAndDetail", json_body={
                "info": __import__("json").dumps(info2, ensure_ascii=False),
                "rows": __import__("json").dumps(rows2, ensure_ascii=False)}), "拆卸+1")
            head = s["db"].query_one(
                "SELECT sub_type FROM jsh_depot_head WHERE number=%s AND tenant_id=%s",
                (no2, s["tenant_id"]))
            assert head["sub_type"] == "拆卸"
        finally:
            for m in (f"{name}m",):
                r = mat.find_by_name(m)
                if r:
                    mat.delete_batch([r["id"]])
            depot.delete_batch([depot.find_id_by_name(f"{name}d")])
            s["sup"].delete_batch([sup_id])
