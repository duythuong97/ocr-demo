# フローの追加・修正

フローは `flows/` 以下の YAML ファイル 1 つにつき 1 フローです。Git の Pull Request でレビューして取り込みます。

## 手順

1. `flows/_template.yaml` をコピーして `flows/<分類>/<名前>.yaml` を作る
2. 中身を書く（下の「書き方のルール」を参照）
3. 検証してプレビューする
   ```bash
   cd walkthrough
   python build.py          # 検証 + flows.json / dist/ を再生成
   python -m http.server 8000
   # → http://localhost:8000/ を開く
   ```
4. `flows/*.yaml` と再生成されたファイルをコミットし、Pull Request を作る

VS Code に「YAML」拡張（Red Hat）を入れると、`flow.schema.json` による入力補完とエラー表示が効きます。

## 書き方のルール

- **1 ステップ = 1 画面での 1 まとまりの操作**。画面が変わったらステップを分ける
- `title` は「〜する」で終わる動作の形にする（分岐は「〜か？」）
- `actor` には操作する人のロールを書く。システムの自動判定は `システム`
- `screen` には **画面ID + 画面名** を書く（例: `SLS-0210 受注入力（ヘッダ）`）。AI 検索や画面との突き合わせに使う
- `actions` は画面上のボタン名・メニュー名を「」で囲んで、マニュアルの表記どおりに書く
- 分岐は `type: decision` と `branches`。各分岐の `next` には移動先ステップの `id` を書く
- `next` を省略すると配列の次のステップへ進む。フローの最後は `end: true`
- 元資料がある場合は `references` に資料名とページを書く

## レビューの観点

- 実際にその手順どおり操作して完了できるか
- 画面ID・ボタン名が最新のシステムと一致しているか
- エラーと対処（`errors`）は実際に問い合わせが多いものか
