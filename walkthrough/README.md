# 業務フロー ウォークスルー (POC)

Hệ thống walkthrough flow thao tác nghiệp vụ: xem từng bước trên sơ đồ, rẽ nhánh theo điều kiện, member đóng góp qua Pull Request, AI đọc được.

```
walkthrough/
├── flows/                 ← NGUỒN DUY NHẤT: 1 file YAML = 1 flow (tiếng Nhật)
│   ├── _template.yaml     ← mẫu để copy
│   └── sales/order-entry.yaml
├── flow.schema.json       ← JSON Schema (VS Code tự gợi ý/validate)
├── build.py               ← validate + sinh ra các file dưới
├── index.html             ← viewer
├── flows.json             ← (sinh ra) dữ liệu cho viewer
├── dist/walkthrough.html  ← (sinh ra) 1 file HTML độc lập, mở trực tiếp không cần server
├── dist/flows-for-ai.md   ← (sinh ra) toàn bộ flow dạng Markdown cho AI/RAG
├── CONTRIBUTING.md        ← hướng dẫn viết flow (tiếng Nhật, cho member)
└── AGENTS.md              ← hướng dẫn cho AI đọc/viết flow
```

## Chạy

```bash
cd walkthrough
pip install pyyaml
python build.py               # validate + build
python -m http.server 8000    # mở http://localhost:8000/
# hoặc mở thẳng dist/walkthrough.html
```

`python build.py --check` chỉ validate (dùng cho CI): kiểm tra field bắt buộc, trùng ID, `next`/`branches` trỏ tới bước không tồn tại.

## Viewer

- **ウォークスルー**: sơ đồ (mermaid) + thẻ bước hiện tại. Phím ←/→ để lùi/tiến, phím 1–9 để chọn nhánh, click vào ô trên sơ đồ để nhảy tới bước đó.
- **全手順**: toàn bộ các bước dạng tài liệu, để review.
- **AI用テキスト**: Markdown của flow, có nút copy để dán cho AI.
- Deep link: `index.html#sales-order-entry`.
