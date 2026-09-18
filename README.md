# AI Customer Support Hub — Portfolio Final 1.0

AIによる問い合わせ分析と、対応期限・担当者・顧客・監査ログを一元管理するマルチテナント型カスタマーサポートSaaSのポートフォリオです。

## 主な機能

- OpenAI APIによる問い合わせのカテゴリ分類、重要度判定、要約、推奨対応、タグ生成
- AI返信案の生成（自動送信せず、人が確認するHuman-in-the-loop設計）
- 問い合わせの検索・絞り込み・担当者・ステータス・期限・社内メモ管理
- SLAダッシュボード（期限超過／本日／明日／3日以内／高重要度）
- 顧客単位の問い合わせ履歴
- カテゴリ・受付経路・担当者別の分析
- 管理者／担当者／閲覧者のロールベースアクセス制御
- 企業単位のマルチテナント分離
- プラットフォーム管理者による企業作成・停止／再開
- 監査ログとCSVエクスポート
- PostgreSQL永続化、Docker Composeによる起動

## 技術スタック

Python 3.12 / Streamlit / PostgreSQL 16 / psycopg 3 / OpenAI API / bcrypt / pandas / Docker Compose

## セットアップ

1. `.env.example` を `.env` にコピーし、OpenAI APIキー・DBパスワード・初期管理者情報を設定します。
2. 初回のみ管理者を作成します。
3. Docker Composeで起動します。

```bash
cp .env.example .env
docker compose up -d db
docker compose run --rm app python bootstrap.py
docker compose up -d --build
```

ブラウザで `http://localhost:8501` を開きます。

## セキュリティ設計

`.env`、認証情報、トークン、ログ、バックアップはGit管理対象外です。パスワードはbcryptでハッシュ化し、問い合わせ・ユーザー・監査ログは企業IDでスコープします。SQLはプレースホルダを利用し、AI生成返信は自動送信しません。

## デモで確認できるポイント

1. 管理者・担当者・閲覧者で表示／操作権限が変わること
2. AI分析から問い合わせ登録までの流れ
3. 問い合わせの更新と監査ログへの記録
4. ダッシュボード・顧客・分析画面への即時反映
5. 別企業でログインした際に他社データが表示されないこと
6. プラットフォーム管理者から企業の作成・利用状況確認ができること

## 構成

- `app.py` — Streamlit UI、権限・テナント境界、各管理画面
- `auth.py` — PostgreSQL認証・RBAC
- `database.py` — DBスキーマ・テナントスコープ付きデータアクセス
- `main.py` — AI分析・問い合わせ登録／更新ロジック
- `bootstrap.py` — 初回企業・管理者作成
- `docker-compose.yml` / `Dockerfile` — 実行環境

## 注意

本リポジトリはポートフォリオ／デモ用途です。本番提供時にはTLS、マネージドDB、Secret Manager、バックアップ／復旧、レート制限、監視、SSO等を環境要件に応じて追加してください。
