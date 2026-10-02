# AI エージェント向けガイド

このディレクトリは業務システムの操作フロー集です。AI がフローを読む・書くときはこのファイルに従ってください。

## 読む

- 正本は `flows/**/*.yaml`（1 ファイル = 1 フロー、スキーマは `flow.schema.json`）
- 全フローを 1 ファイルにまとめた Markdown が `dist/flows-for-ai.md`。質問に答えるときはこれを読むのが早い
- ステップの遷移: `next` があればそれ、なければ配列の次。`end: true` で完了。`type: decision` は `branches[].next` で分岐
- 回答では flow_id とステップ ID（例: `sales-order-entry` / `credit-check`）、画面ID を引用すること

## 書く（日本語の資料からフローを起こす場合）

1. `flows/_template.yaml` の構造に従って `flows/<分類>/<名前>.yaml` を作成する
2. 資料の表記（ボタン名・画面名・メッセージ）は原文の日本語のまま使う。翻訳・言い換えをしない
3. 資料に書かれていないことは推測で埋めない。不明な点は YAML コメント `# TODO: 要確認 …` として残す
4. `references` に元資料名とページを必ず書く
5. `python build.py --check` がエラーなしで通ることを確認し、`python build.py` で生成物を更新する
