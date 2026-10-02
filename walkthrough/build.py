"""
Flow walkthrough builder.

flows/**/*.yaml を読み込み、検証して次を出力する:
  - flows.json                 … index.html (ビューア) が読み込むデータ
  - dist/walkthrough.html      … データ埋め込み済みの単体HTML（サーバ不要で開ける）
  - dist/flows-for-ai.md       … AI/LLM に渡すための全フロー Markdown

Usage:
  python build.py            # build
  python build.py --check    # 検証のみ（CI 用、エラー時は exit 1）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
FLOWS_DIR = ROOT / "flows"
DIST_DIR = ROOT / "dist"
REPO_URL = "https://github.com/duythuong97/ocr-demo"
REPO_BRANCH = "main"
REPO_SUBDIR = "walkthrough"

REQUIRED_FLOW_KEYS = ("id", "title", "system", "summary", "steps")
REQUIRED_STEP_KEYS = ("id", "title")
END_ID = "__end__"


# ── Load & validate ────────────────────────────────────────────────────────────

def load_flows() -> tuple[list[dict], list[str], list[str]]:
    flows, errors, warnings = [], [], []
    seen_ids: dict[str, Path] = {}

    for path in sorted(FLOWS_DIR.rglob("*.y*ml")):
        if path.name.startswith("_"):
            continue  # テンプレート等
        rel = path.relative_to(ROOT).as_posix()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as ex:
            errors.append(f"{rel}: YAML parse error: {ex}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{rel}: top level must be a mapping")
            continue

        errs, warns = validate_flow(data)
        errors += [f"{rel}: {e}" for e in errs]
        warnings += [f"{rel}: {w}" for w in warns]
        if errs:
            continue

        if data["id"] in seen_ids:
            errors.append(f"{rel}: duplicate flow id '{data['id']}' (also in {seen_ids[data['id']]})")
            continue
        seen_ids[data["id"]] = rel

        data["source"] = rel
        data["sourceUrl"] = f"{REPO_URL}/blob/{REPO_BRANCH}/{REPO_SUBDIR}/{rel}"
        resolve_next(data)
        data["mermaid"] = to_mermaid(data)
        data["markdown"] = to_markdown(data)
        flows.append(json.loads(json.dumps(data, default=str)))  # date → str

    return flows, errors, warnings


def validate_flow(f: dict) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for key in REQUIRED_FLOW_KEYS:
        if not f.get(key):
            errors.append(f"missing required key '{key}'")
    steps = f.get("steps") or []
    if not isinstance(steps, list):
        return errors + ["'steps' must be a list"], warnings

    ids = [s.get("id") for s in steps if isinstance(s, dict)]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate step id(s): {', '.join(sorted(map(str, dupes)))}")
    id_set = set(ids)

    for i, s in enumerate(steps):
        where = f"step[{i}] '{s.get('id', '?')}'"
        for key in REQUIRED_STEP_KEYS:
            if not s.get(key):
                errors.append(f"{where}: missing '{key}'")
        if s.get("type") == "decision":
            branches = s.get("branches") or []
            if len(branches) < 2:
                errors.append(f"{where}: decision needs at least 2 branches")
            for b in branches:
                if b.get("next") not in id_set:
                    errors.append(f"{where}: branch '{b.get('label')}' → unknown step '{b.get('next')}'")
        else:
            if s.get("next") and s["next"] not in id_set:
                errors.append(f"{where}: next → unknown step '{s['next']}'")
            if not s.get("actions") and not s.get("description"):
                warnings.append(f"{where}: no 'actions' or 'description'")
    return errors, warnings


def resolve_next(f: dict) -> None:
    """next 省略時は配列の次のステップ、最後または end: true なら完了。"""
    steps = f["steps"]
    for i, s in enumerate(steps):
        if s.get("type") == "decision":
            continue
        if s.get("end"):
            s["next"] = END_ID
        elif not s.get("next"):
            s["next"] = steps[i + 1]["id"] if i + 1 < len(steps) else END_ID


# ── Renderers ──────────────────────────────────────────────────────────────────

def _mm(text: str) -> str:
    return str(text).replace('"', "#quot;").replace("\n", " ")


def to_mermaid(f: dict) -> str:
    idx = {s["id"]: f"n{i}" for i, s in enumerate(f["steps"])}
    idx[END_ID] = "nEnd"
    lines = ["flowchart TD", '  nStart(["開始"])']
    for i, s in enumerate(f["steps"]):
        nid = idx[s["id"]]
        actor = f"<small>{_mm(s['actor'])}</small><br/>" if s.get("actor") else ""
        label = f"{actor}{_mm(s['title'])}"
        lines.append(f'  {nid}{{"{label}"}}' if s.get("type") == "decision" else f'  {nid}["{label}"]')
    lines.append('  nEnd(["完了"])')
    lines.append(f"  nStart --> {idx[f['steps'][0]['id']]}")
    for s in f["steps"]:
        src = idx[s["id"]]
        if s.get("type") == "decision":
            for b in s["branches"]:
                lines.append(f'  {src} -->|"{_mm(b["label"])}"| {idx[b["next"]]}')
        else:
            lines.append(f"  {src} --> {idx[s['next']]}")
    lines.append("  classDef decision fill:#fff4dc,stroke:#b7791f,color:#3d2a06")
    decisions = [idx[s["id"]] for s in f["steps"] if s.get("type") == "decision"]
    if decisions:
        lines.append(f"  class {','.join(decisions)} decision")
    return "\n".join(lines)


def to_markdown(f: dict) -> str:
    """AI/LLM 向け。構造が明確で、単体でも意味が通るように書く。"""
    title_by_id = {s["id"]: s["title"] for s in f["steps"]}
    title_by_id[END_ID] = "（完了）"
    out = [f"# {f['title']}", ""]
    out.append(f"- flow_id: `{f['id']}`")
    out.append(f"- システム: {f['system']}")
    for key, label in (("category", "分類"), ("owner", "管理者"), ("updated", "更新日")):
        if f.get(key):
            out.append(f"- {label}: {f[key]}")
    if f.get("roles"):
        out.append(f"- 登場ロール: {', '.join(f['roles'])}")
    if f.get("tags"):
        out.append(f"- タグ: {', '.join(f['tags'])}")
    out += ["", "## 概要", "", str(f["summary"]).strip(), ""]
    if f.get("preconditions"):
        out += ["## 前提条件", ""] + [f"- {p}" for p in f["preconditions"]] + [""]

    out += ["## 手順", ""]
    for i, s in enumerate(f["steps"], 1):
        kind = "（分岐）" if s.get("type") == "decision" else ""
        out.append(f"### {i}. {s['title']}{kind}  `{s['id']}`")
        out.append("")
        meta = []
        if s.get("actor"):
            meta.append(f"担当: {s['actor']}")
        if s.get("screen"):
            meta.append(f"画面: {s['screen']}")
        if meta:
            out += [" / ".join(meta), ""]
        if s.get("description"):
            out += [str(s["description"]).strip(), ""]
        if s.get("actions"):
            out += [f"{n}. {a}" for n, a in enumerate(s["actions"], 1)] + [""]
        if s.get("inputs"):
            out += ["| 項目 | 必須 | 入力例 | 備考 |", "|---|---|---|---|"]
            for inp in s["inputs"]:
                out.append(f"| {inp['field']} | {'○' if inp.get('required') else ''} | "
                           f"{inp.get('example', '')} | {inp.get('note', '')} |")
            out.append("")
        if s.get("expected"):
            out += [f"期待結果: {s['expected']}", ""]
        for e in s.get("errors") or []:
            out.append(f"- エラー {e.get('code', '')}「{e['message']}」→ {e.get('fix', '')}")
        for t in s.get("tips") or []:
            out.append(f"- ヒント: {t}")
        if s.get("errors") or s.get("tips"):
            out.append("")
        if s.get("type") == "decision":
            for b in s["branches"]:
                note = f"（{b['note']}）" if b.get("note") else ""
                out.append(f"- 「{b['label']}」→ {title_by_id[b['next']]} `{b['next']}`{note}")
            out.append("")
        else:
            out += [f"次へ → {title_by_id[s['next']]}", ""]

    if f.get("references"):
        out += ["## 参照資料", ""]
        out += [f"- {r['title']}" + (f"（{r['location']}）" if r.get("location") else "")
                for r in f["references"]]
        out.append("")
    out += ["## フロー図", "", "```mermaid", f["mermaid"], "```", ""]
    return "\n".join(out)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only")
    args = ap.parse_args()

    flows, errors, warnings = load_flows()
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        return 1
    print(f"OK    {len(flows)} flow(s)")
    if args.check:
        return 0

    payload = json.dumps(flows, ensure_ascii=False, indent=1)
    (ROOT / "flows.json").write_text(payload, encoding="utf-8")

    DIST_DIR.mkdir(exist_ok=True)
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    inline = payload.replace("</", "<\\/")
    html = html.replace("<!--FLOWS_DATA-->", f"<script>window.__FLOWS__ = {inline};</script>")
    (DIST_DIR / "walkthrough.html").write_text(html, encoding="utf-8")

    header = ("<!-- 自動生成ファイル。編集は flows/*.yaml に対して行い、python build.py で再生成する -->\n"
              "# 業務フロー集（AI 参照用）\n\n"
              + "\n".join(f"- [{f['title']}](#{f['id']}) — {f['system']}" for f in flows) + "\n\n")
    body = "\n---\n\n".join(f'<a id="{f["id"]}"></a>\n\n{f["markdown"]}' for f in flows)
    (DIST_DIR / "flows-for-ai.md").write_text(header + body, encoding="utf-8")

    print("wrote flows.json, dist/walkthrough.html, dist/flows-for-ai.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
