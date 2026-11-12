"""命令行入口：inspect 展示原始汇总，analyze 输出执行图归因报告。

analyze 不修改输入资料；使用 --out 写入其他目录时报告内保留输入文件的
sha256 指纹与单位，原始资料永不覆盖。
"""
import argparse
import json
import sys
from pathlib import Path

# 以 `python -I cli.py` 脚本方式运行时，隔离模式不会注入 cwd 与 PYTHONPATH，
# 需要显式把项目根目录加入导入路径；包方式 (-m) 运行时该路径已存在。
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from analysis_data.attribution import explain_payload
from analysis_data.domain import summarize
from analysis_data.io import load


def _dumps(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["inspect", "analyze"])
    parser.add_argument("file")
    parser.add_argument("--out", help="把 analyze 报告写入该路径，不覆盖原始资料")
    args = parser.parse_args()

    value, digest = load(args.file)
    if args.command == "inspect":
        print(_dumps({"source_sha256": digest, "summary": summarize(value)}))
        return

    report = explain_payload(value)
    report = {"source_sha256": digest, **report}
    text = _dumps(report)
    if args.out:
        out = Path(args.out)
        if out.resolve() == Path(args.file).resolve():
            raise ValueError("输出路径不能与输入资料相同，原始资料不覆盖")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf8")
        print(f"报告已写入 {out}（来源指纹 {digest}）")
    else:
        print(text)


if __name__ == "__main__":
    main()
