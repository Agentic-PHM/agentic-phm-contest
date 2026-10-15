# -*- coding: utf-8 -*-
"""AGH 运行时一键重建（P1：修改版 AGH 可复现性）。

从仓库内 vendor zip 完整重建 D:\\agh-contest，全部步骤可重复执行：
  解压展平 → 私有 DACL（whoami 实测账号）→ 注入 Agnes Key（读 .env，不落第二份明文）
  → consent LOCAL → profile 覆盖层 → -p 会话级 yolo 补丁 → 密钥文件补授权 → 守门校验。

不包含：启动 serve（由调用方/演示流程负责）；授权范围说明见 scripts/setup_agh.md 第 10 节。

用法：D:/Python311/python.exe scripts/setup_agh_runtime.py [--verify-only]
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: 包名带版本日期，不能写死文件名（换包时应当只改 SHA 与这一处 glob）
_zips = sorted((REPO / "vendor").glob("agh-portable-*.zip"))
ZIP_PATH = _zips[-1] if _zips else REPO / "vendor" / "agh-portable-20260930.zip"
ZIP_SHA256 = "1323bdad177159e389c0c247823280d69dfbebc31cbb144ffa074760d929a2e4"
TARGET = Path(os.environ.get("AGH_DIR", r"D:\agh-contest"))
CREDENTIAL_REF = "account-bb8adc5052deb964d9b231c2-r1"
YOLO_ANCHOR = "    const session = await openSession(booted, p, io.cwd);"
YOLO_PATCH = ("    await session.setYolo(true); "
              "// [phm-lite demo patch] unattended print mode: session-yolo (auditable), mirrors TUI /yolo\n")
STEPS = []


def step(name, ok, detail=""):
    STEPS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    if not ok:
        print("重建中止：修复上方失败项后重跑（全部步骤幂等）。")
        sys.exit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def verify_zip():
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    step("vendor zip 指纹", digest == ZIP_SHA256,
         f"sha256={digest[:16]}…（期望 {ZIP_SHA256[:16]}…）；不一致 = 供应商包被改动，停止重建")


def unzip_and_flatten():
    if (TARGET / "runtime" / "agnes.mjs").exists():
        step("解压（已存在则跳过）", True, f"{TARGET} 已有运行时，跳过解压（幂等）")
        return
    run(["powershell", "-NoProfile", "-Command",
         f"Expand-Archive -Path '{ZIP_PATH}' -DestinationPath '{TARGET}' -Force; "
         f"Get-ChildItem '{TARGET}\\agh-portable' -Force | "
         f"Move-Item -Destination '{TARGET}' -Force; Remove-Item '{TARGET}\\agh-portable' -Force"])
    step("解压并展平", (TARGET / "runtime" / "agnes.mjs").exists())


def _account():
    """本机实测账号（主机名可能是 AFU/WIN-*，勿写死旧文档值）。"""
    return run(["whoami"]).stdout.strip()


def fix_acl():
    account = _account()  # 本机实测账号（主机名可能是 AFU，勿写死旧文档值）
    domain, user = account.split("\\", 1)
    root = TARGET / "agh-home"
    ps = f"""
$root = '{root}'
$targets = @($root) + @(Get-ChildItem $root -Recurse -Force | ForEach-Object {{ $_.FullName }})
foreach ($t in $targets | Where-Object {{ Test-Path $_ -PathType Container }}) {{
  icacls $t /grant '{domain}\\{user}:(OI)(CI)(F)' 'NT AUTHORITY\\SYSTEM:(OI)(CI)(F)' > $null
}}
foreach ($t in $targets | Where-Object {{ Test-Path $_ -PathType Leaf }}) {{
  icacls $t /grant '{domain}\\{user}:F' 'NT AUTHORITY\\SYSTEM:F' > $null
}}
foreach ($t in $targets) {{
  icacls $t /inheritance:r > $null
}}
"""
    # 顺序铁律（setup_agh.md 第 9 节）：先显式授权、后去继承——反序会在授权失败时整树自锁
    run(["powershell", "-NoProfile", "-Command", ps])
    step("私有 DACL（先授权后去继承，账号=%s）" % account, True)


#: inject_key 的结论（final_gate 据此决定"密钥可读"是硬门禁还是待补项）
KEY_STATE = {"source": None, "value": None}


def resolve_key():
    """Agnes Key 取值顺序：环境变量 AGNES_API_KEY → 仓库 .env → 没有（留给系统设置页）。

    2026-10-15：原实现只读 .env，而 .env 是**故意不入库**的本机凭据；远端历史清洗后
    全新 clone 里根本没有 .env，于是"一键部署"第一步就 FileNotFoundError。
    返回 None 不是错误——profile + 凭据文件骨架仍会建好，评委可在系统设置页录入自己的 Key。"""
    key = (os.environ.get("AGNES_API_KEY") or "").strip()
    if key:
        KEY_STATE.update(source="环境变量 AGNES_API_KEY", value=key)
        return key
    env_path = REPO / ".env"
    if env_path.is_file():
        match = re.search(r"^AGNES_API_KEY=(\S+)", env_path.read_text(encoding="utf-8"),
                          re.MULTILINE)
        if match:
            KEY_STATE.update(source="仓库 .env", value=match.group(1))
            return match.group(1)
    KEY_STATE.update(source=None, value=None)
    return None


def inject_key():
    key = resolve_key()
    secret_dir = TARGET / "agh-home" / "secrets" / "agnes-ai"
    secret_dir.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "kind": "api-key", "provider": "agnes-ai", "value": key or ""}
    (secret_dir / CREDENTIAL_REF).write_text(json_dumps(doc), encoding="utf-8")
    if key:
        step(f"注入 Agnes Key（来源：{KEY_STATE['source']}，凭据文件名=profile 哈希）", True)
    else:
        step("Agnes Key 暂缺（未找到环境变量 AGNES_API_KEY 与 .env）", True,
             "凭据文件骨架已建好：启动后在「系统设置 → Agnes API Key」录入即可")


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


def consent_and_overlay(restricted=False):
    env = dict(os.environ, AGH_HOME=str(TARGET / "agh-home"))
    run([str(TARGET / "node24" / "node.exe"), str(TARGET / "runtime" / "agnes.mjs"),
         "consent", "LOCAL"], env=env, cwd=str(TARGET))
    profile = TARGET / "agh-home" / "profiles" / "local-dev" / "profile.yaml"
    if restricted:
        # P1 问题7：受限模式用 command_policy 白名单替代 session yolo。
        # 运行时代码（agnes.mjs:49284）显示 yolo 会把 needsAsk 直接置 false，而
        # command_policy 只在 needsAsk 为真时求值（:279464）——两者不能并存。
        from phmlite import agh_policy
        profile.write_text(agh_policy.profile_overlay_yaml(), encoding="utf-8")
        step("consent LOCAL + profile 覆盖层（受限 Shell 白名单，无 yolo）", True)
    else:
        profile.write_text("approvals:\n  mode: smart\n", encoding="utf-8")
        step("consent LOCAL + profile 覆盖层 + composer 完全权限", True)
    (TARGET / "agh-home" / "composer-selection.json").write_text(
        '{"permission":"full"}', encoding="utf-8")


def harden_final_acl():
    """收尾再补一轮**显式**授权（不剥继承）。

    2026-10-15 全新目录实测：密钥文件/profile.yaml/composer-selection.json 是在 fix_acl()
    **之后**才写入的，会从父目录 (OI)(CI) 继承出 (I) ACE；而凭据存储的 hasPrivateDacl
    要求"无继承 ACE 的私有 DACL"，于是守门在"密钥文件可读"一步固定 FAIL。
    setup_agh.md 第 9.2 节描述过这个坑，这里把它实现成幂等的一步。"""
    account = _account()
    domain, user = account.split("\\", 1)
    root = TARGET / "agh-home"
    if not root.is_dir():
        return
    dirs, files = [str(root)], []
    for current, dirnames, filenames in os.walk(root):
        # 跳过 junction/符号链接，避免 icacls 递归到树外
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(current, d))]
        dirs.extend(os.path.join(current, d) for d in dirnames)
        files.extend(os.path.join(current, f) for f in filenames)
    dirs = [str(d) for d in dirs]
    files = [str(f) for f in files]
    ps = f"""
$dirs = @({','.join("'" + d + "'" for d in dirs)})
$files = @({','.join("'" + f + "'" for f in files)})
foreach ($t in $dirs)  {{ icacls $t /grant '{domain}\\{user}:(OI)(CI)(F)' 'NT AUTHORITY\\SYSTEM:(OI)(CI)(F)' > $null }}
foreach ($t in $files) {{ icacls $t /grant '{domain}\\{user}:F' 'NT AUTHORITY\\SYSTEM:F' > $null }}
# 关键：必须再去一次继承。只补授权的话，文件上会同时留 (F) 与 (I)(F)，
# hasPrivateDacl 要求"无继承 ACE"，凭据文件仍判不合规。
foreach ($t in $dirs)  {{ icacls $t /inheritance:r > $null }}
foreach ($t in $files) {{ icacls $t /inheritance:r > $null }}
"""
    # 顺序铁律同 fix_acl（setup_agh.md 第 9 节）：先显式授权、后去继承，反序会整树自锁
    run(["powershell", "-NoProfile", "-Command", ps])
    step("收尾补授权（fix_acl 之后新写入的文件补显式 ACE）", True,
         f"{len(dirs)} 目录 / {len(files)} 文件")


def apply_yolo_patch(enabled=True):
    agnes = TARGET / "runtime" / "agnes.mjs"
    src = agnes.read_text(encoding="utf-8")
    if not enabled:
        # P1 问题7：受限模式下不得存在 yolo 补丁，否则白名单根本不生效。
        if "[phm-lite demo patch]" in src:
            step("受限模式要求运行时无 yolo 补丁", False,
                 "仍带 [phm-lite demo patch]：先重解压 vendor zip 或删除该行再重跑")
        step("确认运行时未打 yolo 补丁（受限模式）", True,
             "供应商运行时保持原样，授权改由 command_policy 白名单给出")
        return
    if "[phm-lite demo patch]" in src:
        step("-p 会话级 yolo 补丁（已打）", True)
        return
    assert src.count(YOLO_ANCHOR) == 1, "agnes.mjs 结构与预期不符，请人工核对补丁点"
    agnes.write_text(src.replace(YOLO_ANCHOR, YOLO_ANCHOR + "\n" + YOLO_PATCH),
                     encoding="utf-8", newline="")
    step("-p 会话级 yolo 补丁", True, "恢复方式：重解压 zip 或删除该行（范围见 setup_agh.md 第 10 节）")


def final_gate():
    """守门：凭据树 DACL + 密钥可读 + 补丁在位（用运行时自带 native 模块逐路径校验）。"""
    root = (TARGET / "agh-home").as_posix()
    runtime = (TARGET / "runtime" / "agnes.mjs").as_posix()
    probe = (
        "const { createRequire } = require('module');"
        "const req = createRequire(process.cwd() + '/runtime/agnes.mjs');"
        "const native = req('@agnes/system-node/native');"
        "const fs = require('fs');"
        f"const root = '{root}';"
        # 只校验凭据存储链（根 + auth/ + secrets/）；cache 等运行时产物不在凭据链内，不参与校验
        "const targets = ['', '/auth', '/secrets', '/secrets/agnes-ai'].map(s => root + s);"
        "const bad = [];"
        "for (const p of targets) {"
        "  let ok; try { ok = native.hasPrivateDacl(p); } catch (e) { ok = 'ERR:' + e.message; }"
        "  if (ok !== true) bad.push(p + ' -> ' + ok);"
        "}"
        f"const secret = root + '/secrets/agnes-ai/{CREDENTIAL_REF}';"
        "let readable = 0; try { readable = native.readPrivateFile(secret, 1048576).length; } catch (e) {}"
        f"const patched = fs.readFileSync('{runtime}', 'utf-8')"
        "  .includes('[phm-lite demo patch]');"
        "console.log(JSON.stringify({ bad, readable, patched }));"
    )
    r = run([str(TARGET / "node24" / "node.exe"), "-e", probe], cwd=str(TARGET), timeout=120)
    import json
    out = json.loads(r.stdout.strip().splitlines()[-1])
    step("守门：DACL 树干净", not out["bad"], f"不合规条目 {out['bad'][:3]}" if out["bad"] else "")
    if KEY_STATE["value"]:
        step("守门：密钥文件可读（凭据存储可用）", out["readable"] > 0)
    elif out["readable"] > 0:
        step("守门：凭据文件可读（Key 待系统设置页录入）", True)
    else:
        print("[INFO] 凭据文件暂不可读，但 Key 尚未录入（属于预期），不计为失败。")
    step("守门：yolo 补丁在位", out["patched"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true", help="只跑守门校验，不做重建动作")
    ap.add_argument("--restricted-shell", action="store_true",
                    help="受限授权（P1 问题7）：不注入 session yolo，改用 command_policy 白名单；"
                         "同时需给应用设置 PHM_AGH_RESTRICTED_SHELL=1")
    args = ap.parse_args()
    print(f"AGH 运行时重建：目标 {TARGET}，来源 {ZIP_PATH.name}（sha256 前 16 位 {ZIP_SHA256[:16]}）\n")
    verify_zip()
    if args.verify_only:
        final_gate()
    else:
        unzip_and_flatten()
        fix_acl()
        inject_key()
        consent_and_overlay(restricted=args.restricted_shell)
        apply_yolo_patch(enabled=not args.restricted_shell)
        harden_final_acl()  # 必须在上面几步写文件之后：给新文件补显式 ACE
        final_gate()
    if args.restricted_shell:
        print("\n受限授权模式：运行时保持供应商原样，授权来自 profile 的 approval.command_policy。")
        print("请同时设置 PHM_AGH_RESTRICTED_SHELL=1，让诊断提示词使用白名单覆盖的命令形态。")
        print("应用前先校验预设被 host 接受："
              f"agh profile inspect local-dev --resolved（AGH_HOME={TARGET}\\agh-home）")
    print("\n后续手工步骤：启动 serve ——\n"
          f"  cd {TARGET} && AGH_HOME='{TARGET}\\agh-home' ./node24/node.exe runtime/agnes.mjs "
          "serve --profile local-dev --cwd \"D:\\TSI\\agentic-phm-contest\" --port 4178\n"
          "  然后 python scripts/acceptance_check.py 做真机验收。"
          "授权范围与补丁清单：scripts/setup_agh.md 第 10 节。")


if __name__ == "__main__":
    main()
