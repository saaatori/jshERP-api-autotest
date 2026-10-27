"""报表查询对账(API-RPT-001/002/004~012 + RET-010 + DEP-011/012)。

取证(2026-10-27):
- 报表接口为 GET 平铺参数(不用 search JSON);未审核单计入统计(R4,高风险回归点:
  进销存统计 SQL 刚在部署 commit 8f5dc08 被优化过);
- getInOutStock 行:thisSum=结存、inSum=累计入、outSum=累计出,对账公式 thisSum=inSum-outSum;
- findInOutDetail 缺 remark 参数报 500(缺参未知异常缺陷同源模式)。
RPT-003 多单位口径推迟(多单位商品构造复杂,批次 I)。
"""
from datetime import datetime

import allure
import pytest

from api.account_api import AccountApi
from api.depot_api import DepotApi
from api.depot_head_api import DepotHeadApi
from api.material_api import MaterialApi
from api.report_api import ReportApi
from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def report_env(auth, db):
    """造三笔未审核单据:采购入库3@10(欠30)、销售出库1@20、零售1@20(R4:保存即入账计入报表)。"""
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db, "rpt": ReportApi(c),
         "bill": DepotHeadApi(c), "mat": mat, "acc_api": acc,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b",
         "supplier_name": f"{stamp}s", "customer_name": f"{stamp}c",
         "bill_ids": []}
    assert_code_ok(sup.add(s["supplier_name"], "供应商"), "供应商")
    assert_code_ok(sup.add(s["customer_name"], "客户"), "客户")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["supplier_id"] = sup.find_id_by_name(s["supplier_name"])
    s["customer_id"] = sup.find_id_by_name(s["customer_name"])
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def make_bill(no_prefix, typ, subt, organ_id, num, price):
        no = no_prefix + s["bill"].build_number()
        sign = -1 if typ == "入库" else 1
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": typ, "subType": subt, "organId": organ_id,
                "accountId": s["account_id"], "changeAmount": sign * num * price,
                "discountLastMoney": num * price, "totalPrice": sign * num * price,
                "status": "0", "payType": "现付"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": num, "unitPrice": price, "allPrice": num * price, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": num * price, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), f"{subt}单{no}")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        return no

    s["make_bill"] = make_bill
    make_bill("CGRK", "入库", "采购", s["supplier_id"], 3, 10)
    make_bill("XSCK", "出库", "销售", s["customer_id"], 1, 20)
    make_bill("LSCK", "出库", "零售", "", 1, 20)
    yield s
    for bid in s["bill_ids"]:
        s["bill"].delete_batch([bid])
    mat.delete_batch([s["material_id"]])
    sup.delete_batch([s["supplier_id"], s["customer_id"]])
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


def _stock_row(s, data):
    rows = [r for r in (data.get("rows") or []) if r.get("barCode") == s["bar_code"]]
    assert rows, f"报表未找到商品 {s['bar_code']}: 数据行 {len(data.get('rows') or [])}"
    return rows[0]


@allure.feature("报表查询")
@pytest.mark.usefixtures("env_check")
class TestReport:

    @allure.title("API-RPT-001 进销存公式:thisSum=inSum-outSum=1(含未审核,R4+db_check)")
    @pytest.mark.db_check
    def test_001_in_out_stock_formula(self, report_env):
        s = report_env
        row = _stock_row(s, s["rpt"].in_out_stock(material_param=s["material_name"]))
        assert (float(row["inSum"]), float(row["outSum"]), float(row["thisSum"])) == (3.0, 2.0, 1.0), \
            f"入3出2结存1,实际 in={row.get('inSum')} out={row.get('outSum')} this={row.get('thisSum')}"

    @allure.title("API-RPT-002 未审核单计入进销存:再入5→inSum=8(db_check,commit 8f5dc08 回归)")
    @pytest.mark.db_check
    def test_002_unapproved_counted(self, report_env):
        s = report_env
        before = float(_stock_row(s, s["rpt"].in_out_stock(
            material_param=s["material_name"]))["inSum"])
        s["make_bill"]("QTRK", "入库", "其它", "", 5, 10)
        after = float(_stock_row(s, s["rpt"].in_out_stock(
            material_param=s["material_name"]))["inSum"])
        assert after == before + 5.0, f"未审核入库应计入统计(R4): {before}→{after}"

    @allure.title("API-RPT-004 入库明细与汇总一致:operNumberTotal=numSumTotal(db_check)")
    @pytest.mark.db_check
    def test_004_detail_vs_summary(self, report_env):
        s = report_env
        detail = s["rpt"].find_in_out_detail("入库", material_param=s["material_name"])
        summary = s["rpt"].in_out_material_count("入库", material_param=s["material_name"])
        d_total = float(detail.get("operNumberTotal") or 0)
        s_total = float(summary.get("numSumTotal") or 0)
        assert d_total == s_total > 0, \
            f"明细汇总 {d_total} 应与汇总接口 {s_total} 一致且>0"

    @allure.title("API-RPT-005 出库明细=销售+零售共2,与单据口径一致(db_check)")
    @pytest.mark.db_check
    def test_005_outbound_detail(self, report_env):
        s = report_env
        detail = s["rpt"].find_in_out_detail("出库", material_param=s["material_name"])
        assert float(detail.get("operNumberTotal") or 0) == 2.0, \
            f"出库明细应=2(销售1+零售1): {detail.get('operNumberTotal')}"

    @allure.title("API-RPT-006 采购统计对账:inSum=3/inSumPrice=30(db_check)")
    @pytest.mark.db_check
    def test_006_buy_in(self, report_env):
        s = report_env
        rows = [r for r in (s["rpt"].buy_in(material_param=s["material_name"]).get("rows") or [])
                if r.get("barCode") == s["bar_code"]]
        assert rows and float(rows[0]["inSum"]) == 3.0 and float(rows[0]["inSumPrice"]) == 30.0, \
            f"采购统计应对账入库单: {rows}"

    @allure.title("API-RPT-007 销售统计对账:金额=20(db_check)")
    @pytest.mark.db_check
    def test_007_sale_out_stat(self, report_env):
        s = report_env
        rows = [r for r in (s["rpt"].sale_out(material_param=s["material_name"]).get("rows") or [])
                if r.get("barCode") == s["bar_code"]]
        assert rows and float(rows[0]["outInSumPrice"]) == 20.0, f"销售统计金额应=20: {rows}"

    @allure.title("API-RET-010 零售统计对账:金额=20(db_check)")
    @pytest.mark.db_check
    def test_010_retail_stat(self, report_env):
        s = report_env
        rows = [r for r in (s["rpt"].retail_out(material_param=s["material_name"]).get("rows") or [])
                if r.get("barCode") == s["bar_code"]]
        assert rows and float(rows[0]["outInSumPrice"]) == 20.0, f"零售统计金额应=20: {rows}"

    @allure.title("API-RPT-008 客户对账:含新建客户行(db_check)")
    @pytest.mark.db_check
    def test_008_customer_statement(self, report_env):
        s = report_env
        data = s["rpt"].statement_account("客户", organ_id=s["customer_id"])
        names = [r.get("supplier") for r in (data.get("rows") or [])]
        assert s["customer_name"] in names, f"对账应含新建客户: {names[:5]}"

    @allure.title("API-RPT-009 供应商对账:含新建供应商行(db_check)")
    @pytest.mark.db_check
    def test_009_supplier_statement(self, report_env):
        s = report_env
        data = s["rpt"].statement_account("供应商", organ_id=s["supplier_id"])
        names = [r.get("supplier") for r in (data.get("rows") or [])]
        assert s["supplier_name"] in names, f"对账应含新建供应商: {names[:5]}"

    @allure.title("API-RPT-010 库存预警接口可达且结构正确")
    @pytest.mark.regression
    def test_010_stock_warning(self, report_env):
        data = report_env["rpt"].stock_warning()
        assert isinstance(data.get("total"), int) and isinstance(data.get("rows"), list), \
            f"库存预警结构异常: {str(data)[:150]}"

    @allure.title("API-RPT-011 账户统计:name 过滤单账户=期初金额(db_check)")
    @pytest.mark.db_check
    def test_011_account_statistics(self, report_env):
        """全局 allCurrentAmount 与 DB current_amount 列不同源(接口为实时计算口径,
        环境含历史业务账户余额),故用单账户过滤对账:新账户无流水,统计应=期初。"""
        s = report_env
        acc_name = exp_id("EXPFacc")
        assert_code_ok(s["acc_api"].add(acc_name, initial=123), "对账账户")
        try:
            acc_id = s["acc_api"].find_id_by_name(acc_name)
            body = s["client"].get("/account/getStatistics",
                                   params={"name": acc_name, "serialNo": ""}).json()
            got = float(body["data"].get("allCurrentAmount") or 0)
            assert got == 123.0, f"单账户统计应=期初123,实际 {got}"
        finally:
            s["acc_api"].delete_batch([acc_id])

    @allure.title("API-RPT-012 首页统计即时联动:造销售20元 todaySale 增20(db_check)")
    @pytest.mark.db_check
    def test_012_home_statistics_delta(self, report_env):
        s = report_env
        before = float(s["rpt"].buy_and_sale_statistics().get("todaySale") or 0)
        s["make_bill"]("XSCK", "出库", "销售", s["customer_id"], 1, 20)
        after = float(s["rpt"].buy_and_sale_statistics().get("todaySale") or 0)
        assert after - before == 20.0, f"今日销售应即时+20(R4): {before}→{after}"

    @allure.title("API-DEP-011 商品库存查询:按名过滤含商品行(db_check)")
    @pytest.mark.db_check
    def test_011b_list_with_stock(self, report_env):
        s = report_env
        data = s["rpt"].list_with_stock(material_param=s["material_name"])
        rows = [r for r in (data.get("rows") or []) if r.get("barCode") == s["bar_code"]]
        assert rows or data.get("total", 0) >= 1, \
            f"按名过滤库存查询应含新建商品: total={data.get('total')}"

    @allure.title("API-DEP-012 库存流水重算=结存:thisSum 与 current_stock 一致(db_check)")
    @pytest.mark.db_check
    def test_012b_flow_vs_stock(self, report_env):
        s = report_env
        row = _stock_row(s, s["rpt"].in_out_stock(material_param=s["material_name"]))
        stock = s["db"].query_one(
            "SELECT current_number FROM jsh_material_current_stock "
            "WHERE material_id=%s AND depot_id=%s AND tenant_id=%s AND delete_flag=0",
            (s["material_id"], s["depot_id"], s["tenant_id"]))
        assert float(row["thisSum"]) == float(stock["current_number"]), \
            f"流水重算结存 {row['thisSum']} 应=DB库存 {stock['current_number']}"
