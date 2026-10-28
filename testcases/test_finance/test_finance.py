"""财务管理核心(API-FIN-004/005/006/008/009/014/015):收款核销、优惠记账、余额生效时点。

取证(2026-10-27):
- 财务单 info/rows 与 depotHead 同构;收款 eachAmount 正、付款负(落库 total_price 负);
- 核销联动通道是 GET /depotHead/debtList 的 finishDebt(最终欠款=debt-finishDebt);
  源单 getDetailByNumber 的 debt 与 jsh_supplier.all_need_get 均不随核销变化(实测);
- 未审核财务单不动 DB 余额(F9),审核后 DB current_amount 仍不动(2026-10-27 实测)——
  页面余额走 listWithBalance 实时计算,口径验证归报表对账批次;
- 缺 type → code 500 未知异常(DEF-EXP-02 复现,缺陷回归断言)。
"""
import json
import logging
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

log = logging.getLogger("jsherp")


@pytest.fixture(scope="module")
def fin_env(auth, db):
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "tenant_id": auth["tenant_id"], "db": db,
         "sup": sup, "acc_head": AccountHeadApi(c), "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b",
         "bill_ids": [], "fin_ids": []}
    assert_code_ok(sup.add(f"{stamp}cus", "客户"), "客户")
    assert_code_ok(sup.add(f"{stamp}sup", "供应商"), "供应商")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a", initial=0), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    s["customer_id"] = sup.find_id_by_name(f"{stamp}cus")
    s["supplier_id"] = sup.find_id_by_name(f"{stamp}sup")
    s["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    s["account_id"] = acc.find_id_by_name(f"{stamp}a")
    s["material_id"] = mat.find_by_name(s["material_name"])["id"]

    def sale_out(debt):
        """建销售出库产生客户欠款 debt(不收款)。"""
        no = "XSCK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "出库", "subType": "销售", "organId": s["customer_id"],
                "accountId": s["account_id"], "changeAmount": 0,
                "discountLastMoney": debt, "totalPrice": debt, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"], "depotId": s["depot_id"],
                 "operNumber": 1, "unitPrice": debt, "allPrice": debt, "taxRate": 0,
                 "taxMoney": 0, "taxLastMoney": debt, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), f"出库单{no}")
        s["bill_ids"].append(s["bill"].get_detail_by_number(no).json()["data"]["id"])
        return no

    def fin_bill(type_, organ_id, total_price, rows, discount=""):
        prefix = {"收款": "SKD", "付款": "FKD", "收入": "YSD", "支出": "ZCD", "转账": "ZZD"}[type_]
        no = prefix + s["acc_head"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": type_,
                "billNo": no, "organId": organ_id, "handsPersonId": "",
                "billTime": datetime.now().strftime("%Y-%m-%d"), "accountId": s["account_id"],
                "totalPrice": total_price, "discountMoney": discount,
                "discountDeposit": "", "remark": "AUTO"}
        assert_code_ok(s["acc_head"].add(info, rows), f"{type_}单{no}")
        s["fin_ids"].append(s["acc_head"].get_detail_by_number(no).json()["data"]["id"])
        return no

    s.update({"sale_out": sale_out, "fin_bill": fin_bill})
    yield s
    # teardown:财务单(先反审核)→业务单→资料;删除失败记录不中断
    for fid in s["fin_ids"]:
        row = db.query_one("SELECT status FROM jsh_account_head WHERE id=%s", (fid,))
        if row and str(row["status"]) == "1":
            s["acc_head"].batch_set_status("0", [fid])
        body = s["acc_head"].delete_batch([fid]).json()
        if body.get("code") != 200:
            log.warning("teardown 财务单 %s 删除失败: %s", fid, body)
    for bid in s["bill_ids"]:
        row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
        if row and str(row["status"]) == "1":
            s["bill"].batch_set_status("0", [bid])
        body = s["bill"].delete_batch([bid]).json()
        if body.get("code") != 200:
            log.warning("teardown 单据 %s 删除失败: %s", bid, body)
    mat.delete_batch([s["material_id"]])
    for name in (s["customer_id"], s["supplier_id"]):
        body = sup.delete_batch([name]).json()
        if body.get("code") != 200:
            log.warning("teardown 往来 %s 删除失败(可能仍有依赖): %s", name, body)
    depot.delete_batch([s["depot_id"]])
    acc.delete_batch([s["account_id"]])


def _finish_debt(s, organ_id):
    """欠款单列表(debtList):返回 [(number, debt, finishDebt)],核销联动口径。"""
    body = s["client"].get("/depotHead/debtList", params={
        "search": json.dumps({"organId": organ_id}, ensure_ascii=False),
        "currentPage": 1, "pageSize": 50}).json()
    rows = (body.get("data") or {}).get("rows") or []
    return [(r["number"], float(r["debt"]), float(r.get("finishDebt") or 0)) for r in rows]


def _account_amount(s):
    row = s["db"].query_one(
        "SELECT current_amount FROM jsh_account WHERE id=%s AND tenant_id=%s",
        (s["account_id"], s["tenant_id"]))
    return float(row["current_amount"])


@allure.feature("财务管理")
@pytest.mark.usefixtures("env_check")
class TestFinance:

    @allure.title("API-FIN-004 收款核销客户欠款:核销后 debtList 行移除(最终欠款0,db_check)")
    @pytest.mark.db_check
    def test_004_receipt_write_off(self, fin_env):
        s = fin_env
        bill_no = s["sale_out"](20)
        rows = [r for r in _finish_debt(s, s["customer_id"]) if r[0] == bill_no]
        assert rows and rows[0][1] == 20.0 and rows[0][2] == 0.0, \
            f"出库后欠款应 debt=20/finishDebt=0: {rows}"
        s["fin_bill"]("收款", s["customer_id"], 20,
                      rows=[{"billNumber": bill_no, "eachAmount": 20}])
        rows = [r for r in _finish_debt(s, s["customer_id"]) if r[0] == bill_no]
        assert rows == [], \
            f"足额核销后最终欠款0,debtList 应不再显示该单(实测口径): {rows}"

    @allure.title("API-FIN-005 收款优惠不影响应收:按实付80核销100欠款(db_check)")
    @pytest.mark.db_check
    def test_005_discount_not_offset(self, fin_env):
        s = fin_env
        bill_no = s["sale_out"](100)
        s["fin_bill"]("收款", s["customer_id"], 80,
                      rows=[{"billNumber": bill_no, "eachAmount": 80}], discount=20)
        rows = [r for r in _finish_debt(s, s["customer_id"]) if r[0] == bill_no]
        # debtList 字段语义(实测):debt=当前未核销余额,finishDebt=已核销
        assert rows and rows[0][2] == 80.0 and rows[0][1] == 20.0, \
            f"优惠20不参与核销:已核销80,剩余欠款=100-80=20(R2): {rows}"

    @allure.title("API-FIN-006 付款单落库:total_price=-80/discount_money=20/明细each_amount=+80(db_check)")
    @pytest.mark.db_check
    def test_006_payment_db(self, fin_env):
        s = fin_env
        no = s["fin_bill"]("付款", s["supplier_id"], -80,
                           rows=[{"billNumber": "", "eachAmount": -80}], discount=20)
        head = s["db"].query_one(
            "SELECT total_price, discount_money, change_amount, delete_flag "
            "FROM jsh_account_head WHERE bill_no=%s AND tenant_id=%s", (no, s["tenant_id"]))
        assert head and str(head["delete_flag"]) == "0"
        assert float(head["total_price"]) == -80.0, f"total_price 应=-80: {head}"
        assert float(head["discount_money"]) == 20.0, f"discount_money 应=20: {head}"
        item = s["db"].query_one(
            "SELECT each_amount FROM jsh_account_item WHERE header_id IN "
            "(SELECT id FROM jsh_account_head WHERE bill_no=%s AND tenant_id=%s)",
            (no, s["tenant_id"]))
        # 实测:明细 each_amount 落库取绝对值(+80),收支方向由头表 total_price 符号区分
        assert item and float(item["each_amount"]) == 80.0, f"明细应存+80(绝对值): {item}"

    @allure.title("API-FIN-014 未审核付款单不改变账户余额(F9,db_check)")
    @pytest.mark.db_check
    def test_014_unapproved_no_balance_change(self, fin_env):
        s = fin_env
        before = _account_amount(s)
        s["fin_bill"]("付款", s["supplier_id"], -80,
                      rows=[{"billNumber": "", "eachAmount": -80}])
        assert _account_amount(s) == before, "未审核财务单不应改变 DB 余额(F9)"

    @allure.title("API-FIN-015 付款单审核后 DB 余额仍不变(2026-10-27 实测,记录口径)")
    @pytest.mark.db_check
    def test_015_approved_balance_record(self, fin_env):
        s = fin_env
        no = s["fin_bill"]("付款", s["supplier_id"], -60,
                           rows=[{"billNumber": "", "eachAmount": -60}])
        fid = s["acc_head"].get_detail_by_number(no).json()["data"]["id"]
        before = _account_amount(s)
        assert_code_ok(s["acc_head"].batch_set_status("1", [fid]), "审核")
        after = _account_amount(s)
        assert after == before, \
            f"实测审核后 DB 余额不变,若变更说明口径调整: {before}→{after}"

    @allure.title("API-FIN-008 付款单缺失 type(带明细):NPE 500(DEF-EXP-02 缺陷回归)")
    @pytest.mark.regression
    def test_008_missing_type_npe(self, fin_env):
        s = fin_env
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "billNo": "FKD" + s["acc_head"].build_number()}
        # 需带明细才能越过 9500003(明细不能为空),命中缺 type 的 NPE
        body = s["acc_head"].add(info, [{"eachAmount": -10}]).json()
        assert body.get("code") == 500, f"缺陷现状应500未知异常,若变校验错误请翻转: {body}"

    @allure.title("API-FIN-009 付款明细缺失 billNumber:实测可建单(缺陷回归,关联核销失效)")
    @pytest.mark.regression
    def test_009_row_missing_bill_number(self, fin_env):
        """F4 记录为 NPE;2026-10-27 复测该场景建单成功(code200)且无法关联源单核销——
        断言当前实际行为(建单成功),修复为校验拦截或 NPE 后翻转。"""
        s = fin_env
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "付款",
                "billNo": "FKD" + s["acc_head"].build_number(), "organId": s["supplier_id"],
                "billTime": datetime.now().strftime("%Y-%m-%d"),
                "accountId": s["account_id"], "totalPrice": -10}
        body = s["acc_head"].add(info, [{"eachAmount": -10}]).json()
        assert body.get("code") == 200, f"实测缺关联可建单,若变拦截/NPE请翻转: {body}"
        s["fin_ids"].append(s["acc_head"].get_detail_by_number(info["billNo"]).json()["data"]["id"])

    @allure.title("API-FIN-007 付款核销供应商欠款(暂缓:断言通道待验证)")
    @pytest.mark.skip(reason="实测 debtList 不收录采购方向欠款;getStatementAccount 未审核单不计"
                      "(欠20后 debtMoney=0,付款后 backMoney=-20)——需审核链(建单→审核→付款→对账)"
                      "验证,留待下批实现")
    def test_007_payment_write_off(self, fin_env):
        """对称 FIN-004:采购入库欠款→付款核销→供应商 debtList finishDebt 变化。"""
        s = fin_env
        from datetime import datetime as dt
        no = "CGRK" + s["bill"].build_number()
        info = {"operTime": dt.now().strftime("%Y-%m-%d %H:%M:%S"), "number": no,
                "type": "入库", "subType": "采购", "organId": s["supplier_id"],
                "accountId": s["account_id"], "changeAmount": -20,
                "discountLastMoney": 20, "totalPrice": -20, "status": "0"}
        rows = [{"materialId": s["material_id"], "barCode": s["bar_code"],
                 "depotId": s["depot_id"], "operNumber": 1, "unitPrice": 20,
                 "allPrice": 20, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 20, "unit": "个"}]
        assert_code_ok(s["bill"].add(info, rows), "采购入库欠20")
        bill_id = s["bill"].get_detail_by_number(no).json()["data"]["id"]
        s["bill_ids"].append(bill_id)
        body = s["client"].get("/depotHead/debtList", params={
            "search": json.dumps({"organId": s["supplier_id"]}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()
        hit = [r for r in (body.get("data") or {}).get("rows") or [] if r.get("number") == no]
        assert hit and float(hit[0].get("debt") or 0) == 20.0,             f"供应商欠款应出现在 debtList: {body}"
        s["fin_bill"]("付款", s["supplier_id"], -20,
                      rows=[{"billNumber": no, "eachAmount": -20}])
        body2 = s["client"].get("/depotHead/debtList", params={
            "search": json.dumps({"organId": s["supplier_id"]}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()
        hit2 = [r for r in (body2.get("data") or {}).get("rows") or [] if r.get("number") == no]
        assert hit2 == [] or float(hit2[0].get("finishDebt") or 0) == 20.0,             f"付款核销后应结清(行移除或 finishDebt=20): {hit2}"
