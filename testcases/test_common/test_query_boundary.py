"""通用查询分页边界(API-COM-001~010):yaml 矩阵 + 翻页一致性。

矩阵断言统一:no 500、code=200、行数与 max_rows/empty 约束一致(COM-004/006/008/009
的实测行为只记录不预设)。COM-010 为代码型:逐页取数求和=total 且无重复行。
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
from common.dataloader import load_cases
from utils.dynamic import exp_id


@pytest.fixture(scope="module")
def query_env(auth):
    """预置一张采购入库单,供模糊查询与翻页一致性使用。"""
    c = auth["client"]
    stamp = exp_id()
    sup, depot, acc, mat = SupplierApi(c), DepotApi(c), AccountApi(c), MaterialApi(c)
    s = {"client": c, "bill": DepotHeadApi(c), "mat": mat,
         "material_name": f"{stamp}m", "bar_code": f"{stamp}b", "bill_ids": []}
    assert_code_ok(sup.add(f"{stamp}s", "供应商"), "供应商")
    assert_code_ok(depot.add(f"{stamp}d"), "仓库")
    assert_code_ok(acc.add(f"{stamp}a"), "账户")
    assert_code_ok(mat.add(s["material_name"], s["bar_code"]), "商品")
    supplier_id = sup.find_id_by_name(f"{stamp}s")
    depot_id = depot.find_id_by_name(f"{stamp}d")
    account_id = acc.find_id_by_name(f"{stamp}a")
    material_id = mat.find_by_name(s["material_name"])["id"]
    s["bill_no"] = "CGRK" + s["bill"].build_number()
    info = {"operTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "number": s["bill_no"],
            "type": "入库", "subType": "采购", "organId": supplier_id, "accountId": account_id,
            "changeAmount": -10, "discountLastMoney": 10, "totalPrice": -10, "status": "0"}
    rows = [{"materialId": material_id, "barCode": s["bar_code"], "depotId": depot_id,
             "operNumber": 1, "unitPrice": 10, "allPrice": 10, "taxRate": 0,
             "taxMoney": 0, "taxLastMoney": 10, "unit": "个"}]
    assert_code_ok(s["bill"].add(info, rows), "预置入库单")
    s["bill_ids"].append(s["bill"].get_detail_by_number(s["bill_no"]).json()["data"]["id"])
    s["teardown"] = (mat, material_id, sup, supplier_id, depot, depot_id, acc, account_id)
    yield s
    mat_d, material_id, sup_d, supplier_id, depot_d, depot_id, acc_d, account_id = s["teardown"]
    for bid in s["bill_ids"]:
        s["bill"].delete_batch([bid])
    mat_d.delete_batch([material_id])
    sup_d.delete_batch([supplier_id])
    depot_d.delete_batch([depot_id])
    acc_d.delete_batch([account_id])


def _query_list(client, case):
    params = dict(case["params"])
    if case.get("raw_search") is not None:
        return client.get("/depotHead/list", params={"search": case["raw_search"],
                                                     "currentPage": params.get("currentPage", 1),
                                                     "pageSize": params.get("pageSize", 10)})
    search = {k: v for k, v in params.items()
              if k in ("number", "beginTime", "endTime", "type", "subType", "materialParam")}
    page = {k: params[k] for k in ("currentPage", "pageSize") if k in params}
    return client.get("/depotHead/list", params={
        "search": json.dumps(search, ensure_ascii=False), **page})


@allure.feature("通用查询")
@pytest.mark.regression
class TestQueryBoundaryMatrix:

    @allure.title("查询分页边界矩阵(数据驱动)")
    @pytest.mark.parametrize("case", load_cases("com_query_boundary"),
                             ids=lambda c: c["case_id"])
    def test_query_matrix(self, query_env, case):
        resp = _query_list(query_env["client"], case)
        assert resp.status_code != 500 or resp.json().get("code") == 500, \
            f"{case['case_id']} HTTP 500: {resp.text[:120]}"
        body = resp.json()
        if case["case_id"] == "API-COM-009":
            # 实测(2026-10-27):非法 JSON search 触发 code 500"未知异常"——
            # 属健壮性缺陷(F3 关联),修复后应返回校验错误或空集,届时翻转断言
            assert body.get("code") == 500, f"当前缺陷行为应为500,实际: {body}"
            return
        assert body.get("code") == 200, f"{case['case_id']} code 异常: {str(body)[:150]}"
        data = body.get("data") or {}
        rows = data.get("rows") or []
        exp = case.get("expect", {})
        if exp.get("empty"):
            assert rows == [], f"{case['case_id']} 期望空集,实际 {len(rows)} 行"
        if "max_rows" in exp:
            assert len(rows) <= exp["max_rows"], \
                f"{case['case_id']} 行数超 pageSize: {len(rows)}"
        if exp.get("min_rows"):
            assert len(rows) >= exp["min_rows"], \
                f"{case['case_id']} 期望命中预置单据,实际 {len(rows)} 行"
        if exp.get("total_int"):
            assert isinstance(data.get("total"), int), f"total 应为整数: {data.get('total')}"

    @allure.title("API-COM-010 翻页一致性:各页求和=total 且无重复行")
    def test_pagination_consistency(self, query_env):
        client = query_env["client"]
        seen, total, page = [], None, 1
        while True:
            body = client.get("/depotHead/list", params={
                "search": json.dumps({"type": "入库", "subType": "采购", "number": "CGRK"},
                                     ensure_ascii=False),
                "currentPage": page, "pageSize": 10}).json()
            data = body["data"]
            total = data["total"]
            for r in data["rows"]:
                assert r["id"] not in seen, f"第{page}页出现重复行 id={r['id']}"
                seen.append(r["id"])
            if len(seen) >= total or not data["rows"]:
                break
            page += 1
            assert page <= 50, "翻页异常终止"
        assert len(seen) == total, f"各页行数合计 {len(seen)} != total {total}"
