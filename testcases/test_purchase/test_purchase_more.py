"""采购管理长尾(API-PUR-019/020/023/024/026/027/028/029)。

取证(2026-10-27):
- 漏传 discountLastMoney:头表 discount_last_money 落 NULL,getDetail 欠款显示 -80
  (DEF-EXP-03 复现,缺陷回归断言);
- batchSetLastDebt 修正欠款:ids 带尾逗号字符串可用,数组形式 500;
- 采购退货(type=出库 subType=采购退货+linkNumber/linkId)可对未审核入库单转退;
- 多账户场景(multi_account_flag=0)传 accountIdList 被拒 8500008,PUR-021 推迟。
PUR-017 已实现(2026-10-31 源码+直调定案,GYJ-MSG-001 2.7,见 TestLinkBillQuery);
仍未实现:PUR-030 超量退货边界(待实测钉断言)。
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
from utils.cleanup import delete_bills_with_fallback, delete_depots_with_fallback, delete_materials_with_fallback
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
    delete_bills_with_fallback(c, db, s["bill_ids"])
    delete_materials_with_fallback(c, db, [s["material_id"]])
    sup.delete_batch([s["supplier_id"]])
    delete_depots_with_fallback(c, db, [s["depot_a"], s["depot_b"]])
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


@allure.feature("采购管理")
@pytest.mark.regression
class TestLinkBillQuery:
    """转单关联查询(2026-10-31 源码+直调+DB 双查定案,GYJ-MSG-001 2.7):
    GET /depotHead/getBillListByLinkNumber?number=源单号 → {code:200,data:[...]}。
    **缺陷**:后端 andSubTypeLike("退货") 未加 % 通配符(SQL 精确匹配 sub_type='退货',
    无单据满足),接口对任何单号恒返回空——前端弹窗"关联退货单"展示实际失效
    (缺陷候选,提交禅道);修复为 '%退货%' 后翻转 test_017 断言。"""

    @allure.title("API-PUR-017 转单关联查询恒返回空(缺陷回归:like 缺 % 通配符,DB 双查佐证,修复后翻转)")
    @pytest.mark.db_check
    def test_017_return_link_query(self, pur_env):
        """2026-10-31 定案:建 linkNumber 关联的采购退货单后接口仍返回空——
        后端 andSubTypeLike("退货") 未加 %,SQL 为 sub_type like '退货'(精确匹配)。
        用 DB 同库双查证明数据存在而接口条件失效;修复后请翻转断言。"""
        s = pur_env
        src_no, src_id = s["make_bill"]("CGRK", "入库", "采购", num=3)
        item_id = s["bill"].item_list(src_id).json()["data"]["rows"][0]["id"]
        no = "CGTK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "采购退货", "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": -10,
                "discountLastMoney": 10, "totalPrice": -10,
                "linkNumber": src_no, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 1, "unitPrice": 10, "allPrice": 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 10, "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), "采购退货关联源单")
        rb_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        # DB 双查佐证根因:通配命中 1 行、精确命中 0 行 → 数据在,接口 like 条件失效
        exact = s["db"].query_one(
            "SELECT COUNT(*) n FROM jsh_depot_head WHERE link_number=%s "
            "AND sub_type LIKE '退货' AND delete_flag=0", (src_no,))["n"]
        wild = s["db"].query_one(
            "SELECT COUNT(*) n FROM jsh_depot_head WHERE link_number=%s "
            "AND sub_type LIKE '%%退货%%' AND delete_flag=0", (src_no,))["n"]
        assert wild == 1 and exact == 0, \
            f"DB 应通配命中1行且精确命中0行: exact={exact}, wild={wild}"
        body = s["client"].get("/depotHead/getBillListByLinkNumber",
                               params={"number": src_no}).json()
        assert body.get("code") == 200 and (body.get("data") or []) == [], \
            f"缺陷现状应恒返回空(like 缺 %),若已修复请翻转断言: {body}"
        assert_code_ok(s["bill"].delete_batch([rb_id]), "清理退货单")

    @allure.title("API-PUR-017 语义边界:订单→入库非退货关联与不存在单号均返回空(名实不符观察项)")
    def test_017b_non_return_empty(self, pur_env):
        s = pur_env
        order_no, order_id = s["make_bill"]("CGDD", "其它", "采购订单", num=2)
        item_id = s["bill"].item_list(order_id).json()["data"]["rows"][0]["id"]
        no = "CGRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": -20,
                "discountLastMoney": 20, "totalPrice": -20,
                "linkNumber": order_no, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 2, "unitPrice": 10, "allPrice": 20, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 20, "unit": "个", "linkId": item_id}]
        assert_code_ok(s["bill"].add(info, rows), "订单转入库(非退货关联)")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        body = s["client"].get("/depotHead/getBillListByLinkNumber",
                               params={"number": order_no}).json()
        assert body.get("code") == 200 and (body.get("data") or []) == [], \
            f"非退货关联查源头应返回空数组(只认退货),实际: {body}"
        body2 = s["client"].get("/depotHead/getBillListByLinkNumber",
                                params={"number": "NOEXPFXQR"}).json()
        assert body2.get("code") == 200 and (body2.get("data") or []) == [], \
            f"不存在单号应返回空数组不报错: {body2}"


@allure.feature("采购管理")
@pytest.mark.regression
@pytest.mark.usefixtures("env_check")
class TestPurchaseParamEdge:
    """参数边界补充(2026-10-31 探针取证):订单缺供应商放行(校验缺失家族),
    空明细 8500006 有校验(正面行为),多账户分摊(flag=1)落库;报文见 GYJ-MSG-001。"""

    @allure.title("API-PUR-002 订单缺供应商(organId 缺失)实测放行——缺陷回归")
    def test_002_order_no_organ(self, pur_env):
        s = pur_env
        no = "CGDD" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "其它", "subType": "采购订单", "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                 "operNumber": 1, "unitPrice": 10, "allPrice": 10, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": 10, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "缺供应商建单(缺陷现状)")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])

    @allure.title("API-PUR-003 订单明细为空提交被拒(8500006,正面校验)")
    def test_003_empty_rows(self, pur_env):
        s = pur_env
        no = "CGDD" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "其它", "subType": "采购订单", "organId": s["supplier_id"], "status": "0"}
        body = s["bill"].add(info, []).json()
        assert body.get("code") == 8500006, f"空明细应被 8500006 拦截,实际: {body}"

    @allure.title("API-PUR-021 多账户付款:两账户分摊 12/8 落库(余额不动,F9/db_check)")
    @pytest.mark.db_check
    def test_021_multi_account(self, pur_env):
        """2026-10-31 探针:multiAccountFlag=1 时 info.accountIdList/accountMoneyList
        传紧凑 JSON 数组字符串("[12,8]",禁带空格否则 getArrSum NPE 500);
        金额和绝对值必须等于 changeAmount 绝对值(8500008);未审核单不动账户余额(F9)。"""
        from api.account_api import AccountApi
        from api.system_config_api import SystemConfigApi
        s = pur_env
        syscfg = SystemConfigApi(s["client"])
        flag_before = syscfg.get_current_info().get("multiAccountFlag")
        a1, a2 = exp_id("EXPFa"), exp_id("EXPFa")
        acc_ids = []
        try:
            syscfg.update_field("multiAccountFlag", "1")
            for an in (a1, a2):
                assert_code_ok(AccountApi(s["client"]).add(an, initial=100), "账户")
                acc_ids.append(s["db"].query_one(
                    "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (an,))["id"])
            no = "CGRK" + s["bill"].build_number()
            info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                    "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                    "accountIdList": json.dumps(acc_ids), "accountMoneyList": "[12,8]",
                    "changeAmount": -20, "discountLastMoney": 20, "totalPrice": -20, "status": "0"}
            rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_a"],
                     "operNumber": 2, "unitPrice": 10, "allPrice": 20, "taxRate": 0,
                     "taxMoney": 0, "taxLastMoney": 20, "unit": "个"}]
            assert_code_ok(s["bill"].add(info, rows), "多账户入库")
            head = s["db"].query_one(
                "SELECT account_id_list, account_money_list FROM jsh_depot_head WHERE number=%s AND delete_flag=0",
                (no,))
            assert head and head["account_money_list"] == "12,8", f"分摊金额应落库: {head}"
            for aid in acc_ids:
                assert str(aid) in (head["account_id_list"] or ""), f"账户 {aid} 应在 account_id_list: {head}"
            s["bill_ids"].append(s["db"].query_one(
                "SELECT id FROM jsh_depot_head WHERE number=%s AND delete_flag=0", (no,))["id"])
        finally:
            if flag_before is not None:
                syscfg.update_field("multiAccountFlag", flag_before)
            for an in (a1, a2):
                row = s["db"].query_one(
                    "SELECT id FROM jsh_account WHERE name=%s AND delete_flag=0", (an,))
                if row:
                    s["client"].delete("/account/deleteBatch", params={"ids": f"{row['id']},"})
