"""基础资料-商家管理:API-BAS-001/002/003/008。"""
import allure
import pytest

from api.supplier_api import SupplierApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


@pytest.fixture()
def supplier_clean(auth):
    """记录本测试创建的往来单位名称,测试后按名定位删除(同名多条全删)。"""
    created = []
    yield created
    api = SupplierApi(auth["client"])
    for name in created:
        for sid in api.find_ids_by_name(name):
            api.delete_batch([sid])


@allure.feature("基础资料")
@pytest.mark.smoke
class TestSupplierPositive:

    @allure.title("API-BAS-001 新增供应商正向,DB 落库校验")
    def test_001_add_supplier(self, auth, db, supplier_clean):
        name = exp_id("EXPFsup")
        supplier_clean.append(name)
        assert_code_ok(SupplierApi(auth["client"]).add(name, "供应商"), "供应商新增")
        row = db.query_one(
            "SELECT supplier, type, delete_flag FROM jsh_supplier "
            "WHERE supplier=%s AND tenant_id=%s", (name, auth["tenant_id"]))
        assert row and row["type"] == "供应商" and str(row["delete_flag"]) == "0", \
            f"DB 落库异常: {row}"

    @allure.title("API-BAS-008 新增客户正向,DB 落库校验")
    def test_008_add_customer(self, auth, db, supplier_clean):
        name = exp_id("EXPFcus")
        supplier_clean.append(name)
        assert_code_ok(SupplierApi(auth["client"]).add(name, "客户"), "客户新增")
        row = db.query_one(
            "SELECT supplier, type, delete_flag FROM jsh_supplier "
            "WHERE supplier=%s AND tenant_id=%s", (name, auth["tenant_id"]))
        assert row and row["type"] == "客户" and str(row["delete_flag"]) == "0", \
            f"DB 落库异常: {row}"


@allure.feature("基础资料")
@pytest.mark.regression
class TestSupplierNegative:

    @allure.title("API-BAS-002 同名同类型重复新增——缺陷回归:实测无唯一性校验")
    def test_002_duplicate_name(self, auth, supplier_clean):
        """冒烟实证 /supplier/add 无唯一性校验,重复新增成功;
        修复后本用例预期应改为业务失败(checkIsNameAndTypeExist 拦截)。"""
        api = SupplierApi(auth["client"])
        name = exp_id("EXPFdup")
        supplier_clean.append(name)
        assert_code_ok(api.add(name, "供应商"), "第一次新增")
        assert_code_ok(api.add(name, "供应商"), "重复新增(缺陷现状:无唯一性校验)")

    @allure.title("API-BAS-003 供应商名称为空——缺陷回归:实测可创建")
    def test_003_empty_name(self, auth, db, supplier_clean):
        """与 BAS-002 同源的校验缺失(必填+唯一性均未拦截);
        修复后本用例预期应改为业务失败。"""
        api = SupplierApi(auth["client"])
        name = " "
        supplier_clean.append(name)  # 空格名可按名定位;真空串由下方 DB 兜底清理
        assert_code_ok(api.add(name, "供应商"), "空名新增(缺陷现状:必填未拦截)")
        row = db.query_one(
            "SELECT id FROM jsh_supplier WHERE supplier='' AND tenant_id=%s AND delete_flag=0",
            (auth["tenant_id"],))
        if row:
            api.delete_batch([row["id"]])
