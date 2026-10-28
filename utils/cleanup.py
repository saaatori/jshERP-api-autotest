"""残留数据清理工具(测试基础设施)。

设计:
- 仅清理自动化造数命名的内容:业务单据按单号前缀(CGRK/XSCK/CGDD/XSDD/QTRK/LSCK/DBCK,
  本环境初始 0 单据,这些前缀必为自动化产物),资料按 EXPF 前缀。
- 单据先走业务接口(已审核先反审核);status=2 已完成/3 部分执行的单按系统规则不可删
  (8500011/8500013),此时用 SQL 逻辑删除兜底——清理工具允许写库,业务断言仍只读。
- conftest 的 residue_guard fixture 每轮开跑前调用一次,实现自愈;
  也可独立运行:python -m utils.cleanup
"""
import logging
import re

log = logging.getLogger("jsherp")

BILL_PREFIXES = ("CGRK", "XSCK", "CGDD", "XSDD", "QTRK", "QTCK", "LSCK", "LSTK",
                 "DBCK", "CGTK", "XSTK", "ZZ", "CX")
FIN_PREFIXES = ("FKD", "SKD", "YSD", "ZCD", "ZZD", "SKQ")
AUTO_NAME_RE = re.compile(r"^(EXPF|E[0-9a-f]{6})")

SQL_BYID = {
    "depot_head": "UPDATE jsh_depot_head SET delete_flag=1 WHERE id=%s",
    "account_head": "UPDATE jsh_account_head SET delete_flag=1 WHERE id=%s",
    "material": "UPDATE jsh_material SET delete_flag=1 WHERE id=%s",
    "supplier": "UPDATE jsh_supplier SET delete_flag=1 WHERE id=%s",
    "depot": "UPDATE jsh_depot SET delete_flag=1 WHERE id=%s",
    "account": "UPDATE jsh_account SET delete_flag=1 WHERE id=%s",
    "unit": "UPDATE jsh_unit SET delete_flag=1 WHERE id=%s",
    "material_category": "UPDATE jsh_material_category SET delete_flag=1 WHERE id=%s",
    "role": "UPDATE jsh_role SET delete_flag=1 WHERE id=%s",
    "user": "UPDATE jsh_user SET delete_flag=1 WHERE id=%s",
    "user_business": "UPDATE jsh_user_business SET delete_flag=1 WHERE id=%s",
}


def _sql_flag(db, table, row_id, reason):
    db.execute(SQL_BYID[table], (row_id,))
    log.info("清理(SQL逻辑删) %s id=%s (%s)", table, row_id, reason)


def cleanup_residue(client, db) -> dict:
    """扫描并清理残留,返回统计。失败项仅记录不抛异常(自愈尽力而为)。"""
    stat = {"bills": 0, "fins": 0, "materials": 0, "suppliers": 0, "depots": 0, "accounts": 0,
        "units": 0, "categories": 0, "roles": 0, "users": 0, "orphan_ubs": 0}

    def rows(table, sql, args):
        try:
            return db.query(sql, args)
        except Exception as e:  # noqa: BLE001
            log.warning("清理扫描失败 %s: %s", table, e)
            return []

    # 1) 业务单据
    like = "number LIKE %s OR " * len(BILL_PREFIXES)
    bills = rows("depot_head",
                 f"SELECT id, status FROM jsh_depot_head WHERE tenant_id='63' AND delete_flag=0 AND ({like[:-4]})",
                 tuple(f"{p}%" for p in BILL_PREFIXES))
    for r in bills:
        bid = r["id"]
        if str(r["status"]) == "1":
            client.post("/depotHead/batchSetStatus",
                        json_body={"status": "0", "ids": f"{bid},"})
        body = client.delete("/depotHead/deleteBatch", params={"ids": f"{bid},"}).json()
        if body.get("code") == 200:
            stat["bills"] += 1
        else:  # 已完成/部分执行:系统规则不可删,SQL 兜底
            _sql_flag(db, "depot_head", bid, f"接口拒绝 {body.get('code')}")
            stat["bills"] += 1

    # 2) 财务单据
    like = "bill_no LIKE %s OR " * len(FIN_PREFIXES)
    fins = rows("account_head",
                f"SELECT id, status FROM jsh_account_head WHERE tenant_id='63' AND delete_flag=0 AND ({like[:-4]})",
                tuple(f"{p}%" for p in FIN_PREFIXES))
    for r in fins:
        fid = r["id"]
        if str(r["status"]) == "1":
            client.post("/accountHead/batchSetStatus",
                        json_body={"status": "0", "ids": f"{fid},"})
        body = client.delete("/accountHead/deleteBatch", params={"ids": f"{fid},"}).json()
        if body.get("code") == 200:
            stat["fins"] += 1
        else:
            _sql_flag(db, "account_head", fid, f"接口拒绝 {body.get('code')}")
            stat["fins"] += 1

    # 3) 基础资料(单据已清,接口删除一般可成功;601 依赖时 SQL 兜底)
    # 铁律:候选行必须"取到真实名称且名称匹配自动化特征";取不到名称一律跳过,宁漏勿误删
    # (2026-10-27 事故:SELECT 漏名称列导致空名分支误伤全部初始往来单位,已恢复)
    for table, name_col, sql, api in [
        ("material", "name", "SELECT id, name FROM jsh_material WHERE tenant_id='63' AND delete_flag=0", "/material/deleteBatch"),
        ("supplier", "supplier", "SELECT id, supplier FROM jsh_supplier WHERE tenant_id='63' AND delete_flag=0", "/supplier/deleteBatch"),
        ("depot", "name", "SELECT id, name FROM jsh_depot WHERE tenant_id='63' AND delete_flag=0", "/depot/deleteBatch"),
        ("account", "name", "SELECT id, name FROM jsh_account WHERE tenant_id='63' AND delete_flag=0", "/account/deleteBatch"),
    ]:
        candidates = []
        for r in rows(table, sql, ()):
            name = str(r.get(name_col) or "")
            if not name:  # 名称缺失(查询缺陷或数据异常)→ 跳过,绝不猜测
                log.warning("清理跳过 %s id=%s:名称列为空,人工确认", table, r["id"])
                continue
            if AUTO_NAME_RE.match(name):
                candidates.append(r["id"])
            elif table == "supplier" and not name.strip():
                candidates.append(r["id"])  # 空名往来=BAS-003 缺陷产物
        for rid in list(dict.fromkeys(candidates)):
            body = client.delete(api, params={"ids": f"{rid},"}).json()
            if body.get("code") == 200:
                stat[table + "s"] += 1
            else:
                _sql_flag(db, table, rid, f"接口拒绝 {body.get('code')}")
                stat[table + "s"] += 1

    # 4) 多单位(初始单位 id 15/19/20 之外的全部)
    for r0 in rows("unit", "SELECT id, name FROM jsh_unit WHERE tenant_id='63' AND delete_flag=0 "
                          "AND id NOT IN (15,19,20)", ()):
        body = client.delete("/unit/deleteBatch", params={"ids": f"{r0['id']},"}).json()
        if body.get("code") == 200:
            stat["units"] += 1
        else:
            _sql_flag(db, "unit", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["units"] += 1

    # 5) 商品类别(EXPF)
    for r0 in rows("category", "SELECT id FROM jsh_material_category WHERE tenant_id='63' "
                               "AND delete_flag=0 AND name LIKE 'EXPF%'", ()):
        body = client.delete("/materialCategory/deleteBatch", params={"ids": f"{r0['id']},"}).json()
        if body.get("code") == 200:
            stat["categories"] += 1
        else:
            _sql_flag(db, "material_category", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["categories"] += 1

    # 6) 角色/用户(EXPF/E8c/E+6hex;user_business 孤儿授权——指向已删用户/角色的记录)
    for table, name_col, sql, api, key in [
        ("role", "name", "SELECT id, name FROM jsh_role WHERE tenant_id='63' AND delete_flag=0", "/role/deleteBatch", None),
        ("user", "login_name", "SELECT id, login_name FROM jsh_user WHERE tenant_id='63' AND delete_flag=0", "/user/deleteBatch", None),
    ]:
        for r0 in rows(table, sql, ()):
            name = str(r0.get(name_col) or "")
            if not AUTO_NAME_RE.match(name) and not name.startswith("E8c"):
                continue
            body = client.delete(api, params={"ids": f"{r0['id']},"}).json()
            if body.get("code") == 200:
                stat[table + "s"] += 1
            else:
                _sql_flag(db, table, r0["id"], f"接口拒绝 {body.get('code')}")
                stat[table + "s"] += 1
    orphans = rows("user_business",
                   "SELECT ub.id FROM jsh_user_business ub WHERE ub.tenant_id='63' AND ub.delete_flag=0 AND ("
                   "(ub.type IN ('UserRole','UserDepot','UserCustomer') AND NOT EXISTS "
                   "(SELECT 1 FROM jsh_user u WHERE u.id=ub.key_id AND u.delete_flag=0)) "
                   "OR (ub.type='RoleFunctions' AND NOT EXISTS "
                   "(SELECT 1 FROM jsh_role r WHERE r.id=ub.key_id AND r.delete_flag=0)))", ())
    orphan_ids = ",".join(f"{r0['id']}," for r0 in orphans)
    if orphan_ids:
        body = client.delete("/userBusiness/deleteBatch", params={"ids": orphan_ids}).json()
        if body.get("code") == 200:
            stat["orphan_ubs"] = len(orphans)
        else:
            for r0 in orphans:
                _sql_flag(db, "user_business", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["orphan_ubs"] = len(orphans)
    return stat


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parent.parent)
    from common.client import HttpClient
    from common.config import CFG
    from utils.db import DB
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    c = HttpClient(CFG["base_url"], CFG["timeout"], CFG["request_min_interval"])
    from api.auth_api import AuthApi
    resp = AuthApi(c).login(CFG["admin"]["login_name"], CFG["admin"]["password"]).json()
    assert resp["data"]["msgTip"] == "user can login", resp
    c.token = resp["data"]["token"]
    _db = DB(**CFG["db"])
    print("清理统计:", cleanup_residue(c, _db))
    c.session.close()
    _db.close()
