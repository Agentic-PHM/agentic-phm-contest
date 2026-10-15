"""提交材料打包：按官方命名组装 zip，硬编码排除密钥与内部资产。

用法：python scripts/freeze_package.py --group 组别 --team 队伍 --captain 队长 [--entry 编号]
"""
import argparse
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 硬编码排除：任何情况下不进入提交包
EXCLUDE_DIRS = {".env", ".git", ".superpowers", "__pycache__", "node_modules",
                "docs", "vendor", "data", "agnes-harness", "agh-home", "dist_submit"}
EXCLUDE_FILES = {".env", "demo_video.mp4"}  # demo_video 由脚本单独显式收录（若存在）


def collect():
    """README + 证据 + 源码/构建产物（不含排除项）。"""
    items = []
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if not rel.parts or any(p in EXCLUDE_DIRS for p in rel.parts):
            continue
        if path.is_file():
            if path.name in EXCLUDE_FILES and rel.parts[0] != "":
                if path.name == "demo_video.mp4":
                    continue  # 单独处理
                continue
            if "__pycache__" in rel.parts or path.suffix == ".pyc":
                continue
            items.append(rel)
    return sorted(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--team", required=True)
    ap.add_argument("--captain", required=True)
    ap.add_argument("--entry", default="")
    args = ap.parse_args()

    name = f"{args.group}-{args.entry}-{args.team}-{args.captain}"
    out_dir = REPO / "dist_submit"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{name}.zip"

    video = REPO / "demo_video.mp4"
    if not video.exists():
        print("警告：demo_video.mp4 不在仓库根，录屏后放入再打包；本次先打其余材料。")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in collect():
            zf.write(REPO / rel, str(rel))
        if video.exists():
            zf.write(video, "demo_video.mp4")

    print(f"打包完成: {out}")
    print(f"文件数: {len(collect()) + (1 if video.exists() else 0)}")
    print("隔离确认: .env / vendor / docs / data 不在包内" )


if __name__ == "__main__":
    main()
