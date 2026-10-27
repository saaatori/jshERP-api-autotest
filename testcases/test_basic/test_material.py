"""基础资料-商品管理:API-GOODS-001/002/020。

条码一律动态生成(EXPF+随机),DEF-EXP-07:删除后 extend 表条码仍占用,条码不可复用。
"""
import allure
import pytest

from api.material_api import MaterialApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@allure.feature("商品管理")
@pytest.mark.smoke
class TestMaterialPositive:

    @allure.title("API-GOODS-001 新增商品正向:列表可见,extend 条码落库")
    def test_001_add_material(self, auth, db):
        api = MaterialApi(auth["client"])
        name, barcode = exp_id("EXPFmat"), exp_id("EXPFbc")
        assert_code_ok(api.add(name, barcode), "商品新增")
        row = api.find_by_name(name)
        assert row, "列表未见新增商品"
        try:
            erow = db.query_one(
                "SELECT bar_code FROM jsh_material_extend "
                "WHERE material_id=%s AND delete_flag=0", (row["id"],))
            assert erow and erow["bar_code"] == barcode, f"extend 条码落库异常: {erow}"
        finally:
            api.delete_batch([row["id"]])


@allure.feature("商品管理")
@pytest.mark.regression
class TestMaterialNegative:

    @allure.title("API-GOODS-002 条码重复新增被拒(8000005)")
    def test_002_duplicate_barcode(self, auth):
        api = MaterialApi(auth["client"])
        barcode = exp_id("EXPFdup")
        name1, name2 = exp_id("EXPFm1"), exp_id("EXPFm2")
        assert_code_ok(api.add(name1, barcode), "第一个商品")
        try:
            body = api.add(name2, barcode).json()
            assert body.get("code") == 8000005, \
                f"预期 8000005(条码重复),实际: {body}"
        finally:
            for n in (name1, name2):
                r = api.find_by_name(n)
                if r:
                    api.delete_batch([r["id"]])

    @allure.title("API-GOODS-020 空条码新增——缺陷回归:实测可入库")
    def test_020_empty_barcode(self, auth):
        """冒烟实证空条码可入库(条码唯一性对空值不生效);
        修复后本用例预期应改为业务失败。"""
        api = MaterialApi(auth["client"])
        name = exp_id("EXPFempty")
        assert_code_ok(api.add(name, ""), "空条码新增(缺陷现状)")
        row = api.find_by_name(name)
        if row:
            api.delete_batch([row["id"]])
