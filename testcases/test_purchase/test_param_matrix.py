"""采购入库参数异常矩阵(API-PUR-004~008):yaml 数据驱动 parametrize 展开。

2026-10-27 取证:5 组参数(数量 0/负/小数/10^9、负单价)全部放行建单成功——
数量与单价无校验,与供应商无唯一性/必填校验同源(系统性"校验不一致"缺陷)。
断言当前实际行为;系统修复后将 yaml 的 expect_created 翻转为 false。
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
from common.dataloader import load_cases
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def purchase_env(auth):
    """矩阵共享一套基础资料;成功建的单在 teardown 统一删除(未审核,无状态机障碍)。"""
    c = auth["client"]
    stamp = exp_id()
    sup = SupplierApi(c)
    depot, acc, mat = DepotApi(c), AccountApi(c), MaterialApi(c)
    env = {
        "client": c, "tenant_id": auth["tenant_id"],
        "sup": sup, "depot": depot, "acc": acc, "mat": mat, "bill": DepotHeadApi(c),
        "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": [],
    }
    assert_code_ok(sup.add(f"{stamp}s", "供应商"), "供应商新增")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库新增")
    assert_code_ok(acc.add(f"{stamp}a"), "账户新增")
    assert_code_ok(mat.add(env["material_name"], env["bar_code"]), "商品新增")
    env["supplier_id"] = sup.find_id_by_name(f"{stamp}s")
    env["depot_id"] = depot.find_id_by_name(f"{stamp}d")
    env["account_id"] = acc.find_id_by_name(f"{stamp}a")
    row = mat.find_by_name(env["material_name"])
    env["material_id"] = row["id"] if row else None
    missing = [k for k in ("supplier_id", "depot_id", "account_id", "material_id") if not env[k]]
    assert not missing, f"基础资料提取 ID 失败: {missing}"
    yield env
    if env["bill_ids"]:
        env["bill"].delete_batch(env["bill_ids"])
    mat.delete_batch([env["material_id"]])
    sup.delete_batch([env["supplier_id"]])
    depot.delete_batch([env["depot_id"]])
    acc.delete_batch([env["account_id"]])


@allure.feature("采购管理")
@pytest.mark.regression
@pytest.mark.parametrize("case", load_cases("purchase_param_matrix"),
                         ids=lambda c: c["case_id"])
class TestPurchaseParamMatrix:

    @allure.title("采购参数异常矩阵(数据驱动)")
    def test_param_case(self, purchase_env, case):
        s = purchase_env
        bill_no = "CGRK" + s["bill"].build_number()
        info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "number": bill_no, "type": "入库", "subType": "采购",
                "organId": s["supplier_id"], "accountId": s["account_id"],
                "changeAmount": -20, "discountLastMoney": 30, "totalPrice": -30,
                "status": "0"}
        row = {"materialId": s["material_id"], "barCode": s["bar_code"],
               "depotId": s["depot_id"], "operNumber": 3, "unitPrice": 10,
               "allPrice": 30, "taxRate": 0, "taxMoney": 0, "taxLastMoney": 30,
               "unit": "个"}
        row.update(case["row_overrides"])
        resp = s["bill"].add(info, [row])
        body = resp.json()
        if case["expect_created"]:
            assert_code_ok(resp, f"{case['case_id']}({case['title']})")
            detail = s["bill"].get_detail_by_number(bill_no).json()
            bill_id = detail["data"]["id"]
            s["bill_ids"].append(bill_id)
        else:
            assert body.get("code") != 200, \
                f"{case['case_id']} 修复后应被拦截,实际放行: {body}"
